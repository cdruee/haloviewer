# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5
"""Tests for the single-scan RHI/PPI views and their Fill option
(data geometry, plotting, api/cli, GUI)."""

import os
import shutil
import warnings
from pathlib import Path

import numpy as np
import pytest
import matplotlib
matplotlib.use("Agg")
from matplotlib.image import AxesImage

from haloviewer import api, cli, data, plotting
from haloviewer.scan import scan_directory

from test_filter import _GATE_LENGTH, _write_scan

_DATA_DIR = Path(__file__).parent / "data"
_RHI_FILE = _DATA_DIR / "RHI_77_20260921_000812.hpl"
_VAD_FILE = _DATA_DIR / "VAD_77_20260921_000721.hpl"

_N = 5
_ONES = np.full(_N, 1.5)
# _write_scan writes "Gate length (pts): 10": gates are 30/10 = 3 m apart
_RANGE = _GATE_LENGTH / 2 + np.arange(_N) * _GATE_LENGTH / 10


def _star_file(tmp_path, azimuths, elevation=0.0, name="RHI"):
    """A scan whose rays point along ``azimuths`` at one elevation."""
    p = tmp_path / f"{name}_77_20260919_000000.hpl"
    _write_scan(p, f"{'RHI' if name == 'RHI' else 'VAD'} - csm",
                [(az, elevation, _ONES) for az in azimuths])
    return p


# -- geometry ------------------------------------------------------------

def test_x_axis_follows_first_ray_and_goes_negative_behind(tmp_path):
    # first ray east (90); then west (270), north (0), south (180)
    pts = data.load_scan_points(_star_file(tmp_path, [90, 270, 0, 180]))
    assert pts.azimuth0 == 90.0
    x = pts.x.reshape(4, _N)
    y = pts.y.reshape(4, _N)
    z = pts.z.reshape(4, _N)
    assert np.allclose(x[0], _RANGE) and np.allclose(y[0], 0, atol=1e-9)
    # opposite azimuth -> negative distance
    assert np.allclose(x[1], -_RANGE) and np.allclose(y[1], 0, atol=1e-9)
    # +-90 degrees -> on the y axis; y points to azimuth0 - 90 (north)
    assert np.allclose(x[2], 0, atol=1e-9) and np.allclose(y[2], _RANGE)
    assert np.allclose(x[3], 0, atol=1e-9) and np.allclose(y[3], -_RANGE)
    assert np.allclose(z, 0, atol=1e-9)


def test_over_the_top_rhi_elevation(tmp_path):
    p = tmp_path / "RHI_77_20260919_000000.hpl"
    # elevations past zenith on the same azimuth: 150 deg points back
    _write_scan(p, "RHI - csm", [(30.0, el, _ONES) for el in (30, 90, 150)])
    pts = data.load_scan_points(p)
    x = pts.x.reshape(3, _N)
    z = pts.z.reshape(3, _N)
    assert np.all(x[0] > 0)
    assert np.allclose(x[1], 0, atol=1e-9) and np.allclose(z[1], _RANGE)
    assert np.all(x[2] < 0)
    assert np.allclose(x[2], -x[0]) and np.allclose(z[2], z[0])


def test_rhi_fixture_geometry():
    pts = data.load_scan_points(_RHI_FILE)
    assert pts.azimuth0 == 255.0
    assert pts.x.shape == pts.y.shape == pts.z.shape == pts.velocity.shape
    # all rays share the first ray's azimuth: in front, in the x/z plane
    assert np.all(pts.x > 0)
    assert np.nanmax(np.abs(pts.y)) < 1.0      # only the tilt correction
    # 18 m gates, 6 pts, 20 gates: 9 + i * 3 m -> 9 .. 66 m
    r = np.sqrt(pts.x ** 2 + pts.y ** 2 + pts.z ** 2)
    assert np.nanmin(r) == pytest.approx(9.0)
    assert np.nanmax(r) == pytest.approx(9.0 + 19 * 3.0)


def test_load_scan_points_rejects_empty(tmp_path):
    with pytest.raises(ValueError):
        data.load_scan_points(
            _DATA_DIR / "VAD_77_20260921_000721_empty.hpl")


# -- plotting ------------------------------------------------------------

def _positions(fig, axes):
    fig.canvas.draw()
    return [a.get_position().bounds for a in axes]


def test_plot_rhi_scatter_positions_and_limits():
    pts = data.load_scan_points(_RHI_FILE)
    fig, axes = plotting.create_scan_pair_figure()
    plotting.plot_rhi(*axes, pts.x, pts.z, pts.velocity, pts.beta,
                      distance_xlim=(-100.0, 400.0),
                      height_ylim=(0.0, 300.0), azimuth0=pts.azimuth0)
    ax_vel, ax_beta = axes[:2]
    offsets = ax_vel.collections[0].get_offsets()
    assert np.allclose(offsets[:, 0], pts.x) and np.allclose(offsets[:, 1], pts.z)
    assert ax_vel.get_xlim() == (-100.0, 400.0)
    assert ax_beta.get_ylim() == (0.0, 300.0)   # shared
    assert "255.0" in ax_vel.get_xlabel()
    # one row, two columns: side by side at the same height
    b_vel, b_beta = _positions(fig, axes)[:2]
    assert b_vel[1] == pytest.approx(b_beta[1]) and b_beta[0] > b_vel[0]


def test_plot_ppi_is_square_and_symmetric():
    pts = data.load_scan_points(_VAD_FILE)
    fig, axes = plotting.create_scan_pair_figure()
    plotting.plot_ppi(*axes, pts.x, pts.y, pts.velocity, pts.beta,
                      distance_max=250.0, azimuth0=pts.azimuth0)
    ax = axes[0]
    assert ax.get_xlim() == (-250.0, 250.0)
    assert ax.get_ylim() == (-250.0, 250.0)
    assert ax.get_aspect() == 1.0
    offsets = ax.collections[0].get_offsets()
    assert np.allclose(offsets[:, 1], pts.y)


def test_scan_pair_geometry_is_stable_across_redraws():
    pts = data.load_scan_points(_RHI_FILE)
    fig, axes = plotting.create_scan_pair_figure()
    plotting.plot_rhi(*axes, pts.x, pts.z, pts.velocity, pts.beta)
    before = _positions(fig, axes)
    plotting.plot_rhi(*axes, pts.x, pts.z, pts.velocity, pts.beta,
                      fill=True, title="again")
    assert _positions(fig, axes) == before


def test_fill_is_inverse_distance_weighted_and_clipped_to_hull():
    # four points at the corners of a square
    px = np.array([0.0, 10.0, 0.0, 10.0])
    py = np.array([0.0, 0.0, 10.0, 10.0])
    vals = np.array([1.0, 2.0, 3.0, 4.0])
    extent, (grid,) = plotting._idw_fill(px, py, [vals], (0, 10),
                                          (0, 10), n=10)
    assert extent == (0.0, 10.0, 0.0, 10.0)
    # cell (0, 0) is centred at (0.5, 0.5): check against 1/d**2 weights
    d = np.hypot(px - 0.5, py - 0.5)
    w = 1 / d ** 2
    assert grid[0, 0] == pytest.approx((w * vals).sum() / w.sum())
    # dominated by the nearest corner, but blended with the others
    assert 1.0 < grid[0, 0] < 1.5
    assert 3.5 < grid[-1, -1] < 4.0
    # the single cell of a 1x1 grid sits at the centre, equidistant from
    # all four corners -> plain mean
    extent, (g1,) = plotting._idw_fill(px, py, [vals], (0, 10), (0, 10), n=1)
    assert g1[0, 0] == pytest.approx(vals.mean())
    assert np.all(np.isfinite(grid))
    assert grid.min() >= 1.0 and grid.max() <= 4.0

    # a triangle: the cells outside it stay blank
    extent, (grid,) = plotting._idw_fill(
        px[:3], py[:3], [vals[:3]], (0, 10), (0, 10), n=10)
    assert np.isnan(grid[-1, -1]) and np.isfinite(grid[0, 0])


def test_fill_exact_hit_takes_point_value():
    px = np.array([0.5, 9.5, 0.5, 9.5, 5.0])
    py = np.array([0.5, 0.5, 9.5, 9.5, 5.0])
    vals = np.array([1.0, 2.0, 3.0, 4.0, 10.0])
    # n=10 over (0.5..9.5): cell centres at 0.95, 1.85, ... -- instead use
    # an odd grid so a cell centre lands exactly on (5, 5)
    extent, (grid,) = plotting._idw_fill(px, py, [vals], (0.5, 9.5),
                                          (0.5, 9.5), n=9)
    assert grid[4, 4] == pytest.approx(10.0)


def test_fill_keeps_filtered_points_blank():
    px = np.array([0.0, 10.0, 0.0, 10.0])
    py = np.array([0.0, 0.0, 10.0, 10.0])
    vals = np.array([1.0, np.nan, 3.0, 4.0])
    _, (grid,) = plotting._idw_fill(px, py, [vals], (0, 10), (0, 10),
                                     n=10)
    assert np.isnan(grid[0, -1])          # nearest point was filtered out
    # elsewhere the filtered point is simply left out of the average
    d = np.hypot(px - 0.5, py - 0.5)
    w = np.where(np.isfinite(vals), 1 / d ** 2, 0)
    assert grid[0, 0] == pytest.approx(
        (w * np.nan_to_num(vals)).sum() / w.sum())


def test_fill_degenerate_points_fall_back_to_scatter():
    # a Stare-like scan: every point on one line -> nothing to fill
    x = np.linspace(0, 100, 10)
    assert plotting._idw_fill(x, np.zeros(10), [x], (0, 100),
                                   (-1, 1)) is None
    fig, axes = plotting.create_scan_pair_figure()
    plotting.plot_ppi(*axes, x, np.zeros(10), x, x, fill=True)
    assert axes[0].collections and not axes[0].images


def test_plot_ppi_fill_draws_images():
    pts = data.load_scan_points(_VAD_FILE)
    fig, axes = plotting.create_scan_pair_figure()
    plotting.plot_ppi(*axes, pts.x, pts.y, pts.velocity, pts.beta,
                      fill=True)
    for ax in axes[:2]:
        assert len(ax.images) == 1 and isinstance(ax.images[0], AxesImage)
        assert not ax.collections
        grid = ax.images[0].get_array()
        assert np.isfinite(grid).any() and np.ma.getmaskarray(grid).any() \
            or np.isnan(np.asarray(grid, dtype=float)).any()


# -- api / cli -----------------------------------------------------------

def test_api_rhi_file_defaults_to_rhi_view(tmp_path):
    fig = api.plot(_RHI_FILE, output=str(tmp_path / "rhi.png"))
    assert len(fig.axes) == 4
    assert fig.axes[0].get_ylabel() == "Height (m)"
    assert fig._suptitle.get_text().startswith("RHI  2026-09-21")
    assert plotting.scan_view_title("VAD", "ppi") == "VAD (PPI)"


def test_api_rhi_kind_has_no_profile_mode(tmp_path):
    with pytest.raises(ValueError, match="profile"):
        api.plot_file(_RHI_FILE, mode="profile")


def test_api_processed_profile_has_no_rhi_ppi(proc_tree):
    entry = scan_directory(proc_tree).files("Processed_Wind_Profile")[0]
    for mode in ("rhi", "ppi"):
        with pytest.raises(ValueError, match=mode):
            api.plot_file(entry.path, mode=mode)


def test_api_ppi_uses_distance_max_and_warns_on_height(tmp_path):
    with pytest.warns(UserWarning, match="height"):
        fig = api.plot(_VAD_FILE, mode="ppi", distance=(0, 120),
                       height=(0, 100), output=str(tmp_path / "p.png"))
    assert fig.axes[0].get_xlim() == (-120.0, 120.0)
    assert fig.axes[0].get_ylim() == (-120.0, 120.0)


def test_api_fill_warns_outside_rhi_ppi(tmp_path):
    with pytest.warns(UserWarning, match="fill"):
        api.plot_file(_VAD_FILE, fill=True, output=str(tmp_path / "h.png"))


def test_api_fill_no_warning_for_rhi(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        fig = api.plot(_RHI_FILE, mode="rhi", fill=True,
                       output=str(tmp_path / "f.png"))
    assert fig.axes[0].images


def test_api_single_scan_mode_picks_latest_of_many(tmp_path):
    for hhmm in ("0000", "0010"):
        dst = tmp_path / f"VAD_77_20260921_{hhmm}00.hpl"
        shutil.copy(_VAD_FILE, dst)
    fig = api.plot(tmp_path, mode="ppi", output=str(tmp_path / "x.png"))
    assert fig.axes[0].get_aspect() == 1.0


def test_cli_modes_rhi_ppi_and_fill(tmp_path):
    for mode in ("rhi", "ppi"):
        out = tmp_path / f"{mode}.png"
        rc = cli.main([str(_VAD_FILE), "--mode", mode, "--fill",
                       "-p", str(out)])
        assert rc == 0 and out.exists()
    args = cli.build_parser().parse_args(["x.hpl"])
    assert args.fill is False


# -- GUI -----------------------------------------------------------------

@pytest.fixture
def gui_app(tmp_path):
    tk = pytest.importorskip("tkinter")
    if os.name != "nt" and not os.environ.get("DISPLAY"):
        pytest.skip("no display")
    from haloviewer.gui import HaloViewerApp
    d = tmp_path / "Proc"
    d.mkdir()
    shutil.copy(_RHI_FILE, d)
    shutil.copy(_VAD_FILE, d)
    (d / "Processed_Wind_Profile_77_20260921_000000.hpl").write_text(
        "2\n  9.0 200.0 3.0\n 39.0 210.0 4.0\n")
    root = tk.Tk()
    app = HaloViewerApp(root, initial_dir=str(d))
    yield app
    root.destroy()


def _select(app, kind):
    app.kind_combo.current(app._kind_names.index(kind))
    app._on_kind_select()


def _state(widget):
    return str(widget.cget("state"))


def test_gui_plot_type_availability(gui_app):
    app = gui_app
    _select(app, "RHI")
    assert _state(app.mode_radios["profile"]) == "disabled"
    for m in ("timeseries", "rhi", "ppi"):
        assert _state(app.mode_radios[m]) == "normal"
    assert app.mode_var.get() == "rhi"
    assert app.current_plot_kind == "rhi"

    _select(app, "Processed_Wind_Profile")
    for m in ("rhi", "ppi"):
        assert _state(app.mode_radios[m]) == "disabled"
    assert app.mode_var.get() in ("profile", "timeseries")
    assert _state(app.fill_check) == "disabled"

    _select(app, "VAD")
    for m in ("timeseries", "rhi", "ppi"):
        assert _state(app.mode_radios[m]) == "normal"


def test_gui_ppi_controls_and_fill(gui_app):
    app = gui_app
    _select(app, "VAD")
    app.mode_var.set("ppi")
    app._on_mode_select()
    assert app.current_plot_kind == "ppi"
    assert _state(app.fill_check) == "normal"
    # Height inactive; Distance near end fixed at 0
    assert _state(app.height_ctrl.auto_check) == "disabled"
    assert _state(app.distance_ctrl.auto_check) == "normal"
    app.distance_ctrl.auto_var.set(False)
    app.distance_ctrl._on_auto_toggle()
    assert _state(app.distance_ctrl.bottom_entry) == "disabled"
    assert _state(app.distance_ctrl.top_entry) == "normal"
    app.distance_ctrl.top_var.set("200")
    app._redraw_distance_range()
    ax = app.axes[0]
    assert ax.get_xlim() == (-200.0, 200.0) == ax.get_ylim()

    assert not ax.images
    app.fill_var.set(True)
    app._on_fill_change()
    assert app.axes[0].images and app.axes[1].images

    # back to RHI: bottom editable again, Height active
    app.mode_var.set("rhi")
    app._on_mode_select()
    assert _state(app.height_ctrl.auto_check) == "normal"
    assert _state(app.distance_ctrl.bottom_entry) == "normal"
    assert app.axes[0].images   # Fill still on


def test_beta_colorbar_uses_power_of_ten_label():
    pts = data.load_scan_points(_RHI_FILE)
    fig, axes = plotting.create_scan_pair_figure()
    plotting.plot_rhi(*axes, pts.x, pts.z, pts.velocity, pts.beta)
    fig.canvas.draw()
    cax_beta = axes[3]
    label = cax_beta.get_xlabel()
    n = plotting._power_of_ten(*axes[1].collections[0].get_clim())
    assert label == f'Beta ($10^{{{n}}}$ m⁻¹ sr⁻¹)' and n < 0
    ticks = [t.get_text() for t in cax_beta.get_xticklabels() if t.get_text()]
    assert ticks and all(len(t.strip().lstrip('−-')) <= 4 for t in ticks)
    assert cax_beta.xaxis.get_offset_text().get_text() == ''
    assert plotting._power_of_ten(0, 0) == 0
    assert plotting._power_of_ten(-3e-6, 1.7e-5) == -5


def test_gui_window_title_has_version(gui_app):
    import haloviewer
    title = gui_app.root.title()
    assert title == f"HaloViewer {haloviewer.__version__}"


def test_gui_default_time_preset_is_last_24h(gui_app):
    import pandas as pd
    from haloviewer.gui import _TIME_FMT
    app = gui_app
    _select(app, "VAD")
    assert app.time_preset_var.get() == "24h"
    assert _state(app.start_entry) == "disabled"
    _lo, hi = app.scan_result.time_range("VAD")
    end = pd.Timestamp(app.end_var.get())
    start = pd.Timestamp(app.start_var.get())
    assert end == pd.Timestamp(hi.strftime(_TIME_FMT))
    assert end - start == pd.Timedelta(hours=24)

    # Custom hands Start back to the user; a kind change resets it to
    # the start of the data
    app.time_preset_var.set("custom")
    app._on_time_preset_change()
    assert _state(app.start_entry) == "normal"
    _select(app, "RHI")
    lo, _hi = app.scan_result.time_range("RHI")
    assert app.start_var.get() == lo.strftime(_TIME_FMT)
