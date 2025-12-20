# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/info/read_info.py

"""Read metadata from a PDF file.

Public:

pdf_id_metadata_as_strings
get_named_destinations
resolve_page_number
"""

import logging

logger = logging.getLogger(__name__)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import OutlineItem, NameTree


from pdftl.utils.whatisit import is_page


def pdf_id_metadata_as_strings(pdf):
    """Return PDF trailer ID metadata, as a list of strings"""
    output = []
    if pdf.trailer and hasattr(pdf.trailer, "ID") and pdf.trailer.ID:
        for id_data in pdf.trailer.ID:
            output.append(bytes(id_data).hex())
    return output


def get_named_destinations(pdf):
    """Get the named destinations NameTree from the PDF, if there is one"""
    from pikepdf import NameTree

    if "/Names" in pdf.Root and "/Dests" in pdf.Root.Names:
        return NameTree(pdf.Root.Names.Dests)
    return None


def _get_destination_array(item: "OutlineItem", named_destinations: "NameTree"):
    """
    Extracts the destination array from a bookmark item.

    A destination can be stored directly, in an action, or as a named destination
    that needs to be looked up. This function isolates that logic.

    """
    # logger.debug("item=%s, type: %s", item, type(item))
    # if not isinstance(item, (Dictionary, dict, OutlineItem)) or not hasattr(item, "destination"):
    #     logger.debug("returning early: item is not a valid container")
    #     return None
    from pikepdf import Array, Dictionary, Name, OutlineItem, String

    if not isinstance(item, OutlineItem):
        logger.warning("Invalid item passed, returning None")
        return None

    dest = item.destination
    logger.debug("dest=%s, type: %s", dest, type(dest))
    # 1. Fallback to the action's destination if the primary one is missing
    if dest is None and hasattr(item, "action") and hasattr(item.action, "D"):
        dest = item.action.D

    # 2. Handle direct array destinations
    if isinstance(dest, Array):
        return dest

    # 3. Resolve named destinations (which are often String or Name objects)
    if isinstance(dest, (String, Name, str)):
        dest_str = str(dest)
        if isinstance(dest, Name):
            # strip leading /
            dest_str = dest_str[1:]
        dest_obj = named_destinations.get(dest_str)
        # Named destination objects are dictionaries containing the actual array
        if isinstance(dest_obj, Dictionary) and dest_obj.get("/D"):
            return dest_obj.get("/D")
        # or they can be the array itself!
        # see PDF 32000-1:2008, section 12.3.2.3 Named Destinations
        if isinstance(dest_obj, Array):
            return dest_obj

    logger.debug("fall through from _get_destination_array, returning None")
    return None


def resolve_page_number(item: "OutlineItem", pdf_pages, named_destinations):
    """
    Resolves a bookmark item to a specific page number.

    Returns:
        The page number (1-indexed) or None if it cannot be resolved.
    """
    dest_array = _get_destination_array(item, named_destinations)
    if not dest_array or len(dest_array) == 0:
        logger.debug("dest_array=%s", dest_array)
        logger.debug("Empty(?) dest_array, returning None")
        return None

    # The first element of the destination array is the target page object.
    page_obj = dest_array[0]
    if not is_page(page_obj):
        logger.debug("Not a page, returning None")
        return None

    # just in case:
    assert hasattr(page_obj, "objgen")

    # Find the page by comparing object numbers
    for i, page in enumerate(pdf_pages):
        assert hasattr(page, "objgen")
        logger.debug("page.objgen = %s, type = %s", page.objgen, type(page.objgen))
        logger.debug("page_obj.objgen = %s, type = %s", page_obj.objgen, type(page_obj.objgen))
        if page.objgen == page_obj.objgen:
            return i + 1  # Page numbers are 1-indexed

    logger.debug("Fall-through, returning None")
    return None
