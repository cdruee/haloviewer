"""
Tkinter desktop application for browsing WindLidar ``Proc`` data.

Tkinter is used because it ships with the standard ``python`` conda
package on Linux, macOS and Windows (via its ``tk`` dependency), so a
plain ``conda install numpy pandas matplotlib`` (see
``environment.yml``) is enough to run this GUI -- no extra GUI toolkit
package is needed.

This module only wires widgets to the pure functions in
:mod:`windlidarviewer.scan`, :mod:`windlidarviewer.data` and
:mod:`windlidarviewer.plotting`; it contains no plotting logic of its
own (see those modules' docstrings), and no file-format knowledge (see
:mod:`windlidarviewer.hpl`).
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional

import pandas as pd

from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg,
                                                NavigationToolbar2Tk)

from . import data as _data
from . import plotting
from .scan import (PROFILE_MODE, TIMESERIES_MODE, FileEntry, ScanResult,
                    get_kind_info, scan_directory)

_TIME_FMT = '%Y-%m-%d %H:%M:%S'

# Cap on how many files are opened just to establish stable axis limits
# for profile browsing / a timeseries redraw, so a huge time range
# doesn't make the GUI stall. Sampling evenly across the range still
# gives a representative min/max in practice.
_MAX_FILES_FOR_LIMITS = 300
_MAX_FILES_FOR_TIMESERIES = 4000

# Step size for the height range's up/down buttons: a round number
# that scales with how tall the current (bottom, top) view is. Read as
# "if the span is > threshold, use this step" -- the largest matching
# threshold wins, so a very tall view still only steps by the 250 m cap
# rather than growing without bound.
_HEIGHT_STEP_TABLE = [
    (25.0, 2.5),
    (50.0, 5.0),
    (100.0, 10.0),
    (250.0, 25.0),
    (500.0, 50.0),
    (1000.0, 100.0),
    (2500.0, 250.0),
]
_HEIGHT_MIN_SPAN = 25.0  # the height view is never allowed to collapse
                         # narrower than this


def _height_step_size(span: float) -> float:
    """Up/down step size for a height range view ``span`` metres tall;
    see :data:`_HEIGHT_STEP_TABLE`."""
    step = _HEIGHT_STEP_TABLE[0][1]
    for threshold, s in _HEIGHT_STEP_TABLE:
        if span > threshold:
            step = s
    return step


# Quick time-range presets shown as radio buttons between the end-time
# field and the "Apply range" button. Each (except "custom") sets Start
# time to End time minus the given offset and immediately re-applies
# the range; "custom" just hands Start time back to the user.
_TIME_PRESETS = [
    ('custom', 'Custom', None),
    ('week', 'Week', pd.Timedelta(days=7)),
    ('2days', '2 days', pd.Timedelta(days=2)),
    ('24h', '24h', pd.Timedelta(hours=24)),
    ('12h', '12h', pd.Timedelta(hours=12)),
    ('6h', '6h', pd.Timedelta(hours=6)),
]
# Number of columns the preset radio buttons wrap at -- 6 presets in 3
# columns makes 2 short rows instead of one row too wide for the left
# panel.
_PRESET_COLUMNS = 3
_TIME_PRESET_DELTAS = {key: delta for key, _label, delta in _TIME_PRESETS}


def _sample(entries: List[FileEntry], n: int) -> List[FileEntry]:
    if len(entries) <= n:
        return entries
    step = len(entries) / n
    return [entries[int(i * step)] for i in range(n)]


class WindLidarViewerApp:
    """Top-level application: owns the Tk widgets and the currently
    scanned/selected state, and delegates all data loading to
    :mod:`windlidarviewer.data`/:mod:`windlidarviewer.scan` and all
    drawing to :mod:`windlidarviewer.plotting`."""

    def __init__(self, root: tk.Tk, initial_dir: Optional[str] = None):
        self.root = root
        root.title('WindLidar Viewer')
        root.geometry('1280x800')

        self.scan_result: Optional[ScanResult] = None
        self.current_kind: Optional[str] = None
        self.current_mode: Optional[str] = None
        self.current_index: int = 0
        self.current_files: List[FileEntry] = []

        # figure/axes are only (re)created when the kind or mode
        # changes -- not on every navigation step -- so the panel
        # geometry never moves while stepping through files.
        self.fig = None
        self.axes = None
        self.canvas: Optional[FigureCanvasTkAgg] = None
        self.toolbar: Optional[NavigationToolbar2Tk] = None

        self._speed_xlim = None
        self._height_ylim = None

        # last-loaded data, kept so the height-range control can redraw
        # (e.g. after a stepper click) without re-reading files from disk
        self._last_profile = None
        self._last_series = None

        self._build_widgets()

        if initial_dir:
            self.dir_var.set(str(initial_dir))
            self._rescan()

    # ------------------------------------------------------------------
    # widget construction
    # ------------------------------------------------------------------

    def _build_widgets(self) -> None:
        self.root.columnconfigure(0, weight=1, minsize=280)
        self.root.columnconfigure(1, weight=3)
        self.root.rowconfigure(0, weight=1)

        left = ttk.Frame(self.root, padding=8)
        left.grid(row=0, column=0, sticky='nswe')
        left.columnconfigure(0, weight=1)

        right = ttk.Frame(self.root)
        right.grid(row=0, column=1, sticky='nswe')
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        self._build_left_panel(left)
        self._build_right_panel(right)

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        row = 0

        # -- root directory -------------------------------------------------
        ttk.Label(parent, text='Root directory').grid(
            row=row, column=0, sticky='w')
        row += 1
        dir_frame = ttk.Frame(parent)
        dir_frame.grid(row=row, column=0, sticky='we', pady=(0, 8))
        dir_frame.columnconfigure(0, weight=1)
        self.dir_var = tk.StringVar()
        dir_entry = ttk.Entry(dir_frame, textvariable=self.dir_var)
        dir_entry.grid(row=0, column=0, sticky='we')
        dir_entry.bind('<Return>', lambda e: self._rescan())
        ttk.Button(dir_frame, text='Browse…',
                   command=self._pick_directory).grid(row=0, column=1, padx=(4, 0))
        row += 1

        # -- kind list --------------------------------------------------
        ttk.Label(parent, text='File kind').grid(row=row, column=0, sticky='w')
        row += 1
        kind_frame = ttk.Frame(parent)
        kind_frame.grid(row=row, column=0, sticky='nswe', pady=(0, 8))
        kind_frame.columnconfigure(0, weight=1)
        parent.rowconfigure(row, weight=1)
        self.kind_list = tk.Listbox(kind_frame, height=8, exportselection=False)
        self.kind_list.grid(row=0, column=0, sticky='nswe')
        kind_scroll = ttk.Scrollbar(kind_frame, orient='vertical',
                                     command=self.kind_list.yview)
        kind_scroll.grid(row=0, column=1, sticky='ns')
        self.kind_list.configure(yscrollcommand=kind_scroll.set)
        self.kind_list.bind('<<ListboxSelect>>', self._on_kind_select)
        row += 1

        # -- plot mode ----------------------------------------------------
        ttk.Label(parent, text='Plot type').grid(row=row, column=0, sticky='w')
        row += 1
        mode_frame = ttk.Frame(parent)
        mode_frame.grid(row=row, column=0, sticky='w', pady=(0, 8))
        self.mode_var = tk.StringVar(value=PROFILE_MODE)
        self.profile_radio = ttk.Radiobutton(
            mode_frame, text='Profile', value=PROFILE_MODE,
            variable=self.mode_var, command=self._on_mode_select)
        self.profile_radio.grid(row=0, column=0, sticky='w', padx=(0, 8))
        self.timeseries_radio = ttk.Radiobutton(
            mode_frame, text='Time series', value=TIMESERIES_MODE,
            variable=self.mode_var, command=self._on_mode_select)
        self.timeseries_radio.grid(row=0, column=1, sticky='w')
        row += 1

        # -- height range ---------------------------------------------------
        # Bottom and Top sit side by side, each as a label above one row
        # of [entry][up][down] -- this keeps the control only as tall as
        # a single entry/button row instead of stacking the up/down
        # buttons above one another.
        height_frame = ttk.LabelFrame(parent, text='Height')
        height_frame.grid(row=row, column=0, sticky='we', pady=(0, 8))
        height_frame.columnconfigure(0, weight=1)
        height_frame.columnconfigure(1, weight=1)
        row += 1

        self.height_bottom_var = tk.StringVar(value='0')
        self.height_top_var = tk.StringVar(value='')

        ttk.Label(height_frame, text='Bottom (m)').grid(
            row=0, column=0, sticky='w', padx=(4, 2), pady=(2, 0))
        ttk.Label(height_frame, text='Top (m)').grid(
            row=0, column=1, sticky='w', padx=(2, 4), pady=(2, 0))

        bottom_row = ttk.Frame(height_frame)
        bottom_row.grid(row=1, column=0, sticky='we', padx=(4, 2))
        bottom_row.columnconfigure(0, weight=1)
        self.height_bottom_entry = ttk.Entry(
            bottom_row, textvariable=self.height_bottom_var, width=7)
        self.height_bottom_entry.grid(row=0, column=0, sticky='we')
        self.height_bottom_entry.bind(
            '<Return>', lambda e: self._on_height_entry_change())
        self.height_bottom_up = ttk.Button(
            bottom_row, text='▲', width=2,
            command=lambda: self._step_height('bottom', 1))
        self.height_bottom_up.grid(row=0, column=1)
        self.height_bottom_down = ttk.Button(
            bottom_row, text='▼', width=2,
            command=lambda: self._step_height('bottom', -1))
        self.height_bottom_down.grid(row=0, column=2)

        top_row = ttk.Frame(height_frame)
        top_row.grid(row=1, column=1, sticky='we', padx=(2, 4))
        top_row.columnconfigure(0, weight=1)
        self.height_top_entry = ttk.Entry(
            top_row, textvariable=self.height_top_var, width=7)
        self.height_top_entry.grid(row=0, column=0, sticky='we')
        self.height_top_entry.bind(
            '<Return>', lambda e: self._on_height_entry_change())
        self.height_top_up = ttk.Button(
            top_row, text='▲', width=2,
            command=lambda: self._step_height('top', 1))
        self.height_top_up.grid(row=0, column=1)
        self.height_top_down = ttk.Button(
            top_row, text='▼', width=2,
            command=lambda: self._step_height('top', -1))
        self.height_top_down.grid(row=0, column=2)

        self.height_auto_var = tk.BooleanVar(value=True)
        self.height_auto_check = ttk.Checkbutton(
            height_frame, text='Auto', variable=self.height_auto_var,
            command=self._on_height_auto_toggle)
        self.height_auto_check.grid(
            row=2, column=0, columnspan=2, sticky='w', padx=4, pady=(2, 4))
        # Auto starts on, so the (not yet meaningful) manual controls
        # start disabled -- _on_height_auto_toggle sets this consistently
        # any time Auto is toggled, this just matches that at startup.
        for w in (self.height_bottom_entry, self.height_top_entry,
                  self.height_bottom_up, self.height_bottom_down,
                  self.height_top_up, self.height_top_down):
            w.configure(state='disabled')

        # -- time range: Start time / quick presets / End time, all inside
        # one "Time" frame. Apply range and the First/Back/Forward/Last
        # browse buttons stay outside it. ------------------------------
        time_frame = ttk.LabelFrame(parent, text='Time')
        time_frame.grid(row=row, column=0, sticky='we', pady=(0, 8))
        time_frame.columnconfigure(0, weight=1)
        row += 1
        trow = 0

        ttk.Label(time_frame, text='Start time').grid(
            row=trow, column=0, sticky='w', padx=4, pady=(2, 0))
        trow += 1
        self.start_var = tk.StringVar()
        self.start_entry = ttk.Entry(time_frame, textvariable=self.start_var)
        self.start_entry.grid(row=trow, column=0, sticky='we', padx=4)
        self.start_entry.bind('<Return>', lambda e: self._apply_range())
        trow += 1

        # quick range presets -- wrapped at _PRESET_COLUMNS per row so
        # they fit the narrow left panel in two short rows instead of
        # one very wide one.
        self.time_preset_var = tk.StringVar(value='custom')
        preset_frame = ttk.Frame(time_frame)
        preset_frame.grid(row=trow, column=0, sticky='we', padx=2, pady=(4, 4))
        for c in range(_PRESET_COLUMNS):
            preset_frame.columnconfigure(c, weight=1)
        for i, (key, label, _delta) in enumerate(_TIME_PRESETS):
            ttk.Radiobutton(
                preset_frame, text=label, value=key,
                variable=self.time_preset_var,
                command=self._on_time_preset_change).grid(
                row=i // _PRESET_COLUMNS, column=i % _PRESET_COLUMNS,
                sticky='w')
        trow += 1

        ttk.Label(time_frame, text='End time').grid(
            row=trow, column=0, sticky='w', padx=4)
        trow += 1
        self.end_var = tk.StringVar()
        end_entry = ttk.Entry(time_frame, textvariable=self.end_var)
        end_entry.grid(row=trow, column=0, sticky='we', padx=4, pady=(0, 4))
        end_entry.bind('<Return>', lambda e: self._on_end_time_change())
        trow += 1

        ttk.Button(parent, text='Apply range',
                   command=self._apply_range).grid(
            row=row, column=0, sticky='we', pady=(2, 8))
        row += 1

        # -- browse buttons -------------------------------------------------
        ttk.Label(parent, text='Browse files').grid(row=row, column=0, sticky='w')
        row += 1
        nav_frame = ttk.Frame(parent)
        nav_frame.grid(row=row, column=0, sticky='we', pady=(0, 8))
        for c in range(4):
            nav_frame.columnconfigure(c, weight=1)
        self.btn_first = ttk.Button(nav_frame, text='⏮ First',
                                     command=self._go_first)
        self.btn_back = ttk.Button(nav_frame, text='◀ Back',
                                    command=self._go_back)
        self.btn_fwd = ttk.Button(nav_frame, text='Forward ▶',
                                   command=self._go_forward)
        self.btn_last = ttk.Button(nav_frame, text='Last ⏭',
                                    command=self._go_last)
        self.btn_first.grid(row=0, column=0, sticky='we')
        self.btn_back.grid(row=0, column=1, sticky='we')
        self.btn_fwd.grid(row=0, column=2, sticky='we')
        self.btn_last.grid(row=0, column=3, sticky='we')
        row += 1

        # -- status ---------------------------------------------------------
        self.status_var = tk.StringVar(value='Pick a root directory to begin.')
        status_label = ttk.Label(parent, textvariable=self.status_var,
                                  wraplength=260, foreground='#444444')
        status_label.grid(row=row, column=0, sticky='we', pady=(8, 0))

    def _build_right_panel(self, parent: ttk.Frame) -> None:
        self.plot_container = ttk.Frame(parent)
        self.plot_container.grid(row=0, column=0, sticky='nswe')
        self.plot_container.columnconfigure(0, weight=1)
        self.plot_container.rowconfigure(0, weight=1)
        self._set_mode_figure(PROFILE_MODE)

    # ------------------------------------------------------------------
    # figure (re)creation
    # ------------------------------------------------------------------

    def _set_mode_figure(self, mode: str) -> None:
        """(Re)build the figure/canvas for ``mode``. Only called when
        the mode actually changes, so panel geometry stays fixed while
        stepping through files within a mode."""
        if self.canvas is not None:
            self.canvas.get_tk_widget().destroy()
        if self.toolbar is not None:
            self.toolbar.destroy()

        if mode == TIMESERIES_MODE:
            self.fig, self.axes = plotting.create_timeseries_figure()
        else:
            self.fig, self.axes = plotting.create_profile_figure()

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_container)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky='nswe')
        toolbar_frame = ttk.Frame(self.plot_container)
        toolbar_frame.grid(row=1, column=0, sticky='we')
        self.toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        self.toolbar.update()
        self.current_mode = mode
        self.canvas.draw_idle()

    def _show_message(self, text: str) -> None:
        """Draw a plain status message on the canvas, used for kinds
        without plotting support yet, or empty selections."""
        self.fig.clf()
        ax = self.fig.add_subplot(111)
        ax.axis('off')
        ax.text(0.5, 0.5, text, ha='center', va='center', wrap=True,
                fontsize=11, transform=ax.transAxes)
        self.canvas.draw_idle()
        # the message figure has no fixed gridspec/axes to reuse, so
        # force a full rebuild next time a real plot is drawn
        self.axes = None

    # ------------------------------------------------------------------
    # directory scanning
    # ------------------------------------------------------------------

    def _pick_directory(self) -> None:
        chosen = filedialog.askdirectory(
            initialdir=self.dir_var.get() or None, mustexist=True,
            title='Select the Proc root (or a directory below it)')
        if chosen:
            self.dir_var.set(chosen)
            self._rescan()

    def _rescan(self) -> None:
        root_dir = self.dir_var.get().strip()
        if not root_dir:
            return
        path = Path(root_dir)
        if not path.exists():
            messagebox.showerror('WindLidar Viewer',
                                  f'Directory does not exist:\n{path}')
            return
        self.status_var.set(f'Scanning {path}…')
        self.root.update_idletasks()
        self.scan_result = scan_directory(path)

        self.kind_list.delete(0, tk.END)
        kinds = self.scan_result.kinds()
        for kind in kinds:
            info = get_kind_info(kind)
            n = self.scan_result.count(kind)
            label = f'{kind}  ({n})'
            if not info.supported:
                label = f'{label}  — not yet supported'
            self.kind_list.insert(tk.END, label)
        self._kind_names = kinds

        if kinds:
            self.kind_list.selection_set(0)
            self._select_kind(kinds[0])
            self.status_var.set(
                f'Found {len(kinds)} file kind(s) under {path}.')
        else:
            self.current_kind = None
            self.current_files = []
            self._show_message(f'No .hpl files found under\n{path}')
            self.status_var.set(f'No .hpl files found under {path}.')

    # ------------------------------------------------------------------
    # selection handlers
    # ------------------------------------------------------------------

    def _on_kind_select(self, _event=None) -> None:
        sel = self.kind_list.curselection()
        if not sel:
            return
        kind = self._kind_names[sel[0]]
        self._select_kind(kind)

    def _select_kind(self, kind: str) -> None:
        self.current_kind = kind
        info = get_kind_info(kind)

        # enable/disable the mode radio buttons to match this kind's
        # capabilities
        self.profile_radio.configure(
            state='normal' if PROFILE_MODE in info.modes else 'disabled')
        self.timeseries_radio.configure(
            state='normal' if TIMESERIES_MODE in info.modes else 'disabled')
        if info.modes:
            if self.mode_var.get() not in info.modes:
                self.mode_var.set(info.modes[0])
        self.current_mode = None  # force figure rebuild in _refresh_mode

        lo, hi = self.scan_result.time_range(kind)
        if lo is not None:
            self.start_var.set(lo.strftime(_TIME_FMT))
            self.end_var.set(hi.strftime(_TIME_FMT))
            # if a "N before" preset is active, recompute start from
            # this kind's own end time rather than keeping the other
            # kind's start
            self._apply_time_preset()

        self._refresh_mode()

    def _on_mode_select(self) -> None:
        self._refresh_mode()

    def _refresh_mode(self) -> None:
        mode = self.mode_var.get()
        info = get_kind_info(self.current_kind) if self.current_kind else None

        if info is None or not info.modes:
            self._update_nav_state()
            if self.current_mode != PROFILE_MODE:
                self._set_mode_figure(PROFILE_MODE)
            message = ('No plottable data selected.' if info is None else
                       f'Plotting for "{self.current_kind}" is not '
                       f'implemented yet.')
            self._show_message(message)
            self.status_var.set(message)
            return

        self._update_nav_state()

        if mode not in info.modes:
            mode = info.modes[0]
            self.mode_var.set(mode)

        if self.current_mode != mode:
            self._set_mode_figure(mode)

        self._apply_range()

    # ------------------------------------------------------------------
    # time range
    # ------------------------------------------------------------------

    def _parse_time(self, text: str) -> Optional[pd.Timestamp]:
        text = text.strip()
        if not text:
            return None
        try:
            return pd.Timestamp(text)
        except (ValueError, TypeError):
            messagebox.showerror(
                'WindLidar Viewer',
                f'Could not parse time {text!r}.\n'
                f'Expected a format like "{_TIME_FMT}".')
            return None

    def _apply_time_preset(self) -> None:
        """Sync the Start time field/entry state to the active preset
        radio button: "custom" hands the field back for manual entry,
        anything else computes Start = End - offset and locks the field
        (read-only, like the height range's Auto mode)."""
        delta = _TIME_PRESET_DELTAS.get(self.time_preset_var.get())
        if delta is None:
            self.start_entry.configure(state='normal')
            return
        self.start_entry.configure(state='disabled')
        end = self._parse_time(self.end_var.get())
        if end is None:
            return
        self.start_var.set((end - delta).strftime(_TIME_FMT))

    def _on_time_preset_change(self) -> None:
        self._apply_time_preset()
        self._update_nav_state()
        self._apply_range()

    def _interval_active(self) -> bool:
        """True when a fixed-length preset (Week/2 days/24h/12h/6h) is
        selected rather than Custom -- this is what switches the browse
        buttons from per-file stepping to whole-window shifting."""
        return self.time_preset_var.get() != 'custom'

    def _on_end_time_change(self) -> None:
        # End time is the anchor every non-custom preset is relative
        # to, so recompute Start before reloading.
        self._apply_time_preset()
        self._apply_range()

    def _apply_range(self) -> None:
        if not self.current_kind or self.scan_result is None:
            return
        start = self._parse_time(self.start_var.get())
        end = self._parse_time(self.end_var.get())
        self.current_files = self.scan_result.files_in_range(
            self.current_kind, start, end)
        self.current_index = 0
        self._speed_xlim = None
        # a manually-set height range (Auto off) is a view preference,
        # not tied to which files are loaded -- keep it across a range
        # change; only the auto-computed range needs to be cleared so
        # it gets recomputed from the new file selection.
        if self.height_auto_var.get():
            self._height_ylim = None

        if not self.current_files:
            self._show_message('No files in the selected time range.')
            self.status_var.set('No files in the selected time range.')
            return

        if self.current_mode == TIMESERIES_MODE:
            self._draw_timeseries()
        else:
            self._compute_profile_limits()
            self._draw_profile()

    # ------------------------------------------------------------------
    # navigation
    # ------------------------------------------------------------------
    #
    # Two distinct behaviours share the same four buttons, switched on
    # by the active time preset:
    #  - Custom: the original per-file browsing within the files that
    #    "Apply range" already loaded (First/Last jump to the first/last
    #    loaded file, Back/Forward step one file at a time).
    #  - a fixed-length preset (Week/2 days/24h/12h/6h): the buttons
    #    instead shift the whole [start, end] time *window* by one
    #    interval and reload -- First/Last jump the window to the true
    #    start/end of this kind's data, Back/Forward step the window by
    #    exactly one interval, snapped to a year-start-anchored grid so
    #    repeated stepping can't drift off round numbers.

    def _update_nav_state(self) -> None:
        info = get_kind_info(self.current_kind) if self.current_kind else None
        if info is None or not info.modes:
            state = 'disabled'
        else:
            state = ('normal' if (self._interval_active() or
                                   self.mode_var.get() == PROFILE_MODE)
                      else 'disabled')
        for b in (self.btn_first, self.btn_back, self.btn_fwd, self.btn_last):
            b.configure(state=state)

    def _window_to_data_edge(self, which: str) -> None:
        """First/Last under a fixed-length preset: align the window to
        this kind's actual data start/end (not snapped to the interval
        grid -- the data edge itself is the exact anchor requested)."""
        delta = _TIME_PRESET_DELTAS.get(self.time_preset_var.get())
        if delta is None or self.scan_result is None or not self.current_kind:
            return
        lo, hi = self.scan_result.time_range(self.current_kind)
        if lo is None:
            return
        if which == 'start':
            start, end = lo, lo + delta
        else:
            start, end = hi - delta, hi
        self.start_var.set(start.strftime(_TIME_FMT))
        self.end_var.set(end.strftime(_TIME_FMT))
        self._apply_range()

    def _step_time_window(self, direction: int) -> None:
        """Back/Forward under a fixed-length preset: shift the window by
        one interval. The new End time is snapped to the grid of
        interval-length multiples anchored at the start of its year, so
        stepping always lands on round numbers regardless of where the
        window happened to start."""
        delta = _TIME_PRESET_DELTAS.get(self.time_preset_var.get())
        if delta is None:
            return
        end = self._parse_time(self.end_var.get())
        if end is None:
            return
        anchor = pd.Timestamp(year=end.year, month=1, day=1)
        k = round((end - anchor) / delta)
        new_end = anchor + (k + direction) * delta
        self.start_var.set((new_end - delta).strftime(_TIME_FMT))
        self.end_var.set(new_end.strftime(_TIME_FMT))
        self._apply_range()

    def _go_first(self) -> None:
        if self._interval_active():
            self._window_to_data_edge('start')
            return
        self.current_index = 0
        self._draw_profile()

    def _go_back(self) -> None:
        if self._interval_active():
            self._step_time_window(-1)
            return
        self.current_index = max(0, self.current_index - 1)
        self._draw_profile()

    def _go_forward(self) -> None:
        if self._interval_active():
            self._step_time_window(1)
            return
        self.current_index = min(len(self.current_files) - 1,
                                  self.current_index + 1)
        self._draw_profile()

    def _go_last(self) -> None:
        if self._interval_active():
            self._window_to_data_edge('end')
            return
        self.current_index = len(self.current_files) - 1
        self._draw_profile()

    # ------------------------------------------------------------------
    # drawing
    # ------------------------------------------------------------------

    def _compute_profile_limits(self) -> None:
        """Establish fixed speed/height axis limits from a sample of
        the current file selection, so stepping through files with
        First/Back/Forward/Last never resizes the panels. Speed is
        always recomputed; height only when the height range is in
        Auto mode -- a manual range is a view preference the user set,
        not something a new file selection should overwrite."""
        sample = _sample(self.current_files, _MAX_FILES_FOR_LIMITS)
        speed_max = 1.0
        h_min, h_max = None, None
        for entry in sample:
            try:
                prof = _data.load_profile(entry.path)
            except (IOError, ValueError):
                continue
            if prof.speed.size:
                speed_max = max(speed_max, float(pd.Series(prof.speed).max()))
            if prof.height.size:
                lo = float(prof.height.min())
                hi = float(prof.height.max())
                h_min = lo if h_min is None else min(h_min, lo)
                h_max = hi if h_max is None else max(h_max, hi)
        self._speed_xlim = (0.0, min(speed_max * 1.1, plotting.MAX_AUTOSCALE_SPEED))
        if self.height_auto_var.get():
            if h_min is not None:
                pad = (h_max - h_min) * 0.03 if h_max > h_min else 1.0
                self._height_ylim = (h_min - pad, h_max + pad)
            else:
                self._height_ylim = None

    def _draw_profile(self) -> None:
        """Load the current file and render it. Loading is the
        expensive part; :meth:`_render_profile` (called from here and
        from the height-range control) is the cheap redraw-only path."""
        if not self.current_files:
            return
        if self.axes is None or self.current_mode != PROFILE_MODE:
            self._set_mode_figure(PROFILE_MODE)
        entry = self.current_files[self.current_index]
        try:
            self._last_profile = _data.load_profile(entry.path)
        except (IOError, ValueError) as exc:
            self._show_message(f'Could not read {entry.path.name}:\n{exc}')
            return
        self._render_profile()
        self.status_var.set(
            f'{entry.path.name}\n'
            f'File {self.current_index + 1} of {len(self.current_files)}')

    def _render_profile(self) -> None:
        """Redraw the profile plot from :attr:`_last_profile` and the
        current axis limits, without touching disk."""
        prof = self._last_profile
        if prof is None or self.axes is None:
            return
        ax_speed, ax_dir = self.axes
        plotting.plot_wind_profile(
            ax_speed, ax_dir, prof.height, prof.speed, prof.direction,
            speed_xlim=self._speed_xlim, height_ylim=self._height_ylim,
            title=f'{self.current_kind}  {prof.timestamp:%Y-%m-%d %H:%M:%S}')
        self.canvas.draw_idle()
        self._sync_height_fields_from_axes()

    def _draw_timeseries(self) -> None:
        """Load the current file selection and render it; see
        :meth:`_draw_profile` for the load/render split."""
        if not self.current_files:
            return
        if self.axes is None or self.current_mode != TIMESERIES_MODE:
            self._set_mode_figure(TIMESERIES_MODE)
        files = self.current_files
        if len(files) > _MAX_FILES_FOR_TIMESERIES:
            files = _sample(files, _MAX_FILES_FOR_TIMESERIES)
        try:
            self._last_series = _data.load_profile_series(e.path for e in files)
        except (IOError, ValueError) as exc:
            self._show_message(f'Could not build time series:\n{exc}')
            return
        self._render_timeseries()
        self.status_var.set(f'{len(files)} file(s) in the selected range.')

    def _render_timeseries(self) -> None:
        """Redraw the timeseries plot from :attr:`_last_series` and the
        current axis limits, without touching disk."""
        series = self._last_series
        if series is None or self.axes is None:
            return
        ax_speed, ax_dir, cax_speed, cax_dir = self.axes
        title = self.current_kind
        if len(series.times):
            title = (f'{self.current_kind}  '
                      f'{series.times[0]:%Y-%m-%d %H:%M} – '
                      f'{series.times[-1]:%Y-%m-%d %H:%M}')
        plotting.plot_wind_timeseries(
            ax_speed, ax_dir, cax_speed, cax_dir,
            series.times, series.height, series.speed, series.direction,
            height_ylim=self._height_ylim, title=title)
        self.canvas.draw_idle()
        self._sync_height_fields_from_axes()

    # ------------------------------------------------------------------
    # height range
    # ------------------------------------------------------------------

    def _height_display_range(self):
        """Parse the height Bottom/Top fields as floats, or ``None`` if
        either is currently empty/invalid."""
        try:
            bottom = float(self.height_bottom_var.get())
            top = float(self.height_top_var.get())
        except (TypeError, ValueError):
            return None
        return bottom, top

    def _current_height_ylim(self):
        if self.axes is None:
            return None
        return self.axes[0].get_ylim()

    def _sync_height_fields_from_axes(self) -> None:
        """In Auto mode, mirror the plot's actual (autoscaled) height
        limits into the -- disabled, display-only -- Bottom/Top fields,
        so they always show real numbers rather than stale ones."""
        if not self.height_auto_var.get() or self.axes is None:
            return
        lo, hi = self.axes[0].get_ylim()
        self.height_bottom_var.set(f'{lo:.1f}')
        self.height_top_var.set(f'{hi:.1f}')

    def _on_height_auto_toggle(self) -> None:
        auto = self.height_auto_var.get()
        state = 'disabled' if auto else 'normal'
        for w in (self.height_bottom_entry, self.height_top_entry,
                  self.height_bottom_up, self.height_bottom_down,
                  self.height_top_up, self.height_top_down):
            w.configure(state=state)
        if auto:
            self._height_ylim = None
            if self.current_files:
                if self.current_mode == PROFILE_MODE:
                    self._compute_profile_limits()
                    self._render_profile()
                elif self.current_mode == TIMESERIES_MODE:
                    self._render_timeseries()
        else:
            # seed the now-editable fields: bottom defaults to ground
            # level, top defaults to whatever Auto was just showing
            self.height_bottom_var.set('0')
            lo, hi = self._current_height_ylim() or (0.0, 100.0)
            self.height_top_var.set(f'{hi:.1f}')
            self._redraw_height_range()

    def _on_height_entry_change(self) -> None:
        self._redraw_height_range()

    def _step_height(self, which: str, direction: int) -> None:
        """Step Bottom or Top by the current step size, snapping the
        result to a multiple of that step size (0, step, 2*step, ...)
        rather than just adding it to whatever fractional value is
        currently shown -- so repeated stepping (and stepping after
        Auto seeded a non-round value) always lands on round numbers,
        the same "snap to a grid" rule the time-window Back/Forward
        buttons use."""
        rng = self._height_display_range()
        if rng is None:
            return
        bottom, top = rng
        step = _height_step_size(max(top - bottom, _HEIGHT_MIN_SPAN))
        if which == 'bottom':
            new_bottom = (round(bottom / step) + direction) * step
            # refuse a step that would shrink the span below the
            # minimum, rather than overshooting the other end to force
            # it back to exactly the minimum -- growing the span (the
            # other direction) is always allowed
            if top - new_bottom >= _HEIGHT_MIN_SPAN:
                bottom = new_bottom
        else:
            new_top = (round(top / step) + direction) * step
            if new_top - bottom >= _HEIGHT_MIN_SPAN:
                top = new_top
        self.height_bottom_var.set(f'{bottom:.1f}')
        self.height_top_var.set(f'{top:.1f}')
        self._redraw_height_range()

    def _redraw_height_range(self) -> None:
        """Apply a manually-entered height range to the current plot.
        Cheap: redraws from :attr:`_last_profile`/:attr:`_last_series`
        (already in memory) rather than reloading from disk, so the
        stepper buttons and typed edits feel immediate."""
        if self.height_auto_var.get() or self.axes is None:
            return
        rng = self._height_display_range()
        if rng is None or rng[1] <= rng[0]:
            return
        self._height_ylim = rng
        if self.current_mode == PROFILE_MODE:
            self._render_profile()
        elif self.current_mode == TIMESERIES_MODE:
            self._render_timeseries()


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    initial_dir = argv[0] if argv else None
    root = tk.Tk()
    WindLidarViewerApp(root, initial_dir=initial_dir)
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
