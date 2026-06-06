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


def get_all_fonts_recursive(
    pdf: "pikepdf.Pdf", page_indices: list[int] | None = None
) -> Generator[tuple[str, Any, int], None, None]:
    """
    Recursively walks all pages and Form XObjects (as well as Patterns and ExtGStates)
    in the PDF, yielding all font objects found.

    Yields:
        Tuple containing (font_name, font_obj, page_num)
    """
    import pikepdf

    def _walk_container(container: Any, page_num: int, visited_containers: set[int]):
        if not hasattr(container, "get") or "/Resources" not in container:
            return

        resources = container.Resources

        # 1. Yield any fonts found at this level
        if "/Font" in resources:
            for name, font_obj in resources.Font.items():
                if isinstance(font_obj, pikepdf.Object):
                    yield str(name), font_obj, page_num

        # 2. Dive into any Form XObjects at this level
        if "/XObject" in resources:
            for _, xobj in resources.XObject.items():
                if not isinstance(xobj, pikepdf.Object) or xobj.objgen in visited_containers:
                    continue
                visited_containers.add(xobj.objgen)
                if xobj.get("/Subtype") == "/Form":
                    yield from _walk_container(xobj, page_num, visited_containers)

        # 3. Dive into Patterns (Tiling patterns often contain text)
        if "/Pattern" in resources:
            for _, pat in resources.Pattern.items():
                if isinstance(pat, pikepdf.Object) and pat.objgen not in visited_containers:
                    visited_containers.add(pat.objgen)
                    yield from _walk_container(pat, page_num, visited_containers)

        # 4. Dive into ExtGStates (Sometimes harbor Array-based Font References)
        if "/ExtGState" in resources:
            for gs_key, gs in resources.ExtGState.items():
                if isinstance(gs, pikepdf.Object) and gs.objgen not in visited_containers:
                    visited_containers.add(gs.objgen)
                    if "/Font" in gs:
                        try:
                            font_arr = gs.Font
                            if len(font_arr) > 0 and isinstance(font_arr[0], pikepdf.Object):
                                yield f"{str(gs_key)}_ExtGState", font_arr[0], page_num
                        except (TypeError, KeyError, IndexError, AttributeError, pikepdf.PdfError):
                            pass

    target_pages = page_indices if page_indices else range(1, len(pdf.pages) + 1)

    for page_num in target_pages:
        try:
            page = pdf.pages[page_num - 1]
            # Track visited containers per page to accurately map font usages across different
            # pages
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
