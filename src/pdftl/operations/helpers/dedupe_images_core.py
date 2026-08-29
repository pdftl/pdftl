# src/pdftl/operations/helpers/dedupe_images_core.py
"""Core merge logic for deduplicate_images: find image XObject streams
that are equivalent to each other and rewrite every reference to point
at a single shared copy.

Ported from qpdf's Streams::deduplicateImageXobjects (qpdf PR #1663).
Generic sort/compare/rewrite mechanics live in pdftl.utils.stream_dedup;
this module only supplies "what counts as an image candidate".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdftl.utils.stream_dedup import apply_replacements, build_replacement_map, stream_length

if TYPE_CHECKING:
    import pikepdf


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
    candidates.sort(key=stream_length)
    replacements, bytes_saved = build_replacement_map(candidates, threshold)

    if not replacements:
        return {"merged": 0, "bytes_saved": 0}

    apply_replacements(pdf, replacements)
    return {"merged": len(replacements), "bytes_saved": bytes_saved}
