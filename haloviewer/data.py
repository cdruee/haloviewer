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

from . import cache as _cache
from . import hpl

logger = logging.getLogger(__name__)

__all__ = [
    'DEFAULT_INTENSITY_FILTER', 'resolve_intensity_filter',
    'find_wind_profile_file', 'load_wind_profile_intensity',
    'ProfileData', 'ProfileSeriesData',
    'load_profile', 'load_profile_series',
    'ScanHistoryData', 'load_scan_history',
    'ScanPointsData', 'load_scan_points',
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
DEFAULT_INTENSITY_FILTER: float = 1.018

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


def _f32_threshold(intensity_min: Optional[float]) -> Optional[float]:
    """The filter threshold for intensities held as ``float32`` (see
    :class:`haloviewer.hpl.ScanArrays`), rounded the same way, so that
    a value exactly equal to the threshold in the file (e.g. ``1.050000``
    with a threshold of 1.05) still counts as "not below" it."""
    if intensity_min is None:
        return None
    return float(np.float32(intensity_min))


@dataclass
class ProfileData:
    """One "Processed Wind Profile" snapshot: height, speed and
    direction arrays for a single timestamp."""
    timestamp: pd.Timestamp
    height: np.ndarray
    speed: np.ndarray
    direction: np.ndarray
    path: Path
    #: Intensity (SNR + 1) of each level, taken from the same-numbered
    #: range gate of the matching ``Wind_Profile`` scan file (see
    #: :func:`load_wind_profile_intensity`).
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


def load_wind_profile_intensity(path, n_levels: int) -> np.ndarray:
    """
    Intensity (SNR + 1) of a ``Wind_Profile`` scan file, one value per
    level of the matching processed profile.

    The processed profile's levels correspond one-to-one to the scan's
    range gates: level *n* (the *n*-th data row of the
    ``Processed_Wind_Profile`` file, in file order) belongs to gate *n*
    (``Range Gate`` column) of the ``Wind_Profile`` scan. The two files
    just express the vertical coordinate differently -- height in
    metres there, gate number here -- so no geometric conversion is
    done. The intensity of gate *n* is averaged over all rays (beams)
    of the scan.

    If the scan has fewer gates than the profile has levels, the extra
    levels get ``nan`` (and are therefore not filtered); extra gates are
    ignored. A mismatch is logged as a warning.

    :param path: path to the ``Wind_Profile`` file.
    :param n_levels: number of levels in the processed profile.
    :returns: array of length ``n_levels``.
    :raises ValueError: if the file contains no usable ray data.
    """
    a = _cache.read_scan_arrays(path)
    if a.n_rays == 0 or a.n_gates == 0:
        raise ValueError(f'{path} contains no scan (ray) data')

    gate = a.gate.astype(float)
    inten = a.intensity.astype(float)
    ok = np.isfinite(gate) & (gate >= 0) & (gate < n_levels)
    rows = np.full((a.n_rays, n_levels), np.nan)
    ray_idx = np.nonzero(ok)[0]
    rows[ray_idx, gate[ok].astype(int)] = inten[ok]
    per_ray = list(rows)
    finite = np.isfinite(gate)
    n_gates = (int(np.nanmax(gate)) + 1 if finite.any() else a.n_gates)
    if not per_ray:
        raise ValueError(f'{path} contains no usable intensity data')
    if n_gates != n_levels:
        logger.warning('%s: %d range gates but the processed profile has %d '
                       'levels -- matching level n to gate n for the first '
                       '%d only', Path(path).name, n_gates, n_levels,
                       min(n_gates, n_levels))
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)  # all-nan levels
        return np.nanmean(np.vstack(per_ray), axis=0)


def load_profile(path, intensity_min: Optional[float] = None) -> ProfileData:
    """
    Load a single ``Processed_Wind_Profile_*.hpl`` file.

    :param path: path to the file.
    :param intensity_min: optional intensity filter threshold: speed \
        and direction at level *n* are set to ``nan`` where gate *n* of \
        the matching ``Wind_Profile`` scan \
        (:func:`find_wind_profile_file`) has a (ray-averaged) intensity \
        below this value. If that scan file doesn't exist \
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
    # keep file order until the intensity is attached: level n of the
    # file belongs to gate n of the matching Wind_Profile scan
    df = d.profile.copy()
    filter_missing = False
    if intensity_min is not None:
        wp_path = find_wind_profile_file(path)
        if wp_path is None:
            logger.warning('%s: no matching Wind_Profile file found -- '
                           'intensity filter not applied', path.name)
            filter_missing = True
        else:
            try:
                df['intensity'] = load_wind_profile_intensity(wp_path,
                                                              len(df))
            except (IOError, ValueError) as exc:
                logger.warning('%s: could not read intensity from %s (%s) '
                               '-- intensity filter not applied',
                               path.name, wp_path.name, exc)
                filter_missing = True
    df = df.sort_index()
    prof = ProfileData(
        timestamp=d.timestamp,
        height=df.index.to_numpy(dtype=float),
        speed=df['speed'].to_numpy(dtype=float),
        direction=df['direction'].to_numpy(dtype=float),
        path=path,
        filter_missing=filter_missing,
    )
    if 'intensity' not in df.columns:
        return prof
    prof.intensity = df['intensity'].to_numpy(dtype=float)
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
# "history", and a single scan's points in 3-D for the RHI/PPI views.
# =========================================================================


def _gate_range(gate_index, gate_length: float,
                gate_points: float = 1.0) -> np.ndarray:
    """
    Along-beam range (m) of the centre of range gate(s) ``gate_index``,
    following the Halo convention stated in the ``.hpl`` header::

        range = gate_length / 2 + gate_index * gate_length / gate_points

    with ``gate_length`` the ``Range gate length (m)`` and
    ``gate_points`` the ``Gate length (pts)`` header value. Gates are
    ``gate_length / gate_points`` metres apart, i.e. neighbouring gates
    overlap when ``gate_points > 1``; gate 0's centre sits half a gate
    length out.
    """
    if not gate_points or gate_points <= 0:
        gate_points = 1.0
    idx = np.asarray(gate_index, dtype=float)
    return gate_length / 2.0 + idx * (gate_length / gate_points)


def _gate_distance_axis(gate_length: float, n_gates: int,
                        gate_points: float = 1.0) -> np.ndarray:
    """Range-gate-centre distance (m) of gates ``0 .. n_gates - 1``; see
    :func:`_gate_range` for the formula."""
    return _gate_range(np.arange(n_gates), gate_length, gate_points)


def _header_gate_geometry(header) -> Tuple[Optional[float], float]:
    """``(gate_length_m, gate_points)`` from a parsed header:
    ``Range gate length (m)`` (``None`` if missing) and
    ``Gate length (pts)`` (``1`` if missing or unusable, i.e. gates
    spaced one full gate length apart)."""
    if not header:
        return None, 1.0
    gate_length = None
    try:
        gate_length = float(header.get('gatelength')) or None
    except (TypeError, ValueError):
        pass
    try:
        gate_points = float(header.get('gatepoints'))
        if not gate_points > 0:
            raise ValueError
    except (TypeError, ValueError):
        gate_points = 1.0
    return gate_length, gate_points


def _ray_gate_index(ray) -> np.ndarray:
    """Gate indices of one ray's rows: its ``Range Gate`` column where
    present and usable, else ``0 .. n - 1``."""
    n = len(ray.data.index)
    if 'Range Gate' in ray.data.columns:
        idx = pd.to_numeric(ray.data['Range Gate'], errors='coerce').to_numpy()
        if np.isfinite(idx).all():
            return idx.astype(float)
    return np.arange(n, dtype=float)


def _gate_index_array(gate: np.ndarray) -> np.ndarray:
    """Per-ray gate indices for a ``(n_rays, n_gates)`` ``Range Gate``
    array: a ray's own values where all of them are usable, else
    ``0 .. n_gates - 1`` (as :func:`_ray_gate_index` does per ray)."""
    gate = np.asarray(gate, dtype=float)
    out = np.broadcast_to(np.arange(gate.shape[1], dtype=float),
                          gate.shape).copy()
    good = np.isfinite(gate).all(axis=1)
    out[good] = gate[good]
    return out


def _tilt_corrected_unit_vectors(azimuth, elevation, pitch, roll
                                 ) -> Tuple[np.ndarray, np.ndarray,
                                            np.ndarray]:
    """Array version of :func:`_tilt_corrected_unit_vector` (one
    ``(east, north, up)`` triple per ray); ``nan`` angles count as 0,
    as ``None`` does there."""
    def rad(v):
        v = np.asarray(v, dtype=float)
        return np.deg2rad(np.where(np.isfinite(v), v, 0.0))
    az, el, p, r = rad(azimuth), rad(elevation), rad(pitch), rad(roll)
    x0 = np.cos(el) * np.sin(az)
    y0 = np.cos(el) * np.cos(az)
    z0 = np.sin(el)
    x1 = x0
    y1 = y0 * np.cos(p) - z0 * np.sin(p)
    z1 = y0 * np.sin(p) + z0 * np.cos(p)
    x2 = x1 * np.cos(r) + z1 * np.sin(r)
    y2 = y1
    z2 = -x1 * np.sin(r) + z1 * np.cos(r)
    return x2, y2, z2


def _tilt_corrected_unit_vector(
        azimuth: float, elevation: float,
        pitch: Optional[float], roll: Optional[float]
        ) -> Tuple[float, float, float]:
    """
    Convert one ray's nominal (azimuth, elevation) pointing direction
    plus the instrument's own (pitch, roll) tilt into a true, level-
    frame unit pointing vector ``(east, north, up)``.

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

    :returns: ``(east, north, up)`` components of the corrected unit \
        vector; multiply by the along-beam range to get the point's \
        position relative to the instrument.
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

    return float(x2), float(y2), float(z2)


def _tilt_corrected_unit_components(
        azimuth: float, elevation: float,
        pitch: Optional[float], roll: Optional[float]) -> Tuple[float, float]:
    """
    Horizontal and vertical components of
    :func:`_tilt_corrected_unit_vector`.

    :returns: ``(horizontal, vertical)`` components of the corrected \
        unit vector (``horizontal**2 + vertical**2 == 1``).
    """
    east, north, up = _tilt_corrected_unit_vector(
        azimuth, elevation, pitch, roll)
    return float(np.hypot(east, north)), float(up)


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
    taken directly from the range gates (gate index, and the first
    file's ``Range gate length (m)`` and ``Gate length (pts)`` -- see
    :func:`_gate_range`).
    A bin with no ray falling into it is left as ``nan``; the plotting
    layer then leaves the corresponding pixels empty (background)
    rather than interpolating across the gap.

    A file that fails to load (corrupt, truncated beyond what
    :func:`hpl.read_scan_arrays`'s own truncation handling can
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
    scans = []
    for p in paths:
        try:
            scans.append(_cache.read_scan_arrays(p))
        except (IOError, ValueError) as exc:
            logger.warning('skipping %s: %s', p, exc)
    scans = [a for a in scans if a.n_rays and a.n_gates]
    if not scans:
        raise ValueError('no scan (ray) data found in the given files')

    gate_length, gate_points = 1.0, 1.0
    for a in scans:
        gl, gp = _header_gate_geometry(a.header)
        if gl:
            gate_length, gate_points = gl, gp
            break
    n_gates = max(a.n_gates for a in scans)
    distance = _gate_distance_axis(gate_length, n_gates, gate_points)

    all_times = np.concatenate([a.time for a in scans])
    all_times = all_times[~np.isnat(all_times)]
    if not all_times.size:
        raise ValueError('no scan (ray) data found in the given files')
    t0 = pd.Timestamp(all_times.min())
    span = (pd.Timestamp(all_times.max()) - t0).total_seconds()
    bin_s = _pick_history_bin_seconds(span)
    n_bins = max(1, int(span // bin_s) + 1)

    # accumulated as (bin, gate) and transposed at the end
    sum_i = np.zeros((n_bins, n_gates))
    cnt_i = np.zeros((n_bins, n_gates))
    sum_b = np.zeros((n_bins, n_gates))
    cnt_b = np.zeros((n_bins, n_gates))

    t0_ns = t0.to_datetime64().astype('datetime64[ns]')
    for a in scans:
        valid = ~np.isnat(a.time)
        if not valid.any():
            continue
        ng = a.n_gates
        secs = (a.time[valid] - t0_ns).astype('int64') / 1e9
        j = np.clip((secs // bin_s).astype(int), 0, n_bins - 1)
        inten = a.intensity[valid].astype(float)
        beta = a.beta[valid].astype(float)
        if intensity_min is not None:
            thr = _f32_threshold(intensity_min)
            beta = _mask_below(beta, inten, thr)
            inten = _mask_below(inten, inten, thr)
        ok_i = np.isfinite(inten)
        ok_b = np.isfinite(beta)
        # sum all rays of the same bin at once (much faster than
        # np.add.at): sort by bin, then reduce each run of equal bins
        order = np.argsort(j, kind='stable')
        bins, starts = np.unique(j[order], return_index=True)
        for acc, vals in ((sum_i, np.where(ok_i, inten, 0.0)),
                          (cnt_i, ok_i.astype(float)),
                          (sum_b, np.where(ok_b, beta, 0.0)),
                          (cnt_b, ok_b.astype(float))):
            acc[bins, :ng] += np.add.reduceat(vals[order], starts, axis=0)
    sum_i, cnt_i, sum_b, cnt_b = sum_i.T, cnt_i.T, sum_b.T, cnt_b.T

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
class ScanPointsData:
    """One regular scan's (gate, ray) points in a level, instrument-
    centred Cartesian frame, rotated so that its horizontal axes follow
    the scan's own first ray, paired with each point's radial velocity
    (Doppler) and backscatter (beta). This is what the RHI and PPI
    views project from: RHI shows ``(x, z)``, PPI shows ``(x, y)``.

    The frame:

    * ``x`` -- horizontal, pointing along :attr:`azimuth0`, the
      (recorded) azimuth of the scan's first ray. A point on a ray
      whose azimuth is more than 90 degrees away from :attr:`azimuth0`
      (e.g. the far side of an over-the-top RHI, or the back half of a
      VAD cone) therefore has a *negative* ``x``.
    * ``y`` -- horizontal, 90 degrees counter-clockwise from ``x``
      seen from above (i.e. towards azimuth ``azimuth0 - 90``), so
      ``(x, y, z)`` is right-handed and a PPI plot is an ordinary
      map view rotated so that ``azimuth0`` points to the right.
    * ``z`` -- height above the instrument.

    Each ray's own azimuth, elevation, pitch and roll give its pointing
    vector (:func:`_tilt_corrected_unit_vector`); points from different
    rays don't share a common grid, so this is plotted as individual
    points (or, optionally, inverse-distance-weighted filled -- see
    :mod:`haloviewer.plotting`), not gridded.
    """
    timestamp: pd.Timestamp
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    velocity: np.ndarray
    beta: np.ndarray
    path: Path
    #: azimuth (degrees, clockwise from north) the ``x`` axis points to
    azimuth0: float = 0.0
    #: intensity (SNR + 1) of every point, unfiltered
    intensity: Optional[np.ndarray] = None


def load_scan_points(path, intensity_min: Optional[float] = None
                     ) -> ScanPointsData:
    """
    Load a single regular scan file (VAD, Stare, RHI or Wind_Profile)
    and compute every (gate, ray) point's position in the frame
    described in :class:`ScanPointsData`: from that ray's own azimuth,
    elevation, pitch and roll (:func:`_tilt_corrected_unit_vector`)
    and the gate's centre range (:func:`_gate_range`), rotated
    so that ``x`` points along the first ray's azimuth. Each point is
    paired with its radial velocity (Doppler) and backscatter (beta).

    :param path: path to a regular scan ``.hpl`` file.
    :param intensity_min: optional intensity filter threshold: \
        velocity and beta of points whose intensity is below it are \
        set to ``nan`` (the points keep their position, so the arrays \
        stay aligned).
    :raises ValueError: if the file contains no ray (scan) data.
    """
    path = Path(path)
    a = _cache.read_scan_arrays(path)
    if a.n_rays == 0 or a.n_gates == 0:
        raise ValueError(f'{path} contains no scan (ray) data')
    gate_length, gate_points = _header_gate_geometry(a.header)
    gate_length = gate_length or 1.0

    azimuth0 = (float(a.azimuth[0]) if np.isfinite(a.azimuth[0])
                else 0.0)
    a0 = np.deg2rad(azimuth0)
    # unit vectors of the x (towards azimuth0) and y (towards
    # azimuth0 - 90) axes in east/north components
    ex = (np.sin(a0), np.cos(a0))
    ey = (-np.cos(a0), np.sin(a0))

    # (n_rays, n_gates) ranges, (n_rays, 1) pointing vectors; flattened
    # ray by ray, gate by gate
    gate_range = _gate_range(_gate_index_array(a.gate), gate_length,
                             gate_points)
    east, north, up = (v[:, None] for v in _tilt_corrected_unit_vectors(
        a.azimuth, a.elevation, a.pitch, a.roll))
    x = (gate_range * (east * ex[0] + north * ex[1])).ravel()
    y = (gate_range * (east * ey[0] + north * ey[1])).ravel()
    z = (gate_range * up).ravel()

    intensity = a.intensity.astype(float).ravel()
    return ScanPointsData(
        timestamp=a.timestamp,
        x=x, y=y, z=z,
        velocity=_mask_below(a.doppler.astype(float).ravel(), intensity,
                             _f32_threshold(intensity_min)),
        beta=_mask_below(a.beta.astype(float).ravel(), intensity,
                         _f32_threshold(intensity_min)),
        path=path,
        azimuth0=azimuth0,
        intensity=intensity,
    )
