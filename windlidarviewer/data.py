"""
Domain-level data extraction for plotting: turns parsed ``.hpl`` files
(see :mod:`windlidarviewer.hpl`) into plain numpy/pandas structures
that :mod:`windlidarviewer.plotting` can draw, without either module
knowing about the other's concerns (file format vs. rendering).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import hpl

__all__ = [
    'ProfileData', 'ProfileSeriesData',
    'load_profile', 'load_profile_series',
    'ScanHistoryData', 'load_scan_history',
    'RhiCrossSectionData', 'load_rhi_cross_section',
]


@dataclass
class ProfileData:
    """One "Processed Wind Profile" snapshot: height, speed and
    direction arrays for a single timestamp."""
    timestamp: pd.Timestamp
    height: np.ndarray
    speed: np.ndarray
    direction: np.ndarray
    path: Path


def load_profile(path) -> ProfileData:
    """
    Load a single ``Processed_Wind_Profile_*.hpl`` file.

    :param path: path to the file.
    :raises ValueError: if the file does not contain processed profile \
        data (i.e. it is a regular scan file, not the header-less \
        "Processed Wind Profile" variant).
    """
    path = Path(path)
    d = hpl.DataFile(str(path))
    if d.profile is None:
        raise ValueError(
            f'{path} does not contain Processed Wind Profile data '
            f'(got scan type {d.type!r})')
    df = d.profile.sort_index()
    return ProfileData(
        timestamp=d.timestamp,
        height=df.index.to_numpy(dtype=float),
        speed=df['speed'].to_numpy(dtype=float),
        direction=df['direction'].to_numpy(dtype=float),
        path=path,
    )


@dataclass
class ProfileSeriesData:
    """A time series of profile snapshots, resampled onto a common,
    evenly-spaced height grid so they can be shown as a height-vs-time
    image.

    :ivar times: one timestamp per profile (column of the grids).
    :ivar height: common height grid (row labels of the grids).
    :ivar speed: ``(n_height, n_time)`` array of wind speed.
    :ivar direction: ``(n_height, n_time)`` array of wind direction.
    """
    times: pd.DatetimeIndex
    height: np.ndarray
    speed: np.ndarray
    direction: np.ndarray


def _canonical_height_grid(profiles: List[ProfileData]) -> np.ndarray:
    """
    Build one evenly-spaced height grid spanning all the profiles, at
    (the median of) their own gate spacing. Used so every profile is
    interpolated onto exactly the same rows -- see :func:`load_profile_series`
    for why this matters.
    """
    steps = []
    los = []
    his = []
    for p in profiles:
        if p.height.size:
            los.append(float(np.min(p.height)))
            his.append(float(np.max(p.height)))
        if p.height.size > 1:
            steps.append(float(np.median(np.diff(np.sort(p.height)))))
    if not los:
        return np.asarray([], dtype=float)
    lo, hi = min(los), max(his)
    step = float(np.median(steps)) if steps else (hi - lo) or 1.0
    if step <= 0:
        step = (hi - lo) or 1.0
    n = max(2, int(round((hi - lo) / step)) + 1)
    return np.linspace(lo, hi, n)


def _interp_linear(height_grid: np.ndarray, height: np.ndarray,
                    values: np.ndarray) -> np.ndarray:
    """Linear interpolation of ``values`` (given at ``height``) onto
    ``height_grid``; grid points outside this profile's own height
    range are left as ``nan`` rather than extrapolated."""
    if height.size == 0:
        return np.full(height_grid.shape, np.nan)
    return np.interp(height_grid, height, values, left=np.nan, right=np.nan)


def _interp_circular(height_grid: np.ndarray, height: np.ndarray,
                      degrees: np.ndarray) -> np.ndarray:
    """Interpolate a cyclic quantity in degrees (e.g. wind direction,
    where 0 == 360) onto ``height_grid``. Interpolating the raw degree
    values directly would average e.g. 355 and 5 to 180 (the opposite
    direction); instead this interpolates the corresponding unit
    vectors and converts back, which handles the wrap-around correctly."""
    if height.size == 0:
        return np.full(height_grid.shape, np.nan)
    radians = np.deg2rad(degrees)
    u = _interp_linear(height_grid, height, np.cos(radians))
    v = _interp_linear(height_grid, height, np.sin(radians))
    return np.degrees(np.arctan2(v, u)) % 360.0


def load_profile_series(paths: Iterable) -> ProfileSeriesData:
    """
    Load and combine multiple processed wind profile files into a
    single height-by-time series suitable for a time-height plot.

    Each profile is linearly interpolated in height (circularly for
    direction, see :func:`_interp_circular`) onto one common,
    evenly-spaced height grid built from all the profiles (see
    :func:`_canonical_height_grid`), rather than placed at its own raw
    per-file height values. Even when every file nominally reports the
    same instrument range gates, tiny floating-point differences between
    files otherwise turn what should be one row into several
    near-duplicate rows, each populated by only a few of the time steps
    -- which a height-vs-time image then renders as sparse, near-white
    horizontal streaks. Interpolating onto a shared grid is a no-op
    where the data already line up, and removes that artifact where it
    doesn't.

    :param paths: iterable of file paths.
    :raises ValueError: if ``paths`` is empty.
    """
    profiles: List[ProfileData] = [load_profile(p) for p in paths]
    if not profiles:
        raise ValueError('no files given to build a profile series from')
    profiles.sort(key=lambda p: p.timestamp)

    height = _canonical_height_grid(profiles)
    n_h = len(height)
    n_t = len(profiles)
    speed = np.full((n_h, n_t), np.nan)
    direction = np.full((n_h, n_t), np.nan)
    times = []
    for j, p in enumerate(profiles):
        times.append(p.timestamp)
        speed[:, j] = _interp_linear(height, p.height, p.speed)
        direction[:, j] = _interp_circular(height, p.height, p.direction)

    return ProfileSeriesData(
        times=pd.DatetimeIndex(times), height=height,
        speed=speed, direction=direction,
    )


# =========================================================================
# Regular scan files (VAD, Stare, RHI, Wind_Profile): raw intensity/beta
# "history", and RHI's own distance/height cross section.
# =========================================================================


def _gate_distance_axis(gate_length: float, n_gates: int) -> np.ndarray:
    """Range-gate-center distance (m) along the beam for ``n_gates``
    gates of ``gate_length`` metres each -- gate 0's center sits half a
    gate length out, matching the "Range of measurement (center of
    gate)" convention noted in the ``.hpl`` header."""
    return (np.arange(n_gates) + 0.5) * gate_length


def _tilt_corrected_unit_components(
        azimuth: float, elevation: float,
        pitch: Optional[float], roll: Optional[float]) -> Tuple[float, float]:
    """
    Convert one ray's nominal (azimuth, elevation) pointing direction
    plus the instrument's own (pitch, roll) tilt into a true, level-
    frame unit pointing vector's horizontal and vertical components.

    ``azimuth``/``elevation`` are the ray's recorded angles (elevation
    up from the horizon, azimuth clockwise from north); ``pitch``/
    ``roll`` are the instrument's own small deviations from level
    (``None`` is treated as ``0``). All in degrees.

    Method: build the ray's unit pointing vector in an East/North/Up
    frame from the *nominal* azimuth/elevation as if the instrument
    were perfectly level, then apply the pitch correction (an ordinary
    3-D rotation about the East axis) followed by the roll correction
    (a rotation about the now-pitched North axis). This is a full
    rotation, not a linear/small-angle approximation, so it stays exact
    even though these particular pitch/roll values are typically well
    under a degree.

    :returns: ``(horizontal, vertical)`` components of the corrected \
        unit vector (``horizontal**2 + vertical**2 == 1``); multiply \
        each by the along-beam range to get horizontal distance and \
        height.
    """
    az = np.deg2rad(float(azimuth))
    el = np.deg2rad(float(elevation))
    p = np.deg2rad(float(pitch) if pitch is not None else 0.0)
    r = np.deg2rad(float(roll) if roll is not None else 0.0)

    # nominal (level-instrument) pointing vector: east, north, up
    x0 = np.cos(el) * np.sin(az)
    y0 = np.cos(el) * np.cos(az)
    z0 = np.sin(el)

    # pitch: rotate about the east axis (x unchanged)
    x1 = x0
    y1 = y0 * np.cos(p) - z0 * np.sin(p)
    z1 = y0 * np.sin(p) + z0 * np.cos(p)

    # roll: rotate about the (pitched) north axis (y unchanged)
    x2 = x1 * np.cos(r) + z1 * np.sin(r)
    y2 = y1
    z2 = -x1 * np.sin(r) + z1 * np.cos(r)

    horizontal = float(np.hypot(x2, y2))
    vertical = float(z2)
    return horizontal, vertical


@dataclass
class ScanHistoryData:
    """A time-binned "history" of a regular scan kind (VAD, Stare, RHI
    or Wind_Profile): intensity and beta, averaged into "round" time
    bins (see :func:`_pick_history_bin_seconds`) against a distance
    axis inferred directly from the range gates. Not adjusted for scan
    geometry -- rays within one bin can span many different azimuths/
    elevations, so there is no single meaningful height or horizontal
    distance for a bin, only along-beam range.

    :ivar times: one timestamp (bin center) per column.
    :ivar distance: gate-center distance (m), one per row.
    :ivar intensity: ``(n_distance, n_time)``; ``nan`` where no ray \
        fell into that bin.
    :ivar beta: ``(n_distance, n_time)``; ``nan`` where empty.
    """
    times: pd.DatetimeIndex
    distance: np.ndarray
    intensity: np.ndarray
    beta: np.ndarray


#: Candidate "round" bin widths (seconds) for scan-history time
#: binning, 10 seconds up to a week. :func:`_pick_history_bin_seconds`
#: picks whichever lands roughly 100-250 bins across the loaded span.
_HISTORY_BIN_CANDIDATES_S = [
    10, 15, 30, 60, 120, 300, 600, 900, 1800,
    3600, 7200, 10800, 21600, 43200,
    86400, 172800, 604800,
]


def _pick_history_bin_seconds(span_seconds: float) -> float:
    """Pick a "round" bin width so the number of bins across
    ``span_seconds`` lands in roughly the 100-250 range (comfortably
    within the resolution of a plot a few hundred pixels wide): the
    candidate in :data:`_HISTORY_BIN_CANDIDATES_S` closest, on a log
    scale (so it favours neither end of the range), to
    ``span_seconds / 175`` -- the log-scale midpoint of a 100-250 bin
    count."""
    if span_seconds <= 0:
        return float(_HISTORY_BIN_CANDIDATES_S[0])
    target = span_seconds / 175.0
    return float(min(_HISTORY_BIN_CANDIDATES_S,
                      key=lambda c: abs(np.log(c / target))))


def load_scan_history(paths: Iterable) -> ScanHistoryData:
    """
    Build a time-binned intensity/beta history from one or more regular
    scan files (VAD, Stare, RHI or Wind_Profile) -- the counterpart to
    :func:`load_profile_series` for kinds with no instrument-processed
    profile, built from the raw per-gate, per-ray data instead.

    Individual rays are never plotted on their own (a single file
    already holds several rays a few seconds apart, at different
    azimuths/elevations, and a time range spans many files); instead
    every ray's per-gate intensity/beta is averaged into "round" time
    bins (:func:`_pick_history_bin_seconds`) against a distance axis
    taken directly from the range gates (gate index and the first
    file's ``Range gate length (m)`` -- see :func:`_gate_distance_axis`).
    A bin with no ray falling into it is left as ``nan``; the plotting
    layer then leaves the corresponding pixels empty (background)
    rather than interpolating across the gap.

    :param paths: iterable of file paths (regular scan ``.hpl`` files).
    :raises ValueError: if none of the files contain any ray data.
    """
    files = [hpl.DataFile(str(p)) for p in paths]
    files = [f for f in files if f.rays]
    if not files:
        raise ValueError('no scan (ray) data found in the given files')

    gate_length = 1.0
    for f in files:
        gl = f.header.get('gatelength') if f.header else None
        if gl:
            gate_length = float(gl)
            break
    n_gates = max((len(r.data.index) for f in files for r in f.rays),
                  default=0)
    distance = _gate_distance_axis(gate_length, n_gates)

    ray_times = pd.DatetimeIndex([r.time for f in files for r in f.rays])
    t0 = ray_times.min()
    span = (ray_times.max() - t0).total_seconds()
    bin_s = _pick_history_bin_seconds(span)
    n_bins = max(1, int(span // bin_s) + 1)

    sum_i = np.zeros((n_gates, n_bins))
    cnt_i = np.zeros((n_gates, n_bins))
    sum_b = np.zeros((n_gates, n_bins))
    cnt_b = np.zeros((n_gates, n_bins))

    for f in files:
        for r in f.rays:
            ng = len(r.data.index)
            if ng == 0:
                continue
            j = int((r.time - t0).total_seconds() // bin_s)
            j = min(max(j, 0), n_bins - 1)
            inten = pd.to_numeric(r.data['Intensity'], errors='coerce').to_numpy()
            beta = pd.to_numeric(r.data['Beta'], errors='coerce').to_numpy()
            ok_i = np.isfinite(inten)
            sum_i[:ng, j] += np.where(ok_i, inten, 0.0)
            cnt_i[:ng, j] += ok_i
            ok_b = np.isfinite(beta)
            sum_b[:ng, j] += np.where(ok_b, beta, 0.0)
            cnt_b[:ng, j] += ok_b

    with np.errstate(invalid='ignore'):
        intensity = np.where(cnt_i > 0, sum_i / np.where(cnt_i > 0, cnt_i, 1),
                              np.nan)
        beta = np.where(cnt_b > 0, sum_b / np.where(cnt_b > 0, cnt_b, 1),
                         np.nan)

    bin_times = pd.DatetimeIndex(
        [t0 + pd.Timedelta(seconds=bin_s * (j + 0.5)) for j in range(n_bins)])
    return ScanHistoryData(times=bin_times, distance=distance,
                            intensity=intensity, beta=beta)


@dataclass
class RhiCrossSectionData:
    """One RHI scan's vertical cross section: every (gate, ray) point's
    horizontal distance and height (from
    :func:`_tilt_corrected_unit_components`, using that ray's own
    azimuth/elevation/pitch/roll), paired with its radial velocity
    (Doppler) and backscatter (beta). Points from different rays don't
    share a common distance/height grid the way a fixed-geometry scan
    would, so this is plotted as individual points, not gridded."""
    timestamp: pd.Timestamp
    distance: np.ndarray
    height: np.ndarray
    velocity: np.ndarray
    beta: np.ndarray
    path: Path


def load_rhi_cross_section(path) -> RhiCrossSectionData:
    """
    Load a single RHI scan file and compute its distance/height cross
    section: horizontal distance and height for every range gate of
    every ray, from that ray's own azimuth, elevation, pitch and roll
    (:func:`_tilt_corrected_unit_components`), paired with that gate's
    radial velocity (Doppler) and backscatter (beta).

    :param path: path to an ``RHI_*.hpl`` file.
    :raises ValueError: if the file contains no ray (scan) data.
    """
    path = Path(path)
    f = hpl.DataFile(str(path))
    if not f.rays:
        raise ValueError(f'{path} contains no scan (ray) data')
    gate_length = float(f.header.get('gatelength') or 1.0) if f.header else 1.0

    distances: List[np.ndarray] = []
    heights: List[np.ndarray] = []
    velocities: List[np.ndarray] = []
    betas: List[np.ndarray] = []
    for r in f.rays:
        ng = len(r.data.index)
        if ng == 0:
            continue
        gate_range = _gate_distance_axis(gate_length, ng)
        horiz, vert = _tilt_corrected_unit_components(
            r.azimuth, r.elevation, r.pitch, r.roll)
        distances.append(gate_range * horiz)
        heights.append(gate_range * vert)
        velocities.append(
            pd.to_numeric(r.data['Doppler'], errors='coerce').to_numpy())
        betas.append(
            pd.to_numeric(r.data['Beta'], errors='coerce').to_numpy())

    return RhiCrossSectionData(
        timestamp=f.timestamp,
        distance=np.concatenate(distances) if distances else np.array([]),
        height=np.concatenate(heights) if heights else np.array([]),
        velocity=np.concatenate(velocities) if velocities else np.array([]),
        beta=np.concatenate(betas) if betas else np.array([]),
        path=path,
    )
