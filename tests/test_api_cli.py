import matplotlib
matplotlib.use("Agg")

from windlidarviewer import api, cli
from windlidarviewer.scan import scan_directory


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
    entry = result.files("VAD")[0]
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
    rc = cli.main([pattern, "--mode", "timeseries", "-o", str(out)])
    assert rc == 0
    assert out.exists()
