import logging
import re
from typing import TYPE_CHECKING, List, cast

from pdftl.fonts.font_embedding import embed_truetype_font
from pdftl.utils.dependencies import ensure_dependencies

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)

# Matches font name and size: e.g., b'/Helv 12 Tf'
DA_FONT_RE = re.compile(rb"/([\w\.\-]+)\s+([\d\.]+)\s+Tf")


def replace_form_fonts(pdf: "pikepdf.Pdf", ttf_path: str):
    """
    In-place replacement of fonts used in interactive form fields.
    Matches pdftk's -replacement_font behavior.
    """
    ensure_dependencies(
        feature_name="replacement_font",
        dependencies={"fontTools": "fonttools"},
        extra_tag="replacement-font",
    )

    import pikepdf

    if not hasattr(pdf.Root, "AcroForm"):
        logger.debug("No AcroForm found. Nothing to replace.")
        return

    acroform = pdf.Root.AcroForm

    # 1. Embed the TTF
    NEW_ALIAS = "PdftlReplacementFont"
    font_obj = embed_truetype_font(pdf, ttf_path)

    # 2. Register in Global Resources
    if "/DR" not in acroform:
        acroform.DR = pikepdf.Dictionary()
    if "/Font" not in acroform.DR:
        acroform.DR.Font = pikepdf.Dictionary()

    acroform.DR.Font[pikepdf.Name(f"/{NEW_ALIAS}")] = font_obj

    # 3. Patch Global Default Appearance
    if "/DA" in acroform:
        acroform.DA = pikepdf.String(_patch_da_content(bytes(acroform.DA), NEW_ALIAS))
    else:
        acroform.DA = pikepdf.String(f"/{NEW_ALIAS} 12 Tf 0 g")

    # 4. Patch Individual Fields
    if "/Fields" in acroform:
        for field in cast(List["pikepdf.Object"], acroform.Fields):
            _update_field_font_recursive(field, NEW_ALIAS)

    # 5. Set the regeneration flag
    acroform.NeedAppearances = True
    logger.info("Form fonts replaced with: %s", ttf_path)


def _patch_da_content(da_content: bytes, new_alias: str) -> bytes:
    """Swaps font name while keeping size (Group 2 in regex)."""
    replacement = f"/{new_alias} \\2 Tf".encode()
    return DA_FONT_RE.sub(replacement, da_content)


def _update_field_font_recursive(field: "pikepdf.Object", new_alias: str):
    """Traverse the form field tree to update /DA strings."""
    import pikepdf

    # Update field-level /DA if present
    if "/DA" in field:
        field.DA = pikepdf.String(_patch_da_content(bytes(field.DA), new_alias))

    # Ensure Text (Tx) and Choice (Ch) fields have a valid DA
    if "/FT" in field and field.FT in (pikepdf.Name("/Tx"), pikepdf.Name("/Ch")):
        if "/DA" not in field:
            field.DA = pikepdf.String(f"/{new_alias} 12 Tf 0 g")

    # Recurse into children (Radio Button groups, etc)
    if "/Kids" in field:
        for kid in cast(List["pikepdf.Object"], field.Kids):
            _update_field_font_recursive(kid, new_alias)
