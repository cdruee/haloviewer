# WindLidar Viewer

A viewer and plotting toolkit for Halo Photonics WindLidar data
(`.hpl` files). This first version covers the **processed-product**
tree (`Proc/YYYY/YYYYMM/YYYYMMDD/*.hpl`), starting with
**Processed Wind Profile** files; other scan kinds (`Stare`, `VAD`,
`RHI`, `Wind profile`, ...) are auto-discovered and listed in the GUI,
but plotting support for them is not implemented yet.

## Design

The code is layered so each piece can be used, tested and understood
on its own:

| module | responsibility |
|---|---|
| `windlidarviewer.hpl` | `.hpl` file format parser (adapted from [cdruee/python-readmet](https://github.com/cdruee/python-readmet)'s `hpl` module) |
| `windlidarviewer.scan` | finds `.hpl` files under a root directory, classifies them by kind from the filename, indexes by timestamp |
| `windlidarviewer.data` | turns parsed files into plain numpy/pandas arrays ready to plot |
| `windlidarviewer.plotting` | **pure matplotlib**, no GUI toolkit imports: figure/axes creation and drawing functions |
| `windlidarviewer.api` | small programmatic entry points (`plot_file`, `plot_files`) |
| `windlidarviewer.cli` | command-line interface on top of `api` |
| `windlidarviewer.gui` | Tkinter desktop app; wires widgets to `scan`/`data`/`plotting` and contains no plotting logic itself |

`plotting.py` never imports `tkinter`, and `gui.py` never calls
matplotlib drawing primitives directly -- it only calls functions in
`plotting.py`. This means the exact same plotting code is used by the
GUI, the CLI, and any script that imports `windlidarviewer.api`.

## Installation

### Conda (recommended -- minimal footprint on top of miniconda)

```bash
conda env create -f environment.yml
conda activate windlidarviewer
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
windlidar-gui                  # then pick a directory from the GUI
windlidar-gui /path/to/Data/Proc   # or start with a directory already loaded
```

Layout: a settings panel on the left (root directory picker, file-kind
list, plot-type selector, start/end time range, First/Back/Forward/Last
browse buttons) and the plot on the right, with the standard matplotlib
navigation toolbar (zoom/pan/save) underneath it. The root directory
can be the `Proc` folder itself or any directory below it (e.g. a
single year, month, or day); the tree is rescanned whenever a new
directory is picked or typed in and confirmed with Enter.

For `Processed_Wind_Profile` files:

* **Profile** mode plots one file as two side-by-side panels sharing a
  height axis: wind speed on the left, wind direction on the right,
  with a small gap between them. The panel geometry (axis box size and
  position) is fixed once a kind/mode/time-range is chosen, so
  stepping through files with First/Back/Forward/Last never makes the
  plot "wobble".
* **Time series** mode combines every file in the selected time range
  into a height-vs-time image, with two panels stacked vertically
  (speed on top, direction below) sharing a time axis. Speed uses the
  colorblind-friendly sequential `viridis` colormap; direction (a
  cyclic quantity) uses matplotlib's perceptually-uniform cyclic
  `twilight` colormap.

## Command line

```bash
# a single profile
windlidar-plot Processed_Wind_Profile_77_20260919_121707.hpl -o profile.png

# a whole day as a time-height plot (shell-expanded glob, or quote it
# and let the CLI expand it)
windlidar-plot "Proc/2026/202609/20260919/Processed_Wind_Profile_*.hpl" \
    --mode timeseries -o 20260919_timeseries.png

# open interactively instead of saving
windlidar-plot some_file.hpl --show
```

Run `windlidar-plot --help` for all options.

## Programmatic API

```python
from windlidarviewer.api import plot_file, plot_files

# single file -> profile plot
fig = plot_file("Processed_Wind_Profile_77_20260919_121707.hpl",
                 output="profile.png")

# multiple files of the same kind -> time series plot
fig = plot_files(sorted(glob.glob("Proc/2026/202609/20260919/"
                                   "Processed_Wind_Profile_*.hpl")),
                  output="timeseries.png")
```

## Extending to more scan kinds

To add plotting support for another kind (e.g. `Stare`, `VAD`):

1. Add a reader to `data.py` that turns a parsed `hpl.DataFile` (or a
   set of them) into plain arrays.
2. Add drawing function(s) to `plotting.py` that take those arrays and
   axes/figure objects.
3. Register the kind's capabilities in `scan.KIND_CAPABILITIES`
   (`supported=True`, its plot modes).
4. Wire the new mode(s) into `api.plot_file`/`plot_files` and, if the
   drawing needs different axes layout, into `gui._set_mode_figure`.

The GUI will then automatically offer that kind and mode as soon as it
is discovered on disk -- no other GUI changes are needed.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

## Licensing

This project is MIT-licensed (`LICENSE.txt`), except
`windlidarviewer/hpl.py`, which is adapted from
[cdruee/python-readmet](https://github.com/cdruee/python-readmet) and
remains under the European Union Public Licence v1.2
(`LICENSE-EUPL-1.2.txt`).
