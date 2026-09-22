"""
Command-line interface for plotting WindLidar files without the GUI.

Examples::

    # plot a single processed wind profile, save as PNG
    windlidar-plot Processed_Wind_Profile_77_20260919_121707.hpl \\
        --output profile.png

    # combine a day's worth of profiles into a time-height plot
    windlidar-plot Proc/2026/202609/20260919/Processed_Wind_Profile_*.hpl \\
        --mode timeseries --output 20260919_timeseries.png

    # open interactively instead of (or as well as) saving
    windlidar-plot some_file.hpl --show
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path
from typing import List

from . import api
from .scan import PROFILE_MODE, TIMESERIES_MODE


def _expand(patterns: List[str]) -> List[Path]:
    paths: List[Path] = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if matches:
            paths.extend(Path(m) for m in matches)
        elif Path(pattern).exists():
            paths.append(Path(pattern))
        else:
            print(f'warning: no file matches {pattern!r}', file=sys.stderr)
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='windlidar-plot',
        description='Plot one or more Halo Photonics WindLidar .hpl files.')
    parser.add_argument(
        'files', nargs='+',
        help='file path(s) or glob pattern(s) for the .hpl file(s) to plot')
    parser.add_argument(
        '--mode', choices=[PROFILE_MODE, TIMESERIES_MODE], default=None,
        help='plot type. Defaults to "profile" for a single file, '
             '"timeseries" for more than one.')
    parser.add_argument(
        '--output', '-o', metavar='PATH',
        help='save the figure to PATH (format inferred from extension, '
             'e.g. .png, .pdf, .svg)')
    parser.add_argument(
        '--show', action='store_true',
        help='display the figure in an interactive window')
    parser.add_argument(
        '--figsize', nargs=2, type=float, metavar=('WIDTH', 'HEIGHT'),
        help='figure size in inches')
    parser.add_argument(
        '--verbose', '-v', action='store_true', help='enable debug logging')
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    paths = _expand(args.files)
    if not paths:
        print('error: no input files found', file=sys.stderr)
        return 2

    if not args.output and not args.show:
        print('note: neither --output nor --show given; the figure will '
              'be built but not saved or displayed', file=sys.stderr)

    figsize = tuple(args.figsize) if args.figsize else None
    mode = args.mode

    try:
        if len(paths) == 1:
            api.plot_file(paths[0], mode=mode, output=args.output,
                           show=args.show, figsize=figsize)
        else:
            api.plot_files(paths, mode=mode or TIMESERIES_MODE,
                            output=args.output, show=args.show,
                            figsize=figsize)
    except (ValueError, NotImplementedError, IOError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
