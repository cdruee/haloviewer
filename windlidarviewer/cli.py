"""
Command-line interface for plotting WindLidar files without the GUI.

``windlidar-plot`` is a thin wrapper around :func:`windlidarviewer.plot`:
it turns the command line into keyword arguments for that function, which
does all the real work (resolving ``FILE`` to a list of ``.hpl`` files,
picking a kind/mode/time-range, and drawing the figure).

Examples::

    # plot a single processed wind profile, save as PNG
    windlidar-plot Processed_Wind_Profile_77_20260919_121707.hpl \\
        --output profile.png

    # combine a day's worth of profiles into a time-height plot
    windlidar-plot Proc/2026/202609/20260919 \\
        --kind Processed_Wind_Profile --mode history \\
        --output 20260919_history.png

    # last 24h of RHI scans up to a given time, with fixed axis ranges
    windlidar-plot Proc/2026/202609 --kind RHI --start 24h --time \\
        "2026-09-19 12:00" --height 0 3000 --output rhi_24h.png

    # open interactively instead of (or as well as) saving
    windlidar-plot some_file.hpl --show
"""

from __future__ import annotations

import argparse
import sys
import warnings
from typing import List, Optional

from . import api


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='windlidar-plot',
        description='Plot Halo Photonics WindLidar .hpl file(s).')
    parser.add_argument(
        'files', nargs='+', metavar='FILE',
        help='file(s), directory/directories (searched recursively for '
             '.hpl files), and/or glob pattern(s) to plot')
    parser.add_argument(
        '-k', '--kind', metavar='KIND',
        help='file kind to select (e.g. Processed_Wind_Profile, VAD, '
             'Stare, Wind_Profile, RHI). Inferred from the filename(s) '
             'when FILE resolves to a single kind; required if it '
             'resolves to more than one')
    parser.add_argument(
        '--mode', choices=['profile', 'history'], default=None,
        help='plot type: "profile" for a single scan/profile, "history" '
             'for a time series of several files. Defaults to "profile" '
             'when exactly one file falls in the selected time range, '
             'otherwise "history"')
    parser.add_argument(
        '-s', '--start', metavar='START',
        help='start of the time range: an absolute timestamp '
             '("YYYY-MM-DD [HH:MM[:SS]]") or a relative interval back '
             'from the end time ("##d" or "##h", e.g. "24h" or "2.5d")')
    parser.add_argument(
        '-t', '--time', dest='end', metavar='TIME',
        help='end of the time range ("YYYY-MM-DD [HH:MM[:SS]]"). '
             'Defaults to the latest matching file\'s timestamp (i.e. '
             'that file\'s own timestamp, if only one file is given)')
    parser.add_argument(
        '--height', nargs=2, type=float, metavar=('MIN', 'MAX'),
        help='fix the height/vertical axis range (deselects autoscale)')
    parser.add_argument(
        '--distance', nargs=2, type=float, metavar=('MIN', 'MAX'),
        help='fix the distance (range) axis for RHI profile plots '
             '(deselects autoscale; warns if not applicable)')
    parser.add_argument(
        '--speed', nargs=2, type=float, metavar=('MIN', 'MAX'),
        help='fix the wind-speed/velocity axis or colour range '
             '(deselects autoscale; warns if not applicable)')
    parser.add_argument(
        '--output', '-o', metavar='PATH',
        help='save the figure to PATH (format inferred from extension, '
             'e.g. .png, .pdf, .svg)')
    parser.add_argument(
        '-p', '--plot', '--show', dest='show', action='store_true',
        help='display the figure in an interactive window')
    parser.add_argument(
        '--figsize', nargs=2, type=float, metavar=('WIDTH', 'HEIGHT'),
        help='figure size in inches (default: A4 landscape, 11.69x8.27)')
    parser.add_argument(
        '--fontsize', type=float, metavar='PT',
        help='base font size in points for titles/labels/ticks '
             '(default: 16)')
    parser.add_argument(
        '--verbose', '-v', action='store_true', help='enable debug logging')
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    if not args.output and not args.show:
        print('note: neither --output nor --show/-p given; the figure '
              'will be built but not saved or displayed', file=sys.stderr)

    kwargs = dict(
        kind=args.kind,
        mode=args.mode,
        start=args.start,
        end=args.end,
        output=args.output,
        show=args.show,
    )
    if args.height is not None:
        kwargs['height'] = tuple(args.height)
    if args.distance is not None:
        kwargs['distance'] = tuple(args.distance)
    if args.speed is not None:
        kwargs['speed'] = tuple(args.speed)
    if args.figsize is not None:
        kwargs['figsize'] = tuple(args.figsize)
    if args.fontsize is not None:
        kwargs['fontsize'] = args.fontsize

    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            api.plot(args.files, **kwargs)
        for w in caught:
            print(f'warning: {w.message}', file=sys.stderr)
    except (ValueError, NotImplementedError, IOError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
