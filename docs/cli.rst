================================
Command-line tool (``haloplot``)
================================

``haloplot`` plots Halo wind lidar ``.hpl`` files to an image file (or
an interactive window) without starting the GUI. It is a thin wrapper
around :func:`haloviewer.plot <haloviewer.api.plot>` (see :doc:`api`): every option maps to a
keyword argument of that function, so the command line and scripts
behave identically.

Synopsis
--------

.. code:: text

   haloplot FILE [FILE ...] [-k KIND] [--mode {profile,history,rhi,ppi}]
            [-s START] [-t TIME] [--height MIN MAX] [--dist MIN MAX]
            [--speed MIN MAX] [--filter VALUE] [--fill] [-p PATH]
            [--show] [--figsize WIDTH HEIGHT] [--verbose]

How the plot is chosen
----------------------

``haloplot`` works through four steps. Each has a sensible default, so
in the simplest case a single file name is enough.

1. **Files.** Each ``FILE`` can be

   * a single ``.hpl`` file,
   * a directory, which is searched recursively for ``.hpl`` files, or
   * a glob pattern (containing ``*``, ``?`` or ``[``; ``**`` also
     matches sub-directories). Quote the pattern so the shell passes it
     through unexpanded.

   Any mix of these can be given. A pattern that matches nothing, a
   missing path, or a file whose name doesn't follow the Halo naming
   convention (``<Type words>_<system id>_<yyyymmdd>_<hhmmss>.hpl``)
   produces a warning and is skipped. It does not abort the run.

2. **Kind.** The file kind (``Processed_Wind_Profile``, ``VAD``,
   ``Stare``, ``Wind_Profile``, ``RHI``) is read from each file name. If
   all files are the same kind, nothing needs to be specified. If they
   span several kinds (typically when a whole directory is given),
   ``-k/--kind`` is required. The error message lists the kinds that
   were found.

3. **Time range.** ``-t/--time`` sets the end of the range. It defaults
   to the timestamp of the newest selected file, which for a single file
   is simply that file's own time. ``-s/--start`` sets the beginning,
   either as an absolute timestamp (``"2026-09-19 06:00"``) or as an
   interval back from the end: ``24h``, ``2d``, ``2.5d`` (days and hours,
   case-insensitive, fractions allowed). Without ``--start`` there is no
   lower limit. If no file falls into the range, ``haloplot`` stops with
   an error.

4. **Mode.** ``--mode history`` combines all files in the range into a
   time/height (or time/distance) image. The other three modes show a
   single file: ``--mode profile`` a processed wind profile,
   ``--mode rhi`` and ``--mode ppi`` one scan's data points projected
   onto the vertical or the horizontal plane (see :doc:`viewer` for the
   coordinate frame: x points along the azimuth of the scan's first
   ray). By default, the kind's own single-file view -- *profile* for
   ``Processed_Wind_Profile``, *rhi* for ``RHI`` -- is used when exactly
   one file is in range, otherwise *history*. If a single-file mode is
   requested while several files are in range, the newest file at or
   before the end time is plotted.

Which kinds support which mode:

.. list-table::
   :header-rows: 1
   :widths: 28 18 18 18 18

   * - kind
     - profile
     - history
     - rhi
     - ppi
   * - ``Processed_Wind_Profile``
     - speed + direction vs. height
     - speed + direction, time/height
     - --
     - --
   * - ``RHI``
     - --
     - intensity + beta, time/distance
     - velocity + beta, x/z
     - velocity + beta, x/y
   * - ``VAD``, ``Stare``, ``Wind_Profile``
     - --
     - intensity + beta, time/distance
     - velocity + beta, x/z
     - velocity + beta, x/y

The plots themselves are the same as in the graphic viewer and are
described in more detail in :doc:`viewer`.

Axis ranges
-----------

By default all axes and colour scales are autoscaled. Three options fix
them instead, each taking a minimum and a maximum:

``--height MIN MAX``
   The shared vertical axis (height, or gate distance for the raw scan
   kinds' history image). Not used for ppi, which has no height axis.
``--dist MIN MAX``
   The horizontal distance axis of rhi plots (MIN can be negative). For
   ppi only MAX is used: both axes then span -MAX .. +MAX.
``--speed MIN MAX``
   The wind-speed / radial-velocity axis or colour range. Used wherever
   speed is plotted (not in the raw scan kinds' intensity/beta history).

If one of these doesn't apply to the selected kind and mode,
``haloplot`` prints a warning and ignores it.

Fill
----

``--fill`` (rhi and ppi only) fills the area between the data points by
nearest-neighbour interpolation instead of drawing one dot per point.
Only the area inside the outline of the data points is filled, and
points removed by ``--filter`` stay blank. For other modes it is
ignored with a warning.

Intensity filter
----------------

Low-signal data can be hidden with the intensity filter. It is off by
default. ``--filter VALUE`` blanks every data point whose intensity
(SNR + 1) is below ``VALUE``. ``--filter True`` uses the default
threshold of 1.018 (:data:`haloviewer.data.DEFAULT_INTENSITY_FILTER`).
Anything else that isn't a number is an error.

What gets blanked depends on the kind:

* ``VAD``, ``Stare``, ``Wind_Profile`` and ``RHI`` history: intensity
  and beta of every gate below the threshold. The gates are dropped
  before time binning, so a bin that only held such gates stays blank.
* rhi and ppi plots: radial velocity and beta of those points.
* ``Processed_Wind_Profile`` (profile and history): wind speed and
  direction. These files carry no intensity of their own, so it is
  taken from the ``Wind_Profile`` scan file with the same system id and
  timestamp in the same directory. Level *n* of the processed profile
  (its *n*-th row, in metres of height) belongs to range gate *n* of
  the scan (counted in gate numbers); the intensity of that gate is
  averaged over all beams of the scan. A
  profile without a matching ``Wind_Profile`` file is plotted
  unfiltered, and a warning says how many were affected.

When the filter is on, the plot title ends with
``(intensity < VALUE removed)``.

Output
------

``-p/--plot PATH``
   Save the figure to ``PATH``. The format follows the extension
   (``.png``, ``.pdf``, ``.svg``, ... -- anything matplotlib supports).
``--show``
   Open the figure in an interactive matplotlib window. Can be combined
   with ``-p``.

If neither is given, the figure is saved as ``plot.png`` in the current
directory, and a note saying so is printed.

Figures are A4 landscape (11.69 x 8.27 in) by default; ``--figsize
WIDTH HEIGHT`` (inches) changes that. There is no font-size option: the
base font size scales with the figure area. It is 16 pt at A4 landscape
(:data:`haloviewer.api.BASE_FONTSIZE_AT_A4`) and proportionally smaller
or larger for other sizes, so text keeps the same relative size.

Messages and exit status
------------------------

Advisory messages (skipped files, options that don't apply, profiles
the intensity filter couldn't be applied to) are printed to standard
error as ``warning: ...`` lines, and the plot is still made.
Problems that prevent a plot (no files found, ambiguous kind, empty time
range, unsupported kind/mode) are printed as ``error: ...`` and the exit
status is 1. ``--verbose`` additionally prints debug logging, e.g. about
truncated files. On success the exit status is 0.

Examples
--------

.. code:: bash

   # a single profile -- kind and "profile" mode both inferred, since
   # there's exactly one matching file
   haloplot Processed_Wind_Profile_77_20260919_121707.hpl -p profile.png

   # a whole day as a time-height plot: a directory is searched recursively,
   # a glob pattern is expanded -- both work the same way
   haloplot Proc/2026/202609/20260919 \
       --kind Processed_Wind_Profile -p 20260919_history.png
   haloplot "Proc/2026/202609/20260919/Processed_Wind_Profile_*.hpl" \
       --mode history -p 20260919_history.png

   # -k/--kind is required only when FILE resolves to more than one kind
   # (e.g. a directory or pattern that covers several scan types)
   haloplot Proc/2026/202609 --kind RHI -p rhi_all.png

   # a time range: -s/--start ("##d"/"##h" relative to the end time, or an
   # absolute timestamp) and -t/--time (the end timestamp, default: the
   # latest matching file's own timestamp)
   haloplot Proc/2026/202609 --kind RHI \
       --start 24h --time "2026-09-19 12:00" -p rhi_24h.png

   # a single RHI scan (the default for one RHI file), filled, with
   # fixed distance and height ranges
   haloplot RHI_77_20260921_000812.hpl --fill \
       --dist -2000 6000 --height 0 3000 -p rhi.png

   # a single VAD scan in the horizontal plane, +-2 km
   haloplot VAD_77_20260921_000721.hpl --mode ppi --dist 0 2000 -p ppi.png

   # fix axis ranges instead of autoscaling (--height/--dist/--speed warn,
   # but don't fail, if they don't apply to the selected kind/mode)
   haloplot some_profile.hpl --height 0 3000 --speed 0 20 -p profile.png

   # hide low-signal data: "True" uses the default threshold 1.018,
   # or give a threshold of your own
   haloplot Proc/2026/202609/20260919 --kind VAD --filter True -p vad.png
   haloplot Proc/2026/202609/20260919 --kind Processed_Wind_Profile \
       --filter 1.05 -p wind_filtered.png

   # open interactively instead of saving
   haloplot some_file.hpl --show

   # neither -p/--plot nor --show given -> saved as "plot.png"
   haloplot some_file.hpl

Option reference
----------------

This is the output of ``haloplot --help``:

.. literalinclude:: _generated/haloplot_help.txt
   :language: text
