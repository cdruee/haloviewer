# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5

import pandas as pd

from haloviewer.scan import parse_filename, scan_directory, get_kind_info


def test_parse_filename_processed_profile():
    parsed = parse_filename(
        "Processed_Wind_Profile_77_20260919_121707.hpl")
    assert parsed.kind == "Processed_Wind_Profile"
    assert parsed.system_id == "77"
    assert parsed.timestamp == pd.Timestamp("2026-09-19 12:17:07")


def test_parse_filename_short_time():
    parsed = parse_filename("Stare_77_20220629_08.hpl")
    assert parsed.kind == "Stare"
    assert parsed.timestamp == pd.Timestamp("2022-06-29 08:00:00")


def test_parse_filename_rejects_non_hpl():
    assert parse_filename("readme.txt") is None
    assert parse_filename("Processed_Wind_Profile_77_20260919.hpl") is None


def test_scan_directory_classifies_and_skips(proc_tree):
    result = scan_directory(proc_tree)
    assert set(result.kinds()) == {"Processed_Wind_Profile", "User1"}
    assert result.count("Processed_Wind_Profile") == 3
    assert result.count("User1") == 1
    assert not any(p.endswith("notes.txt") for p in result.skipped)

    lo, hi = result.time_range("Processed_Wind_Profile")
    assert lo == pd.Timestamp("2026-09-19 00:00:00")
    assert hi == pd.Timestamp("2026-09-19 00:30:00")


def test_kind_capabilities():
    info = get_kind_info("Processed_Wind_Profile")
    assert info.supported
    # Processed Wind Profile: Profile + History, never RHI/PPI
    assert set(info.modes) == {"profile", "timeseries"}

    for kind in ("VAD", "Stare", "Wind_Profile"):
        raw = get_kind_info(kind)
        assert raw.supported
        assert raw.modes[0] == "timeseries"   # History stays the default
        assert set(raw.modes) == {"timeseries", "rhi", "ppi"}

    rhi = get_kind_info("RHI")
    assert rhi.supported
    # RHI files: no Profile any more; the RHI view is the default
    assert rhi.modes[0] == "rhi"
    assert set(rhi.modes) == {"rhi", "timeseries", "ppi"}

    unknown = get_kind_info("Something_Else")
    assert not unknown.supported
    assert unknown.modes == ()
