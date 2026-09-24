# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5
"""
Command-line interface for plotting Halo wind lidar files without the GUI.

``haloplot`` is a thin wrapper around :func:`haloviewer.plot`:
it turns the command line into keyword arguments for that function, which
does all the real work (resolving ``FILE`` to a list of ``.hpl`` files,
picking a kind/mode/time-range, and drawing the figure).

Examples::

    # plot a single processed wind profile, save as PNG
    haloplot Processed_Wind_Profile_77_20260919_121707.hpl \\
        -p profile.png

    # combine a day's worth of profiles into a time-height plot
    haloplot Proc/2026/202609/20260919 \\
        --kind Processed_Wind_Profile --mode history \\
        --plot 20260919_history.png

    # last 24h of RHI scans up to a given time, with fixed axis ranges
    haloplot Proc/2026/202609 --kind RHI --start 24h --time \\
        "2026-09-19 12:00" --height 0 3000 -p rhi_24h.png

    # hide low-signal data (intensity < 1.18, the default threshold)
    haloplot Proc/2026/202609/20260919 -k VAD --filter True -p vad.png

    # open interactively instead of (or as well as) saving
    haloplot some_file.hpl --show

    # neither -p/--plot nor --show given -> saved as "plot.png"
    haloplot some_file.hpl
"""

from __future__ import annotations

import argparse
import sys
import warnings
from typing import List, Optional

from . import api
from . import data as _data

#: Output filename used when neither -p/--plot nor --show is given.
DEFAULT_OUTPUT = 'plot.png'


def _filter_value(text: str):
    """argparse ``type`` for ``--filter``: ``True`` (any case) selects
    the default threshold, anything else must be a number."""
    try:
        return _data.resolve_intensity_filter(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f'expected "True" or a number, got {text!r}') from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='haloplot',
        description='Plot Halo Photonics wind lidar .hpl file(s).')
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
        '--dist', dest='distance', nargs=2, type=float,
        metavar=('MIN', 'MAX'),
        help='fix the distance (range) axis for RHI profile plots '
             '(deselects autoscale; warns if not applicable)')
    parser.add_argument(
        '--speed', nargs=2, type=float, metavar=('MIN', 'MAX'),
        help='fix the wind-speed/velocity axis or colour range '
             '(deselects autoscale; warns if not applicable)')
    parser.add_argument(
        '--filter', dest='filter', type=_filter_value, metavar='VALUE',
        help='hide data points whose intensity (SNR + 1) is below VALUE '
             '(shown blank). "True" selects the default threshold '
             '(%g). For Processed_Wind_Profile, the intensity comes from '
             'the Wind_Profile file with the same timestamp'
             % _data.DEFAULT_INTENSITY_FILTER)
    parser.add_argument(
        '-p', '--plot', dest='output', metavar='PATH',
        help='save the figure to PATH (format inferred from the '
             'extension, e.g. .png, .pdf, .svg). If neither this nor '
             '--show is given, the figure is saved as %r'
             % DEFAULT_OUTPUT)
    parser.add_argument(
        '--show', action='store_true',
        help='display the figure in an interactive window')
    parser.add_argument(
        '--figsize', nargs=2, type=float, metavar=('WIDTH', 'HEIGHT'),
        help='figure size in inches (default: A4 landscape, 11.69x8.27)')
    parser.add_argument(
        '--verbose', '-v', action='store_true', help='enable debug logging')
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    output = args.output
    if output is None and not args.show:
        output = DEFAULT_OUTPUT
        print(f'note: neither -p/--plot nor --show given; saving to '
              f'{output!r}', file=sys.stderr)

    kwargs = dict(
        kind=args.kind,
        mode=args.mode,
        start=args.start,
        end=args.end,
        output=output,
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
    if args.filter is not None:
        kwargs['filter'] = args.filter

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
