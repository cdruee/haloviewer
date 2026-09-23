# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5

import numpy as np
import pytest


@pytest.fixture
def proc_tree(tmp_path):
    """Build a tiny synthetic Proc tree with a handful of
    Processed_Wind_Profile files plus one unsupported/unrelated kind and
    one non-matching junk file, and return its root."""
    root = tmp_path / "Data" / "Proc" / "2026" / "202609" / "20260919"
    root.mkdir(parents=True)
    heights = np.arange(9.0, 9.0 + 30 * 5, 30.0)
    for i, (hh, mm) in enumerate([(0, 0), (0, 15), (0, 30)]):
        fname = f"Processed_Wind_Profile_77_20260919_{hh:02d}{mm:02d}00.hpl"
        lines = [f"{len(heights)}"]
        for h in heights:
            d = (180 + i * 10) % 360
            s = 3.0 + i
            lines.append(f"{h:6.1f} {d:7.2f} {s:6.2f}")
        (root / fname).write_text("\n".join(lines) + "\n")
    (root / "User1_77_20260919_080000.hpl").write_text("Filename:\tx\n")
    (root / "notes.txt").write_text("not an hpl file\n")
    return tmp_path / "Data" / "Proc"
