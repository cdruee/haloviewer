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

        # -- time range ---------------------------------------------------
        ttk.Label(parent, text='Start time').grid(row=row, column=0, sticky='w')
        row += 1
        self.start_var = tk.StringVar()
        start_entry = ttk.Entry(parent, textvariable=self.start_var)
        start_entry.grid(row=row, column=0, sticky='we')
        start_entry.bind('<Return>', lambda e: self._apply_range())
        row += 1
        ttk.Label(parent, text='End time').grid(row=row, column=0, sticky='w')
        row += 1
        self.end_var = tk.StringVar()
        end_entry = ttk.Entry(parent, textvariable=self.end_var)
        end_entry.grid(row=row, column=0, sticky='we')
        end_entry.bind('<Return>', lambda e: self._apply_range())
        row += 1
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

        self._refresh_mode()

    def _on_mode_select(self) -> None:
        self._refresh_mode()

    def _refresh_mode(self) -> None:
        mode = self.mode_var.get()
        info = get_kind_info(self.current_kind) if self.current_kind else None

        if info is None or not info.modes:
            for b in (self.btn_first, self.btn_back, self.btn_fwd, self.btn_last):
                b.configure(state='disabled')
            if self.current_mode != PROFILE_MODE:
                self._set_mode_figure(PROFILE_MODE)
            message = ('No plottable data selected.' if info is None else
                       f'Plotting for "{self.current_kind}" is not '
                       f'implemented yet.')
            self._show_message(message)
            self.status_var.set(message)
            return

        nav_state = 'normal' if mode == PROFILE_MODE else 'disabled'
        for b in (self.btn_first, self.btn_back, self.btn_fwd, self.btn_last):
            b.configure(state=nav_state)

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

    def _apply_range(self) -> None:
        if not self.current_kind or self.scan_result is None:
            return
        start = self._parse_time(self.start_var.get())
        end = self._parse_time(self.end_var.get())
        self.current_files = self.scan_result.files_in_range(
            self.current_kind, start, end)
        self.current_index = 0
        self._speed_xlim = None
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

    def _go_first(self) -> None:
        self.current_index = 0
        self._draw_profile()

    def _go_back(self) -> None:
        self.current_index = max(0, self.current_index - 1)
        self._draw_profile()

    def _go_forward(self) -> None:
        self.current_index = min(len(self.current_files) - 1,
                                  self.current_index + 1)
        self._draw_profile()

    def _go_last(self) -> None:
        self.current_index = len(self.current_files) - 1
        self._draw_profile()

    # ------------------------------------------------------------------
    # drawing
    # ------------------------------------------------------------------

    def _compute_profile_limits(self) -> None:
        """Establish fixed speed/height axis limits from a sample of
        the current file selection, so stepping through files with
        First/Back/Forward/Last never resizes the panels."""
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
        if h_min is not None:
            pad = (h_max - h_min) * 0.03 if h_max > h_min else 1.0
            self._height_ylim = (h_min - pad, h_max + pad)

    def _draw_profile(self) -> None:
        if not self.current_files:
            return
        if self.axes is None or self.current_mode != PROFILE_MODE:
            self._set_mode_figure(PROFILE_MODE)
        entry = self.current_files[self.current_index]
        try:
            prof = _data.load_profile(entry.path)
        except (IOError, ValueError) as exc:
            self._show_message(f'Could not read {entry.path.name}:\n{exc}')
            return
        ax_speed, ax_dir = self.axes
        plotting.plot_wind_profile(
            ax_speed, ax_dir, prof.height, prof.speed, prof.direction,
            speed_xlim=self._speed_xlim, height_ylim=self._height_ylim,
            title=f'{self.current_kind}  {prof.timestamp:%Y-%m-%d %H:%M:%S}')
        self.canvas.draw_idle()
        self.status_var.set(
            f'{entry.path.name}\n'
            f'File {self.current_index + 1} of {len(self.current_files)}')

    def _draw_timeseries(self) -> None:
        if not self.current_files:
            return
        if self.axes is None or self.current_mode != TIMESERIES_MODE:
            self._set_mode_figure(TIMESERIES_MODE)
        files = self.current_files
        if len(files) > _MAX_FILES_FOR_TIMESERIES:
            files = _sample(files, _MAX_FILES_FOR_TIMESERIES)
        try:
            series = _data.load_profile_series(e.path for e in files)
        except (IOError, ValueError) as exc:
            self._show_message(f'Could not build time series:\n{exc}')
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
            title=title)
        self.canvas.draw_idle()
        self.status_var.set(f'{len(files)} file(s) in the selected range.')


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    initial_dir = argv[0] if argv else None
    root = tk.Tk()
    WindLidarViewerApp(root, initial_dir=initial_dir)
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
