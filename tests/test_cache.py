# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5
"""Fast array reader (hpl.read_scan_arrays) and the scan cache."""

import os
import shutil
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from haloviewer import cache, data, hpl

_DATA_DIR = Path(__file__).parent / "data"
_RHI_FILE = _DATA_DIR / "RHI_77_20260921_000812.hpl"
_VAD_FILE = _DATA_DIR / "VAD_77_20260921_000721.hpl"
_VAD_TRUNCATED_FILE = _DATA_DIR / "VAD_77_20260921_000721_truncated.hpl"
_VAD_EMPTY_FILE = _DATA_DIR / "VAD_77_20260921_000721_empty.hpl"

_FIELDS = ("time", "azimuth", "elevation", "pitch", "roll",
           "gate", "doppler", "intensity", "beta")


def _assert_same(a, b):
    assert a.n_rays == b.n_rays
    if not a.n_rays:
        return
    for k in _FIELDS:
        assert np.array_equal(getattr(a, k), getattr(b, k),
                              equal_nan=True), k
    assert a.timestamp == b.timestamp
    assert (a.type, a.mode) == (b.type, b.mode)
    assert a.header == b.header


# -- fast reader -----------------------------------------------------------

@pytest.mark.parametrize("path", [_RHI_FILE, _VAD_FILE, _VAD_TRUNCATED_FILE,
                                  _VAD_EMPTY_FILE])
def test_fast_reader_matches_datafile(path):
    fast = hpl.read_scan_arrays(path)
    slow = hpl.scan_arrays_from_datafile(hpl.DataFile(str(path)))
    _assert_same(fast, slow)


def test_fast_reader_matches_ray_objects():
    a = hpl.read_scan_arrays(_RHI_FILE)
    d = hpl.DataFile(str(_RHI_FILE))
    assert a.n_rays == len(d.rays) and a.n_gates == 20
    for i, r in enumerate(d.rays):
        assert pd.Timestamp(a.time[i]) == r.time
        assert (a.azimuth[i], a.elevation[i], a.pitch[i], a.roll[i]) == \
            (r.azimuth, r.elevation, r.pitch, r.roll)
        assert np.array_equal(
            a.doppler[i],
            pd.to_numeric(r.data["Doppler"]).to_numpy(dtype=np.float32))
    assert a.doppler.dtype == np.float32


def test_fast_reader_truncated_reads_complete_rays():
    assert hpl.read_scan_arrays(_VAD_TRUNCATED_FILE).n_rays == 5
    assert hpl.read_scan_arrays(_VAD_EMPTY_FILE).n_rays == 0


def test_fast_reader_falls_back_on_irregular_block(tmp_path, monkeypatch):
    # an extra value on one ray line: DataFile tolerates it, the fast
    # path can't split the block evenly and must hand the file over
    lines = _VAD_FILE.read_text().splitlines()
    i = lines.index("****") + 1
    lines[i] += " 0.00"
    odd = tmp_path / _VAD_FILE.name
    odd.write_text("\n".join(lines) + "\n")
    calls = []
    real = hpl.scan_arrays_from_datafile

    def spy(d):
        calls.append(d.file)
        return real(d)
    monkeypatch.setattr(hpl, "scan_arrays_from_datafile", spy)
    got = hpl.read_scan_arrays(odd)
    assert calls == [str(odd)]
    _assert_same(got, hpl.read_scan_arrays(_VAD_FILE))


def test_fast_reader_rejects_processed_profile(tmp_path):
    p = tmp_path / "Processed_Wind_Profile_77_20260921_000000.hpl"
    p.write_text("2\n  9.0 200.0 3.0\n 39.0 210.0 4.0\n")
    with pytest.raises(ValueError):
        hpl.read_scan_arrays(p)


# -- cache -------------------------------------------------------------------

def test_default_cache_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv(cache.ENV_VAR, str(tmp_path / "c"))
    assert cache.default_cache_dir() == tmp_path / "c"
    for word in ("off", "0", "none", "False"):
        monkeypatch.setenv(cache.ENV_VAR, word)
        assert cache.default_cache_dir() is None
        assert cache.get_default_cache() is None
    monkeypatch.delenv(cache.ENV_VAR)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    assert cache.default_cache_dir().name.startswith("haloviewer-cache-")


def test_cache_roundtrip_and_hit(tmp_path, monkeypatch):
    c = cache.ScanCache(tmp_path / "c")
    src = tmp_path / _RHI_FILE.name
    shutil.copy(_RHI_FILE, src)
    assert not c.is_cached(src)
    first = c.get(src)
    assert c.is_cached(src)
    # a second get must not parse again
    monkeypatch.setattr(hpl, "read_scan_arrays",
                        lambda p: pytest.fail("parsed despite cache hit"))
    _assert_same(c.get(src), first)
    assert c.size_bytes() > 0


def test_cache_misses_after_source_change(tmp_path):
    c = cache.ScanCache(tmp_path / "c")
    src = tmp_path / _VAD_FILE.name
    shutil.copy(_VAD_TRUNCATED_FILE, src)
    assert c.get(src).n_rays == 5
    shutil.copy(_VAD_FILE, src)
    os.utime(src, ns=(time.time_ns(), time.time_ns() + 10**9))
    assert not c.is_cached(src)
    assert c.get(src).n_rays == 6
    # the old entry is now stale and pruned; the new one stays
    assert c.prune() == 1
    assert c.is_cached(src)
    src.unlink()
    assert c.prune() == 1
    assert c.size_bytes() == 0


def test_prune_keeps_entries_of_unavailable_directories(tmp_path):
    c = cache.ScanCache(tmp_path / "c")
    srcdir = tmp_path / "drive"
    srcdir.mkdir()
    src = srcdir / _VAD_FILE.name
    shutil.copy(_VAD_FILE, src)
    c.get(src)
    shutil.rmtree(srcdir)        # e.g. a network drive not mounted
    assert c.prune() == 0
    assert c.size_bytes() > 0


def test_broken_entry_is_discarded(tmp_path):
    c = cache.ScanCache(tmp_path / "c")
    src = tmp_path / _VAD_FILE.name
    shutil.copy(_VAD_FILE, src)
    c.get(src)
    c.entry_path(src).write_bytes(b"not a zip file")
    assert c.load(src) is None
    assert not c.is_cached(src)
    assert c.get(src).n_rays == 6


def test_unwritable_cache_still_reads(tmp_path):
    blocker = tmp_path / "c"
    blocker.write_text("a file where the cache directory should be")
    c = cache.ScanCache(blocker)
    assert c.get(_VAD_FILE).n_rays == 6
    assert not c.is_cached(_VAD_FILE)


def test_build_progress_and_stop(tmp_path):
    c = cache.ScanCache(tmp_path / "c")
    srcs = []
    for i in range(3):
        p = tmp_path / f"VAD_77_20260921_00072{i}.hpl"
        shutil.copy(_VAD_FILE, p)
        srcs.append(p)
    bad = tmp_path / "VAD_77_20260921_000730.hpl"
    bad.write_text("Filename:\tbroken\n")
    seen = []
    assert c.build(srcs + [bad],
                   progress=lambda d, t: seen.append((d, t))) == 4
    assert seen[0] == (0, 4) and seen[-1] == (4, 4)
    assert all(c.is_cached(p) for p in srcs)
    assert c.build(srcs) == 0             # nothing left to do
    stop = threading.Event()
    stop.set()
    c.clear()
    assert c.build(srcs, stop=stop) == 0
    assert c.size_bytes() == 0


def test_loaders_identical_with_and_without_cache(tmp_path):
    files = [_VAD_FILE, _VAD_TRUNCATED_FILE, _RHI_FILE]
    results = {}
    for mode in ("off", "cold", "warm"):
        cache.set_default_cache(None if mode == "off" else
                                cache.ScanCache(tmp_path / "c"))
        results[mode] = (
            data.load_scan_history(files[:2], intensity_min=1.0),
            data.load_scan_points(_RHI_FILE, intensity_min=1.0),
            data.load_wind_profile_intensity(_VAD_FILE, 20),
        )
    cache.set_default_cache()
    ref = results["off"]
    for mode in ("cold", "warm"):
        h, p, w = results[mode]
        assert (h.times == ref[0].times).all()
        np.testing.assert_array_equal(h.intensity, ref[0].intensity)
        np.testing.assert_array_equal(h.beta, ref[0].beta)
        for k in ("x", "y", "z", "velocity", "beta", "intensity"):
            np.testing.assert_array_equal(getattr(p, k), getattr(ref[1], k))
        np.testing.assert_array_equal(w, ref[2])


def test_filter_threshold_equal_to_a_value_keeps_it(tmp_path):
    # a value exactly equal to the threshold is "not below" it, even
    # though both are held as float32 internally
    a = hpl.read_scan_arrays(_RHI_FILE)
    v = float(np.round(float(a.intensity[0, 0]), 6))
    pts = data.load_scan_points(_RHI_FILE, intensity_min=v)
    assert np.isfinite(pts.velocity[0])
    pts = data.load_scan_points(_RHI_FILE, intensity_min=v + 1e-6)
    assert np.isnan(pts.velocity[0])


# -- GUI -----------------------------------------------------------------------

def test_gui_builds_cache_in_background(tmp_path):
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
    c = cache.ScanCache(tmp_path / "c")
    cache.set_default_cache(c)
    root = tk.Tk()
    try:
        app = HaloViewerApp(root, initial_dir=str(d))
        deadline = time.time() + 20
        while (not app.cache_var.get().endswith("MB)")
               and time.time() < deadline):
            root.update()
            time.sleep(0.05)
        root.update()
        assert app._cache_state[0] == "done"
        assert app.cache_var.get().startswith("Cache: 2 file(s) ready")
        assert c.is_cached(d / _RHI_FILE.name)
        assert c.is_cached(d / _VAD_FILE.name)
    finally:
        cache.set_default_cache()
        root.destroy()
