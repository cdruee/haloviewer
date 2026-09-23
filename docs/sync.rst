==========================
Sync tool (``halosync``)
==========================

``halosync`` is a small desktop application for copying lidar data
selectively from a *source* (typically the SMB share of the lidar
control PC) to a *destination* (a backup disk, a server, the analysis
machine). It only copies what is missing, never deletes anything, and
leaves alone the file the lidar software is still writing.

It uses only the Python standard library (Tkinter, ``shutil``,
``threading``, ...) and is independent of the plotting part of
|product|. If ``import tkinter`` fails in a Miniconda installation,
``conda install tk`` fixes it.

.. code:: bash

   halosync

Data layout
-----------

Both source and destination are expected to contain up to three
top-level *groups*, each split into *subgroups* by file-name prefix:

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - group
     - subgroups (file-name prefixes)
   * - ``Metek``
     - ``Stare``, ``VAD``
   * - ``Proc``
     - ``system_parameters``, ``Background``, ``Stare``, ``VAD``,
       ``RHI``, ``User1`` ... ``User5``, ``Processed_Wind_Profile``,
       ``Wind_Profile``
   * - ``Raw``
     - ``Stare``, ``VAD``, ``RHI``, ``User1`` ... ``User5`` (the prefix
       may also be written ``aet_<subgroup>``)

A file belongs to a subgroup if its *file name* (not its path) starts
with the subgroup name. Files matching none of the prefixes are ignored
and never copied. The directory structure below each group (years,
months, days) is copied as it is. The table is defined in
``GROUPS`` at the top of
``haloviewer/halosync.py`` and can be adapted there.

Window layout
-------------

The window has three columns:

**Source** (left)
   A path field with a browse button, and below it a tree of the groups
   and subgroups found in the source, each with a tick-box. Ticking a
   group selects all its subgroups. Subgroups without any files are
   greyed out. If the source contains none of the groups, "no data
   found" is shown.

**Scan and Sync** (middle)
   *Scan*: the ↻ button rescans source and destination, and the
   "full scan" tick-box discards the scan cache first (see below).
   A status line shows when the last scan ran.

   *Sync*: the → button starts copying the selected files. Below it,
   the number and total size of the files that would be copied are
   shown, followed by the options described in the next section.

**Destination** (right)
   A path field with a browse button, and the same tree with a
   *signal light* instead of a tick-box for each group and subgroup:

   * **green** -- everything that can be synchronised is already there,
   * **orange** -- files are still missing (not counting the current
     file, see below),
   * **grey** -- the subgroup has no files in the source.

   The lights are independent of the tick-boxes: they always show the
   state of the whole subgroup.

Synchronising
-------------

A file counts as *already present* at the destination if a file with
the same relative path, the same size and the same modification time
(within 2 seconds, to allow for SMB/FAT rounding) exists there. Such
files are skipped.

The newest file of each group, judged by the timestamp in its file
name, is taken to be the **current file**: the one the lidar software
most likely still has open. It is left out by default. Two timestamp
notations are recognised: ``yyyymmdd_hhmmss`` (e.g.
``VAD_77_20221020_111520.hpl``) and ``ddmmyy-hhmmss`` (e.g.
``Background_170926-065156.txt``). Files without a timestamp, such as
``system_parameters_*.txt``, are never treated as the current file.

Two one-off options change this. Both untick themselves after each
sync:

``⚠ copy current files``
   Also copy the current file of each group.
``⚠ copy everything``
   Ignore the *already present* check and copy (overwrite) all selected
   files again.

While a sync runs, a progress window shows the file being copied, the
amount transferred and a *Cancel* button. Each file is copied in one
go, so cancelling takes effect between two files. Files are first
written under a temporary name ending in ``.part`` and renamed only
when complete, so a half-copied file never appears under its real name
at the destination. Errors on single files are reported and the rest
continue.

Scheduled sync
--------------

Ticking **sync every … h** starts an automatic sync every 1, 2, 3, 6,
12 or 24 hours. A countdown (hh:mm, updated every minute) shows the
time to the next run, and one minute before it a scan is started so the
sync works with an up-to-date file list. While the schedule is active,
the manual → button and the two ⚠ options are greyed out: scheduled
syncs always run without the current file and without forced
re-copying. The interval can still be changed.

There is no other background activity. Scans only happen at program
start, when a path changes, when ↻ is pressed, and before a scheduled
sync.

Scan cache and saved state
--------------------------

Listing directories over a network share is slow, so ``halosync``
keeps a cache of every directory it has listed (file names, sizes and
modification times) for both sides. Unchanged folders are not listed
again. After every scan, the cache and the last source and destination
paths are saved to a JSON file:

* Windows: ``%APPDATA%\LidarSyncGUI\state.json`` (the user's roaming
  profile),
* elsewhere: ``~/LidarSyncGUI/state.json``.

On the next start the paths are restored and the first scan starts with
this "warm" cache. If the file is missing, unreadable or from an
incompatible version, ``halosync`` simply starts empty. Tick **full
scan** before pressing ↻ to throw the cache away once, e.g. if you
don't trust the results.

Reference
---------

The non-GUI logic of ``halosync`` is kept in plain functions, so it can
be tested and reused on its own.

.. autofunction:: haloviewer.halosync.extract_timestamp
.. autofunction:: haloviewer.halosync.classify_subgroup
.. autofunction:: haloviewer.halosync.scan_root
.. autofunction:: haloviewer.halosync.dest_file_matches
.. autofunction:: haloviewer.halosync.build_transfer_list
.. autofunction:: haloviewer.halosync.get_config_path
.. autofunction:: haloviewer.halosync.save_state
.. autofunction:: haloviewer.halosync.load_state
