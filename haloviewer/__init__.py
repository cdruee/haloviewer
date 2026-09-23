# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5
"""
haloviewer
==========

Viewer and plotting tools for Halo Photonics wind lidar data (``.hpl``
files), covering the processed-product tree (``Proc/YYYY/YYYYMM/YYYYMMDD``)
first, with raw-scan support intended to follow.

The package is split into independent layers so each can be used, tested
and reasoned about on its own:

``hpl``
    File-format parser for ``.hpl`` files (regular scan files and the
    header-less "Processed Wind Profile" variant), adapted from
    cdruee/python-readmet's ``readmet.hpl`` module.

``scan``
    Directory scanning / indexing: finds ``.hpl`` files under a root
    directory, classifies them by "kind" (scan type) from their filename,
    and indexes them by timestamp for browsing.

``plotting``
    Pure matplotlib plotting functions. Take data in and axes/figures to
    draw on; know nothing about any GUI toolkit.

``api``
    Small programmatic entry points (``plot`` / ``plot_file`` /
    ``plot_files``) for scripting and notebooks.

``cli``
    Command-line interface built on top of ``api``.

``gui``
    Tkinter desktop application built on top of ``scan`` and ``plotting``.

The high-level :func:`plot` function is re-exported at the package level,
so ``haloviewer.plot(path, kind=..., ...)`` works directly::

    import haloviewer
    haloviewer.plot("Proc/2026/202609/20260919", mode="history",
                          start="24h", output="day.png")
"""

from .api import plot, plot_file, plot_files

__all__ = ['plot', 'plot_file', 'plot_files']

__version__ = "0.1.0"
