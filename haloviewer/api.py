# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5
"""
Programmatic entry points for plotting Halo wind lidar files,
independent of both the CLI (:mod:`haloviewer.cli`) and the GUI
(:mod:`haloviewer.gui`). Use these directly from a script or
notebook::

    from haloviewer import plot
    fig = plot("Proc/2026/202609/20260919", kind="RHI", mode="history",
               start="24h", end="2026-09-19 12:00", output="rhi.png")

Three entry points, from highest- to lowest-level:

:func:`plot`
    Resolves ``path`` (a file, a directory searched recursively, or a
    glob pattern -- or a mix of those), picks the file kind and time
    range, and plots it. This is what :mod:`haloviewer.cli` calls
    and is the right starting point for most scripting.
:func:`plot_file`
    Plots one already-known file.
:func:`plot_files`
    Plots several already-known files of the same kind together (a
    History) -- or, for a single-scan mode (``"profile"``, ``"rhi"``,
    ``"ppi"``), that one scan if exactly one path is given.
"""

from __future__ import annotations

import contextlib
import glob as _glob
import math
import os
import re
import warnings
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple, Union

import pandas as pd
if os.environ.get('BUILDING_SPHINX', 'false') == 'false':
    import matplotlib as mpl
    import matplotlib.dates as mdates
    from matplotlib.figure import Figure
else:
    mpl = mdates = Figure = None

from . import data as _data
from . import plotting
from .scan import (PPI_MODE, PROFILE_MODE, RHI_MODE, SINGLE_SCAN_MODES,
                    TIMESERIES_MODE, get_kind_info, parse_filename)

__all__ = ['plot', 'plot_file', 'plot_files']

PathLike = Union[str, Path]

#: Default figure size when none is given: A4 landscape, in inches
#: (210mm x 297mm). Used by :func:`plot_file`/:func:`plot_files`
#: (and so, transitively, :func:`plot`) -- not by the GUI, which sizes
#: its embedded canvas from the window instead.
DEFAULT_FIGSIZE: Tuple[float, float] = (11.69, 8.27)

#: Base font size (points), for all text in the figure -- title, axis
#: labels, ticks, colorbar labels -- at :data:`DEFAULT_FIGSIZE` (A4
#: landscape). This is the anchor :func:`_auto_fontsize` scales from;
#: it is not itself a fixed default (see there).
BASE_FONTSIZE_AT_A4: float = 16.0


def _auto_fontsize(figsize: Tuple[float, float]) -> float:
    """The default font size when ``fontsize`` isn't given explicitly:
    proportional to ``figsize``'s area, equal to
    :data:`BASE_FONTSIZE_AT_A4` at the default A4-landscape size
    (:data:`DEFAULT_FIGSIZE`) and scaling smoothly for any other size
    -- e.g. a figure with half the area gets a ~30% smaller base font,
    the same way print typography scales text with the page rather
    than pinning it to one absolute size regardless of how big the
    page is."""
    a4_w, a4_h = DEFAULT_FIGSIZE
    w, h = figsize
    scale = math.sqrt((w * h) / (a4_w * a4_h))
    return BASE_FONTSIZE_AT_A4 * scale


def _resolve_kind(path: PathLike) -> str:
    parsed = parse_filename(path)
    if parsed is None:
        raise ValueError(
            f'{path!r} does not look like a Halo-style ".hpl" filename '
            f'(expected "<Type words>_<system id>_<yyyymmdd>_<hhmmss>.hpl")')
    return parsed.kind


def _check_supported(kind: str, mode: str) -> None:
    info = get_kind_info(kind)
    if not info.supported:
        raise NotImplementedError(
            f'plotting is not yet implemented for file kind {kind!r}')
    if mode not in info.modes:
        raise ValueError(
            f'kind {kind!r} does not support mode {mode!r}; '
            f'available modes: {list(info.modes)}')


#: Kinds with no instrument-processed profile of their own: History
#: mode comes from the raw per-gate intensity/beta instead of a
#: derived wind speed/direction (see :func:`~haloviewer.data.load_scan_history`).
_SCAN_HISTORY_KINDS = {'VAD', 'Stare', 'Wind_Profile', 'RHI'}


def _classify(kind: str, mode: str) -> str:
    """Which of the five load/render pipelines a (kind, mode) pair maps
    to -- the same classification as
    :meth:`haloviewer.gui.HaloViewerApp._plot_kind`, but as a
    pure function with no GUI state, used here only to decide whether
    ``height``/``distance``/``speed``/``fill`` apply (see
    :func:`_warn_if_inapplicable`)."""
    if kind == 'Processed_Wind_Profile':
        return 'wind_profile' if mode == PROFILE_MODE else 'wind_timeseries'
    if mode == RHI_MODE:
        return 'rhi'
    if mode == PPI_MODE:
        return 'ppi'
    return 'scan_history'


#: `height` is the vertical axis of everything except the PPI view
#: (a horizontal plane has no height axis).
_HEIGHT_APPLICABLE = {'wind_profile', 'wind_timeseries', 'scan_history',
                      'rhi'}
#: `distance` is the horizontal axis of the RHI/PPI views only.
_DISTANCE_APPLICABLE = {'rhi', 'ppi'}
#: `speed` means a wind-speed or radial-velocity axis/colour range;
#: the raw scan kinds' History image has no such dimension (its panels
#: are intensity and beta).
_SPEED_APPLICABLE = {'wind_profile', 'wind_timeseries', 'rhi', 'ppi'}
#: `fill` (nearest-neighbour fill between points) only applies to the
#: scattered-point RHI/PPI views.
_FILL_APPLICABLE = {'rhi', 'ppi'}


def _warn_if_inapplicable(kind: str, mode: str, *, height=None,
                           distance=None, speed=None, fill=False) -> None:
    plot_kind = _classify(kind, mode)
    if height is not None and plot_kind not in _HEIGHT_APPLICABLE:
        warnings.warn(
            f'height= is not applicable to {kind!r} in {mode!r} mode '
            f'(a horizontal PPI plane has no height axis) -- ignoring it',
            stacklevel=3)
    if distance is not None and plot_kind not in _DISTANCE_APPLICABLE:
        warnings.warn(
            f'distance= is not applicable to {kind!r} in {mode!r} mode '
            f'(only the "rhi" and "ppi" modes have a distance axis) -- '
            f'ignoring it', stacklevel=3)
    if speed is not None and plot_kind not in _SPEED_APPLICABLE:
        warnings.warn(
            f'speed= is not applicable to {kind!r} in {mode!r} mode '
            f'(its panels have no speed/velocity dimension) -- ignoring it',
            stacklevel=3)
    if fill and plot_kind not in _FILL_APPLICABLE:
        warnings.warn(
            f'fill= is not applicable to {kind!r} in {mode!r} mode '
            f'(only the "rhi" and "ppi" modes draw scattered points) -- '
            f'ignoring it', stacklevel=3)


@contextlib.contextmanager
def _font_context(fontsize: Optional[float]):
    """Apply ``fontsize`` as the base font size for every text artist
    created inside the block (title, axis labels, ticks, colorbar
    labels), via matplotlib's rcParams -- this works whether the
    figure came from :mod:`matplotlib.pyplot` or a bare ``Figure()``,
    since text objects capture their size from rcParams at creation
    time. A no-op if ``fontsize`` is ``None``."""
    if fontsize is None:
        yield
    else:
        with mpl.rc_context({'font.size': fontsize}):
            yield


FilterArg = Union[None, bool, float, str]


def _filter_title(title: str, intensity_min: Optional[float]) -> str:
    """Append a note on the active intensity filter to a plot title."""
    if intensity_min is None:
        return title
    return f'{title}  (intensity < {intensity_min:g} removed)'


def _warn_filter_missing(missing, stacklevel: int = 3) -> None:
    """Warn once if the intensity filter could not be applied to some
    ``Processed_Wind_Profile`` files (no matching ``Wind_Profile``)."""
    n = len(missing)
    if n:
        warnings.warn(
            f'intensity filter not applied to {n} Processed_Wind_Profile '
            f'file(s): no matching Wind_Profile file with the same '
            f'timestamp found', stacklevel=stacklevel)


def _ppi_distance_max(distance: Optional[Tuple[float, float]]
                      ) -> Optional[float]:
    """The PPI view only uses the far end of ``distance`` (both axes
    span ``[-max, +max]``); the near end is fixed at 0."""
    if distance is None:
        return None
    return float(max(distance))


def plot_file(path: PathLike, *,
              mode: Optional[str] = None,
              height: Optional[Tuple[float, float]] = None,
              distance: Optional[Tuple[float, float]] = None,
              speed: Optional[Tuple[float, float]] = None,
              output: Optional[PathLike] = None,
              show: bool = False,
              figsize: Optional[Tuple[float, float]] = None,
              fontsize: Optional[float] = None,
              filter: FilterArg = None,
              fill: bool = False) -> Figure:
    """
    Plot a single Halo wind lidar file.

    :param path: path to a ``.hpl`` file.
    :param mode: ``"profile"``, ``"timeseries"`` (shown in the GUI as \
        "History"), ``"rhi"`` or ``"ppi"``. Defaults to the first mode \
        the file's kind supports. ``"profile"`` (a height/speed/ \
        direction profile) is only available for \
        ``Processed_Wind_Profile``; ``"rhi"`` (the scan's points \
        projected onto the vertical x/z plane, x along the first ray's \
        azimuth) and ``"ppi"`` (projected onto the horizontal x/y \
        plane) only for the regular scan kinds (VAD, Stare, \
        Wind_Profile, RHI). A single file plotted in \
        ``"timeseries"``/History mode produces a one-column (or, for \
        the scan kinds, one-file's-worth-of-rays) image; use \
        :func:`plot_files` to combine several files into a real \
        history.
    :param height: fixed ``(min, max)`` for the vertical axis (height, \
        or gate-inferred distance for the raw scan kinds' History); \
        ``None`` autoscales. Not applicable to ``"ppi"`` -- a warning \
        is issued (and the value ignored) there.
    :param distance: fixed ``(min, max)`` for the horizontal distance \
        axis of the ``"rhi"`` view; for ``"ppi"`` only the max is used \
        (both axes then span ``[-max, +max]``). A warning is issued \
        (and the value ignored) for the other modes.
    :param speed: fixed ``(min, max)`` for the wind-speed/radial- \
        velocity axis or colour range; not applicable to the raw scan \
        kinds' "timeseries"/History image -- a warning is issued (and \
        the value ignored) there.
    :param output: if given, save the figure to this path (format \
        inferred from the extension, e.g. ``.png``, ``.pdf``).
    :param show: if ``True``, display the figure in an interactive \
        window (blocks until closed). Requires an interactive \
        matplotlib backend to be available.
    :param figsize: figure size in inches; defaults to \
        :data:`DEFAULT_FIGSIZE` (A4 landscape) if omitted.
    :param fontsize: base font size for all text in the figure; if \
        omitted, scales proportionally with ``figsize`` (see \
        :func:`_auto_fontsize`), working out to \
        :data:`BASE_FONTSIZE_AT_A4` (16) at the default A4-landscape \
        size.
    :param filter: optional intensity filter. ``None``/``False`` (the \
        default) plots everything; ``True`` uses \
        :data:`~haloviewer.data.DEFAULT_INTENSITY_FILTER` (1.018); a \
        number uses that threshold. Data points whose intensity \
        (SNR + 1) is below the threshold are shown as ``nan`` (blank) \
        -- intensity, beta and radial velocity for the scan kinds; \
        wind speed and direction for ``Processed_Wind_Profile``, \
        judged by the intensity of the ``Wind_Profile`` scan file with \
        the same timestamp (a warning is issued for profiles that have \
        none, which are then left unfiltered).
    :param fill: ``"rhi"``/``"ppi"`` only: fill the area between the \
        data points by nearest-neighbour interpolation instead of \
        drawing individual points (needs :mod:`scipy`).
    :returns: the :class:`~matplotlib.figure.Figure` that was drawn.
    """
    path = Path(path)
    intensity_min = _data.resolve_intensity_filter(filter)
    kind = _resolve_kind(path)
    if mode is None:
        info = get_kind_info(kind)
        if not info.modes:
            raise NotImplementedError(
                f'plotting is not yet implemented for file kind {kind!r}')
        mode = info.modes[0]
    _check_supported(kind, mode)
    _warn_if_inapplicable(kind, mode, height=height, distance=distance,
                          speed=speed, fill=fill)

    figsize = figsize or DEFAULT_FIGSIZE

    if mode == TIMESERIES_MODE:
        return plot_files([path], mode=mode, height=height,
                           distance=distance, speed=speed, output=output,
                           show=show, figsize=figsize, fontsize=fontsize,
                           filter=intensity_min, fill=fill)

    fig = _new_figure(show, figsize)
    resolved_fontsize = fontsize if fontsize is not None else _auto_fontsize(figsize)
    with _font_context(resolved_fontsize):
        if mode == PROFILE_MODE and kind == 'Processed_Wind_Profile':
            prof = _data.load_profile(path, intensity_min=intensity_min)
            if prof.filter_missing:
                _warn_filter_missing([prof.timestamp])
            fig, (ax_speed, ax_dir) = plotting.create_profile_figure(
                figsize=figsize, fig=fig)
            plotting.plot_wind_profile(
                ax_speed, ax_dir, prof.height, prof.speed, prof.direction,
                speed_xlim=speed, height_ylim=height,
                title=_filter_title(
                    f'{kind}  {prof.timestamp:%Y-%m-%d %H:%M:%S}',
                    intensity_min))
        elif mode in (RHI_MODE, PPI_MODE):
            pts = _data.load_scan_points(path, intensity_min=intensity_min)
            fig, axes = plotting.create_scan_pair_figure(
                figsize=figsize, fig=fig)
            title = _filter_title(
                f'{plotting.scan_view_title(kind, mode)}  '
                f'{pts.timestamp:%Y-%m-%d %H:%M:%S}', intensity_min)
            if mode == RHI_MODE:
                plotting.plot_rhi(
                    *axes, pts.x, pts.z, pts.velocity, pts.beta,
                    distance_xlim=distance, height_ylim=height,
                    speed_vlim=speed, fill=fill, azimuth0=pts.azimuth0,
                    title=title)
            else:
                plotting.plot_ppi(
                    *axes, pts.x, pts.y, pts.velocity, pts.beta,
                    distance_max=_ppi_distance_max(distance),
                    speed_vlim=speed, fill=fill, azimuth0=pts.azimuth0,
                    title=title)
        else:
            raise ValueError(f'mode {mode!r} is not supported for kind {kind!r}')

    _finish(fig, output, show)
    return fig


def plot_files(paths: Iterable[PathLike], *,
                mode: str = TIMESERIES_MODE,
                height: Optional[Tuple[float, float]] = None,
                distance: Optional[Tuple[float, float]] = None,
                speed: Optional[Tuple[float, float]] = None,
                output: Optional[PathLike] = None,
                show: bool = False,
                figsize: Optional[Tuple[float, float]] = None,
                fontsize: Optional[float] = None,
                filter: FilterArg = None,
                fill: bool = False,
                fig: Optional[Figure] = None) -> Figure:
    """
    Plot several Halo wind lidar files of the same kind together as a
    History: height/time (speed/direction as colour) for
    ``Processed_Wind_Profile``, or distance/time (intensity/beta as
    colour) for VAD, Stare, Wind_Profile or RHI.

    :param paths: paths to ``.hpl`` files, all of the same kind.
    :param mode: only ``"timeseries"``/History is meaningful here for \
        multiple files; a single-scan mode (``"profile"``, ``"rhi"``, \
        ``"ppi"``) is routed to :func:`plot_file` if exactly one path \
        is given.
    :param height: see :func:`plot_file`.
    :param distance: see :func:`plot_file`.
    :param speed: see :func:`plot_file`.
    :param output: if given, save the figure to this path.
    :param show: if ``True``, display the figure interactively.
    :param figsize: figure size in inches; defaults to \
        :data:`DEFAULT_FIGSIZE` (A4 landscape) if omitted.
    :param fontsize: base font size for all text in the figure; see \
        :func:`plot_file`.
    :param filter: optional intensity filter (``None``, ``True`` or a \
        threshold); see :func:`plot_file`.
    :param fill: see :func:`plot_file` (single-scan RHI/PPI only).
    :param fig: internal use (an already-created figure to draw into).
    :returns: the :class:`~matplotlib.figure.Figure` that was drawn.
    """
    paths = [Path(p) for p in paths]
    if not paths:
        raise ValueError('no files given')
    intensity_min = _data.resolve_intensity_filter(filter)
    kinds = {_resolve_kind(p) for p in paths}
    if len(kinds) > 1:
        raise ValueError(f'files must all be the same kind, got {kinds}')
    kind = kinds.pop()

    if mode in SINGLE_SCAN_MODES:
        if len(paths) != 1:
            raise ValueError(
                f'{mode!r} mode shows a single scan; pass exactly one '
                f'file (got {len(paths)})')
        return plot_file(paths[0], mode=mode, height=height,
                          distance=distance, speed=speed, output=output,
                          show=show, figsize=figsize, fontsize=fontsize,
                          filter=intensity_min, fill=fill)

    _check_supported(kind, mode)
    _warn_if_inapplicable(kind, mode, height=height, distance=distance,
                          speed=speed, fill=fill)

    figsize = figsize or DEFAULT_FIGSIZE
    if fig is None:
        fig = _new_figure(show, figsize)

    resolved_fontsize = fontsize if fontsize is not None else _auto_fontsize(figsize)
    with _font_context(resolved_fontsize):
        if mode == TIMESERIES_MODE and kind == 'Processed_Wind_Profile':
            series = _data.load_profile_series(
                paths, intensity_min=intensity_min)
            _warn_filter_missing(series.filter_missing)
            fig, (ax_speed, ax_dir, cax_speed, cax_dir) = \
                plotting.create_timeseries_figure(figsize=figsize, fig=fig)
            title = kind
            if len(series.times):
                title = (f'{kind}  '
                          f'{series.times[0]:%Y-%m-%d %H:%M} – '
                          f'{series.times[-1]:%Y-%m-%d %H:%M}')
            plotting.plot_wind_timeseries(
                ax_speed, ax_dir, cax_speed, cax_dir,
                series.times, series.height, series.speed, series.direction,
                height_ylim=height, speed_vlim=speed,
                title=_filter_title(title, intensity_min))
        elif mode == TIMESERIES_MODE and kind in _SCAN_HISTORY_KINDS:
            hist = _data.load_scan_history(paths,
                                           intensity_min=intensity_min)
            fig, (ax_int, ax_beta, cax_int, cax_beta) = \
                plotting.create_timeseries_figure(figsize=figsize, fig=fig)
            title = kind
            if len(hist.times):
                title = (f'{kind}  '
                          f'{hist.times[0]:%Y-%m-%d %H:%M} – '
                          f'{hist.times[-1]:%Y-%m-%d %H:%M}')
            plotting.plot_scan_history(
                ax_int, ax_beta, cax_int, cax_beta,
                hist.times, hist.distance, hist.intensity, hist.beta,
                distance_ylim=height,
                title=_filter_title(title, intensity_min))
        else:
            raise ValueError(
                f'mode {mode!r} is not supported for multiple files of kind '
                f'{kind!r}')

    _finish(fig, output, show)
    return fig


# =========================================================================
# plot(): path resolution (file / directory / glob) + kind + time-range
# selection on top of plot_file/plot_files.
# =========================================================================

#: ``"##d"`` or ``"##h"`` (a plain number of days/hours, e.g. ``"24h"``
#: or ``"2.5d"``) -- a relative :data:`start` interval back from
#: :data:`end`, as opposed to an absolute timestamp.
_RELATIVE_INTERVAL_RE = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*([dh])\s*$',
                                    re.IGNORECASE)


def _is_glob_pattern(s: str) -> bool:
    return any(c in s for c in '*?[')


def _resolve_candidate_paths(path) -> List[Path]:
    """
    Expand ``path`` -- a single path/pattern, or an iterable of them --
    into a flat, de-duplicated list of existing file paths: a
    directory contributes every ``.hpl`` file recursively below it, a
    glob pattern (containing ``*``, ``?`` or ``[``) is expanded, and a
    plain existing file is used as-is. Unmatched patterns and missing
    files are warned about, not raised, so one typo in a list of
    several doesn't abort the whole call.
    """
    entries = [path] if isinstance(path, (str, Path)) else list(path)

    out: List[Path] = []
    seen = set()

    def _add(p: Path) -> None:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(p)

    for entry in entries:
        entry_str = str(entry)
        p = Path(entry_str)
        if p.is_dir():
            for hpl_path in sorted(p.rglob('*.hpl')):
                _add(hpl_path)
        elif _is_glob_pattern(entry_str):
            matches = sorted(_glob.glob(entry_str, recursive=True))
            if not matches:
                warnings.warn(f'no file matches {entry_str!r}', stacklevel=3)
            for m in matches:
                _add(Path(m))
        elif p.exists():
            _add(p)
        else:
            warnings.warn(f'{entry_str!r} does not exist', stacklevel=3)
    return out


def _normalize_mode(mode: Optional[str]) -> Optional[str]:
    if mode is None:
        return None
    m = str(mode).strip().lower()
    if m == 'profile':
        return PROFILE_MODE
    if m in ('history', 'timeseries'):
        return TIMESERIES_MODE
    if m == 'rhi':
        return RHI_MODE
    if m == 'ppi':
        return PPI_MODE
    raise ValueError(
        f'mode must be "profile", "history", "rhi" or "ppi", got {mode!r}')


def _default_single_scan_mode(kind: str) -> Optional[str]:
    """The mode :func:`plot` picks when exactly one file is in range:
    the kind's own single-scan view (``"profile"`` for
    ``Processed_Wind_Profile``, ``"rhi"`` for ``RHI``), or ``None`` for
    kinds that default to History even for one file."""
    info = get_kind_info(kind)
    if PROFILE_MODE in info.modes:
        return PROFILE_MODE
    if kind == 'RHI' and RHI_MODE in info.modes:
        return RHI_MODE
    return None


def plot(path: Union[PathLike, Iterable[PathLike]], *,
         kind: Optional[str] = None,
         mode: Optional[str] = None,
         start: Optional[Union[str, pd.Timestamp]] = None,
         end: Optional[Union[str, pd.Timestamp]] = None,
         height: Optional[Tuple[float, float]] = None,
         distance: Optional[Tuple[float, float]] = None,
         speed: Optional[Tuple[float, float]] = None,
         output: Optional[PathLike] = None,
         show: bool = False,
         figsize: Optional[Tuple[float, float]] = None,
         fontsize: Optional[float] = None,
         filter: FilterArg = None,
         fill: bool = False) -> Figure:
    """
    High-level entry point: resolve ``path``, pick the file kind and
    time range, and plot it. This is what :mod:`haloviewer.cli`
    (``haloplot``) calls; :func:`plot_file`/:func:`plot_files`
    remain available for callers that have already resolved an exact
    file or file list of one known kind.

    :param path: a file, a directory (searched recursively for \
        ``.hpl`` files), or a glob pattern (``*``, ``?``, ``[``) -- or \
        an iterable of any mix of those.
    :param kind: which file kind to plot (e.g. ``"RHI"``). Optional if \
        every file ``path`` resolves to is the same kind (inferred \
        from each filename); required if they span more than one kind.
    :param mode: ``"profile"``, ``"history"`` (``"timeseries"`` is \
        also accepted), ``"rhi"`` or ``"ppi"``. Defaults to the kind's \
        single-scan view (``"profile"`` for Processed_Wind_Profile, \
        ``"rhi"`` for RHI) if exactly one file falls in the resolved \
        time range, else ``"history"``. In a single-scan mode \
        (``"profile"``, ``"rhi"``, ``"ppi"``) with more than one file \
        in range, the single most recent one (at or before ``end``) \
        is plotted.
    :param start: start of the time range: an absolute timestamp \
        (e.g. ``"2026-09-21 00:00"``), or a relative interval back \
        from ``end`` such as ``"24h"`` or ``"2d"``. Omit for no lower \
        bound (every matching file up to ``end``).
    :param end: end of the time range, an absolute timestamp. Defaults \
        to the latest timestamp among the resolved (and kind-filtered) \
        files -- which, when exactly one such file was found, is just \
        that file's own timestamp.
    :param height: fixed ``(min, max)`` for the vertical axis; \
        ``None`` autoscales. Not applicable to ``"ppi"`` -- see \
        :func:`plot_file`.
    :param distance: fixed ``(min, max)`` for the horizontal distance \
        axis (``"rhi"``; only the max for ``"ppi"``; warns and is \
        ignored elsewhere) -- see :func:`plot_file`.
    :param speed: fixed ``(min, max)`` for the wind-speed/radial- \
        velocity axis or colour range (warns and is ignored for the \
        raw scan kinds' History image) -- see :func:`plot_file`.
    :param output: if given, save the figure to this path.
    :param show: if ``True``, display the figure interactively.
    :param figsize: figure size in inches; defaults to \
        :data:`DEFAULT_FIGSIZE` (A4 landscape).
    :param fontsize: base font size for all text in the figure; if \
        omitted, scales proportionally with ``figsize`` -- see \
        :func:`plot_file`.
    :param filter: optional intensity filter: ``None`` (default, off), \
        ``True`` (threshold \
        :data:`~haloviewer.data.DEFAULT_INTENSITY_FILTER` = 1.018) or a \
        threshold value; see :func:`plot_file`.
    :param fill: ``"rhi"``/``"ppi"`` only: nearest-neighbour fill \
        between the data points; see :func:`plot_file`.
    :returns: the :class:`~matplotlib.figure.Figure` that was drawn.
    :raises ValueError: if no files are found, if they span more than \
        one kind and ``kind`` wasn't given, if ``kind`` matches none \
        of them, or if none fall in the requested time range.
    """
    # validate early, before any file is touched
    intensity_min = _data.resolve_intensity_filter(filter)
    candidates = _resolve_candidate_paths(path)

    parsed = []
    for p in candidates:
        info = parse_filename(p)
        if info is None:
            warnings.warn(
                f'{p} does not look like a Halo-style ".hpl" filename -- '
                f'skipping it', stacklevel=2)
            continue
        parsed.append((p, info.kind, info.timestamp))
    if not parsed:
        raise ValueError(f'no .hpl files found for {path!r}')

    kinds_found = sorted({k for _, k, _ in parsed})
    if kind is None:
        if len(kinds_found) > 1:
            raise ValueError(
                f'{path!r} contains more than one file kind '
                f'{kinds_found} -- pass kind= (-k/--kind) to select one')
        kind = kinds_found[0]
    else:
        parsed = [(p, k, ts) for p, k, ts in parsed if k == kind]
        if not parsed:
            raise ValueError(
                f'no files of kind {kind!r} found among {kinds_found}')

    parsed.sort(key=lambda item: item[2])

    end_ts = pd.Timestamp(end) if end is not None else parsed[-1][2]

    start_ts = None
    if start is not None:
        m = _RELATIVE_INTERVAL_RE.match(str(start))
        if m:
            amount, unit = m.groups()
            delta = (pd.Timedelta(days=float(amount)) if unit.lower() == 'd'
                      else pd.Timedelta(hours=float(amount)))
            start_ts = end_ts - delta
        else:
            start_ts = pd.Timestamp(start)

    in_range = [(p, k, ts) for p, k, ts in parsed
                if (start_ts is None or ts >= start_ts) and ts <= end_ts]
    if not in_range:
        raise ValueError(
            f'no {kind!r} files in the selected time range '
            f'({start_ts} .. {end_ts})')

    info = get_kind_info(kind)
    norm_mode = _normalize_mode(mode)
    if norm_mode is None:
        single = _default_single_scan_mode(kind)
        if len(in_range) == 1 and single is not None:
            norm_mode = single
        elif TIMESERIES_MODE in info.modes:
            norm_mode = TIMESERIES_MODE
        elif info.modes:
            norm_mode = info.modes[0]
        else:
            raise NotImplementedError(
                f'plotting is not yet implemented for file kind {kind!r}')

    if norm_mode in SINGLE_SCAN_MODES and len(in_range) > 1:
        # a single-scan mode is inherently one file: take the most
        # recent file at or before `end` as the natural anchor for
        # "the scan as of this time".
        in_range = [in_range[-1]]

    paths_out = [p for p, _, _ in in_range]
    common_kwargs = dict(mode=norm_mode, height=height, distance=distance,
                          speed=speed, output=output, show=show,
                          figsize=figsize, fontsize=fontsize,
                          filter=intensity_min, fill=fill)
    if len(paths_out) == 1:
        return plot_file(paths_out[0], **common_kwargs)
    return plot_files(paths_out, **common_kwargs)


def _new_figure(show: bool, figsize: Optional[Tuple[float, float]]) -> Optional[Figure]:
    """Create a pyplot-managed figure when ``show`` is requested (so
    ``plt.show()`` has a window to display), otherwise let the
    ``create_*_figure`` helpers make a bare, backend-independent one."""
    if not show:
        return None
    import matplotlib.pyplot as plt
    return plt.figure(figsize=figsize)


def _finish(fig: Figure, output: Optional[PathLike], show: bool) -> None:
    if output is not None:
        fig.savefig(output, dpi=150)
    if show:
        import matplotlib.pyplot as plt
        plt.show()
