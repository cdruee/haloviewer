#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5
"""
Lidar Sync GUI
==============

Selective synchronisation of Wind Lidar data (Metek / Proc / Raw) from a
source to a destination. Depending on the setup, either the source (e.g.
the SMB share of the lidar control PC) or the destination (e.g. a remote
backup) may be the slower network side -- the scan cache and persistence
(see point 8 below) therefore treat both sides exactly the same.

Only the Python standard library is used (tkinter, os, shutil, re,
threading, ...). Under Miniconda this normally runs without any further
installation; if "import tkinter" fails,
    conda install tk
(a very small, official Anaconda package) will help.

------------------------------------------------------------------------
Important assumptions (please check these match your setup):
------------------------------------------------------------------------

1. Groups/subgroups and their file prefixes are fixed in GROUPS below.
   A file belongs to a subgroup if its filename (not the path) starts
   with the subgroup name; for "Raw" the prefix "aet_<subgroup>" is also
   accepted. Files that do not match any of the listed prefixes are not
   picked up by this tool and are therefore not synchronised.
   "Wind_Profile" and "Processed_Wind_Profile" are listed as separate
   Proc subgroups (the prefixes do not overlap -- "Processed_..." does
   not start with "Wind_Profile" -- so their order in the list does not
   matter).

2. "Current file" per top-level group (Metek/Proc/Raw): the newest file
   is determined across ALL files of the group (recursively, group-wide,
   not per subgroup) using a timestamp contained in the filename. Two
   timestamp notations are recognised:
     - yyyymmdd_hhmmss   (e.g. VAD_77_20221020_111520.hpl)
     - ddmmyy-hhmmss     (e.g. Background_170926-065156.txt)
   Files without any recognisable timestamp (e.g. system_parameters_*.txt)
   are never treated as the "current file" and are always handled as
   regular files.

3. "copy current files": includes the currently determined file (=
   presumably still open in the lidar control program) per top-level
   group in the transfer.

4. "copy everything": ignores the "already exists at the destination
   with matching name/time/size" check and overwrites/copies all
   eligible files again. Both tick-boxes are one-off actions and are
   automatically cleared again after every synchronisation.

5. The "signal lights" in the destination area are independent of the
   checkboxes: they show whether synchronisable files are still missing
   for this subgroup (orange, excluding the "current file") or not
   (green). If the subgroup is greyed out on the source side (no files),
   it is greyed out here too (grey).

6. The "already present" check compares relative path, file size and
   modification time (2-second tolerance, because of SMB/FAT rounding).

7. For simplicity and robustness, each file is copied in one go (not in
   chunks); cancelling therefore takes effect between two files, not
   within a single file. For the file sizes typical here (usually KB to
   a few MB) this should in practice be "as fast as possible". Files are
   first copied under a temporary name (".part") and only then renamed
   atomically, so that a partially copied file never appears under its
   real name at the destination.

8. Persistence: the most recently selected source/destination paths as
   well as the entire directory scan cache (size/timestamp per file) are
   automatically written to a JSON file in the signed-in Windows user's
   roaming profile after every scan (%APPDATA%\\LidarSyncGUI\\state.json,
   see get_config_path()) and reloaded on the next program start. The
   scan then starts immediately with a "warm" cache -- unchanged folders
   do not need to be listed over the network again, even after a program
   restart. The "full scan" tick-box next to the refresh button discards
   the cache once before the next scan (in case you ever don't trust the
   results) and then unticks itself automatically afterwards.

9. There is NO automatic background scan any more (earlier versions
   silently rescanned every 10 minutes). A scan now only happens when
   triggered: program start (with the persisted paths), a path change, a
   click on the refresh button (↻), or automatically one minute before a
   scheduled sync (see point 10).

10. Scheduled sync ("sync every ... h", tick-box in the Sync frame): sets
    up an automatic synchronisation every 1/2/3/6/12/24 hours. While
    ticked, the manual sync button as well as "copy current files"/"copy
    everything" are greyed out (the automatic sync then always runs with
    their default value, i.e. without the current file and without
    forced re-copying); the interval menu remains usable. Below it, a
    countdown (format hh:mm, updated every minute) counts down to the
    next sync. One minute before it elapses, a scan is triggered
    automatically so the sync starts with an up-to-date file list.

Anyone needing different groups/prefixes or a different "current file"
criterion can most easily adjust GROUPS above, or extract_timestamp() /
classify_subgroup(), accordingly.
"""

from __future__ import annotations

import os
import re
import json
import shutil
import threading
import queue
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


# --------------------------------------------------------------------------
# Configuration: groups and their subgroups (order = display order)
# --------------------------------------------------------------------------

GROUPS: Dict[str, List[str]] = {
    "Metek": ["Stare", "VAD"],
    "Proc": [
        "system_parameters", "Background", "Stare", "VAD", "RHI",
        "User1", "User2", "User3", "User4", "User5",
        "Processed_Wind_Profile", "Wind_Profile",
    ],
    "Raw": [
        "Stare", "VAD", "RHI", "User1", "User2", "User3", "User4", "User5",
    ],
}

MTIME_TOLERANCE_S = 2.0
COPY_SUFFIX = ".part"

LIGHT_GREY = "#a0a0a0"
LIGHT_GREEN = "#2ecc71"
LIGHT_ORANGE = "#e67e22"
DISABLED_FG = "#a0a0a0"

# Fixed row height for the tree rows (see Row): a Checkbutton (with its
# built-in box indicator) is inherently a bit taller than a 14px canvas +
# label, which otherwise makes the source (checkboxes) and destination
# (lights) trees different heights and stops the two trees lining up row
# for row. A forced height that is identical for both row kinds (together
# with pack_propagate(False)) keeps them exactly in sync.
TREE_ROW_HEIGHT = 24


# --------------------------------------------------------------------------
# Pure logic (no GUI code) -- separately testable
# --------------------------------------------------------------------------

_TS_RE_UNDERSCORE = re.compile(r"(\d{8})_(\d{6})")
_TS_RE_DASH = re.compile(r"(?<!\d)(\d{6})-(\d{6})(?!\d)")


def extract_timestamp(filename: str) -> Optional[datetime]:
    """Extracts a timestamp from a filename, if possible."""
    m = _TS_RE_UNDERSCORE.search(filename)
    if m:
        try:
            return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            pass
    m = _TS_RE_DASH.search(filename)
    if m:
        dd, mo, yy = m.group(1)[0:2], m.group(1)[2:4], m.group(1)[4:6]
        hh, mi, ss = m.group(2)[0:2], m.group(2)[2:4], m.group(2)[4:6]
        try:
            return datetime(2000 + int(yy), int(mo), int(dd), int(hh), int(mi), int(ss))
        except ValueError:
            pass
    return None


def classify_subgroup(group: str, basename: str) -> Optional[str]:
    """Assigns a filename to a subgroup of the given group."""
    for sub in GROUPS[group]:
        if basename.startswith(sub):
            return sub
        if group == "Raw" and basename.startswith("aet_" + sub):
            return sub
    return None


@dataclass
class FileEntry:
    relpath: str  # relative to the root directory, with "/" as separator
    size: int
    mtime: float


@dataclass
class SubgroupData:
    name: str
    enabled: bool = False
    files: List[FileEntry] = field(default_factory=list)


@dataclass
class GroupData:
    name: str
    exists: bool = False
    subgroups: Dict[str, SubgroupData] = field(default_factory=dict)
    current_file: Optional[str] = None  # relpath of the group-wide newest file


# Cache entry per directory: (dir_mtime_ns, [(name, size, mtime, is_dir), ...])
_DirListing = tuple


def _list_dir_cached(dirpath: Path, cache: dict) -> List[tuple]:
    """Lists a directory via os.scandir() and returns, per entry,
    (name, size, mtime, is_dir).

    Two optimisations over os.walk()+Path.stat():
    1. entry.stat() is called on the DirEntry from scandir() instead of a
       separate Path(...).stat(). On Windows, listing a folder
       (FindFirstFile/FindNextFile, which scandir() uses) already returns
       the size and timestamp of every file -- an additional stat() call
       per file was simply a second, unnecessary network round trip to
       the SMB server. That was most likely the main reason for slow
       scanning over a higher-latency connection (e.g. LTE) -- bandwidth
       barely plays a role here; it is simply a very large number of
       small requests.
    2. The result is cached keyed on the directory's own modification
       time: as long as a folder's contents do not change (no file
       added/removed/renamed), its mtime does not change either, and a
       repeated scan (manual refresh, or the automatic scan shortly
       before a scheduled sync) can reuse the last listing without
       querying the folder over the network again. This helps
       particularly with the many completed old day/year folders that
       never change again.

    Note: some network shares do not update the directory mtime entirely
    reliably. If the cache ever "lags" (new files do not show up), the
    "full scan" tick-box in the GUI helps.
    """
    try:
        dmtime = dirpath.stat().st_mtime_ns
    except OSError:
        return []
    key = str(dirpath)
    cached = cache.get(key)
    if cached is not None and cached[0] == dmtime:
        return cached[1]
    results = []
    try:
        with os.scandir(dirpath) as it:
            for entry in it:
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    results.append((entry.name, None, None, True))
                else:
                    try:
                        st = entry.stat()  # cached DirEntry info, no extra round trip
                    except OSError:
                        continue
                    results.append((entry.name, st.st_size, st.st_mtime, False))
    except OSError:
        results = []
    cache[key] = (dmtime, results)
    return results


def _walk_cached(dirpath: Path, cache: dict):
    """Recursive generator over all files below dirpath, yielding
    (full_path: Path, size: int, mtime: float)."""
    for name, size, mtime, is_dir in _list_dir_cached(dirpath, cache):
        full = dirpath / name
        if is_dir:
            yield from _walk_cached(full, cache)
        else:
            yield full, size, mtime


def scan_root(root: Path, cache: Optional[dict] = None):
    """Scans a root directory and classifies all matching files.

    `cache` is an optional dict reused by the caller across multiple
    scans (see _list_dir_cached). Without a cache, every call reads
    everything fresh from the network.

    Returns: (dict[group] -> GroupData, any_group_found: bool)
    """
    if cache is None:
        cache = {}
    result: Dict[str, GroupData] = {}
    any_found = False
    for group, subnames in GROUPS.items():
        gdata = GroupData(name=group)
        gpath = root / group
        if gpath.is_dir():
            gdata.exists = True
            any_found = True
            for sub in subnames:
                gdata.subgroups[sub] = SubgroupData(name=sub)
            latest_ts = None
            latest_relpath = None
            for full, size, mtime in _walk_cached(gpath, cache):
                fn = full.name
                sub = classify_subgroup(group, fn)
                if sub is None:
                    continue
                rel = full.relative_to(root).as_posix()
                entry = FileEntry(relpath=rel, size=size, mtime=mtime)
                gdata.subgroups[sub].files.append(entry)
                ts = extract_timestamp(fn)
                if ts is not None and (latest_ts is None or ts > latest_ts):
                    latest_ts = ts
                    latest_relpath = rel
            gdata.current_file = latest_relpath
            for sdata in gdata.subgroups.values():
                sdata.enabled = len(sdata.files) > 0
        result[group] = gdata
    return result, any_found


def dest_file_matches(dest_root: Path, entry: FileEntry) -> bool:
    """Checks whether an identical file (name/size/time) already exists at the destination."""
    p = dest_root.joinpath(*PurePosixPath(entry.relpath).parts)
    try:
        st = p.stat()
    except OSError:
        return False
    if st.st_size != entry.size:
        return False
    if abs(st.st_mtime - entry.mtime) > MTIME_TOLERANCE_S:
        return False
    return True


def eligible_files(gdata: GroupData, sub: str, include_current: bool) -> List[FileEntry]:
    sdata = gdata.subgroups.get(sub)
    if not sdata or not sdata.enabled:
        return []
    out = []
    for f in sdata.files:
        if not include_current and gdata.current_file == f.relpath:
            continue
        out.append(f)
    return out


def group_enabled(gdata: GroupData) -> bool:
    return gdata.exists and any(s.enabled for s in gdata.subgroups.values())


def subgroup_light(gdata_src: GroupData, dest_root: Optional[Path], sub: str) -> str:
    sdata = gdata_src.subgroups.get(sub)
    if not sdata or not sdata.enabled:
        return "grey"
    if dest_root is None:
        return "orange"
    files = eligible_files(gdata_src, sub, include_current=False)
    if not files:
        return "green"
    for f in files:
        if not dest_file_matches(dest_root, f):
            return "orange"
    return "green"


def group_light(gdata_src: GroupData, dest_root: Optional[Path]) -> str:
    if not group_enabled(gdata_src):
        return "grey"
    lights = [
        subgroup_light(gdata_src, dest_root, sub)
        for sub, sdata in gdata_src.subgroups.items()
        if sdata.enabled
    ]
    if any(l == "orange" for l in lights):
        return "orange"
    return "green"


def build_transfer_list(
    src_data: Dict[str, GroupData],
    dest_root: Path,
    selection: Dict[str, object],
    include_current: bool,
    force_all: bool,
) -> List[FileEntry]:
    """selection[group] is either 'all' (group tick-box set), a set of
    subgroup names, or missing/None (nothing selected)."""
    todo: List[FileEntry] = []
    for group, gdata in src_data.items():
        sel = selection.get(group)
        if not sel:
            continue
        subs = gdata.subgroups.keys() if sel == "all" else sel
        for sub in subs:
            for f in eligible_files(gdata, sub, include_current):
                if force_all or not dest_file_matches(dest_root, f):
                    todo.append(f)
    return todo


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


# --------------------------------------------------------------------------
# Persistence (per Windows user): last-selected paths + scan cache
# --------------------------------------------------------------------------

STATE_VERSION = 1
STATE_FILENAME = "state.json"


def get_config_dir() -> Path:
    """Roaming profile folder of the signed-in user (per-user, as is usual
    on Windows); falls back to the home directory outside Windows."""
    base = os.environ.get("APPDATA")
    if not base:
        base = str(Path.home())
    d = Path(base) / "LidarSyncGUI"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_config_path() -> Path:
    return get_config_dir() / STATE_FILENAME


def save_state(path: Path, source_path: str, dest_path: str,
                src_cache: dict, dst_cache: dict) -> None:
    """Writes paths + scan cache atomically as JSON (writing to a
    temporary file, then os.replace, so that a crash midway through
    writing never leaves a corrupt state.json behind)."""
    data = {
        "version": STATE_VERSION,
        "source_path": source_path,
        "dest_path": dest_path,
        "src_cache": src_cache,
        "dst_cache": dst_cache,
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp_path, path)


def load_state(path: Path) -> Optional[dict]:
    """Reads a previously saved state.json. Returns None if none exists,
    it is unreadable, or it is from an incompatible version -- the app
    then simply starts with an empty state instead of crashing."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        return None
    return data


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

# Photo of the lidar instrument (120x160 PNG), base64-embedded so the
# script stays a single file and does not depend on an image in the
# working directory. tk.PhotoImage can load PNG directly since Tk 8.6, so
# no Pillow/extra installation is needed.
LIDAR_PHOTO_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAHgAAACgCAMAAADw11iiAAABhWlDQ1BJQ0MgcHJvZmlsZQAAKJF9kb9Lw0AcxV9bxV8tgnYQ"
    "cchQnexiRRxrFYpQIdQKrTqYXPoLmjQkKS6OgmvBwR+LVQcXZ10dXAVB8AeIf4A4KbpIid9LCi1iPDjuw7t7j7t3gL9RYarZ"
    "FQdUzTLSyYSQza0KPa/owxAGEEJMYqY+J4opeI6ve/j4ehflWd7n/hwhJW8ywCcQx5luWMQbxDObls55nzjMSpJCfE48adAF"
    "iR+5Lrv8xrnosJ9nho1Mep44TCwUO1juYFYyVOJp4oiiapTvz7qscN7irFZqrHVP/sJgXltZ5jrNMSSxiCWIECCjhjIqsBCl"
    "VSPFRJr2Ex7+UccvkksmVxmMHAuoQoXk+MH/4He3ZiE25SYFE0D3i21/jAM9u0Czbtvfx7bdPAECz8CV1vZXG8DsJ+n1thY5"
    "Aga3gYvrtibvAZc7wMiTLhmSIwVo+gsF4P2MvikHDN8C/Wtub619nD4AGeoqdQMcHAITRcpe93h3b2dv/55p9fcDoYFyuTbP"
    "IuAAAAL9UExURQAAAAgECwEHChIGDhAIBgkOAQUPMA4RDQ4RFRAVAA8ZARgWBDYNCRQZIAYaShgbFCEZFRYfAhkcHB4bHh4f"
    "BSgbEBofJxskAyQiAx0jEx8oASgmASAlLCUkJiUlHgcuKVgZDh8pOjAmKGcaBS8tCBUuXycwBywsJSgsNCwsLCcwHSszFSw2"
    "Ag09ODIyM04rIQ0+Pjo0GTs0JUgzBD4yPT4zNTY2Lzs4Byw3SjM2Pio5PjQ9FTk5Nzg5PD47FzY/Cjc9KzhBARVDiY8pDTxE"
    "AEM/QT9BOVE7Pz5BQDxBSjhCUzVDWFU+PkBKFk1DPkJLC0dJGEZINVNDR1dCRjpJXlJFOUtGSEdKKUZIR0VOBDhKZD5JWoE5"
    "JEJJU0dJQXg+OWZEPF1MBz9PZ1lNIUVQYUhQXFBPTU9PUktQV19ONF9OS1tPUlFZDlFYGlZTTFJYJlVWQ1ZXNmFTRlVdAk1X"
    "aFBXYlVXWldXVVlfAMo+F5VQJFleaV1eYWJdW11fW2dcWmddY15mHF9nEG9cXFZidbpKImNpB1xkXG5gU2NmNXBjO55VOola"
    "V2FmcWVmY29jYmRrLJBbSWplYmVmaWlpVWxqSHZqaHFsaWxuam51F2lwaHB2Cm51JIBqa2RxhIBtTodsSHBwc2xxe3xvYHR0"
    "RdJaJn5ySIVwSnV0cn1ycHh0bYZ4eoh7Vn97fHp9e32EJJd5SoGFEtdnO3t+iYt+UJOAKZZ7W4OCVYiEK5J9WpF9Y4Z/eIx+"
    "boSBaLhyW4CBeHaClYKGSIeGOspyS4ODjJGDXY+BhIKEk4WFhH+Gj5CHT+VyNet1LIeMm5CUHYiNlY6Ni5iKjJyMZ4yOkZOQ"
    "YqaKa5SWN5SQiJ2Pfp6Qc/R/NI+WpZaWlpGXn/ODRKCiJPGGV5uen62ceJafraKdnpmgqK2ega6eiqWgmaCnsKanqqmnpbGt"
    "q62usbqtmq2wrbWvp6uxu7ytrrW2ub2/xci+rcTEwcbIzdTT1eXi4+7r6/j1+vv29PX49P358DACth4AACAASURBVHjajZwP"
    "TNP33u+788womBOGeIYwIbmGFFlJb/SxcLgtlCW2wHnsH0ooNQvNTjZ+rf0DbV3bJW0pRcMD5raQllJ6YCKUKDAKj2HXqWDc"
    "zNS6RO1kTszpcoOHqePPtnPwUfGZyX1/f7+iuLObez/2P/T3+n7+fj+fX4uskt1cgbpaXF0tEFeqBdzK6upqkzgYHDY1d5uD"
    "eFJnrjN31NWZzcPBajMjdXXVteQHxyAd5mPBoMkU7DDjp3jWhB93kJchh44FzeJqNX5w6BD53WPm6lqLpT025XAMTbEE6loz"
    "3tdhMVWLK7lYhUBsxtMOc63FbO6wqMvLxdXmary5qQPLqCbYOrFYzDw2dzTVVZs7mPWQI2NN9Ovkt+gXyvGr5MEhc0fHsUOH"
    "ak0WS6Pv3502Kyt4LDgcBMqMhQt2E3BRUrOOYDDYATAOpVabqnFMcTlMY66rLqdFjGOZX4DMx3BbzYi521RXTZPNeLeJ3L+P"
    "n9YegqHUJlOj3ed0sszJBePXg+rduwsE3CJaLbxKTAhLmbpNoZC7GpqDViSuToLF5WK4Abf0wfDuDpNJrSbGUKtDjbSTzGZT"
    "OTH9IXpp1dXU4BBkCnLtGousUAy1TOXVZjF3p8AEGpYLa1eDhWdmi0lq17dbiCuTLi4iCzCBaLo4MjgRjY5enI7FrsS/u3b9"
    "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"
)


class Row:
    """A row in the tree (either a top-level group or a subgroup)."""

    def __init__(self, parent, text, indent, kind):
        self.text = text
        self.kind = kind  # "checkbox" or "light"
        self.enabled = False
        self.frame = tk.Frame(parent, height=TREE_ROW_HEIGHT)
        self.frame.pack(fill="x", padx=(4 + indent, 4), pady=1)
        self.frame.pack_propagate(False)

        if kind == "checkbox":
            self.var = tk.BooleanVar(value=False)
            self.widget = tk.Checkbutton(
                self.frame, text=text, variable=self.var, anchor="w",
                onvalue=True, offvalue=False,
            )
            self.widget.pack(side="left", fill="x")
        else:
            self.canvas = tk.Canvas(self.frame, width=14, height=14,
                                     highlightthickness=0)
            self.canvas.pack(side="left", padx=(0, 6))
            self.oval = self.canvas.create_oval(2, 2, 12, 12, fill=LIGHT_GREY,
                                                 outline="")
            self.label = tk.Label(self.frame, text=text, anchor="w")
            self.label.pack(side="left", fill="x")

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        if self.kind == "checkbox":
            if enabled:
                self.widget.configure(state="normal", fg="black")
            else:
                self.var.set(False)
                self.widget.configure(state="disabled", fg=DISABLED_FG)
        else:
            fg = "black" if enabled else DISABLED_FG
            self.label.configure(fg=fg)
            if not enabled:
                self.canvas.itemconfig(self.oval, fill=LIGHT_GREY)

    def set_locked(self, locked: bool):
        """For checkbox subgroups: greyed out because the parent group
        already covers everything (but stays conceptually 'enabled')."""
        if self.kind != "checkbox":
            return
        if not self.enabled:
            return
        if locked:
            self.widget.configure(state="disabled", fg=DISABLED_FG)
        else:
            self.widget.configure(state="normal", fg="black")

    def set_light(self, color: str):
        if self.kind != "light":
            return
        colors = {"grey": LIGHT_GREY, "green": LIGHT_GREEN, "orange": LIGHT_ORANGE}
        self.canvas.itemconfig(self.oval, fill=colors.get(color, LIGHT_GREY))


class TreePanel:
    """Builds the Metek/Proc/Raw tree view for one side (source/destination)."""

    def __init__(self, parent, kind, on_toggle=None):
        self.kind = kind
        self.on_toggle = on_toggle
        self.container = tk.Frame(parent)
        self.container.pack(fill="both", expand=True)

        self.nodata_label = tk.Label(
            self.container, text="no data found", fg=DISABLED_FG,
            font=("TkDefaultFont", 12, "italic"),
        )
        self.tree_frame = tk.Frame(self.container)

        self.group_rows: Dict[str, Row] = {}
        self.sub_rows: Dict[str, Dict[str, Row]] = {}

        for group, subnames in GROUPS.items():
            grow = Row(self.tree_frame, group, indent=0, kind=kind)
            self.group_rows[group] = grow
            self.sub_rows[group] = {}
            if kind == "checkbox":
                grow.var.trace_add("write", lambda *_a, g=group: self._group_toggled(g))
            for sub in subnames:
                srow = Row(self.tree_frame, sub, indent=22, kind=kind)
                self.sub_rows[group][sub] = srow
                if kind == "checkbox":
                    srow.var.trace_add("write", lambda *_a: self._fire_toggle())

        self.show_tree(False)

    def show_tree(self, show: bool):
        if show:
            self.nodata_label.pack_forget()
            self.tree_frame.pack(fill="both", expand=True)
        else:
            self.tree_frame.pack_forget()
            self.nodata_label.pack(expand=True)

    def _group_toggled(self, group):
        if self.kind != "checkbox":
            return
        locked = self.group_rows[group].var.get()
        for srow in self.sub_rows[group].values():
            srow.set_locked(locked)
        self._fire_toggle()

    def _fire_toggle(self):
        if self.on_toggle:
            self.on_toggle()

    def apply_scan(self, data: Optional[Dict[str, GroupData]], other_root: Optional[Path]):
        """Updates enabled state/lights from the scan results.
        `data` always belongs to the source (even if this panel is the
        destination), since subgroups on the destination side are tied to
        the source. `other_root` is the destination root directory (used
        for comparison) when kind=='light', and unused when
        kind=='checkbox'.
        """
        if data is None:
            self.show_tree(False)
            return
        self.show_tree(True)
        for group, gdata in data.items():
            genabled = group_enabled(gdata)
            self.group_rows[group].set_enabled(genabled)
            if self.kind == "light":
                self.group_rows[group].set_light(group_light(gdata, other_root))
            for sub, sdata in gdata.subgroups.items():
                srow = self.sub_rows[group][sub]
                srow.set_enabled(sdata.enabled)
                if self.kind == "checkbox" and genabled and self.group_rows[group].var.get():
                    srow.set_locked(True)
                if self.kind == "light":
                    srow.set_light(subgroup_light(gdata, other_root, sub))

    def get_selection(self) -> Dict[str, object]:
        """Only for kind=='checkbox': returns, per group, 'all', a set of
        subgroups, or nothing (group missing from the dict)."""
        sel: Dict[str, object] = {}
        for group, grow in self.group_rows.items():
            if not grow.enabled:
                continue
            if grow.var.get():
                sel[group] = "all"
                continue
            chosen = {s for s, srow in self.sub_rows[group].items()
                      if srow.enabled and srow.var.get()}
            if chosen:
                sel[group] = chosen
        return sel


class ProgressDialog(tk.Toplevel):
    def __init__(self, master, todo: List[FileEntry], src_root: Path,
                 dest_root: Path, on_done):
        super().__init__(master)
        self.title("Synchronising...")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self.todo = todo
        self.src_root = src_root
        self.dest_root = dest_root
        self.on_done = on_done
        self.total_bytes = max(sum(f.size for f in todo), 1)
        self.cancel_event = threading.Event()
        self.msg_queue: "queue.Queue" = queue.Queue()

        pad = {"padx": 12, "pady": 6}
        self.progress = ttk.Progressbar(self, orient="horizontal", length=420,
                                         mode="determinate",
                                         maximum=self.total_bytes)
        self.progress.pack(fill="x", **pad)

        self.current_label = tk.Label(self, text="", anchor="w",
                                       wraplength=420, justify="left")
        self.current_label.pack(fill="x", padx=12)

        self.status_label = tk.Label(self, text=f"0 / {human_size(self.total_bytes)}",
                                      anchor="w")
        self.status_label.pack(fill="x", padx=12, pady=(0, 6))

        btn = tk.Button(self, text="Cancel", command=self._on_cancel)
        btn.pack(pady=(0, 10))

        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()
        self.after(80, self._poll)

    def _on_cancel(self):
        self.cancel_event.set()

    def _run(self):
        done_bytes = 0
        copied = 0
        for entry in self.todo:
            if self.cancel_event.is_set():
                break
            self.msg_queue.put(("current", entry.relpath, done_bytes))
            src_path = self.src_root.joinpath(*PurePosixPath(entry.relpath).parts)
            dest_path = self.dest_root.joinpath(*PurePosixPath(entry.relpath).parts)
            tmp_path = dest_path.with_name(dest_path.name + COPY_SUFFIX)
            try:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, tmp_path)
                if self.cancel_event.is_set():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                    break
                os.replace(tmp_path, dest_path)
                copied += 1
            except OSError as exc:
                self.msg_queue.put(("error", f"{entry.relpath}: {exc}", done_bytes))
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except OSError:
                    pass
            done_bytes += entry.size
            self.msg_queue.put(("progress", entry.relpath, done_bytes))
        self.msg_queue.put(("done", copied, done_bytes))

    def _poll(self):
        try:
            while True:
                kind, a, b = self.msg_queue.get_nowait()
                if kind == "current":
                    self.current_label.configure(text=f"Copying: {a}")
                elif kind == "progress":
                    self.progress["value"] = b
                    self.status_label.configure(
                        text=f"{human_size(b)} / {human_size(self.total_bytes)}")
                elif kind == "error":
                    self.status_label.configure(text=f"Error: {a}")
                elif kind == "done":
                    self.progress["value"] = b
                    cancelled = self.cancel_event.is_set()
                    self.destroy()
                    self.on_done(copied=a, cancelled=cancelled)
                    return
        except queue.Empty:
            pass
        self.after(80, self._poll)


class LidarSyncApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Lidar Sync")
        # Narrower than before (source/destination were stretched
        # unnecessarily wide at 1100px width by the weight=1 columns --
        # with the same column weighting, a narrower window automatically
        # gives narrower, less "washed out"-looking tree areas, about 2/3
        # of the previous width per side) and noticeably taller (the 24
        # tree rows -- 3 top-level groups + 21 subgroups -- did not fully
        # fit at 680px, the list used to cut off around Raw/User2, for
        # example). Height reduced again by ~15% from the previous
        # 820x920/minsize 760x780 once that fit was confirmed.
        root.geometry("820x780")
        root.minsize(760, 663)

        self.source_root: Optional[Path] = None
        self.dest_root: Optional[Path] = None
        self.src_data: Optional[Dict[str, GroupData]] = None
        self.dest_data: Optional[Dict[str, GroupData]] = None
        self.scanning = False
        self.rescan_pending = False
        self.pending_transfer: List[FileEntry] = []
        self.scan_queue: "queue.Queue" = queue.Queue()
        # Directory caches persisted across multiple scans (and program
        # starts) (see _list_dir_cached); kept separate for source/
        # destination so a path change on one side does not affect the
        # other side.
        self._src_cache: dict = {}
        self._dst_cache: dict = {}
        # Set by _manual_refresh when "full scan" is ticked; only the
        # next _trigger_rescan() call that actually starts a new scan
        # (not merely marks one as pending) clears the caches for that --
        # that is the only point at which no background thread is
        # concurrently reading/writing them.
        self._full_scan_requested = False
        self._full_scan_just_ran = False
        self._last_scan_at: Optional[float] = None

        self.copy_current_var = tk.BooleanVar(value=False)
        self.copy_everything_var = tk.BooleanVar(value=False)
        self.full_scan_var = tk.BooleanVar(value=False)

        # -- Scheduled sync ("sync every ... h") --
        self.sync_schedule_var = tk.BooleanVar(value=False)
        self.sync_interval_var = tk.StringVar(value="1")
        self._next_sync_at: Optional[float] = None  # epoch seconds
        self._schedule_prescan_done = False
        self._schedule_timer_handle: Optional[str] = None

        self._build_ui()
        self._load_persisted_state()

    # -- UI construction ---------------------------------------------------
    def _build_ui(self):
        main = tk.Frame(self.root)
        main.pack(fill="both", expand=True, padx=8, pady=8)
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=0)
        main.columnconfigure(2, weight=1)
        main.rowconfigure(0, weight=1)

        # -- Source (left) --
        left = tk.LabelFrame(main, text="Source")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.source_var = tk.StringVar()
        self._build_path_row(left, self.source_var, self._browse_source,
                              self._path_changed_source)
        self.source_panel = TreePanel(left, kind="checkbox",
                                       on_toggle=self._on_selection_changed)

        # -- Middle --
        mid = tk.Frame(main)
        mid.grid(row=0, column=1, sticky="ns", padx=6)

        # Photo of the lidar instrument, fixed at the top centre (not part
        # of the centred button cluster below, hence before the top spacer).
        self._lidar_photo = tk.PhotoImage(data=LIDAR_PHOTO_B64)
        tk.Label(mid, image=self._lidar_photo).pack(pady=(0, 10))

        tk.Frame(mid).pack(expand=True)  # top spacer

        # -- Scan frame --
        # fill="x" on both frames (Scan and Sync below) matches their
        # widths to each other: pack stretches "fill=x" children to the
        # width the widest sibling in "mid" needs anyway (that is the Sync
        # frame with the schedule row), so Scan becomes exactly as wide.
        scan_frame = tk.LabelFrame(mid, text="Scan", padx=8, pady=8)
        scan_frame.pack(fill="x", pady=(0, 12))
        self.refresh_btn = tk.Button(scan_frame, text="↻", font=("TkDefaultFont", 16, "bold"),
                                      width=3, command=self._manual_refresh)
        self.refresh_btn.pack(pady=(0, 2))
        tk.Checkbutton(scan_frame, text="full scan", variable=self.full_scan_var).pack(pady=(0, 6))
        self.scan_status_label = tk.Label(scan_frame, text="no scan yet", fg="#555555",
                                           wraplength=140, justify="center")
        self.scan_status_label.pack()

        # -- Sync frame --
        sync_frame = tk.LabelFrame(mid, text="Sync", padx=8, pady=8)
        sync_frame.pack(fill="x")
        self.sync_btn = tk.Button(sync_frame, text="→", font=("TkDefaultFont", 18, "bold"),
                                   width=3, command=self._start_sync, state="disabled")
        self.sync_btn.pack(pady=(0, 10))
        self.size_label = tk.Label(sync_frame, text="-", wraplength=140, justify="center")
        self.size_label.pack(pady=(0, 16))

        cur_frame = tk.Frame(sync_frame)
        cur_frame.pack(anchor="w", pady=2)
        self.copy_current_chk = tk.Checkbutton(
            cur_frame, text="⚠ copy current files", variable=self.copy_current_var,
            command=self._on_selection_changed)
        self.copy_current_chk.pack(anchor="w")
        self.copy_everything_chk = tk.Checkbutton(
            cur_frame, text="⚠ copy everything", variable=self.copy_everything_var,
            command=self._on_selection_changed)
        self.copy_everything_chk.pack(anchor="w")

        sched_frame = tk.Frame(sync_frame)
        sched_frame.pack(anchor="w", pady=(12, 0))
        tk.Checkbutton(sched_frame, text="sync every", variable=self.sync_schedule_var,
                        command=self._on_schedule_toggle).pack(side="left")
        self.interval_menu = tk.OptionMenu(sched_frame, self.sync_interval_var,
                                            "1", "2", "3", "6", "12", "24",
                                            command=self._on_interval_changed)
        self.interval_menu.configure(width=2)
        self.interval_menu.pack(side="left", padx=(4, 2))
        tk.Label(sched_frame, text="h").pack(side="left")

        self.countdown_label = tk.Label(sync_frame, text="", fg="#555555",
                                         wraplength=140, justify="center")
        self.countdown_label.pack(pady=(2, 0))

        self.sync_status_label = tk.Label(sync_frame, text="", fg="#555555",
                                           wraplength=140, justify="center")
        self.sync_status_label.pack(pady=(12, 0))

        tk.Frame(mid).pack(expand=True)  # bottom spacer

        # -- Destination (right) --
        right = tk.LabelFrame(main, text="Destination")
        right.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
        self.dest_var = tk.StringVar()
        self._build_path_row(right, self.dest_var, self._browse_dest,
                              self._path_changed_dest)
        self.dest_panel = TreePanel(right, kind="light")

    def _build_path_row(self, parent, var, browse_cmd, changed_cmd):
        row = tk.Frame(parent)
        row.pack(fill="x", padx=6, pady=6)
        entry = tk.Entry(row, textvariable=var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: changed_cmd())
        entry.bind("<FocusOut>", lambda _e: changed_cmd())
        tk.Button(row, text="...", command=browse_cmd).pack(side="left", padx=(6, 0))

    # -- Path selection ---------------------------------------------------
    def _browse_source(self):
        d = filedialog.askdirectory(title="Select source folder",
                                     initialdir=self.source_var.get() or None)
        if d:
            self.source_var.set(d)
            self._path_changed_source()

    def _browse_dest(self):
        d = filedialog.askdirectory(title="Select destination folder",
                                     initialdir=self.dest_var.get() or None)
        if d:
            self.dest_var.set(d)
            self._path_changed_dest()

    def _path_changed_source(self):
        text = self.source_var.get().strip()
        self.source_root = Path(text) if text else None
        self._trigger_rescan()

    def _path_changed_dest(self):
        text = self.dest_var.get().strip()
        self.dest_root = Path(text) if text else None
        self._trigger_rescan()

    # -- Persistence ---------------------------------------------------
    def _load_persisted_state(self):
        """Loads the last-selected paths + scan cache from the previous
        program run (if any) and immediately starts a scan with them
        (usually fast, thanks to the warm cache)."""
        data = load_state(get_config_path())
        if not data:
            return
        self._src_cache = data.get("src_cache") or {}
        self._dst_cache = data.get("dst_cache") or {}
        sp = (data.get("source_path") or "").strip()
        dp = (data.get("dest_path") or "").strip()
        if sp:
            self.source_var.set(sp)
            self.source_root = Path(sp)
        if dp:
            self.dest_var.set(dp)
            self.dest_root = Path(dp)
        if sp or dp:
            self._trigger_rescan()

    def _save_state(self, src: Optional[Path], dst: Optional[Path]):
        """Called from the scan background thread after the caches have
        been updated. Purely best-effort: an error while saving (e.g. a
        full disk, no write permission in the profile) must never crash
        the scan itself."""
        try:
            save_state(get_config_path(), str(src) if src else "",
                       str(dst) if dst else "", self._src_cache, self._dst_cache)
        except Exception:
            pass

    # -- Scanning (background thread, so SMB access does not block the GUI)
    def _manual_refresh(self):
        """Handler for the refresh button (↻). If "full scan" is ticked,
        the cache is cleared before the next scan that actually starts
        (all file info is then queried completely fresh); the tick-box
        then clears itself automatically afterwards, like the two
        "copy ..." tick-boxes."""
        if self.full_scan_var.get():
            self._full_scan_requested = True
        self._trigger_rescan()

    def _trigger_rescan(self):
        if self.scanning:
            self.rescan_pending = True
            return
        if self._full_scan_requested:
            # Safe to clear only here: at this point no background thread
            # is guaranteed to still be using self._src_cache/_dst_cache
            # (see the comment at the initialisation above).
            self._src_cache.clear()
            self._dst_cache.clear()
            self._full_scan_requested = False
            self._full_scan_just_ran = True
        else:
            self._full_scan_just_ran = False
        self.scanning = True
        self.refresh_btn.configure(state="disabled")
        self.scan_status_label.configure(text="Scanning...")
        src, dst = self.source_root, self.dest_root
        threading.Thread(target=self._scan_worker, args=(src, dst), daemon=True).start()
        self.root.after(80, self._poll_scan)

    def _scan_worker(self, src: Optional[Path], dst: Optional[Path]):
        src_data = src_found = None
        dst_data = dst_found = None
        try:
            if src is not None and src.is_dir():
                src_data, src_found = scan_root(src, cache=self._src_cache)
                if not src_found:
                    src_data = None
        except OSError:
            src_data = None
        try:
            if dst is not None and dst.is_dir():
                dst_data, dst_found = scan_root(dst, cache=self._dst_cache)
                if not dst_found:
                    dst_data = None
        except OSError:
            dst_data = None
        self._save_state(src, dst)
        self.scan_queue.put((src_data, dst_data))

    def _poll_scan(self):
        try:
            src_data, dst_data = self.scan_queue.get_nowait()
        except queue.Empty:
            self.root.after(80, self._poll_scan)
            return
        self.src_data = src_data
        self.dest_data = dst_data
        self.source_panel.apply_scan(src_data, None)
        # The destination tree mirrors the source (greyed-out state/
        # subgroups), lights compare against the actual destination root
        # directory.
        self.dest_panel.apply_scan(src_data, self.dest_root if dst_data is not None else None)
        self.scanning = False
        self.refresh_btn.configure(state="normal")
        self._last_scan_at = time.time()
        self.scan_status_label.configure(text=self._format_last_scan())
        if self._full_scan_just_ran:
            self._full_scan_just_ran = False
            self.full_scan_var.set(False)
        self._recompute_transfer()
        if self.rescan_pending:
            self.rescan_pending = False
            self._trigger_rescan()

    def _format_last_scan(self) -> str:
        if self._last_scan_at is None:
            return "no scan yet"
        ts = time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(self._last_scan_at))
        return f"Last scan:\n{ts}"

    # -- Selection / transfer list ---------------------------------------------------
    def _on_selection_changed(self):
        self._recompute_transfer()

    def _recompute_transfer(self):
        if not self.src_data or not self.dest_data or self.dest_root is None:
            self.pending_transfer = []
            self.size_label.configure(text="-")
            self.sync_btn.configure(state="disabled")
            return
        selection = self.source_panel.get_selection()
        todo = build_transfer_list(
            self.src_data, self.dest_root, selection,
            include_current=self.copy_current_var.get(),
            force_all=self.copy_everything_var.get(),
        )
        self.pending_transfer = todo
        total = sum(f.size for f in todo)
        if todo:
            self.size_label.configure(
                text=f"{human_size(total)}\n({len(todo)} files)")
        else:
            self.size_label.configure(text="nothing to\ntransfer")
        # While a schedule is active, the sync button stays greyed out --
        # the transfer then only runs automatically (see _on_schedule_toggle).
        if todo and not self.sync_schedule_var.get():
            self.sync_btn.configure(state="normal")
        else:
            self.sync_btn.configure(state="disabled")

    # -- Sync ---------------------------------------------------
    def _start_sync(self):
        if not self.pending_transfer or self.source_root is None or self.dest_root is None:
            return
        self.sync_btn.configure(state="disabled")
        ProgressDialog(self.root, list(self.pending_transfer), self.source_root,
                       self.dest_root, on_done=self._on_sync_done)

    def _on_sync_done(self, copied: int, cancelled: bool):
        self.copy_current_var.set(False)
        self.copy_everything_var.set(False)
        if cancelled:
            self.sync_status_label.configure(text=f"Cancelled ({copied} files copied)")
        else:
            self.sync_status_label.configure(text=f"Done ({copied} files copied)")
        self._trigger_rescan()

    # -- Scheduled sync ("sync every ... h") ---------------------------------------------------
    def _current_interval_hours(self) -> float:
        try:
            return float(self.sync_interval_var.get())
        except ValueError:
            return 1.0

    def _set_sync_controls_enabled(self, enabled: bool):
        """Greys out the manual sync button and the two "copy ..."
        tick-boxes while a schedule is active (the interval menu itself
        stays usable, so the interval can still be adjusted while the
        countdown is running)."""
        state = "normal" if enabled else "disabled"
        self.copy_current_chk.configure(state=state)
        self.copy_everything_chk.configure(state=state)
        if enabled:
            self._recompute_transfer()  # recompute sync_btn state normally
        else:
            self.sync_btn.configure(state="disabled")

    def _on_schedule_toggle(self):
        if self.sync_schedule_var.get():
            self._set_sync_controls_enabled(False)
            self._arm_schedule(self._current_interval_hours())
        else:
            self._stop_schedule()

    def _on_interval_changed(self, *_args):
        if self.sync_schedule_var.get():
            self._arm_schedule(self._current_interval_hours())

    def _stop_schedule(self):
        self._next_sync_at = None
        if self._schedule_timer_handle is not None:
            try:
                self.root.after_cancel(self._schedule_timer_handle)
            except ValueError:
                pass
            self._schedule_timer_handle = None
        self.countdown_label.configure(text="")
        self._set_sync_controls_enabled(True)

    def _arm_schedule(self, hours: float):
        """Sets the next sync time (now + hours) and starts/updates the
        per-minute countdown."""
        if self._schedule_timer_handle is not None:
            try:
                self.root.after_cancel(self._schedule_timer_handle)
            except ValueError:
                pass
            self._schedule_timer_handle = None
        self._next_sync_at = time.time() + hours * 3600.0
        self._schedule_prescan_done = False
        self._update_countdown()

    def _update_countdown(self):
        self._schedule_timer_handle = None
        if self._next_sync_at is None:
            return
        remaining = self._next_sync_at - time.time()
        if remaining <= 0:
            self._perform_scheduled_sync()
            return
        # Scan automatically once, one minute before it elapses, so an
        # up-to-date file list is available at the actual sync time.
        if remaining <= 60 and not self._schedule_prescan_done:
            self._schedule_prescan_done = True
            self._trigger_rescan()
        total_minutes = max(int(remaining // 60), 0)
        hh, mm = divmod(total_minutes, 60)
        self.countdown_label.configure(text=f"next sync in {hh:02d}:{mm:02d}")
        self._schedule_timer_handle = self.root.after(60000, self._update_countdown)

    def _perform_scheduled_sync(self):
        if self.pending_transfer:
            self._start_sync()
        else:
            self.sync_status_label.configure(text="Schedule: nothing to transfer")
        if self.sync_schedule_var.get():
            self._arm_schedule(self._current_interval_hours())
        else:
            self.countdown_label.configure(text="")


def main():
    root = tk.Tk()
    app = LidarSyncApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
