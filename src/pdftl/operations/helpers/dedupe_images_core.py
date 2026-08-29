# src/pdftl/operations/helpers/dedupe_images_core.py
"""Core merge logic for deduplicate_images: find image XObject streams
that are equivalent to each other and rewrite every reference to point
at a single shared copy.

Ported from qpdf's Streams::deduplicateImageXobjects (qpdf PR #1663).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdftl.utils.object_equivalence import check_object_equivalence

if TYPE_CHECKING:
    # Import-time only (for type hints); see the lazy `import pikepdf`
    # inside each function below for why this isn't a top-level import.
    import pikepdf


def _stream_length(obj: pikepdf.Object) -> int:
    """The stream's declared /Length, falling back to the actual raw
    byte count for a malformed stream missing that (required) key."""
    try:
        return int(obj.get("/Length"))
    except (ValueError, AttributeError, TypeError):
        return len(obj.read_raw_bytes())


def _find_image_candidates(pdf: pikepdf.Pdf) -> list[pikepdf.Object]:
    """Note: `pdf.objects` has been observed (installed pikepdf 10.5.1)
    to yield more than one Python wrapper for the same underlying object
    -- same `.objgen`, distinct `id()` -- so results here are deduped by
    objgen. Without this, an image could be compared against itself as
    if it were a second copy, corrupting the merge count."""
    import pikepdf
    from pikepdf import Name

    seen = set()
    candidates = []
    for obj in pdf.objects:
        if not isinstance(obj, pikepdf.Stream):
            continue
        if obj.get("/Subtype") != Name("/Image"):
            continue
        og = obj.objgen
        if og in seen:
            continue
        seen.add(og)
        candidates.append(obj)
    return candidates


def _rewrite_indirect_ref_or_recurse(container, key_or_idx, val, replacements) -> None:
    """Shared step for one dict-value or array-element slot: swap it for
    its replacement if it's an indirect reference we're merging away,
    otherwise recurse into it if it's a direct nested container."""
    import pikepdf

    if isinstance(val, pikepdf.Object) and val.is_indirect:
        og = val.objgen
        if og in replacements:
            container[key_or_idx] = replacements[og]
    elif isinstance(val, (pikepdf.Dictionary, pikepdf.Stream, pikepdf.Array)):
        _rewrite_references(val, replacements)


def _rewrite_references(node: pikepdf.Object, replacements: dict) -> None:
    """Replace any indirect reference in `node` matching a key in
    `replacements` with its mapped master object. Recurses into direct
    (non-indirect) nested dictionaries/arrays only -- an indirect
    sub-object is visited separately, at the top level, since the
    caller walks every indirect object in the document."""
    import pikepdf

    if isinstance(node, (pikepdf.Dictionary, pikepdf.Stream)):
        for key in list(node.keys()):
            _rewrite_indirect_ref_or_recurse(node, key, node[key], replacements)
    elif isinstance(node, pikepdf.Array):
        for idx in range(len(node)):
            _rewrite_indirect_ref_or_recurse(node, idx, node[idx], replacements)


def _find_duplicates_of(master, master_len, remaining_candidates, replacements) -> int:
    """Scan `remaining_candidates` (already sorted ascending by length,
    all with index greater than master's) for streams equivalent to
    `master`, recording each as a replacement. Returns bytes reclaimed."""
    bytes_saved = 0
    for candidate in remaining_candidates:
        cand_len = _stream_length(candidate)
        if cand_len != master_len:
            break  # sorted ascending -- no further candidate can match
        cand_og = candidate.objgen
        if cand_og in replacements:
            continue
        if check_object_equivalence(candidate, master):
            replacements[cand_og] = master
            # cand_len == master_len here (guaranteed by the sorted
            # same-length scan above), so either would do.
            bytes_saved += cand_len
    return bytes_saved


def _build_replacement_map(candidates: list, threshold: int) -> tuple[dict, int]:
    """Walk the length-sorted candidate list once, grouping equivalent
    streams and choosing the first (smallest, or tied-smallest) of each
    group as the surviving master. Returns (replacements, bytes_saved)."""
    replacements: dict[tuple, pikepdf.Object] = {}
    bytes_saved = 0
    for i, master in enumerate(candidates):
        master_len = _stream_length(master)
        if master_len < threshold:
            continue
        if master.objgen in replacements:
            continue  # already folded into an earlier master itself
        bytes_saved += _find_duplicates_of(master, master_len, candidates[i + 1 :], replacements)
    return replacements, bytes_saved


def _apply_replacements(pdf: pikepdf.Pdf, replacements: dict) -> None:
    """Walk every indirect object in the document once, rewriting any
    reference that matches a key in `replacements`."""
    import pikepdf

    seen = set()
    for obj in pdf.objects:
        if not isinstance(obj, pikepdf.Object):
            continue  # e.g. a bare indirect scalar has no .objgen to walk
        og = obj.objgen
        if og in seen:
            continue
        seen.add(og)
        _rewrite_references(obj, replacements)


def deduplicate_image_xobjects(pdf: pikepdf.Pdf, threshold: int = 0) -> dict:
    """Merge image XObject streams with equivalent content into a single
    shared object, rewriting every reference in the document to point
    at one copy. Modifies `pdf` in place.

    `threshold`: images with a declared /Length below this many bytes
    are never merged -- comparing (and later, saving) very small images
    isn't worth the overhead. Default 0 merges everything.

    Returns a report dict:
    ``{"merged": <count of streams merged away>, "bytes_saved": <int>}``.
    ``bytes_saved`` sums each merged-away duplicate's stream ``/Length``
    (i.e. its raw, already-filtered/compressed size -- the size it was
    actually occupying in the file). This is a lower bound on the actual
    file-size reduction: the duplicate object itself isn't fully
    reclaimed until qpdf's/pikepdf's own garbage collection runs at
    save time, but every byte counted here really was duplicate stream
    payload no longer referenced by anything after this call.
    """
    candidates = _find_image_candidates(pdf)
    candidates.sort(key=_stream_length)
    replacements, bytes_saved = _build_replacement_map(candidates, threshold)

    if not replacements:
        return {"merged": 0, "bytes_saved": 0}

    _apply_replacements(pdf, replacements)
    return {"merged": len(replacements), "bytes_saved": bytes_saved}
