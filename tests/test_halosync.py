# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5

from datetime import datetime

import pytest

pytest.importorskip("tkinter")
halosync = pytest.importorskip("haloviewer.halosync")


def test_extract_timestamp_both_notations():
    assert halosync.extract_timestamp("VAD_77_20221020_111520.hpl") == \
        datetime(2022, 10, 20, 11, 15, 20)
    assert halosync.extract_timestamp("Background_170926-065156.txt") == \
        datetime(2026, 9, 17, 6, 51, 56)
    assert halosync.extract_timestamp("system_parameters_77.txt") is None


def test_classify_subgroup():
    assert halosync.classify_subgroup(
        "Proc", "Processed_Wind_Profile_77_20260921_000812.hpl") == \
        "Processed_Wind_Profile"
    assert halosync.classify_subgroup(
        "Proc", "Wind_Profile_77_20260921_000812.hpl") == "Wind_Profile"
    assert halosync.classify_subgroup("Raw", "aet_VAD_1.raw") == "VAD"
    assert halosync.classify_subgroup("Proc", "junk.txt") is None


def test_scan_and_transfer_list(tmp_path):
    src = tmp_path / "src"
    day = src / "Proc" / "2026" / "202609" / "20260921"
    day.mkdir(parents=True)
    for name in ["VAD_77_20260921_000000.hpl", "VAD_77_20260921_010000.hpl"]:
        (day / name).write_text("x")
    data, found = halosync.scan_root(src)
    assert found
    proc = data["Proc"]
    assert len(proc.subgroups["VAD"].files) == 2
    # newest file is the "current file" and is excluded by default
    assert proc.current_file.endswith("VAD_77_20260921_010000.hpl")
    dst = tmp_path / "dst"
    dst.mkdir()
    todo = halosync.build_transfer_list(
        data, dst, {"Proc": "all"}, include_current=False, force_all=False)
    assert [f.relpath.rsplit("/", 1)[1] for f in todo] == \
        ["VAD_77_20260921_000000.hpl"]
    assert halosync.subgroup_light(proc, dst, "VAD") == "orange"
