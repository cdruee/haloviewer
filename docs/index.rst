.. HaloViewer documentation master file

==========
HaloViewer
==========

A viewer and plotting toolkit for Halo Photonics wind lidar data
(``.hpl`` files) covering the **processed-product** tree
(``Proc/YYYY/YYYYMM/YYYYMMDD/*.hpl``). Every kind found there is
plottable: the instrument-processed **Processed Wind Profile** (a
single scan's height/speed/direction profile, or many combined into a
height/time History image), and the raw regular-scan kinds --
**VAD**, **Stare**, **Wind Profile** and **RHI** -- each of which gets
a distance/time intensity+beta History image built from their raw
per-gate data (no instrument-processed profile of their own), plus two
single-scan views of radial velocity and beta: **RHI** (the scan's
points projected onto the vertical plane along the first ray's
azimuth) and **PPI** (projected onto the horizontal plane), each drawn
as dots or, optionally, filled by inverse-distance-weighted interpolation.
Any other kind found on disk is still listed (so you can see what's in
your data tree) but reported as not yet implemented.

Installation
------------

Conda (recommended -- minimal footprint on top of miniconda)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code:: bash

   conda env create -f environment.yml
   conda activate haloviewer

This only adds ``numpy``, ``pandas``, ``matplotlib`` and ``scipy`` on top
of a base Python; the GUI toolkit (Tkinter) ships with the standard
``python`` conda package on Linux, macOS and Windows, so nothing extra is
needed for the GUI itself. Tested against Python 3.9+ on Ubuntu 22.04,
24.04 and 26.04, macOS and Windows.

Plain pip
~~~~~~~~~

.. code:: bash

   pip install -e .

(On some minimal Linux distributions Tkinter is a separate OS package,
e.g. ``sudo apt install python3-tk`` on Debian/Ubuntu -- not needed when
using the conda environment above, since conda's ``python`` package
already includes it.)

To build this documentation as well, install the ``docs`` extra and run
Sphinx from the project root; the HTML ends up in ``build/html/``:

.. code:: bash

   pip install -e ".[docs]"
   sphinx-build -b html docs build/html

Version numbers come from the git history via `setuptools-scm
<https://setuptools-scm.readthedocs.io>`_: tag a release (e.g.
``git tag v0.2.0``) and reinstall, and :data:`haloviewer.__version__`
follows. Without git metadata the version falls back to ``0.1.0``.

Contents
--------

HaloViewer provides four ways to work with the data:

:doc:`viewer` -- ``haloviewer``
   A Tkinter desktop application for browsing a ``Proc`` tree: pick a
   directory, a file kind and a plot type (*Profile*, *History*, *RHI*
   or *PPI*), then step through single files or whole time windows, with
   adjustable height, distance and speed ranges.

   .. code:: bash

      haloviewer /path/to/Data/Proc

:doc:`cli` -- ``haloplot``
   Plots files, directories or glob patterns straight to an image file,
   without the GUI. File kind, plot mode and time range are inferred
   where possible and can be set explicitly.

   .. code:: bash

      haloplot Proc/2026/202609 --kind RHI --start 24h -p rhi_24h.png

:doc:`sync` -- ``halosync``
   A separate GUI for selectively copying the ``Metek``, ``Proc`` and
   ``Raw`` trees from the lidar control PC to a backup or analysis disk.
   It skips files that are already there, leaves out the file the lidar
   is still writing, and can run on a fixed schedule.

   .. code:: bash

      halosync

:doc:`api` -- ``haloviewer.plot()``
   The same functionality as ``haloplot`` for scripts and notebooks. It
   returns a matplotlib :class:`~matplotlib.figure.Figure` for further
   customisation.

   .. code:: python

      import haloviewer
      fig = haloviewer.plot("Proc/2026/202609/20260919",
                            kind="Processed_Wind_Profile", start="24h")

.. toctree::
   :maxdepth: 2
   :hidden:

   viewer
   cli
   sync
   api

Design
------

File structure
~~~~~~~~~~~~~~

The code is layered so each piece can be used, tested and understood
on its own:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - module
     - responsibility
   * - :mod:`haloviewer.hpl`
     - ``.hpl`` file format parser (adapted from the ``hpl`` module of
       `cdruee/python-readmet <https://github.com/cdruee/python-readmet>`_)
   * - :mod:`haloviewer.scan`
     - finds ``.hpl`` files under a root directory, classifies them by
       kind from the filename, indexes by timestamp
   * - :mod:`haloviewer.data`
     - turns parsed files into plain numpy/pandas arrays ready to plot
   * - :mod:`haloviewer.plotting`
     - **pure matplotlib**, no GUI toolkit imports: figure/axes creation
       and drawing functions
   * - :mod:`haloviewer.api`
     - programmatic entry points (``plot``, ``plot_file``,
       ``plot_files``); ``plot`` also re-exported as ``haloviewer.plot``
   * - ``haloviewer.cli``
     - command-line interface (``haloplot``) on top of ``api``
   * - ``haloviewer.gui``
     - Tkinter desktop app (``haloviewer``); wires widgets to
       ``scan``/``data``/``plotting`` and contains no plotting logic
       itself
   * - ``haloviewer.halosync``
     - standalone data-sync GUI (``halosync``); standard library only,
       independent of the other modules
   * - ``haloviewer._metadata``, ``haloviewer._version``
     - static project information; version number generated by
       setuptools-scm

``plotting.py`` never imports ``tkinter``, and ``gui.py`` never calls
matplotlib drawing primitives directly -- it only calls functions in
``plotting.py``. This means the exact same plotting code is used by the
GUI, the CLI, and any script that imports :mod:`haloviewer.api`.

Extending to more scan kinds
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Processed_Wind_Profile``, ``VAD``, ``Stare``, ``Wind_Profile`` and ``RHI`` are
all implemented; a user-defined pattern (``User1``...\ ``User5`` in the raw
``.hpl`` header's ``Scan type``) or a future Halo scan kind would follow
the same recipe:

1. Add a reader to ``data.py`` that turns a parsed ``hpl.DataFile`` (or a
   set of them) into plain arrays. A regular scan file can usually reuse
   ``load_scan_history`` and ``load_scan_points`` as they are.
2. Add drawing function(s) to ``plotting.py`` that take those arrays and
   axes/figure objects -- reuse ``create_timeseries_figure``'s two
   stacked, colour-mapped panels (all History flavours) or
   ``create_scan_pair_figure``'s two side-by-side panels (the RHI/PPI
   views) if one of those shapes fits.
3. Register the kind's capabilities in ``scan.KIND_CAPABILITIES``
   (``supported=True``, its plot modes).
4. Wire the new mode(s) into ``api.plot_file``/``plot_files``, and into
   ``gui.HaloViewerApp._plot_kind`` (which of the five load/render
   pipelines applies) and ``_update_range_controls_enabled`` (which of
   Height/Distance/Speed/Fill make sense for it).

The GUI will then automatically offer that kind and mode as soon as it
is discovered on disk -- no other GUI changes are needed.

Tests
~~~~~


.. code:: bash

   pip install -e ".[dev]"
   pytest

Licence
-------

|product| is licensed under the European Union Public Licence v1.2
(EUPL-1.2); see the ``LICENSE`` file in the project root for the full
licence text.

``haloviewer/hpl.py`` is adapted from the ``hpl`` module of
`cdruee/python-readmet <https://github.com/cdruee/python-readmet>`_,
which is itself licensed under the EUPL-1.2.

Copyright
---------

|copyright|

Developed with support of Anthropic Claude Opus 5.5.
