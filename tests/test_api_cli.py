# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5

from pathlib import Path

import pytest
import matplotlib
matplotlib.use("Agg")

import haloviewer
from haloviewer import api, cli
from haloviewer.scan import scan_directory

_DATA_DIR = Path(__file__).parent / "data"
_RHI_FILE = _DATA_DIR / "RHI_77_20260921_000812.hpl"
_VAD_FILE = _DATA_DIR / "VAD_77_20260921_000721.hpl"


def test_plot_file_single(proc_tree, tmp_path):
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]
    out = tmp_path / "out.png"
    fig = api.plot_file(entry.path, output=str(out))
    assert out.exists()
    assert fig is not None


def test_plot_files_timeseries(proc_tree, tmp_path):
    result = scan_directory(proc_tree)
    files = [e.path for e in result.files("Processed_Wind_Profile")]
    out = tmp_path / "ts.png"
    api.plot_files(files, output=str(out))
    assert out.exists()


def test_unsupported_kind_raises(proc_tree):
    result = scan_directory(proc_tree)
    entry = result.files("User1")[0]
    try:
        api.plot_file(entry.path)
        assert False, "expected NotImplementedError"
    except NotImplementedError:
        pass


def test_cli_single_file(proc_tree, tmp_path):
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]
    out = tmp_path / "cli_out.png"
    rc = cli.main([str(entry.path), "-p", str(out)])
    assert rc == 0
    assert out.exists()


def test_cli_glob_timeseries(proc_tree, tmp_path):
    pattern = str(proc_tree / "2026" / "202609" / "20260919" /
                   "Processed_Wind_Profile_*.hpl")
    out = tmp_path / "cli_ts.png"
    rc = cli.main([pattern, "--mode", "history", "-p", str(out)])
    assert rc == 0
    assert out.exists()


# =========================================================================
# haloviewer.plot() -- package-level re-export
# =========================================================================

def test_plot_reexported_at_package_level():
    assert haloviewer.plot is api.plot
    assert haloviewer.plot_file is api.plot_file
    assert haloviewer.plot_files is api.plot_files


# =========================================================================
# api.plot(): path resolution (directory / glob / single file)
# =========================================================================

def test_plot_directory_multi_kind_requires_kind(proc_tree):
    # proc_tree contains both Processed_Wind_Profile and User1 files, so
    # kind can't be inferred without help.
    with pytest.raises(ValueError, match="kind"):
        api.plot(proc_tree)


def test_plot_directory_single_kind_explicit(proc_tree, tmp_path):
    out = tmp_path / "dir.png"
    fig = api.plot(proc_tree, kind="Processed_Wind_Profile", output=str(out))
    assert out.exists()
    # all 3 files fall in range -> defaults to "history" (4 axes: two
    # panels + two colorbars), not the 2-axes single-scan "profile" figure.
    assert len(fig.axes) == 4


def test_plot_glob_pattern(proc_tree, tmp_path):
    pattern = str(proc_tree / "2026" / "202609" / "20260919" /
                   "Processed_Wind_Profile_*.hpl")
    out = tmp_path / "glob.png"
    fig = api.plot(pattern, output=str(out))
    assert out.exists()
    assert len(fig.axes) == 4


def test_plot_single_file_defaults_to_profile_mode(proc_tree, tmp_path):
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]
    out = tmp_path / "single.png"
    fig = api.plot(entry.path, output=str(out))
    assert out.exists()
    # a lone matching file -> "profile" mode by default (2 axes, no
    # colorbars), since there's nothing to make a time series out of.
    assert len(fig.axes) == 2


def test_plot_missing_path_warns_and_raises():
    with pytest.warns(UserWarning, match="does not exist"):
        with pytest.raises(ValueError, match="no .hpl files"):
            api.plot("/no/such/path/at/all.hpl")


# =========================================================================
# api.plot(): time-range selection (absolute + relative start/end)
# =========================================================================

def test_plot_end_defaults_to_latest_file(proc_tree, tmp_path):
    out = tmp_path / "end_default.png"
    # no start/end given -> end defaults to the latest file's timestamp,
    # start defaults to no lower bound, so all 3 files are in range.
    fig = api.plot(proc_tree, kind="Processed_Wind_Profile", output=str(out))
    assert len(fig.axes) == 4


def test_plot_relative_start_hours_narrows_to_last_file(proc_tree, tmp_path):
    out = tmp_path / "start_h.png"
    fig = api.plot(proc_tree, kind="Processed_Wind_Profile",
                    end="2026-09-19 00:30:00", start="0.2h", output=str(out))
    # 0.2h = 12 minutes back from 00:30 -> only the 00:30 file qualifies
    # -> a single file -> defaults to "profile" mode (2 axes).
    assert len(fig.axes) == 2


def test_plot_relative_start_days_includes_all(proc_tree, tmp_path):
    out = tmp_path / "start_d.png"
    fig = api.plot(proc_tree, kind="Processed_Wind_Profile",
                    end="2026-09-19 00:30:00", start="1d", output=str(out))
    assert len(fig.axes) == 4


def test_plot_start_after_all_files_raises(proc_tree):
    with pytest.raises(ValueError, match="time range"):
        api.plot(proc_tree, kind="Processed_Wind_Profile",
                  start="2026-09-20 00:00:00")


# =========================================================================
# api.plot_file(): height/distance/speed overrides
# =========================================================================

def test_height_override_reaches_axes(proc_tree, tmp_path):
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]
    out = tmp_path / "height.png"
    fig = api.plot_file(entry.path, height=(0.0, 1000.0), output=str(out))
    ax_speed = fig.axes[0]
    assert ax_speed.get_ylim() == (0.0, 1000.0)


def test_distance_warns_when_not_applicable(proc_tree, tmp_path):
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]
    with pytest.warns(UserWarning, match="distance"):
        api.plot_file(entry.path, distance=(0.0, 100.0),
                       output=str(tmp_path / "d.png"))


def test_speed_warns_when_not_applicable_for_scan_history(tmp_path):
    with pytest.warns(UserWarning, match="speed"):
        api.plot_file(_VAD_FILE, speed=(0.0, 10.0),
                       output=str(tmp_path / "vad.png"))


# =========================================================================
# CLI: new flags
# =========================================================================

def test_cli_kind_flag_on_directory(proc_tree, tmp_path):
    out = tmp_path / "cli_kind.png"
    rc = cli.main([str(proc_tree), "-k", "Processed_Wind_Profile",
                    "-p", str(out)])
    assert rc == 0
    assert out.exists()


def test_cli_directory_without_kind_errors(proc_tree, tmp_path):
    out = tmp_path / "cli_multi.png"
    rc = cli.main([str(proc_tree), "-p", str(out)])
    assert rc == 1
    assert not out.exists()


def test_cli_height_flag(proc_tree, tmp_path):
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]
    out = tmp_path / "cli_height.png"
    rc = cli.main([str(entry.path), "--height", "0", "1000", "-p", str(out)])
    assert rc == 0
    assert out.exists()


def test_cli_start_and_time_flags(proc_tree, tmp_path):
    out = tmp_path / "cli_start.png"
    rc = cli.main([str(proc_tree), "-k", "Processed_Wind_Profile",
                    "-t", "2026-09-19 00:30:00", "-s", "0.2h",
                    "-p", str(out)])
    assert rc == 0
    assert out.exists()


def test_cli_mode_profile_and_history(proc_tree, tmp_path):
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]
    out = tmp_path / "cli_mode.png"
    rc = cli.main([str(entry.path), "--mode", "profile", "-p", str(out)])
    assert rc == 0
    assert out.exists()


def test_cli_mode_rejects_old_timeseries_spelling(proc_tree, tmp_path):
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]
    with pytest.raises(SystemExit):
        cli.main([str(entry.path), "--mode", "timeseries"])


def test_cli_plot_flag_sets_output_path(proc_tree, tmp_path):
    # -p/--plot is the output-path flag (renamed from --output/-o).
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]
    out = tmp_path / "cli_plot_flag.png"
    rc = cli.main([str(entry.path), "--plot", str(out)])
    assert rc == 0
    assert out.exists()


def test_cli_show_only_does_not_default_save(proc_tree, tmp_path, monkeypatch):
    # --show alone: interactive display, no file written (Agg backend
    # makes plt.show() a harmless no-op) and no "plot.png" side effect.
    monkeypatch.chdir(tmp_path)
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]
    rc = cli.main([str(entry.path), "--show"])
    assert rc == 0
    assert not (tmp_path / "plot.png").exists()


def test_cli_defaults_output_to_plot_png(proc_tree, tmp_path, monkeypatch, capsys):
    # neither -p/--plot nor --show given -> saved as "plot.png" in the
    # current directory, with a note explaining why.
    monkeypatch.chdir(tmp_path)
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]
    rc = cli.main([str(entry.path)])
    assert rc == 0
    assert (tmp_path / "plot.png").exists()
    captured = capsys.readouterr()
    assert "note:" in captured.err
    assert "plot.png" in captured.err


def test_cli_warns_on_inapplicable_dist(proc_tree, tmp_path, capsys):
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]
    out = tmp_path / "cli_warn.png"
    rc = cli.main([str(entry.path), "--dist", "0", "100",
                    "-p", str(out)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "warning:" in captured.err
    assert "distance" in captured.err


def test_cli_figsize_flag(proc_tree, tmp_path):
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]
    out = tmp_path / "cli_fig.png"
    rc = cli.main([str(entry.path), "--figsize", "8", "5", "-p", str(out)])
    assert rc == 0
    assert out.exists()


def test_cli_no_fontsize_flag():
    # --fontsize was removed: font size is now derived automatically
    # from figsize (see test_fontsize_scales_with_figsize below).
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["some_file.hpl", "--fontsize", "12"])


# =========================================================================
# api: figure-size-proportional default font size
# =========================================================================

def test_fontsize_scales_with_figsize(proc_tree, tmp_path):
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]

    fig_a4 = api.plot_file(entry.path, output=str(tmp_path / "a4.png"))
    assert fig_a4.get_size_inches() == pytest.approx(api.DEFAULT_FIGSIZE)
    # at the default A4-landscape figsize, the base font size is exactly
    # BASE_FONTSIZE_AT_A4.
    assert fig_a4.axes[0].xaxis.label.get_fontsize() == pytest.approx(
        api.BASE_FONTSIZE_AT_A4)

    # a smaller figure (half the area) gets a proportionally smaller
    # base font, not the fixed A4 value.
    half_area = (api.DEFAULT_FIGSIZE[0] / 2 ** 0.5,
                 api.DEFAULT_FIGSIZE[1] / 2 ** 0.5)
    fig_small = api.plot_file(entry.path, figsize=half_area,
                               output=str(tmp_path / "small.png"))
    small_fontsize = fig_small.axes[0].xaxis.label.get_fontsize()
    assert small_fontsize == pytest.approx(
        api.BASE_FONTSIZE_AT_A4 / 2 ** 0.5, rel=1e-3)
    assert small_fontsize < api.BASE_FONTSIZE_AT_A4

    # an explicit fontsize= always overrides the automatic scaling.
    fig_explicit = api.plot_file(entry.path, fontsize=20.0,
                                  output=str(tmp_path / "explicit.png"))
    assert fig_explicit.axes[0].xaxis.label.get_fontsize() == pytest.approx(20.0)
