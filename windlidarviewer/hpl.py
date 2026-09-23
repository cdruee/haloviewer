#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Parser for Halo Photonics Ltd. wind-lidar ``.hpl`` files.

This module is adapted from the ``hpl`` module of `cdruee/python-readmet
<https://github.com/cdruee/python-readmet>`_ (C. Drüe, University of
Trier), which is licensed under the European Union Public Licence v1.2
(EUPL-1.2); this file is redistributed here under the same licence
(see ``LICENSE-EUPL-1.2.txt`` at the project root). The rest of the
``windlidarviewer`` package is licensed separately (see ``LICENSE.txt``).
Most changes are cosmetic (module path, a couple of type-hint
compatibility tweaks); the one functional change is adding ``'Stare'``
to :data:`TYPES`, so a ``Scan type: Stare`` header (a real Halo scan
pattern the original whitelist didn't include) parses instead of
raising.

A ``.hpl`` file holds one scan of Doppler-lidar line-of-sight wind data.
Two variants are supported:

*Regular scan files* start with a ``Key: value`` header, one entry per
line, terminated by a line of two or more asterisks (``**...**``).
Among others, the header gives the instrument id (``System ID``), the
scan start time (``Start time``), the number of range gates
(``Number of gates``) and their length (``Range gate length (m)``,
``Gate length (pts)``), the number of laser pulses averaged per ray
(``Pulses/ray``), the number of rays recorded
(``No. of rays in file``), the scan pattern (``Scan type``, e.g.
``VAD``, ``RHI``, ``Wind profile``, ``Stare`` or a user-defined
pattern, optionally suffixed with a scan mode such as ``csm``
[continuous scanner motion], ``stepped`` or ``overlapping``), an
optional electronic focus distance (``Focus range``), and the Doppler
velocity resolution (``Resolution (m/s)``). Two further header lines,
``Data line 1`` and ``Data line 2``, each followed by a line with a
Fortran-style format specification (e.g. ``f9.6,1x,f6.2,1x,f6.2``,
where ``1x`` marks a single-character spacer between fields),
describe the data that follows: ``Data line 1`` lists the per-ray
quantities (decimal time, azimuth, elevation, pitch and roll), and
``Data line 2`` lists the per-gate quantities (range gate index,
Doppler velocity, intensity [signal-to-noise ratio plus one], and
attenuated backscatter beta).

After the header, the data block holds one block per ray: a single
line with the per-ray values (as described by ``Data line 1``),
followed by one line per range gate with the per-gate values (as
described by ``Data line 2``).

*Processed wind profile files* are a much reduced, header-less
variant (``Processed Wind Profile``) produced by the instrument's own
processing software: the first line just gives the number of profile
levels, followed by one line per level with height (m), wind
direction (degrees) and wind speed (m/s).
"""

import glob
import logging
import os
import re

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------

MARKER = re.compile(r'^\s*[*]{2,}\s*$')
TYPES = ['VAD', 'RHI', 'Wind profile', 'Stare', 'User1',
         'User2', 'User3', 'User4', 'User5',
         'Processed Wind Profile']


# =========================================================================

class Ray(object):
    """
    Holds one ray (single beam) of a lidar scan: its time, orientation,
    gate count, and the per-gate measurements along the beam.

    :param time: time of the ray
    :param elevation: elevation angle in degrees
    :param azimuth: azimuth angle in degrees
    :param pitch: pitch angle in degrees
    :param roll: roll angle in degrees
    :param gates: number of range gates
    :param df: per-gate data (see :attr:`data`)
    """

    def __init__(self, time=None, elevation=None, azimuth=None,
                 pitch=None, roll=None, gates=None, df=None):
        self.time = time if time is not None else np.datetime64('NaT')
        self.elevation = elevation
        self.azimuth = azimuth
        self.pitch = pitch
        self.roll = roll
        self.gates = gates
        self.data = df if df is not None else pd.DataFrame(
            columns=['gate', 'doppler', 'intensity', 'beta'])


# =========================================================================

class DataFile(object):
    """
    Object class that holds data and metadata of a ``.hpl`` file.

    Handles both variants described in the module documentation: a
    regular scan file (data ends up in :attr:`rays`, one :class:`Ray`
    per beam) and a header-less "Processed Wind Profile" file (data
    ends up in :attr:`profile`; :attr:`rays` is then empty and
    :attr:`vars` is ``None``, since there is no per-variable format
    information to read).

    :param file: filename (optionally including path). \
      If missing, an empty object is returned.
    :param text: (optional) If ``True`` the raw file contents \
      are retained as attribute :attr:`text` of the object. If \
      ``False`` or missing, the raw file contents are discarded \
      after parsing.
    """

    file = None
    """ name of file loaded into object """
    header = None
    """ dictionary containing the header entries as strings. For a
      header-less "Processed Wind Profile" file (see module
      description), which has no real header, this instead holds the
      synthesized ``variables_1``/``units_1``/``variables_2``/
      ``units_2`` entries used to build :attr:`profile`. """
    vars = None
    """ ``pandas.DataFrame`` containing information on the per-gate
      variables (as given by the file's ``Data line 2`` header). The
      index and column "label" hold the variable name, "unit" its
      unit, and "type" its decoded Fortran type (``"f"`` or ``"i"``).
      ``None`` for a "Processed Wind Profile" file. """
    rays = None
    """ list of :class:`Ray` objects held in the file, one per ray
      (beam), in the order recorded. Empty for a "Processed Wind
      Profile" file. """
    profile = None
    """ ``pandas.DataFrame`` holding the processed wind profile data
      for a "Processed Wind Profile" file (see module description);
      ``None`` for a regular scan file. The index is height (m); the
      columns are wind direction (degrees) and speed (m/s). """
    text = None
    """ text contents the file loaded. """
    timestamp = None
    type = None
    """ scan type string, one of :data:`TYPES` """
    mode = None
    """ mode of the scan, one of ``csm``, ``overlapping``, ``stepped``,
        or empty """

    #
    # read header "header"
    #
    def _get_header(self):
        """
        Parse the ``Key: value`` header of a regular scan file (see
        module description) up to the terminating line of asterisks.

        :returns: dict of decoded header entries.
        """
        header = dict()
        text_iter = iter(self.text)
        for line in text_iter:
            # line of stars end the header
            if re.match(MARKER, line):
                break
            try:
                hkey, hval = line.split(':', 1)
            except ValueError:
                continue
            if hkey == 'Filename':
                header['filename'] = hval.strip()
            elif hkey == 'System ID':
                header['instrument'] = hval.strip()
            elif hkey == 'Start time':
                header['starttime'] = hval.strip()
            elif hkey == 'Number of gates':
                header['gates'] = hval.strip()
            elif hkey == 'Range gate length (m)':
                header['gatelength'] = hval.strip()
            elif hkey == 'Gate length (pts)':
                header['gatepoints'] = hval.strip()
            elif hkey == 'Pulses/ray':
                header['pulses'] = hval.strip()
            elif hkey == 'No. of rays in file':
                header['rays'] = hval.strip()
            elif hkey == 'Scan type':
                header['scantype'] = hval.strip()
            elif hkey == 'Focus range':
                header['focus'] = hval.strip()
            elif hkey == 'Resolution (m/s)':
                header['resolution'] = hval.strip()
            elif hkey == 'Data line 1' or hkey == 'Data line 2':
                num = int(hkey.split()[2])
                header[f'variables_{num}'] = []
                header[f'units_{num}'] = []
                fields = hval.replace('  ', ')').split(')')
                for f in fields:
                    if len(f) > 0:
                        if '(' in f:
                            x, y = f.split('(')
                        else:
                            x = f
                            y = ''
                        header[f'variables_{num}'].append(x.strip())
                        header[f'units_{num}'].append(y.strip())
                # NOTE: next(text_iter) on its own only *advances* the
                # iterator, it doesn't rebind `line`.
                fmt_line = next(text_iter)
                header[f'format_{num}'] = fmt_line.strip()
            else:
                raise ValueError(
                    f'unknown header {hkey} in {self.file}')
        return header

    #
    # read variable definitions
    #
    def _get_variables(self):
        """
        Build :attr:`vars` from the ``Data line 2`` header entry: one
        row per per-gate variable, decoding its Fortran format code
        (e.g. ``f8.6`` or ``i3``) into a simple ``"f"``/``"i"`` type.

        :returns: the per-gate variable table (see :attr:`vars`).
        """
        columns = ["label", "unit", "type"]
        variables = pd.DataFrame(columns=columns)
        variables["label"] = self.header['variables_2']
        variables["unit"] = self.header['units_2']
        # decode types from format string, e.g.
        # "i3,1x,f6.4,1x,f8.6,1x,e12.6 - repeat for no. gates"
        # "1x" tokens are Fortran-style single-character spacers,
        # keep everything *except* those.
        formats = [x for x in self.header['format_2'].split(',')
                   if not x.endswith('x')]
        types = []
        for f in formats:
            # the last format code can carry a trailing free-text
            # comment (as above); strip it before classifying, or
            # letters from the comment itself can be mistaken for
            # format-code letters.
            code = f.split(' - ')[0].strip()
            if any(c in code for c in ('f', 'd', 'g', 'e')):
                types.append('f')
            elif any(c in code for c in ('i', 'u')):
                types.append('i')
            else:
                fmt_2 = self.header["format_2"]
                raise ValueError(
                    f'unknown format {code} in '
                    f'format {fmt_2} '
                    f'in file {self.file}')
        # some hpl files miss fields in format description
        while len(types) < len(variables["label"]):
            types.append('f')
        variables['type'] = types
        variables.index = variables['label']
        return variables

    #
    # get the data block from file text
    #
    def _get_val(self, name, names, values):
        """
        Look up ``name`` in the parallel lists ``names``/``values``
        (as decoded from a ``Data line 1`` row), returning ``None``
        instead of raising if ``name`` isn't present.
        """
        try:
            return values[names.index(name)]
        except ValueError:
            return None

    def _read_ray(self, lines, gates=None,
                  variables_1=None, variables_2=None):
        """
        Parse one ray's block of text (its ``Data line 1`` row
        followed by one ``Data line 2`` row per gate) into a
        :class:`Ray`.

        :param lines: the ray's block of text, as a single \
            newline-separated string or a list of lines; the first \
            line is the per-ray row, the rest are the per-gate rows.
        :param gates: expected number of gates; defaults to \
            :attr:`header`\\ ``['gates']``.
        :param variables_1: per-ray variable names, in the order \
            they appear in the first line; defaults to \
            :attr:`header`\\ ``['variables_1']``.
        :param variables_2: per-gate variable names, used as the \
            column labels of the returned :class:`Ray`'s :attr:`~Ray.data`.
        :returns: the parsed :class:`Ray`.
        """
        # ensure lines is list of strings
        if isinstance(lines, str):
            lines = lines.split('\n')
        if not lines:
            raise IOError(
                f'ray block is empty in {self.file} (file truncated?)')
        # ensure we have number of gates
        if not gates:
            gates = int(float(self.header['gates']))
        # get attribute values
        if not variables_1:
            variables_1 = self.header['variables_1']
        values_1 = [float(x) for x in lines[0].split()]
        raw_hours = self._get_val('Decimal time', variables_1, values_1)
        hours = pd.Timedelta(
            hours=raw_hours) if raw_hours is not None else None
        azimuth = self._get_val('Azimuth', variables_1, values_1)
        elevation = self._get_val('Elevation', variables_1, values_1)
        pitch = self._get_val('Pitch', variables_1, values_1)
        roll = self._get_val('Roll', variables_1, values_1)
        # construct ray time by replacing the hours in the file timestamp
        if hours is None:
            time = self.timestamp
        else:
            daystart = self.timestamp.replace(hour=0, minute=0, second=0,
                                               microsecond=0)
            time = daystart + hours
        # get the values along the ray
        df = pd.DataFrame([line.split() for line in lines[1:]])
        if len(df.index) != gates:
            raise IOError(f'gates read: {len(df.index)} '
                          f'but expected {gates}')
        if variables_2:
            if len(df.columns) != len(variables_2):
                raise ValueError(
                    f"variables_2 has {len(variables_2)} "
                    f"elements but data have {len(df.columns)} columns")
            df.columns = variables_2
        #
        # put it into a ray object
        return Ray(time=time, elevation=elevation, azimuth=azimuth,
                   pitch=pitch, roll=roll, gates=gates, df=df)

    #
    # read the datablock into rays
    #
    def _get_datablock(self):
        """
        Parse the whole data block of a regular scan file into
        :attr:`rays`, one :class:`Ray` per recorded ray.

        The header's own ``No. of rays in file`` is only trusted up to
        how many complete ray blocks (``Data line 1`` row + one row per
        gate) are actually present in the file: a scan that was still
        being written, or aborted early, ends up with a header count
        higher than what's really there, and the trailing, incomplete
        block would otherwise crash the parser outright (an ``IndexError``
        from indexing into an empty slice of lines) instead of just
        reading the rays that *are* complete. Only the shortfall is
        dropped -- every complete ray up to that point is still read.

        :returns: list of :class:`Ray` objects, in file order.
        """
        # fast forward to data:
        for nl, line in enumerate(self.text):
            # line of stars end the header
            if re.match(MARKER, line):
                data_start = nl + 1
                break
        else:
            raise IOError('data block not found in file')
        nrays = int(float(self.header['rays']))
        ngates = int(float(self.header['gates']))
        block = ngates + 1
        available_rays = max(0, (len(self.text) - data_start) // block)
        if available_rays < nrays:
            logger.warning(
                '%s: header declares %d ray(s) but only %d complete '
                'ray block(s) (%d gates each) are present -- the file '
                'looks truncated; reading only the %d complete ray(s) '
                'found.', self.file, nrays, available_rays, ngates,
                available_rays)
            nrays = available_rays
        rays = []
        for n in range(nrays):
            ray_start = data_start + n * block
            ray_end = data_start + (n + 1) * block
            ray = self._read_ray(self.text[ray_start:ray_end],
                                  gates=ngates,
                                  variables_1=self.header['variables_1'],
                                  variables_2=self.header['variables_2'])
            rays.append(ray)
        return rays

    #
    # read profile data
    #
    def _get_profile(self):
        """
        Regular scan files carry no processed wind profile of their
        own; always returns ``None``. (The header-less "Processed
        Wind Profile" variant bypasses this and builds :attr:`profile`
        directly in :meth:`_load_processed_profile`.)
        """
        return None

    #
    # evaluate file name (used when the file itself carries no header
    # to get the type/timestamp from, e.g. the header-less
    # "Processed Wind Profile" variant, see _load_processed_profile)
    #
    def _parse_filename(self, name=None):
        """
        Derive the scan type and timestamp from the filename, for
        files that have no header of their own to read them from
        (currently only the "Processed Wind Profile" variant).
        Expects a name of the form
        ``<type words>_<number>_<yyyymmdd>_<hhmmss>.hpl``, e.g.
        ``Processed_Wind_Profile_77_20260919_121707.hpl``.

        :param name: filename to parse; defaults to :attr:`file`.
        :returns: ``(type, timestamp)`` tuple.
        """
        if name is None:
            name = self.file
        name = os.path.basename(name)
        # remove extension
        if name.lower().endswith('.hpl'):
            name = name[:-4]
        strings = name.split('_')
        timestamp = pd.to_datetime(''.join(strings[-2:]),
                                    format='%Y%m%d%H%M%S')
        type_ = ' '.join(strings[:-3])
        return type_, timestamp

    def _load_processed_profile(self):
        """
        Parse a header-less "Processed Wind Profile" file: no header
        at all, just a level count followed by whitespace-separated
        height/direction/speed rows, e.g.::

            750
            9.0 240.00   1.17
            ...

        Populates :attr:`header` (synthesized), :attr:`type`,
        :attr:`timestamp`, :attr:`mode`, :attr:`vars` (``None``),
        :attr:`rays` (``[]``) and :attr:`profile`.
        """
        self.header = {
            'variables_1': ['number of levels'],
            'units_1': [''],
            'variables_2': ['height', 'direction', 'speed'],
            'units_2': ['m', 'degrees', 'm/s'],
        }
        # no header to read the type/timestamp from -- get them from
        # the filename instead
        self.type, self.timestamp = self._parse_filename()
        self.mode = ''
        self.vars = None
        self.rays = []
        nlevels = int(self.text[0].strip())
        rows = [line.split() for line in self.text[1:1 + nlevels]
                if line.strip()]
        if len(rows) != nlevels:
            raise IOError(f'profile levels read: {len(rows)} '
                          f'but expected {nlevels} in {self.file}')
        profile = pd.DataFrame(rows, columns=self.header['variables_2'])
        profile = profile.astype(float)
        profile = profile.set_index('height')
        self.profile = profile

    def _parse_scantype(self, scantype):
        """
        Split a header ``Scan type`` value into its type and mode
        parts, e.g. ``"Wind profile - overlapping"`` into
        ``("Wind profile", "overlapping")``. Raises ``ValueError`` if
        the type part isn't one of the known :data:`TYPES`.

        :param scantype: raw ``Scan type`` header value.
        :returns: ``(type, mode)`` tuple; ``mode`` is ``''`` if the \
            header gave no mode.
        """
        if '-' in scantype:
            typestring, modestring = scantype.split('-')
        else:
            typestring = scantype
            modestring = ''
        typestring = typestring.strip()
        modestring = modestring.strip()
        if typestring.upper() not in [x.upper() for x in TYPES]:
            raise ValueError(f'unknown scantype {typestring}')
        return typestring, modestring

    #
    # read file into memory
    #
    def load(self, file=None, text=False):
        """
        Read ``file`` and populate this object from it. Detects which
        of the two file variants (see module description) it is by
        whether the first line contains a ``:`` (a regular file's
        first header line is always ``Filename: ...``); dispatches to
        :meth:`_get_header`/:meth:`_get_datablock` for a regular scan
        file, or to :meth:`_load_processed_profile` for a header-less
        "Processed Wind Profile" file.

        Called automatically from the constructor when ``file`` is
        given there; call it again to (re-)load a different file into
        an existing object.

        :param file: filename (optionally including path); defaults \
            to :attr:`file` (i.e. the file the object was already \
            constructed with).
        :param text: (optional) If ``True`` the raw file contents \
            are retained as attribute :attr:`text` of the object. If \
            ``False`` or missing, the raw file contents are discarded \
            after parsing.
        """
        if file is not None:
            self.file = file
        with open(self.file, 'r') as f:
            self.text = [x.rstrip() for x in f.readlines()]
        # a regular ".hpl" file starts with a "Key: value" header; the
        # "Processed Wind Profile" variant has no header at all, its
        # first line is just the number of profile levels
        if ':' in self.text[0]:
            self.header = self._get_header()
            starttime = self.header['starttime'].strip()
            # some files give whole-second start times with no
            # fractional part; pad so the format string (which
            # requires it) still matches
            if '.' not in starttime:
                starttime += '.0'
            self.timestamp = pd.to_datetime(starttime,
                                             format='%Y%m%d %H:%M:%S.%f')
            self.type, self.mode = self._parse_scantype(
                self.header['scantype'])
            self.vars = self._get_variables()
            self.rays = self._get_datablock()
            self.profile = self._get_profile()
        else:
            self._load_processed_profile()
        if text is not True:
            self.text = None

    #
    # constructor
    #
    def __init__(self, file=None, text=None):
        object.__init__(self)
        self.file = file
        if file is not None:
            self.load(file, text)

    # ------------------------------------------------------------------------

    def filter(self, number=None, azimuth=None, elevation=None):
        """
        Select rays from :attr:`rays` matching all of the given
        criteria. Each argument accepts either a single value or a
        list/tuple of values; a value of ``None`` (the default)
        applies no filter on that criterion.

        :param number: ray index (or indices) within :attr:`rays`, \
            e.g. ``0`` for the first ray recorded.
        :param azimuth: azimuth angle in degrees (or list of angles) \
            to keep.
        :param elevation: elevation angle in degrees (or list of \
            angles) to keep.
        :returns: a single :class:`Ray` if exactly one ray matches, \
            otherwise a (possibly empty) list of :class:`Ray` \
            objects.
        """
        out = []
        if number is not None and not isinstance(number, (list, tuple)):
            number = [number]
        if azimuth is not None and not isinstance(azimuth, (list, tuple)):
            azimuth = [azimuth]
        if elevation is not None and not isinstance(elevation, (list, tuple)):
            elevation = [elevation]
        for i, r in enumerate(self.rays):
            if (((number is None) or (i in number)) and
                    ((azimuth is None) or (r.azimuth in azimuth)) and
                    ((elevation is None) or (r.elevation in elevation))):
                out.append(r)
        if len(out) == 1:
            return out[0]
        else:
            return out


# ------------------------------------------------------------------------

def read(pattern, number=None, azimuth=None, elevation=None):
    """
    read a sequence of ".hpl" files into one data structure

    :param pattern: a globbing pattern describing one or multiple \
        filenames or paths, or a list of such patterns
    :param number: (optional) ray index (within each file) or list of \
        indices to keep. See :meth:`DataFile.filter`.
    :param azimuth: (optional) azimuth angle in degrees, or list of \
        angles, to keep. See :meth:`DataFile.filter`.
    :param elevation: (optional) elevation angle in degrees, or list of \
        angles, to keep. See :meth:`DataFile.filter`.
    :returns: contained variables (i.e. the columns of `Ray.data` other \
        than ``Range Gate``) as dictionary with variable names as keys. \
        Each variable is returned as a ``pandas.DataFrame`` with ray \
        time as index and range gate number as columns.

    .. note:: this reads rays, not profiles: a header-less \
        "Processed Wind Profile" file (see module description) has \
        no rays (:attr:`DataFile.rays` is empty), so it contributes \
        nothing to the result. Read such files individually with \
        :class:`DataFile` and use its :attr:`~DataFile.profile` \
        instead.
    """
    # expand globbing pattern
    if isinstance(pattern, list):
        files = []
        for x in pattern:
            logger.debug('globbing pattern is: %s', x)
            ex = glob.glob(x)
            logger.debug('expanded file list : %s', ex)
            files += ex
    else:
        files = glob.glob(pattern)
    logger.debug('list of files to open: %s', files)
    # read all files
    fields = {}
    for i, file in enumerate(files):
        logger.info('opening file #%d: %s', i, file)
        data = DataFile(file)
        selected = data.filter(number=number, azimuth=azimuth,
                                elevation=elevation)
        # DataFile.filter() returns a bare Ray when exactly one matches,
        # and a (possibly empty) list otherwise -- normalize to a list.
        if isinstance(selected, Ray):
            selected = [selected]
        for r in selected:
            # column labels: the range gate number, if present,
            # else just 0..gates-1
            if 'Range Gate' in r.data.columns:
                gates = [int(x) for x in r.data['Range Gate']]
            else:
                gates = list(range(len(r.data.index)))
            for c in r.data.columns:
                if c == 'Range Gate':
                    continue
                # one row (this ray, at this time) of gate values
                nf = pd.DataFrame(r.data[c]).transpose()
                nf.set_index(pd.DatetimeIndex([r.time]), inplace=True)
                nf.columns = gates
                if c not in fields:
                    fields[c] = nf
                else:
                    fields[c] = pd.concat([fields[c], nf])
        del data
    # sort each variable's data by time
    for c in fields:
        fields[c].sort_index(inplace=True)
    return fields
