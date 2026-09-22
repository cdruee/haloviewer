"""
windlidarviewer
================

Viewer and plotting tools for Halo Photonics WindLidar data (``.hpl``
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
    Small programmatic entry points (``plot_file`` / ``plot_files``) for
    scripting and notebooks.

``cli``
    Command-line interface built on top of ``api``.

``gui``
    Tkinter desktop application built on top of ``scan`` and ``plotting``.
"""

__version__ = "0.1.0"
