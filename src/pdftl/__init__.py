# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/__init__.py

"""
pdftl - PDF tackle
A powerful, compatible replacement for pdftk with advanced features.
"""

# Expose the core API module
from pdftl import api

# Expose exception hierarchy for easier error handling
from pdftl.exceptions import PdftlError

# Expose the Fluent API class for easy importing
from pdftl.fluent import PdfPipeline, pipeline

# Define what is exported when using 'from pdftl import *'
__all__ = [
    "PdfPipeline",
    "pipeline",
    "api",
    "PdftlError",
]

# Version management
try:
    from ._version import version as __version__
except ImportError:
    __version__ = "0.0.0+unknown"


def __getattr__(name):
    """
    Allow top-level access to API commands (e.g. pdftl.cat instead of pdftl.api.cat).
    """
    try:
        return getattr(api, name)
    except AttributeError:
        raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def __dir__():
    """
    Include API commands in top-level tab completion.
    """
    return list(globals().keys()) + dir(api)
