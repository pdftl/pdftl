# src/pdftl/operations/helpers/dedupe_xobjects_core.py
"""Core merge logic for deduplicate_xobjects: merge equivalent Form XObjects
that are only ever drawn with `Do` from page content or from other such
forms.

A form is a candidate only if every reference to it is an entry of a
/Resources /XObject dictionary, and every owner of those resources is a
page, a /Pages node or another candidate. The one exception is the
appearance of a Link annotation held in the annotation's own /AP
dictionary: links carry no user content, so no viewer rewrites their
appearance. Other annotation appearances (widgets especially, which a
viewer redraws when a field changes), soft masks, pattern cells, Type3
glyphs and structure-tree object references are never merged. Forms with
/StructParent or /StructParents are skipped as well.

Equivalence covers the whole stream dictionary, /Resources included, and
the raw stream bytes.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pdftl.utils.stream_dedup import (
    XOBJECT_IGNORED_KEYS,
    apply_replacements,
    build_replacement_map,
    stream_length,
)

if TYPE_CHECKING:
    import pikepdf

_EQUIVALENCE_DEPTH = 32
_EXCLUDED_KEYS = ("/StructParent", "/StructParents")
_PAGE_TYPES = ("/Page", "/Pages")


@dataclass
class _Graph:
    forms: dict = field(default_factory=dict)  # objgen -> form stream
    refs: dict = field(default_factory=lambda: defaultdict(set))  # form objgen -> slots
    owners: dict = field(default_factory=lambda: defaultdict(set))  # xobject slot -> owners
    kinds: dict = field(default_factory=dict)  # owner -> "page" | "form" | "other"
    link_slots: set = field(default_factory=set)  # appearance slots of Link annotations


def _is_form(obj) -> bool:
    import pikepdf

    return isinstance(obj, pikepdf.Stream) and obj.get("/Subtype") == "/Form"


def _here(value, holder, path):
    """Where a value lives: its own object if indirect, else its slot in `holder`."""
    return (value.objgen, ()) if value.is_indirect else (holder, path)


def _owner_kind(node, path) -> str:
    if path != ():
        return "other"
    if _is_form(node):
        return "form"
    return "page" if node.get("/Type") in _PAGE_TYPES else "other"


def _add_owner(graph: _Graph, node, holder, path) -> None:
    from pikepdf import Dictionary

    owner = (holder, path)
    graph.kinds[owner] = _owner_kind(node, path)
    resources = node["/Resources"]
    if not isinstance(resources, Dictionary):
        return
    r_holder, r_path = _here(resources, holder, path + ("/Resources",))
    xobjects = resources.get("/XObject")
    if not isinstance(xobjects, Dictionary):
        return
    x_holder, x_path = _here(xobjects, r_holder, r_path + ("/XObject",))
    for name in xobjects.keys():
        graph.owners[(x_holder, x_path + (name,))].add(owner)


def _add_link_slots(graph: _Graph, annot, holder, path) -> None:
    import pikepdf

    appearances = annot.get("/AP")
    if not isinstance(appearances, pikepdf.Dictionary) or appearances.is_indirect:
        return  # an indirect /AP dictionary might be shared with another annotation
    for key in appearances.keys():
        value = appearances[key]
        slot = path + ("/AP", key)
        if isinstance(value, pikepdf.Dictionary) and not value.is_indirect:
            graph.link_slots.update((holder, slot + (state,)) for state in value.keys())
        else:
            graph.link_slots.add((holder, slot))


def _walk(graph: _Graph, node, holder, path) -> None:
    import pikepdf

    if isinstance(node, (pikepdf.Dictionary, pikepdf.Stream)):
        if "/Resources" in node:
            _add_owner(graph, node, holder, path)
        if node.get("/Subtype") == "/Link":
            _add_link_slots(graph, node, holder, path)
        items = ((key, node[key]) for key in node.keys())
    elif isinstance(node, pikepdf.Array):
        items = enumerate(node)
    else:
        return
    for key, value in items:
        if not isinstance(value, pikepdf.Object):
            continue
        if value.is_indirect:
            if _is_form(value):
                graph.refs[value.objgen].add((holder, path + (key,)))
        else:
            _walk(graph, value, holder, path + (key,))


def _build_graph(pdf: pikepdf.Pdf) -> _Graph:
    import pikepdf

    graph = _Graph()
    seen = set()
    for obj in pdf.objects:
        if not isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream, pikepdf.Array)):
            continue
        og = obj.objgen
        if og in seen:
            continue
        seen.add(og)
        if _is_form(obj):
            graph.forms[og] = obj
        _walk(graph, obj, og, ())
    _walk(graph, pdf.trailer, "trailer", ())
    return graph


def _drawn_only(graph: _Graph) -> list:
    """Forms referenced only from XObject resources of pages or of other such forms,
    or as Link appearances."""
    pending = {
        og: slots
        for og, slots in graph.refs.items()
        if og in graph.forms
        and not any(k in graph.forms[og] for k in _EXCLUDED_KEYS)
        and all(slot in graph.owners or slot in graph.link_slots for slot in slots)
    }
    eligible: set = set()
    changed = True
    while changed:
        changed = False
        for og, slots in list(pending.items()):
            owners = set().union(*(graph.owners.get(slot, ()) for slot in slots))
            if all(
                graph.kinds[o] == "page" or (graph.kinds[o] == "form" and o[0] in eligible)
                for o in owners
            ):
                eligible.add(og)
                del pending[og]
                changed = True
    return [graph.forms[og] for og in eligible]


def deduplicate_form_xobjects(pdf: pikepdf.Pdf, threshold: int = 0) -> dict:
    """Merge equivalent drawn-only Form XObjects into one shared object each,
    rewriting every reference to point at it. Modifies `pdf` in place.

    Forms whose raw stream is shorter than `threshold` bytes are left alone.
    Returns ``{"merged": <count merged away>, "bytes_saved": <raw stream bytes>}``.
    """
    candidates = _drawn_only(_build_graph(pdf))
    candidates.sort(key=lambda form: (stream_length(form), form.objgen))
    replacements, bytes_saved = build_replacement_map(
        candidates, threshold, depth=_EQUIVALENCE_DEPTH, ignore_keys=XOBJECT_IGNORED_KEYS
    )
    if not replacements:
        return {"merged": 0, "bytes_saved": 0}
    apply_replacements(pdf, replacements)
    return {"merged": len(replacements), "bytes_saved": bytes_saved}
