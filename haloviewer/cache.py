# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5
"""
On-disk cache of parsed regular scan files.

Parsing a large ``.hpl`` text file takes a noticeable fraction of a
second; loading the same data back from a numpy ``.npz`` file takes a
few milliseconds. This module keeps one such ``.npz`` file per scan
file (a :class:`~haloviewer.hpl.ScanArrays` -- every ray's time and
angles, every gate's Doppler velocity, intensity and beta, *unfiltered*)
in a per-user cache directory, so every scan file is parsed at most
once.

Cache entries are named after a hash of the source file's absolute
path, size and modification time (and :data:`CACHE_FORMAT`), so a
changed or replaced source file simply misses the cache and is parsed
afresh; nothing ever has to be "invalidated". Entries are written
atomically (to a temporary file, then renamed), so several HaloViewer
instances can share the directory, and a crash never leaves a broken
entry behind. Deleting the directory at any time is safe -- it is
rebuilt on demand.

Where the cache lives is a remembered setting (:mod:`haloviewer.settings`,
changed with :func:`configure`, the ``--no-cache``/``--tmp-cache``/
``--user-cache``/``--cache-dir`` options of ``haloviewer`` and
``haloplot``, or the viewer's "Cache settings" dialogue), one of
:data:`CACHE_MODES`:

* ``tmp`` (the default) -- in the system temp directory
  (:func:`tmp_cache_dir`), e.g. ``/tmp/haloviewer-cache-<user>`` or
  ``%TEMP%\\haloviewer-cache-<user>``. The system removes it on reboot
  or once it is old enough, depending on its configuration.
* ``user`` -- in the per-user cache directory (:func:`user_cache_dir`):
  ``%LOCALAPPDATA%\\haloviewer\\Cache`` on Windows,
  ``~/Library/Caches/haloviewer`` on macOS,
  ``$XDG_CACHE_HOME/haloviewer`` (normally ``~/.cache/haloviewer``)
  elsewhere. Persistent, but it can grow large.
* ``custom`` -- like ``user``, in a directory of one's choice.
* ``off`` -- no cache; switching to it deletes the existing caches.

The environment variable ``HALOVIEWER_CACHE`` overrides the setting: a
directory, or ``off`` (``0``, ``no``, ``none``, ``false``) for no cache.

Only regular scan files (VAD, Stare, RHI, Wind_Profile, ...) are cached.
``Processed_Wind_Profile`` files are small and are always read directly.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import logging
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Union

import numpy as np
import pandas as pd

from . import hpl
from . import settings as _settings

logger = logging.getLogger(__name__)

__all__ = ['CACHE_FORMAT', 'ENV_VAR', 'CACHE_MODES', 'MODE_LABELS',
           'tmp_cache_dir', 'user_cache_dir', 'cache_dir_for',
           'effective_setting', 'default_cache_dir', 'configure',
           'wipe_all', 'ScanCache', 'get_default_cache',
           'set_default_cache', 'read_scan_arrays']

#: Version of the entry layout. Part of every entry's name, so bumping it
#: makes all old entries miss (they are removed by :meth:`ScanCache.prune`).
CACHE_FORMAT = 1

#: Environment variable that overrides the cache directory, or switches
#: the cache off (``off``, ``0``, ``no``, ``none``, ``false``).
ENV_VAR = 'HALOVIEWER_CACHE'

_OFF_WORDS = {'', 'off', '0', 'no', 'none', 'false'}

_ARRAY_FIELDS = ('time', 'azimuth', 'elevation', 'pitch', 'roll',
                 'gate', 'doppler', 'intensity', 'beta')


#: The cache settings: no cache, system temp directory (default),
#: per-user cache directory, or a directory of one's choice.
CACHE_MODES = ('off', 'tmp', 'user', 'custom')

#: Short descriptions of :data:`CACHE_MODES`, for messages.
MODE_LABELS = {
    'off': 'off',
    'tmp': 'temporary (system temp directory)',
    'user': 'per-user (persistent)',
    'custom': 'custom directory (persistent)',
}


def user_cache_dir() -> Path:
    """The platform's per-user cache directory for HaloViewer."""
    if os.name == 'nt':
        base = os.environ.get('LOCALAPPDATA')
        base = Path(base) if base else Path.home() / 'AppData' / 'Local'
        return base / 'haloviewer' / 'Cache'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Caches' / 'haloviewer'
    base = os.environ.get('XDG_CACHE_HOME')
    base = Path(base) if base else Path.home() / '.cache'
    return base / 'haloviewer'


def _user_name() -> str:
    try:
        name = getpass.getuser()
    except Exception:  # noqa: BLE001 -- no user name available
        name = str(os.getuid()) if hasattr(os, 'getuid') else 'user'
    return ''.join(c if c.isalnum() or c in '-_.' else '_' for c in name)


def tmp_cache_dir() -> Path:
    """``haloviewer-cache-<user>`` in the system temp directory.

    On a shared temp directory (``/tmp``) it is created readable by its
    owner only. Should a directory of that name already exist but belong
    to someone else, the per-user cache directory is used instead.
    """
    d = Path(tempfile.gettempdir()) / f'haloviewer-cache-{_user_name()}'
    if hasattr(os, 'getuid'):
        try:
            d.mkdir(mode=0o700, exist_ok=True)
            if d.stat().st_uid != os.getuid():
                logger.warning('%s belongs to another user; using %s '
                               'for the scan cache instead', d,
                               user_cache_dir())
                return user_cache_dir()
        except OSError:
            pass
    return d


def cache_dir_for(mode: str, directory=None) -> Optional[Path]:
    """The cache directory of a cache setting (``None`` for ``off``)."""
    if mode == 'off':
        return None
    if mode == 'user':
        return user_cache_dir()
    if mode == 'custom' and directory:
        return Path(directory).expanduser()
    return tmp_cache_dir()


def effective_setting() -> tuple:
    """``(mode, directory)`` in effect: from ``HALOVIEWER_CACHE`` if set,
    else the remembered setting. ``directory`` is ``None`` for ``off``."""
    env = os.environ.get(ENV_VAR)
    if env is not None:
        if env.strip().lower() in _OFF_WORDS:
            return 'off', None
        return 'custom', Path(env).expanduser()
    s = _settings.load()
    mode = s['cache_mode'] if s['cache_mode'] in CACHE_MODES else 'tmp'
    if mode == 'custom' and not s['cache_dir']:
        mode = 'tmp'
    return mode, cache_dir_for(mode, s['cache_dir'])


def default_cache_dir() -> Optional[Path]:
    """The cache directory in effect (see :func:`effective_setting`), or
    ``None`` if caching is switched off."""
    return effective_setting()[1]


def wipe_all(extra: Iterable = ()) -> None:
    """Delete every cache entry in all the places a cache may be: the
    temp and per-user directories, the remembered custom directory, the
    one ``HALOVIEWER_CACHE`` names, and ``extra`` directories. Only
    cache entries are removed -- other files in a custom directory are
    left alone; the temp and per-user directories are removed entirely
    once empty."""
    own = [tmp_cache_dir(), user_cache_dir()]
    others = [Path(d) for d in extra]
    custom = _settings.load()['cache_dir']
    if custom:
        others.append(Path(custom).expanduser())
    env = os.environ.get(ENV_VAR)
    if env and env.strip().lower() not in _OFF_WORDS:
        others.append(Path(env).expanduser())
    done = set()
    for d, remove_dir in [(d, True) for d in own] + [(d, False)
                                                      for d in others]:
        key = os.path.abspath(str(d))
        if key in done:
            continue
        done.add(key)
        ScanCache(d).clear(remove_dir=remove_dir)


def configure(mode: str, directory=None) -> Optional[Path]:
    """Choose (and remember) the cache setting, see :data:`CACHE_MODES`.
    ``off`` also deletes the existing caches (:func:`wipe_all`).
    ``custom`` needs ``directory`` (or a remembered one).

    :returns: the cache directory now in effect (``None`` for ``off``).
    :raises ValueError: for an unknown mode, or ``custom`` without a \
        directory.
    """
    if mode not in CACHE_MODES:
        raise ValueError(f'unknown cache mode {mode!r}')
    updates = {'cache_mode': mode}
    if mode == 'custom':
        directory = directory or _settings.load()['cache_dir']
        if not directory:
            raise ValueError('a custom cache needs a directory')
        updates['cache_dir'] = os.path.abspath(
            str(Path(directory).expanduser()))
    if mode == 'off':
        wipe_all()
    _settings.save(**updates)
    set_default_cache()
    return cache_dir_for(mode, updates.get('cache_dir'))


def _source_id(path) -> tuple:
    """``(absolute path, size, mtime_ns)`` of a source file."""
    p = os.path.abspath(str(path))
    st = os.stat(p)
    return p, st.st_size, st.st_mtime_ns


class ScanCache(object):
    """
    A cache directory of parsed scan files; see the module description.

    :param directory: where to keep the entries (created on first write).
    """

    def __init__(self, directory: Union[str, Path]):
        self.directory = Path(directory)

    def __repr__(self) -> str:
        return f'ScanCache({str(self.directory)!r})'

    # -- naming -----------------------------------------------------

    def entry_path(self, path) -> Path:
        """The cache entry the *current* state of source file ``path``
        maps to (whether or not it exists yet)."""
        src, size, mtime = _source_id(path)
        key = f'{src}|{size}|{mtime}|{CACHE_FORMAT}'
        name = hashlib.sha1(key.encode('utf-8')).hexdigest()[:32]
        # two-level layout keeps directories small for years of data
        return self.directory / name[:2] / f'{name}.npz'

    def is_cached(self, path) -> bool:
        """``True`` if an up-to-date entry for ``path`` exists."""
        try:
            return self.entry_path(path).is_file()
        except OSError:
            return False

    # -- reading / writing -------------------------------------------

    def load(self, path) -> Optional[hpl.ScanArrays]:
        """The cached arrays of ``path``, or ``None`` on a cache miss
        (a broken entry counts as a miss and is removed)."""
        try:
            entry = self.entry_path(path)
        except OSError:
            return None
        if not entry.is_file():
            return None
        try:
            with np.load(entry, allow_pickle=False) as z:
                meta = json.loads(str(z['meta']))
                arrays = {k: z[k] for k in _ARRAY_FIELDS}
            return hpl.ScanArrays(
                header=meta['header'],
                timestamp=pd.Timestamp(meta['timestamp']),
                type_=meta['type'], mode=meta['mode'], **arrays)
        except Exception as exc:  # noqa: BLE001 -- any damage = miss
            logger.warning('discarding broken cache entry %s (%s)',
                           entry, exc)
            try:
                entry.unlink()
            except OSError:
                pass
            return None

    def store(self, path, arrays: hpl.ScanArrays) -> Optional[Path]:
        """Write ``arrays`` as the entry for ``path``. Never raises: a
        cache that can't be written (disk full, no permission) just
        stays empty. :returns: the entry, or ``None`` if not written."""
        try:
            src, size, mtime = _source_id(path)
            entry = self.entry_path(path)
            entry.parent.mkdir(parents=True, exist_ok=True)
            meta = json.dumps({
                'source': src, 'size': size, 'mtime_ns': mtime,
                'format': CACHE_FORMAT, 'header': arrays.header,
                'timestamp': arrays.timestamp.isoformat(),
                'type': arrays.type, 'mode': arrays.mode})
            fd, tmp = tempfile.mkstemp(dir=entry.parent, suffix='.tmp')
            try:
                with os.fdopen(fd, 'wb') as fh:
                    np.savez(fh, meta=np.array(meta),
                             **{k: getattr(arrays, k) for k in _ARRAY_FIELDS})
                os.replace(tmp, entry)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return entry
        except Exception as exc:  # noqa: BLE001
            logger.debug('could not write cache entry for %s: %s', path, exc)
            return None

    def get(self, path) -> hpl.ScanArrays:
        """The arrays of scan file ``path``: from the cache if possible,
        otherwise parsed (:func:`hpl.read_scan_arrays`) and stored.

        :raises IOError, ValueError: as :func:`hpl.read_scan_arrays`.
        """
        arrays = self.load(path)
        if arrays is None:
            arrays = hpl.read_scan_arrays(path)
            self.store(path, arrays)
        return arrays

    # -- bulk maintenance --------------------------------------------

    def missing(self, paths: Iterable) -> List:
        """Those of ``paths`` that have no up-to-date entry."""
        return [p for p in paths if not self.is_cached(p)]

    def build(self, paths: Iterable,
              stop: Optional[threading.Event] = None,
              progress: Optional[Callable[[int, int], None]] = None,
              pause: float = 0.0) -> int:
        """Make sure every one of ``paths`` has an up-to-date entry,
        parsing those that don't, in the given order. Files that can't
        be parsed are skipped (logged at debug level).

        :param stop: optional event; the build ends early once it is set.
        :param progress: optional callback ``progress(done, total)``, \
            called after every file (``total`` counts only the files \
            that needed parsing).
        :param pause: seconds to sleep after each file, to leave some \
            CPU to the GUI when building in a background thread.
        :returns: number of files parsed.
        """
        todo = self.missing(paths)
        total = len(todo)
        if progress is not None:
            progress(0, total)
        done = 0
        for p in todo:
            if stop is not None and stop.is_set():
                break
            try:
                self.store(p, hpl.read_scan_arrays(p))
            except Exception as exc:  # noqa: BLE001
                logger.debug('cache: skipping %s: %s', p, exc)
            done += 1
            if progress is not None:
                progress(done, total)
            if pause:
                time.sleep(pause)
        return done

    def _entries(self):
        if not self.directory.is_dir():
            return
        for sub in self.directory.iterdir():
            if sub.is_dir() and len(sub.name) == 2:
                yield from sub.iterdir()

    def prune(self, stop: Optional[threading.Event] = None) -> int:
        """Remove entries that can no longer be hit: older entry
        formats, and entries whose source file was changed or deleted.
        An entry whose source *directory* is missing altogether (e.g. a
        network drive that isn't mounted right now) is kept. Also
        removes temporary files left behind by a crash.

        :returns: number of files removed.
        """
        removed = 0
        now = time.time()
        for f in list(self._entries() or []):
            if stop is not None and stop.is_set():
                break
            try:
                if f.suffix == '.tmp':
                    if now - f.stat().st_mtime > 3600:
                        f.unlink()
                        removed += 1
                    continue
                if f.suffix != '.npz':
                    continue
                with np.load(f, allow_pickle=False) as z:
                    meta = json.loads(str(z['meta']))
                src = meta.get('source', '')
                if meta.get('format') != CACHE_FORMAT:
                    stale = True
                elif not os.path.isdir(os.path.dirname(src)):
                    stale = False  # source drive/dir not available
                elif not os.path.isfile(src):
                    stale = True
                else:
                    stale = f != self.entry_path(src)
            except Exception:  # noqa: BLE001 -- unreadable entry
                stale = True
            if stale:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def clear(self, remove_dir: bool = False) -> None:
        """Remove every entry, and the then empty sub-directories. With
        ``remove_dir``, also the cache directory itself if nothing else
        is left in it."""
        for f in list(self._entries() or []):
            if f.suffix not in ('.npz', '.tmp'):
                continue
            try:
                f.unlink()
            except OSError:
                pass
        if not self.directory.is_dir():
            return
        for sub in self.directory.iterdir():
            if sub.is_dir() and len(sub.name) == 2:
                try:
                    sub.rmdir()      # only if empty
                except OSError:
                    pass
        if remove_dir:
            try:
                self.directory.rmdir()
            except OSError:
                pass

    def stats(self) -> tuple:
        """``(number of entries, total size in bytes)``."""
        n = total = 0
        for f in self._entries() or []:
            if f.suffix != '.npz':
                continue
            try:
                total += f.stat().st_size
                n += 1
            except OSError:
                pass
        return n, total

    def size_bytes(self) -> int:
        """Total size of all entries."""
        total = 0
        for f in self._entries() or []:
            try:
                total += f.stat().st_size
            except OSError:
                pass
        return total


# -------------------------------------------------------------------------
# the cache used by the haloviewer.data loaders
# -------------------------------------------------------------------------

_UNSET = object()
_default = _UNSET


def set_default_cache(cache=_UNSET) -> None:
    """Choose the cache used by :func:`read_scan_arrays` (and therefore
    by all :mod:`haloviewer.data` loaders, the API, ``haloplot`` and the
    viewer): a :class:`ScanCache`, a directory, ``None`` to switch
    caching off, or no argument to go back to :func:`default_cache_dir`.
    """
    global _default, _resolved
    _resolved = (None, None)
    if cache is _UNSET or cache is None or isinstance(cache, ScanCache):
        _default = cache
    else:
        _default = ScanCache(cache)


_resolved = (None, None)


def get_default_cache() -> Optional[ScanCache]:
    """The cache currently in use (see :func:`set_default_cache`), or
    ``None`` if caching is off."""
    global _resolved
    if _default is not _UNSET:
        return _default
    # resolved again only when the environment or the settings change
    try:
        mtime = _settings.settings_path().stat().st_mtime_ns
    except OSError:
        mtime = None
    key = (os.environ.get(ENV_VAR), str(_settings.settings_path()), mtime)
    if _resolved[0] != key:
        d = default_cache_dir()
        _resolved = (key, ScanCache(d) if d is not None else None)
    return _resolved[1]


def read_scan_arrays(path) -> hpl.ScanArrays:
    """The arrays of regular scan file ``path`` -- through the default
    cache if there is one, otherwise parsed directly.

    :raises IOError, ValueError: as :func:`hpl.read_scan_arrays`.
    """
    cache = get_default_cache()
    if cache is None:
        return hpl.read_scan_arrays(path)
    return cache.get(path)
