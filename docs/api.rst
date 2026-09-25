===================
Python API
===================

Everything the graphic viewer and ``haloplot`` can draw is also
available from Python, e.g. in a script or a Jupyter notebook. The
functions return a matplotlib :class:`~matplotlib.figure.Figure`, which
can be customised further before saving.

Quick start
-----------

:func:`haloviewer.plot <haloviewer.api.plot>` is the high-level entry point -- the same path
resolution, kind/mode inference and time-range selection as the CLI,
available directly from a script or notebook:

.. code:: python

   import haloviewer

   # single file -> "profile" mode inferred (a lone match); kind inferred
   # from the filename
   fig = haloviewer.plot(
       "Processed_Wind_Profile_77_20260919_121707.hpl", output="profile.png")

   # a directory or glob pattern with more than one file kind needs kind=;
   # a time range narrows which files are included
   fig = haloviewer.plot(
       "Proc/2026/202609", kind="RHI", start="24h",
       end="2026-09-19 12:00", height=(0, 3000), speed=(0, 20),
       output="rhi_24h.png")

   # one scan projected onto the vertical ("rhi") or horizontal ("ppi")
   # plane, x along the first ray's azimuth; fill=True fills between the
   # points by inverse-distance-weighted interpolation. For "ppi" only the max
   # of distance= is used (both axes span -max..+max).
   fig = haloviewer.plot("RHI_77_20260921_000812.hpl", mode="rhi",
                         fill=True, output="rhi.png")
   fig = haloviewer.plot("VAD_77_20260921_000721.hpl", mode="ppi",
                         distance=(0, 2000), output="ppi.png")

   # fontsize defaults to scaling with figsize (16pt at A4 landscape,
   # proportionally smaller/larger otherwise); pass it explicitly to
   # override that
   fig = haloviewer.plot("some_profile.hpl", figsize=(6, 4), fontsize=10)

Note ``distance=`` is the API-level name for what the CLI calls ``--dist``
(kept short on the command line only).

.. _lower-entry-points:

:func:`~haloviewer.api.plot_file` and :func:`~haloviewer.api.plot_files` remain available (also re-exported at the
package level) for callers that have already resolved an exact file or
file list of one known kind, and skip path/kind/time-range resolution:

.. code:: python

   import glob
   from haloviewer import plot_file, plot_files

   # single file -> profile plot
   fig = plot_file("Processed_Wind_Profile_77_20260919_121707.hpl",
                    output="profile.png")

   # multiple files of the same kind -> a history (time series) plot
   fig = plot_files(sorted(glob.glob("Proc/2026/202609/20260919/"
                                      "Processed_Wind_Profile_*.hpl")),
                     output="history.png")

Warnings and errors
-------------------

Advisory conditions (a glob pattern matching nothing, a missing path, a
file name that doesn't follow the Halo convention, a ``height=``,
``distance=``, ``speed=`` or ``fill=`` setting that doesn't apply) are
issued with :func:`warnings.warn` and don't stop the plot. They can be
filtered or turned into errors with the standard :mod:`warnings`
machinery:

.. code:: python

   import warnings
   import haloviewer

   with warnings.catch_warnings():
       warnings.simplefilter("error")     # make every warning fatal
       haloviewer.plot("Proc/2026/202609", kind="VAD", speed=(0, 20))

Conditions that make a plot impossible raise :class:`ValueError`
(no files, ambiguous or unknown kind, empty time range, a mode the kind
doesn't support) or :class:`NotImplementedError` (a kind that isn't
supported at all).

Package information
-------------------

.. py:data:: haloviewer.__version__

   Version string, derived from the git history by setuptools-scm at
   install time (``"0+unknown"`` when running from a source tree that
   was never installed).

Static project information is available as ``haloviewer.__title__``,
``__product__``, ``__description__``, ``__author__``, ``__email__``,
``__license__``, ``__copyright__`` and ``__credits__`` (defined in
``haloviewer/_metadata.py``).

Plotting functions
------------------

These three functions are re-exported at package level, so
``haloviewer.plot`` and ``haloviewer.api.plot`` are the same object.

.. automodule:: haloviewer.api
   :members: plot, plot_file, plot_files, DEFAULT_FIGSIZE, BASE_FONTSIZE_AT_A4

Lower-level modules
-------------------

The plotting functions above are built from four independent layers,
which can also be used directly, e.g. to read ``.hpl`` files into
arrays for your own analysis.

``haloviewer.hpl`` -- file format
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: haloviewer.hpl

``haloviewer.scan`` -- finding and classifying files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: haloviewer.scan

``haloviewer.data`` -- data extraction
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: haloviewer.data

``haloviewer.plotting`` -- drawing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: haloviewer.plotting
