# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5
"""Tests for the optional intensity filter (data/api/cli)."""

import warnings
from pathlib import Path

import numpy as np
import pytest
import matplotlib
matplotlib.use("Agg")

from haloviewer import api, cli, data

_HEIGHTS = np.arange(9.0, 9.0 + 30 * 5, 30.0)   # 9, 39, 69, 99, 129
_GATE_LENGTH = 30.0
_N_GATES = 10
# gates 0-2 have strong signal, the rest are noise
_INTENSITY = np.array([1.5] * 3 + [1.0] * (_N_GATES - 3))
# Wind_Profile scan: one gate per processed level (5), with a pattern
# that only matches if level n is paired with gate n -- converting gate
# range to height would pair them differently
_WP_INTENSITY = np.array([1.5, 1.0, 1.5, 1.0, 1.0])
_WP_BLANK = np.array([False, True, False, True, True])


def _write_scan(path: Path, scan_type: str, rays, start="20260919 00:00:00.00"):
    """Write a minimal regular ``.hpl`` scan file. ``rays`` is a list of
    ``(azimuth, elevation, intensity_per_gate)``."""
    lines = [
        f"Filename:\t{path.name}",
        "System ID:\t77",
        f"Number of gates:\t{len(rays[0][2])}",
        f"Range gate length (m):\t{_GATE_LENGTH}",
        "Gate length (pts):\t10",
        "Pulses/ray:\t10000",
        f"No. of rays in file:\t{len(rays)}",
        f"Scan type:\t{scan_type}",
        "Focus range:\t65535",
        f"Start time:\t{start}",
        "Resolution (m/s):\t0.0382",
        "Range of measurement (center of gate) = (range gate + 0.5) * Gate length",
        "Data line 1: Decimal time (hours)  Azimuth (degrees)  "
        "Elevation (degrees) Pitch (degrees) Roll (degrees)",
        "f9.6,1x,f6.2,1x,f6.2",
        "Data line 2: Range Gate  Doppler (m/s)  Intensity (SNR + 1)  "
        "Beta (m-1 sr-1)",
        "i3,1x,f6.4,1x,f8.6,1x,e12.6 - repeat for no. gates",
        "****",
    ]
    for k, (az, el, inten) in enumerate(rays):
        lines.append(f"{0.001 * (k + 1):.8f} {az:6.2f} {el:6.2f} 0.00 0.00")
        for g, i in enumerate(inten):
            lines.append(f"{g:3d} 1.0000 {i:.6f}  1.000000E-6 ")
    path.write_text("\n".join(lines) + "\n")


def _write_processed(path: Path, speed=5.0, direction=200.0,
                     heights=_HEIGHTS):
    lines = [str(len(heights))]
    lines += [f"{h:6.1f} {direction:7.2f} {speed:6.2f}" for h in heights]
    path.write_text("\n".join(lines) + "\n")


@pytest.fixture
def filter_dir(tmp_path):
    """Two Processed_Wind_Profile files: 00:00 with a matching
    Wind_Profile scan file, 00:15 without one. Plus a VAD and an RHI."""
    d = tmp_path / "20260919"
    d.mkdir()
    _write_processed(d / "Processed_Wind_Profile_77_20260919_000000.hpl")
    _write_processed(d / "Processed_Wind_Profile_77_20260919_001500.hpl")
    _write_scan(d / "Wind_Profile_77_20260919_000000.hpl",
                "Wind profile - stepped",
                [(az, 75.0, _WP_INTENSITY) for az in (0.0, 120.0, 240.0)])
    _write_scan(d / "VAD_77_20260919_000000.hpl", "VAD - stepped",
                [(az, 75.0, _INTENSITY) for az in (0.0, 90.0, 180.0)])
    _write_scan(d / "RHI_77_20260919_000000.hpl", "RHI - csm",
                [(0.0, el, _INTENSITY) for el in (10.0, 30.0, 60.0)])
    return d


# -- resolve_intensity_filter ----------------------------------------------

@pytest.mark.parametrize("value, expected", [
    (None, None), (False, None), (True, data.DEFAULT_INTENSITY_FILTER),
    (1.05, 1.05), (2, 2.0), ("True", data.DEFAULT_INTENSITY_FILTER),
    ("true", data.DEFAULT_INTENSITY_FILTER), ("1.3", 1.3), ("none", None),
])
def test_resolve_intensity_filter(value, expected):
    assert data.resolve_intensity_filter(value) == expected


@pytest.mark.parametrize("value", ["abc", float("nan"), [1.0]])
def test_resolve_intensity_filter_rejects_garbage(value):
    with pytest.raises(ValueError):
        data.resolve_intensity_filter(value)


def test_default_threshold():
    assert data.DEFAULT_INTENSITY_FILTER == 1.018


# -- Processed_Wind_Profile via matching Wind_Profile file ----------------

def test_find_wind_profile_file(filter_dir):
    p = filter_dir / "Processed_Wind_Profile_77_20260919_000000.hpl"
    assert data.find_wind_profile_file(p) == \
        filter_dir / "Wind_Profile_77_20260919_000000.hpl"
    p2 = filter_dir / "Processed_Wind_Profile_77_20260919_001500.hpl"
    assert data.find_wind_profile_file(p2) is None
    assert data.find_wind_profile_file(
        filter_dir / "VAD_77_20260919_000000.hpl") is None


def test_load_profile_filtered_by_wind_profile_intensity(filter_dir):
    p = filter_dir / "Processed_Wind_Profile_77_20260919_000000.hpl"
    raw = data.load_profile(p)
    assert np.isfinite(raw.speed).all() and raw.intensity is None

    prof = data.load_profile(p, intensity_min=1.018)
    assert not prof.filter_missing
    # level n <-> gate n
    assert np.array_equal(np.isnan(prof.speed), _WP_BLANK)
    assert np.array_equal(np.isnan(prof.speed), np.isnan(prof.direction))
    assert np.allclose(prof.intensity, _WP_INTENSITY)


def test_wind_profile_intensity_is_gate_indexed_and_ray_averaged(tmp_path):
    wp = tmp_path / "Wind_Profile_77_20260919_000000.hpl"
    # 7 gates for 5 levels: the extra gates are ignored; the two rays
    # are averaged per gate
    _write_scan(wp, "Wind profile - stepped",
                [(0.0, 75.0, np.array([1.0, 1.1, 1.2, 1.3, 1.4, 9.0, 9.0])),
                 (90.0, 75.0, np.array([1.2, 1.3, 1.4, 1.5, 1.6, 9.0, 9.0]))])
    inten = data.load_wind_profile_intensity(wp, 5)
    assert np.allclose(inten, [1.1, 1.2, 1.3, 1.4, 1.5])
    # fewer gates than levels: the extra levels have no intensity
    inten = data.load_wind_profile_intensity(wp, 9)
    assert np.isnan(inten[7:]).all() and np.isfinite(inten[:7]).all()


def test_level_order_in_file_is_kept_for_matching(tmp_path):
    # levels written top-down: file row n still pairs with gate n, and
    # the result is sorted by height afterwards
    _write_processed(tmp_path / "Processed_Wind_Profile_77_20260919_000000.hpl",
                     heights=_HEIGHTS[::-1])
    _write_scan(tmp_path / "Wind_Profile_77_20260919_000000.hpl",
                "Wind profile - stepped", [(0.0, 75.0, _WP_INTENSITY)])
    prof = data.load_profile(
        tmp_path / "Processed_Wind_Profile_77_20260919_000000.hpl",
        intensity_min=1.018)
    assert np.all(np.diff(prof.height) > 0)
    assert np.array_equal(np.isnan(prof.speed), _WP_BLANK[::-1])


def test_load_profile_without_wind_profile_is_left_unfiltered(filter_dir):
    p = filter_dir / "Processed_Wind_Profile_77_20260919_001500.hpl"
    prof = data.load_profile(p, intensity_min=1.018)
    assert prof.filter_missing
    assert np.isfinite(prof.speed).all()


def test_load_profile_series_filtered(filter_dir):
    paths = sorted(filter_dir.glob("Processed_Wind_Profile_*.hpl"))
    series = data.load_profile_series(paths, intensity_min=1.018)
    assert len(series.filter_missing) == 1
    # first (filtered) column has blanks at the top, second has none
    assert np.isnan(series.speed[:, 0]).any()
    assert np.isfinite(series.speed[:, 1]).all()


# -- scan kinds -----------------------------------------------------------

def test_scan_history_filtered(filter_dir):
    p = filter_dir / "VAD_77_20260919_000000.hpl"
    raw = data.load_scan_history([p])
    assert np.isfinite(raw.intensity).all()
    hist = data.load_scan_history([p], intensity_min=1.018)
    assert np.isfinite(hist.intensity[:3]).all()
    assert np.isnan(hist.intensity[3:]).all()
    assert np.array_equal(np.isnan(hist.intensity), np.isnan(hist.beta))


def test_scan_points_filtered(filter_dir):
    p = filter_dir / "RHI_77_20260919_000000.hpl"
    pts = data.load_scan_points(p, intensity_min=1.018)
    low = pts.intensity < 1.018
    assert low.any() and (~low).any()
    assert np.isnan(pts.velocity[low]).all()
    assert np.isnan(pts.beta[low]).all()
    assert np.isfinite(pts.velocity[~low]).all()
    # geometry is untouched
    assert np.isfinite(pts.x).all() and np.isfinite(pts.z).all()


# -- api / cli --------------------------------------------------------------

def test_api_filter_true_profile(filter_dir, tmp_path):
    p = filter_dir / "Processed_Wind_Profile_77_20260919_000000.hpl"
    fig = api.plot(p, filter=True, output=str(tmp_path / "f.png"))
    ax_speed = fig.axes[0]
    xdata = ax_speed.lines[0].get_xdata()
    assert np.array_equal(np.isnan(xdata), _WP_BLANK)
    assert "intensity < 1.018" in fig._suptitle.get_text()


def test_api_filter_warns_on_missing_wind_profile(filter_dir, tmp_path):
    with pytest.warns(UserWarning, match="Wind_Profile"):
        api.plot(filter_dir, kind="Processed_Wind_Profile", filter=1.1,
                 output=str(tmp_path / "h.png"))


def test_api_no_filter_no_warning(filter_dir, tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        api.plot(filter_dir, kind="Processed_Wind_Profile",
                 output=str(tmp_path / "n.png"))


def test_api_filter_rejects_bad_value(filter_dir):
    with pytest.raises(ValueError, match="filter"):
        api.plot(filter_dir, kind="VAD", filter="lots")


def test_api_filter_scan_kinds(filter_dir, tmp_path):
    api.plot(filter_dir, kind="VAD", filter=True,
             output=str(tmp_path / "v.png"))
    api.plot(filter_dir, kind="RHI", filter=1.2,
             output=str(tmp_path / "r.png"))
    api.plot(filter_dir, kind="VAD", mode="ppi", filter=True, fill=True,
             output=str(tmp_path / "p.png"))
    assert (tmp_path / "v.png").exists() and (tmp_path / "r.png").exists()
    assert (tmp_path / "p.png").exists()


def test_cli_filter_true_and_value(filter_dir, tmp_path):
    for value in ("True", "1.05"):
        out = tmp_path / f"cli_{value}.png"
        rc = cli.main([str(filter_dir), "-k", "VAD", "--filter", value,
                       "-p", str(out)])
        assert rc == 0 and out.exists()
    args = cli.build_parser().parse_args(["x.hpl", "--filter", "True"])
    assert args.filter == data.DEFAULT_INTENSITY_FILTER
    args = cli.build_parser().parse_args(["x.hpl", "--filter", "1.3"])
    assert args.filter == 1.3


def test_cli_filter_rejects_garbage():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["x.hpl", "--filter", "abc"])
