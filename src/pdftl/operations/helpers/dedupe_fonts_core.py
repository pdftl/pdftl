# src/pdftl/operations/helpers/dedupe_fonts_core.py
"""Core merge logic for deduplicate_fonts: find embedded font-program
streams (/FontFile, /FontFile2, /FontFile3) that are equivalent to each
other and rewrite every reference to point at a single shared copy.

Scope is deliberately narrow -- only the font PROGRAM stream itself is
compared and merged. The surrounding /FontDescriptor and /Font
dictionaries are never touched or compared: two fonts can share an
identical embedded program while differing in /Encoding, /Widths,
/BaseFont, or /FontDescriptor metadata, and none of that is safe to
collapse. check_object_equivalence naturally enforces this boundary
for free: it only ever sees the FontFile stream object itself (dict +
raw bytes), so e.g. a /FontFile (Type1, carrying /Length1../3) can
never spuriously match a /FontFile2 (TrueType, carrying only /Length1)
even with byte-identical program data, because their own stream dicts
have different key sets.

Reuses the generic replacement-map-building and reference-rewriting
machinery from dedupe_images_core -- that logic has nothing image-
specific in it, only "here are some candidate streams, merge the
equivalent ones and rewrite every reference in the document".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdftl.utils.stream_dedup import apply_replacements, build_replacement_map, stream_length

if TYPE_CHECKING:
    import pikepdf

FONT_FILE_KEYS = ("/FontFile", "/FontFile2", "/FontFile3")


def _find_font_file_candidates(pdf: pikepdf.Pdf) -> list[pikepdf.Object]:
    """Every embedded font-program stream reachable from a /Font
    object's /FontDescriptor, deduped by objgen -- a /FontDescriptor
    (and therefore its FontFile) can legitimately already be shared by
    more than one /Font dict, which must not be double-counted as two
    separate candidates."""
    import pikepdf
    from pikepdf import Dictionary, Name

    seen = set()
    candidates = []
    for obj in pdf.objects:
        if not isinstance(obj, Dictionary):
            continue
        if obj.get("/Type") != Name("/Font"):
            continue
        descriptor = obj.get("/FontDescriptor")
        if not isinstance(descriptor, Dictionary):
            continue  # absent (non-embedded standard font) or malformed
        for key in FONT_FILE_KEYS:
            stream = descriptor.get(key)
            if stream is None:
                continue
            if not isinstance(stream, pikepdf.Stream):
                continue  # malformed: key present but not actually a stream
            og = stream.objgen
            if og in seen:
                continue
            seen.add(og)
            candidates.append(stream)
    return candidates


def deduplicate_font_files(pdf: pikepdf.Pdf, threshold: int = 0) -> dict:
    """Merge embedded font-program streams with equivalent content into
    a single shared object, rewriting every /FontFile, /FontFile2, and
    /FontFile3 reference in the document to point at one copy. Modifies
    `pdf` in place.

    `threshold`: font programs with a declared /Length below this many
    bytes are never merged. Default 0 merges everything.

    Returns a report dict: ``{"merged": <count>, "bytes_saved": <int>}``
    -- see deduplicate_image_xobjects's docstring for the bytes_saved
    caveat re: qpdf/pikepdf's own garbage collection at save time.
    """
    candidates = _find_font_file_candidates(pdf)
    candidates.sort(key=stream_length)
    replacements, bytes_saved = build_replacement_map(candidates, threshold)

    if not replacements:
        return {"merged": 0, "bytes_saved": 0}

    apply_replacements(pdf, replacements)
    return {"merged": len(replacements), "bytes_saved": bytes_saved}


# --- append to dedupe_fonts_core.py ---

from pdftl.utils.object_equivalence import check_object_equivalence


def _find_font_dict_candidates(pdf: pikepdf.Pdf) -> list[pikepdf.Object]:
    """Every indirect /Font dictionary in the document, deduped by
    objgen. Unlike _find_font_file_candidates -- which walks from
    /FontDescriptor and can genuinely reach the same stream twice via
    two different /Font dicts -- this iterates pdf.objects directly,
    where pikepdf already yields each indirect object exactly once, so
    no dedup pass is needed here. Kept as a plain list build; a `seen`
    guard was tried and removed after coverage showed it unreachable.
    /FontDescriptor -- it looks at /Font dicts themselves, since a
    whole-/Font merge is a different (broader-scope) operation than a
    FontFile-only merge."""
    from pikepdf import Dictionary, Name

    candidates = []
    for obj in pdf.objects:
        if not isinstance(obj, Dictionary):
            continue
        if obj.get("/Type") != Name("/Font"):
            continue
        candidates.append(obj)
    return candidates


def _font_dict_bucket_key(font_obj) -> tuple:
    """A cheap pre-filter to avoid O(n^2) full comparisons across
    obviously-distinct fonts in a large document. Two equivalent /Font
    dicts must carry the identical key set (check_object_equivalence's
    dict rule), so a differing key count rules out equivalence for
    free; BaseFont is an even cheaper, near-universal discriminator on
    top of that."""
    base_font = font_obj.get("/BaseFont")
    return (len(font_obj.keys()), str(base_font) if base_font is not None else None)


def _build_font_dict_replacement_map(candidates: list) -> dict:
    """Full pairwise check_object_equivalence within each bucket only
    -- not a sorted-length scan like stream_dedup's build_replacement_map,
    since /Font dicts have no natural "length" to sort by."""
    from collections import defaultdict

    buckets: dict[tuple, list] = defaultdict(list)
    for obj in candidates:
        buckets[_font_dict_bucket_key(obj)].append(obj)

    replacements: dict = {}
    for bucket in buckets.values():
        for i, master in enumerate(bucket):
            if master.objgen in replacements:
                continue
            for candidate in bucket[i + 1 :]:
                cand_og = candidate.objgen
                if cand_og in replacements:
                    continue
                if check_object_equivalence(candidate, master):
                    replacements[cand_og] = master
    return replacements


def deduplicate_font_dicts(pdf: pikepdf.Pdf) -> dict:
    """Merges whole /Font dictionaries that are fully structurally
    equivalent (same encoding, widths, descriptor, and embedded program
    -- everything) into a single shared object. This is a STRICTER,
    bigger-payoff sibling of deduplicate_font_files: it only fires when
    literally everything about two fonts matches, but when it does, it
    collapses the whole tree (dict + descriptor + program), not just
    the program stream.

    Returns a report dict: {"merged": <count>}. No bytes_saved -- a
    /Font dict has no meaningful "/Length" of its own to sum the way a
    stream does; the real savings come from whatever streams (fonts,
    ToUnicode CMaps) end up unreferenced as a result.
    """
    candidates = _find_font_dict_candidates(pdf)
    replacements = _build_font_dict_replacement_map(candidates)
    if not replacements:
        return {"merged": 0}
    apply_replacements(pdf, replacements)
    return {"merged": len(replacements)}
