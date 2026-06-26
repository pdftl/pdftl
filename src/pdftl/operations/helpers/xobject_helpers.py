# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/helpers/xobject_helpers.py

"""Utilities for handling and reading Form XObjects."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def read_xobject_stream(xobj, normalize: bool) -> bytes:
    """Return the content bytes for a Form XObject, normalized or raw."""
    import pikepdf

    if not normalize:
        return xobj.read_bytes()
    try:
        return pikepdf.unparse_content_stream(pikepdf.parse_content_stream(xobj))
    except (pikepdf.PdfError, ValueError, TypeError) as e:
        logger.warning(
            "Could not normalize Form XObject stream: %s. Falling back to raw bytes.", e
        )
        return xobj.read_bytes()
