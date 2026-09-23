from pathlib import Path

import pytest
import matplotlib
matplotlib.use("Agg")

import windlidarviewer
from windlidarviewer import api, cli
from windlidarviewer.scan import scan_directory

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
    rc = cli.main([str(entry.path), "-o", str(out)])
    assert rc == 0
    assert out.exists()


def test_cli_glob_timeseries(proc_tree, tmp_path):
    pattern = str(proc_tree / "2026" / "202609" / "20260919" /
                   "Processed_Wind_Profile_*.hpl")
    out = tmp_path / "cli_ts.png"
    rc = cli.main([pattern, "--mode", "history", "-o", str(out)])
    assert rc == 0
    assert out.exists()


# =========================================================================
# windlidarviewer.plot() -- package-level re-export
# =========================================================================

def test_plot_reexported_at_package_level():
    assert windlidarviewer.plot is api.plot
    assert windlidarviewer.plot_file is api.plot_file
    assert windlidarviewer.plot_files is api.plot_files


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
                    "-o", str(out)])
    assert rc == 0
    assert out.exists()


def test_cli_directory_without_kind_errors(proc_tree, tmp_path):
    out = tmp_path / "cli_multi.png"
    rc = cli.main([str(proc_tree), "-o", str(out)])
    assert rc == 1
    assert not out.exists()


def test_cli_height_flag(proc_tree, tmp_path):
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]
    out = tmp_path / "cli_height.png"
    rc = cli.main([str(entry.path), "--height", "0", "1000", "-o", str(out)])
    assert rc == 0
    assert out.exists()


def test_cli_start_and_time_flags(proc_tree, tmp_path):
    out = tmp_path / "cli_start.png"
    rc = cli.main([str(proc_tree), "-k", "Processed_Wind_Profile",
                    "-t", "2026-09-19 00:30:00", "-s", "0.2h",
                    "-o", str(out)])
    assert rc == 0
    assert out.exists()


def test_cli_mode_profile_and_history(proc_tree, tmp_path):
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]
    out = tmp_path / "cli_mode.png"
    rc = cli.main([str(entry.path), "--mode", "profile", "-o", str(out)])
    assert rc == 0
    assert out.exists()


def test_cli_mode_rejects_old_timeseries_spelling(proc_tree, tmp_path):
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]
    with pytest.raises(SystemExit):
        cli.main([str(entry.path), "--mode", "timeseries"])


def test_cli_plot_alias_for_show(proc_tree, tmp_path):
    # -p/--plot behaves as an alias for --show; with the non-interactive
    # Agg backend this just needs to not error.
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]
    rc = cli.main([str(entry.path), "-p"])
    assert rc == 0


def test_cli_warns_on_inapplicable_distance(proc_tree, tmp_path, capsys):
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]
    out = tmp_path / "cli_warn.png"
    rc = cli.main([str(entry.path), "--distance", "0", "100",
                    "-o", str(out)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "warning:" in captured.err
    assert "distance" in captured.err


def test_cli_fontsize_and_figsize_flags(proc_tree, tmp_path):
    result = scan_directory(proc_tree)
    entry = result.files("Processed_Wind_Profile")[0]
    out = tmp_path / "cli_fig.png"
    rc = cli.main([str(entry.path), "--figsize", "8", "5",
                    "--fontsize", "12", "-o", str(out)])
    assert rc == 0
    assert out.exists()
