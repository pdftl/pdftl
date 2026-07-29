# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/widths_utils.py

"""
Utilities for extracting, normalizing, and compressing PDF font width metrics.

Handles both Simple Fonts (/Widths) and Composite/Type0 Fonts (/W) with
flexible formats and automatic chunk compression back to compliant /W arrays.

Also handles the /CIDToGIDMap stream carried by CIDFontType2 descendant
fonts, which maps 16-bit CIDs to the TrueType GIDs actually present in the
embedded (or system) font program. This is a distinct table from /W: /W
describes CID->width, while /CIDToGIDMap describes CID->GID. The two must
never be conflated when compressing or rewriting either table, since a
contiguous run of equal widths in /W says nothing about whether the
corresponding GIDs are themselves contiguous.
"""

from __future__ import annotations

import logging
from pdftl.fonts.standard14_metrics import STANDARD_14_WIDTHS
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


def _get_descendant_cid_font(font_obj: Any) -> Any | None:
    """
    Resolves the single descendant CIDFont dictionary from a Type0 font.
    Returns None if the font has no /DescendantFonts array, or the array
    is empty (a malformed but not-uncommon real-world PDF defect).

    Shared by /W and /CIDToGIDMap handling below, since both tables live
    on the same descendant CIDFont dictionary and must be resolved
    identically to avoid the two tables drifting onto different objects.
    """
    if "/DescendantFonts" not in font_obj:
        return None
    descendants = font_obj["/DescendantFonts"]
    if len(descendants) == 0:
        return None
    return descendants[0]


def extract_font_widths(font_obj: Any) -> dict[str, float]:
    """
    Extracts all character widths from a font dictionary into a flat dictionary.
    Maps character code / CID (hex string, e.g. "41" or "001C") -> Width (float).
    """
    subtype = str(font_obj.get("/Subtype", ""))

    if subtype == "/Type0":
        return _extract_composite_widths(font_obj)
    return _extract_simple_widths(font_obj)


def _extract_simple_widths(font_obj: Any) -> dict[str, float]:
    """Extract flat /Widths array from Simple fonts as hex keys (e.g. '41')."""
    widths: dict[str, float] = {}
    if "/Widths" not in font_obj or "/FirstChar" not in font_obj:
        return _standard14_fallback_widths(font_obj)

    try:
        first_char = int(font_obj["/FirstChar"])
    except (ValueError, TypeError):
        return widths

    widths_array = font_obj["/Widths"]

    for idx, w in enumerate(widths_array):
        char_code = first_char + idx
        try:
            widths[f"{char_code:02X}"] = float(w)
        except (ValueError, TypeError):
            pass  # skip entries with non-numeric width data

    return widths


def _standard14_fallback_widths(font_obj: Any) -> dict[str, float]:
    """A non-embedded Standard-14 font (e.g. plain /Helvetica) legally omits
    /Widths entirely -- the spec says the viewer supplies the built-in AFM
    metrics instead. Without this, every glyph in such a font silently
    measures as 0-width, which breaks any caller relying on advance widths
    (e.g. excise's glyph-position bbox test never advances the pen, so a
    whole run of text tests at its start position only). Falls back to
    STANDARD_14_WIDTHS keyed by /BaseFont name (subset prefix stripped)
    when it names one of the 14 standard fonts; returns {} otherwise so
    callers' existing "unknown width -> 0.0" behavior is unchanged for
    genuinely unresolvable fonts.
    """
    base_font = str(font_obj.get("/BaseFont", "")).lstrip("/").split("+")[-1]
    table = STANDARD_14_WIDTHS.get(base_font)
    if table is None:
        return {}
    return {f"{code:02X}": w for code, w in table.items()}


def _extract_seq_widths(start_cid: int, seq_items: list, widths: dict[str, float]) -> None:
    """Helper to populate sequential character widths mapped from lists/arrays."""
    for offset, w in enumerate(seq_items):
        cid = start_cid + offset
        try:
            widths[f"{cid:04X}"] = float(w)
        except (ValueError, TypeError):
            pass  # skip entries with non-numeric width data


def _extract_range_widths(
    start_cid: int, end_val: Any, w_val: Any, widths: dict[str, float]
) -> None:
    """Helper to populate wide ranges of consistent glyph widths."""
    try:
        end_cid = int(end_val)
        w = float(w_val)
        for cid in range(start_cid, end_cid + 1):
            widths[f"{cid:04X}"] = w
    except (ValueError, TypeError):
        pass  # non-numeric width value; skip this CID range


def _extract_composite_widths(font_obj: Any) -> dict[str, float]:
    """Extract compressed /W array from descendant CIDFonts as hex keys."""
    widths: dict[str, float] = {}
    cid_font = _get_descendant_cid_font(font_obj)
    if cid_font is None or "/W" not in cid_font:
        return widths

    w_array = cid_font["/W"]
    idx = 0
    while idx < len(w_array):
        try:
            start_cid = int(w_array[idx])
        except (ValueError, TypeError):
            idx += 1
            continue

        if idx + 1 >= len(w_array):
            break

        next_val = w_array[idx + 1]

        # Case A: Sequence array (e.g. 10 [250 300 250])
        #
        # pikepdf may return nested arrays wrapped in different concrete types
        # depending on version/context (e.g. pikepdf.Array vs a generic
        # pikepdf.Object of array type), so checking the class name or
        # `isinstance(..., list)` is unreliable. Instead, duck-type: numeric
        # PDF objects (ints/reals) and strings/bytes are not sequence arrays.
        if isinstance(next_val, (str, bytes)):
            is_sequence = False
        else:
            try:
                seq_items = list(next_val)
                is_sequence = True
            except TypeError:
                is_sequence = False

        if is_sequence:
            _extract_seq_widths(start_cid, seq_items, widths)
            idx += 2
        # Case B: Standard range (e.g. 10 20 500)
        elif idx + 2 < len(w_array):
            _extract_range_widths(start_cid, next_val, w_array[idx + 2], widths)
            idx += 3
        else:
            idx += 1

    return widths


def update_font_widths(font_obj: Any, widths_map: dict[str, float], pikepdf) -> None:
    """
    Writes a flat widths map back into the target PDF font object.
    Automatically detects Simple vs Composite fonts and formats them accordingly.
    Accepts hex keys (e.g., '41' or '001C') and converts them appropriately.
    """
    subtype = str(font_obj.get("/Subtype", ""))

    if subtype == "/Type0":
        _update_composite_widths(font_obj, widths_map, pikepdf)
    else:
        _update_simple_widths(font_obj, widths_map, pikepdf)


def _update_simple_widths(font_obj: Any, widths_map: dict[str, float], pikepdf) -> None:
    """Write flat /Widths array back into a Simple Font, converting hex keys."""
    if not widths_map:
        return

    # Normalize keys to 2-digit uppercase hex
    normalized_map = {}
    for k, v in widths_map.items():
        try:
            normalized_map[f"{int(k, 16):02X}"] = v
        except ValueError:
            pass  # skip keys that aren't valid hex

    if not normalized_map:
        return

    keys_int = sorted(int(k, 16) for k in normalized_map.keys())
    first_char = keys_int[0]
    last_char = keys_int[-1]

    widths_array = []
    for code in range(first_char, last_char + 1):
        hex_key = f"{code:02X}"
        widths_array.append(normalized_map.get(hex_key, 0.0))

    font_obj["/FirstChar"] = first_char
    font_obj["/LastChar"] = last_char
    font_obj["/Widths"] = pikepdf.Array(widths_array)


def _detect_w_range(idx: int, sorted_cids: list[int], normalized_map: dict[str, float]) -> int:
    """Detects sequential range runs where the same width applies across multiple CIDs."""
    start_cid = sorted_cids[idx]
    start_w = normalized_map[f"{start_cid:04X}"]
    range_len = 1
    while idx + range_len < len(sorted_cids):
        next_cid = sorted_cids[idx + range_len]
        next_w = normalized_map[f"{next_cid:04X}"]
        if next_cid == start_cid + range_len and next_w == start_w:
            range_len += 1
        else:
            break
    return range_len


def _detect_w_sequence(idx: int, sorted_cids: list[int]) -> int:
    """Detects sequential CID runs where varying widths may be stored."""
    start_cid = sorted_cids[idx]
    seq_len = 1
    while idx + seq_len < len(sorted_cids):
        next_cid = sorted_cids[idx + seq_len]
        if next_cid == start_cid + seq_len:
            seq_len += 1
        else:
            break
    return seq_len


def _update_composite_widths(font_obj: Any, widths_map: dict[str, float], pikepdf) -> None:
    """Compress and write flat map back to a CIDFont's nested /W array."""
    cid_font = _get_descendant_cid_font(font_obj)
    if cid_font is None:
        return

    if not widths_map:
        if "/W" in cid_font:
            del cid_font["/W"]
        return

    # Normalize keys to 4-digit uppercase hex
    normalized_map = {}
    for k, v in widths_map.items():
        try:
            normalized_map[f"{int(k, 16):04X}"] = v
        except ValueError:
            pass  # skip keys that aren't valid hex

    if not normalized_map:
        if "/W" in cid_font:
            del cid_font["/W"]
        return

    sorted_cids = sorted(int(k, 16) for k in normalized_map.keys())
    w_array_data = []

    idx = 0
    while idx < len(sorted_cids):
        start_cid = sorted_cids[idx]
        range_len = _detect_w_range(idx, sorted_cids, normalized_map)

        if range_len >= 3:
            end_cid = sorted_cids[idx + range_len - 1]
            start_w = normalized_map[f"{start_cid:04X}"]
            w_array_data.extend([start_cid, end_cid, start_w])
            idx += range_len
            continue

        seq_len = _detect_w_sequence(idx, sorted_cids)
        seq_widths = []
        for offset in range(seq_len):
            cid_val = sorted_cids[idx + offset]
            seq_widths.append(normalized_map[f"{cid_val:04X}"])

        w_array_data.extend([start_cid, pikepdf.Array(seq_widths)])
        idx += seq_len

    cid_font["/W"] = pikepdf.Array(w_array_data)


# ============================================================================
# /CIDToGIDMap
# ============================================================================
#
# A CIDFontType2 descendant font may carry /CIDToGIDMap as either:
#   - the Name /Identity (or, non-conformantly, absent entirely), meaning
#     CID N maps directly to GID N with no table at all; or
#   - a Stream of 2-byte big-endian records, where the GID for CID N is
#     stored at byte offset 2*N. Per ISO 32000-2 9.7.4.3, a CID with no
#     entry (i.e. past the end of a short stream) or an entry of 0 maps to
#     .notdef.
#
# This table is functionally independent of /W: /W is keyed and compressed
# by width equality, /CIDToGIDMap is keyed and compressed by GID identity.
# They are extracted, edited, and recompiled as entirely separate concerns,
# even though both live on the same descendant CIDFont dictionary.


def parse_cid_to_gid_map(stream_bytes: bytes) -> dict[int, int]:
    """
    Parses a /CIDToGIDMap stream into a flat CID -> GID dictionary.

    Records are 2-byte big-endian GIDs, positionally indexed by CID (the
    GID for CID N sits at byte offset 2*N). A trailing odd byte (a
    malformed stream) is ignored rather than raising. CIDs whose GID is 0
    are omitted from the result entirely: per spec, GID 0 both means
    ".notdef" and is indistinguishable from "no entry for this CID", so
    keeping only the non-zero, meaningful mappings avoids implying an
    explicit assignment that isn't really there.
    """
    mapping: dict[int, int] = {}
    record_count = len(stream_bytes) // 2
    for cid in range(record_count):
        offset = cid * 2
        gid = (stream_bytes[offset] << 8) | stream_bytes[offset + 1]
        if gid != 0:
            mapping[cid] = gid
    return mapping


def compile_cid_to_gid_map(mapping: dict[int, int]) -> bytes:
    """
    Compiles a flat CID -> GID dictionary back into a /CIDToGIDMap stream.

    Produces a contiguous table sized to cover the highest CID present;
    any CID in that range absent from `mapping` is written as GID 0
    (".notdef"), matching the stream's implicit convention. An empty
    mapping compiles to empty bytes; callers should treat that as a signal
    to fall back to the /Identity Name instead of writing a degenerate
    zero-length stream.
    """
    if not mapping:
        return b""

    max_cid = max(mapping.keys())
    table = bytearray((max_cid + 1) * 2)
    for cid, gid in mapping.items():
        offset = cid * 2
        table[offset] = (gid >> 8) & 0xFF
        table[offset + 1] = gid & 0xFF
    return bytes(table)


def extract_cid_to_gid_map(font_obj: Any) -> dict[int, int] | str | None:
    """
    Extracts the /CIDToGIDMap for a Type0 font's descendant CIDFont.

    Returns:
      - "Identity" if the font declares no descendant, no /CIDToGIDMap key,
        or an explicit /Identity (or other bare Name) value -- the common
        case where CID equals GID and no table is needed.
      - A flat {cid: gid} dict if an explicit stream is present, readable,
        and contains at least one non-zero mapping.
      - None if `font_obj` isn't a Type0 font at all, so callers can
        distinguish "not applicable" from "applicable, identity mapping".
    """
    if str(font_obj.get("/Subtype", "")) != "/Type0":
        return None

    cid_font = _get_descendant_cid_font(font_obj)
    if cid_font is None or "/CIDToGIDMap" not in cid_font:
        return "Identity"

    c2g = cid_font["/CIDToGIDMap"]

    import pikepdf

    if not isinstance(c2g, pikepdf.Stream):
        # /CIDToGIDMap is a bare Name (typically /Identity, but any other
        # Name value is non-conformant and treated the same way: there is
        # no explicit table to read). hasattr(c2g, "read_bytes") is NOT a
        # reliable test here -- read_bytes is defined on pikepdf's common
        # Object base class, so it's present (and hasattr-true) on a Name
        # too; it only raises pikepdf.PdfError once actually called on a
        # non-stream object, which is what the isinstance check below
        # avoids ever attempting in the first place.
        return "Identity"

    try:
        raw = c2g.read_bytes()
    except (AttributeError, TypeError, pikepdf.PdfError) as e:
        # A malformed or unreadable /CIDToGIDMap stream shouldn't abort the
        # whole font extraction; degrade to Identity, which is the safest
        # fallback assumption when the real table can't be recovered.
        logger.warning("Failed to read /CIDToGIDMap stream, falling back to Identity: %s", e)
        return "Identity"

    mapping = parse_cid_to_gid_map(raw)
    return mapping if mapping else "Identity"


def update_cid_to_gid_map(
    font_obj: Any, mapping: dict[int, int] | str, pikepdf, pdf: Any = None
) -> None:
    """
    Writes a CID -> GID mapping back into a Type0 font's descendant CIDFont.

    `mapping` is either the string "Identity" (writes the bare /Identity
    Name, removing any existing stream) or a flat {cid: gid} dict (compiles
    and writes an explicit stream, reusing the existing stream object if
    one is already present so its indirect reference is preserved).
    Silently no-ops if `font_obj` has no resolvable descendant CIDFont.
    """
    cid_font = _get_descendant_cid_font(font_obj)
    if cid_font is None:
        return

    if mapping == "Identity" or not mapping:
        cid_font["/CIDToGIDMap"] = pikepdf.Name("/Identity")
        return

    compiled = compile_cid_to_gid_map(mapping)
    existing = cid_font.get("/CIDToGIDMap")
    if existing is not None and hasattr(existing, "write"):
        existing.write(compiled)
    else:
        pdf_ctx = pdf or getattr(cid_font, "owner", None) or getattr(font_obj, "owner", None)
        if pdf_ctx is not None and hasattr(pdf_ctx, "make_stream"):
            cid_font["/CIDToGIDMap"] = pdf_ctx.make_stream(compiled)
        else:
            raise ValueError(
                "A valid pikepdf.Pdf context must be provided to create a new CIDToGIDMap stream."
            )


# ============================================================================
# Vertical writing mode: /DW2 and /W2
# ============================================================================
#
# A CIDFont used in vertical writing mode (WMode 1) carries vertical glyph
# metrics entirely separately from the horizontal /W table:
#
#   /DW2 [v_y w1]  -- default vertical metrics applied to any CID with no
#                     explicit /W2 entry. Per ISO 32000-2 9.7.4.3, if /DW2
#                     itself is absent the spec default is [880, -1000].
#
#   /W2  -- per-CID overrides, in the same two encoding shapes as /W, but
#           each "width" is a (w1y, vx, vy) TRIPLE instead of a single
#           number:
#             sequence form:  c [w1y_1 vx_1 vy_1  w1y_2 vx_2 vy_2 ...]
#             range form:     cFirst cLast w1y vx vy
#
# Where (per spec):
#   w1y  = vertical displacement (glyph advance along the vertical axis,
#          i.e. the vertical analogue of a horizontal /W width)
#   vx, vy = position vector: the vertical origin of the glyph, relative to
#          the horizontal origin, in glyph space (1/1000 units)
#
# This table is functionally independent of /W and /CIDToGIDMap: /W is
# keyed by width equality, /CIDToGIDMap by GID identity, /W2 by (w1y, vx,
# vy) triple equality. All three are extracted, edited, and recompiled as
# entirely separate concerns even though they share the same descendant
# CIDFont dictionary.

_SPEC_DEFAULT_DW2 = (880.0, -1000.0)  # (v_y, w1) per ISO 32000-2 9.7.4.3


def get_default_vertical_metrics(cid_font: Any) -> tuple[float, float]:
    """
    Reads a descendant CIDFont's /DW2 default vertical metrics.

    Returns (v_y, w1) -- the position-vector y-component and the default
    vertical displacement applied to any CID absent from /W2. Falls back
    to the spec default (880, -1000) if /DW2 is missing or malformed,
    matching the same "degrade to the documented default" pattern used
    elsewhere in this module (e.g. update_cid_to_gid_map's Identity
    fallback).
    """
    if cid_font is None or "/DW2" not in cid_font:
        return _SPEC_DEFAULT_DW2
    try:
        dw2 = cid_font["/DW2"]
        return float(dw2[0]), float(dw2[1])
    except (IndexError, TypeError, ValueError):
        logger.warning("Malformed /DW2 entry, falling back to spec default.")
        return _SPEC_DEFAULT_DW2


def _extract_vertical_seq(
    start_cid: int, seq_items: list, metrics: dict[str, tuple[float, float, float]]
) -> None:
    """Helper: populate vertical metrics from a /W2 sequence-form entry,
    where seq_items is a flat run of (w1y, vx, vy) triples."""
    n_triples = len(seq_items) // 3
    for i in range(n_triples):
        cid = start_cid + i
        try:
            w1y = float(seq_items[3 * i])
            vx = float(seq_items[3 * i + 1])
            vy = float(seq_items[3 * i + 2])
            metrics[f"{cid:04X}"] = (w1y, vx, vy)
        except (ValueError, TypeError):
            pass  # skip malformed triples rather than aborting the whole run


def _extract_vertical_range(
    start_cid: int,
    end_val: Any,
    w1y_val: Any,
    vx_val: Any,
    vy_val: Any,
    metrics: dict[str, tuple[float, float, float]],
) -> None:
    """Helper: populate vertical metrics from a /W2 range-form entry."""
    try:
        end_cid = int(end_val)
        w1y = float(w1y_val)
        vx = float(vx_val)
        vy = float(vy_val)
        for cid in range(start_cid, end_cid + 1):
            metrics[f"{cid:04X}"] = (w1y, vx, vy)
    except (ValueError, TypeError):
        pass  # non-numeric range entry; skip this range


def extract_vertical_widths(font_obj: Any) -> dict[str, tuple[float, float, float]]:
    """
    Extracts a Type0 font's /W2 vertical metrics into a flat dict.

    Maps CID (hex string, e.g. "001C") -> (w1y, vx, vy). Returns an empty
    dict for non-Type0 fonts, fonts with no descendant CIDFont, or a
    CIDFont with no /W2 entry -- callers should fall back to
    get_default_vertical_metrics() for any CID not present here.

    Mirrors _extract_composite_widths' sequence/range dispatch shape
    (including the same duck-typed "is this a nested array?" detection,
    since pikepdf may wrap nested /W2 arrays in different concrete types
    depending on context), but each entry is a 3-tuple instead of a
    single width.
    """
    metrics: dict[str, tuple[float, float, float]] = {}
    cid_font = _get_descendant_cid_font(font_obj)
    if cid_font is None or "/W2" not in cid_font:
        return metrics

    w2_array = cid_font["/W2"]
    idx = 0
    while idx < len(w2_array):
        try:
            start_cid = int(w2_array[idx])
        except (ValueError, TypeError):
            idx += 1
            continue

        if idx + 1 >= len(w2_array):
            break

        next_val = w2_array[idx + 1]

        if isinstance(next_val, (str, bytes)):
            is_sequence = False
        else:
            try:
                seq_items = list(next_val)
                is_sequence = True
            except TypeError:
                is_sequence = False

        if is_sequence:
            _extract_vertical_seq(start_cid, seq_items, metrics)
            idx += 2
        elif idx + 4 < len(w2_array):
            _extract_vertical_range(
                start_cid,
                next_val,
                w2_array[idx + 2],
                w2_array[idx + 3],
                w2_array[idx + 4],
                metrics,
            )
            idx += 5
        else:
            idx += 1

    return metrics


def build_vertical_metrics_lookup(font_obj: Any) -> VerticalMetricsLookup:
    """
    Builds a reusable lookup for a font's vertical metrics: parses /W2 and
    /DW2 ONCE and returns a small callable-like object, rather than every
    per-glyph call re-parsing the whole /W2 array from scratch (which is
    what an earlier version of this module did -- O(glyphs * W2 size)
    instead of O(glyphs)). Callers iterating glyphs across a page (e.g.
    `trim`'s per-glyph bbox test) should call this once per font and reuse
    the result, not call extract_vertical_widths()/get_default_vertical_metrics()
    per glyph.

    Also resolves spec-exact vx (position-vector x-component) defaulting:
    per ISO 32000-2 9.7.4.3, vx defaults to w0/2 where w0 is the glyph's
    HORIZONTAL displacement from /W -- not 0.0. This function pulls
    /W (via extract_font_widths) alongside /W2/DW2 specifically so that
    default can be computed correctly, rather than punting it to the
    caller or silently defaulting to 0.0.
    """
    cid_font = _get_descendant_cid_font(font_obj)
    w2_map = extract_vertical_widths(font_obj)
    dw2 = get_default_vertical_metrics(cid_font)
    horizontal_widths = extract_font_widths(font_obj)  # for spec-exact vx default
    return VerticalMetricsLookup(w2_map=w2_map, dw2=dw2, horizontal_widths=horizontal_widths)


@dataclass(frozen=True)
class VerticalMetricsLookup:
    """Pre-parsed /W2 + /DW2 + /W state for O(1) per-glyph vertical metric
    lookups. Build via build_vertical_metrics_lookup(font_obj); don't
    construct directly."""

    w2_map: dict[str, tuple[float, float, float]]
    dw2: tuple[float, float]  # (v_y, w1)
    horizontal_widths: dict[str, float]

    def get(self, cid_hex: str) -> tuple[float, float, float]:
        """Returns (w1y, vx, vy) for one CID. O(1) dict lookups only --
        no re-parsing of /W2 on every call."""
        if cid_hex in self.w2_map:
            return self.w2_map[cid_hex]
        v_y, w1 = self.dw2
        w0 = self.horizontal_widths.get(cid_hex, 0.0)
        return (w1, w0 / 2.0, v_y)


def is_vertical_writing_mode(font_obj: Any) -> bool:
    """
    Detects whether a Type0 font uses vertical writing mode (WMode 1).

    Checks, in order:
      1. /Encoding as a Name ending in "-V" (e.g. /Identity-V) -- the
         common case for predefined CMaps.
      2. /Encoding as an embedded CMap Stream with an explicit /WMode
         key equal to 1.

    Returns False for non-Type0 fonts, fonts with no /Encoding, or any
    case that can't be determined (degrade-to-horizontal is the safe
    default, since horizontal is far more common and a false negative
    here just means vertical glyphs get slightly wrong extents rather
    than crashing).

    KNOWN GAP: does not resolve /Encoding given as a Name that refers to
    a non-predefined, PDF-registered CMap resource (i.e. some Name other
    than one of the spec's predefined CMap names like /Identity-H,
    /Identity-V, or the various */UCS2 etc. names) whose actual WMode
    would require looking up a CMap resource elsewhere in the PDF and
    parsing its /WMode. That resolution path is not implemented; such a
    font will be (incorrectly) treated as horizontal. This is considered
    rare enough in practice to defer, but any caller hitting mis-detected
    vertical text on a real-world PDF should suspect this path first.
    """
    if str(font_obj.get("/Subtype", "")) != "/Type0":
        return False
    if "/Encoding" not in font_obj:
        return False

    encoding = font_obj["/Encoding"]

    import pikepdf

    if isinstance(encoding, pikepdf.Name):
        return str(encoding).endswith("-V")

    if isinstance(encoding, pikepdf.Stream):
        try:
            return int(encoding.get("/WMode", 0)) == 1
        except (TypeError, ValueError):
            return False

    return False
