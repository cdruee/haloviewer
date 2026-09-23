# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5
"""
Sphinx configuration for the HaloViewer documentation.

Build from the project root with::

    sphinx-build -b html docs build/html

Project information comes from ``haloviewer/_metadata.py``, the version
from setuptools-scm (``haloviewer/_version.py``, written on install).
"""

import os
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent
sys.path.insert(0, str(ROOT))

# Plotting code must not try to open windows while autodoc imports it.
os.environ.setdefault("MPLBACKEND", "Agg")

import haloviewer  # noqa: E402
from haloviewer import _metadata as meta  # noqa: E402

# -- Project information ---------------------------------------------------

project = meta.__product__
author = meta.__author__
copyright = f"2026, {meta.__author__}, {meta.__affiliation__}"
release = haloviewer.__version__
version = ".".join(release.split(".")[:2])

# -- General configuration -------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
]

# Tkinter is not always installed where the docs are built (e.g. a
# headless CI runner). The GUI modules are only documented in prose, but
# mock it anyway so an accidental import never breaks the build.
try:
    import tkinter  # noqa: F401
except ImportError:
    autodoc_mock_imports = ["tkinter"]

autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_default_options = {"members": True, "undoc-members": False}

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "matplotlib": ("https://matplotlib.org/stable", None),
    "pandas": ("https://pandas.pydata.org/docs", None),
    "numpy": ("https://numpy.org/doc/stable", None),
}

templates_path = []
exclude_patterns = ["_build", "_generated"]

rst_epilog = f"""
.. |product| replace:: {meta.__product__}
.. |copyright| replace:: {meta.__copyright__.replace("(c)", chr(92) + "(c)")}
.. |credits| replace:: {meta.__credits__}
"""

# -- HTML output -----------------------------------------------------------

html_theme = "nature"
html_title = f"{meta.__product__} {release}"
html_static_path = []
html_show_sourcelink = False


# -- Generated content -----------------------------------------------------

def _write_cli_help(app):
    """Write ``haloplot --help`` to docs/_generated/ so the CLI page
    always shows the option list of the code actually being documented."""
    from haloviewer import cli
    out = DOCS / "_generated"
    out.mkdir(exist_ok=True)
    parser = cli.build_parser()
    os.environ["COLUMNS"] = "78"
    (out / "haloplot_help.txt").write_text(parser.format_help(),
                                           encoding="utf-8")


def setup(app):
    app.connect("builder-inited", _write_cli_help)
