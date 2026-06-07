# src/pdftl/utils/pdf_resources.py

import logging
from typing import Any, TYPE_CHECKING
from collections.abc import Generator

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
            try:
                font_arr = gs.Font
                if len(font_arr) > 0 and isinstance(font_arr[0], pikepdf.Object):
                    yield f"{str(gs_key)}_ExtGState", font_arr[0], page_num
            except (TypeError, KeyError, IndexError, AttributeError, pikepdf.PdfError):
                pass


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
        except (
            IndexError,
            TypeError,
            KeyError,
            AttributeError,
            ValueError,
            pikepdf.PdfError,
        ) as e:
            logger.warning("Error traversing page %d: %s", page_num, e)
