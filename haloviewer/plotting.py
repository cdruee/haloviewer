# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5
"""
Pure matplotlib plotting functions for Halo wind lidar data.

Everything here works on plain ``matplotlib`` :class:`~matplotlib.figure.Figure`
and :class:`~matplotlib.axes.Axes` objects and plain ``numpy``/``pandas``
data; nothing in this module imports a GUI toolkit or knows about the
directory-scanning or file-parsing layers. That keeps it usable from a
script, a notebook, the CLI, or a GUI equally, and is what
:mod:`haloviewer.gui` embeds via ``FigureCanvasTkAgg``.

Figure/axes creation is split from drawing (``create_*_figure`` vs.
``plot_*``) so a caller that steps through many files -- the GUI's
first/back/forward/last navigation -- can create the figure/axes *once*
and just redraw data into the same axes on each step. Combined with
fixed (not ``tight_layout``/``constrained_layout``) subplot positions,
this keeps the panel geometry pixel-identical between redraws: nothing
about a plot's content (tick label widths, title length, colorbar
presence) is allowed to move the axes boxes around, so stepping through
files does not make the plot "wobble".

Colour choices:

* Wind speed (a sequential quantity) uses matplotlib's ``viridis``,
  a perceptually-uniform, colour-vision-deficiency-friendly colormap.
* Wind direction (a cyclic quantity, 0 degrees == 360 degrees) uses
  matplotlib's ``twilight``, a perceptually-uniform *cyclic* colormap
  that is far more accessible than traditional cyclic choices like
  ``hsv`` or ``jet``.
* Intensity (a sequential quantity, the raw scan kinds' SNR+1) uses
  ``cividis``, another perceptually-uniform sequential colormap, chosen
  to look visually distinct from speed's ``viridis`` at a glance.
* Beta / backscatter (also sequential) uses ``magma``, likewise
  perceptually uniform and distinct from both of the above.
* Radial velocity (the RHI/PPI views' Doppler value, which is signed --
  towards vs. away from the instrument) uses ``PuOr``, a
  perceptually-balanced *diverging* colormap that avoids the red/green
  endpoints most likely to be confused under red-green colour vision
  deficiency.
"""

from __future__ import annotations

import warnings
from typing import List, Optional, Sequence, Tuple

import matplotlib.dates as mdates
import numpy as np
from matplotlib.figure import Figure
from mpl_toolkits.axes_grid1 import make_axes_locatable

__all__ = [
    'SPEED_CMAP', 'DIRECTION_CMAP', 'INTENSITY_CMAP', 'BETA_CMAP',
    'VELOCITY_CMAP', 'MAX_AUTOSCALE_SPEED', 'FILL_GRID_SIZE',
    'create_profile_figure', 'plot_wind_profile',
    'create_timeseries_figure', 'plot_wind_timeseries',
    'plot_scan_history',
    'create_scan_pair_figure', 'plot_rhi', 'plot_ppi', 'scan_view_title',
]

SPEED_CMAP = 'viridis'
DIRECTION_CMAP = 'twilight'
INTENSITY_CMAP = 'cividis'
BETA_CMAP = 'magma'
VELOCITY_CMAP = 'PuOr'

_SPEED_COLOR = '#1b6ca8'      # colorblind-safe blue
_DIRECTION_COLOR = '#d55e00'  # colorblind-safe vermillion (Okabe-Ito)

#: Ceiling for automatically-chosen wind speed axis/colour ranges (m/s).
#: A handful of spurious high readings should not wash out the whole
#: scale, so autoscaling never picks a max above this; pass an explicit
#: ``speed_xlim``/``speed_vlim`` to override it for a specific plot.
MAX_AUTOSCALE_SPEED = 50.0

#: A direction line segment whose two endpoints differ by more than this
#: many degrees is a wrap-around artifact (e.g. 355 degrees to 5 degrees,
#: a 10 degree turn) rather than a real jump, and is not drawn.
_DIRECTION_WRAP_THRESHOLD = 180.0

#: Number of cells along each axis of the grid the RHI/PPI "Fill"
#: option paints (see :func:`_nearest_fill`).
FILL_GRID_SIZE = 400


def _auto_speed_max(speed: np.ndarray, pad: float = 1.1) -> float:
    """Autoscaled upper bound for a speed axis/colour range: the data
    maximum with a small margin, capped at :data:`MAX_AUTOSCALE_SPEED`."""
    if speed.size and np.any(np.isfinite(speed)):
        vmax = float(np.nanmax(speed)) * pad
    else:
        vmax = 1.0
    return min(vmax, MAX_AUTOSCALE_SPEED)


def _auto_vlim(values: np.ndarray, lo_pct: float = 2.0,
                hi_pct: float = 98.0) -> Tuple[float, float]:
    """Autoscaled ``(vmin, vmax)`` for a colour range with no natural
    fixed scale (intensity, beta): the ``lo_pct``/``hi_pct`` percentiles
    of the finite values, rather than the raw min/max, so a handful of
    extreme outliers (very common in raw beta, which is dominated by
    near-zero noise with occasional real-signal spikes) don't wash out
    the whole scale."""
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.0, 1.0
    vmin = float(np.percentile(finite, lo_pct))
    vmax = float(np.percentile(finite, hi_pct))
    if vmax <= vmin:
        vmax = vmin + 1.0
    return vmin, vmax


def _break_circular_line(along: np.ndarray, other: np.ndarray,
                          threshold: float = _DIRECTION_WRAP_THRESHOLD):
    """
    Return copies of ``along``/``other`` with a ``nan`` row inserted
    wherever consecutive values of ``along`` (a cyclic quantity such as
    wind direction, in degrees) differ by more than ``threshold``. Used
    to keep a line plot's *markers* at every data point while not
    drawing the connecting segment across a 0/360 wrap-around, which
    would otherwise appear as a long near-horizontal line spanning the
    whole width of the panel.

    Only breaks the one segment that jumps; unaffected neighbouring
    segments are left connected.
    """
    along = np.asarray(along, dtype=float)
    other = np.asarray(other, dtype=float)
    if along.size < 2:
        return along, other
    diffs = np.abs(np.diff(along))
    breaks = np.where(diffs > threshold)[0]
    if breaks.size == 0:
        return along, other
    along_out = along.copy()
    other_out = other.copy()
    for offset, i in enumerate(breaks):
        insert_at = i + 1 + offset
        along_out = np.insert(along_out, insert_at, np.nan)
        other_out = np.insert(other_out, insert_at, np.nan)
    return along_out, other_out


# =========================================================================
# Profile plot: two panels, shared height (y) axis, separate x axes
# =========================================================================

def create_profile_figure(
        figsize: Tuple[float, float] = (6.4, 6.0),
        fig: Optional[Figure] = None) -> Tuple[Figure, tuple]:
    """
    Create a figure with two side-by-side panels (speed, direction)
    sharing a vertical height axis, with a small gap between them and
    a fixed subplot layout (see module docstring for why layout is
    fixed rather than "tight").

    :param figsize: figure size in inches. Ignored if ``fig`` is given.
    :param fig: an existing (empty) :class:`~matplotlib.figure.Figure` \
        to build the axes on, e.g. one obtained from \
        ``matplotlib.pyplot.figure()`` so it can be shown interactively. \
        If ``None`` (the default), a bare, pyplot-independent \
        :class:`~matplotlib.figure.Figure` is created -- the right \
        choice when embedding in a GUI canvas.
    :returns: ``(figure, (ax_speed, ax_dir))``.
    """
    if fig is None:
        fig = Figure(figsize=figsize)
    gs = fig.add_gridspec(1, 2, wspace=0.08,
                           left=0.16, right=0.97, top=0.90, bottom=0.11)
    ax_speed = fig.add_subplot(gs[0, 0])
    ax_dir = fig.add_subplot(gs[0, 1], sharey=ax_speed)
    ax_dir.tick_params(axis='y', labelleft=False)
    return fig, (ax_speed, ax_dir)


def plot_wind_profile(
        ax_speed, ax_dir,
        height: np.ndarray, speed: np.ndarray, direction: np.ndarray,
        *,
        speed_xlim: Optional[Tuple[float, float]] = None,
        height_ylim: Optional[Tuple[float, float]] = None,
        title: Optional[str] = None) -> None:
    """
    Draw one wind profile into an existing pair of axes created by
    :func:`create_profile_figure`. Clears and redraws in place so the
    same axes can be reused across many calls (e.g. when stepping
    through files) without recreating the figure.

    :param ax_speed: left axes (speed vs. height).
    :param ax_dir: right axes (direction vs. height), sharing y with \
        ``ax_speed``.
    :param height: height values (m), 1-D.
    :param speed: wind speed values (m/s), same shape as ``height``.
    :param direction: wind direction values (degrees), same shape as \
        ``height``.
    :param speed_xlim: fixed ``(min, max)`` for the speed axis; if \
        ``None``, chosen from the data with a small margin, capped at \
        :data:`MAX_AUTOSCALE_SPEED`. Pass a fixed value (e.g. the max \
        over all files you'll step through) to keep the axis identical \
        while browsing, or to lift the cap.
    :param height_ylim: fixed ``(min, max)`` for the shared height \
        axis; if ``None``, chosen from the data. As with \
        ``speed_xlim``, pass a fixed range when browsing multiple files.
    :param title: optional title drawn above the speed panel.
    """
    ax_speed.cla()
    ax_dir.cla()

    ax_speed.plot(speed, height, '-o', ms=3, lw=1.2, color=_SPEED_COLOR)
    ax_speed.set_xlabel('Wind speed (m/s)')
    ax_speed.set_ylabel('Height (m)')
    if speed_xlim is not None:
        ax_speed.set_xlim(*speed_xlim)
    else:
        ax_speed.set_xlim(0, max(_auto_speed_max(speed), 1.0))
    ax_speed.grid(True, alpha=0.3)

    # markers at every point, but the connecting line skips segments
    # that jump by more than 180 degrees -- those are 0/360 wrap-around
    # artifacts (e.g. 355 -> 5 is really a 10 degree turn), not real
    # spans, and drawing them as a straight line across the panel is
    # misleading.
    ax_dir.plot(direction, height, 'o', ms=3, color=_DIRECTION_COLOR)
    line_dir, line_height = _break_circular_line(direction, height)
    ax_dir.plot(line_dir, line_height, '-', lw=1.2, color=_DIRECTION_COLOR)
    ax_dir.set_xlabel('Wind direction (°)')
    ax_dir.set_xlim(0, 360)
    ax_dir.set_xticks([0, 90, 180, 270, 360])
    ax_dir.grid(True, alpha=0.3)
    ax_dir.tick_params(axis='y', labelleft=False)

    if height_ylim is not None:
        ax_speed.set_ylim(*height_ylim)
    elif height.size:
        lo, hi = float(np.nanmin(height)), float(np.nanmax(height))
        pad = (hi - lo) * 0.03 if hi > lo else 1.0
        ax_speed.set_ylim(lo - pad, hi + pad)

    if title:
        ax_speed.figure.suptitle(title)


# =========================================================================
# Timeseries plot: two panels stacked, shared time (x) axis
# =========================================================================

def create_timeseries_figure(
        figsize: Tuple[float, float] = (10.0, 6.0),
        fig: Optional[Figure] = None) -> Tuple[Figure, tuple]:
    """
    Create a figure with two vertically-stacked panels sharing a
    horizontal x axis *and* a vertical y axis (so panning/zooming one
    panel keeps the other in sync), each with its own colorbar, a small
    gap between the panels, and a fixed subplot layout.

    This is the shared "two stacked, colour-mapped panels" layout for
    every kind of History plot (:func:`plot_wind_timeseries` for
    ``Processed_Wind_Profile``'s speed/direction,
    :func:`plot_scan_history` for the raw scan kinds' intensity/beta)
    -- the content differs, the geometry doesn't. (The single-scan
    RHI/PPI views use the side-by-side
    :func:`create_scan_pair_figure` instead.)

    :param figsize: figure size in inches. Ignored if ``fig`` is given.
    :param fig: an existing (empty) figure to build the axes on; see \
        :func:`create_profile_figure` for when to pass this.
    :returns: ``(figure, (ax_speed, ax_dir, cax_speed, cax_dir))``.
    """
    if fig is None:
        fig = Figure(figsize=figsize)
    left, right, top, bottom = 0.10, 0.88, 0.93, 0.11
    cbar_w = 0.025
    cbar_gap = 0.02
    gs = fig.add_gridspec(2, 1, hspace=0.08,
                           left=left, right=right, top=top, bottom=bottom)
    ax_speed = fig.add_subplot(gs[0, 0])
    ax_dir = fig.add_subplot(gs[1, 0], sharex=ax_speed, sharey=ax_speed)
    ax_speed.tick_params(axis='x', labelbottom=False)

    cax_speed = fig.add_axes([right + cbar_gap, ax_speed.get_position().y0,
                               cbar_w, ax_speed.get_position().height])
    cax_dir = fig.add_axes([right + cbar_gap, ax_dir.get_position().y0,
                             cbar_w, ax_dir.get_position().height])
    return fig, (ax_speed, ax_dir, cax_speed, cax_dir)


def plot_wind_timeseries(
        ax_speed, ax_dir, cax_speed, cax_dir,
        times: Sequence, height: np.ndarray,
        speed: np.ndarray, direction: np.ndarray,
        *,
        height_ylim: Optional[Tuple[float, float]] = None,
        speed_vlim: Optional[Tuple[float, float]] = None,
        title: Optional[str] = None) -> None:
    """
    Draw a height-vs-time image of wind speed and direction into an
    existing set of axes created by :func:`create_timeseries_figure`.
    Clears and redraws in place so the same axes can be reused across
    calls (e.g. when the selected time range changes).

    :param ax_speed: top axes (speed image).
    :param ax_dir: bottom axes (direction image), sharing x with \
        ``ax_speed``.
    :param cax_speed: colorbar axes for the speed image.
    :param cax_dir: colorbar axes for the direction image.
    :param times: 1-D sequence of ``n_time`` timestamps.
    :param height: 1-D array of ``n_height`` heights (m).
    :param speed: ``(n_height, n_time)`` array of wind speed (m/s).
    :param direction: ``(n_height, n_time)`` array of wind direction \
        (degrees, 0-360).
    :param height_ylim: fixed ``(min, max)`` for the shared height axis.
    :param speed_vlim: fixed ``(min, max)`` color range for speed; if \
        ``None``, chosen from the data, capped at \
        :data:`MAX_AUTOSCALE_SPEED`. Direction always uses the full \
        0-360 range since it is cyclic.
    :param title: optional title drawn above the speed panel.
    """
    ax_speed.cla()
    ax_dir.cla()
    cax_speed.cla()
    cax_dir.cla()

    if speed_vlim is not None:
        vmin, vmax = speed_vlim
    else:
        vmin, vmax = 0.0, max(_auto_speed_max(speed, pad=1.0), 1.0)

    mesh_speed = ax_speed.pcolormesh(
        times, height, speed, shading='nearest',
        cmap=SPEED_CMAP, vmin=vmin, vmax=vmax)
    fig = ax_speed.figure
    fig.colorbar(mesh_speed, cax=cax_speed, label='Speed (m/s)')
    ax_speed.set_ylabel('Height (m)')
    ax_speed.tick_params(axis='x', labelbottom=False)

    mesh_dir = ax_dir.pcolormesh(
        times, height, direction, shading='nearest',
        cmap=DIRECTION_CMAP, vmin=0, vmax=360)
    cb_dir = fig.colorbar(mesh_dir, cax=cax_dir, label='Direction (°)',
                           ticks=[0, 90, 180, 270, 360])
    ax_dir.set_ylabel('Height (m)')
    ax_dir.set_xlabel('Time (UTC)')

    if height_ylim is not None:
        ax_speed.set_ylim(*height_ylim)
    elif height.size:
        ax_speed.set_ylim(float(np.nanmin(height)), float(np.nanmax(height)))

    locator = mdates.AutoDateLocator()
    formatter = mdates.ConciseDateFormatter(locator)
    ax_dir.xaxis.set_major_locator(locator)
    ax_dir.xaxis.set_major_formatter(formatter)

    if title:
        ax_speed.figure.suptitle(title)


# =========================================================================
# Scan history plot (VAD/Stare/RHI/Wind_Profile): intensity/beta vs.
# time and gate-inferred distance. Same panel shape as the wind
# timeseries plot above (built by the same create_timeseries_figure);
# see that function's docstring.
# =========================================================================

def plot_scan_history(
        ax_int, ax_beta, cax_int, cax_beta,
        times: Sequence, distance: np.ndarray,
        intensity: np.ndarray, beta: np.ndarray,
        *,
        distance_ylim: Optional[Tuple[float, float]] = None,
        title: Optional[str] = None) -> None:
    """
    Draw a distance-vs-time image of raw intensity and beta into an
    existing set of axes created by :func:`create_timeseries_figure`.
    Clears and redraws in place, same as :func:`plot_wind_timeseries`.

    Cells that were never populated (see
    :func:`haloviewer.data.load_scan_history`) are ``nan``;
    ``pcolormesh`` leaves ``nan`` cells uncoloured, so an empty time bin
    shows as blank background rather than an interpolated guess.

    :param ax_int: top axes (intensity image).
    :param ax_beta: bottom axes (beta image), sharing x with ``ax_int``.
    :param cax_int: colorbar axes for the intensity image.
    :param cax_beta: colorbar axes for the beta image.
    :param times: 1-D sequence of ``n_time`` bin-center timestamps.
    :param distance: 1-D array of ``n_distance`` gate-center distances (m).
    :param intensity: ``(n_distance, n_time)`` array, SNR + 1.
    :param beta: ``(n_distance, n_time)`` array, attenuated backscatter.
    :param distance_ylim: fixed ``(min, max)`` for the shared distance \
        axis; if ``None``, chosen from the data.
    :param title: optional title drawn above the intensity panel.
    """
    ax_int.cla()
    ax_beta.cla()
    cax_int.cla()
    cax_beta.cla()

    vmin_i, vmax_i = _auto_vlim(intensity)
    mesh_i = ax_int.pcolormesh(
        times, distance, intensity, shading='nearest',
        cmap=INTENSITY_CMAP, vmin=vmin_i, vmax=vmax_i)
    fig = ax_int.figure
    fig.colorbar(mesh_i, cax=cax_int, label='Intensity (SNR + 1)')
    ax_int.set_ylabel('Distance (m)')
    ax_int.tick_params(axis='x', labelbottom=False)

    vmin_b, vmax_b = _auto_vlim(beta)
    mesh_b = ax_beta.pcolormesh(
        times, distance, beta, shading='nearest',
        cmap=BETA_CMAP, vmin=vmin_b, vmax=vmax_b)
    fig.colorbar(mesh_b, cax=cax_beta, label='Beta (m⁻¹ sr⁻¹)')
    ax_beta.set_ylabel('Distance (m)')
    ax_beta.set_xlabel('Time (UTC)')

    if distance_ylim is not None:
        ax_int.set_ylim(*distance_ylim)
    elif distance.size:
        ax_int.set_ylim(float(np.nanmin(distance)), float(np.nanmax(distance)))

    locator = mdates.AutoDateLocator()
    formatter = mdates.ConciseDateFormatter(locator)
    ax_beta.xaxis.set_major_locator(locator)
    ax_beta.xaxis.set_major_formatter(formatter)

    if title:
        ax_int.figure.suptitle(title)


# =========================================================================
# Single-scan RHI / PPI views: one scan's (gate, ray) points projected
# onto the vertical x/z plane (RHI) or the horizontal x/y plane (PPI),
# x pointing along the first ray's azimuth (see
# data.ScanPointsData). Radial velocity and beta side by side, each
# either as colour-coded scatter points or -- with ``fill`` -- as a
# nearest-neighbour filled image.
# =========================================================================

def create_scan_pair_figure(
        figsize: Tuple[float, float] = (11.0, 5.5),
        fig: Optional[Figure] = None) -> Tuple[Figure, tuple]:
    """
    Create a figure with two side-by-side panels (1 row, 2 columns)
    sharing both the x and the y axis, each with its own colorbar on
    its right-hand side. This is the layout of the single-scan RHI and
    PPI views (:func:`plot_rhi`, :func:`plot_ppi`): radial velocity on
    the left, beta on the right.

    The colorbars are attached with an axes divider rather than at
    fixed figure coordinates, so they stay flush with their panel even
    when the PPI view shrinks the panels to an equal (square) aspect.
    The layout is otherwise fixed, like the other ``create_*_figure``
    helpers.

    :param figsize: figure size in inches. Ignored if ``fig`` is given.
    :param fig: an existing (empty) figure to build the axes on; see \
        :func:`create_profile_figure` for when to pass this.
    :returns: ``(figure, (ax_vel, ax_beta, cax_vel, cax_beta))``.
    """
    if fig is None:
        fig = Figure(figsize=figsize)
    gs = fig.add_gridspec(1, 2, wspace=0.32,
                           left=0.08, right=0.89, top=0.88, bottom=0.12)
    ax_vel = fig.add_subplot(gs[0, 0])
    ax_beta = fig.add_subplot(gs[0, 1], sharex=ax_vel, sharey=ax_vel)
    cax_vel = make_axes_locatable(ax_vel).append_axes(
        'right', size='4%', pad=0.08)
    cax_beta = make_axes_locatable(ax_beta).append_axes(
        'right', size='4%', pad=0.08)
    return fig, (ax_vel, ax_beta, cax_vel, cax_beta)


def _convex_hull(points: np.ndarray) -> np.ndarray:
    """Vertices of the 2-D convex hull of ``points`` (``(n, 2)``), in
    counter-clockwise order (Andrew's monotone chain). Returns fewer
    than 3 vertices if the points are (nearly) collinear."""
    pts = np.unique(points, axis=0)
    if len(pts) < 3:
        return pts

    def _half(seq):
        out: List[np.ndarray] = []
        for p in seq:
            while len(out) >= 2:
                a, b = out[-2], out[-1]
                cross = ((b[0] - a[0]) * (p[1] - a[1]) -
                         (b[1] - a[1]) * (p[0] - a[0]))
                if cross > 0:
                    break
                out.pop()
            out.append(p)
        return out

    lower = _half(pts)
    upper = _half(pts[::-1])
    return np.array(lower[:-1] + upper[:-1])


def _nearest_fill(px: np.ndarray, py: np.ndarray,
                  values: Sequence[np.ndarray],
                  xlim: Tuple[float, float], ylim: Tuple[float, float],
                  n: int = FILL_GRID_SIZE):
    """
    Nearest-neighbour interpolation of scattered points onto a regular
    grid, for the RHI/PPI "Fill" option.

    The grid (``n`` x ``n`` cells) covers the part of the view
    (``xlim`` x ``ylim``) that the data actually span; every cell
    takes the value of the data point nearest to its centre. Cells
    outside the convex hull of the data points are left ``nan``
    (blank), so the fill doesn't smear the outermost values out over
    regions the scan never looked at. A point whose value is ``nan``
    (e.g. removed by the intensity filter) still "owns" its
    neighbourhood, so filtered-out regions stay blank rather than
    being papered over by their neighbours.

    Needs :mod:`scipy` (``scipy.spatial.cKDTree``).

    :param px: x coordinates of the data points.
    :param py: y coordinates of the data points (same shape).
    :param values: one or more value arrays (same shape as ``px``).
    :param xlim: ``(min, max)`` of the view in x.
    :param ylim: ``(min, max)`` of the view in y.
    :param n: grid cells per axis.
    :returns: ``(extent, grids)`` -- ``extent`` is ``(x0, x1, y0, y1)`` \
        for :meth:`~matplotlib.axes.Axes.imshow` and ``grids`` a list \
        of ``(n, n)`` arrays (row 0 at ``y0``), one per ``values`` \
        entry -- or ``None`` if no fill is possible (fewer than three \
        non-collinear points, or no overlap with the view).
    """
    from matplotlib.path import Path as _MplPath
    from scipy.spatial import cKDTree

    px = np.asarray(px, dtype=float)
    py = np.asarray(py, dtype=float)
    ok = np.isfinite(px) & np.isfinite(py)
    if ok.sum() < 3:
        return None
    pts = np.column_stack([px[ok], py[ok]])
    hull = _convex_hull(pts)
    if len(hull) < 3:
        return None

    x0 = max(min(xlim), float(pts[:, 0].min()))
    x1 = min(max(xlim), float(pts[:, 0].max()))
    y0 = max(min(ylim), float(pts[:, 1].min()))
    y1 = min(max(ylim), float(pts[:, 1].max()))
    if not (x1 > x0 and y1 > y0):
        return None

    gx = x0 + (np.arange(n) + 0.5) * (x1 - x0) / n
    gy = y0 + (np.arange(n) + 0.5) * (y1 - y0) / n
    mx, my = np.meshgrid(gx, gy)
    cells = np.column_stack([mx.ravel(), my.ravel()])

    _, idx = cKDTree(pts).query(cells)
    # a tiny tolerance keeps cells whose centre sits exactly on the
    # hull boundary (e.g. along a straight outermost ray)
    span = max(x1 - x0, y1 - y0)
    inside = _MplPath(hull).contains_points(cells, radius=1e-9 * span) | \
        _MplPath(hull[::-1]).contains_points(cells, radius=1e-9 * span)

    grids = []
    for v in values:
        g = np.asarray(v, dtype=float)[ok][idx]
        g[~inside] = np.nan
        grids.append(g.reshape(n, n))
    return (x0, x1, y0, y1), grids


def _data_range(values: np.ndarray, pad_frac: float = 0.0
                ) -> Optional[Tuple[float, float]]:
    finite = values[np.isfinite(values)] if values.size else values
    if finite.size == 0:
        return None
    lo, hi = float(finite.min()), float(finite.max())
    pad = (hi - lo) * pad_frac if hi > lo else 1.0
    return lo - pad, hi + pad


def _plot_scan_pair(ax_vel, ax_beta, cax_vel, cax_beta,
                    h: np.ndarray, v: np.ndarray,
                    velocity: np.ndarray, beta: np.ndarray, *,
                    xlim: Tuple[float, float], ylim: Tuple[float, float],
                    speed_vlim: Optional[Tuple[float, float]],
                    fill: bool, equal_aspect: bool,
                    xlabel: str, ylabel: str,
                    title: Optional[str], marker_size: float) -> None:
    """Shared drawing code of :func:`plot_rhi` and :func:`plot_ppi`:
    ``h``/``v`` are the points' horizontal/vertical plot coordinates."""
    for ax in (ax_vel, ax_beta, cax_vel, cax_beta):
        ax.cla()

    h = np.asarray(h, dtype=float)
    v = np.asarray(v, dtype=float)
    velocity = np.asarray(velocity, dtype=float)
    beta = np.asarray(beta, dtype=float)

    if speed_vlim is not None:
        vmin, vmax = speed_vlim
    else:
        vmax = _auto_speed_max(np.abs(velocity), pad=1.0)
        vmin = -vmax
    bmin, bmax = _auto_vlim(beta)

    filled = None
    if fill:
        try:
            filled = _nearest_fill(h, v, [velocity, beta], xlim, ylim)
        except ImportError:
            warnings.warn('fill needs scipy (not installed) -- drawing '
                          'the data points instead', stacklevel=3)

    fig = ax_vel.figure
    panels = (
        (ax_vel, cax_vel, velocity, VELOCITY_CMAP, vmin, vmax,
         'Radial velocity (m/s)'),
        (ax_beta, cax_beta, beta, BETA_CMAP, bmin, bmax,
         'Beta (m⁻¹ sr⁻¹)'),
    )
    for k, (ax, cax, values, cmap, lo, hi, label) in enumerate(panels):
        if filled is not None:
            extent, grids = filled
            artist = ax.imshow(grids[k], extent=extent, origin='lower',
                               cmap=cmap, vmin=lo, vmax=hi,
                               interpolation='nearest', aspect='auto')
        else:
            artist = ax.scatter(h, v, c=values, cmap=cmap, vmin=lo,
                                vmax=hi, s=marker_size, linewidths=0)
        fig.colorbar(artist, cax=cax, label=label)
        ax.set_xlabel(xlabel)
        ax.grid(True, alpha=0.3)
    ax_vel.set_ylabel(ylabel)
    ax_beta.tick_params(axis='y', labelleft=False)

    ax_vel.set_xlim(*xlim)
    ax_vel.set_ylim(*ylim)
    for ax in (ax_vel, ax_beta):
        ax.set_aspect('equal' if equal_aspect else 'auto', adjustable='box')

    if title:
        fig.suptitle(title)


def scan_view_title(kind: str, view: str) -> str:
    """Title prefix for an RHI/PPI view of a ``kind`` file: just the
    kind when they coincide (an RHI file shown as RHI), else
    ``"<kind> (<VIEW>)"``, e.g. ``"VAD (PPI)"``."""
    view = view.upper()
    return kind if kind.upper() == view else f'{kind} ({view})'


def _azimuth_label(azimuth: Optional[float]) -> str:
    return '' if azimuth is None else f' → {azimuth % 360:.1f}°'


def plot_rhi(
        ax_vel, ax_beta, cax_vel, cax_beta,
        x: np.ndarray, z: np.ndarray,
        velocity: np.ndarray, beta: np.ndarray,
        *,
        distance_xlim: Optional[Tuple[float, float]] = None,
        height_ylim: Optional[Tuple[float, float]] = None,
        speed_vlim: Optional[Tuple[float, float]] = None,
        fill: bool = False,
        azimuth0: Optional[float] = None,
        title: Optional[str] = None,
        marker_size: float = 6.0) -> None:
    """
    Draw one scan projected onto the vertical x/z plane (RHI view) into
    an existing set of axes created by :func:`create_scan_pair_figure`:
    radial velocity left, beta right, every point placed at its own
    ``(x, z)`` -- ``x`` horizontal along the scan's first-ray azimuth
    (negative for rays pointing more than 90 degrees away from it),
    ``z`` height (see :class:`haloviewer.data.ScanPointsData`). Clears
    and redraws in place.

    :param ax_vel: left axes (radial velocity).
    :param ax_beta: right axes (beta), sharing x/y with ``ax_vel``.
    :param cax_vel: colorbar axes for the velocity panel.
    :param cax_beta: colorbar axes for the beta panel.
    :param x: 1-D array of horizontal distance along the first-ray \
        azimuth (m), one per point.
    :param z: 1-D array of height (m), same shape as ``x``.
    :param velocity: 1-D array of radial (Doppler) velocity (m/s).
    :param beta: 1-D array of attenuated backscatter.
    :param distance_xlim: fixed ``(min, max)`` for the shared distance \
        (x) axis; if ``None``, chosen from the data.
    :param height_ylim: fixed ``(min, max)`` for the shared height \
        (z) axis; if ``None``, chosen from the data.
    :param speed_vlim: fixed ``(min, max)`` colour range for radial \
        velocity; if ``None``, a symmetric range from the data's \
        magnitude, capped like :data:`MAX_AUTOSCALE_SPEED`. Beta \
        always autoscales from its own data (see :func:`_auto_vlim`).
    :param fill: if ``True``, fill the area between the points by \
        nearest-neighbour interpolation (see :func:`_nearest_fill`) \
        instead of drawing individual points.
    :param azimuth0: azimuth (degrees) the x axis points to, shown in \
        the axis label.
    :param title: optional figure title.
    :param marker_size: scatter marker size (points²) when not filled.
    """
    x = np.asarray(x, dtype=float)
    z = np.asarray(z, dtype=float)
    xlim = distance_xlim or _data_range(x) or (0.0, 1.0)
    ylim = height_ylim or _data_range(z) or (0.0, 1.0)
    _plot_scan_pair(ax_vel, ax_beta, cax_vel, cax_beta, x, z,
                    velocity, beta, xlim=xlim, ylim=ylim,
                    speed_vlim=speed_vlim, fill=fill, equal_aspect=False,
                    xlabel=f'Distance{_azimuth_label(azimuth0)} (m)',
                    ylabel='Height (m)', title=title,
                    marker_size=marker_size)


def plot_ppi(
        ax_vel, ax_beta, cax_vel, cax_beta,
        x: np.ndarray, y: np.ndarray,
        velocity: np.ndarray, beta: np.ndarray,
        *,
        distance_max: Optional[float] = None,
        speed_vlim: Optional[Tuple[float, float]] = None,
        fill: bool = False,
        azimuth0: Optional[float] = None,
        title: Optional[str] = None,
        marker_size: float = 6.0) -> None:
    """
    Draw one scan projected onto the horizontal x/y plane (PPI view)
    into an existing set of axes created by
    :func:`create_scan_pair_figure`: radial velocity left, beta right,
    on square (equal-aspect) panels. ``x`` points along the scan's
    first-ray azimuth, ``y`` 90 degrees counter-clockwise from it (see
    :class:`haloviewer.data.ScanPointsData`), so the picture is a map
    view rotated to put ``azimuth0`` on the right. Clears and redraws in
    place.

    :param ax_vel: left axes (radial velocity).
    :param ax_beta: right axes (beta), sharing x/y with ``ax_vel``.
    :param cax_vel: colorbar axes for the velocity panel.
    :param cax_beta: colorbar axes for the beta panel.
    :param x: 1-D array of the points' x coordinate (m).
    :param y: 1-D array of the points' y coordinate (m).
    :param velocity: 1-D array of radial (Doppler) velocity (m/s).
    :param beta: 1-D array of attenuated backscatter.
    :param distance_max: both axes show ``[-distance_max, \
        +distance_max]``; if ``None``, the largest ``|x|``/``|y|`` of \
        the data.
    :param speed_vlim: see :func:`plot_rhi`.
    :param fill: see :func:`plot_rhi`.
    :param azimuth0: azimuth (degrees) the x axis points to, shown in \
        the axis labels.
    :param title: optional figure title.
    :param marker_size: scatter marker size (points²) when not filled.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if distance_max is None or not distance_max > 0:
        finite = np.abs(np.concatenate([x, y]))
        finite = finite[np.isfinite(finite)]
        distance_max = float(finite.max()) if finite.size else 1.0
        distance_max = distance_max or 1.0
    lim = (-float(distance_max), float(distance_max))
    y_az = None if azimuth0 is None else azimuth0 - 90.0
    _plot_scan_pair(ax_vel, ax_beta, cax_vel, cax_beta, x, y,
                    velocity, beta, xlim=lim, ylim=lim,
                    speed_vlim=speed_vlim, fill=fill, equal_aspect=True,
                    xlabel=f'x{_azimuth_label(azimuth0)} (m)',
                    ylabel=f'y{_azimuth_label(y_az)} (m)', title=title,
                    marker_size=marker_size)
