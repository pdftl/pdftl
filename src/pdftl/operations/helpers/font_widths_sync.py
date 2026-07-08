# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/helpers/font_widths_sync.py

"""
Width synchronization logic for the `import_fonts` operation: deciding which
sync mode a font's sidecar requests (auto / manual / patch_font_metrics /
squash_font_vectors / preserve), resolving CID->GID mappings needed for
Type0-font binary edits, and executing the in-memory font-binary patch or
squash, always falling back to a manual /Widths (or /W) write so a
requested edit never silently vanishes.

## Module split

This used to live directly in font_import_helpers.py, which now only holds
the non-widths import logic (binary stream injection, Type 3 glyphs,
/ToUnicode, /Differences, /Encoding, /CIDToGIDMap, /FontDescriptor) and
re-exports this module's public names for backward compatibility -- see
that module's own docstring.

This module deliberately imports `pdftl.operations.helpers.font_import_helpers`
as a bare module reference (`_fih`), rather than importing the individual
names it needs (`patch_font_file_metrics`, `squash_font_file_vectors`,
`get_font_widths_from_file`, `update_font_widths`, `classify_binary_format`,
`rekey_name_widths_to_hex_codes`, `file_hash`, `_inject_font_bytes`) directly
from their own source modules. Every one of those is looked up as `_fih.<name>`
at call time instead. This is required, not stylistic: font_import_helpers.py
re-exports all of them (see its own imports), and the existing test suite
monkeypatches them as attributes on that module (e.g.
`monkeypatch.setattr(fih, "patch_font_file_metrics", fake_patch)`). A direct
`from X import Y` here would bind a private copy at import time, decoupled
from font_import_helpers's own attribute -- silently defeating those
monkeypatches rather than raising anything. Looking them up via `_fih.<name>`
at call time means they always resolve against whatever font_import_helpers
currently has bound, monkeypatched or not.

The reverse import (font_import_helpers.py importing this module's public
functions for re-export) makes this a genuine two-way relationship between
the two modules. That's safe here specifically because this module only
ever does a bare `import module_x as _fih` and defers all attribute access
to function bodies -- never `from module_x import name` -- so it doesn't
matter that font_import_helpers.py may still be mid-import the first time
this module is loaded.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pdftl.operations.helpers.font_import_helpers as _fih

logger = logging.getLogger(__name__)


def _resolve_cid_to_gid_for_sync(
    font_obj: Any, font_entry: dict, out_dir: Path
) -> dict[int, int] | str | None:
    """
    Resolves the CID->GID mapping to use when patching or squashing a
    Type0 font binary's metrics/vectors during width synchronization.
    Callers must only invoke this for a font already confirmed to be
    Type0 (see patch_font_file_metrics's `cid_to_gid_map` parameter).

    A bare (non-sfnt-wrapped) CID-keyed CFF -- `embedded_format ==
    "cff"`, i.e. a /CIDFontType0C program -- has no /CIDToGIDMap at all
    (ISO 32000-2 Table 115 restricts that entry to Type 2 CIDFonts): its
    CID->GID resolution lives entirely inside the CFF's own ROS/charset
    machinery instead, which `cff_binary_utils.get_widths_from_cff` /
    `patch_cff_widths` only reach when passed the sentinel string
    `"cff_native"` -- see their docstrings. Returning the sidecar's
    `cid_to_gid_map` value ("Identity" or a {cid: gid} dict, meant for a
    real /CIDToGIDMap on a Type 2 CIDFont) here instead would make
    `patch_font_file_metrics`/`get_font_widths_from_file` dispatch such a
    font down their *name*-keyed branch, where nothing keyed by hex CID
    string can ever match a name -- a silent, total no-op rather than an
    error. This function special-cases that font shape first, before
    falling through to the ordinary sidecar-driven resolution below (used
    for genuine Type 2 CIDFonts with a real /CIDToGIDMap, sfnt or
    otherwise).

    Otherwise returns "Identity" or a flat {cid: gid} dict on success,
    matching the same vocabulary as
    pdftl.fonts.widths_utils.extract_cid_to_gid_map /
    import_cid_to_gid_map. Returns None if an explicit mapping was
    expected but its sidecar file is missing or unreadable -- callers
    must treat that as "cannot safely resolve" and skip CID-aware sync
    for this font entirely, rather than guessing Identity, since a
    CID-keyed font with a genuinely non-identity mapping would then have
    its glyphs patched under the wrong GIDs.

    Note this only affects the font *binary's* own hmtx/glyf/CFF
    patch/squash, which genuinely needs GID (or, for bare CFF, CID->CFF
    ROS) resolution. It does not affect writing `/W` itself, which is
    CID-keyed and needs no GID resolution at all -- see
    _apply_in_memory_patch/_apply_in_memory_squash, which still write /W
    manually even when this returns None.
    """
    if _fih.classify_binary_format(font_entry.get("embedded_format")) == "bare_cff":
        return "cff_native"

    mode = font_entry.get("cid_to_gid_map")
    if mode is None or mode == "Identity":
        return "Identity"

    filename = font_entry.get("cid_to_gid_map_file")
    if not filename:
        return None

    filepath = out_dir / filename
    if not filepath.is_file():
        logger.warning(
            "CIDToGIDMap sidecar file %s not found; skipping CID-aware width sync for this font.",
            filename,
        )
        return None

    try:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        hex_mapping = data.get("cid_to_gid", {})
        return {int(c, 16): int(g, 16) for c, g in hex_mapping.items()}
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "Failed to read CIDToGIDMap sidecar %s for width sync: %s; "
            "skipping CID-aware width sync for this font.",
            filepath,
            exc,
        )
        return None


def _rekey_simple_font_widths_if_needed(
    widths_map: dict[str, float], font_entry: dict
) -> dict[str, float]:
    """
    Translates a widths map read back from a bare CFF or Type 1 Simple
    font's own binary from glyph-name keys to hex-PDF-code keys, so it
    matches the shape `update_font_widths` expects on every other code
    path.

    `get_font_widths_from_file` returns a name-keyed map for these two
    formats -- see its docstring's "Simple-font key-space translation"
    reference -- since neither format has a PDF-code-indexed table of its
    own to read widths out by. TrueType/OpenType (sfnt) and CID-keyed CFF
    widths are already correctly hex-keyed and pass through unchanged.
    """
    fmt = _fih.classify_binary_format(font_entry.get("embedded_format"))
    if fmt not in ("bare_cff", "type1"):
        return widths_map
    return _fih.rekey_name_widths_to_hex_codes(
        widths_map,
        differences=font_entry.get("differences"),
        base_encoding=font_entry.get("base_encoding"),
    )


def _resolve_cid_to_gid_if_type0(
    font_obj: Any, font_entry: dict, out_dir: Path, is_type0: bool
) -> tuple[dict[int, int] | str | None, bool]:
    """
    Resolves the CID->GID mapping for auto width-sync, only if the font is
    Type0. Returns (cid_to_gid_map, ok); ok=False means the mapping could
    not be resolved and a warning has already been logged -- the caller
    must abort automatic sync for this font entirely (see
    _resolve_cid_to_gid_for_sync's docstring for why guessing is unsafe).
    """
    if not is_type0:
        return None, True

    cid_to_gid_map = _resolve_cid_to_gid_for_sync(font_obj, font_entry, out_dir)
    if cid_to_gid_map is None:
        logger.warning(
            "Could not resolve CID->GID mapping for %s; skipping automatic width sync.",
            font_entry.get("base_font", ""),
        )
        return None, False
    return cid_to_gid_map, True


def _load_and_rekey_widths_for_sync(
    filepath: Path,
    font_entry: dict,
    cid_to_gid_map: dict[int, int] | str | None,
    is_type0: bool,
) -> dict[str, float] | None:
    """Reads widths from the font's binary and, for non-Type0 formats whose
    binary has no PDF-code-indexed table of its own (bare CFF / Type 1),
    rekeys them from glyph-name to hex-PDF-code -- see
    _rekey_simple_font_widths_if_needed's docstring. Returns None if no
    widths were read, or if rekeying leaves nothing usable."""
    widths_map = _fih.get_font_widths_from_file(
        filepath,
        cid_to_gid_map=cid_to_gid_map,
        embedded_format=font_entry.get("embedded_format"),
        base_font=font_entry.get("base_font", ""),
    )
    if not widths_map:
        return None

    if not is_type0:
        widths_map = _rekey_simple_font_widths_if_needed(widths_map, font_entry)
        if not widths_map:
            return None

    return widths_map


def _auto_sync_widths_from_font(font_obj: Any, font_entry: dict, out_dir: Path, pikepdf) -> bool:
    """Loads the manually modified font using fontTools and auto-syncs widths to the PDF."""
    try:
        from fontTools.ttLib import TTLibError
    except ImportError:
        TTLibError = type("DummyError", (Exception,), {})

    filename = font_entry.get("embedded_file")
    if not filename:
        return False

    filepath = out_dir / filename
    if not filepath.is_file():
        return False

    current_hash = _fih.file_hash(filepath)
    if current_hash == font_entry.get("binary_md5"):
        return False

    is_type0 = hasattr(font_obj, "get") and str(font_obj.get("/Subtype", "")) == "/Type0"
    cid_to_gid_map, ok = _resolve_cid_to_gid_if_type0(font_obj, font_entry, out_dir, is_type0)
    if not ok:
        return False

    try:
        widths_map = _load_and_rekey_widths_for_sync(
            filepath, font_entry, cid_to_gid_map, is_type0
        )
        if not widths_map:
            return False

        _fih.update_font_widths(font_obj, widths_map, pikepdf)
        logger.info(
            "Automatically synchronized /Widths metrics for %s from TrueType tables.",
            font_entry["base_font"],
        )
        return True
    except (
        OSError,
        ValueError,
        KeyError,
        AttributeError,
        TypeError,
        TTLibError,
        pikepdf.PdfError,
    ) as e:
        logger.warning("Failed to auto-sync widths for %s: %s", font_entry["base_font"], e)
        return False


def _read_sync_mode(font_entry: dict, out_dir: Path) -> tuple[str, Path | None]:
    """Helper method to determine width synchronization scheme from sidecar metadata files."""
    sync_mode = "auto"
    json_path = None
    if "sidecar_json_file" in font_entry:
        json_path = out_dir / font_entry["sidecar_json_file"]
        if json_path.is_file():
            try:
                with open(json_path, encoding="utf-8") as f:
                    sync_mode = json.load(f).get("width_sync_mode", "auto")
            except (OSError, ValueError, TypeError, KeyError):
                pass
    return sync_mode, json_path


def _extract_manual_widths(mappings: dict[str, dict]) -> dict[str, float]:
    """Helper method to filter and extract user-defined widths from schema collections."""
    pdf_widths = {}
    for hex_key, entry in mappings.items():
        if "width" in entry and "pdf" in entry["width"]:
            try:
                int(hex_key, 16)
                pdf_widths[hex_key] = entry["width"]["pdf"]
            except ValueError:
                continue
    return pdf_widths


def _apply_in_memory_patch(
    font_obj: Any,
    font_entry: dict,
    pdf_widths: dict[str, float],
    filepath: Path,
    out_dir: Path,
    pikepdf,
) -> bool:
    """
    Helper execution block to patch advance widths in font and synchronize
    PDF widths.

    Guarantees that whatever `pdf_widths` the sidecar carries always lands
    in the PDF's /Widths or /W array, even when the binary-level patch
    can't be applied at all -- an unresolvable CID->GID mapping, no codes
    matched anything patchable, or a malformed/unreadable font program.
    Only the font program's own hmtx metrics are best-effort here; the
    PDF-level width correctness that /W itself provides is never silently
    dropped, since /W is keyed by CID/code directly and needs none of the
    font-binary-level resolution that can legitimately fail.
    """
    is_type0 = str(font_obj.get("/Subtype", "")) == "/Type0"
    cid_to_gid_map = None
    differences = None
    base_encoding = None

    if is_type0:
        cid_to_gid_map = _resolve_cid_to_gid_for_sync(font_obj, font_entry, out_dir)
        if cid_to_gid_map is None:
            # /W is CID-keyed, not GID-keyed -- an unresolvable CID->GID
            # mapping only blocks the *font binary* patch below, which
            # genuinely needs GID to locate the right glyph. It does not
            # block writing the sidecar's CID-keyed widths to /W itself.
            logger.warning(
                "Could not resolve CID->GID mapping for %s; skipping in-memory "
                "metric patch, but still writing sidecar widths to /W manually.",
                font_entry.get("base_font", ""),
            )
            if pdf_widths:
                _fih.update_font_widths(font_obj, pdf_widths, pikepdf)
            return False
    else:
        differences = font_entry.get("differences")
        base_encoding = font_entry.get("base_encoding")

    modified_bytes = _fih.patch_font_file_metrics(
        filepath,
        pdf_widths,
        differences=differences,
        base_encoding=base_encoding,
        cid_to_gid_map=cid_to_gid_map,
        embedded_format=font_entry.get("embedded_format"),
        base_font=font_entry.get("base_font", ""),
    )
    if not modified_bytes:
        # Covers both "no codes matched anything patchable" and a
        # malformed/unreadable font program. Either way, the sidecar's
        # requested pdf widths must still reach /W or /Widths.
        logger.debug(
            "In-memory metric patch did not apply for %s; falling back to "
            "a manual /Widths (or /W) update instead.",
            font_entry.get("base_font", ""),
        )
        if pdf_widths:
            _fih.update_font_widths(font_obj, pdf_widths, pikepdf)
        return False

    if _fih._inject_font_bytes(font_obj, font_entry, modified_bytes):
        logger.info("Injected in-memory patched metrics for %s", font_entry.get("base_font", ""))
    _fih.update_font_widths(font_obj, pdf_widths, pikepdf)
    return True


def _apply_in_memory_squash(
    font_obj: Any,
    font_entry: dict,
    pdf_widths: dict[str, float],
    filepath: Path,
    out_dir: Path,
    pikepdf,
) -> bool:
    """
    Helper execution block to scale glyf vectors in font and synchronize
    PDF widths. Carries the same fallback guarantee as
    _apply_in_memory_patch -- see its docstring. This matters even more
    here, since squash_font_file_vectors structurally can never rescale
    the *outline* of any CFF-flavored font (Type1C, CIDFontType0C,
    OpenType/CFF): those have no /glyf table to deform at all. For a bare
    CFF or Type 1 program, squash_font_file_vectors already degrades to a
    metrics-only patch on its own (see its docstring); this function's own
    fallback below only has to cover a genuine squash/patch miss (no code
    matched) or a malformed font program, the same as _apply_in_memory_patch.
    """
    is_type0 = str(font_obj.get("/Subtype", "")) == "/Type0"
    cid_to_gid_map = None
    differences = None
    base_encoding = None

    if is_type0:
        cid_to_gid_map = _resolve_cid_to_gid_for_sync(font_obj, font_entry, out_dir)
        if cid_to_gid_map is None:
            logger.warning(
                "Could not resolve CID->GID mapping for %s; skipping in-memory "
                "vector squash, but still writing sidecar widths to /W manually.",
                font_entry.get("base_font", ""),
            )
            if pdf_widths:
                _fih.update_font_widths(font_obj, pdf_widths, pikepdf)
            return False
    else:
        differences = font_entry.get("differences")
        base_encoding = font_entry.get("base_encoding")

    modified_bytes = _fih.squash_font_file_vectors(
        filepath,
        pdf_widths,
        differences=differences,
        base_encoding=base_encoding,
        cid_to_gid_map=cid_to_gid_map,
        embedded_format=font_entry.get("embedded_format"),
        base_font=font_entry.get("base_font", ""),
    )
    if not modified_bytes:
        # Covers a genuine squash/patch miss (no code matched) and a
        # malformed font program -- squash_font_file_vectors already logs
        # and returns None for those; this is the single place responsible
        # for not letting the requested edit vanish silently.
        logger.debug(
            "In-memory vector squash did not apply for %s; falling back to "
            "a manual /Widths (or /W) update instead.",
            font_entry.get("base_font", ""),
        )
        if pdf_widths:
            _fih.update_font_widths(font_obj, pdf_widths, pikepdf)
        return False

    if _fih._inject_font_bytes(font_obj, font_entry, modified_bytes):
        logger.info("Injected in-memory squashed vectors for %s", font_entry.get("base_font", ""))
    _fih.update_font_widths(font_obj, pdf_widths, pikepdf)
    return True


def _execute_widths_sync(
    font_obj: Any,
    font_entry: dict,
    out_dir: Path,
    sync_mode: str,
    pdf_widths: dict[str, float],
    filepath: Path | None,
    pikepdf,
) -> bool:
    """Helper execution engine to synchronize targeted layouts back to raw formats."""
    has_file = filepath is not None and filepath.is_file()

    # In-memory Metric Patching
    if sync_mode == "patch_font_metrics" and has_file:
        return _apply_in_memory_patch(font_obj, font_entry, pdf_widths, filepath, out_dir, pikepdf)

    # In-memory Vector Squashing
    if sync_mode == "squash_font_vectors" and has_file:
        return _apply_in_memory_squash(
            font_obj, font_entry, pdf_widths, filepath, out_dir, pikepdf
        )

    # A binary-dependent sync mode was requested but no font program is
    # available to operate on -- typically an unembedded Core 14 font
    # (is_embedded: false, embedded_file: null in the manifest). Degrade
    # explicitly to a manual /Widths (or /W) write using whatever pdf
    # widths the sidecar carries, rather than silently dropping the
    # requested edits or relying on the generic 'auto' path below to
    # happen to do the right thing.
    if sync_mode in ("patch_font_metrics", "squash_font_vectors") and not has_file:
        logger.debug(
            "No embedded font binary available for '%s' sync on %s; "
            "falling back to a manual /Widths (or /W) update instead.",
            sync_mode,
            font_entry.get("base_font", ""),
        )
        if pdf_widths:
            _fih.update_font_widths(font_obj, pdf_widths, pikepdf)
        return False

    # Manual PDF Width Overrides
    if sync_mode == "manual":
        _fih.update_font_widths(font_obj, pdf_widths, pikepdf)
        return False

    # Default: sync_mode == "auto" (Reads disk file only if modified)
    if not _auto_sync_widths_from_font(font_obj, font_entry, out_dir, pikepdf) and pdf_widths:
        _fih.update_font_widths(font_obj, pdf_widths, pikepdf)

    return False


def import_widths(
    font_obj: Any,
    font_entry: dict,
    out_dir: Path,
    mappings: dict[str, dict],
    pikepdf,
) -> bool:
    """Decides on and executes widths synchronization strategy."""
    sync_mode, json_path = _read_sync_mode(font_entry, out_dir)

    if sync_mode == "preserve":
        return False

    pdf_widths = _extract_manual_widths(mappings)
    filename = font_entry.get("embedded_file")
    filepath = out_dir / filename if filename else None

    if json_path and sync_mode not in (
        "auto",
        "preserve",
        "patch_font_metrics",
        "squash_font_vectors",
        "manual",
    ):
        logger.warning("Unknown sync mode '%s' in %s. Treating as 'auto'", sync_mode, json_path)

    return _execute_widths_sync(
        font_obj, font_entry, out_dir, sync_mode, pdf_widths, filepath, pikepdf
    )
