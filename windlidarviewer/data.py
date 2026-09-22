"""
Domain-level data extraction for plotting: turns parsed ``.hpl`` files
(see :mod:`windlidarviewer.hpl`) into plain numpy/pandas structures
that :mod:`windlidarviewer.plotting` can draw, without either module
knowing about the other's concerns (file format vs. rendering).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

import numpy as np
import pandas as pd

from . import hpl

__all__ = [
    'ProfileData', 'ProfileSeriesData',
    'load_profile', 'load_profile_series',
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
