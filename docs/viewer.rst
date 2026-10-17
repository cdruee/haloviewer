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

   haloviewer                     # the directory used last time (or pick one)
   haloviewer /path/to/Data/Proc   # start with this directory
   haloviewer --info              # show the remembered settings, cache size

The optional argument is the root directory to load on start-up;
without it, the viewer opens the directory it showed last time. The
cache options ``--no-cache``, ``--tmp-cache``, ``--user-cache`` and
``--cache-dir DIR`` choose where the scan cache lives (see
:ref:`scan-cache`); ``-i``/``--info`` prints the remembered settings and
the cache location and size, and exits.

Remembered settings
~~~~~~~~~~~~~~~~~~~

On closing, the viewer remembers its root directory, file kind, plot
type, time preset, filter (on/off and value), Fill, and window size, and
restores them on the next start. They are kept, together with the cache
setting, in a small JSON file:

* Windows: ``%APPDATA%\haloviewer\settings.json`` (the roaming profile)
* elsewhere: ``~/.config/haloviewer/settings.json`` (or
  ``$XDG_CONFIG_HOME/haloviewer/settings.json``)

The environment variable ``HALOVIEWER_CONFIG`` names a different
directory. Deleting the file just resets everything to the defaults.

Window layout
-------------

Layout: a settings panel on the left (root directory picker, file-kind
drop-down, plot-type selector, intensity filter and Fill option,
Height/Distance/Speed range controls, start/end time range with quick
presets, First/Back/Forward/Last browse buttons) and the plot on the
right, with the standard matplotlib navigation toolbar
(zoom/pan/save) underneath it. The root directory can be the ``Proc``
folder itself or any directory below it (e.g. a single year, month, or
day); the tree is rescanned whenever a new directory is picked or
typed in and confirmed with Enter.

Kinds and plot types
--------------------

The file-kind drop-down lists every kind found below the root
directory, with the number of files of each kind. Kinds the viewer
cannot plot yet (e.g. ``User1`` ... ``User5``) are still listed, so you
can see what is in the tree, but selecting them only shows a "not yet
supported" message. The plot-type selector offers four types:

* **Profile** -- one processed wind profile (``Processed_Wind_Profile``
  only);
* **History** -- all files in the selected time range combined into
  one time/height (or time/distance) image (every kind);
* **RHI** and **PPI** -- one scan's data points projected onto a
  vertical or horizontal plane (the regular scan kinds ``VAD``,
  ``Stare``, ``Wind_Profile`` and ``RHI``).

Whichever type a kind doesn't support is greyed out.

.. list-table::
   :header-rows: 1
   :widths: 34 12 12 12 12

   * - kind
     - Profile
     - History
     - RHI
     - PPI
   * - ``Processed_Wind_Profile``
     - yes
     - yes
     - --
     - --
   * - ``RHI``
     - --
     - yes
     - yes (default)
     - yes
   * - ``VAD``, ``Stare``, ``Wind_Profile``
     - --
     - yes (default)
     - yes
     - yes

Profile (Processed_Wind_Profile)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Plots one file as two side-by-side panels sharing a height axis: wind
speed on the left, wind direction on the right, with a small gap
between them. The panel geometry (axis box size and position) is fixed
once a kind/type/time-range is chosen, so stepping through files with
First/Back/Forward/Last never makes the plot "wobble".

History
~~~~~~~

-  For ``Processed_Wind_Profile``, every file in the selected time
   range is combined into a height-vs-time image, with two panels
   stacked vertically (speed on top, direction below) sharing a time
   axis. Speed uses the colorblind-friendly sequential ``viridis``
   colormap; direction (a cyclic quantity) uses matplotlib's
   perceptually-uniform cyclic ``twilight`` colormap.
-  For the regular scan kinds (``VAD``, ``Stare``, ``Wind_Profile``,
   ``RHI``), every file in the selected time range is combined into a
   distance-vs-time image of the raw per-gate data, two panels stacked
   vertically: intensity (SNR + 1) on top using the ``cividis``
   colormap, attenuated backscatter (beta) below using ``magma``. The
   vertical axis is distance inferred purely from range gates (see
   `Range of a gate`_ below), not scan geometry -- individual rays are
   never plotted; instead all rays across the loaded files are
   averaged into "round" time bins (10s, 15s, 30s, 1 min, ... up to a
   week) sized so the image is roughly 100-250 pixels wide regardless
   of how long a span is selected. A time bin with no rays in it is
   left as ``NaN``, which renders as blank background rather than an
   interpolated guess.

Range of a gate
~~~~~~~~~~~~~~~

The along-beam range of a data point is the centre of its range gate,
following the convention stated in every ``.hpl`` header:

.. math::

   r = \frac{L}{2} + i \cdot \frac{L}{n}

with *i* the range gate index, *L* the ``Range gate length (m)`` and *n*
the ``Gate length (pts)`` header value. Neighbouring gates are therefore
*L*/*n* metres apart and overlap when *n* > 1; e.g. 18 m gates of 6 points
are centred at 9, 12, 15, ... m. If the header has no
``Gate length (pts)``, *n* = 1 is assumed.

RHI and PPI
~~~~~~~~~~~

Both show a single scan, as two side-by-side panels (one row, two
columns) sharing both axes: radial (Doppler) velocity on the left using
the diverging ``PuOr`` colormap (chosen to avoid the red/green
endpoints most likely to be confused under red-green colour vision
deficiency), attenuated backscatter (beta) on the right using
``magma``. Each data point is one range gate along one ray, drawn as a
colour-coded dot at its true position, computed from that ray's own
azimuth and elevation, the instrument's pitch/roll tilt correction,
and the gate's centre range (see `Range of a gate`_).

Positions are given in a level, instrument-centred frame that is
rotated to follow the scan itself:

* **x** points horizontally (elevation 0) in the direction of the
  azimuth of the *first ray in the file*. A ray whose azimuth is more
  than 90° away from that direction (e.g. the far side of an
  over-the-top RHI, or the back half of a VAD cone) therefore lies at
  *negative* x.
* **y** is horizontal and points 90° counter-clockwise from x seen from
  above (towards the first-ray azimuth minus 90°), so the PPI picture
  is an ordinary map view, rotated so that the first ray points to the
  right.
* **z** is the height above the instrument.

The axis labels show the azimuth each horizontal axis points to, e.g.
"Distance → 255.0° (m)".

-  **RHI** shows the projection of the points onto the vertical x/z
   plane: distance along x horizontally, height vertically.
-  **PPI** shows the projection onto the horizontal x/y plane. Both
   panels are square (equal scale on both axes) and span
   [-*max distance*, +*max distance*] in x and in y.

Fill
~~~~

Next to the intensity filter is the **Fill** checkbox (enabled only for
RHI and PPI). When it is ticked, the area between the data points is
filled by inverse-distance-weighted (Shepard) interpolation: every
pixel takes the weighted mean of the 8 data points closest to it, each
weighted by 1/*d*\ :sup:`2`. The distance *d* is measured in the scan's
own polar grid -- range in units of the gate spacing, angle in units of
the ray spacing -- rather than in metres. Gates along a ray are much
closer together than neighbouring rays, so in metres all nearby points
would come from the same ray and each ray would just paint its own
wedge; measured this way, the colours blend smoothly between
neighbouring gates *and* rays. Only
the area inside the outline (convex hull) of the data points is filled;
outside it the plot stays blank. Points blanked by the intensity filter
are left out of the averages, and pixels whose closest data point was
blanked stay blank too, so filtered-out areas are not covered up by
their neighbours. A scan whose
points all lie on one line in the plotted plane (e.g. a Stare scan in
PPI) can't be filled and is drawn as dots. Toggling Fill only redraws
-- no files are re-read.

Intensity filter
----------------

Below the plot-type selector is the line
**Filter (intensity <** *1.018* **)**: a checkbox and a value field. It
is off by default. When it is checked, every data point whose
intensity (SNR + 1) is below the value is left blank: intensity and
beta in the scan histories, radial velocity and beta in the RHI and
PPI views, and wind speed and direction for ``Processed_Wind_Profile``.

``Processed_Wind_Profile`` files carry no intensity of their own. The
viewer reads it from the ``Wind_Profile`` scan file with the same
timestamp in the same directory. Level *n* of the profile (given as a
height) belongs to range gate *n* of the scan (given as a gate number);
the intensity of that gate is averaged over all beams. If a
profile has no matching ``Wind_Profile`` file, it is shown unfiltered
and the status line below the browse buttons says so.

Ticking or unticking the box reloads the plot. A changed value takes
effect when you press Enter or leave the field. An invalid value is
reported and replaced by the default 1.018. In the single-file plot
types (Profile, RHI, PPI) the current file stays selected. While the
filter is on, the plot title ends with "(intensity < *value* removed)".
The same filter is available as ``--filter`` in :doc:`cli` and
``filter=`` in :doc:`api`.

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

-  **Height** controls the shared vertical axis: height for
   Processed Wind Profile and the RHI view, gate-inferred distance for
   the raw scan kinds' History image. It is greyed out for PPI, which
   has no vertical axis.
-  **Distance** controls the horizontal distance axis of the RHI and
   PPI views and is greyed out everywhere else. In RHI, Near and Far
   are the limits of the x axis (Near can be negative). In PPI, Near is
   fixed at 0 and Far sets both axes to [-Far, +Far].
-  **Speed** controls the wind-speed/radial-velocity axis or colour
   range, and is enabled everywhere that has a speed dimension --
   Processed Wind Profile (both Profile and History), RHI and PPI --
   but greyed out for the raw scan kinds' History image, whose two
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

On start-up, and whenever a file kind is selected, End time is set to
the end of that kind's data. The default preset is **24h**, so the
viewer opens on the last 24 hours of data. Select Custom to see the
whole data range (Start time is then reset to the start of the data
when you pick a file kind).

Browsing files and time windows
-------------------------------

The **First/Back/Forward/Last** buttons have two meanings, depending on
the plot type and the active time preset.

In the single-file plot types (Profile, RHI, PPI) they always step
through the files in the selected time range, one at a time: First and
Last jump to the first and last file, Back and Forward move one file.

In History with a fixed-length preset active, they move the whole
[Start, End] window instead (under Custom they are inactive in
History). First jumps the window to the true start of this kind's data;
Last jumps it to the true end. Back/Forward shift the window by exactly
one interval; the resulting End time is snapped to a grid of
interval-length multiples anchored at the start of its year (e.g. with
24h selected, End always lands on a whole day-since-Jan-1 boundary), so
repeated stepping can't drift off round numbers the way plain addition
would.

Zooming and panning
-------------------

The plot's own zoom/pan tools (in the toolbar under it) work as usual;
holding ``x`` or ``y`` while dragging the zoom-rectangle constrains it to
one axis. The two panels of every plot type share their axes (Profile:
the height axis; History, RHI and PPI: both axes), so zooming/panning
either one keeps the pair in sync. With Fill on, the filled image is
computed for the view as it was drawn, so zooming in far enough shows
its pixels; changing a range control redraws it at full resolution.

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

.. _scan-cache:

Scan cache
----------

Reading a raw scan file (``.hpl`` text) is slow compared with drawing
it: a large Stare file takes about a quarter of a second to parse, so a
History over a few days of data took a long time to appear. HaloViewer
therefore keeps every regular scan file it has read (VAD, Stare, RHI,
Wind_Profile, ...) as a numpy ``.npz`` file in a cache directory and
reads it from there next time -- dozens of times faster. The cache holds
the unfiltered data, so the intensity filter, Fill and all plot types
work on it unchanged; ``Processed_Wind_Profile`` files are small and are
always read directly.

After a root directory has been scanned, the viewer brings the cache up
to date in the background, newest files first (the default view is the
last 24 h), and then removes entries whose source file was changed or
deleted. Its progress is shown below the status line ("Cache: preparing
files… 120/700", then "Cache: 700 file(s) ready (850 MB)"). The viewer
stays usable meanwhile; a file that isn't cached yet is simply read
from the ``.hpl`` file (and cached) when it is plotted.

Where the cache lives
~~~~~~~~~~~~~~~~~~~~~

There are four choices. Pick one with a command-line option of
``haloviewer`` or ``haloplot``, or in the **Cache settings…** dialogue
(button next to "Root directory"). The choice is remembered (see
`Remembered settings`_) and shared by both programs.

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - option
     - cache location
   * - ``--tmp-cache``
       (default)
     - The system temp directory: ``/tmp/haloviewer-cache-<user>``
       (readable by you only) or ``%TEMP%\haloviewer-cache-<user>``.
       The system cleans it up -- on reboot or once the files are old
       enough, depending on its configuration -- so it never hogs disk
       space, but may have to be rebuilt now and then.
   * - ``--user-cache``
     - The per-user cache directory: ``~/.cache/haloviewer`` on Linux,
       ``%LOCALAPPDATA%\haloviewer\Cache`` on Windows,
       ``~/Library/Caches/haloviewer`` on macOS. Persistent, but it
       grows with the amount of data viewed (about half the size of the
       ``.hpl`` files).
   * - ``--cache-dir DIR``
     - Like ``--user-cache``, in ``DIR``.
   * - ``--no-cache``
     - No cache at all; every file is parsed each time. Choosing it
       deletes the existing cache(s) -- in the temp and per-user
       directories and the last custom directory (only cache files are
       removed there).

The dialogue also shows the current cache folder and its size, and
**Empty cache** deletes its contents (they are created again as
needed). ``haloviewer -i`` / ``haloplot -i`` show the same on the
command line.

The environment variable ``HALOVIEWER_CACHE`` overrides the setting: a
directory to use, or ``off`` for no cache. Entries whose source
directory isn't available (e.g. an unmounted network drive) are kept.

Troubleshooting
---------------

* **The window doesn't open / "No module named tkinter".** On some
  Linux distributions Tkinter is a separate OS package (e.g.
  ``sudo apt install python3-tk``). The conda environment already
  contains it.
* **A kind is missing from the drop-down.** Only files whose names
  follow the Halo convention
  ``<Type words>_<system id>_<yyyymmdd>_<hhmmss>.hpl`` are recognised.
  Other files in the tree are ignored.
* **Some files of a History are skipped.** Truncated files (e.g. a scan
  that was still being written when it was copied) are read up to their
  last complete ray. Unreadable files are skipped with a warning
  instead of blanking the whole plot.
* **The filter has no effect on a wind profile.** The matching
  ``Wind_Profile_<id>_<date>_<time>.hpl`` file must sit in the same
  directory as the ``Processed_Wind_Profile`` file and have exactly the
  same timestamp in its name.
* **Fill shows dots instead of a filled area.** The points of that scan
  lie on a single line in the plotted plane (e.g. a Stare scan in PPI),
  or ``scipy`` is not installed (a warning says so).
