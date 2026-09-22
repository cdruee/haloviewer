"""
Programmatic entry points for plotting WindLidar files, independent of
both the CLI (:mod:`windlidarviewer.cli`) and the GUI
(:mod:`windlidarviewer.gui`). Use these directly from a script or
notebook::

    from windlidarviewer.api import plot_file
    fig = plot_file("Processed_Wind_Profile_77_20260919_121707.hpl",
                     output="profile.png")
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple, Union

from matplotlib.figure import Figure

from . import data as _data
from . import plotting
from .scan import PROFILE_MODE, TIMESERIES_MODE, get_kind_info, parse_filename

__all__ = ['plot_file', 'plot_files']

PathLike = Union[str, Path]


def _resolve_kind(path: PathLike) -> str:
    parsed = parse_filename(path)
    if parsed is None:
        raise ValueError(
            f'{path!r} does not look like a Halo-style ".hpl" filename '
            f'(expected "<Type words>_<system id>_<yyyymmdd>_<hhmmss>.hpl")')
    return parsed.kind


def _check_supported(kind: str, mode: str) -> None:
    info = get_kind_info(kind)
    if not info.supported:
        raise NotImplementedError(
            f'plotting is not yet implemented for file kind {kind!r}')
    if mode not in info.modes:
        raise ValueError(
            f'kind {kind!r} does not support mode {mode!r}; '
            f'available modes: {list(info.modes)}')


#: Kinds with no instrument-processed profile of their own: History
#: mode comes from the raw per-gate intensity/beta instead of a
#: derived wind speed/direction (see :func:`~windlidarviewer.data.load_scan_history`).
_SCAN_HISTORY_KINDS = {'VAD', 'Stare', 'Wind_Profile', 'RHI'}


def plot_file(path: PathLike, *,
              mode: Optional[str] = None,
              output: Optional[PathLike] = None,
              show: bool = False,
              figsize: Optional[Tuple[float, float]] = None) -> Figure:
    """
    Plot a single WindLidar file.

    :param path: path to a ``.hpl`` file.
    :param mode: ``"profile"`` or ``"timeseries"`` (shown in the GUI as \
        "History"). Defaults to the first mode the file's kind \
        supports. Only ``Processed_Wind_Profile`` and ``RHI`` support \
        ``"profile"`` (a single scan by itself -- a height/speed/ \
        direction profile for the former, a distance/height cross \
        section for the latter); every other supported kind (VAD, \
        Stare, Wind_Profile) only has ``"timeseries"``/History. A \
        single file plotted in that mode produces a one-column (or, \
        for the scan kinds, one-file's-worth-of-rays) image; use \
        :func:`plot_files` to combine several files into a real \
        history.
    :param output: if given, save the figure to this path (format \
        inferred from the extension, e.g. ``.png``, ``.pdf``).
    :param show: if ``True``, display the figure in an interactive \
        window (blocks until closed). Requires an interactive \
        matplotlib backend to be available.
    :param figsize: figure size in inches; a sensible default is used \
        if omitted.
    :returns: the :class:`~matplotlib.figure.Figure` that was drawn.
    """
    path = Path(path)
    kind = _resolve_kind(path)
    if mode is None:
        info = get_kind_info(kind)
        if not info.modes:
            raise NotImplementedError(
                f'plotting is not yet implemented for file kind {kind!r}')
        mode = info.modes[0]
    _check_supported(kind, mode)

    fig = _new_figure(show, figsize)

    if mode == PROFILE_MODE and kind == 'Processed_Wind_Profile':
        prof = _data.load_profile(path)
        fig, (ax_speed, ax_dir) = plotting.create_profile_figure(
            figsize=figsize or (6.4, 6.0), fig=fig)
        plotting.plot_wind_profile(
            ax_speed, ax_dir, prof.height, prof.speed, prof.direction,
            title=f'{kind}  {prof.timestamp:%Y-%m-%d %H:%M:%S}')
    elif mode == PROFILE_MODE and kind == 'RHI':
        cross = _data.load_rhi_cross_section(path)
        fig, (ax_vel, ax_beta, cax_vel, cax_beta) = \
            plotting.create_timeseries_figure(
                figsize=figsize or (10.0, 6.0), fig=fig)
        plotting.plot_rhi_cross_section(
            ax_vel, ax_beta, cax_vel, cax_beta,
            cross.distance, cross.height, cross.velocity, cross.beta,
            title=f'{kind}  {cross.timestamp:%Y-%m-%d %H:%M:%S}')
    elif mode == TIMESERIES_MODE:
        return plot_files([path], mode=mode, output=output, show=show,
                           figsize=figsize, fig=fig)
    else:
        raise ValueError(f'mode {mode!r} is not supported for kind {kind!r}')

    _finish(fig, output, show)
    return fig


def plot_files(paths: Iterable[PathLike], *,
                mode: str = TIMESERIES_MODE,
                output: Optional[PathLike] = None,
                show: bool = False,
                figsize: Optional[Tuple[float, float]] = None,
                fig: Optional[Figure] = None) -> Figure:
    """
    Plot several WindLidar files of the same kind together as a
    History: height/time (speed/direction as colour) for
    ``Processed_Wind_Profile``, or distance/time (intensity/beta as
    colour) for VAD, Stare, Wind_Profile or RHI.

    :param paths: paths to ``.hpl`` files, all of the same kind.
    :param mode: only ``"timeseries"``/History is meaningful here for \
        multiple files; RHI's single-scan ``"profile"`` cross section \
        is routed to :func:`plot_file` if exactly one path is given.
    :param output: if given, save the figure to this path.
    :param show: if ``True``, display the figure interactively.
    :param figsize: figure size in inches.
    :param fig: internal use (an already-created figure to draw into).
    :returns: the :class:`~matplotlib.figure.Figure` that was drawn.
    """
    paths = [Path(p) for p in paths]
    if not paths:
        raise ValueError('no files given')
    kinds = {_resolve_kind(p) for p in paths}
    if len(kinds) > 1:
        raise ValueError(f'files must all be the same kind, got {kinds}')
    kind = kinds.pop()

    if mode == PROFILE_MODE and kind == 'RHI':
        if len(paths) != 1:
            raise ValueError(
                'RHI "profile" mode is a single scan\'s cross section; '
                'pass exactly one file (got %d)' % len(paths))
        return plot_file(paths[0], mode=mode, output=output, show=show,
                          figsize=figsize)

    _check_supported(kind, mode)

    if fig is None:
        fig = _new_figure(show, figsize)

    if mode == TIMESERIES_MODE and kind == 'Processed_Wind_Profile':
        series = _data.load_profile_series(paths)
        fig, (ax_speed, ax_dir, cax_speed, cax_dir) = \
            plotting.create_timeseries_figure(
                figsize=figsize or (10.0, 6.0), fig=fig)
        title = kind
        if len(series.times):
            title = (f'{kind}  '
                      f'{series.times[0]:%Y-%m-%d %H:%M} – '
                      f'{series.times[-1]:%Y-%m-%d %H:%M}')
        plotting.plot_wind_timeseries(
            ax_speed, ax_dir, cax_speed, cax_dir,
            series.times, series.height, series.speed, series.direction,
            title=title)
    elif mode == TIMESERIES_MODE and kind in _SCAN_HISTORY_KINDS:
        hist = _data.load_scan_history(paths)
        fig, (ax_int, ax_beta, cax_int, cax_beta) = \
            plotting.create_timeseries_figure(
                figsize=figsize or (10.0, 6.0), fig=fig)
        title = kind
        if len(hist.times):
            title = (f'{kind}  '
                      f'{hist.times[0]:%Y-%m-%d %H:%M} – '
                      f'{hist.times[-1]:%Y-%m-%d %H:%M}')
        plotting.plot_scan_history(
            ax_int, ax_beta, cax_int, cax_beta,
            hist.times, hist.distance, hist.intensity, hist.beta,
            title=title)
    else:
        raise ValueError(
            f'mode {mode!r} is not supported for multiple files of kind '
            f'{kind!r}')

    _finish(fig, output, show)
    return fig


def _new_figure(show: bool, figsize: Optional[Tuple[float, float]]) -> Optional[Figure]:
    """Create a pyplot-managed figure when ``show`` is requested (so
    ``plt.show()`` has a window to display), otherwise let the
    ``create_*_figure`` helpers make a bare, backend-independent one."""
    if not show:
        return None
    import matplotlib.pyplot as plt
    return plt.figure(figsize=figsize)


def _finish(fig: Figure, output: Optional[PathLike], show: bool) -> None:
    if output is not None:
        fig.savefig(output, dpi=150)
    if show:
        import matplotlib.pyplot as plt
        plt.show()
