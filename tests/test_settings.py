# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5
"""Remembered settings, cache modes, and the command-line options."""

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from haloviewer import cache, cli, settings

_DATA_DIR = Path(__file__).parent / "data"
_VAD_FILE = _DATA_DIR / "VAD_77_20260921_000721.hpl"


@pytest.fixture
def cache_places(tmp_path, monkeypatch):
    """No HALOVIEWER_CACHE override; temp and per-user cache dirs inside
    tmp_path."""
    monkeypatch.delenv(cache.ENV_VAR, raising=False)
    tmp = tmp_path / "systemtmp"
    tmp.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(tmp))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdgcache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "xdgcache"))
    cache.set_default_cache()
    yield tmp_path
    cache.set_default_cache()


# -- settings file ----------------------------------------------------------

def test_settings_defaults_and_roundtrip():
    assert not settings.settings_path().exists()
    s = settings.load()
    assert s == settings.DEFAULTS
    assert settings.save(root_dir="/data/Proc", kind="VAD", bogus=1)
    assert settings.save(fill=True)          # keeps the other keys
    s = settings.load()
    assert (s["root_dir"], s["kind"], s["fill"]) == ("/data/Proc", "VAD",
                                                     True)
    raw = json.loads(settings.settings_path().read_text())
    assert "bogus" not in raw and raw["version"] == settings.SETTINGS_VERSION


def test_settings_tolerate_broken_file():
    p = settings.settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ not json")
    assert settings.load() == settings.DEFAULTS
    p.write_text(json.dumps({"version": 1, "fill": "yes", "kind": "RHI"}))
    s = settings.load()
    assert s["fill"] is False and s["kind"] == "RHI"   # wrong type ignored


# -- cache modes --------------------------------------------------------------

def test_default_mode_is_tmp(cache_places):
    mode, d = cache.effective_setting()
    assert mode == "tmp"
    assert d.parent == cache_places / "systemtmp"
    assert d.name.startswith("haloviewer-cache-")
    if hasattr(os, "getuid"):
        assert (d.stat().st_mode & 0o777) == 0o700


def test_configure_modes(cache_places, tmp_path):
    assert cache.configure("user") == cache.user_cache_dir()
    assert cache.user_cache_dir().parent == tmp_path / "xdgcache"
    assert cache.get_default_cache().directory == cache.user_cache_dir()
    custom = tmp_path / "mycache"
    assert cache.configure("custom", custom) == custom
    assert settings.load()["cache_dir"] == str(custom)
    assert cache.get_default_cache().directory == custom
    # custom again without a directory: the remembered one
    assert cache.configure("custom") == custom
    assert cache.configure("tmp").name.startswith("haloviewer-cache-")
    with pytest.raises(ValueError):
        cache.configure("bogus")


def test_custom_without_directory_is_an_error(cache_places):
    with pytest.raises(ValueError):
        cache.configure("custom")


def test_no_cache_wipes_everything(cache_places, tmp_path):
    custom = tmp_path / "mycache"
    custom.mkdir()
    (custom / "keep.txt").write_text("not a cache file")
    for mode, d in (("tmp", None), ("user", None), ("custom", custom)):
        cache.configure(mode, d)
        cache.get_default_cache().get(_VAD_FILE)
        assert cache.get_default_cache().stats()[0] == 1
    tmp_dir, user_dir = cache.tmp_cache_dir(), cache.user_cache_dir()
    assert cache.configure("off") is None
    assert cache.get_default_cache() is None
    assert not user_dir.exists()
    assert not any(p for p in tmp_dir.rglob("*.npz")) if tmp_dir.exists() \
        else True
    assert cache.ScanCache(custom).stats() == (0, 0)
    assert (custom / "keep.txt").exists()          # only cache entries go


def test_env_var_overrides_setting(cache_places, tmp_path, monkeypatch):
    cache.configure("user")
    monkeypatch.setenv(cache.ENV_VAR, str(tmp_path / "env"))
    assert cache.effective_setting() == ("custom", tmp_path / "env")
    monkeypatch.setenv(cache.ENV_VAR, "off")
    assert cache.effective_setting() == ("off", None)


# -- haloplot options ---------------------------------------------------------

def test_haloplot_cache_options_are_remembered(cache_places, tmp_path,
                                               capsys):
    assert cli.main(["--user-cache"]) == 0
    assert settings.load()["cache_mode"] == "user"
    assert cli.main(["--cache-dir", str(tmp_path / "c")]) == 0
    s = settings.load()
    assert (s["cache_mode"], s["cache_dir"]) == ("custom",
                                                 str(tmp_path / "c"))
    assert cli.main(["--tmp-cache"]) == 0
    assert settings.load()["cache_mode"] == "tmp"
    assert cli.main(["--no-cache"]) == 0
    assert settings.load()["cache_mode"] == "off"
    err = capsys.readouterr().err
    assert "switched off and deleted" in err


def test_haloplot_cache_options_exclusive(cache_places):
    with pytest.raises(SystemExit):
        cli.main(["--user-cache", "--no-cache"])


def test_haloplot_requires_file_without_info(cache_places):
    with pytest.raises(SystemExit):
        cli.main([])


def test_haloplot_info(cache_places, capsys):
    settings.save(root_dir="/data/Proc", kind="VAD", filter_on=True,
                  filter_value="1.02")
    cache.configure("user")
    cache.get_default_cache().get(_VAD_FILE)
    assert cli.main(["-i"]) == 0
    out = capsys.readouterr().out
    assert str(settings.settings_path()) in out
    assert "/data/Proc" in out and "VAD" in out
    assert "intensity < 1.02" in out
    assert "per-user" in out and str(cache.user_cache_dir()) in out
    assert "1 file(s)" in out


def test_haloplot_plots_with_cache_option(cache_places, tmp_path):
    out = tmp_path / "x.png"
    assert cli.main([str(_VAD_FILE), "--user-cache", "-p", str(out)]) == 0
    assert out.exists()
    assert cache.ScanCache(cache.user_cache_dir()).stats()[0] == 1


# -- viewer options -------------------------------------------------------------

def test_viewer_info_and_options(cache_places, capsys):
    pytest.importorskip("tkinter")
    from haloviewer import gui
    assert gui.main(["--user-cache", "--info"]) == 0
    out = capsys.readouterr().out
    assert "per-user" in out
    assert settings.load()["cache_mode"] == "user"


# -- halosync state location ----------------------------------------------------

def test_halosync_moves_old_state(tmp_path, monkeypatch):
    pytest.importorskip("tkinter")
    halosync = pytest.importorskip("haloviewer.halosync")
    monkeypatch.delenv("HALOSYNC_CONFIG", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    old = tmp_path / "LidarSyncGUI"
    old.mkdir()
    (old / "state.json").write_text('{"version": 1}')
    d = halosync.get_config_dir()
    assert d == tmp_path / "halosync"
    assert (d / "state.json").read_text() == '{"version": 1}'
    assert not old.exists()
    assert halosync.get_config_path() == d / "state.json"


# -- GUI ----------------------------------------------------------------------

def _gui_root():
    tk = pytest.importorskip("tkinter")
    if os.name != "nt" and not os.environ.get("DISPLAY"):
        pytest.skip("no display")
    return tk.Tk()


def _proc_dir(tmp_path):
    d = tmp_path / "Proc"
    d.mkdir()
    shutil.copy(_VAD_FILE, d)
    shutil.copy(_DATA_DIR / "RHI_77_20260921_000812.hpl", d)
    return d


def test_gui_remembers_path_and_settings(tmp_path):
    from haloviewer.gui import HaloViewerApp
    d = _proc_dir(tmp_path)
    root = _gui_root()
    app = HaloViewerApp(root, initial_dir=str(d))
    app.kind_combo.current(app._kind_names.index("RHI"))
    app._on_kind_select()
    app.mode_var.set("ppi")
    app._on_mode_select()
    app.fill_var.set(True)
    app.filter_var.set(True)
    app.filter_value_var.set("1.03")
    app.time_preset_var.set("6h")
    app._on_close()                     # saves and destroys
    s = settings.load()
    assert (s["root_dir"], s["kind"], s["mode"]) == (str(d), "RHI", "ppi")
    assert (s["fill"], s["filter_on"], s["filter_value"],
            s["time_preset"]) == (True, True, "1.03", "6h")

    root = _gui_root()
    try:
        app = HaloViewerApp(root)        # no directory given
        assert app.dir_var.get() == str(d)
        assert app.current_kind == "RHI"
        assert app.mode_var.get() == "ppi"
        assert app.fill_var.get() and app.filter_var.get()
        assert app.filter_value_var.get() == "1.03"
        assert app.time_preset_var.get() == "6h"
    finally:
        root.destroy()


def test_gui_cache_dialog(cache_places, tmp_path, monkeypatch):
    from haloviewer import gui
    d = _proc_dir(tmp_path)
    root = _gui_root()
    try:
        app = gui.HaloViewerApp(root, initial_dir=str(d))
        dlg = gui._CacheDialog(app)
        assert dlg.mode_var.get() == "tmp"
        assert "temporary" in dlg.info_var.get()
        assert str(dlg.dir_entry.cget("state")) == "disabled"
        dlg.mode_var.set("custom")
        dlg._update_dir_state()
        assert str(dlg.dir_entry.cget("state")) == "normal"
        dlg.dir_var.set(str(tmp_path / "mine"))
        dlg._apply()
        assert settings.load()["cache_mode"] == "custom"
        deadline = time.time() + 20
        while (not app.cache_var.get().endswith("MB)")
               and time.time() < deadline):
            root.update()
            time.sleep(0.05)
        assert cache.ScanCache(tmp_path / "mine").stats()[0] == 2
        dlg._refresh_info()
        assert "2 file(s)" in dlg.info_var.get()
        monkeypatch.setattr(gui.messagebox, "askyesno",
                            lambda *a, **k: True)
        dlg._empty()
        assert cache.ScanCache(tmp_path / "mine").stats()[0] == 0
        dlg.mode_var.set("off")
        dlg._apply()
        assert cache.get_default_cache() is None
        assert app.cache_var.get() == "Cache: off"
        dlg.win.destroy()
    finally:
        root.destroy()
