# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5
"""
Domain-level data extraction for plotting: turns parsed ``.hpl`` files
(see :mod:`haloviewer.hpl`) into plain numpy/pandas structures
that :mod:`haloviewer.plotting` can draw, without either module
knowing about the other's concerns (file format vs. rendering).
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import hpl

logger = logging.getLogger(__name__)

__all__ = [
    'DEFAULT_INTENSITY_FILTER', 'resolve_intensity_filter',
    'find_wind_profile_file', 'load_wind_profile_intensity',
    'ProfileData', 'ProfileSeriesData',
    'load_profile', 'load_profile_series',
    'ScanHistoryData', 'load_scan_history',
    'RhiCrossSectionData', 'load_rhi_cross_section',
]


# =========================================================================
# Optional intensity filter
# =========================================================================

#: Default threshold for the optional intensity filter (intensity is
#: the instrument's SNR + 1): data points whose intensity is *below*
#: this value are replaced by ``nan``. Used whenever the filter is
#: switched on without an explicit value (``filter=True`` in
#: :func:`haloviewer.plot <haloviewer.api.plot>`, ``--filter True`` in
#: ``haloplot``, and the initial value of the GUI's filter field).
DEFAULT_INTENSITY_FILTER: float = 1.18

_TRUE_WORDS = {'true', 'yes', 'on', 'default'}
_FALSE_WORDS = {'false', 'no', 'off', 'none', ''}


def resolve_intensity_filter(value) -> Optional[float]:
    """
    Turn the user-facing ``filter`` setting into a numeric intensity
    threshold, or ``None`` for "no filtering".

    * ``None`` or ``False`` -> ``None`` (filter off).
    * ``True`` -> :data:`DEFAULT_INTENSITY_FILTER`.
    * a number -> that number.
    * a string (as typed on the command line): ``"true"``/``"yes"``/
      ``"on"``/``"default"`` (any case) -> the default threshold,
      ``"false"``/``"no"``/``"off"``/``"none"`` -> ``None``, anything
      else is parsed as a number.

    :raises ValueError: if the value is not one of the above, or not a \
        finite number.
    """
    if value is None or value is False:
        return None
    if value is True:
        return DEFAULT_INTENSITY_FILTER
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return DEFAULT_INTENSITY_FILTER
        if word in _FALSE_WORDS:
            return None
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f'filter must be None, True/False or a number, got {value!r}'
        ) from None
    if not np.isfinite(threshold):
        raise ValueError(f'filter threshold must be finite, got {value!r}')
    return threshold


def _mask_below(values: np.ndarray, intensity: np.ndarray,
                 intensity_min: Optional[float]) -> np.ndarray:
    """``values`` with every element whose ``intensity`` is below
    ``intensity_min`` replaced by ``nan`` (a copy; the input is left
    alone). Points with no intensity at all (``nan``) are kept -- there
    is nothing to decide on. ``intensity_min`` of ``None`` returns
    ``values`` unchanged."""
    values = np.asarray(values, dtype=float)
    if intensity_min is None:
        return values
    with np.errstate(invalid='ignore'):
        below = np.asarray(intensity, dtype=float) < intensity_min
    if not below.any():
        return values
    out = values.copy()
    out[below] = np.nan
    return out


@dataclass
class ProfileData:
    """One "Processed Wind Profile" snapshot: height, speed and
    direction arrays for a single timestamp."""
    timestamp: pd.Timestamp
    height: np.ndarray
    speed: np.ndarray
    direction: np.ndarray
    path: Path
    #: Intensity (SNR + 1) at each ``height``, taken from the matching
    #: ``Wind_Profile`` scan file (see :func:`load_wind_profile_intensity`).
    #: Only filled in when the intensity filter was requested; ``None``
    #: if it wasn't, or if no matching scan file was found (in which
    #: case speed/direction are left unfiltered).
    intensity: Optional[np.ndarray] = None
    #: ``True`` if the intensity filter was requested but could not be
    #: applied (no matching ``Wind_Profile`` file).
    filter_missing: bool = False


def find_wind_profile_file(path) -> Optional[Path]:
    """
    The raw ``Wind_Profile`` scan file a ``Processed_Wind_Profile`` file
    was derived from: same directory, same system id, same timestamp
    (``Processed_Wind_Profile_77_20260919_121707.hpl`` ->
    ``Wind_Profile_77_20260919_121707.hpl``).

    :returns: its path, or ``None`` if ``path`` isn't a \
        ``Processed_Wind_Profile`` file or no such scan file exists.
    """
    path = Path(path)
    prefix = 'Processed_Wind_Profile_'
    if not path.name.startswith(prefix):
        return None
    candidate = path.with_name('Wind_Profile_' + path.name[len(prefix):])
    return candidate if candidate.is_file() else None


def load_wind_profile_intensity(path, height: np.ndarray) -> np.ndarray:
    """
    Intensity (SNR + 1) of a ``Wind_Profile`` scan file at the given
    ``height`` levels (m), for filtering the matching processed profile.

    Every ray (beam) of the scan is converted from range gates to
    height with its own tilt-corrected elevation (see
    :func:`_tilt_corrected_unit_components`), its intensity is
    interpolated linearly onto ``height``, and the result is averaged
    over all rays -- the same "one value per level from all beams" that
    the processed profile itself represents. A level more than one gate
    below the lowest or above the highest gate of a ray gets no value
    from that ray; a level no ray covers is ``nan``.

    :raises ValueError: if the file contains no usable ray data.
    """
    height = np.asarray(height, dtype=float)
    f = hpl.DataFile(str(path))
    if not f.rays:
        raise ValueError(f'{path} contains no scan (ray) data')
    gate_length = float(f.header.get('gatelength') or 1.0) if f.header else 1.0

    per_ray: List[np.ndarray] = []
    for r in f.rays:
        ng = len(r.data.index)
        if ng == 0:
            continue
        _horiz, vert = _tilt_corrected_unit_components(
            r.azimuth, r.elevation, r.pitch, r.roll)
        if vert <= 0:
            continue  # a beam at/below the horizon has no height axis
        gate_height = _gate_distance_axis(gate_length, ng) * vert
        inten = pd.to_numeric(r.data['Intensity'], errors='coerce').to_numpy()
        ok = np.isfinite(inten)
        if not ok.any():
            continue
        h_ok, i_ok = gate_height[ok], inten[ok]
        values = np.interp(height, h_ok, i_ok)
        step = gate_length * vert
        values[(height < h_ok[0] - step) | (height > h_ok[-1] + step)] = np.nan
        per_ray.append(values)
    if not per_ray:
        raise ValueError(f'{path} contains no usable intensity data')
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)  # all-nan levels
        return np.nanmean(np.vstack(per_ray), axis=0)


def load_profile(path, intensity_min: Optional[float] = None) -> ProfileData:
    """
    Load a single ``Processed_Wind_Profile_*.hpl`` file.

    :param path: path to the file.
    :param intensity_min: optional intensity filter threshold: speed \
        and direction at levels where the matching ``Wind_Profile`` \
        scan (:func:`find_wind_profile_file`) has an intensity below \
        this value are set to ``nan``. If that scan file doesn't exist \
        or can't be read, a warning is logged, the profile is returned \
        unfiltered and :attr:`ProfileData.filter_missing` is set.
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
    prof = ProfileData(
        timestamp=d.timestamp,
        height=df.index.to_numpy(dtype=float),
        speed=df['speed'].to_numpy(dtype=float),
        direction=df['direction'].to_numpy(dtype=float),
        path=path,
    )
    if intensity_min is None:
        return prof

    wp_path = find_wind_profile_file(path)
    if wp_path is None:
        logger.warning('%s: no matching Wind_Profile file found -- '
                       'intensity filter not applied', path.name)
        prof.filter_missing = True
        return prof
    try:
        prof.intensity = load_wind_profile_intensity(wp_path, prof.height)
    except (IOError, ValueError) as exc:
        logger.warning('%s: could not read intensity from %s (%s) -- '
                       'intensity filter not applied',
                       path.name, wp_path.name, exc)
        prof.filter_missing = True
        return prof
    prof.speed = _mask_below(prof.speed, prof.intensity, intensity_min)
    prof.direction = _mask_below(prof.direction, prof.intensity,
                                 intensity_min)
    return prof


@dataclass
class ProfileSeriesData:
    """A time series of profile snapshots, resampled onto a common,
    evenly-spaced height grid so they can be shown as a height-vs-time
    image.

    :ivar times: one timestamp per profile (column of the grids).
    :ivar height: common height grid (row labels of the grids).
    :ivar speed: ``(n_height, n_time)`` array of wind speed.
    :ivar direction: ``(n_height, n_time)`` array of wind direction.
    :ivar filter_missing: timestamps of the profiles for which an \
        intensity filter was requested but could not be applied (no \
        matching ``Wind_Profile`` file); empty otherwise.
    """
    times: pd.DatetimeIndex
    height: np.ndarray
    speed: np.ndarray
    direction: np.ndarray
    filter_missing: List[pd.Timestamp] = field(default_factory=list)


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


def load_profile_series(paths: Iterable,
                        intensity_min: Optional[float] = None
                        ) -> ProfileSeriesData:
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

    A file that fails to load (corrupt, truncated, or -- unexpectedly --
    not actually a Processed Wind Profile file) is skipped with a
    logged warning rather than aborting the whole series, so one bad
    file out of many doesn't blank the whole plot.

    :param paths: iterable of file paths.
    :param intensity_min: optional intensity filter threshold, applied \
        to each profile before interpolation; see :func:`load_profile`.
    :raises ValueError: if ``paths`` is empty, or none of them could \
        be loaded.
    """
    profiles: List[ProfileData] = []
    for p in paths:
        try:
            profiles.append(load_profile(p, intensity_min=intensity_min))
        except (IOError, ValueError) as exc:
            logger.warning('skipping %s: %s', p, exc)
    if not profiles:
        raise ValueError('no files could be loaded to build a profile series from')
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
        filter_missing=[p.timestamp for p in profiles if p.filter_missing],
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


def load_scan_history(paths: Iterable,
                      intensity_min: Optional[float] = None
                      ) -> ScanHistoryData:
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

    A file that fails to load (corrupt, truncated beyond what
    :meth:`hpl.DataFile._get_datablock`'s own truncation handling can
    recover, or not actually a regular scan file) is skipped with a
    logged warning rather than aborting the whole history, so one bad
    file among many doesn't blank the whole plot.

    :param paths: iterable of file paths (regular scan ``.hpl`` files).
    :param intensity_min: optional intensity filter threshold: gates \
        whose intensity is below it are dropped (both their intensity \
        and their beta) *before* time binning, so a bin that only held \
        such gates ends up ``nan``.
    :raises ValueError: if none of the files could be loaded, or none \
        of the ones that did contain any ray data.
    """
    files = []
    for p in paths:
        try:
            files.append(hpl.DataFile(str(p)))
        except (IOError, ValueError) as exc:
            logger.warning('skipping %s: %s', p, exc)
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
            if intensity_min is not None:
                beta = _mask_below(beta, inten, intensity_min)
                inten = _mask_below(inten, inten, intensity_min)
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
    #: intensity (SNR + 1) of every point, unfiltered
    intensity: Optional[np.ndarray] = None


def load_rhi_cross_section(path, intensity_min: Optional[float] = None
                           ) -> RhiCrossSectionData:
    """
    Load a single RHI scan file and compute its distance/height cross
    section: horizontal distance and height for every range gate of
    every ray, from that ray's own azimuth, elevation, pitch and roll
    (:func:`_tilt_corrected_unit_components`), paired with that gate's
    radial velocity (Doppler) and backscatter (beta).

    :param path: path to an ``RHI_*.hpl`` file.
    :param intensity_min: optional intensity filter threshold: \
        velocity and beta of points whose intensity is below it are \
        set to ``nan`` (the points keep their position, so the arrays \
        stay aligned).
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
    intensities: List[np.ndarray] = []
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
        intensities.append(
            pd.to_numeric(r.data['Intensity'], errors='coerce').to_numpy())

    def _cat(parts: List[np.ndarray]) -> np.ndarray:
        return np.concatenate(parts).astype(float) if parts else np.array([])

    intensity = _cat(intensities)
    return RhiCrossSectionData(
        timestamp=f.timestamp,
        distance=_cat(distances),
        height=_cat(heights),
        velocity=_mask_below(_cat(velocities), intensity, intensity_min),
        beta=_mask_below(_cat(betas), intensity, intensity_min),
        path=path,
        intensity=intensity,
    )
