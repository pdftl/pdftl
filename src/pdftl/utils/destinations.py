import logging
from collections.abc import Iterable
from typing import Any, NamedTuple, cast

logger = logging.getLogger(__name__)


class ResolvedDest(NamedTuple):
    page_num: int
    dest_type: str
    args: list[Any]


def get_named_destinations(pdf):
    from pikepdf import NameTree

    if "/Names" in pdf.Root and "/Dests" in pdf.Root.Names:
        return NameTree(pdf.Root.Names.Dests)
    return None


def _find_page_index(page_obj, pdf_pages) -> int | None:
    """Helper to find 1-based index of a page object."""
    if not hasattr(page_obj, "objgen"):
        return None
    for i, page in enumerate(pdf_pages):
        if hasattr(page, "objgen") and page.objgen == page_obj.objgen:
            return i + 1
    return None


def _dest_from_outline_item(dest):
    # OutlineItem.destination handles the primary lookup;
    # fallback to action if None
    orig_item = dest
    dest = orig_item.destination
    if dest is None and hasattr(orig_item, "action"):
        action = orig_item.action
        if action is not None and hasattr(action, "D"):
            dest = action.D
    return dest


def _resolve_named_dest_to_array(dest, named_destinations):
    from pikepdf import Array, Dictionary

    dest_str = str(dest).lstrip("/")
    dest_obj = named_destinations.get(dest_str) if named_destinations else None

    if isinstance(dest_obj, Dictionary) and "/D" in dest_obj:
        return dest_obj["/D"]
    elif isinstance(dest_obj, Array):
        return dest_obj

    return None


def resolve_dest_to_page_num(
    dest: Any, pdf_pages: Any, named_destinations: Any
) -> ResolvedDest | None:
    """
    Generalized resolver: Takes an OutlineItem, Array, Name, String, or Dict
    and returns a ResolvedDest(page_num, dest_type, args).
    """
    from pikepdf import Array, Dictionary, Name, OutlineItem, String

    # 1. Extract destination from OutlineItem or Action Dictionary
    if isinstance(dest, OutlineItem):
        dest = _dest_from_outline_item(dest)

    if isinstance(dest, Dictionary) and "/D" in dest:
        dest = dest["/D"]

    # 2. Resolve Named Destinations (String/Name -> Array)
    if isinstance(dest, (String, Name, str)):
        dest = _resolve_named_dest_to_array(dest, named_destinations)

    # 3. Extract Data from [page_obj /Type ...]
    if isinstance(dest, Array) and len(dest) > 0:
        page_num = _find_page_index(dest[0], pdf_pages)
        if page_num:
            dest_type = str(dest[1]).lstrip("/") if len(dest) > 1 else "XYZ"
            dest_args = list(cast(Iterable[Any], dest))[2:] if len(dest) > 2 else []
            return ResolvedDest(page_num, dest_type, dest_args)

    return None
