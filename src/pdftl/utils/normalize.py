# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/normalize.py

"""Normalize page content streams"""

import logging

logger = logging.getLogger(__name__)
LOG_TRUNC = 500


def get_normalized_page_content_stream(page):
    """Return a normalized version of the content stream of
    a page. This should have one PDF operator and its
    arguments per line."""
    import pikepdf

    parsed = pikepdf.parse_content_stream(page)
    logger.debug("str(parsed)[:%s]=%s", LOG_TRUNC, str(parsed)[:LOG_TRUNC])
    unparsed = pikepdf.unparse_content_stream(parsed)
    logger.debug("str(unparsed)[:%s]=%s", LOG_TRUNC, str(unparsed)[:LOG_TRUNC])
    return unparsed


def normalize_page_content_stream(pdf, page):
    """Replace the content stream of a page with its
    normalized verison.

    See get_normalized_page_content_stream for details of
    normalization.

    """
    page.Contents = pdf.make_stream(get_normalized_page_content_stream(page))
