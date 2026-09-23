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
* Radial velocity (RHI's Doppler value, which is signed -- towards vs.
  away from the instrument) uses ``PuOr``, a perceptually-balanced
  *diverging* colormap that avoids the red/green endpoints most likely
  to be confused under red-green colour vision deficiency.
"""

from __future__ import annotations

import os
from typing import Optional, Sequence, Tuple

import numpy as np
if os.environ.get('BUILDING_SPHINX', 'false') == 'false':
    import matplotlib.dates as mdates
    from matplotlib.figure import Figure
else:
    mdates = Figure = None

__all__ = [
    'SPEED_CMAP', 'DIRECTION_CMAP', 'INTENSITY_CMAP', 'BETA_CMAP',
    'VELOCITY_CMAP', 'MAX_AUTOSCALE_SPEED',
    'create_profile_figure', 'plot_wind_profile',
    'create_timeseries_figure', 'plot_wind_timeseries',
    'plot_scan_history', 'plot_rhi_cross_section',
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
    *and* for RHI's own Profile mode (:func:`plot_rhi_cross_section`,
    a scatter rather than a gridded image, but the same axes shape) --
    the content differs, the geometry doesn't.

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
# RHI cross section plot: a single scan's distance/height cross
# section, radial velocity and beta as colour-coded scatter points
# (rays don't share a common distance/height grid the way a fixed scan
# geometry would, so this is not gridded like the images above). Same
# panel shape as the two plots above, again from create_timeseries_figure.
# =========================================================================

def plot_rhi_cross_section(
        ax_vel, ax_beta, cax_vel, cax_beta,
        distance: np.ndarray, height: np.ndarray,
        velocity: np.ndarray, beta: np.ndarray,
        *,
        distance_xlim: Optional[Tuple[float, float]] = None,
        height_ylim: Optional[Tuple[float, float]] = None,
        speed_vlim: Optional[Tuple[float, float]] = None,
        title: Optional[str] = None,
        marker_size: float = 6.0) -> None:
    """
    Draw one RHI scan's distance/height cross section into an existing
    set of axes created by :func:`create_timeseries_figure`: radial
    velocity on top, beta below, each point placed by its own
    (tilt-corrected) horizontal distance and height and coloured by
    value. Clears and redraws in place.

    :param ax_vel: top axes (radial velocity scatter).
    :param ax_beta: bottom axes (beta scatter), sharing x/y with \
        ``ax_vel``.
    :param cax_vel: colorbar axes for the velocity scatter.
    :param cax_beta: colorbar axes for the beta scatter.
    :param distance: 1-D array of horizontal distance (m), one per point.
    :param height: 1-D array of height (m), same shape as ``distance``.
    :param velocity: 1-D array of radial (Doppler) velocity (m/s).
    :param beta: 1-D array of attenuated backscatter.
    :param distance_xlim: fixed ``(min, max)`` for the shared distance \
        (x) axis; if ``None``, chosen from the data.
    :param height_ylim: fixed ``(min, max)`` for the shared height \
        (y) axis; if ``None``, chosen from the data.
    :param speed_vlim: fixed ``(min, max)`` colour range for radial \
        velocity; if ``None``, a symmetric range from the data's \
        magnitude, capped like :data:`MAX_AUTOSCALE_SPEED`. Beta \
        always autoscales from its own data (see \
        :func:`_auto_vlim`) -- there is no separate manual control \
        for it.
    :param title: optional title drawn above the velocity panel.
    """
    ax_vel.cla()
    ax_beta.cla()
    cax_vel.cla()
    cax_beta.cla()

    if speed_vlim is not None:
        vmin, vmax = speed_vlim
    else:
        vmax = _auto_speed_max(np.abs(velocity), pad=1.0)
        vmin = -vmax

    sc_vel = ax_vel.scatter(distance, height, c=velocity, cmap=VELOCITY_CMAP,
                             vmin=vmin, vmax=vmax, s=marker_size,
                             linewidths=0)
    fig = ax_vel.figure
    fig.colorbar(sc_vel, cax=cax_vel, label='Radial velocity (m/s)')
    ax_vel.set_ylabel('Height (m)')
    ax_vel.tick_params(axis='x', labelbottom=False)
    ax_vel.grid(True, alpha=0.3)

    bmin, bmax = _auto_vlim(beta)
    sc_beta = ax_beta.scatter(distance, height, c=beta, cmap=BETA_CMAP,
                               vmin=bmin, vmax=bmax, s=marker_size,
                               linewidths=0)
    fig.colorbar(sc_beta, cax=cax_beta, label='Beta (m⁻¹ sr⁻¹)')
    ax_beta.set_ylabel('Height (m)')
    ax_beta.set_xlabel('Distance (m)')
    ax_beta.grid(True, alpha=0.3)

    if distance_xlim is not None:
        ax_vel.set_xlim(*distance_xlim)
    elif distance.size:
        ax_vel.set_xlim(float(np.nanmin(distance)), float(np.nanmax(distance)))

    if height_ylim is not None:
        ax_vel.set_ylim(*height_ylim)
    elif height.size:
        ax_vel.set_ylim(float(np.nanmin(height)), float(np.nanmax(height)))

    if title:
        ax_vel.figure.suptitle(title)
