import numpy as np
import matplotlib
matplotlib.use("Agg")

from windlidarviewer import data, plotting
from windlidarviewer.scan import scan_directory


def test_load_profile(proc_tree):
    result = scan_directory(proc_tree)
    files = result.files("Processed_Wind_Profile")
    prof = data.load_profile(files[0].path)
    assert len(prof.height) == 5
    assert len(prof.speed) == 5
    assert len(prof.direction) == 5
    assert prof.timestamp == files[0].timestamp


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
