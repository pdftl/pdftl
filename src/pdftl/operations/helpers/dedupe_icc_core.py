# src/pdftl/operations/helpers/dedupe_icc_core.py
"""Core merge logic for deduplicate_icc_profiles: find embedded ICC
color-profile streams (/ICCBased) that are byte-and-dict equivalent to
each other and rewrite every reference to point at a single shared
copy.

Resources lookup uses pdf_resources.get_resources (inheritance-aware
per pikepdf_helpers.py's READ RULE), not a raw /Resources key access,
since /Resources can be inherited from an ancestor /Pages node.

Scope
-----
Only ICC profile streams reachable from a `/ColorSpace` entry are
considered -- either an image XObject's own `/ColorSpace` key, or a
named color space inside a `/Resources /ColorSpace` dictionary. Both
shapes are walked, and `/Separation` and `/DeviceN` color spaces are
recursed into (their alternate color space, at index 2 of the array,
can itself be `[/ICCBased ref]`).

Document-level output intents (`Root/OutputIntents/*/DestOutputProfile`)
are deliberately NOT included as candidates. A single document
realistically has at most one or two output intents, so there is
nothing to deduplicate *within* one file by including them -- adding
that traversal would only add complexity and a chance of touching the
wrong thing, for no realistic byte savings. If a future need arises to
merge an output intent's profile against an image's ICCBased profile
that happens to share the same bytes, that would be a deliberate,
separately-considered feature, not an accidental side effect of this
one.

Comparison is a single strict pass: whole-stream equivalence (dict +
raw bytes) via check_object_equivalence, unmodified. This means a
stream carrying an `/Alternate` fallback color space will NOT match an
otherwise-byte-identical stream lacking one -- deliberately, mirroring
the FontFile-vs-FontFile2 caution already applied to font dedup (see
dedupe_fonts_core.py). If this turns out to reject real cross-producer
duplicates in practice (same profile bytes, differing /Alternate), a
second, looser pass -- comparing /N + raw bytes only, ignoring
/Alternate -- can be added later. Not built now: unconfirmed against a
real multi-producer document, and speculative broadening was exactly
the mistake the upstream qpdf PR had to walk back from early on.

Reuses the same generic stream_dedup machinery as dedupe_images_core
and dedupe_fonts_core -- nothing here is ICC-specific beyond candidate
discovery.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdftl.utils.pdf_resources import get_resources
from pdftl.utils.stream_dedup import apply_replacements, build_replacement_map, stream_length

if TYPE_CHECKING:
    import pikepdf

_ALTERNATE_SPACE_TAGS = ("/Separation", "/DeviceN")


def _extract_icc_streams(colorspace_value, out: list, seen: set) -> None:
    """Given a single color space value (as found directly in a
    /ColorSpace entry, or as one value of a /ColorSpace dictionary),
    collect any /ICCBased stream it directly is or contains (recursing
    into /Separation and /DeviceN's alternate space at index 2).
    Silently ignores anything else (/DeviceGray, /DeviceRGB,
    /DeviceCMYK, /Indexed, /Pattern, /CalRGB, /CalGray, /Lab, ...) --
    none of those wrap an ICC profile stream, and treating an
    unrecognized shape as an error would make this fragile against
    perfectly valid PDFs using color space types we don't specifically
    care about."""
    import pikepdf
    from pikepdf import Array, Name

    if not isinstance(colorspace_value, Array) or len(colorspace_value) == 0:
        return

    tag = colorspace_value[0]

    if tag == Name("/ICCBased"):
        if len(colorspace_value) < 2:
            return  # malformed: tag present but no stream reference
        stream = colorspace_value[1]
        if not isinstance(stream, pikepdf.Stream):
            return  # malformed: second element isn't actually a stream
        og = stream.objgen
        if og in seen:
            return
        seen.add(og)
        out.append(stream)
        return

    if tag in (Name(t) for t in _ALTERNATE_SPACE_TAGS) and len(colorspace_value) >= 3:
        # /Separation: [/Separation name altSpace tintTransform]
        # /DeviceN:    [/DeviceN names altSpace tintTransform ...]
        # Both carry the alternate color space at index 2.
        _extract_icc_streams(colorspace_value[2], out, seen)


def _collect_from_container(container, candidates: list, seen: set) -> None:
    """Inspect one object's own /ColorSpace entry (if present) and
    collect any ICC profile streams it leads to. Handles both shapes
    /ColorSpace can take: an image XObject's own /ColorSpace key is a
    color space value directly; a /Resources dictionary's /ColorSpace
    key is itself a dictionary mapping arbitrary names to color space
    values, each of which must be inspected in turn.

    A page or form XObject's /Resources dictionary is very often an
    INLINE (non-indirect) dict -- it never appears as its own entry
    when iterating pdf.objects, so it must be checked explicitly here
    rather than relying on the top-level walk to visit it separately.
    Resources lookup goes through get_resources() rather than a raw
    `.get("/Resources")`, since a Page's /Resources can be inherited
    from an ancestor /Pages node (see module docstring)."""
    from pikepdf import Dictionary

    _collect_colorspace_entry(container.get("/ColorSpace"), candidates, seen)

    resources = get_resources(container)
    if isinstance(resources, Dictionary):
        _collect_colorspace_entry(resources.get("/ColorSpace"), candidates, seen)


def _collect_colorspace_entry(cs, candidates: list, seen: set) -> None:
    """Handles a single /ColorSpace value, which is either a bare
    color space (an image XObject's own /ColorSpace key) or a
    dictionary of named color spaces (a /Resources /ColorSpace key)."""
    from pikepdf import Dictionary

    if cs is None:
        return
    if isinstance(cs, Dictionary):
        for value in cs.values():
            _extract_icc_streams(value, candidates, seen)
    else:
        _extract_icc_streams(cs, candidates, seen)


def _find_icc_candidates(pdf: pikepdf.Pdf) -> list[pikepdf.Object]:
    """Every ICC profile stream reachable from a /ColorSpace entry
    anywhere in the document, deduped by objgen -- the same profile is
    commonly referenced by many images/resources dicts at once and
    must not be double-counted as separate candidates."""
    import pikepdf
    from pikepdf import Dictionary

    seen: set = set()
    candidates: list = []
    for obj in pdf.objects:
        if not isinstance(obj, (Dictionary, pikepdf.Stream)):
            continue
        _collect_from_container(obj, candidates, seen)
    return candidates


def deduplicate_icc_profiles(pdf: pikepdf.Pdf, threshold: int = 0) -> dict:
    """Merge embedded ICC profile streams with equivalent content into
    a single shared object, rewriting every reference in the document
    to point at one copy. Modifies `pdf` in place.

    `threshold`: ICC profiles with a declared /Length below this many
    bytes are never merged. Default 0 merges everything.

    Returns a report dict: ``{"merged": <count>, "bytes_saved": <int>}``.
    """
    candidates = _find_icc_candidates(pdf)
    candidates.sort(key=stream_length)
    replacements, bytes_saved = build_replacement_map(candidates, threshold)

    if not replacements:
        return {"merged": 0, "bytes_saved": 0}

    apply_replacements(pdf, replacements)
    return {"merged": len(replacements), "bytes_saved": bytes_saved}
