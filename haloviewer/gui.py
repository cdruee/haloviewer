# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5
"""
Tkinter desktop application for browsing Halo wind lidar ``Proc`` data.

Tkinter is used because it ships with the standard ``python`` conda
package on Linux, macOS and Windows (via its ``tk`` dependency), so a
plain ``conda install numpy pandas matplotlib`` (see
``environment.yml``) is enough to run this GUI -- no extra GUI toolkit
package is needed.

This module only wires widgets to the pure functions in
:mod:`haloviewer.scan`, :mod:`haloviewer.data` and
:mod:`haloviewer.plotting`; it contains no plotting logic of its
own (see those modules' docstrings), and no file-format knowledge (see
:mod:`haloviewer.hpl`).

Four (kind, mode) combinations are plottable, each routed to its own
load/render pair (see :meth:`HaloViewerApp._plot_kind`):

* ``wind_profile`` -- ``Processed_Wind_Profile`` + Profile: one file's
  height/speed/direction profile.
* ``wind_timeseries`` -- ``Processed_Wind_Profile`` + History: many
  files' height/time speed+direction image.
* ``scan_history`` -- VAD/Stare/Wind_Profile/RHI + History: many
  files' distance/time intensity+beta image (raw scans have no
  instrument-processed profile of their own).
* ``rhi_profile`` -- RHI + Profile: one scan's distance/height cross
  section (radial velocity + beta), the only other kind with a Profile
  mode.

Three range controls (Height, Distance, Speed -- see
:class:`_RangeControl`) sit in the left panel; which ones are enabled
depends on the current plot kind (:meth:`HaloViewerApp.
_update_range_controls_enabled`).

An optional intensity filter ("Filter (intensity < [1.018])") blanks
every data point whose intensity is below the entered threshold; see
:meth:`HaloViewerApp._intensity_min` and the ``intensity_min`` argument
of the :mod:`haloviewer.data` loaders.
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg,
                                                NavigationToolbar2Tk)

from . import data as _data
from . import plotting
from .scan import (PROFILE_MODE, TIMESERIES_MODE, FileEntry, ScanResult,
                    get_kind_info, scan_directory)

_TIME_FMT = '%Y-%m-%d %H:%M:%S'

# Cap on how many files are opened just to establish stable axis limits
# for profile browsing / a history redraw, so a huge time range doesn't
# make the GUI stall. Sampling evenly across the range still gives a
# representative min/max in practice.
_MAX_FILES_FOR_LIMITS = 300
_MAX_FILES_FOR_TIMESERIES = 4000

# Step size tables for the Height/Distance/Speed range controls' up/down
# buttons: a round number that scales with how wide the current
# (bottom, top) view is. Read as "if the span is > threshold, use this
# step" -- the largest matching threshold wins, so a very wide view
# still only steps by the largest cap rather than growing without bound.
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

_DISTANCE_STEP_TABLE = [
    (50.0, 5.0),
    (100.0, 10.0),
    (250.0, 25.0),
    (500.0, 50.0),
    (1000.0, 100.0),
    (2500.0, 250.0),
    (5000.0, 500.0),
    (10000.0, 1000.0),
]
_DISTANCE_MIN_SPAN = 50.0

_SPEED_STEP_TABLE = [
    (5.0, 0.5),
    (10.0, 1.0),
    (25.0, 2.5),
    (50.0, 5.0),
    (100.0, 10.0),
]
_SPEED_MIN_SPAN = 2.0


def _table_step_size(table: List[Tuple[float, float]], span: float) -> float:
    """Up/down step size for a range control whose current view is
    ``span`` wide; see e.g. :data:`_HEIGHT_STEP_TABLE`."""
    step = table[0][1]
    for threshold, s in table:
        if span > threshold:
            step = s
    return step


def _pad_range(lo: Optional[float], hi: Optional[float],
               default: Tuple[float, float] = (0.0, 1.0)
               ) -> Tuple[float, float]:
    """``(lo, hi)`` widened by a small margin, or ``default`` if no data
    was seen at all (``lo is None``)."""
    if lo is None:
        return default
    pad = (hi - lo) * 0.03 if hi > lo else 1.0
    return lo - pad, hi + pad


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


class _RangeControl:
    """
    A ``ttk.LabelFrame`` with Bottom/Top numeric fields (each with an
    inline ▲/▼ stepper) and an "Auto" checkbox -- the reusable shape
    behind the Height, Distance and Speed controls in the left panel.

    "Auto" means the owning app computes a natural range (from the
    data, or by reading back the plotted axes) and mirrors it into
    these -- then disabled, display-only -- fields. Turning Auto off
    hands the fields back for manual entry/stepping, and the value the
    fields hold is then used verbatim.

    This class only owns the widgets and the stepping arithmetic; it
    has no idea what it controls or how to redraw a plot -- that's the
    two callbacks the owning app supplies.
    """

    def __init__(self, parent: ttk.Frame, row: int, *, title: str,
                 bottom_label: str, top_label: str, default_bottom: str,
                 step_table: List[Tuple[float, float]], min_span: float,
                 on_auto_toggle, on_manual_change):
        self.step_table = step_table
        self.min_span = min_span
        self.on_auto_toggle = on_auto_toggle
        self.on_manual_change = on_manual_change

        # Bottom and Top sit side by side, each as a label above one
        # row of [entry][up][down] -- this keeps the control only as
        # tall as a single entry/button row instead of stacking the
        # up/down buttons above one another.
        self.frame = ttk.LabelFrame(parent, text=title)
        self.frame.grid(row=row, column=0, sticky='we', pady=(0, 8))
        self.frame.columnconfigure(0, weight=1)
        self.frame.columnconfigure(1, weight=1)

        self.bottom_var = tk.StringVar(value=default_bottom)
        self.top_var = tk.StringVar(value='')

        ttk.Label(self.frame, text=bottom_label).grid(
            row=0, column=0, sticky='w', padx=(4, 2), pady=(2, 0))
        ttk.Label(self.frame, text=top_label).grid(
            row=0, column=1, sticky='w', padx=(2, 4), pady=(2, 0))

        bottom_row = ttk.Frame(self.frame)
        bottom_row.grid(row=1, column=0, sticky='we', padx=(4, 2))
        bottom_row.columnconfigure(0, weight=1)
        self.bottom_entry = ttk.Entry(
            bottom_row, textvariable=self.bottom_var, width=7)
        self.bottom_entry.grid(row=0, column=0, sticky='we')
        self.bottom_entry.bind(
            '<Return>', lambda e: self.on_manual_change())
        self.bottom_up = ttk.Button(
            bottom_row, text='▲', width=2,
            command=lambda: self._step('bottom', 1))
        self.bottom_up.grid(row=0, column=1)
        self.bottom_down = ttk.Button(
            bottom_row, text='▼', width=2,
            command=lambda: self._step('bottom', -1))
        self.bottom_down.grid(row=0, column=2)

        top_row = ttk.Frame(self.frame)
        top_row.grid(row=1, column=1, sticky='we', padx=(2, 4))
        top_row.columnconfigure(0, weight=1)
        self.top_entry = ttk.Entry(
            top_row, textvariable=self.top_var, width=7)
        self.top_entry.grid(row=0, column=0, sticky='we')
        self.top_entry.bind('<Return>', lambda e: self.on_manual_change())
        self.top_up = ttk.Button(
            top_row, text='▲', width=2,
            command=lambda: self._step('top', 1))
        self.top_up.grid(row=0, column=1)
        self.top_down = ttk.Button(
            top_row, text='▼', width=2,
            command=lambda: self._step('top', -1))
        self.top_down.grid(row=0, column=2)

        self.auto_var = tk.BooleanVar(value=True)
        self.auto_check = ttk.Checkbutton(
            self.frame, text='Auto', variable=self.auto_var,
            command=self._on_auto_toggle)
        self.auto_check.grid(
            row=2, column=0, columnspan=2, sticky='w', padx=4, pady=(2, 4))

        self._manual_widgets = (
            self.bottom_entry, self.top_entry,
            self.bottom_up, self.bottom_down,
            self.top_up, self.top_down)
        # Auto starts on, so the (not yet meaningful) manual controls
        # start disabled -- _on_auto_toggle sets this consistently any
        # time Auto is toggled, this just matches that at startup.
        for w in self._manual_widgets:
            w.configure(state='disabled')

    # -- enable/disable the whole control (kind/mode-dependent greying) --

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.auto_check.configure(state='normal')
            manual_state = 'disabled' if self.auto_var.get() else 'normal'
            for w in self._manual_widgets:
                w.configure(state=manual_state)
        else:
            self.auto_check.configure(state='disabled')
            for w in self._manual_widgets:
                w.configure(state='disabled')

    # -- value access ---------------------------------------------------

    def display_range(self) -> Optional[Tuple[float, float]]:
        """Parse the Bottom/Top fields as floats, or ``None`` if either
        is currently empty/invalid."""
        try:
            bottom = float(self.bottom_var.get())
            top = float(self.top_var.get())
        except (TypeError, ValueError):
            return None
        return bottom, top

    def set_display_range(self, lo: float, hi: float) -> None:
        self.bottom_var.set(f'{lo:.1f}')
        self.top_var.set(f'{hi:.1f}')

    # -- internal ---------------------------------------------------------

    def _on_auto_toggle(self) -> None:
        auto = self.auto_var.get()
        state = 'disabled' if auto else 'normal'
        for w in self._manual_widgets:
            w.configure(state=state)
        self.on_auto_toggle()

    def _step(self, which: str, direction: int) -> None:
        """Step Bottom or Top by the current step size, snapping the
        result to a multiple of that step size (0, step, 2*step, ...)
        rather than just adding it to whatever fractional value is
        currently shown -- so repeated stepping (and stepping after
        Auto seeded a non-round value) always lands on round numbers,
        the same "snap to a grid" rule the time-window Back/Forward
        buttons use."""
        rng = self.display_range()
        if rng is None:
            return
        bottom, top = rng
        step = _table_step_size(self.step_table, max(top - bottom, self.min_span))
        if which == 'bottom':
            new_bottom = (round(bottom / step) + direction) * step
            # refuse a step that would shrink the span below the
            # minimum, rather than overshooting the other end to force
            # it back to exactly the minimum -- growing the span (the
            # other direction) is always allowed
            if top - new_bottom >= self.min_span:
                bottom = new_bottom
        else:
            new_top = (round(top / step) + direction) * step
            if new_top - bottom >= self.min_span:
                top = new_top
        self.set_display_range(bottom, top)
        self.on_manual_change()


class HaloViewerApp:
    """Top-level application: owns the Tk widgets and the currently
    scanned/selected state, and delegates all data loading to
    :mod:`haloviewer.data`/:mod:`haloviewer.scan` and all
    drawing to :mod:`haloviewer.plotting`."""

    def __init__(self, root: tk.Tk, initial_dir: Optional[str] = None):
        self.root = root
        root.title('HaloViewer')
        root.geometry('1280x800')

        self.scan_result: Optional[ScanResult] = None
        self.current_kind: Optional[str] = None
        self.current_index: int = 0
        self.current_files: List[FileEntry] = []

        # figure/axes are only (re)created when the *plot kind* (see
        # _plot_kind) changes -- not on every navigation step -- so the
        # panel geometry never moves while stepping through files.
        self.fig = None
        self.axes = None
        self.current_plot_kind: Optional[str] = None
        self.canvas: Optional[FigureCanvasTkAgg] = None
        self.toolbar: Optional[NavigationToolbar2Tk] = None

        # explicit axis/colour ranges resolved by _resolve_range (or,
        # for height, read back from the axes) just before each redraw
        self._height_ylim = None
        self._distance_xlim = None
        self._speed_range = None

        # last-loaded data, kept so the range controls can redraw
        # (e.g. after a stepper click) without re-reading files from disk
        self._last_profile = None
        self._last_series = None
        self._last_history = None
        self._last_rhi = None
        self._kind_names: List[str] = []

        self._build_widgets()
        self._update_range_controls_enabled(None)

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

        # -- file kind: a read-only drop-down (one line instead of a
        # tall list box, to leave room for the other controls) ---------
        ttk.Label(parent, text='File kind').grid(row=row, column=0, sticky='w')
        row += 1
        self.kind_var = tk.StringVar()
        self.kind_combo = ttk.Combobox(parent, textvariable=self.kind_var,
                                        state='readonly', values=[])
        self.kind_combo.grid(row=row, column=0, sticky='we', pady=(0, 8))
        self.kind_combo.bind('<<ComboboxSelected>>', self._on_kind_select)
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
            mode_frame, text='History', value=TIMESERIES_MODE,
            variable=self.mode_var, command=self._on_mode_select)
        self.timeseries_radio.grid(row=0, column=1, sticky='w')
        row += 1

        # -- intensity filter: [x] Filter (intensity < [1.018]) ------------
        filter_frame = ttk.Frame(parent)
        filter_frame.grid(row=row, column=0, sticky='w', pady=(0, 8))
        self.filter_var = tk.BooleanVar(value=False)
        self.filter_check = ttk.Checkbutton(
            filter_frame, text='Filter (intensity <', variable=self.filter_var,
            command=self._on_filter_change)
        self.filter_check.grid(row=0, column=0, sticky='w')
        self.filter_value_var = tk.StringVar(
            value=f'{_data.DEFAULT_INTENSITY_FILTER:g}')
        self.filter_entry = ttk.Entry(
            filter_frame, textvariable=self.filter_value_var, width=6)
        self.filter_entry.grid(row=0, column=1, padx=(2, 0))
        self.filter_entry.bind('<Return>', lambda e: self._on_filter_change())
        self.filter_entry.bind('<FocusOut>',
                               lambda e: self._on_filter_value_leave())
        ttk.Label(filter_frame, text=')').grid(row=0, column=2, sticky='w')
        self._applied_filter: Optional[float] = None
        row += 1

        # -- range controls: Height (vertical axis -- height for the
        # processed profile/history, gate-inferred distance for the raw
        # scan history and RHI's own vertical axis), Distance (RHI
        # Profile's horizontal cross-section axis only) and Speed (wind
        # speed / radial velocity colour-or-axis range) -- all the same
        # shape, see _RangeControl. --------------------------------------
        self.height_ctrl = _RangeControl(
            parent, row, title='Height',
            bottom_label='Bottom (m)', top_label='Top (m)',
            default_bottom='0', step_table=_HEIGHT_STEP_TABLE,
            min_span=_HEIGHT_MIN_SPAN,
            on_auto_toggle=self._on_height_auto_toggle,
            on_manual_change=self._redraw_height_range)
        row += 1

        self.distance_ctrl = _RangeControl(
            parent, row, title='Distance',
            bottom_label='Near (m)', top_label='Far (m)',
            default_bottom='0', step_table=_DISTANCE_STEP_TABLE,
            min_span=_DISTANCE_MIN_SPAN,
            on_auto_toggle=self._on_distance_auto_toggle,
            on_manual_change=self._redraw_distance_range)
        row += 1

        self.speed_ctrl = _RangeControl(
            parent, row, title='Speed',
            bottom_label='Min (m/s)', top_label='Max (m/s)',
            default_bottom='0', step_table=_SPEED_STEP_TABLE,
            min_span=_SPEED_MIN_SPAN,
            on_auto_toggle=self._on_speed_auto_toggle,
            on_manual_change=self._redraw_speed_range)
        row += 1

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
        self._set_mode_figure('wind_profile')

    # ------------------------------------------------------------------
    # figure (re)creation
    # ------------------------------------------------------------------

    def _set_mode_figure(self, plot_kind: str) -> None:
        """(Re)build the figure/canvas for ``plot_kind`` (see
        :meth:`_plot_kind`). Only called when the plot kind actually
        changes, so panel geometry stays fixed while stepping through
        files within a plot kind."""
        if self.canvas is not None:
            self.canvas.get_tk_widget().destroy()
        if self.toolbar is not None:
            self.toolbar.destroy()

        if plot_kind == 'wind_profile':
            self.fig, self.axes = plotting.create_profile_figure()
        else:
            # wind_timeseries, scan_history and rhi_profile all share
            # the two-stacked-panels-with-colorbars shape.
            self.fig, self.axes = plotting.create_timeseries_figure()

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_container)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky='nswe')
        toolbar_frame = ttk.Frame(self.plot_container)
        toolbar_frame.grid(row=1, column=0, sticky='we')
        self.toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        self.toolbar.update()
        self.current_plot_kind = plot_kind
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
        self.current_plot_kind = None

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
            messagebox.showerror('HaloViewer',
                                  f'Directory does not exist:\n{path}')
            return
        self.status_var.set(f'Scanning {path}…')
        self.root.update_idletasks()
        self.scan_result = scan_directory(path)

        kinds = self.scan_result.kinds()
        labels = []
        for kind in kinds:
            info = get_kind_info(kind)
            n = self.scan_result.count(kind)
            label = f'{kind}  ({n})'
            if not info.supported:
                label = f'{label}  — not yet supported'
            labels.append(label)
        self.kind_combo.configure(values=labels)
        self._kind_names = kinds

        if kinds:
            self.kind_combo.current(0)
            self._select_kind(kinds[0])
            self.status_var.set(
                f'Found {len(kinds)} file kind(s) under {path}.')
        else:
            self.kind_var.set('')
            self.current_kind = None
            self.current_files = []
            self._update_range_controls_enabled(None)
            self._show_message(f'No .hpl files found under\n{path}')
            self.status_var.set(f'No .hpl files found under {path}.')

    # ------------------------------------------------------------------
    # selection handlers
    # ------------------------------------------------------------------

    def _on_kind_select(self, _event=None) -> None:
        idx = self.kind_combo.current()
        if idx < 0 or idx >= len(self._kind_names):
            return
        self._select_kind(self._kind_names[idx])

    def _select_kind(self, kind: str) -> None:
        self.current_kind = kind
        info = get_kind_info(kind)

        # enable/disable the mode radio buttons to match this kind's
        # capabilities -- only Processed_Wind_Profile and RHI have a
        # Profile mode; every other supported kind is greyed out there.
        self.profile_radio.configure(
            state='normal' if PROFILE_MODE in info.modes else 'disabled')
        self.timeseries_radio.configure(
            state='normal' if TIMESERIES_MODE in info.modes else 'disabled')
        if info.modes:
            if self.mode_var.get() not in info.modes:
                self.mode_var.set(info.modes[0])
        self.current_plot_kind = None  # force figure rebuild in _refresh_mode

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

    def _plot_kind(self) -> Optional[str]:
        """Which of the four load/render pipelines applies to the
        current kind + selected mode, or ``None`` if nothing plottable
        is selected. See the module docstring for what each one is."""
        if not self.current_kind:
            return None
        mode = self.mode_var.get()
        if self.current_kind == 'Processed_Wind_Profile':
            return 'wind_profile' if mode == PROFILE_MODE else 'wind_timeseries'
        if self.current_kind == 'RHI' and mode == PROFILE_MODE:
            return 'rhi_profile'
        # VAD, Stare, Wind_Profile (History only) and RHI's own History
        # all share the raw intensity/beta scan-history plot.
        return 'scan_history'

    def _update_range_controls_enabled(self, plot_kind: Optional[str]) -> None:
        """Grey out the Height/Distance/Speed controls that don't apply
        to the current plot kind: Height (the vertical axis) applies to
        all four; Distance (the horizontal cross-section axis) only to
        RHI's own Profile; Speed (the wind-speed/radial-velocity axis
        or colour range) to everything with a speed dimension -- i.e.
        everything except the raw scan history, whose two panels are
        intensity and beta."""
        self.height_ctrl.set_enabled(plot_kind is not None)
        self.filter_check.configure(
            state='normal' if plot_kind is not None else 'disabled')
        self.filter_entry.configure(
            state='normal' if plot_kind is not None else 'disabled')
        self.distance_ctrl.set_enabled(plot_kind == 'rhi_profile')
        self.speed_ctrl.set_enabled(
            plot_kind in ('wind_profile', 'wind_timeseries', 'rhi_profile'))

    def _refresh_mode(self) -> None:
        info = get_kind_info(self.current_kind) if self.current_kind else None

        if info is None or not info.modes:
            self._update_nav_state()
            self._update_range_controls_enabled(None)
            if self.current_plot_kind != 'wind_profile':
                self._set_mode_figure('wind_profile')
            message = ('No plottable data selected.' if info is None else
                       f'Plotting for "{self.current_kind}" is not '
                       f'implemented yet.')
            self._show_message(message)
            self.status_var.set(message)
            return

        mode = self.mode_var.get()
        if mode not in info.modes:
            mode = info.modes[0]
            self.mode_var.set(mode)

        self._update_nav_state()

        plot_kind = self._plot_kind()
        self._update_range_controls_enabled(plot_kind)
        if self.current_plot_kind != plot_kind:
            self._set_mode_figure(plot_kind)

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
                'HaloViewer',
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
        # a manually-set height range (Auto off) is a view preference,
        # not tied to which files are loaded -- keep it across a range
        # change; only the auto-computed range needs to be cleared so
        # it gets recomputed from the new file selection.
        if self.height_ctrl.auto_var.get():
            self._height_ylim = None

        if not self.current_files:
            self._show_message('No files in the selected time range.')
            self.status_var.set('No files in the selected time range.')
            return

        # wind_profile/rhi_profile step through files one at a time, so
        # their Height/Distance/Speed ranges are precomputed once from
        # a sample of the whole selection here (kept fixed while
        # stepping); wind_timeseries/scan_history load the whole
        # selection in one go and resolve their own ranges from that.
        plot_kind = self._plot_kind()
        if plot_kind == 'wind_profile':
            self._compute_profile_limits()
        elif plot_kind == 'rhi_profile':
            self._compute_rhi_profile_limits()
        self._draw_current()

    # ------------------------------------------------------------------
    # intensity filter
    # ------------------------------------------------------------------

    def _intensity_min(self) -> Optional[float]:
        """The active intensity filter threshold, or ``None`` when the
        Filter checkbox is off. An unparsable (or non-finite) value in
        the field is reported and replaced by the default threshold."""
        if not self.filter_var.get():
            return None
        text = self.filter_value_var.get().strip()
        try:
            value = float(text)
            if not np.isfinite(value):
                raise ValueError
        except ValueError:
            default = _data.DEFAULT_INTENSITY_FILTER
            messagebox.showerror(
                'HaloViewer',
                f'Invalid filter value {text!r}; using the default '
                f'{default:g}.')
            self.filter_value_var.set(f'{default:g}')
            value = default
        return value

    def _on_filter_value_leave(self) -> None:
        """Leaving the value field applies a changed value (only if the
        filter is on and the value actually differs from the one used
        for the current plot, so tabbing through is free)."""
        if not self.filter_var.get():
            return
        try:
            value = float(self.filter_value_var.get())
        except ValueError:
            value = None
        if value != self._applied_filter:
            self._on_filter_change()

    def _on_filter_change(self) -> None:
        """Filter toggled or value entered: reload the current
        selection with the new filter, keeping the file position in
        Profile mode (the filter changes what is shown, not which
        files are loaded)."""
        if not self.current_files:
            return
        plot_kind = self._plot_kind()
        if plot_kind == 'wind_profile':
            self._compute_profile_limits()
        elif plot_kind == 'rhi_profile':
            self._compute_rhi_profile_limits()
        self._draw_current()

    def _title(self, title: str) -> str:
        if self._applied_filter is None:
            return title
        return f'{title}  (intensity < {self._applied_filter:g} removed)'

    def _filter_status(self, missing) -> str:
        """Status-line note for Processed_Wind_Profile files the filter
        could not be applied to (no Wind_Profile file alongside)."""
        n = len(missing)
        if not n:
            return ''
        return (f'\nFilter not applied to {n} profile(s): no matching '
                f'Wind_Profile file.')

    # ------------------------------------------------------------------
    # navigation
    # ------------------------------------------------------------------
    #
    # Two distinct behaviours share the same four buttons, switched on
    # by the plot kind and the active time preset (_nav_shifts_window):
    #  - Profile mode (wind_profile, rhi_profile), or Custom: per-file
    #    browsing within the files that "Apply range" already loaded
    #    (First/Last jump to the first/last loaded file, Back/Forward
    #    step one file at a time).
    #  - History mode with a fixed-length preset (Week/2 days/24h/12h/6h): the buttons
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

    def _nav_shifts_window(self) -> bool:
        """True when the browse buttons should shift the whole time
        window rather than step through individual files: only for the
        History plots (which show the whole window at once) under a
        fixed-length preset. The per-file Profile plots (wind_profile,
        rhi_profile) always step file by file within the loaded window,
        whatever preset is active."""
        return (self._interval_active() and
                self._plot_kind() not in ('wind_profile', 'rhi_profile'))

    def _go_first(self) -> None:
        if self._nav_shifts_window():
            self._window_to_data_edge('start')
            return
        if not self.current_files:
            return
        self.current_index = 0
        self._draw_current()

    def _go_back(self) -> None:
        if self._nav_shifts_window():
            self._step_time_window(-1)
            return
        if not self.current_files:
            return
        self.current_index = max(0, self.current_index - 1)
        self._draw_current()

    def _go_forward(self) -> None:
        if self._nav_shifts_window():
            self._step_time_window(1)
            return
        if not self.current_files:
            return
        self.current_index = min(len(self.current_files) - 1,
                                  self.current_index + 1)
        self._draw_current()

    def _go_last(self) -> None:
        if self._nav_shifts_window():
            self._window_to_data_edge('end')
            return
        if not self.current_files:
            return
        self.current_index = len(self.current_files) - 1
        self._draw_current()

    # ------------------------------------------------------------------
    # drawing -- dispatch
    # ------------------------------------------------------------------

    def _draw_current(self) -> None:
        """Load (from disk, if needed) and render whatever the current
        plot kind is."""
        self._applied_filter = self._intensity_min()
        plot_kind = self._plot_kind()
        if plot_kind == 'wind_profile':
            self._draw_profile()
        elif plot_kind == 'wind_timeseries':
            self._draw_timeseries()
        elif plot_kind == 'scan_history':
            self._draw_history()
        elif plot_kind == 'rhi_profile':
            self._draw_rhi_profile()

    def _render_current(self) -> None:
        """Redraw the plot kind that's actually built (:attr:`current_plot_kind`)
        from its already-loaded data, without touching disk -- used by
        the range controls after a stepper click or Auto toggle."""
        plot_kind = self.current_plot_kind
        if plot_kind == 'wind_profile':
            self._render_profile()
        elif plot_kind == 'wind_timeseries':
            self._render_timeseries()
        elif plot_kind == 'scan_history':
            self._render_history()
        elif plot_kind == 'rhi_profile':
            self._render_rhi_profile()

    def _resolve_range(self, ctrl: _RangeControl,
                        natural: Tuple[float, float]) -> Tuple[float, float]:
        """Distance/Speed always resolve to an explicit numeric range
        to pass into the plotting functions, whether Auto or manual:
        Auto means the range comes from the data (``natural``, also
        mirrored read-only into the control's fields); manual means it
        comes from the control's own fields. (Height instead passes
        ``None`` in Auto mode and reads the result back from the axes
        after rendering -- see :meth:`_sync_height_fields_from_axes` --
        since matplotlib's own autoscaling already does that job for a
        y axis; Distance/Speed have no such built-in autoscale to hook
        into for a colour range or a scatter's x axis.)"""
        if ctrl.auto_var.get():
            ctrl.set_display_range(*natural)
            return natural
        rng = ctrl.display_range()
        if rng is None or rng[1] <= rng[0]:
            return natural
        return rng

    # ------------------------------------------------------------------
    # drawing -- wind_profile (Processed_Wind_Profile, Profile mode)
    # ------------------------------------------------------------------

    def _compute_profile_limits(self) -> None:
        """Establish fixed speed/height axis limits from a sample of
        the current file selection, so stepping through files with
        First/Back/Forward/Last never resizes the panels."""
        sample = _sample(self.current_files, _MAX_FILES_FOR_LIMITS)
        intensity_min = self._intensity_min()
        speed_max = 1.0
        h_min, h_max = None, None
        for entry in sample:
            try:
                prof = _data.load_profile(
                    entry.path, intensity_min=intensity_min)
            except (IOError, ValueError):
                continue
            if prof.speed.size and np.any(np.isfinite(prof.speed)):
                speed_max = max(speed_max, float(np.nanmax(prof.speed)))
            if prof.height.size:
                lo = float(prof.height.min())
                hi = float(prof.height.max())
                h_min = lo if h_min is None else min(h_min, lo)
                h_max = hi if h_max is None else max(h_max, hi)

        natural_speed = (0.0, min(speed_max * 1.1, plotting.MAX_AUTOSCALE_SPEED))
        self._speed_range = self._resolve_range(self.speed_ctrl, natural_speed)

        if self.height_ctrl.auto_var.get():
            self._height_ylim = _pad_range(h_min, h_max) if h_min is not None else None

    def _draw_profile(self) -> None:
        """Load the current file and render it. Loading is the
        expensive part; :meth:`_render_profile` (called from here and
        from the range controls) is the cheap redraw-only path."""
        if not self.current_files:
            return
        if self.axes is None or self.current_plot_kind != 'wind_profile':
            self._set_mode_figure('wind_profile')
        entry = self.current_files[self.current_index]
        try:
            self._last_profile = _data.load_profile(
                entry.path, intensity_min=self._applied_filter)
        except (IOError, ValueError) as exc:
            self._show_message(f'Could not read {entry.path.name}:\n{exc}')
            return
        self._render_profile()
        missing = ([self._last_profile.timestamp]
                   if self._last_profile.filter_missing else [])
        self.status_var.set(
            f'{entry.path.name}\n'
            f'File {self.current_index + 1} of {len(self.current_files)}'
            + self._filter_status(missing))

    def _render_profile(self) -> None:
        """Redraw the profile plot from :attr:`_last_profile` and the
        current axis limits, without touching disk."""
        prof = self._last_profile
        if prof is None or self.axes is None:
            return
        ax_speed, ax_dir = self.axes
        plotting.plot_wind_profile(
            ax_speed, ax_dir, prof.height, prof.speed, prof.direction,
            speed_xlim=self._speed_range, height_ylim=self._height_ylim,
            title=self._title(
                f'{self.current_kind}  {prof.timestamp:%Y-%m-%d %H:%M:%S}'))
        self.canvas.draw_idle()
        self._sync_height_fields_from_axes()

    # ------------------------------------------------------------------
    # drawing -- wind_timeseries (Processed_Wind_Profile, History mode)
    # ------------------------------------------------------------------

    def _compute_series_speed_range(self) -> None:
        series = self._last_series
        natural = (0.0, 1.0)
        if series is not None and series.speed.size and np.any(np.isfinite(series.speed)):
            vmax = float(np.nanmax(series.speed)) * 1.1
            natural = (0.0, min(max(vmax, 1.0), plotting.MAX_AUTOSCALE_SPEED))
        self._speed_range = self._resolve_range(self.speed_ctrl, natural)

    def _draw_timeseries(self) -> None:
        """Load the current file selection and render it; see
        :meth:`_draw_profile` for the load/render split."""
        if not self.current_files:
            return
        if self.axes is None or self.current_plot_kind != 'wind_timeseries':
            self._set_mode_figure('wind_timeseries')
        files = self.current_files
        if len(files) > _MAX_FILES_FOR_TIMESERIES:
            files = _sample(files, _MAX_FILES_FOR_TIMESERIES)
        try:
            self._last_series = _data.load_profile_series(
                (e.path for e in files), intensity_min=self._applied_filter)
        except (IOError, ValueError) as exc:
            self._show_message(f'Could not build history:\n{exc}')
            return
        self._compute_series_speed_range()
        self._render_timeseries()
        self.status_var.set(
            f'{len(files)} file(s) in the selected range.'
            + self._filter_status(self._last_series.filter_missing))

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
            height_ylim=self._height_ylim, speed_vlim=self._speed_range,
            title=self._title(title))
        self.canvas.draw_idle()
        self._sync_height_fields_from_axes()

    # ------------------------------------------------------------------
    # drawing -- scan_history (VAD/Stare/Wind_Profile/RHI, History mode)
    # ------------------------------------------------------------------

    def _draw_history(self) -> None:
        """Load the current file selection and render it as a raw
        intensity/beta scan history; see :meth:`_draw_profile` for the
        load/render split."""
        if not self.current_files:
            return
        if self.axes is None or self.current_plot_kind != 'scan_history':
            self._set_mode_figure('scan_history')
        files = self.current_files
        if len(files) > _MAX_FILES_FOR_TIMESERIES:
            files = _sample(files, _MAX_FILES_FOR_TIMESERIES)
        try:
            self._last_history = _data.load_scan_history(
                (e.path for e in files), intensity_min=self._applied_filter)
        except (IOError, ValueError) as exc:
            self._show_message(f'Could not build history:\n{exc}')
            return
        self._render_history()
        self.status_var.set(f'{len(files)} file(s) in the selected range.')

    def _render_history(self) -> None:
        """Redraw the scan history plot from :attr:`_last_history` and
        the current axis limits, without touching disk."""
        hist = self._last_history
        if hist is None or self.axes is None:
            return
        ax_int, ax_beta, cax_int, cax_beta = self.axes
        title = self.current_kind
        if len(hist.times):
            title = (f'{self.current_kind}  '
                      f'{hist.times[0]:%Y-%m-%d %H:%M} – '
                      f'{hist.times[-1]:%Y-%m-%d %H:%M}')
        plotting.plot_scan_history(
            ax_int, ax_beta, cax_int, cax_beta,
            hist.times, hist.distance, hist.intensity, hist.beta,
            distance_ylim=self._height_ylim, title=self._title(title))
        self.canvas.draw_idle()
        self._sync_height_fields_from_axes()

    # ------------------------------------------------------------------
    # drawing -- rhi_profile (RHI, Profile mode)
    # ------------------------------------------------------------------

    def _compute_rhi_profile_limits(self) -> None:
        """Establish fixed distance/height/speed axis limits from a
        sample of the current file selection, mirroring
        :meth:`_compute_profile_limits`."""
        sample = _sample(self.current_files, _MAX_FILES_FOR_LIMITS)
        intensity_min = self._intensity_min()
        d_min, d_max = None, None
        h_min, h_max = None, None
        v_max = 1.0
        for entry in sample:
            try:
                cross = _data.load_rhi_cross_section(
                    entry.path, intensity_min=intensity_min)
            except (IOError, ValueError):
                continue
            if cross.distance.size:
                lo = float(np.nanmin(cross.distance))
                hi = float(np.nanmax(cross.distance))
                d_min = lo if d_min is None else min(d_min, lo)
                d_max = hi if d_max is None else max(d_max, hi)
            if cross.height.size:
                lo = float(np.nanmin(cross.height))
                hi = float(np.nanmax(cross.height))
                h_min = lo if h_min is None else min(h_min, lo)
                h_max = hi if h_max is None else max(h_max, hi)
            if cross.velocity.size and np.any(np.isfinite(cross.velocity)):
                v_max = max(v_max, float(np.nanmax(np.abs(cross.velocity))))

        natural_distance = _pad_range(d_min, d_max, default=(0.0, 1000.0))
        self._distance_xlim = self._resolve_range(self.distance_ctrl, natural_distance)

        vmax_capped = min(v_max * 1.1, plotting.MAX_AUTOSCALE_SPEED)
        natural_speed = (-vmax_capped, vmax_capped)
        self._speed_range = self._resolve_range(self.speed_ctrl, natural_speed)

        if self.height_ctrl.auto_var.get():
            self._height_ylim = _pad_range(h_min, h_max) if h_min is not None else None

    def _draw_rhi_profile(self) -> None:
        """Load the current file and render it as a distance/height
        cross section; see :meth:`_draw_profile` for the load/render
        split."""
        if not self.current_files:
            return
        if self.axes is None or self.current_plot_kind != 'rhi_profile':
            self._set_mode_figure('rhi_profile')
        entry = self.current_files[self.current_index]
        try:
            self._last_rhi = _data.load_rhi_cross_section(
                entry.path, intensity_min=self._applied_filter)
        except (IOError, ValueError) as exc:
            self._show_message(f'Could not read {entry.path.name}:\n{exc}')
            return
        self._render_rhi_profile()
        self.status_var.set(
            f'{entry.path.name}\n'
            f'File {self.current_index + 1} of {len(self.current_files)}')

    def _render_rhi_profile(self) -> None:
        """Redraw the RHI cross section from :attr:`_last_rhi` and the
        current axis limits, without touching disk."""
        cross = self._last_rhi
        if cross is None or self.axes is None:
            return
        ax_vel, ax_beta, cax_vel, cax_beta = self.axes
        plotting.plot_rhi_cross_section(
            ax_vel, ax_beta, cax_vel, cax_beta,
            cross.distance, cross.height, cross.velocity, cross.beta,
            distance_xlim=self._distance_xlim, height_ylim=self._height_ylim,
            speed_vlim=self._speed_range,
            title=self._title(
                f'{self.current_kind}  {cross.timestamp:%Y-%m-%d %H:%M:%S}'))
        self.canvas.draw_idle()
        self._sync_height_fields_from_axes()

    # ------------------------------------------------------------------
    # Height range
    # ------------------------------------------------------------------

    def _current_height_ylim(self):
        if self.axes is None:
            return None
        return self.axes[0].get_ylim()

    def _sync_height_fields_from_axes(self) -> None:
        """In Auto mode, mirror the plot's actual (autoscaled) height
        limits into the -- disabled, display-only -- Bottom/Top fields,
        so they always show real numbers rather than stale ones."""
        if not self.height_ctrl.auto_var.get() or self.axes is None:
            return
        lo, hi = self.axes[0].get_ylim()
        self.height_ctrl.set_display_range(lo, hi)

    def _on_height_auto_toggle(self) -> None:
        auto = self.height_ctrl.auto_var.get()
        if auto:
            self._height_ylim = None
            if self.current_files:
                if self.current_plot_kind == 'wind_profile':
                    self._compute_profile_limits()
                elif self.current_plot_kind == 'rhi_profile':
                    self._compute_rhi_profile_limits()
                self._render_current()
        else:
            # seed the now-editable fields: bottom defaults to ground
            # level, top defaults to whatever Auto was just showing
            lo, hi = self._current_height_ylim() or (0.0, 100.0)
            self.height_ctrl.set_display_range(0.0, hi)
            self._redraw_height_range()

    def _redraw_height_range(self) -> None:
        """Apply a manually-entered height range to the current plot.
        Cheap: redraws from the last-loaded data (already in memory)
        rather than reloading from disk, so the stepper buttons and
        typed edits feel immediate."""
        if self.height_ctrl.auto_var.get() or self.axes is None:
            return
        rng = self.height_ctrl.display_range()
        if rng is None or rng[1] <= rng[0]:
            return
        self._height_ylim = rng
        self._render_current()

    # ------------------------------------------------------------------
    # Distance range (RHI Profile only)
    # ------------------------------------------------------------------

    def _on_distance_auto_toggle(self) -> None:
        auto = self.distance_ctrl.auto_var.get()
        if auto:
            if self.current_files and self.current_plot_kind == 'rhi_profile':
                self._compute_rhi_profile_limits()
                self._render_current()
        else:
            lo, hi = self._distance_xlim or (0.0, 1000.0)
            self.distance_ctrl.set_display_range(lo, hi)
            self._redraw_distance_range()

    def _redraw_distance_range(self) -> None:
        if self.distance_ctrl.auto_var.get() or self.axes is None:
            return
        rng = self.distance_ctrl.display_range()
        if rng is None or rng[1] <= rng[0]:
            return
        self._distance_xlim = rng
        self._render_current()

    # ------------------------------------------------------------------
    # Speed range (wind_profile, wind_timeseries, rhi_profile)
    # ------------------------------------------------------------------

    def _on_speed_auto_toggle(self) -> None:
        auto = self.speed_ctrl.auto_var.get()
        if auto:
            if self.current_files:
                if self.current_plot_kind == 'wind_profile':
                    self._compute_profile_limits()
                elif self.current_plot_kind == 'wind_timeseries':
                    self._compute_series_speed_range()
                elif self.current_plot_kind == 'rhi_profile':
                    self._compute_rhi_profile_limits()
                self._render_current()
        else:
            lo, hi = self._speed_range or (0.0, 25.0)
            self.speed_ctrl.set_display_range(lo, hi)
            self._redraw_speed_range()

    def _redraw_speed_range(self) -> None:
        if self.speed_ctrl.auto_var.get() or self.axes is None:
            return
        rng = self.speed_ctrl.display_range()
        if rng is None or rng[1] <= rng[0]:
            return
        self._speed_range = rng
        self._render_current()


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    initial_dir = argv[0] if argv else None
    root = tk.Tk()
    HaloViewerApp(root, initial_dir=initial_dir)
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
