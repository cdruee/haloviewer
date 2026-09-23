===============================
Graphic viewer (``haloviewer``)
===============================

The graphic viewer is a Tkinter desktop application for browsing a
Halo wind lidar ``Proc`` tree interactively. It uses exactly the same
plotting code as :doc:`cli` and :doc:`api`, so what you see on screen
is what ``haloplot`` writes to a file.

Starting the viewer
-------------------

.. code:: bash

   haloviewer                     # then pick a directory from the GUI
   haloviewer /path/to/Data/Proc   # or start with a directory already loaded

The optional argument is the root directory to load on start-up.

Window layout
-------------

Layout: a settings panel on the left (root directory picker, file-kind
list, plot-type selector, Height/Distance/Speed range controls,
start/end time range with quick presets, First/Back/Forward/Last
browse buttons) and the plot on the right, with the standard
matplotlib navigation toolbar (zoom/pan/save) underneath it. The root
directory can be the ``Proc`` folder itself or any directory below it
(e.g. a single year, month, or day); the tree is rescanned whenever a
new directory is picked or typed in and confirmed with Enter.

Kinds and plot modes
--------------------

The file-kind list shows every kind found below the root directory.
Kinds the viewer cannot plot yet (e.g. ``User1`` ... ``User5``) are
still listed, so you can see what is in the tree, but selecting them
only shows a "not yet supported" message. The plot-type selector
switches between **Profile** (a single scan) and **History** (all files
in the selected time range combined into one time/height image).
Whichever mode a kind doesn't support is greyed out.

Processed_Wind_Profile
~~~~~~~~~~~~~~~~~~~~~~


-  **Profile** mode plots one file as two side-by-side panels sharing a
   height axis: wind speed on the left, wind direction on the right,
   with a small gap between them. The panel geometry (axis box size and
   position) is fixed once a kind/mode/time-range is chosen, so
   stepping through files with First/Back/Forward/Last never makes the
   plot "wobble".
-  **History** mode combines every file in the selected time range into
   a height-vs-time image, with two panels stacked vertically (speed on
   top, direction below) sharing a time axis. Speed uses the
   colorblind-friendly sequential ``viridis`` colormap; direction (a
   cyclic quantity) uses matplotlib's perceptually-uniform cyclic
   ``twilight`` colormap.

VAD, Stare and Wind_Profile
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Raw regular scans with no instrument-processed profile of their own.

-  **History** (their only mode -- Profile is greyed out) combines
   every file in the selected time range into a distance-vs-time image
   of the raw per-gate data, two panels stacked vertically: intensity
   (SNR + 1) on top using the ``cividis`` colormap, attenuated backscatter
   (beta) below using ``magma``. The vertical axis is distance inferred
   purely from range gates (gate index × gate length), not scan
   geometry -- individual rays are never plotted; instead all rays
   across the loaded files are averaged into "round" time bins (10s,
   15s, 30s, 1 min, ... up to a week) sized so the image is roughly
   100-250 pixels wide regardless of how long a span is selected. A time
   bin with no rays in it is left as ``NaN``, which renders as blank
   background rather than an interpolated guess.

RHI
~~~


-  **Profile** mode plots one scan as two stacked panels sharing both a
   distance (horizontal) and height (vertical) axis: radial (Doppler)
   velocity on top using the diverging ``PuOr`` colormap (chosen to avoid
   the red/green endpoints most likely to be confused under red-green
   colour vision deficiency), attenuated backscatter (beta) below using
   ``magma``. Each point is one range gate along one ray, plotted as an
   individual colour-coded dot -- not gridded, since RHI rays don't
   share a common distance/height grid the way a fixed scan geometry
   would -- with its distance and height computed from that ray's own
   azimuth/elevation and the instrument's pitch/roll tilt correction.
-  **History** mode is the same raw intensity/beta scan history
   described above for VAD/Stare/Wind_Profile.

Height, Distance and Speed ranges
---------------------------------

Three range controls share the same
shape -- Bottom/Top (or Near/Far, or Min/Max) fields, each with its own
▲/▼ stepper, plus an **Auto** checkbox. With Auto checked (the
default) the fields are read-only and just display the current
autoscaled range; uncheck it to type your own values or use the
steppers (the step size scales with the current span, from a fraction
of a unit up to a capped maximum for very wide views; the span can't
be shrunk below a small minimum). Each stepper click snaps the field
to a round multiple of that step size rather than just nudging
whatever value is currently shown. A manual range is applied
instantly, without re-reading any files, and persists across
navigation and time-range changes until you switch Auto back on.

Which controls are enabled depends on the current file kind and plot
type -- a greyed-out control simply doesn't apply to what's on screen:

-  **Height** controls the shared vertical axis and is always enabled:
   height for Processed Wind Profile, gate-inferred distance for the
   raw scan kinds' History image, height for RHI's own Profile.
-  **Distance** controls the horizontal distance axis and is only
   enabled for RHI's Profile mode (its distance/height cross section);
   it's greyed out everywhere else.
-  **Speed** controls the wind-speed/radial-velocity axis or colour
   range, and is enabled everywhere that has a speed dimension --
   Processed Wind Profile (both Profile and History) and RHI's Profile
   -- but greyed out for the raw scan kinds' History image, whose two
   panels are intensity and beta, not speed.

Time range and presets
----------------------

The "Time" frame holds Start time, a row (wrapped over two
lines) of quick-range radio buttons, and End time. The presets --
Custom, Week, 2 days, 24h, 12h, 6h -- set Start time to End time minus
that offset (End time itself is left as-is) and immediately reload;
Start time is editable only when Custom is selected. Changing End time
while a preset other than Custom is active recomputes Start time from
it.

Browsing files and time windows
-------------------------------

The **First/Back/Forward/Last** buttons have two meanings, depending on
the active time preset.

With a fixed-length preset active, **Browse files** changes meaning:
instead of stepping through individual files, First/Back/Forward/Last
move the whole [Start, End] window, and stay active in both Profile and
History mode (under Custom they only step files, one at a time, and
only in Profile mode, as before). First jumps the window to the true
start of this kind's data; Last jumps it to the true end. Back/Forward
shift the window by exactly one interval; the resulting End time is
snapped to a grid of interval-length multiples anchored at the start of
its year (e.g. with 24h selected, End always lands on a whole
day-since-Jan-1 boundary), so repeated stepping can't drift off round
numbers the way plain addition would.

Zooming and panning
-------------------

The plot's own zoom/pan tools (in the toolbar under it) work as usual;
holding ``x`` or ``y`` while dragging the zoom-rectangle constrains it to
one axis. Profile mode's two panels share the height (or, for RHI,
also the distance) axis, and History mode's two panels share both the
time and height/distance axes, so zooming/panning either one keeps the
pair in sync.

Colour maps
-----------

All colour maps are chosen to be readable with the most common forms of
colour vision deficiency:

.. list-table::
   :header-rows: 1
   :widths: 30 25 45

   * - quantity
     - colour map
     - note
   * - wind speed
     - ``viridis``
     - sequential
   * - wind direction
     - ``twilight``
     - cyclic, so 359° and 1° get similar colours
   * - intensity (SNR + 1)
     - ``cividis``
     - sequential
   * - attenuated backscatter (beta)
     - ``magma``
     - sequential
   * - radial (Doppler) velocity
     - ``PuOr``
     - diverging, centred on zero; avoids red/green endpoints

Wind-speed autoscaling is capped at 50 m/s, so a few noisy gates don't
squash the rest of the profile. Intensity and beta scale to the
2nd-98th percentile of the data shown.

Troubleshooting
---------------

* **The window doesn't open / "No module named tkinter".** On some
  Linux distributions Tkinter is a separate OS package (e.g.
  ``sudo apt install python3-tk``). The conda environment already
  contains it.
* **A kind is missing from the list.** Only files whose names follow
  the Halo convention
  ``<Type words>_<system id>_<yyyymmdd>_<hhmmss>.hpl`` are recognised.
  Other files in the tree are ignored.
* **Some files of a History are skipped.** Truncated files (e.g. a scan
  that was still being written when it was copied) are read up to their
  last complete ray. Unreadable files are skipped with a warning
  instead of blanking the whole plot.
