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

# Runtime dependencies that may be missing where the docs are built
# (e.g. a CI job that only installs Sphinx). Any that can't be imported
# are replaced by Sphinx's mock modules -- both for the import of
# haloviewer right here (project information) and for autodoc -- so the
# build never fails just because numpy/matplotlib/... aren't installed.
# Installing the package itself (``pip install -e ".[docs]"``) gives the
# most accurate API pages and the real version number.
_OPTIONAL_DEPS = ["numpy", "pandas", "matplotlib", "mpl_toolkits",
                  "scipy", "tkinter"]


def _missing(modules):
    import importlib.util
    out = []
    for name in modules:
        try:
            if importlib.util.find_spec(name) is None:
                out.append(name)
        except (ImportError, ValueError):
            out.append(name)
    return out


autodoc_mock_imports = _missing(_OPTIONAL_DEPS)

from sphinx.ext.autodoc.mock import mock  # noqa: E402

with mock(autodoc_mock_imports):
    import haloviewer  # noqa: E402
    from haloviewer import _metadata as meta  # noqa: E402
    from haloviewer import cli as _cli  # noqa: E402  (for _write_cli_help)


def _release() -> str:
    """The installed version, or -- in a source tree that was never
    installed (no generated ``_version.py``) -- one computed directly
    from the git history by setuptools-scm, if that is available."""
    v = haloviewer.__version__
    if v != "0+unknown":
        return v
    try:
        from setuptools_scm import get_version
        return get_version(root=str(ROOT), fallback_version="0.1.0")
    except Exception:
        pass
    # last resort without setuptools-scm: the latest git tag (v0.2.0 ->
    # 0.2.0). A CI clone must fetch tags for this (GIT_DEPTH=0 or
    # "git fetch --tags").
    try:
        import subprocess
        tag = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"], cwd=ROOT,
            capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()
        return tag[1:] if tag.startswith("v") else (tag or v)
    except Exception:
        return v


# -- Project information ---------------------------------------------------

project = meta.__product__
author = meta.__author__
copyright = f"2026, {meta.__author__}, {meta.__affiliation__}"
release = _release()
version = ".".join(release.split(".")[:2])

# -- General configuration -------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
]

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
    cli = _cli
    out = DOCS / "_generated"
    out.mkdir(exist_ok=True)
    parser = cli.build_parser()
    os.environ["COLUMNS"] = "78"
    (out / "haloplot_help.txt").write_text(parser.format_help(),
                                           encoding="utf-8")


def setup(app):
    app.connect("builder-inited", _write_cli_help)
