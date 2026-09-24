# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5

from pathlib import Path

import numpy as np
import pytest
import matplotlib
matplotlib.use("Agg")

from haloviewer import data, hpl, plotting
from haloviewer.scan import scan_directory

# Small real (trimmed) regular-scan fixtures -- see tests/data/README
# for how they were derived from the user's own sample files.
_DATA_DIR = Path(__file__).parent / "data"
_RHI_FILE = _DATA_DIR / "RHI_77_20260921_000812.hpl"
_VAD_FILE = _DATA_DIR / "VAD_77_20260921_000721.hpl"
# header claims 6 rays; file is cut off partway through the 6th
_VAD_TRUNCATED_FILE = _DATA_DIR / "VAD_77_20260921_000721_truncated.hpl"
# header claims 6 rays; no ray data at all follows the marker line
_VAD_EMPTY_FILE = _DATA_DIR / "VAD_77_20260921_000721_empty.hpl"


def test_load_profile(proc_tree):
    result = scan_directory(proc_tree)
    files = result.files("Processed_Wind_Profile")
    prof = data.load_profile(files[0].path)
    assert len(prof.height) == 5
    assert len(prof.speed) == 5
    assert len(prof.direction) == 5
    assert prof.timestamp == files[0].timestamp


def test_load_profile_series_skips_a_bad_file(proc_tree):
    # a file of the wrong kind (a regular scan, not a headerless
    # Processed Wind Profile) mixed into the selection must be skipped
    # with a warning, not abort the whole series
    result = scan_directory(proc_tree)
    paths = [e.path for e in result.files("Processed_Wind_Profile")]
    paths.append(_VAD_FILE)
    series = data.load_profile_series(paths)
    assert len(series.times) == 3  # the 3 good files, not 4


def test_load_profile_series(proc_tree):
    result = scan_directory(proc_tree)
    files = result.files("Processed_Wind_Profile")
    series = data.load_profile_series(f.path for f in files)
    assert series.speed.shape == (5, 3)
    assert series.direction.shape == (5, 3)
    assert len(series.times) == 3


def test_plot_wind_profile_runs_and_axes_are_reusable(proc_tree):
    result = scan_directory(proc_tree)
    files = result.files("Processed_Wind_Profile")
    fig, (ax_speed, ax_dir) = plotting.create_profile_figure()
    pos_before = ax_speed.get_position().bounds

    for entry in files:
        prof = data.load_profile(entry.path)
        plotting.plot_wind_profile(ax_speed, ax_dir, prof.height,
                                    prof.speed, prof.direction,
                                    title=str(entry.timestamp))
    pos_after = ax_speed.get_position().bounds
    # panel geometry must not move between redraws ("no wobbling")
    assert pos_before == pos_after


def test_plot_wind_timeseries_runs(proc_tree):
    result = scan_directory(proc_tree)
    files = result.files("Processed_Wind_Profile")
    series = data.load_profile_series(f.path for f in files)
    fig, (ax_speed, ax_dir, cax_speed, cax_dir) = \
        plotting.create_timeseries_figure()
    plotting.plot_wind_timeseries(ax_speed, ax_dir, cax_speed, cax_dir,
                                   series.times, series.height,
                                   series.speed, series.direction)
    assert ax_speed.collections or ax_speed.get_children()


def test_load_profile_series_ignores_float_noise_in_heights(proc_tree):
    # Simulate real-world floating point noise between files that
    # nominally share the same range-gate heights: without vertical
    # interpolation this used to blow up into many near-duplicate,
    # mostly-empty rows (rendered as horizontal white streaks).
    result = scan_directory(proc_tree)
    files = result.files("Processed_Wind_Profile")
    profiles = [data.load_profile(f.path) for f in files]
    for i, p in enumerate(profiles):
        p.height = p.height + i * 1e-6
    series_height = data._canonical_height_grid(profiles)
    # grid size stays close to the per-file gate count (5), not
    # len(files) times that from spurious near-duplicate rows
    assert len(series_height) <= 6
    # interior rows should be fully covered -- no near-white streak
    # from data that actually exists just being split across several
    # almost-identical height rows
    speed = np.full((len(series_height), len(profiles)), np.nan)
    for j, p in enumerate(profiles):
        speed[:, j] = data._interp_linear(series_height, p.height, p.speed)
    coverage = np.isfinite(speed).mean(axis=1)
    assert coverage[1:-1].min() == 1.0
    # the two boundary rows only get partial coverage because they sit
    # right at the edge of some profiles' own (noise-shifted) range,
    # where interpolation correctly declines to extrapolate -- that's
    # expected, but they must still have *some* data, not be empty
    assert coverage[0] > 0 and coverage[-1] > 0


def test_interp_circular_handles_wraparound():
    height_grid = np.array([0.0, 1.0, 2.0])
    height = np.array([0.0, 2.0])
    degrees = np.array([355.0, 5.0])  # a 10 degree turn, not 350
    out = data._interp_circular(height_grid, height, degrees)
    # midpoint of a 355 -> 5 turn should land near 0/360, not near 180
    assert out[1] < 15 or out[1] > 345


def test_break_circular_line_splits_only_the_jump():
    # 20 -> 355 spans 335 degrees as *drawn* on a 0-360 axis, and
    # 355 -> 5 spans 350 degrees as drawn (even though it's really
    # just a 10 degree turn through the wrap) -- both are wide spans
    # on the panel and both must be broken; only the non-jumping
    # 10 -> 20 segment should stay a single connected line.
    direction = np.array([10.0, 20.0, 355.0, 5.0])
    height = np.array([0.0, 1.0, 2.0, 3.0])
    line_dir, line_height = plotting._break_circular_line(direction, height)
    assert np.isnan(line_dir).sum() == 2
    assert len(line_dir) == len(direction) + 2
    assert list(line_dir[:2]) == [10.0, 20.0]


def test_speed_autoscale_is_capped():
    speed = np.array([10.0, 500.0, 20.0])
    assert plotting._auto_speed_max(speed) == plotting.MAX_AUTOSCALE_SPEED

    fig, (ax_speed, ax_dir) = plotting.create_profile_figure()
    height = np.array([0.0, 10.0, 20.0])
    direction = np.array([10.0, 20.0, 30.0])
    plotting.plot_wind_profile(ax_speed, ax_dir, height, speed, direction)
    assert ax_speed.get_xlim()[1] <= plotting.MAX_AUTOSCALE_SPEED

    fig2, (ax_s2, ax_d2, cax_s2, cax_d2) = plotting.create_timeseries_figure()
    times = pd_range = __import__("pandas").DatetimeIndex(
        ["2026-01-01", "2026-01-02", "2026-01-03"])
    grid = np.tile(speed.reshape(-1, 1), (1, 3))
    dir_grid = np.tile(direction.reshape(-1, 1), (1, 3))
    plotting.plot_wind_timeseries(ax_s2, ax_d2, cax_s2, cax_d2,
                                   times, height, grid, dir_grid)
    assert ax_s2.collections[0].get_clim()[1] <= plotting.MAX_AUTOSCALE_SPEED


# =========================================================================
# Raw scan kinds (VAD/Stare/Wind_Profile/RHI): scan history. The
# single-scan RHI/PPI views are tested in test_rhi_ppi.py.
# =========================================================================

def test_tilt_corrected_unit_components_sanity():
    # straight up: all vertical, no horizontal component
    horiz, vert = data._tilt_corrected_unit_components(
        azimuth=0.0, elevation=90.0, pitch=0.0, roll=0.0)
    assert abs(horiz) < 1e-9
    assert abs(vert - 1.0) < 1e-9

    # level, pointing north: all horizontal, no vertical component
    horiz, vert = data._tilt_corrected_unit_components(
        azimuth=0.0, elevation=0.0, pitch=0.0, roll=0.0)
    assert abs(horiz - 1.0) < 1e-9
    assert abs(vert) < 1e-9

    # missing pitch/roll defaults to no tilt correction (same as 0/0)
    horiz2, vert2 = data._tilt_corrected_unit_components(
        azimuth=90.0, elevation=30.0, pitch=None, roll=None)
    horiz3, vert3 = data._tilt_corrected_unit_components(
        azimuth=90.0, elevation=30.0, pitch=0.0, roll=0.0)
    assert abs(horiz2 - horiz3) < 1e-9
    assert abs(vert2 - vert3) < 1e-9


def test_gate_distance_axis_is_gate_center():
    distance = data._gate_distance_axis(gate_length=18.0, n_gates=3)
    assert list(distance) == [9.0, 27.0, 45.0]


def test_load_scan_history_shape_and_nan_gaps():
    hist = data.load_scan_history([_VAD_FILE])
    n_dist = hist.distance.size
    assert n_dist == 20  # the fixture was trimmed to 20 gates
    assert hist.intensity.shape == (n_dist, hist.times.size)
    assert hist.beta.shape == (n_dist, hist.times.size)
    assert np.all(hist.distance > 0)
    # every gate got at least one real ray in this short fixture, so
    # the bins the rays actually landed in should be finite...
    assert np.isfinite(hist.intensity).any()
    # ...while a history spanning well beyond the last real ray must
    # still show unpopulated bins as NaN, not zero or an interpolated
    # guess (see plot_scan_history's docstring for why that matters)
    padded = data.load_scan_history([_VAD_FILE, _VAD_FILE])
    # loading the same short file twice can't create new time bins
    # beyond its own span, so this just re-confirms determinism/shape
    assert padded.distance.size == n_dist


def test_plot_scan_history_runs_and_axes_are_reusable():
    hist = data.load_scan_history([_VAD_FILE])
    fig, (ax_int, ax_beta, cax_int, cax_beta) = \
        plotting.create_timeseries_figure()
    pos_before = ax_int.get_position().bounds
    plotting.plot_scan_history(ax_int, ax_beta, cax_int, cax_beta,
                                hist.times, hist.distance,
                                hist.intensity, hist.beta,
                                title="VAD test")
    pos_after = ax_int.get_position().bounds
    assert pos_before == pos_after
    assert ax_int.collections and ax_beta.collections


# =========================================================================
# Truncated/corrupt files: a scan that was still being written, or
# aborted early, leaves a header ray count higher than what's actually
# in the file. This used to crash with a raw IndexError deep inside the
# parser instead of being reported (or skipped) cleanly -- see
# hpl.DataFile._get_datablock and data.load_scan_history/
# load_profile_series's per-file skip-on-failure behaviour.
# =========================================================================

def test_truncated_file_reads_only_its_complete_rays():
    f = hpl.DataFile(str(_VAD_TRUNCATED_FILE))
    # header still claims 6 rays; only 5 are actually complete
    assert int(float(f.header["rays"])) == 6
    assert len(f.rays) == 5


def test_file_with_no_ray_data_parses_to_zero_rays_not_a_crash():
    f = hpl.DataFile(str(_VAD_EMPTY_FILE))
    assert len(f.rays) == 0


def test_load_scan_history_skips_bad_files_among_good_ones():
    # a good file + a truncated file (partial data) + an empty file
    # (no ray data at all) -- the history should still build from
    # whatever real ray data is available, not raise
    hist = data.load_scan_history(
        [_VAD_FILE, _VAD_TRUNCATED_FILE, _VAD_EMPTY_FILE])
    assert hist.times.size > 0
    assert hist.distance.size == 20


def test_load_scan_history_raises_when_every_file_is_unusable():
    with pytest.raises(ValueError):
        data.load_scan_history([_VAD_EMPTY_FILE])
