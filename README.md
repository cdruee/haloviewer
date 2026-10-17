# HaloViewer

<img src="docs/images/haloviewer_logo.png" alt="HaloViewer logo" width="128">

A viewer and plotting toolkit for Halo Photonics wind lidar data
(`.hpl` files) covering the **processed-product** tree
(`Proc/YYYY/YYYYMM/YYYYMMDD/*.hpl`). Every kind found there is
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

[![Documentation](https://img.shields.io/badge/docs-GitLab%20Pages-blue)](https://uwm.gitlab-pages.uni-trier.de/lidar/haloviewer/)
[![PyPI](https://img.shields.io/pypi/v/HaloViewer)](https://pypi.org/project/HaloViewer/)
[![Source code](https://img.shields.io/badge/source-GitHub-181717?logo=github)](https://github.com/cdruee/haloviewer)

## Installation

### Conda (recommended -- minimal footprint on top of miniconda)

```bash
conda env create -f environment.yml
conda activate haloviewer
```

This only adds `numpy`, `pandas`, `matplotlib` and `scipy` on top of a
base Python; the GUI toolkit (Tkinter) ships with the standard `python`
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

## Contents

HaloViewer provides four ways to work with the data. Each has its own
page in the [documentation](https://uwm.gitlab-pages.uni-trier.de/lidar/haloviewer/).

**Graphic viewer -- `haloviewer`.**
A Tkinter desktop application for browsing a `Proc` tree: pick a
directory, a file kind and a plot type (*Profile*, *History*, *RHI* or
*PPI*), then step through single files or whole time windows, with
adjustable height, distance and speed ranges.

```bash
haloviewer /path/to/Data/Proc
```

**Command-line tool -- `haloplot`.**
Plots files, directories or glob patterns straight to an image file,
without the GUI. File kind, plot mode and time range are inferred where
possible and can be set explicitly.

```bash
haloplot Proc/2026/202609 --kind RHI --start 24h -p rhi_24h.png
```

**Sync tool -- `halosync`.**
A separate GUI for selectively copying the `Metek`, `Proc` and `Raw`
trees from the lidar control PC to a backup or analysis disk. It skips
files that are already there, leaves out the file the lidar is still
writing, and can run on a fixed schedule.

```bash
halosync
```

**Python API -- `haloviewer.plot()`.**
The same functionality as `haloplot` for scripts and notebooks. It
returns a matplotlib `Figure` for further customisation.

```python
import haloviewer
fig = haloviewer.plot("Proc/2026/202609/20260919",
                      kind="Processed_Wind_Profile", start="24h")
```

### Documentation

The full documentation is online at
<https://uwm.gitlab-pages.uni-trier.de/lidar/haloviewer/>. It describes
each tool in detail, includes the API reference generated from the
docstrings, and has a
[Design](https://uwm.gitlab-pages.uni-trier.de/lidar/haloviewer/design.html)
page on how the code is organised (modules, scan cache and settings,
version numbers), how to support further scan kinds, and how to run the
tests. Its sources (Sphinx) are in `docs/`; to build it locally, run
this from the project root (the HTML ends up in `build/html/`):

```bash
pip install -e ".[docs]"
sphinx-build -b html docs build/html
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
