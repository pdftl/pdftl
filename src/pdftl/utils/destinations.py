# src/pdftl/utils/destinations.py

import logging
from typing import Any, List, NamedTuple, Optional

import pikepdf

logger = logging.getLogger(__name__)


class ResolvedDest(NamedTuple):
    page_num: int
    dest_type: str
    args: List[str]


def get_named_destinations(pdf):
    """Get the named destinations NameTree from the PDF, if there is one"""
    from pikepdf import NameTree

    if "/Names" in pdf.Root and "/Dests" in pdf.Root.Names:
        return NameTree(pdf.Root.Names.Dests)
    return None


def resolve_dest_to_page_num(
    dest: Any, pdf_pages: Any, named_destinations: Any
) -> Optional[ResolvedDest]:
    """
    Generalized resolver: Takes a pikepdf Object (Array, Name, String, or Dict)
    and returns a ResolvedDest(page_num, dest_type, args).
    """
    from pikepdf import Array, Dictionary, Name, String

    # 1. Handle Action Dictionaries (GoTo actions use /D for destination)
    if isinstance(dest, Dictionary) and "/D" in dest:
        dest = dest["/D"]

    # 2. Resolve Named Destinations
    if isinstance(dest, (String, Name, str)):
        dest_str = str(dest).lstrip("/")
        # Look up in the NameTree
        dest_obj = named_destinations.get(dest_str) if named_destinations else None

        if isinstance(dest_obj, Dictionary) and "/D" in dest_obj:
            dest = dest_obj["/D"]
        elif isinstance(dest_obj, Array):
            dest = dest_obj
        else:
            logger.debug("Could not resolve named destination: %s", dest_str)
            return None

    # 3. Extract Data from Destination Array [page_obj /Type ...]
    if isinstance(dest, Array) and len(dest) > 0:
        page_obj = dest[0]

        # Match page object to 1-indexed page number using objgen
        page_num = None
        if hasattr(page_obj, "objgen"):
            for i, page in enumerate(pdf_pages):
                if page.objgen == page_obj.objgen:
                    page_num = i + 1
                    break

        if page_num is None:
            logger.debug("Destination array found but page object could not be matched.")
            return None

        # Extract type (e.g., /XYZ, /Fit)
        dest_type = str(dest[1]).lstrip("/") if len(dest) > 1 else "XYZ"

        # Extract remaining arguments as strings (null, 0, 806, etc.)
        dest_args = list(dest)[2:] if len(dest) > 2 else []

        return ResolvedDest(page_num, dest_type, dest_args)

    return None
