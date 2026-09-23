# HaloViewer

A viewer and plotting toolkit for Halo Photonics wind lidar data
(`.hpl` files) covering the **processed-product** tree
(`Proc/YYYY/YYYYMM/YYYYMMDD/*.hpl`). Every kind found there is
plottable: the instrument-processed **Processed Wind Profile** (a
single scan's height/speed/direction profile, or many combined into a
height/time History image), and the raw regular-scan kinds --
**VAD**, **Stare**, **Wind Profile** and **RHI** -- each of which gets
a distance/time intensity+beta History image built from their raw
per-gate data (no instrument-processed profile of their own). **RHI**
additionally gets its own **Profile** mode: a single scan's
distance/height cross section (radial velocity and beta), the only
other kind besides Processed Wind Profile with one. Any other kind
found on disk is still listed (so you can see what's in your data
tree) but reported as not yet implemented.

## Installation

### Conda (recommended -- minimal footprint on top of miniconda)

```bash
conda env create -f environment.yml
conda activate haloviewer
```

This only adds `numpy`, `pandas` and `matplotlib` on top of a base
Python; the GUI toolkit (Tkinter) ships with the standard `python`
conda package on Linux, macOS and Windows, so nothing extra is needed
for the GUI itself. Tested against Python 3.9+ on Ubuntu 22.04, 24.04
and 26.04, macOS and Windows.

### Plain pip

```bash
pip install -e .
```

(On some minimal Linux distributions Tkinter is a separate OS package,
e.g. `sudo apt install python3-tk` on Debian/Ubuntu -- not needed when
using the conda environment above, since conda's `python` package
already includes it.)

## Running the GUI

```bash
haloviewer                     # then pick a directory from the GUI
haloviewer /path/to/Data/Proc   # or start with a directory already loaded
```

Layout: a settings panel on the left (root directory picker, file-kind
list, plot-type selector, Height/Distance/Speed range controls,
start/end time range with quick presets, First/Back/Forward/Last
browse buttons) and the plot on the right, with the standard
matplotlib navigation toolbar (zoom/pan/save) underneath it. The root
directory can be the `Proc` folder itself or any directory below it
(e.g. a single year, month, or day); the tree is rescanned whenever a
new directory is picked or typed in and confirmed with Enter.

**Height, Distance and Speed.** Three range controls share the same
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

* **Height** controls the shared vertical axis and is always enabled:
  height for Processed Wind Profile, gate-inferred distance for the
  raw scan kinds' History image, height for RHI's own Profile.
* **Distance** controls the horizontal distance axis and is only
  enabled for RHI's Profile mode (its distance/height cross section);
  it's greyed out everywhere else.
* **Speed** controls the wind-speed/radial-velocity axis or colour
  range, and is enabled everywhere that has a speed dimension --
  Processed Wind Profile (both Profile and History) and RHI's Profile
  -- but greyed out for the raw scan kinds' History image, whose two
  panels are intensity and beta, not speed.

**Time.** The "Time" frame holds Start time, a row (wrapped over two
lines) of quick-range radio buttons, and End time. The presets --
Custom, Week, 2 days, 24h, 12h, 6h -- set Start time to End time minus
that offset (End time itself is left as-is) and immediately reload;
Start time is editable only when Custom is selected. Changing End time
while a preset other than Custom is active recomputes Start time from
it.

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

The plot's own zoom/pan tools (in the toolbar under it) work as usual;
holding `x` or `y` while dragging the zoom-rectangle constrains it to
one axis. Profile mode's two panels share the height (or, for RHI,
also the distance) axis, and History mode's two panels share both the
time and height/distance axes, so zooming/panning either one keeps the
pair in sync.

For `Processed_Wind_Profile` files:

* **Profile** mode plots one file as two side-by-side panels sharing a
  height axis: wind speed on the left, wind direction on the right,
  with a small gap between them. The panel geometry (axis box size and
  position) is fixed once a kind/mode/time-range is chosen, so
  stepping through files with First/Back/Forward/Last never makes the
  plot "wobble".
* **History** mode combines every file in the selected time range into
  a height-vs-time image, with two panels stacked vertically (speed on
  top, direction below) sharing a time axis. Speed uses the
  colorblind-friendly sequential `viridis` colormap; direction (a
  cyclic quantity) uses matplotlib's perceptually-uniform cyclic
  `twilight` colormap.

For `VAD`, `Stare` and `Wind_Profile` files (raw regular scans with no
instrument-processed profile of their own):

* **History** (their only mode -- Profile is greyed out) combines
  every file in the selected time range into a distance-vs-time image
  of the raw per-gate data, two panels stacked vertically: intensity
  (SNR + 1) on top using the `cividis` colormap, attenuated backscatter
  (beta) below using `magma`. The vertical axis is distance inferred
  purely from range gates (gate index × gate length), not scan
  geometry -- individual rays are never plotted; instead all rays
  across the loaded files are averaged into "round" time bins (10s,
  15s, 30s, 1 min, ... up to a week) sized so the image is roughly
  100-250 pixels wide regardless of how long a span is selected. A time
  bin with no rays in it is left as `NaN`, which renders as blank
  background rather than an interpolated guess.

For `RHI` files:

* **Profile** mode plots one scan as two stacked panels sharing both a
  distance (horizontal) and height (vertical) axis: radial (Doppler)
  velocity on top using the diverging `PuOr` colormap (chosen to avoid
  the red/green endpoints most likely to be confused under red-green
  colour vision deficiency), attenuated backscatter (beta) below using
  `magma`. Each point is one range gate along one ray, plotted as an
  individual colour-coded dot -- not gridded, since RHI rays don't
  share a common distance/height grid the way a fixed scan geometry
  would -- with its distance and height computed from that ray's own
  azimuth/elevation and the instrument's pitch/roll tilt correction.
* **History** mode is the same raw intensity/beta scan history
  described above for VAD/Stare/Wind_Profile.

## Command line

`haloplot FILE...` accepts one or more files, directories (searched
recursively for `.hpl` files) and/or glob patterns, resolves the file
kind and a time range, and plots it -- it's a thin CLI wrapper around
`haloviewer.plot()` below.

```bash
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

# fix axis ranges instead of autoscaling (--dist/--speed warn, but
# don't fail, if they don't apply to the selected kind/mode)
haloplot some_profile.hpl --height 0 3000 --speed 0 20 -p profile.png

# open interactively instead of saving
haloplot some_file.hpl --show

# neither -p/--plot nor --show given -> saved as "plot.png"
haloplot some_file.hpl
```

Figures default to A4 landscape (11.69 x 8.27 in); override with
`--figsize WIDTH HEIGHT`. The base font size isn't a separate option --
it scales automatically with the figure's area, working out to 16pt at
the default A4 landscape size (see `haloviewer.api.BASE_FONTSIZE_AT_A4`)
and proportionally smaller/larger for any other `--figsize`. Run
`haloplot --help` for the full option list.

## Programmatic API

`haloviewer.plot()` is the high-level entry point -- the same path
resolution, kind/mode inference and time-range selection as the CLI,
available directly from a script or notebook:

```python
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

# fontsize defaults to scaling with figsize (16pt at A4 landscape,
# proportionally smaller/larger otherwise); pass it explicitly to
# override that
fig = haloviewer.plot("some_profile.hpl", figsize=(6, 4), fontsize=10)
```

Note `distance=` is the API-level name for what the CLI calls `--dist`
(kept short on the command line only).

`plot_file`/`plot_files` remain available (also re-exported at the
package level) for callers that have already resolved an exact file or
file list of one known kind, and skip path/kind/time-range resolution:

```python
from haloviewer import plot_file, plot_files

# single file -> profile plot
fig = plot_file("Processed_Wind_Profile_77_20260919_121707.hpl",
                 output="profile.png")

# multiple files of the same kind -> a history (time series) plot
fig = plot_files(sorted(glob.glob("Proc/2026/202609/20260919/"
                                   "Processed_Wind_Profile_*.hpl")),
                  output="history.png")
```

## Design

### File structure

The code is layered so each piece can be used, tested and understood
on its own:

| module | responsibility |
|---|---|
| `haloviewer.hpl` | `.hpl` file format parser (adapted from [cdruee/python-readmet](https://github.com/cdruee/python-readmet)'s `hpl` module) |
| `haloviewer.scan` | finds `.hpl` files under a root directory, classifies them by kind from the filename, indexes by timestamp |
| `haloviewer.data` | turns parsed files into plain numpy/pandas arrays ready to plot |
| `haloviewer.plotting` | **pure matplotlib**, no GUI toolkit imports: figure/axes creation and drawing functions |
| `haloviewer.api` | programmatic entry points (`plot`, `plot_file`, `plot_files`); `plot` also re-exported as `haloviewer.plot` |
| `haloviewer.cli` | command-line interface on top of `api` |
| `haloviewer.gui` | Tkinter desktop app; wires widgets to `scan`/`data`/`plotting` and contains no plotting logic itself |

`plotting.py` never imports `tkinter`, and `gui.py` never calls
matplotlib drawing primitives directly -- it only calls functions in
`plotting.py`. This means the exact same plotting code is used by the
GUI, the CLI, and any script that imports `haloviewer.api`.

### Extending to more scan kinds

`Processed_Wind_Profile`, `VAD`, `Stare`, `Wind_Profile` and `RHI` are
all implemented; a user-defined pattern (`User1`...`User5` in the raw
`.hpl` header's `Scan type`) or a future Halo scan kind would follow
the same recipe:

1. Add a reader to `data.py` that turns a parsed `hpl.DataFile` (or a
   set of them) into plain arrays.
2. Add drawing function(s) to `plotting.py` that take those arrays and
   axes/figure objects -- reuse `create_timeseries_figure`'s two
   stacked, colour-mapped panels if that shape fits; that's what all
   three History flavours and RHI's own Profile scatter share.
3. Register the kind's capabilities in `scan.KIND_CAPABILITIES`
   (`supported=True`, its plot modes).
4. Wire the new mode(s) into `api.plot_file`/`plot_files`, and into
   `gui.HaloViewerApp._plot_kind` (which of the four load/render
   pipelines applies) and `_update_range_controls_enabled` (which of
   Height/Distance/Speed make sense for it).

The GUI will then automatically offer that kind and mode as soon as it
is discovered on disk -- no other GUI changes are needed.

### Tests

```bash
pip install -e ".[dev]"
pytest
```

## Licence

HaloViewer is licensed under the European Union Public Licence v1.2
(EUPL-1.2); see [`LICENSE`](LICENSE) for the full licence text.

`haloviewer/hpl.py` is adapted from the `hpl` module of
[cdruee/python-readmet](https://github.com/cdruee/python-readmet),
which is itself licensed under the EUPL-1.2.

## Copyright

(c) 2026 Clemens Drüe, Universität Trier

Developed with support of Anthropic Claude Opus 5.5.
