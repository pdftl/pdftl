# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/type3_tounicode_synthesis.py

"""
Synthesizes a /ToUnicode CMap for Type3 fonts that lack one, so pdfium's
own (otherwise excellent) text-extraction pipeline resolves them
correctly instead of falling back to its `unicode == charcode` guess.

Supersedes the earlier whole-page content-stream-walking fallback (see
dump_text.py's git history): rather than reimplementing a strictly
worse general-purpose text extractor and swapping it in for any page
merely touched by Type3, this module patches the *input* pdfium sees --
attaching a synthetic /ToUnicode directly to the Type3 font object
before handing the PDF to pdfium -- so pdfium's own reading-order,
bidi, ligature, and layout handling all keep working for the whole
page, including proper interleaving with every other font on it.

Resolution priority for a Simple font (Type1/TrueType/Type3 -- never
Type0), mirroring font_encoding_tables._get_maps's chain one level up
(PDF-object level, not font-program level -- this module never opens
an embedded font binary at all):

  1. /ToUnicode, if present (pdftl.fonts.cmap_utils.parse_to_unicode_cmap).
  2. /Encoding /Differences and/or /BaseEncoding glyph names resolved
     through the Adobe Glyph List (AGL).
  3. A best-effort StandardEncoding guess if /Encoding is absent
     entirely (not spec-guaranteed correct -- see
     _resolve_via_encoding_and_agl's docstring).
"""

from __future__ import annotations

import logging
from typing import Any

from pdftl.fonts.cmap_utils import compile_to_unicode_cmap, parse_to_unicode_cmap
from pdftl.fonts.font_encoding_tables import _get_maps
from pdftl.utils.pdf_resources import get_resources

logger = logging.getLogger(__name__)


def resolve_simple_font_char_to_unicode(font_obj: Any) -> dict[int, str]:
    """
    Resolves a Simple font's (Type1/TrueType/Type3) character-code ->
    Unicode mapping: /ToUnicode if present, else /Differences and/or
    /BaseEncoding resolved through the Adobe Glyph List. Returns {} if
    neither source yields anything -- callers should treat that as
    "unresolvable", not fall back to any charcode-based guess.
    """
    result = _resolve_via_to_unicode(font_obj)
    if result:
        return result
    return _resolve_via_encoding_and_agl(font_obj)


def _resolve_via_to_unicode(font_obj: Any) -> dict[int, str]:
    if "/ToUnicode" not in font_obj:
        return {}
    try:
        cmap_bytes = font_obj["/ToUnicode"].read_bytes()
    except (AttributeError, TypeError) as e:
        logger.debug("Failed to read /ToUnicode stream: %s", e)
        return {}
    mappings = parse_to_unicode_cmap(cmap_bytes)
    result = {int(hex_code, 16): val for hex_code, val in mappings.items()}
    logger.debug("Resolved %d code(s) via /ToUnicode", len(result))
    return result


def _extract_differences_and_base_encoding(
    encoding: Any,
) -> tuple[list | None, str | None]:
    """
    Determines the (/Differences, /BaseEncoding) pair implied by a Simple
    font's /Encoding value, in whichever of its legal shapes it takes
    (ISO 32000-2 9.6.6.2): a bare Name, a Dictionary, or entirely absent.
    """
    import pikepdf

    if isinstance(encoding, pikepdf.Name):
        return None, str(encoding).lstrip("/")

    if encoding is not None and hasattr(encoding, "get"):
        differences: list | None = None
        if "/Differences" in encoding:
            try:
                differences = list(encoding["/Differences"])
            except (AttributeError, TypeError) as e:
                logger.debug("Malformed /Differences: %s", e)
        base_enc_val = encoding.get("/BaseEncoding")
        base_encoding = str(base_enc_val).lstrip("/") if base_enc_val is not None else None
        return differences, base_encoding

    if encoding is None:
        # No /Encoding object at all -- per ISO 32000-2 9.6.6.2, this
        # means "use the font program's own built-in encoding," which
        # this module can't read without opening the actual embedded
        # font (out of scope here -- see
        # pdftl.fonts.type1_to_cff.resolve_glyph_names for the module
        # that DOES do this, for the Type1-subsetting path). As a
        # best-effort heuristic only -- NOT spec-guaranteed correct --
        # default to StandardEncoding for a non-symbolic Simple font,
        # matching the same fallback convention
        # font_extraction_utils._get_fallback_encoding already applies
        # elsewhere in pdftl for this identical "no /Encoding" case.
        # A genuinely symbolic font (custom glyph names with no
        # standard meaning, e.g. a dingbats-style font) will resolve
        # incorrectly or not at all under this guess; there's no way
        # to distinguish that case from here without the font program.
        logger.debug(
            "No /Encoding object present; guessing StandardEncoding as a "
            "best-effort fallback (not spec-guaranteed for this font)."
        )
        return None, "StandardEncoding"

    return None, None


def _resolve_maps_via_agl(
    differences_map: dict[str, str], base_encoding_map: dict[int, str]
) -> dict[int, str]:
    """
    Resolves a merged set of codes (from /Differences and/or a predefined
    base encoding) to Unicode via the Adobe Glyph List, preferring a
    /Differences glyph name over the base-encoding one for the same code.
    """
    from fontTools import agl

    result: dict[int, str] = {}
    unresolved_names: list[str] = []
    codes: set[int] = set()
    if differences_map:
        codes |= {int(hc, 16) for hc in differences_map}
    if base_encoding_map:
        codes |= set(base_encoding_map.keys())

    for code in codes:
        hex_code = f"{code:02X}"
        gname = (differences_map or {}).get(hex_code) or (base_encoding_map or {}).get(code)
        if gname is None:
            continue
        unicode_str = agl.toUnicode(gname)
        if unicode_str:
            result[code] = unicode_str
        elif len(gname) == 1:
            result[code] = gname
        else:
            unresolved_names.append(gname)

    if unresolved_names:
        logger.debug(
            "Resolved %d/%d code(s) via /Encoding + AGL (unresolved glyph names: %s)",
            len(result),
            len(codes),
            unresolved_names[:10],
        )
    return result


def _resolve_via_encoding_and_agl(font_obj: Any) -> dict[int, str]:
    """
    Resolves via a Simple font's /Encoding, in either of its two legal
    shapes (ISO 32000-2 9.6.6.2): a bare Name (a predefined base
    encoding, e.g. /WinAnsiEncoding, with no /Differences at all -- the
    common case for a plain embedded Type1/TrueType text font with no
    /ToUnicode), or a Dictionary carrying /BaseEncoding and/or
    /Differences. Delegates the actual code->glyph-name resolution to
    font_encoding_tables._get_maps, the same table-construction helper
    the sfnt/CFF/Type1 metric-sync paths already use, so this doesn't
    duplicate that priority logic a second time.
    """
    encoding = font_obj.get("/Encoding")
    differences, base_encoding = _extract_differences_and_base_encoding(encoding)

    if differences is None and base_encoding is None:
        logger.debug("No /ToUnicode and no usable /Encoding; nothing to resolve.")
        return {}

    differences_map, base_encoding_map = _get_maps(differences, base_encoding)
    return _resolve_maps_via_agl(differences_map, base_encoding_map)


def build_synthetic_to_unicode_map(font_obj: Any) -> dict[int, str]:
    """
    Wraps resolve_simple_font_char_to_unicode, kept as its own entry
    point in case Type3-specific quirks emerge later that shouldn't
    apply to the general Simple-font case.
    """
    return resolve_simple_font_char_to_unicode(font_obj)


_MAX_XOBJECT_RECURSION_DEPTH = 12


def _collect_fonts_from_resources(
    resources: Any,
    seen_objgens: set[Any],
    seen_xobject_objgens: set[Any],
    fonts: list[Any],
    depth: int,
) -> None:
    """
    Collects font objects reachable from a single /Resources dict --
    its own /Font entries, plus (recursively) any Form XObject's own
    /Resources/Font. A Type3 font confined entirely to a nested Form
    XObject (e.g. a generated chart) is just as much a candidate for
    patching as one used directly on a page -- pdfium mishandles it the
    same way either way, so this must recurse into /XObject, not just
    look at the page's own /Font dict. A Form XObject with no /Resources
    of its own inherits the invoking scope's resources (ISO 32000-2
    8.10.2), so `resources` (not the XObject's own, possibly-absent
    dict) is passed down as the fallback.

    `seen_xobject_objgens` guards against a self-referencing or
    mutually-recursive XObject graph looping forever; `depth` is a hard
    backstop on top of that guard. Dedup uses pikepdf's `objgen`, not
    Python `id()`: repeated attribute access on the same underlying PDF
    object can hand back distinct wrapper instances with different
    `id()`s, so `id()` is not a reliable identity check here.
    """
    if resources is None:
        return

    if "/Font" in resources:
        for font_obj in resources.Font.values():
            objgen = font_obj.objgen
            if objgen in seen_objgens:
                continue
            seen_objgens.add(objgen)
            fonts.append(font_obj)

    if depth >= _MAX_XOBJECT_RECURSION_DEPTH or "/XObject" not in resources:
        return

    for xobj in resources.XObject.values():
        if str(xobj.get("/Subtype", "")) != "/Form":
            continue
        xobj_objgen = xobj.objgen
        if xobj_objgen in seen_xobject_objgens:
            continue
        seen_xobject_objgens.add(xobj_objgen)
        xobj_resources = xobj.get("/Resources", resources)
        _collect_fonts_from_resources(
            xobj_resources, seen_objgens, seen_xobject_objgens, fonts, depth + 1
        )


def _iter_unique_font_objects(pdf_pike: Any) -> list[Any]:
    """
    Walks every page's /Resources/Font, and recursively every Form
    XObject's own /Resources/Font reachable from that page, returning
    each distinct font object once (deduped by pikepdf objgen), since
    the same font object can be shared across many pages -- or between
    a page and a nested XObject -- via the object graph and should only
    be patched once.
    """
    seen_objgens: set[Any] = set()
    seen_xobject_objgens: set[Any] = set()
    fonts: list[Any] = []
    for page in pdf_pike.pages:
        _collect_fonts_from_resources(
            get_resources(page), seen_objgens, seen_xobject_objgens, fonts, depth=0
        )
    return fonts


def patch_missing_to_unicode(pdf_pike: Any) -> int:
    """
    Walks pdf_pike's font objects (deduped by objgen), and for every
    Type3 font missing /ToUnicode, builds and attaches a synthetic one
    via compile_to_unicode_cmap. A font that already has /ToUnicode is
    left untouched -- overwriting an existing (possibly more
    authoritative) /ToUnicode would be a regression, not a fix.

    Mutates `pdf_pike` in place. Callers must treat this as an
    in-memory-only transformation and never persist the mutated copy
    back to the user's actual output file.

    Returns the count of fonts patched, for logging/visibility.
    """
    patched_count = 0
    for font_obj in _iter_unique_font_objects(pdf_pike):
        if str(font_obj.get("/Subtype", "")) != "/Type3":
            continue
        if "/ToUnicode" in font_obj:
            logger.debug("Type3 font already has /ToUnicode; skipping.")
            continue

        char_map = build_synthetic_to_unicode_map(font_obj)
        if not char_map:
            logger.debug("Type3 font: no code could be resolved; leaving /ToUnicode unset.")
            continue

        hex_map = {f"{code:02X}": ch for code, ch in char_map.items()}
        cmap_bytes = compile_to_unicode_cmap(hex_map)
        if not cmap_bytes:
            continue

        font_obj["/ToUnicode"] = pdf_pike.make_stream(cmap_bytes)
        patched_count += 1
        # logger.debug("Patched synthetic /ToUnicode onto Type3 font (%d code(s)).", len(char_map))

    logger.debug("patch_missing_to_unicode: patched %d font(s).", patched_count)
    return patched_count
