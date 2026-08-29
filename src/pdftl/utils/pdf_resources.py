# src/pdftl/utils/pdf_resources.py

import logging
from typing import Any, TYPE_CHECKING
from collections.abc import Generator
from contextlib import suppress
from dataclasses import dataclass

if TYPE_CHECKING:
    import pikepdf

from pdftl.utils.pikepdf_helpers import get_inheritable


logger = logging.getLogger(__name__)


# See pikepdf_helpers for usage note
# basically: use this for write access to a Page's resources
# (and only in that case)
#
# IMPORTANT Note on Sub-Dictionaries (/Font, /XObject, /ExtGState, etc.):
# ensure_page_resources creates a private top-level /Resources dict, but its
# sub-dictionaries still reference shared parent objects.
# If mutating a sub-dictionary in place (e.g. adding a key to res.Font), copy
# that sub-dictionary first to avoid polluting sibling pages:
#   res["/Font"] = pikepdf.Dictionary(res.Font)
def ensure_page_resources(page) -> "pikepdf.Dictionary":
    """Ensures page has a local /Resources dict, copying inherited resources if necessary."""
    import pikepdf

    page_obj = getattr(page, "obj", page)
    if "/Resources" not in page_obj:
        inherited = get_inheritable(page, "/Resources")
        if inherited is not None:
            # Fork/copy inherited dictionary
            # so we don't mutate parent or shadow it with an empty dict
            page_obj["/Resources"] = pikepdf.Dictionary(inherited)
        else:
            page_obj["/Resources"] = pikepdf.Dictionary()
    return page_obj["/Resources"]


def get_resources(container: Any) -> Any | None:
    """Gets /Resources dict, accounting for /Pages inheritance if container is a Page."""
    page_obj = getattr(container, "obj", container)
    if hasattr(container, "as_page") or (
        hasattr(page_obj, "get") and page_obj.get("/Type") == "/Page"
    ):
        return get_inheritable(container, "/Resources")

    if hasattr(container, "get"):
        return container.get("/Resources")

    return None


def unshare_resources_key(resources: "pikepdf.Dictionary", key: str):
    import pikepdf

    if key not in resources:
        resources[key] = pikepdf.Dictionary({})
    else:
        resources[key] = pikepdf.Dictionary(resources[key])
    return resources[key]


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
    resources = get_resources(container)
    if resources is None:
        return None

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
    keyed by appearance state (e.g. /N << /Off ... /On ... >>).

    Yields (state_name, stream_obj) pairs. state_name is None when ap_entry
    is a direct stream (no appearance-state sub-dictionary); otherwise it's
    the state key (e.g. "/On") the stream was found under.
    """
    import pikepdf

    if not isinstance(ap_entry, pikepdf.Object):
        return

    if ap_entry.get("/Subtype") == "/Form":
        yield None, ap_entry
        return

    # Otherwise assume it's a dict of appearance states
    with suppress(TypeError, KeyError, AttributeError, pikepdf.PdfError):
        for state_key, state_obj in ap_entry.items():
            if isinstance(state_obj, pikepdf.Object):
                yield str(state_key), state_obj


def _yield_annot_fonts(
    annot: Any, page_num: int, visited: set[int]
) -> Generator[tuple[str, Any, int], None, None]:
    """Yields fonts found across a single annotation's /AP /N, /D, and /R
    appearance stream(s)."""
    ap = annot.AP
    for ap_key in ("/N", "/D", "/R"):
        if ap_key not in ap:
            continue
        for _state_name, stream_obj in _iter_appearance_streams(ap[ap_key]):
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
    resources = get_resources(container)
    if resources is None:
        return

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


# --- new additions to src/pdftl/utils/pdf_resources.py ---


@dataclass
class StreamContext:
    """Metadata accompanying a yielded content stream."""

    page_num: int
    depth: int
    kind: str  # "page" | "form" | "pattern" | "smask" | "annotation"
    resources: Any | None  # this stream's own /Resources dict, if any
    # owner_resources/owner_key together identify the single dict entry
    # that references this stream, so callers can rewrite it to point at
    # a private copy (e.g. trim._process_page's unsharing) or build a
    # breadcrumb path (e.g. dump_streams). Populated per kind as follows:
    #   "form":    owner_resources = the /Resources dict holding /XObject;
    #              owner_key = the /XObject entry name (e.g. "/Fm0")
    #   "pattern": owner_resources = the /Resources dict holding /Pattern;
    #              owner_key = the /Pattern entry name (e.g. "/P1")
    #   "smask":   owner_key = the /ExtGState entry name that references
    #              this group via /SMask /G (e.g. "/GS1"); owner_resources
    #              not populated, since the group itself has no direct
    #              /Resources-keyed entry
    #   "page", "annotation": not populated -- annotations use
    #              annot_index/ap_key/ap_state below instead, since they
    #              have no name under a resources dict
    owner_resources: Any | None = None
    owner_key: str | None = None
    # Populated ONLY for kind=="annotation": position of the annotation
    # in the page's /Annots array, which /AP key it came from ("/N",
    # "/D", or "/R"), and -- if /AP's entry was a state sub-dictionary
    # rather than a direct stream -- the state key (e.g. "/On"), else None.
    annot_index: int | None = None
    ap_key: str | None = None
    ap_state: str | None = None


def walk_content_streams(
    pdf: "pikepdf.Pdf", page_indices: list[int] | None = None, yield_duplicates: bool = False
) -> Generator[tuple[Any, StreamContext], None, None]:
    """
    Canonical recursive walker over every content stream that can paint
    visible content: page /Contents, Form XObjects, tiling Patterns,
    ExtGState /SMask /G groups, and Annotation /AP appearance streams.

    Yields (stream_obj, StreamContext) pairs. Does NOT parse or mutate the
    stream bytes itself — callers own that. Dedup is per-page (a shared
    XObject referenced from multiple pages is yielded once per page it's
    reachable from, since page-scoped callers like `trim` need per-page
    page_num context; pass a single-page page_indices list if you need
    strict document-wide dedup instead).

    Caveat: if page.Contents is an array (multiple streams), it is yielded
    as-is (kind="page"). Callers that need a single coalesced stream should
    call `pikepdf.Page(page).contents_coalesce()` before walking that page.

    `yield_duplicates`: when False (default), each unique stream is yielded
    once per page regardless of how many resource names point at it -- the
    behavior all five existing callers rely on. When True, every named
    reference to a stream is yielded (needed by dump_streams to produce an
    entry, or an alias stub, for each occurrence), but a stream is only
    *descended into* the first time it's encountered -- cycle prevention
    stays unconditional either way.
    """

    target_pages = page_indices if page_indices else range(1, len(pdf.pages) + 1)

    for page_num in target_pages:
        try:
            page = pdf.pages[page_num - 1]
        except IndexError:
            logger.warning("walk_content_streams: page %d out of range", page_num)
            continue

        visited: set[tuple] = set()
        page_res = get_resources(page)

        if "/Contents" in page:
            ctx = StreamContext(page_num=page_num, depth=0, kind="page", resources=page_res)
            yield page.Contents, ctx

        if page_res is not None:
            yield from _walk_resources_streams(page_res, page_num, 1, visited, yield_duplicates)

        yield from _walk_annotation_streams(page, page_num, visited, yield_duplicates)


def _walk_resources_streams(
    resources: Any, page_num: int, depth: int, visited: set[tuple], yield_duplicates: bool = False
) -> Generator[tuple[Any, StreamContext], None, None]:
    yield from _walk_xobject_forms(resources, page_num, depth, visited, yield_duplicates)
    yield from _walk_tiling_patterns(resources, page_num, depth, visited, yield_duplicates)
    yield from _walk_extgstate_smasks(resources, page_num, depth, visited, yield_duplicates)


def _walk_xobject_forms(resources, page_num, depth, visited, yield_duplicates=False):
    import pikepdf

    if "/XObject" not in resources:
        return
    for name, xobj in resources.XObject.items():
        if not isinstance(xobj, pikepdf.Object):
            continue
        if xobj.get("/Subtype") != "/Form":
            visited.add(xobj.objgen)
            continue

        already_visited = xobj.objgen in visited
        if already_visited and not yield_duplicates:
            continue

        xobj_res = get_resources(xobj)
        ctx = StreamContext(
            page_num=page_num,
            depth=depth,
            kind="form",
            resources=xobj_res,
            owner_resources=resources,
            owner_key=str(name),
        )
        yield xobj, ctx

        if not already_visited:
            visited.add(xobj.objgen)
            if xobj_res is not None:
                yield from _walk_resources_streams(
                    xobj_res, page_num, depth + 1, visited, yield_duplicates
                )


def _walk_tiling_patterns(resources, page_num, depth, visited, yield_duplicates=False):
    import pikepdf

    if "/Pattern" not in resources:
        return
    for name, pat in resources.Pattern.items():
        if not isinstance(pat, pikepdf.Object):
            continue
        try:
            if int(pat.get("/PatternType", 0)) != 1:
                visited.add(pat.objgen)
                continue
        except (TypeError, ValueError):
            continue

        already_visited = pat.objgen in visited
        if already_visited and not yield_duplicates:
            continue

        pat_res = get_resources(pat)
        ctx = StreamContext(
            page_num=page_num,
            depth=depth,
            kind="pattern",
            resources=pat_res,
            owner_resources=resources,
            owner_key=str(name),
        )
        yield pat, ctx

        if not already_visited:
            visited.add(pat.objgen)
            if pat_res is not None:
                yield from _walk_resources_streams(
                    pat_res, page_num, depth + 1, visited, yield_duplicates
                )


def _walk_extgstate_smasks(resources, page_num, depth, visited, yield_duplicates=False):
    import pikepdf

    if "/ExtGState" not in resources:
        return
    for gs_key, gs in resources.ExtGState.items():
        if not isinstance(gs, pikepdf.Object):
            continue
        yield from _walk_one_extgstate_smask(
            gs, page_num, depth, visited, str(gs_key), yield_duplicates
        )


def _walk_one_extgstate_smask(gs, page_num, depth, visited, gs_key, yield_duplicates=False):
    import pikepdf

    with suppress(TypeError, KeyError, AttributeError, pikepdf.PdfError):
        group = _get_smask_group(gs)
        if group is None:
            return

        already_visited = group.objgen in visited
        if already_visited and not yield_duplicates:
            return

        group_res = get_resources(group)
        ctx = StreamContext(
            page_num=page_num, depth=depth, kind="smask", resources=group_res, owner_key=gs_key
        )
        yield group, ctx

        if not already_visited:
            visited.add(group.objgen)
            if group_res is not None:
                yield from _walk_resources_streams(
                    group_res, page_num, depth + 1, visited, yield_duplicates
                )


def _get_smask_group(gs):
    """Resolve gs's /SMask /G group, or None if absent/None/malformed.

    Renamed from _resolve_smask_group -- no longer takes `visited`, since
    dedup is now handled entirely by the caller via `already_visited`."""
    import pikepdf

    smask = gs.get("/SMask")
    if not isinstance(smask, pikepdf.Object) or smask == pikepdf.Name("/None"):
        return None
    group = smask.get("/G")
    if not isinstance(group, pikepdf.Object):
        return None
    return group


def _walk_annotation_streams(page, page_num, visited, yield_duplicates=False):
    import pikepdf

    if "/Annots" not in page:
        return
    with suppress(TypeError, KeyError, AttributeError, ValueError, pikepdf.PdfError):
        for annot_index, annot in enumerate(page.Annots):
            if not isinstance(annot, pikepdf.Object) or "/AP" not in annot:
                continue
            yield from _walk_one_annot(annot, page_num, visited, annot_index, yield_duplicates)


def _walk_one_annot(annot, page_num, visited, annot_index, yield_duplicates=False):
    ap = annot.AP
    for ap_key in ("/N", "/D", "/R"):
        if ap_key not in ap:
            continue
        yield from _walk_ap_entry_streams(
            ap[ap_key], page_num, visited, annot_index, ap_key, yield_duplicates
        )


def _walk_ap_entry_streams(
    ap_entry, page_num, visited, annot_index, ap_key, yield_duplicates=False
):
    for ap_state, stream_obj in _iter_appearance_streams(ap_entry):
        already_visited = stream_obj.objgen in visited
        if already_visited and not yield_duplicates:
            continue

        stream_res = get_resources(stream_obj)
        ctx = StreamContext(
            page_num=page_num,
            depth=1,
            kind="annotation",
            resources=stream_res,
            annot_index=annot_index,
            ap_key=ap_key,
            ap_state=ap_state,
        )
        yield stream_obj, ctx

        if not already_visited:
            visited.add(stream_obj.objgen)
            if stream_res is not None:
                yield from _walk_resources_streams(
                    stream_res, page_num, 2, visited, yield_duplicates
                )


def walk_content_streams_deduped(
    pdf: "pikepdf.Pdf",
    page_indices: list[int] | None,
    seen: set[tuple[int, int]],
) -> Generator[tuple[Any, StreamContext], None, None]:
    """
    Thin filter over walk_content_streams for callers that need dedup across
    repeated calls (e.g. once per page), rather than the per-call dedup
    walk_content_streams already does on its own.

    `seen` is owned and persisted by the caller (e.g. a `_processed` set
    living on a per-document replacer instance across apply() calls). Any
    stream whose objgen is already in `seen` is skipped and never yielded;
    every other stream's objgen is added to `seen` before it's yielded.

    Not suitable for excise_stream.py's Form handling, which needs
    private-copy-on-mutation semantics for shared Forms rather than dedup.
    """
    for stream_obj, ctx in walk_content_streams(pdf, page_indices):
        objgen = stream_obj.objgen
        if objgen in seen:
            continue
        seen.add(objgen)
        yield stream_obj, ctx
