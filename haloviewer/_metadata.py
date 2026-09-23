# SPDX-License-Identifier: EUPL-1.2
# (c) 2026 Clemens Drüe, Universität Trier
# developed with support of Anthropic Claude Opus 5.5
"""
Static package metadata.

Everything here is fixed information about the project (name, author,
licence, ...). The *version* is not here: it is derived from the
version-control history by `setuptools-scm
<https://setuptools-scm.readthedocs.io>`_ and written to
``haloviewer/_version.py`` at build/install time (see
:data:`haloviewer.__version__`).

Used by the package itself and by the Sphinx configuration
(``docs/conf.py``), so project information lives in one place.
"""

#: Distribution / import name.
__title__ = "haloviewer"

#: Human-readable product name.
__product__ = "HaloViewer"

#: One-line summary.
__description__ = ("Viewer, plotting and synchronisation tools for "
                   "Halo Photonics wind lidar data")

#: Author.
__author__ = "Clemens Drüe"

#: Author e-mail.
__email__ = "druee@uni-trier.de"

#: Institution.
__affiliation__ = "Universität Trier"

#: SPDX licence identifier.
__license__ = "EUPL-1.2"

#: Copyright notice.
__copyright__ = "(c) 2026 Clemens Drüe, Universität Trier"

#: Acknowledgement.
__credits__ = "developed with support of Anthropic Claude Opus 5.5"
