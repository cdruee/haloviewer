# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5
"""
Remembered settings of HaloViewer, and the command-line options that
change them.

The settings live in one small JSON file, ``settings.json``, in the
per-user configuration directory (:func:`config_dir`):

* Windows: ``%APPDATA%\\haloviewer\\settings.json`` (roaming profile)
* elsewhere: ``$XDG_CONFIG_HOME/haloviewer/settings.json``, normally
  ``~/.config/haloviewer/settings.json``

The environment variable ``HALOVIEWER_CONFIG`` names a different
directory. The file holds the viewer's last data directory and GUI
settings (file kind, plot type, time preset, filter, fill, window size)
and the scan cache setting (:mod:`haloviewer.cache`), which ``haloplot``
and the viewer share. A missing, unreadable or foreign file simply
counts as "no settings saved yet". Every write reads the file first and
changes only the given keys, so ``haloplot`` changing the cache setting
never loses the viewer's settings (and vice versa).
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

__all__ = ['SETTINGS_VERSION', 'ENV_VAR', 'DEFAULTS', 'config_dir',
           'settings_path', 'load', 'save', 'add_cache_arguments',
           'apply_cache_arguments', 'info_text']

SETTINGS_VERSION = 1
SETTINGS_FILENAME = 'settings.json'

#: Environment variable naming a different configuration directory.
ENV_VAR = 'HALOVIEWER_CONFIG'

#: Every remembered setting and its default.
DEFAULTS: Dict[str, Any] = {
    'root_dir': '',          # last data (Proc) directory of the viewer
    'kind': '',              # last file kind
    'mode': '',              # last plot type
    'time_preset': '24h',    # last time preset
    'filter_on': False,
    'filter_value': '',      # '' -> the default threshold
    'fill': False,
    'geometry': '',          # Tk window geometry, e.g. "1280x800+10+10"
    'cache_mode': 'tmp',     # 'off', 'tmp', 'user' or 'custom'
    'cache_dir': '',         # directory for cache_mode 'custom'
}


def config_dir() -> Path:
    """Directory of ``settings.json`` (not created by this function)."""
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).expanduser()
    if os.name == 'nt':
        base = os.environ.get('APPDATA')
        base = Path(base) if base else Path.home() / 'AppData' / 'Roaming'
        return base / 'haloviewer'
    base = os.environ.get('XDG_CONFIG_HOME')
    base = Path(base) if base else Path.home() / '.config'
    return base / 'haloviewer'


def settings_path() -> Path:
    return config_dir() / SETTINGS_FILENAME


def _read_file() -> Dict[str, Any]:
    try:
        with open(settings_path(), 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get('version') != SETTINGS_VERSION:
        return {}
    return data


def load() -> Dict[str, Any]:
    """All settings: the saved ones, completed with :data:`DEFAULTS`
    (a saved value of the wrong type falls back to the default)."""
    data = _read_file()
    out = dict(DEFAULTS)
    for key, default in DEFAULTS.items():
        value = data.get(key, default)
        if isinstance(value, type(default)):
            out[key] = value
    return out


def save(**updates: Any) -> bool:
    """Change the given settings (unknown keys are ignored), keeping all
    others as saved. Written atomically. Never raises.

    :returns: ``True`` if the file was written.
    """
    data = _read_file()
    data.update({k: v for k, v in updates.items() if k in DEFAULTS})
    data['version'] = SETTINGS_VERSION
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=1, sort_keys=True)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except OSError:
        return False


# -------------------------------------------------------------------------
# command-line options shared by ``haloviewer`` and ``haloplot``
# -------------------------------------------------------------------------

def add_cache_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ``--no-cache``/``--tmp-cache``/``--user-cache``/
    ``--cache-dir`` and ``-i``/``--info`` to ``parser``."""
    group = parser.add_argument_group(
        'scan cache (the choice is remembered for later runs)')
    excl = group.add_mutually_exclusive_group()
    excl.add_argument(
        '--no-cache', dest='cache_mode', action='store_const', const='off',
        help='use no scan cache, and delete the existing one')
    excl.add_argument(
        '--tmp-cache', dest='cache_mode', action='store_const', const='tmp',
        help='keep the cache in the system temp directory, which the '
             'system cleans up (on reboot or by age); the default')
    excl.add_argument(
        '--user-cache', dest='cache_mode', action='store_const',
        const='user',
        help='keep the cache in the per-user cache directory: persistent, '
             'but it can grow large')
    excl.add_argument(
        '--cache-dir', metavar='DIR',
        help='like --user-cache, but keep the cache in DIR')
    parser.add_argument(
        '-i', '--info', action='store_true',
        help='show the remembered settings and the cache location and '
             'size')


def apply_cache_arguments(args: argparse.Namespace) -> Optional[str]:
    """Apply and remember the cache option given on the command line, if
    any (see :func:`haloviewer.cache.configure`).

    :returns: a one-line message describing the change, or ``None``.
    """
    from . import cache
    mode = getattr(args, 'cache_mode', None)
    directory = getattr(args, 'cache_dir', None)
    if directory:
        mode = 'custom'
    if mode is None:
        return None
    cache.configure(mode, directory)
    if mode == 'off':
        return 'scan cache switched off and deleted'
    return f'scan cache: {cache.MODE_LABELS[mode]}, {cache.cache_dir_for(mode, directory)}'


def _size(n_bytes: int) -> str:
    if n_bytes >= 1e9:
        return f'{n_bytes / 1e9:.2f} GB'
    return f'{n_bytes / 1e6:.1f} MB'


def info_text() -> str:
    """Human-readable report of the remembered settings and the scan
    cache (the ``-i``/``--info`` output)."""
    from . import cache, __version__
    s = load()
    path = settings_path()
    lines = [f'HaloViewer {__version__}', '',
             f'Settings file:  {path}'
             + ('' if path.is_file() else '  (not created yet)')]
    lines.append(f'  data directory: {s["root_dir"] or "-"}')
    lines.append(f'  file kind:      {s["kind"] or "-"}')
    lines.append(f'  plot type:      {s["mode"] or "-"}')
    lines.append(f'  time preset:    {s["time_preset"]}')
    if s['filter_on']:
        lines.append(f'  filter:         intensity < '
                     f'{s["filter_value"] or "default"}')
    else:
        lines.append('  filter:         off')
    lines.append(f'  fill:           {"on" if s["fill"] else "off"}')
    lines.append('')
    mode, directory = cache.effective_setting()
    env = os.environ.get(cache.ENV_VAR)
    lines.append(f'Scan cache:     {cache.MODE_LABELS[mode]}'
                 + (f'  (set by {cache.ENV_VAR})' if env is not None
                    else ''))
    if directory is not None:
        n, size = cache.ScanCache(directory).stats()
        lines.append(f'  directory:      {directory}')
        lines.append(f'  contents:       {n} file(s), {_size(size)}')
    return '\n'.join(lines)
