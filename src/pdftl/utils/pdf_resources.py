# src/pdftl/utils/pdf_resources.py

import logging
from typing import Any, TYPE_CHECKING
from collections.abc import Generator
from contextlib import suppress

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)


def find_resource_recursive(
    pdf: "pikepdf.Pdf", resource_type: str, resource_name: str
) -> Any | None:
    """
    Searches for a specific resource (e.g. a Font named '/F1') across all pages
    and their nested XObjects.
    """
    visited_xobjects: set[int] = set()

    for page in pdf.pages:
        result = _search_container(page, resource_type, resource_name, visited_xobjects)
        if result:
            return result
    return None


def _search_container(
    container: Any, resource_type: str, target_name: str, visited: set[int]
) -> Any | None:
    """Recursively searches a container (Page or XObject). resource_type is e.g., '/Font'"""
    if "/Resources" not in container:
        return None

    resources = container.Resources
    return (
        _check_immediate_resources(resource_type, resources, target_name)
        or _next_xobject_match(resource_type, resources, target_name, visited)
        or None
    )


def _check_immediate_resources(resource_type, resources, target_name):
    # Check immediate resources
    # resource_type is e.g. "/Font"
    if resource_type in resources:
        target_key = target_name if target_name.startswith("/") else "/" + target_name
        if target_key in resources[resource_type]:
            return resources[resource_type][target_key]
    return None


def _next_xobject_match(resource_type, resources, target_name, visited):
    import pikepdf

    if "/XObject" in resources:
        for _, xobj in resources["/XObject"].items():
            if not isinstance(xobj, pikepdf.Object) or xobj.objgen in visited:
                continue
            visited.add(xobj.objgen)

            if xobj.get("/Subtype") == "/Form":
                return _search_container(xobj, resource_type, target_name, visited)

    return None


def _yield_immediate_fonts(
    resources: Any, page_num: int
) -> Generator[tuple[str, Any, int], None, None]:
    import pikepdf

    if "/Font" not in resources:
        return
    for name, font_obj in resources.Font.items():
        if isinstance(font_obj, pikepdf.Object):
            yield str(name), font_obj, page_num


def _yield_xobject_fonts(
    resources: Any, page_num: int, visited: set[int]
) -> Generator[tuple[str, Any, int], None, None]:
    import pikepdf

    if "/XObject" not in resources:
        return
    for _, xobj in resources.XObject.items():
        if not isinstance(xobj, pikepdf.Object) or xobj.objgen in visited:
            continue
        visited.add(xobj.objgen)
        if xobj.get("/Subtype") == "/Form":
            yield from _walk_container(xobj, page_num, visited)


def _yield_pattern_fonts(
    resources: Any, page_num: int, visited: set[int]
) -> Generator[tuple[str, Any, int], None, None]:
    import pikepdf

    if "/Pattern" not in resources:
        return
    for _, pat in resources.Pattern.items():
        if isinstance(pat, pikepdf.Object) and pat.objgen not in visited:
            visited.add(pat.objgen)
            yield from _walk_container(pat, page_num, visited)


def _yield_extgstate_fonts(
    resources: Any, page_num: int, visited: set[int]
) -> Generator[tuple[str, Any, int], None, None]:
    import pikepdf

    if "/ExtGState" not in resources:
        return
    for gs_key, gs in resources.ExtGState.items():
        if not isinstance(gs, pikepdf.Object) or gs.objgen in visited:
            continue
        visited.add(gs.objgen)
        if "/Font" in gs:
            with suppress(TypeError, KeyError, IndexError, AttributeError, pikepdf.PdfError):
                font_arr = gs.Font
                if len(font_arr) > 0 and isinstance(font_arr[0], pikepdf.Object):
                    yield f"{str(gs_key)}_ExtGState", font_arr[0], page_num


def _iter_appearance_streams(ap_entry: Any) -> Generator[Any, None, None]:
    """Yields the actual stream object(s) referenced by an /AP /N, /D, or /R
    entry, which per spec can either be a stream directly, or a sub-dictionary
    keyed by appearance state (e.g. /N << /Off ... /On ... >>)."""
    import pikepdf

    if not isinstance(ap_entry, pikepdf.Object):
        return

    if ap_entry.get("/Subtype") == "/Form":
        yield ap_entry
        return

    # Otherwise assume it's a dict of appearance states
    with suppress(TypeError, KeyError, AttributeError, pikepdf.PdfError):
        for _, state_obj in ap_entry.items():
            if isinstance(state_obj, pikepdf.Object):
                yield state_obj


def _yield_annot_fonts(
    annot: Any, page_num: int, visited: set[int]
) -> Generator[tuple[str, Any, int], None, None]:
    """Yields fonts found across a single annotation's /AP /N, /D, and /R
    appearance stream(s)."""
    ap = annot.AP
    for ap_key in ("/N", "/D", "/R"):
        if ap_key not in ap:
            continue
        for stream_obj in _iter_appearance_streams(ap[ap_key]):
            if stream_obj.objgen in visited:
                continue
            visited.add(stream_obj.objgen)
            yield from _walk_container(stream_obj, page_num, visited)


def _yield_annotation_fonts(
    page: Any, page_num: int, visited: set[int]
) -> Generator[tuple[str, Any, int], None, None]:
    """Walks a page's /Annots array and yields fonts found inside each
    annotation's appearance stream(s) (/AP /N, /D, /R), since these are
    never part of the page's own /Resources dict."""
    import pikepdf

    if "/Annots" not in page:
        return

    with suppress(TypeError, KeyError, AttributeError, pikepdf.PdfError):
        for annot in page.Annots:
            if not isinstance(annot, pikepdf.Object) or "/AP" not in annot:
                continue
            yield from _yield_annot_fonts(annot, page_num, visited)


def _walk_container(
    container: Any, page_num: int, visited: set[int]
) -> Generator[tuple[str, Any, int], None, None]:
    if not hasattr(container, "get") or "/Resources" not in container:
        return

    resources = container.Resources
    yield from _yield_immediate_fonts(resources, page_num)
    yield from _yield_xobject_fonts(resources, page_num, visited)
    yield from _yield_pattern_fonts(resources, page_num, visited)
    yield from _yield_extgstate_fonts(resources, page_num, visited)


def get_all_fonts_recursive(
    pdf: "pikepdf.Pdf", page_indices: list[int] | None = None
) -> Generator[tuple[str, Any, int], None, None]:
    """
    Recursively walks all pages and Form XObjects (as well as Patterns and ExtGStates)
    in the PDF, yielding all font objects found.
    """
    import pikepdf

    target_pages = page_indices if page_indices else range(1, len(pdf.pages) + 1)

    for page_num in target_pages:
        try:
            page = pdf.pages[page_num - 1]
            visited = set()
            yield from _walk_container(page, page_num, visited)
            yield from _yield_annotation_fonts(page, page_num, visited)
        except (
            IndexError,
            TypeError,
            KeyError,
            AttributeError,
            ValueError,
            pikepdf.PdfError,
        ) as e:
            logger.warning("Error traversing page %d: %s", page_num, e)
