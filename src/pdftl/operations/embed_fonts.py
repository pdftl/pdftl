# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/embed_fonts.py

"""
Operation for automatically locating and embedding system fonts into a PDF document
where they are declared but missing their binary streams.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.fonts.file_locator import resolve_system_font_path
from pdftl.fonts.font_extraction_utils import find_font_descriptor, process_single_font
from pdftl.operations.helpers.font_ops_shared import get_target_pages
from pdftl.utils.dependencies import ensure_dependencies
from pdftl.utils.pdf_resources import get_all_fonts_recursive

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)

_EMBED_FONTS_LONG_DESC = """
The `embed_fonts` operation automatically locates and embeds missing fonts. It scans
the PDF for any font definitions (including Standard 14 BaseFonts like Helvetica or
Arial) that lack an embedded binary stream.

When an unembedded font is found, the operation searches the host operating system's
font directories (and any custom directories provided) to find a matching `.ttf`,
`.otf`, or `.pfb` file. If a match is found, the font is read into memory and
injected directly into the PDF.

If the original PDF font lacked a `/FontDescriptor` entirely, the operation leverages
`fontTools` to dynamically extract accurate typography metrics from the located binary
and constructs a compliant descriptor from scratch before embedding it.

### Arguments

* `[page_range]`: Optional page ranges to limit the scope of the font scanning.
* `[fontdir <dir>]`: Optional custom directory to search for font files. Can be specified multiple
  times.
* `[nosys]`: If provided, skips searching the host operating system's standard font directories,
  strictly relying on provided `fontdir` paths.
* `[rename]`: If provided, renames the PDF's internal `/BaseFont` and `/FontName` structures
  to match the true name of the injected font binary.
"""

_EMBED_FONTS_EXAMPLES = [
    {
        "cmd": "in.pdf embed_fonts output out.pdf",
        "desc": "Scan the entire document and embed any missing fonts using system equivalents.",
    },
    {
        "cmd": "in.pdf embed_fonts 1-5 fontdir /brand/assets nosys output out.pdf",
        "desc": (
            "Scan only pages 1 through 5, explicitly looking for missing fonts "
            "only in '/brand/assets' and ignoring system fonts.",
        ),
    },
]


def _extract_ps_name(sys_path: str, file_ext: str) -> str | None:
    """Extracts the true PostScript name from a font binary on disk."""
    if file_ext in (".ttf", ".ttc", ".otf"):
        try:
            from fontTools.ttLib import TTFont, TTLibError
            from pdftl.fonts.font_embedding import _extract_ttf_metrics

            tt = TTFont(sys_path)
            return _extract_ttf_metrics(tt).ps_name
        except (OSError, ValueError, KeyError, AttributeError, TypeError, TTLibError) as e:
            logger.debug("Failed to extract PS name from sfnt font: %s", e)
    elif file_ext in (".pfb", ".pfa"):
        try:
            from fontTools.t1Lib import T1Font, T1Error

            try:
                t1 = T1Font(sys_path)
                t1.parse()
            except T1Error:
                t1 = T1Font(sys_path, kind="OTHER")
                t1.parse()
            return t1.font.get("FontName")
        except (OSError, ValueError, KeyError, AttributeError, TypeError, T1Error) as e:
            logger.debug("Failed to extract PS name from Type 1 font: %s", e)
    return None


def _rename_font_objects(font_obj: Any, descriptor: Any, ps_name: str, pikepdf_mod: Any) -> None:
    """Updates the PDF font structures to reflect the true name of the embedded binary."""
    new_name = pikepdf_mod.Name(f"/{ps_name}")

    if descriptor:
        descriptor["/FontName"] = new_name

    font_obj["/BaseFont"] = new_name

    if str(font_obj.get("/Subtype", "")) == "/Type0" and "/DescendantFonts" in font_obj:
        try:
            # For composite Type0 fonts, the descendant CIDFont also needs its BaseFont renamed
            descendant = font_obj.DescendantFonts[0]
            descendant["/BaseFont"] = new_name
        except (AttributeError, IndexError, TypeError):
            pass


def _attach_stream_to_descriptor(
    pdf: pikepdf.Pdf, descriptor: Any, font_bytes: bytes, file_ext: str
) -> None:
    """Attaches the raw font byte stream to a FontDescriptor dictionary
    under the correct format-specific key."""
    import pikepdf

    font_stream = pdf.make_stream(font_bytes)

    # A descriptor may already carry a stale FontFile* key from whatever
    # program it held before (e.g. a Type 1 /FontFile left over from a
    # prior font being replaced by a TrueType binary here). Per ISO
    # 32000-2 Table 121, at most one of /FontFile, /FontFile2, /FontFile3
    # is meaningful for a given font program; leaving a stale one in place
    # alongside the new key is invalid and, in practice, some renderers
    # silently prefer the old (now-mismatched) stream over the new one --
    # producing exactly the "replacement silently had no visual effect"
    # failure mode. Always clear all three before writing the new key.
    for stale_key in ("/FontFile", "/FontFile2", "/FontFile3"):
        if stale_key in descriptor:
            del descriptor[stale_key]

    if file_ext in (".ttf", ".ttc"):
        font_stream.Length1 = len(font_bytes)
        descriptor["/FontFile2"] = font_stream
    elif file_ext == ".otf":
        font_stream.Subtype = pikepdf.Name("/OpenType")
        descriptor["/FontFile3"] = font_stream
    elif file_ext in (".pfb", ".pfa"):
        from pdftl.operations.helpers.font_import_helpers import _update_type1_length_fields

        _update_type1_length_fields(font_stream, font_bytes)
        descriptor["/FontFile"] = font_stream
    else:
        # Graceful fallback: Treat unrecognized but located files as generic OpenType
        font_stream.Subtype = pikepdf.Name("/OpenType")
        descriptor["/FontFile3"] = font_stream


def _create_and_attach_descriptor(
    pdf: pikepdf.Pdf, font_obj: Any, font_bytes: bytes, sys_path: str, file_ext: str
) -> bool:
    """Dynamically parses a located system font to generate a fully compliant
    FontDescriptor from its metrics when the PDF lacks one."""
    import pikepdf
    from fontTools.ttLib import TTFont, TTLibError

    from pdftl.fonts.font_embedding import _extract_ttf_metrics, FontMetricsData

    if file_ext in (".ttf", ".ttc", ".otf"):
        try:
            tt = TTFont(sys_path)
            metrics = _extract_ttf_metrics(tt)
        except (TTLibError, OSError, ValueError, KeyError, AttributeError) as e:
            # A corrupted, unparseable, or missing-table font file cannot yield a valid descriptor.
            logger.warning("Could not parse font %s to generate descriptor: %s", sys_path, e)
            return False
    elif file_ext in (".pfb", ".pfa"):
        try:
            from fontTools.t1Lib import T1Font, T1Error

            # Attempt to parse as a segmented PFB container first.
            # Fall back to kind="OTHER" to support plain PostScript streams.
            try:
                t1 = T1Font(sys_path)
                t1.parse()
            except T1Error:
                t1 = T1Font(sys_path, kind="OTHER")
                t1.parse()

            font_info = t1.font.get("FontInfo", {})
            private = t1.font.get("Private", {})

            bbox = font_info.get("FontBBox", [0.0, 0.0, 0.0, 0.0])
            bbox_valid = isinstance(bbox, list) and len(bbox) == 4

            metrics = FontMetricsData(
                ps_name=t1.font.get("FontName", "EmbeddedType1"),
                flags=33 if font_info.get("isFixedPitch") else 32,
                bbox=bbox if bbox_valid else [0.0, 0.0, 0.0, 0.0],
                italic_angle=float(font_info.get("ItalicAngle", 0.0)),
                ascent=float(bbox[3]) if bbox_valid else 0.0,
                descent=float(bbox[1]) if bbox_valid else 0.0,
                cap_height=float(private.get("CapHeight", bbox[3] if bbox_valid else 0.0)),
                scale=1.0,
                units_per_em=1000,
            )
        except (T1Error, OSError, ValueError, KeyError, AttributeError, TypeError) as e:
            logger.warning(
                "Could not parse Type 1 font %s to generate descriptor: %s", sys_path, e
            )
            return False
    else:
        logger.info("Cannot auto-generate descriptor for unrecognized format: %s", sys_path)
        return False

    descriptor = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name(f"/{metrics.ps_name}"),
                "/Flags": metrics.flags,
                "/FontBBox": pikepdf.Array([float(x) for x in metrics.bbox]),
                "/ItalicAngle": float(metrics.italic_angle),
                "/Ascent": float(metrics.ascent),
                "/Descent": float(metrics.descent),
                "/CapHeight": float(metrics.cap_height),
                "/StemV": 80,  # Standard fallback approximation for StemV thickness
            }
        )
    )

    _attach_stream_to_descriptor(pdf, descriptor, font_bytes, file_ext)

    target_obj = font_obj
    if str(font_obj.get("/Subtype", "")) == "/Type0" and "/DescendantFonts" in font_obj:
        try:
            # For composite Type0 fonts, the descriptor strictly belongs to the descendant CIDFont
            target_obj = font_obj.DescendantFonts[0]
        except (AttributeError, IndexError, TypeError):
            # If the descendant structure is malformed, fall back to writing on the parent
            pass

    target_obj["/FontDescriptor"] = descriptor
    return True


@register_operation(
    "embed_fonts",
    tags=["in_place", "fonts", "embed", "repair"],
    type="single input operation",
    desc="Automatically locate and embed missing system fonts",
    long_desc=_EMBED_FONTS_LONG_DESC,
    examples=_EMBED_FONTS_EXAMPLES,
    usage="<input> embed_fonts [<spec>...] [output <output>]",
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def embed_fonts(pdf: pikepdf.Pdf, specs: list[str]) -> OpResult:
    """
    Locate equivalents for unembedded fonts and inject their binary
    streams back into the PDF structures.
    """
    ensure_dependencies(
        feature_name="embed_fonts",
        dependencies={"fontTools": "fonttools"},
        extra_tag="embed-fonts",
    )

    custom_dirs = []
    use_system = True
    do_rename = False
    clean_specs = []

    i = 0
    while i < len(specs):
        arg = specs[i]
        if arg == "fontdir" and i + 1 < len(specs):
            custom_dirs.append(specs[i + 1])
            i += 2
        elif arg == "nosys":
            use_system = False
            i += 1
        elif arg == "rename":
            do_rename = True
            i += 1
        else:
            clean_specs.append(arg)
            i += 1

    target_pages = get_target_pages(pdf, clean_specs)
    embedded_count = 0
    seen_obj_ids = set()

    for local_name, font_obj, _ in get_all_fonts_recursive(pdf, target_pages):
        obj_id = (
            font_obj.objgen[0] if hasattr(font_obj, "objgen") and font_obj.objgen else id(font_obj)
        )
        if obj_id in seen_obj_ids:
            continue
        seen_obj_ids.add(obj_id)

        font_meta = process_single_font(local_name, font_obj)
        if not font_meta or font_meta.get("is_embedded"):
            continue

        base_font = font_meta.get("base_font")
        if not base_font or base_font == "[none]":
            continue

        sys_path = resolve_system_font_path(
            base_font, custom_dirs=custom_dirs, use_system=use_system
        )
        if not sys_path:
            logger.warning("Could not locate font binary for unembedded PDF font: %s", base_font)
            continue

        try:
            with open(sys_path, "rb") as f:
                font_bytes = f.read()
        except OSError as e:
            # File exists but access denied or read error occurred; skip gracefully
            logger.warning("Failed to read located font file %s: %s", sys_path, e)
            continue

        file_ext = Path(sys_path).suffix.lower()
        descriptor = find_font_descriptor(font_obj)

        if descriptor:
            _attach_stream_to_descriptor(pdf, descriptor, font_bytes, file_ext)
            embedded_count += 1
            logger.info(
                "Successfully embedded missing font stream for %s from %s", base_font, sys_path
            )
        else:
            if _create_and_attach_descriptor(pdf, font_obj, font_bytes, sys_path, file_ext):
                embedded_count += 1
                logger.info(
                    "Created descriptor and embedded missing font for %s from %s",
                    base_font,
                    sys_path,
                )

        if do_rename:
            import pikepdf

            ps_name = _extract_ps_name(sys_path, file_ext)
            if ps_name:
                resolved_desc = descriptor if descriptor else font_obj.get("/FontDescriptor")
                _rename_font_objects(font_obj, resolved_desc, ps_name, pikepdf)
                logger.info(
                    "Renamed PDF font structures from %s to match injected binary %s",
                    base_font,
                    ps_name,
                )

    logger.info("Successfully embedded %d missing font(s).", embedded_count)
    return OpResult(success=True, pdf=pdf)
