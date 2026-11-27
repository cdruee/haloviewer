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
   roaming profile after every scan (%APPDATA%\\halosync\\state.json,
   ~/.config/halosync/state.json outside Windows, see get_config_path())
   and reloaded on the next program start. The
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

11. Alerts ("enable alerts" tick-box and "alert setup..." button in the
    Sync frame): after every *scheduled* sync, the number and size of
    the files actually copied are compared with the thresholds set in
    the alert setup dialog (default: fewer than 2 files or less than
    1 kB -> alert; 0 switches a threshold off). By default an alert is
    sent only once when the sync turns faulty, and a "recovered" message
    once it works again; this state is remembered in alerts.json and
    reset by switching alerts off/on. Optional "canary" all-clear
    messages after unremarkable syncs are sent at most once a day by
    default. Messages go by email, either through any SMTP server or
    through Gmail (with a Google app password), and/or as push messages
    through ntfy (https://ntfy.sh or another ntfy server; a plain HTTP
    POST to a public topic). The tick-box is greyed
    out while "sync every ... h" is not ticked or no alert channel is
    completely configured; the bell next to it shows the alert state
    (grey off, green OK, orange mail failed, red faulty; hover for
    details). The settings are kept in alerts.json next to state.json;
    passwords are encrypted for the signed-in Windows user (DPAPI) and
    only obfuscated (base64, file readable only by the user) on other
    systems.

Anyone needing different groups/prefixes or a different "current file"
criterion can most easily adjust GROUPS above, or extract_timestamp() /
classify_subgroup(), accordingly.
"""

from __future__ import annotations

import os
import re
import sys
import json
import base64
import shutil
import smtplib
import socket
import ssl
import threading
import queue
import secrets
import time
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from dataclasses import dataclass, field, fields, asdict, replace
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
    """Per-user configuration folder: %APPDATA%\\halosync (the roaming
    profile of the signed-in user) on Windows, $XDG_CONFIG_HOME/halosync
    (normally ~/.config/halosync) elsewhere; HALOSYNC_CONFIG names a
    different folder. A state file left by earlier versions in their
    "LidarSyncGUI" folder is moved here, so saved paths and the scan
    cache survive the rename."""
    env = os.environ.get("HALOSYNC_CONFIG")
    appdata = os.environ.get("APPDATA")
    if env:
        d, old = Path(env).expanduser(), None
    elif appdata:
        d, old = Path(appdata) / "halosync", Path(appdata) / "LidarSyncGUI"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        d, old = Path(base) / "halosync", Path.home() / "LidarSyncGUI"
    d.mkdir(parents=True, exist_ok=True)
    if old is not None:
        _migrate_state(old, d)
    return d


def _migrate_state(old_dir: Path, new_dir: Path) -> None:
    """Move state.json from the folder of earlier versions to new_dir
    (unless new_dir already has one), then remove the old folder if it
    is empty. Never raises."""
    old_file = old_dir / STATE_FILENAME
    new_file = new_dir / STATE_FILENAME
    try:
        if old_file.is_file() and not new_file.exists():
            shutil.move(str(old_file), str(new_file))
        if old_dir.is_dir() and not any(old_dir.iterdir()):
            old_dir.rmdir()
    except OSError:
        pass


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
# Alerts (no GUI code): thresholds, messages, email via SMTP / Gmail
# --------------------------------------------------------------------------

ALERTS_VERSION = 1
ALERTS_FILENAME = "alerts.json"

EMAIL_MODES = ("smtp", "gmail")
SMTP_SECURITY = ("STARTTLS", "SSL/TLS", "none")
SMTP_DEFAULT_PORTS = {"STARTTLS": 587, "SSL/TLS": 465, "none": 25}
SMTP_TIMEOUT_S = 20.0
GMAIL_HOST = "smtp.gmail.com"
GMAIL_PORT = 587
GMAIL_SECURITY = "STARTTLS"
NTFY_DEFAULT_SERVER = "https://ntfy.sh"
NTFY_TIMEOUT_S = 20.0
_NTFY_TOPIC_RE = re.compile(r"^[-_A-Za-z0-9]{1,64}$")

# Prefixes of a stored password: how it was protected (see protect_secret)
_SECRET_DPAPI = "dpapi:"
_SECRET_B64 = "b64:"


@dataclass
class AlertConfig:
    """All alert settings (what the alert setup dialog edits, plus the
    "enable alerts" tick-box). Thresholds of 0 are switched off."""
    enabled: bool = False          # "enable alerts" tick-box in the Sync frame
    min_files: int = 2             # alert if fewer files were copied
    min_kb: int = 1                # alert if less data (kB = 1024 bytes) was copied
    canary: bool = False           # also send an all-clear after unremarkable syncs
    canary_daily: bool = True      # ... but at most one per (calendar) day
    alert_once: bool = True        # alert only when the sync turns faulty, not
                                   # again until it has worked once more
    via_email: bool = False
    email_mode: str = "smtp"       # "smtp" or "gmail"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: str = "STARTTLS"
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_sender: str = ""          # empty: use smtp_user
    smtp_no_verify: bool = False   # don't check the server certificate (insecure)
    gmail_address: str = ""
    gmail_password: str = ""       # a Google *app* password
    recipients: str = ""           # comma/semicolon/space separated
    via_ntfy: bool = False         # push messages through an ntfy server
    ntfy_server: str = NTFY_DEFAULT_SERVER
    ntfy_topic: str = ""           # public topic: anyone knowing it can read it


_SECRET_FIELDS = ("smtp_password", "gmail_password")


def parse_recipients(text: str) -> List[str]:
    """Splits a list of addresses separated by commas, semicolons or
    whitespace."""
    return [a for a in re.split(r"[,;\s]+", text or "") if a]


def email_settings(cfg: AlertConfig) -> dict:
    """The effective connection data for the selected email mode:
    host, port, security, user, password, sender, recipients."""
    if cfg.email_mode == "gmail":
        addr = cfg.gmail_address.strip()
        return {"host": GMAIL_HOST, "port": GMAIL_PORT,
                "security": GMAIL_SECURITY, "user": addr,
                "password": cfg.gmail_password, "sender": addr,
                "recipients": parse_recipients(cfg.recipients),
                "no_verify": False}
    user = cfg.smtp_user.strip()
    return {"host": cfg.smtp_host.strip(), "port": int(cfg.smtp_port),
            "security": cfg.smtp_security if cfg.smtp_security in SMTP_SECURITY
            else "STARTTLS",
            "user": user, "password": cfg.smtp_password,
            "sender": cfg.smtp_sender.strip() or user,
            "recipients": parse_recipients(cfg.recipients),
            "no_verify": bool(cfg.smtp_no_verify)}


def email_missing(cfg: AlertConfig) -> List[str]:
    """Names of the email settings that are still missing (empty list =
    complete). For SMTP, login and password are optional (servers that
    accept mail without authentication), the sender then must be given."""
    s = email_settings(cfg)
    missing = []
    if cfg.email_mode == "gmail":
        if "@" not in s["user"]:
            missing.append("Gmail address")
        if not s["password"]:
            missing.append("app password")
    else:
        if not s["host"]:
            missing.append("SMTP server")
        if not 0 < s["port"] < 65536:
            missing.append("port")
        if "@" not in s["sender"]:
            missing.append("sender address (From)")
        if s["user"] and not s["password"]:
            missing.append("password")
    if not s["recipients"]:
        missing.append("recipient (Send to)")
    return missing


def alerts_configured(cfg: AlertConfig) -> bool:
    """True if alerts could actually be sent: at least one channel is
    switched on and completely set up, and there is something to send
    (a threshold or canary messages)."""
    if not (cfg.min_files > 0 or cfg.min_kb > 0 or cfg.canary):
        return False
    return bool(active_channels(cfg))


def ntfy_url(cfg: AlertConfig) -> str:
    """The URL messages are posted to (and that subscribers open):
    <server>/<topic>; the server defaults to https://ntfy.sh."""
    server = (cfg.ntfy_server or "").strip() or NTFY_DEFAULT_SERVER
    if "://" not in server:
        server = "https://" + server
    return server.rstrip("/") + "/" + cfg.ntfy_topic.strip()


def ntfy_missing(cfg: AlertConfig) -> List[str]:
    """What is still missing or wrong in the ntfy settings (empty list =
    complete)."""
    missing = []
    server = (cfg.ntfy_server or "").strip()
    if server and "://" in server and not server.lower().startswith(("http://", "https://")):
        missing.append("server (must start with https://)")
    topic = cfg.ntfy_topic.strip()
    if not topic:
        missing.append("topic")
    elif not _NTFY_TOPIC_RE.match(topic):
        missing.append("topic (letters, digits, - and _ only, at most 64)")
    return missing


def active_channels(cfg: AlertConfig) -> List[str]:
    """The channels that are switched on *and* completely set up."""
    out = []
    if cfg.via_email and not email_missing(cfg):
        out.append("email")
    if cfg.via_ntfy and not ntfy_missing(cfg):
        out.append("ntfy")
    return out


@dataclass
class AlertState:
    """What alerting remembers between scheduled syncs (and program
    starts): whether the sync is currently faulty, whether an alert has
    already gone out for that, and when the last canary was sent."""
    faulty_since: str = ""         # ISO time of the first faulty sync, "" = OK
    alert_sent: bool = False       # an alert went out for the current faulty spell
    last_canary: str = ""          # ISO date of the last canary/recovery message


def sync_is_faulty(cfg: AlertConfig, files: int, nbytes: int) -> bool:
    """True if a scheduled sync that copied `files` files with `nbytes`
    bytes falls below one of the (non-zero) thresholds."""
    if cfg.min_files > 0 and files < cfg.min_files:
        return True
    return cfg.min_kb > 0 and nbytes < cfg.min_kb * 1024


def next_message(cfg: AlertConfig, state: AlertState, faulty: bool,
                 now: Optional[datetime] = None):
    """Decides what to send after a scheduled sync and how the remembered
    state changes. Returns ``(kind, new_state)`` with kind one of
    "alert", "recovered", "canary" or None.

    * faulty: "alert" -- with ``alert_once`` only if no alert has been
      sent yet for this faulty spell;
    * first good sync after an alert was sent: "recovered";
    * otherwise good: "canary" if canaries are on (with ``canary_daily``
      only if none was sent today yet).

    The state returned only records the sync itself; call mark_sent()
    once the message has actually been sent, so a message that could
    not be sent is tried again after the next sync."""
    now = now or datetime.now()
    s = replace(state)
    if faulty:
        if not s.faulty_since:
            s.faulty_since = now.isoformat(timespec="seconds")
            s.alert_sent = False
        if cfg.alert_once and s.alert_sent:
            return None, s
        return "alert", s
    was_alerted = bool(s.faulty_since) and s.alert_sent
    s.faulty_since, s.alert_sent = "", False
    if was_alerted:
        return "recovered", s
    if cfg.canary and not (cfg.canary_daily
                           and s.last_canary == now.date().isoformat()):
        return "canary", s
    return None, s


def mark_sent(state: AlertState, kind: str,
              now: Optional[datetime] = None) -> AlertState:
    """The state after a message of `kind` was sent successfully. A
    recovery message counts as the day's canary."""
    now = now or datetime.now()
    s = replace(state)
    if kind == "alert" and s.faulty_since:
        s.alert_sent = True
    elif kind in ("canary", "recovered"):
        s.last_canary = now.date().isoformat()
    return s


def _fmt_iso(text: str) -> str:
    try:
        return datetime.fromisoformat(text).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return text or "-"


def build_alert_message(cfg: AlertConfig, kind: str, files: int, nbytes: int,
                        source: str = "", dest: str = "", note: str = "",
                        errors: Optional[List[str]] = None,
                        when: Optional[datetime] = None,
                        faulty_since: str = "",
                        channel: str = "email") -> EmailMessage:
    """Builds the email for an alert (kind="alert"), the all-clear after
    an alert (kind="recovered"), a canary message (kind="canary") or a
    test message (kind="test"). `faulty_since` (ISO time) is the start
    of the faulty spell, for "alert" and "recovered". `channel` ("email"
    or "ntfy") only changes what a test message lists."""
    s = email_settings(cfg)
    when = when or datetime.now()
    host = socket.gethostname()
    amount = f"{human_size(nbytes)} in {files} file{'s' if files != 1 else ''}"
    if kind == "alert":
        subject = f"[halosync] ALERT on {host}: only {amount} synchronised"
        head = "halosync ALERT: the last scheduled sync copied too little data."
    elif kind == "recovered":
        subject = f"[halosync] RECOVERED on {host}: {amount} synchronised"
        head = ("halosync: the scheduled sync works again "
                f"(faulty since {_fmt_iso(faulty_since)}).")
    elif kind == "canary":
        subject = f"[halosync] OK on {host}: {amount} synchronised"
        head = f"halosync canary: no alert -- {amount} synchronised."
    else:
        subject = f"[halosync] test message from {host}"
        head = ("This is a test message from halosync: alert messages from "
                "this computer reach you.")
        if channel == "ntfy":
            subject = f"[halosync] test notification from {host}"
    crit = []
    if cfg.min_files > 0:
        crit.append(f"fewer than {cfg.min_files} files")
    if cfg.min_kb > 0:
        crit.append(f"less than {cfg.min_kb} kB")
    lines = [
        head, "",
        f"Time:         {when.strftime('%d.%m.%Y %H:%M:%S')}",
        f"Computer:     {host}",
    ]
    if kind == "test":
        once = ("once per faulty spell, then RECOVERED" if cfg.alert_once
                else "after every faulty sync")
        canary = (("at most once a day" if cfg.canary_daily
                   else "after every good sync") if cfg.canary else "off")
        lines += ([f"ntfy topic:   {ntfy_url(cfg)}"] if channel == "ntfy" else [
            f"Mail server:  {s['host']}:{s['port']} ({s['security']})",
            f"Sender:       {s['sender']}"]) + [
            f"Alert if:     {' or '.join(crit) if crit else '(no thresholds)'}",
            f"Alerts:       {once}",
            f"Canary:       {canary}",
        ]
    else:
        lines += [
            f"Source:       {source or '-'}",
            f"Destination:  {dest or '-'}",
            f"Copied:       {amount} ({nbytes} bytes)",
            f"Alert if:     {' or '.join(crit) if crit else '(no thresholds)'}",
        ]
    if note:
        lines.append(f"Note:         {note}")
    if kind == "alert" and faulty_since:
        lines.append(f"Faulty since: {_fmt_iso(faulty_since)}")
    if kind == "alert" and cfg.alert_once:
        lines += ["", "No further alerts will be sent until a scheduled sync "
                      "works again (you will then get a RECOVERED message)."]
    if kind == "canary" and cfg.canary_daily:
        lines += ["", "Canary messages are sent at most once a day."]
    if errors:
        lines += ["", f"Errors ({len(errors)}):"]
        lines += [f"  {e}" for e in errors[:20]]
        if len(errors) > 20:
            lines.append(f"  ... and {len(errors) - 20} more")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = s["sender"]
    msg["To"] = ", ".join(s["recipients"])
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=host or None)
    msg.set_content("\n".join(lines) + "\n")
    return msg


def _smtp_open(s: dict, timeout: float = SMTP_TIMEOUT_S) -> smtplib.SMTP:
    """Connects (and, where given, logs in) according to email_settings().
    The server certificate is checked unless "no_verify" is set (the
    "don't check the server certificate" tick-box; never for Gmail).
    Raises smtplib.SMTPException / OSError on failure."""
    ctx = ssl.create_default_context()
    if s.get("no_verify"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if s["security"] == "SSL/TLS":
        conn = smtplib.SMTP_SSL(s["host"], s["port"], timeout=timeout, context=ctx)
    else:
        conn = smtplib.SMTP(s["host"], s["port"], timeout=timeout)
    try:
        conn.ehlo()
        if s["security"] == "STARTTLS":
            conn.starttls(context=ctx)
            conn.ehlo()
        if s["user"]:
            conn.login(s["user"], s["password"])
    except BaseException:
        try:
            conn.close()
        except Exception:
            pass
        raise
    return conn


def send_email(cfg: AlertConfig, msg: EmailMessage,
               timeout: float = SMTP_TIMEOUT_S) -> dict:
    """Sends msg with the configured settings; raises on failure (also
    if the server refuses every recipient). Returns the recipients the
    server refused, as {address: (code, response)} (empty if none)."""
    s = email_settings(cfg)
    conn = _smtp_open(s, timeout)
    try:
        return conn.send_message(msg, from_addr=s["sender"],
                                 to_addrs=s["recipients"]) or {}
    finally:
        try:
            conn.quit()
        except Exception:
            pass


def smtp_send_test(cfg: AlertConfig, timeout: float = SMTP_TIMEOUT_S) -> str:
    """Sends a test message (build_alert_message kind "test") with the
    given settings. Returns a short success text; raises (readable via
    describe_error) on failure. The server accepting the message does
    not guarantee delivery -- it may still bounce or land in a spam
    folder."""
    missing = email_missing(cfg)
    if missing:
        raise ValueError("missing: " + ", ".join(missing))
    s = email_settings(cfg)
    refused = send_email(cfg, build_alert_message(cfg, "test", 0, 0), timeout)
    sent_to = [r for r in s["recipients"] if r not in refused]
    text = f"test mail sent to {', '.join(sent_to)} via {s['host']}:{s['port']}"
    if refused:
        text += f"; REFUSED: {', '.join(refused)}"
    return text + " -- check the inbox (and the spam folder)"


_NTFY_STYLE = {   # kind -> (priority, tags); tags become emojis in the apps
    "alert": ("high", "warning"),
    "recovered": ("default", "white_check_mark"),
    "canary": ("low", "bird"),
    "test": ("default", "test_tube"),
}


def send_ntfy(cfg: AlertConfig, msg: EmailMessage, kind: str,
              timeout: float = NTFY_TIMEOUT_S) -> int:
    """Publishes msg (built by build_alert_message) to the ntfy topic:
    a plain HTTP POST of the text, with title, priority and tags as
    headers. Returns the HTTP status; raises urllib.error.URLError /
    HTTPError (or OSError) on failure."""
    title = str(msg["Subject"]).replace("[halosync] ", "", 1)
    priority, tags = _NTFY_STYLE.get(kind, ("default", ""))
    req = urllib.request.Request(
        ntfy_url(cfg), data=msg.get_content().strip().encode("utf-8"),
        method="POST",
        headers={"Title": title.encode("ascii", "replace").decode("ascii"),
                 "Priority": priority, "Tags": tags})
    with urllib.request.urlopen(req, timeout=timeout,
                                context=ssl.create_default_context()) as resp:
        return resp.status


def ntfy_send_test(cfg: AlertConfig, timeout: float = NTFY_TIMEOUT_S) -> str:
    """Sends a test notification; returns a short success text, raises on
    failure."""
    missing = ntfy_missing(cfg)
    if missing:
        raise ValueError("missing: " + ", ".join(missing))
    send_ntfy(cfg, build_alert_message(cfg, "test", 0, 0, channel="ntfy"),
              "test", timeout)
    return f"test message sent to {ntfy_url(cfg)} -- check the ntfy app"


def deliver(cfg: AlertConfig, kind: str, files: int, nbytes: int,
            **message_kw) -> Dict[str, Optional[str]]:
    """Sends an alert/recovery/canary message through every active
    channel (active_channels). Returns {channel: None if sent, else a
    description of the error}; never raises."""
    results: Dict[str, Optional[str]] = {}
    for channel in active_channels(cfg):
        try:
            msg = build_alert_message(cfg, kind, files, nbytes,
                                      channel=channel, **message_kw)
            if channel == "email":
                send_email(cfg, msg)
            else:
                send_ntfy(cfg, msg, kind)
            results[channel] = None
        except Exception as exc:
            results[channel] = describe_error(exc)
    return results


def describe_error(exc: BaseException) -> str:
    """A one-line, human readable description of an SMTP/network error."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"ntfy server answered {exc.code} {exc.reason}"
    if isinstance(exc, urllib.error.URLError):
        if isinstance(exc.reason, BaseException):
            return describe_error(exc.reason)
        return f"server not reachable: {exc.reason}"
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "login refused (check user name/password; Gmail needs an app password)"
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        r = ", ".join(exc.recipients) if exc.recipients else ""
        return f"recipient refused: {r}"
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return f"sender refused: {exc.sender}"
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return f"not supported by the server: {exc}"
    if isinstance(exc, ssl.SSLCertVerificationError):
        why = exc.verify_message or exc.reason or str(exc)
        return f"server certificate not accepted: {why}"
    if isinstance(exc, ssl.SSLError):
        return f"encryption failed ({exc.reason or exc}); check port/encryption"
    if isinstance(exc, socket.timeout):
        return "connection timed out"
    if isinstance(exc, socket.gaierror):
        return "server name not found"
    if isinstance(exc, ConnectionRefusedError):
        return "connection refused (wrong server or port?)"
    if isinstance(exc, smtplib.SMTPResponseException):
        text = exc.smtp_error.decode("utf-8", "replace") \
            if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
        return f"server answered {exc.smtp_code}: {text}"
    return str(exc) or exc.__class__.__name__


# -- Password protection ---------------------------------------------------

def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows DPAPI (CryptProtectData/CryptUnprotectData) via ctypes:
    the result can only be decrypted by the same Windows user."""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in), ctypes.c_wchar_p("halosync"), None, None,
            None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def protect_secret(text: str) -> str:
    """Encodes a password for alerts.json: DPAPI-encrypted on Windows,
    otherwise only base64-obfuscated (the file itself is then made
    readable by the user only)."""
    if not text:
        return ""
    raw = text.encode("utf-8")
    if sys.platform == "win32":
        try:
            return _SECRET_DPAPI + base64.b64encode(_dpapi(raw, True)).decode("ascii")
        except Exception:
            pass
    return _SECRET_B64 + base64.b64encode(raw).decode("ascii")


def unprotect_secret(text: str) -> str:
    """Reverses protect_secret(); returns "" if it cannot be decoded
    (e.g. a DPAPI value copied from another user or computer)."""
    if not text:
        return ""
    try:
        if text.startswith(_SECRET_DPAPI):
            if sys.platform != "win32":
                return ""
            return _dpapi(base64.b64decode(text[len(_SECRET_DPAPI):]),
                          False).decode("utf-8")
        if text.startswith(_SECRET_B64):
            return base64.b64decode(text[len(_SECRET_B64):]).decode("utf-8")
    except Exception:
        return ""
    return ""


# -- Persistence of the alert settings -------------------------------------

def get_alerts_path() -> Path:
    return get_config_dir() / ALERTS_FILENAME


def save_alert_config(path: Path, cfg: AlertConfig,
                      state: Optional[AlertState] = None) -> None:
    """Writes the alert settings atomically as JSON, passwords protected
    with protect_secret(), together with the remembered AlertState
    (`state` None keeps the one already in the file)."""
    data = asdict(cfg)
    for k in _SECRET_FIELDS:
        data[k] = protect_secret(data[k])
    if state is None:
        state = load_alert_state(path)
    data["state"] = asdict(state)
    data["version"] = ALERTS_VERSION
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass
    os.replace(tmp_path, path)


def load_alert_state(path: Path) -> AlertState:
    """The AlertState remembered in alerts.json (defaults if none)."""
    st = AlertState()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        saved = data.get("state") if data.get("version") == ALERTS_VERSION else None
    except (OSError, ValueError, AttributeError):
        return st
    if not isinstance(saved, dict):
        return st
    for fld in fields(AlertState):
        val = saved.get(fld.name)
        if isinstance(val, type(getattr(st, fld.name))):
            setattr(st, fld.name, val)
    return st


def load_alert_config(path: Path) -> AlertConfig:
    """Reads alerts.json; missing, unreadable or incompatible files give
    the defaults, unknown or ill-typed entries are ignored."""
    cfg = AlertConfig()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return cfg
    if not isinstance(data, dict) or data.get("version") != ALERTS_VERSION:
        return cfg
    for fld in fields(AlertConfig):
        if fld.name not in data:
            continue
        val = data[fld.name]
        default = getattr(cfg, fld.name)
        if fld.name in _SECRET_FIELDS:
            val = unprotect_secret(val) if isinstance(val, str) else ""
        if isinstance(default, bool):
            if not isinstance(val, bool):
                continue
        elif isinstance(default, int):
            if isinstance(val, bool) or not isinstance(val, int) or val < 0:
                continue
        elif not isinstance(val, str):
            continue
        setattr(cfg, fld.name, val)
    if cfg.email_mode not in EMAIL_MODES:
        cfg.email_mode = "smtp"
    if cfg.smtp_security not in SMTP_SECURITY:
        cfg.smtp_security = "STARTTLS"
    return cfg


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

# HaloViewer logo (128x128 PNG, scaled down from docs/images/
# haloviewer_logo.png), base64-embedded so the script stays a single file
# and does not depend on an image in the working directory. tk.PhotoImage
# can load PNG (with transparency) directly since Tk 8.6, so no
# Pillow/extra installation is needed. Also used as the window icon.
HALOVIEWER_LOGO_B64 = (
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
        # Filled by the worker thread, read only after its "done" message
        self.copied_bytes = 0
        self.errors: List[str] = []

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
                self.copied_bytes += entry.size
            except OSError as exc:
                self.errors.append(f"{entry.relpath}: {exc}")
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
                    self.on_done(copied=a, cancelled=cancelled,
                                 copied_bytes=self.copied_bytes,
                                 errors=list(self.errors))
                    return
        except queue.Empty:
            pass
        self.after(80, self._poll)


def _run_in_background(widget, func, on_result, poll_ms: int = 100):
    """Runs func() in a daemon thread and calls on_result(ok, value) in
    the Tk main loop afterwards (value = return value or the exception).
    Nothing is called if `widget` no longer exists by then."""
    q: "queue.Queue" = queue.Queue()

    def worker():
        try:
            q.put((True, func()))
        except BaseException as exc:  # reported to the GUI, never raised here
            q.put((False, exc))

    def poll():
        try:
            ok, value = q.get_nowait()
        except queue.Empty:
            try:
                widget.after(poll_ms, poll)
            except tk.TclError:
                pass
            return
        try:
            if widget.winfo_exists():
                on_result(ok, value)
        except tk.TclError:
            pass

    threading.Thread(target=worker, daemon=True).start()
    widget.after(poll_ms, poll)


class AlertSetupDialog(tk.Toplevel):
    """Alert configuration: "General" tab (when to send what, through
    which channel) and "Email" tab (SMTP server or Gmail)."""

    def __init__(self, master, cfg: AlertConfig, on_save):
        super().__init__(master)
        self.title("Alert setup")
        self.resizable(False, False)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.on_save = on_save
        self._enabled = cfg.enabled  # the Sync-frame tick-box, not edited here

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        gen = ttk.Frame(self.nb, padding=12)
        mail = ttk.Frame(self.nb, padding=12)
        self.nb.add(gen, text="General")
        self.nb.add(mail, text="Email")
        ntfy = ttk.Frame(self.nb, padding=12)
        self.nb.add(ntfy, text="ntfy")
        self._build_general(gen, cfg)
        self._build_email(mail, cfg)
        self._build_ntfy(ntfy, cfg)

        btns = ttk.Frame(self, padding=(10, 8))
        btns.pack(fill="x")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="OK", command=self._on_ok).pack(side="right", padx=(0, 6))
        self.bind("<Escape>", lambda _e: self.destroy())

        self.nb.select(0)
        self.grab_set()

    # -- tabs ----------------------------------------------------------------
    def _spinbox(self, parent, var, lo, hi, width=6):
        vcmd = (self.register(lambda v: v == "" or v.isdigit()), "%P")
        return ttk.Spinbox(parent, from_=lo, to=hi, increment=1, width=width,
                           textvariable=var, validate="key", validatecommand=vcmd)

    def _build_general(self, f, cfg: AlertConfig):
        self.min_files_var = tk.StringVar(value=str(cfg.min_files))
        self.min_kb_var = tk.StringVar(value=str(cfg.min_kb))
        self.canary_var = tk.BooleanVar(value=cfg.canary)
        self.canary_daily_var = tk.BooleanVar(value=cfg.canary_daily)
        self.alert_once_var = tk.BooleanVar(value=cfg.alert_once)
        self.via_email_var = tk.BooleanVar(value=cfg.via_email)
        self.via_ntfy_var = tk.BooleanVar(value=cfg.via_ntfy)

        th = ttk.Frame(f)
        th.grid(row=0, column=0, sticky="w")
        ttk.Label(th, text="Send alert if fewer than").grid(row=0, column=0, sticky="w", pady=3)
        self._spinbox(th, self.min_files_var, 0, 100000).grid(row=0, column=1, padx=6, pady=3)
        ttk.Label(th, text="files synched").grid(row=0, column=2, sticky="w", pady=3)
        ttk.Label(th, text="Send alert if less than").grid(row=1, column=0, sticky="w", pady=3)
        self._spinbox(th, self.min_kb_var, 0, 10000000).grid(row=1, column=1, padx=6, pady=3)
        ttk.Label(th, text="kB new data").grid(row=1, column=2, sticky="w", pady=3)

        hint = dict(foreground="#555555", wraplength=380, justify="left")
        ttk.Label(f, text="0 switches a threshold off. Checked after every scheduled "
                          "sync (\"sync every ... h\").", **hint).grid(
            row=1, column=0, sticky="w", pady=(2, 8))

        ttk.Checkbutton(f, text="Send an alert only once when the sync turns faulty",
                        variable=self.alert_once_var).grid(row=2, column=0, sticky="w")
        ttk.Label(f, text="no further alerts until a scheduled sync works again; "
                          "then a \"recovered\" message is sent",
                  **hint).grid(row=3, column=0, sticky="w", padx=(22, 0), pady=(0, 8))

        ttk.Checkbutton(f, text="Send canary messages", variable=self.canary_var,
                        command=self._canary_changed).grid(row=4, column=0, sticky="w")
        ttk.Label(f, text="\"no alert: ... kB in ... files synched\" after a "
                          "scheduled sync without alert, so you know alerting works",
                  **hint).grid(row=5, column=0, sticky="w", padx=(22, 0))
        self.canary_daily_chk = ttk.Checkbutton(
            f, text="only once a day", variable=self.canary_daily_var)
        self.canary_daily_chk.grid(row=6, column=0, sticky="w", padx=(22, 0), pady=(2, 8))

        ttk.Checkbutton(f, text="Send via email", variable=self.via_email_var).grid(
            row=7, column=0, sticky="w")
        ttk.Label(f, text="set up on the \"Email\" tab", **hint).grid(
            row=8, column=0, sticky="w", padx=(22, 0), pady=(0, 4))
        ttk.Checkbutton(f, text="Send via ntfy (push messages to phone or browser)",
                        variable=self.via_ntfy_var).grid(row=9, column=0, sticky="w")
        ttk.Label(f, text="set up on the \"ntfy\" tab", **hint).grid(
            row=10, column=0, sticky="w", padx=(22, 0))
        self._canary_changed()

    def _canary_changed(self):
        self.canary_daily_chk.state(["!disabled"] if self.canary_var.get()
                                    else ["disabled"])

    def _build_email(self, f, cfg: AlertConfig):
        self.mode_var = tk.StringVar(value=cfg.email_mode)
        self.host_var = tk.StringVar(value=cfg.smtp_host)
        self.port_var = tk.StringVar(value=str(cfg.smtp_port))
        self.security_var = tk.StringVar(value=cfg.smtp_security)
        self.user_var = tk.StringVar(value=cfg.smtp_user)
        self.password_var = tk.StringVar(value=cfg.smtp_password)
        self.sender_var = tk.StringVar(value=cfg.smtp_sender)
        self.no_verify_var = tk.BooleanVar(value=cfg.smtp_no_verify)
        self.gmail_var = tk.StringVar(value=cfg.gmail_address)
        self.gmail_pw_var = tk.StringVar(value=cfg.gmail_password)
        self.recipients_var = tk.StringVar(value=cfg.recipients)
        self._last_security = cfg.smtp_security

        f.columnconfigure(0, weight=1)
        ttk.Radiobutton(f, text="SMTP server", value="smtp", variable=self.mode_var,
                        command=self._mode_changed).grid(row=0, column=0, sticky="w")
        self.smtp_frame = ttk.Frame(f, padding=(22, 4, 0, 8))
        self.smtp_frame.grid(row=1, column=0, sticky="ew")
        sf = self.smtp_frame
        sf.columnconfigure(1, weight=1)
        ttk.Label(sf, text="Server").grid(padx=(0, 8), row=0, column=0, sticky="w", pady=2)
        ttk.Entry(sf, textvariable=self.host_var, width=30).grid(
            row=0, column=1, columnspan=3, sticky="ew", pady=2)
        ttk.Label(sf, text="Port").grid(padx=(0, 8), row=1, column=0, sticky="w", pady=2)
        self._spinbox(sf, self.port_var, 1, 65535).grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(sf, text="Encryption").grid(row=1, column=2, sticky="e", padx=(8, 4), pady=2)
        sec = ttk.Combobox(sf, textvariable=self.security_var, values=SMTP_SECURITY,
                           state="readonly", width=9)
        sec.grid(row=1, column=3, sticky="e", pady=2)
        sec.bind("<<ComboboxSelected>>", self._security_changed)
        ttk.Label(sf, text="Login").grid(padx=(0, 8), row=2, column=0, sticky="w", pady=2)
        ttk.Entry(sf, textvariable=self.user_var).grid(
            row=2, column=1, columnspan=3, sticky="ew", pady=2)
        ttk.Label(sf, text="Password").grid(padx=(0, 8), row=3, column=0, sticky="w", pady=2)
        ttk.Entry(sf, textvariable=self.password_var, show="•").grid(
            row=3, column=1, columnspan=3, sticky="ew", pady=2)
        ttk.Label(sf, text="From").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=2)
        ttk.Entry(sf, textvariable=self.sender_var).grid(
            row=4, column=1, columnspan=3, sticky="ew", pady=2)
        ttk.Label(sf, text="empty = login; leave login empty if the server needs none",
                  foreground="#555555").grid(row=5, column=1, columnspan=3, sticky="w")
        ttk.Checkbutton(sf, text="Don't check the server certificate (insecure)",
                        variable=self.no_verify_var).grid(
            row=6, column=0, columnspan=4, sticky="w", pady=(6, 0))

        ttk.Radiobutton(f, text="Gmail", value="gmail", variable=self.mode_var,
                        command=self._mode_changed).grid(row=2, column=0, sticky="w")
        self.gmail_frame = ttk.Frame(f, padding=(22, 4, 0, 8))
        self.gmail_frame.grid(row=3, column=0, sticky="ew")
        gf = self.gmail_frame
        gf.columnconfigure(1, weight=1)
        ttk.Label(gf, text="Gmail address").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        ttk.Entry(gf, textvariable=self.gmail_var).grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Label(gf, text="App password").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=2)
        ttk.Entry(gf, textvariable=self.gmail_pw_var, show="•").grid(
            row=1, column=1, sticky="ew", pady=2)
        ttk.Label(gf, text="Needs 2-step verification and an app password (Google "
                           "account → Security → App passwords); the normal "
                           "Google password is refused.",
                  foreground="#555555", wraplength=330, justify="left").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))

        ttk.Separator(f).grid(row=4, column=0, sticky="ew", pady=6)
        rf = ttk.Frame(f)
        rf.grid(row=5, column=0, sticky="ew")
        rf.columnconfigure(1, weight=1)
        ttk.Label(rf, text="Send to").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(rf, textvariable=self.recipients_var).grid(row=0, column=1, sticky="ew")
        ttk.Label(rf, text="several addresses separated by commas",
                  foreground="#555555").grid(row=1, column=1, sticky="w")

        tf = ttk.Frame(f)
        tf.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        self.test_btn = ttk.Button(tf, text="Send test mail", command=self._test_connection)
        self.test_btn.pack(side="left", anchor="n")
        self.test_label = tk.Label(tf, text="", anchor="w", justify="left", wraplength=260)
        self.test_label.pack(side="left", fill="x", padx=(8, 0))

        self._mode_changed()

    def _build_ntfy(self, f, cfg: AlertConfig):
        self.ntfy_server_var = tk.StringVar(value=cfg.ntfy_server or NTFY_DEFAULT_SERVER)
        self.ntfy_topic_var = tk.StringVar(value=cfg.ntfy_topic)
        f.columnconfigure(1, weight=1)
        hint = dict(foreground="#555555", wraplength=400, justify="left")
        ttk.Label(f, text="Server").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        ttk.Entry(f, textvariable=self.ntfy_server_var).grid(
            row=0, column=1, columnspan=2, sticky="ew", pady=2)
        ttk.Label(f, text="Topic").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=2)
        ttk.Entry(f, textvariable=self.ntfy_topic_var).grid(
            row=1, column=1, sticky="ew", pady=2)
        ttk.Button(f, text="Generate", command=self._generate_topic).grid(
            row=1, column=2, sticky="e", padx=(6, 0), pady=2)
        ttk.Label(f, text="Topics on a public server are not protected: anyone who "
                          "knows the name can read and send messages on it. Use a "
                          "long, hard-to-guess name (\"Generate\" makes one).",
                  **hint).grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 8))
        ttk.Label(f, text="To receive the messages, subscribe to the topic in the "
                          "ntfy app (Android, iOS) or open this address in a "
                          "browser:", **hint).grid(
            row=3, column=0, columnspan=3, sticky="w")
        self.ntfy_url_label = ttk.Label(f, text="", foreground="#1b4f72")
        self.ntfy_url_label.grid(row=4, column=0, columnspan=3, sticky="w", pady=(2, 0))
        for var in (self.ntfy_server_var, self.ntfy_topic_var):
            var.trace_add("write", lambda *_a: self._show_ntfy_url())
        self._show_ntfy_url()

        tf = ttk.Frame(f)
        tf.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        self.ntfy_test_btn = ttk.Button(tf, text="Send test message",
                                        command=self._test_ntfy)
        self.ntfy_test_btn.pack(side="left", anchor="n")
        self.ntfy_test_label = tk.Label(tf, text="", anchor="w", justify="left",
                                        wraplength=260)
        self.ntfy_test_label.pack(side="left", fill="x", padx=(8, 0))

    def _ntfy_cfg(self) -> AlertConfig:
        return AlertConfig(ntfy_server=self.ntfy_server_var.get().strip()
                           or NTFY_DEFAULT_SERVER,
                           ntfy_topic=self.ntfy_topic_var.get().strip())

    def _show_ntfy_url(self):
        cfg = self._ntfy_cfg()
        self.ntfy_url_label.configure(
            text=ntfy_url(cfg) if not ntfy_missing(cfg) else "(enter a topic)")

    def _generate_topic(self):
        self.ntfy_topic_var.set("halosync-" + secrets.token_hex(8))

    def _test_ntfy(self):
        cfg = self._ntfy_cfg()
        missing = ntfy_missing(cfg)
        if missing:
            self.ntfy_test_label.configure(text="\u2717 missing: " + ", ".join(missing),
                                           fg="#c0392b")
            return
        self.ntfy_test_btn.state(["disabled"])
        self.ntfy_test_label.configure(text="sending ...", fg="#555555")

        def done(ok, value):
            self.ntfy_test_btn.state(["!disabled"])
            if ok:
                self.ntfy_test_label.configure(text=f"\u2713 {value}", fg="#1e8449")
            else:
                self.ntfy_test_label.configure(text=f"\u2717 {describe_error(value)}",
                                               fg="#c0392b")

        _run_in_background(self, lambda: ntfy_send_test(cfg), done)

    # -- behaviour -----------------------------------------------------------
    @staticmethod
    def _set_frame_state(frame, enabled: bool):
        for w in frame.winfo_children():
            try:
                w.state(["!disabled"] if enabled else ["disabled"])
            except (AttributeError, tk.TclError):
                pass

    def _mode_changed(self):
        smtp = self.mode_var.get() != "gmail"
        self._set_frame_state(self.smtp_frame, smtp)
        self._set_frame_state(self.gmail_frame, not smtp)
        self.test_label.configure(text="")

    def _security_changed(self, _event=None):
        """Follow the port to the usual one of the chosen encryption, unless
        the user had typed a non-standard port."""
        new = self.security_var.get()
        if self.port_var.get().strip() in ("", str(SMTP_DEFAULT_PORTS.get(self._last_security))):
            self.port_var.set(str(SMTP_DEFAULT_PORTS.get(new, 587)))
        self._last_security = new

    def _int(self, var, name, lo=0, hi=None) -> int:
        text = var.get().strip()
        try:
            v = int(text)
        except ValueError:
            raise ValueError(f"{name}: please enter a whole number") from None
        if v < lo or (hi is not None and v > hi):
            raise ValueError(f"{name}: must be between {lo} and {hi}" if hi is not None
                             else f"{name}: must be at least {lo}")
        return v

    def _port(self) -> int:
        """The SMTP port; in Gmail mode an invalid entry in the (then
        inactive) SMTP fields must not block saving."""
        try:
            return self._int(self.port_var, "port", 1, 65535)
        except ValueError:
            if self.mode_var.get() == "gmail":
                return 587
            raise

    def collect(self) -> AlertConfig:
        """Current dialog contents as an AlertConfig (raises ValueError
        for invalid numbers)."""
        return AlertConfig(
            enabled=self._enabled,
            min_files=self._int(self.min_files_var, "files threshold"),
            min_kb=self._int(self.min_kb_var, "kB threshold"),
            canary=self.canary_var.get(),
            canary_daily=self.canary_daily_var.get(),
            alert_once=self.alert_once_var.get(),
            via_email=self.via_email_var.get(),
            via_ntfy=self.via_ntfy_var.get(),
            ntfy_server=self.ntfy_server_var.get().strip() or NTFY_DEFAULT_SERVER,
            ntfy_topic=self.ntfy_topic_var.get().strip(),
            email_mode="gmail" if self.mode_var.get() == "gmail" else "smtp",
            smtp_host=self.host_var.get().strip(),
            smtp_port=self._port(),
            smtp_security=self.security_var.get(),
            smtp_user=self.user_var.get().strip(),
            smtp_password=self.password_var.get(),
            smtp_sender=self.sender_var.get().strip(),
            smtp_no_verify=self.no_verify_var.get(),
            gmail_address=self.gmail_var.get().strip(),
            gmail_password=self.gmail_pw_var.get(),
            recipients=self.recipients_var.get().strip(),
        )

    def _test_connection(self):
        try:
            cfg = self.collect()
        except ValueError as exc:
            self.test_label.configure(text=f"✗ {exc}", fg="#c0392b")
            return
        missing = email_missing(cfg)
        if missing:
            self.test_label.configure(text="✗ missing: " + ", ".join(missing),
                                      fg="#c0392b")
            return
        self.test_btn.state(["disabled"])
        self.test_label.configure(text="sending ...", fg="#555555")

        def done(ok, value):
            self.test_btn.state(["!disabled"])
            if ok:
                self.test_label.configure(
                    text=f"\u2713 {value}",
                    fg="#b9770e" if "REFUSED" in value else "#1e8449")
            else:
                self.test_label.configure(text=f"✗ {describe_error(value)}",
                                          fg="#c0392b")

        _run_in_background(self, lambda: smtp_send_test(cfg), done)

    def _on_ok(self):
        try:
            cfg = self.collect()
        except ValueError as exc:
            messagebox.showerror("Alert setup", str(exc), parent=self)
            return
        problems = []
        if cfg.via_email and email_missing(cfg):
            problems.append("email settings incomplete (missing: "
                            + ", ".join(email_missing(cfg)) + ")")
        if cfg.via_ntfy and ntfy_missing(cfg):
            problems.append("ntfy settings incomplete (missing: "
                            + ", ".join(ntfy_missing(cfg)) + ")")
        if not (cfg.via_email or cfg.via_ntfy):
            problems.append("neither \"Send via email\" nor \"Send via ntfy\" "
                            "is ticked, so there is no way to send alerts")
        if problems:
            usable = active_channels(cfg)
            messagebox.showwarning(
                "Alert setup",
                "\n".join("\u2022 " + p for p in problems)
                + ("\n\nThe settings are saved; alerts go out via "
                   + " and ".join(usable) + " only." if usable else
                   "\n\nThe settings are saved, but alerts cannot be enabled "
                   "until a channel is completely set up."), parent=self)
        self.on_save(cfg)
        self.destroy()


class ToolTip:
    """Minimal hover tooltip; the text is taken from a callable each time
    it is shown, so it always describes the current state."""

    def __init__(self, widget, text_func, delay_ms: int = 400):
        self.widget = widget
        self.text_func = text_func
        self.delay_ms = delay_ms
        self._after = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._hide()
        self._after = self.widget.after(self.delay_ms, self._show)

    def _show(self):
        self._after = None
        text = self.text_func()
        if not text:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=text, justify="left", background="#ffffe0",
                 relief="solid", borderwidth=1, padx=6, pady=3,
                 wraplength=320).pack()

    def _hide(self, _event=None):
        if self._after is not None:
            self.widget.after_cancel(self._after)
            self._after = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


#: Colours of the alert status icon (see AlertIcon)
ALERT_ICON_COLORS = {
    "off": LIGHT_GREY,          # alerts not active
    "ok": LIGHT_GREEN,          # active, last scheduled sync fine
    "mailfail": LIGHT_ORANGE,   # active, but the last message could not be sent
    "faulty": "#c0392b",        # the scheduled sync is faulty
}


class AlertIcon(tk.Canvas):
    """A small bell in the colour of the alert state; crossed out when
    alerts are not active."""

    def __init__(self, parent, size: int = 20):
        super().__init__(parent, width=size, height=size, highlightthickness=0)
        k = size / 20.0
        self._bell = self.create_polygon(
            *[c * k for c in (10, 2, 15, 4, 15.5, 10, 18.5, 15, 1.5, 15,
                              4.5, 10, 5, 4)],
            smooth=True, fill=LIGHT_GREY, outline="#404040", width=1)
        self._clapper = self.create_oval(8 * k, 15 * k, 12 * k, 19 * k,
                                         fill="#404040", outline="")
        self._slash = self.create_line(3 * k, 18 * k, 17 * k, 2 * k,
                                       fill="#404040", width=2)
        self.icon_state = None
        self.set_state("off")

    def set_state(self, state: str):
        self.icon_state = state
        self.itemconfigure(self._bell, fill=ALERT_ICON_COLORS.get(state, LIGHT_GREY))
        self.itemconfigure(self._slash, state="normal" if state == "off" else "hidden")


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

        # -- Alerts (checked after every scheduled sync) --
        self.alert_cfg: AlertConfig = load_alert_config(get_alerts_path())
        self.alert_state: AlertState = load_alert_state(get_alerts_path())
        self._alert_send_failed = ""  # error text of the last failed message
        self.alerts_var = tk.BooleanVar(value=self.alert_cfg.enabled)
        self._scheduled_run = False  # the running sync was started by the schedule

        self._build_ui()
        self._update_alert_controls()
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

        # HaloViewer logo, fixed at the top centre (not part of the
        # centred button cluster below, hence before the top spacer).
        self._logo = tk.PhotoImage(data=HALOVIEWER_LOGO_B64)
        tk.Label(mid, image=self._logo).pack(pady=(0, 10))
        try:
            self.root.iconphoto(True, self._logo)
        except tk.TclError:
            pass

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

        alert_frame = tk.Frame(sync_frame)
        alert_frame.pack(anchor="w", pady=(10, 0))
        alert_row = tk.Frame(alert_frame)
        alert_row.pack(anchor="w")
        self.alerts_chk = tk.Checkbutton(alert_row, text="enable alerts",
                                         variable=self.alerts_var,
                                         command=self._on_alerts_toggle)
        self.alerts_chk.pack(side="left")
        self.alert_icon = AlertIcon(alert_row)
        self.alert_icon.pack(side="left", padx=(4, 0))
        ToolTip(self.alert_icon, self._alert_icon_text)
        self.alert_setup_btn = tk.Button(alert_frame, text="alert setup\u2026",
                                         command=self._open_alert_setup)
        self.alert_setup_btn.pack(anchor="w", padx=(4, 0), pady=(2, 0))
        self.alert_status_label = tk.Label(sync_frame, text="", fg="#555555",
                                           wraplength=140, justify="center")
        self.alert_status_label.pack(pady=(2, 0))

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

    def _on_sync_done(self, copied: int, cancelled: bool, copied_bytes: int = 0,
                      errors: Optional[List[str]] = None):
        self.copy_current_var.set(False)
        self.copy_everything_var.set(False)
        if cancelled:
            self.sync_status_label.configure(text=f"Cancelled ({copied} files copied)")
        else:
            self.sync_status_label.configure(text=f"Done ({copied} files copied)")
        if self._scheduled_run:
            self._scheduled_run = False
            self._check_alerts(copied, copied_bytes,
                               note="sync was cancelled" if cancelled else "",
                               errors=errors)
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
        self._update_alert_controls()

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
        if self.pending_transfer and self.source_root is not None \
                and self.dest_root is not None:
            self._scheduled_run = True
            self._start_sync()
        else:
            self.sync_status_label.configure(text="Schedule: nothing to transfer")
            self._check_alerts(0, 0, note=self._nothing_to_transfer_reason())
        if self.sync_schedule_var.get():
            self._arm_schedule(self._current_interval_hours())
        else:
            self.countdown_label.configure(text="")

    # -- Alerts ---------------------------------------------------
    def _update_alert_controls(self):
        """"enable alerts" is only usable while a schedule is active and at
        least one alert channel is completely configured; the setup
        button always is."""
        usable = self.sync_schedule_var.get() and alerts_configured(self.alert_cfg)
        self.alerts_chk.configure(state="normal" if usable else "disabled")
        self._update_alert_icon()

    def _alerts_active(self) -> bool:
        return (self.alerts_var.get() and self.sync_schedule_var.get()
                and alerts_configured(self.alert_cfg))

    def _alert_icon_state(self) -> str:
        if not self._alerts_active():
            return "off"
        if self.alert_state.faulty_since:
            return "faulty"
        if self._alert_send_failed:
            return "mailfail"
        return "ok"

    def _update_alert_icon(self):
        self.alert_icon.set_state(self._alert_icon_state())

    def _alert_icon_text(self) -> str:
        """Tooltip of the alert icon."""
        st = self.alert_state
        if not self._alerts_active():
            why = []
            if not self.sync_schedule_var.get():
                why.append("\"sync every ... h\" is not ticked")
            if not alerts_configured(self.alert_cfg):
                why.append("no alert channel is completely set up")
            if not self.alerts_var.get():
                why.append("\"enable alerts\" is not ticked")
            lines = ["Alerts are off: " + "; ".join(why) + "."]
        elif st.faulty_since:
            lines = [f"Scheduled sync FAULTY since {_fmt_iso(st.faulty_since)}."]
            if st.alert_sent and self.alert_cfg.alert_once:
                lines.append("An alert was sent; no further alerts until it works again.")
            elif not st.alert_sent:
                lines.append("No alert could be sent yet.")
        else:
            lines = ["Alerts are on; the last scheduled sync was fine "
                     "(or none has run yet)."]
        if self._alerts_active() and self._alert_send_failed:
            lines.append(f"Last message NOT sent: {self._alert_send_failed}")
        if self.alert_cfg.canary and st.last_canary:
            lines.append(f"Last canary/recovery message: {st.last_canary}")
        return "\n".join(lines)

    def _save_alert_config(self):
        try:
            save_alert_config(get_alerts_path(), self.alert_cfg, self.alert_state)
        except Exception as exc:
            self.alert_status_label.configure(
                text=f"could not save alert settings: {exc}", fg="#c0392b")

    def _reset_alert_state(self):
        """Forget a faulty spell (switching alerts off or on starts afresh);
        the date of the last canary is kept."""
        self.alert_state = AlertState(last_canary=self.alert_state.last_canary)
        self._alert_send_failed = ""

    def _on_alerts_toggle(self):
        self.alert_cfg.enabled = self.alerts_var.get()
        self._reset_alert_state()
        self.alert_status_label.configure(text="")
        self._save_alert_config()
        self._update_alert_icon()

    def _open_alert_setup(self):
        AlertSetupDialog(self.root, replace(self.alert_cfg),
                         on_save=self._alert_setup_saved)

    def _alert_setup_saved(self, cfg: AlertConfig):
        cfg.enabled = self.alerts_var.get()
        self.alert_cfg = cfg
        self._save_alert_config()
        self._update_alert_controls()

    def _nothing_to_transfer_reason(self) -> str:
        if self.source_root is None or not self.src_data:
            return "source not reachable or no Metek/Proc/Raw data found"
        if self.dest_root is None or not self.dest_data:
            return ("destination not reachable or it contains no "
                    "Metek/Proc/Raw folder")
        if not self.source_panel.get_selection():
            return "nothing is selected in the source tree"
        return "no new files to transfer"

    def _check_alerts(self, files: int, nbytes: int, note: str = "",
                      errors: Optional[List[str]] = None):
        """Called after every scheduled sync (also when there was nothing
        to copy): updates the remembered alert state and sends an alert,
        recovery or canary message if one is due (see next_message). The
        message is sent (through every active channel, see deliver) in a
        background thread so a slow server never blocks the GUI."""
        if not self._alerts_active():
            return
        faulty = sync_is_faulty(self.alert_cfg, files, nbytes)
        faulty_since = self.alert_state.faulty_since
        kind, self.alert_state = next_message(self.alert_cfg, self.alert_state, faulty)
        faulty_since = self.alert_state.faulty_since or faulty_since
        self._save_alert_config()
        self._update_alert_icon()
        ts = time.strftime("%d.%m. %H:%M")
        if kind is None:
            self.alert_status_label.configure(
                text=(f"sync faulty, alert already sent ({ts})" if faulty
                      else f"sync OK ({ts})"),
                fg="#c0392b" if faulty else "#555555")
            return
        cfg = replace(self.alert_cfg)
        kw = dict(source=str(self.source_root or ""), dest=str(self.dest_root or ""),
                  note=note, errors=errors, faulty_since=faulty_since)
        what = {"alert": "alert", "recovered": "recovery message"}.get(
            kind, "canary message")
        self.alert_status_label.configure(text=f"sending {what} ...", fg="#555555")

        def done(ok, results):
            ts = time.strftime("%d.%m. %H:%M")
            if not ok:                      # deliver() never raises; just in case
                results = {"all": describe_error(results)}
            sent = [ch for ch, err in results.items() if err is None]
            failed = {ch: err for ch, err in results.items() if err is not None}
            if sent:
                self.alert_state = mark_sent(self.alert_state, kind)
                self._save_alert_config()
            self._alert_send_failed = "; ".join(f"{ch}: {err}"
                                                for ch, err in failed.items())
            text = (f"{what} sent via {' and '.join(sent)} {ts}" if sent
                    else f"{what} NOT sent {ts}")
            if failed:
                text += ("; " if sent else ": ") + "; ".join(
                    (f"{ch} NOT sent: {err}" if sent else f"{ch}: {err}")
                    for ch, err in failed.items())
            self.alert_status_label.configure(
                text=text, fg="#c0392b" if failed or kind == "alert" else "#555555")
            self._update_alert_icon()

        _run_in_background(self.root,
                           lambda: deliver(cfg, kind, files, nbytes, **kw), done)

def main():
    root = tk.Tk()
    app = LidarSyncApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
