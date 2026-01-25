# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/info/read_info.py

"""Read metadata from a PDF file.

Public:

pdf_id_metadata_as_strings
resolve_page_number
"""

import logging

from pdftl.utils.destinations import resolve_dest_to_page_num

logger = logging.getLogger(__name__)


def pdf_id_metadata_as_strings(pdf):
    """Return PDF trailer ID metadata, as a list of strings"""
    output = []
    if pdf.trailer and hasattr(pdf.trailer, "ID") and pdf.trailer.ID:
        for id_data in pdf.trailer.ID:
            output.append(bytes(id_data).hex())
    return output


def resolve_page_number(item, pdf_pages, named_destinations):
    """
    Resolves a bookmark item to a specific page number.
    """
    resolved = resolve_dest_to_page_num(item, pdf_pages, named_destinations)
    return resolved.page_num if resolved else None
