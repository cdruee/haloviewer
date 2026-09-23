# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5
"""
Directory scanning and indexing for Halo wind lidar ``Proc`` trees.

Halo Photonics processed-product files follow the naming convention
``<Type words>_<system id>_<yyyymmdd>_<hhmmss>.hpl``, e.g.
``Processed_Wind_Profile_77_20260919_121707.hpl`` or
``VAD_77_20221020_080000.hpl``. This module finds all ``.hpl`` files
below a chosen root directory (which may be the ``Proc`` folder itself,
any directory below it, such as a single day, or even the parent ``Data``
folder -- scanning is recursive and simply finds nothing under sibling
trees such as ``Raw`` or ``Metek``, which hold different file types),
classifies each by "kind" (the type-words part of the filename), and
indexes them by timestamp so a GUI or script can browse by kind and
time range without opening every file up front.

Classifying and timestamping purely from the filename keeps scanning an
Otherwise expensive directory tree fast even with tens of thousands of
files: full parsing (via :mod:`haloviewer.hpl`) only happens for
the handful of files actually plotted.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

# -------------------------------------------------------------------------
# Filename parsing
# -------------------------------------------------------------------------


def parse_filename(path) -> Optional["ParsedName"]:
    """
    Parse a Halo-style filename of the form
    ``<Type words>_<system id>_<yyyymmdd>_<hhmmss>.hpl`` (the time part
    may be shorter than 6 digits, e.g. just ``hh`` for some raw-scan
    naming conventions).

    :param path: filename or path (only the basename is used).
    :returns: a :class:`ParsedName`, or ``None`` if the filename does \
        not match the expected pattern.
    """
    name = os.path.basename(str(path))
    stem, ext = os.path.splitext(name)
    if ext.lower() != '.hpl':
        return None
    tokens = stem.split('_')
    if len(tokens) < 4:
        return None
    time_tok = tokens[-1]
    date_tok = tokens[-2]
    sysid_tok = tokens[-3]
    type_toks = tokens[:-3]
    if not (date_tok.isdigit() and len(date_tok) == 8):
        return None
    if not (time_tok.isdigit() and 2 <= len(time_tok) <= 6):
        return None
    if not type_toks:
        return None
    # pad hh / hhmm style times out to hhmmss for parsing
    time_padded = (time_tok + '0000')[:6]
    try:
        timestamp = pd.to_datetime(date_tok + time_padded,
                                    format='%Y%m%d%H%M%S')
    except ValueError:
        return None
    kind = '_'.join(type_toks)
    return ParsedName(kind=kind, timestamp=timestamp, system_id=sysid_tok)


@dataclass(frozen=True)
class ParsedName:
    kind: str
    timestamp: pd.Timestamp
    system_id: str


@dataclass(frozen=True)
class FileEntry:
    """One indexed ``.hpl`` file: its path, kind and (filename-derived)
    timestamp."""
    path: Path
    kind: str
    timestamp: pd.Timestamp
    system_id: str


# -------------------------------------------------------------------------
# Kind capabilities registry
# -------------------------------------------------------------------------

PROFILE_MODE = 'profile'
#: Internal mode key kept as "timeseries" for backward compatibility;
#: the GUI shows this mode to the user as "History" (it now covers not
#: just the wind-profile height/time image but the same binned,
#: intensity/beta "scan history" image for VAD/Stare/RHI/Wind_Profile).
TIMESERIES_MODE = 'timeseries'


@dataclass(frozen=True)
class KindInfo:
    """Describes what the viewer can do with files of a given kind."""
    name: str
    modes: Sequence[str] = field(default_factory=tuple)
    supported: bool = False
    description: str = ''


# Kinds with full plotting support. Kinds discovered on disk that are not
# listed here still show up (so the user can see what's in their data
# tree) but are reported as "not yet implemented" -- see api.plot_file.
#
# Only Processed_Wind_Profile and RHI have a "Profile" mode (a single
# scan shown by itself); the GUI greys out the Profile radio button for
# every other kind. Every supported kind has "History" (the internal
# TIMESERIES_MODE key) -- a multiple-file, time-binned image, built from
# either the processed height/direction/speed profile (for
# Processed_Wind_Profile) or the raw per-gate intensity/beta from the
# regular scan files (for VAD/Stare/Wind_Profile/RHI).
KIND_CAPABILITIES: Dict[str, KindInfo] = {
    'Processed_Wind_Profile': KindInfo(
        name='Processed_Wind_Profile',
        modes=(PROFILE_MODE, TIMESERIES_MODE),
        supported=True,
        description='Instrument-processed wind profile (height, '
                    'direction, speed).',
    ),
    'VAD': KindInfo(
        name='VAD',
        modes=(TIMESERIES_MODE,),
        supported=True,
        description='Conical (constant-elevation) scan: raw intensity '
                    'and backscatter (beta) history only.',
    ),
    'Stare': KindInfo(
        name='Stare',
        modes=(TIMESERIES_MODE,),
        supported=True,
        description='Fixed-pointing scan: raw intensity and '
                    'backscatter (beta) history only.',
    ),
    'Wind_Profile': KindInfo(
        name='Wind_Profile',
        modes=(TIMESERIES_MODE,),
        supported=True,
        description='Raw multi-beam scan behind the processed wind '
                    'profile: intensity and backscatter (beta) history '
                    'only.',
    ),
    'RHI': KindInfo(
        name='RHI',
        modes=(PROFILE_MODE, TIMESERIES_MODE),
        supported=True,
        description='Range-height indicator (vertical) scan: a single '
                    'scan\'s distance/height cross section (radial '
                    'velocity, beta), or an intensity/beta history.',
    ),
}


def get_kind_info(kind: str) -> KindInfo:
    """Look up capabilities for ``kind``; unknown kinds are reported as
    unsupported with no plot modes, rather than raising."""
    return KIND_CAPABILITIES.get(kind, KindInfo(name=kind))


# -------------------------------------------------------------------------
# Scanning
# -------------------------------------------------------------------------

class ScanResult:
    """
    Index of ``.hpl`` files found below a root directory, grouped by
    kind and sorted by timestamp within each kind.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self._by_kind: Dict[str, List[FileEntry]] = {}
        self.skipped: List[str] = []

    # -- building ---------------------------------------------------

    def _add(self, entry: FileEntry) -> None:
        self._by_kind.setdefault(entry.kind, []).append(entry)

    def _finalize(self) -> None:
        for kind, entries in self._by_kind.items():
            entries.sort(key=lambda e: e.timestamp)

    # -- query --------------------------------------------------------

    def kinds(self) -> List[str]:
        """Kinds found, sorted with kinds that have plotting support
        first (alphabetically within each group)."""
        def sort_key(k):
            return (0 if get_kind_info(k).supported else 1, k)
        return sorted(self._by_kind.keys(), key=sort_key)

    def files(self, kind: str) -> List[FileEntry]:
        return list(self._by_kind.get(kind, []))

    def count(self, kind: str) -> int:
        return len(self._by_kind.get(kind, []))

    def time_range(self, kind: str):
        """``(min_timestamp, max_timestamp)`` for ``kind``, or
        ``(None, None)`` if there are no files of that kind."""
        entries = self._by_kind.get(kind, [])
        if not entries:
            return None, None
        return entries[0].timestamp, entries[-1].timestamp

    def files_in_range(self, kind: str, start=None, end=None) -> List[FileEntry]:
        """Files of ``kind`` with ``start <= timestamp <= end``
        (inclusive); ``start``/``end`` of ``None`` leaves that side
        unbounded."""
        entries = self._by_kind.get(kind, [])
        start_ts = pd.Timestamp(start) if start is not None else None
        end_ts = pd.Timestamp(end) if end is not None else None
        out = []
        for e in entries:
            if start_ts is not None and e.timestamp < start_ts:
                continue
            if end_ts is not None and e.timestamp > end_ts:
                continue
            out.append(e)
        return out

    def is_empty(self) -> bool:
        return not self._by_kind


def scan_directory(root) -> ScanResult:
    """
    Recursively find all ``.hpl`` files below ``root`` and index them
    by kind and timestamp.

    :param root: root directory to scan. May be a ``Proc`` folder, any \
        directory below it (e.g. a single year, month or day), or a \
        parent directory that also contains unrelated trees (which \
        simply contribute no ``.hpl`` files).
    :returns: a populated :class:`ScanResult`.
    """
    root = Path(root)
    result = ScanResult(root)
    if not root.exists():
        return result
    for dirpath, _dirnames, filenames in os.walk(root):
        for fname in filenames:
            if not fname.lower().endswith('.hpl'):
                continue
            full = Path(dirpath) / fname
            parsed = parse_filename(full)
            if parsed is None:
                result.skipped.append(str(full))
                continue
            result._add(FileEntry(path=full, kind=parsed.kind,
                                   timestamp=parsed.timestamp,
                                   system_id=parsed.system_id))
    result._finalize()
    return result
