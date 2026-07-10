# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/stamp_fields.py

"""Stamp a PDF page into an interactive form field's Appearance Stream."""

import logging
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.io_helpers import smart_pikepdf_open
from pdftl.utils.keyval_parser import parse_keyval_list

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)

_STAMP_FIELDS_LONG_DESC = """
The `stamp_fields` operation places pages from an external PDF into interactive
form fields' Appearance Streams (`/AP`). This permanently applies a visual overlay
(like a signature, seal, or stamp) directly onto the field areas.

It applies page 1 of the source PDF to the first specified field, page 2 to the
second field, and so on. If there are more fields than pages in the source PDF,
the last page of the source is repeated for the remaining fields.

Because form field names are unique across the entire document, you do not need
to specify a page number. Nested fields can be targeted using dotted notation
(e.g., `Parent.Child`).

### Syntax

`<input> stamp_fields <source.pdf> <field_name>... [<option>=<value>]... output <out.pdf>`

### Options

Options are provided as flat `key=value` pairs following the target field names
and apply globally to all fields stamped in the command.

* `scale=<stretch|fit|width|height|<float>>`: Controls how the stamp page is scaled
  within the field's bounding box.
  * `fit` (default): Uniformly scales the stamp to fit inside the widget while
    maintaining its original aspect ratio.
  * `stretch`: Scales the stamp independently on the X and Y axes
    to exactly fill the widget.
  * `width`: Uniformly scales the stamp to match the exact width of the widget.
  * `height`: Uniformly scales the stamp to match the exact height of the widget.
  * `<float>`: Uniformly fits the stamp inside the widget (defaulting to the `fit` mode)
    and applies this positive scale multiplier (e.g. `scale=0.8` for 80% size).
* `align=<keyword>`: Visual alignment of the stamp within the field box.
  Format can be `v-h` or `center`, where `v` is `top`, `mid`, or `bottom` and
  `h` is `left`, `center`, or `right`. Defaults to `mid-center`
  (can also be specified as `center`).
* `opacity=<float>`: Transparency setting between 0.0 (transparent) and
  1.0 (opaque). Defaults to 1.0.
"""

_STAMP_FIELDS_EXAMPLES = [
    {
        "cmd": "in.pdf stamp_fields sig.pdf signature output out.pdf",
        "desc": "Stamp sig.pdf into the field named 'signature' using default fit-centering.",
    },
    {
        "cmd": (
            "in.pdf stamp_fields sig.pdf EmploymentForm.Sig "
            "scale=0.8 align=bottom-right opacity=0.8 output out.pdf"
        ),
        "desc": (
            "Stamp into a nested field, scaled uniformly to 80%, "
            "aligned to the bottom right and 80% opaque."
        ),
    },
    {
        "cmd": ("in.pdf stamp_fields stamps.pdf Sig1 Sig2 scale=fit output out.pdf"),
        "desc": "Stamp page 1 of stamps.pdf onto Sig1, and page 2 onto Sig2.",
    },
]


def _parse_args(args: list[str]) -> tuple[str, list[str], dict]:
    """Parses <source.pdf> <fields>... [<options>=<value>]."""
    bare_tokens: list[str] = []
    options = parse_keyval_list(
        args,
        bare_tokens=bare_tokens,
        allowed_keys=["scale", "align", "opacity"],
        lowercase_values=True,
        context="stamp_fields",
    )

    if not bare_tokens:
        raise InvalidArgumentError(
            f"Missing arguments. Expected format: {STAMP_FIELDS_ARG_STRING}"
        )

    source_path = bare_tokens[0]
    fields = bare_tokens[1:]

    if not fields:
        raise InvalidArgumentError("Missing target field names.")

    return source_path, fields, options


def _get_field_widgets(field_obj, pikepdf, visited=None) -> list:
    """Recursively gather all visual widget annotations for a field."""
    if visited is None:
        visited = set()

    if hasattr(field_obj, "objgen"):
        if field_obj.objgen in visited:
            return []
        visited.add(field_obj.objgen)

    widgets = []
    if "/Rect" in field_obj:
        widgets.append(field_obj)
    elif "/Kids" in field_obj:
        for kid in field_obj.Kids:
            widgets.extend(_get_field_widgets(kid, pikepdf, visited))
    return widgets


def _parse_align(align_str: str) -> tuple[str, str]:
    """Parses align option to extract vertical and horizontal alignment anchors."""
    v_align = "mid"
    h_align = "center"

    align_str = align_str.lower().strip()
    if align_str == "center":
        return v_align, h_align

    parts = align_str.split("-")
    v_opts = {"top", "mid", "bottom"}
    h_opts = {"left", "center", "right"}

    expected_format = "Expected combination of top/mid/bottom and left/center/right"

    if len(parts) == 2:
        p1, p2 = parts[0], parts[1]
        if p1 in v_opts and p2 in h_opts:
            return p1, p2
        if p1 in h_opts and p2 in v_opts:
            return p2, p1
        raise InvalidArgumentError(f"Invalid align values: '{align_str}'. {expected_format}.")

    if len(parts) == 1:
        p = parts[0]
        if p in v_opts:
            return p, h_align
        if p in h_opts:
            return v_align, p
        raise InvalidArgumentError(f"Invalid align values: '{align_str}'. {expected_format}.")

    raise InvalidArgumentError(f"Invalid align format: '{align_str}'. {expected_format}.")


def _transformation_parameters(
    scale_mode, user_scale, v_align, h_align, src_bbox, widget_w, widget_h
):
    src_w = float(src_bbox[2] - src_bbox[0])
    src_h = float(src_bbox[3] - src_bbox[1])

    # 1. Base Scale Factor Determination
    if scale_mode == "stretch":
        scale_x = (widget_w / src_w if src_w else 1.0) * user_scale
        scale_y = (widget_h / src_h if src_h else 1.0) * user_scale
    else:
        raw_scale_x = widget_w / src_w if src_w else 1.0
        raw_scale_y = widget_h / src_h if src_h else 1.0

        if scale_mode == "fit":
            scale = min(raw_scale_x, raw_scale_y)
        elif scale_mode == "width":
            scale = raw_scale_x
        else:  # height
            scale = raw_scale_y

        scale_x = scale_y = scale * user_scale

    # 2. Alignment Offset Calculations
    offset_x, offset_y = _get_alignment_offsets(
        h_align, v_align, src_w * scale_x, src_h * scale_y, widget_w, widget_h
    )

    # Subtract the scaled minimum bounds of the source BBox to normalize its origin to (0,0)
    trans_x = offset_x - float(src_bbox[0]) * scale_x
    trans_y = offset_y - float(src_bbox[1]) * scale_y

    return scale_x, scale_y, trans_x, trans_y


def _get_alignment_offsets(h_align, v_align, target_w, target_h, widget_w, widget_h):
    # Horizontal position offsets
    if h_align == "left":
        offset_x = 0.0
    elif h_align == "right":
        offset_x = widget_w - target_w
    else:  # center
        offset_x = (widget_w - target_w) / 2.0

    # Vertical position offsets
    if v_align == "bottom":
        offset_y = 0.0
    elif v_align == "top":
        offset_y = widget_h - target_h
    else:  # mid
        offset_y = (widget_h - target_h) / 2.0

    return offset_x, offset_y


def _get_scale_mode(options):
    scale_opt = options.get("scale", "fit")
    allowed_modes = {"stretch", "fit", "width", "height"}
    if scale_opt in allowed_modes:
        return scale_opt, 1.0
    try:
        user_scale = float(scale_opt)
        if user_scale <= 0:
            raise ValueError("Scale must be greater than 0")
        return "zoom", user_scale
    except ValueError as exc:
        logger.debug("Scale validation failed: %s", exc)
        raise InvalidArgumentError(
            f"Invalid scale '{scale_opt}'. "
            "Expected 'stretch', 'fit', 'width', 'height', or a positive number."
        ) from exc


def _build_appearance_stream(
    pdf, foreign_xobj, widget_w: float, widget_h: float, options: dict, pikepdf
):
    """Constructs the Form XObject that will act as the field's /AP."""
    scale_mode, scale_factor = _get_scale_mode(options)
    align_str = options.get("align", "mid-center")
    v_align, h_align = _parse_align(align_str)
    src_bbox = foreign_xobj.BBox

    scale_x, scale_y, trans_x, trans_y = _transformation_parameters(
        scale_mode, scale_factor, v_align, h_align, src_bbox, widget_w, widget_h
    )

    ap_stream = pdf.make_stream(b"")
    resources = pikepdf.Dictionary({"/XObject": {"/SrcPageForm": foreign_xobj}})

    content_lines = [b"q"]

    opacity = 1.0
    if "opacity" in options:
        try:
            opacity = float(options["opacity"])
            if not (0.0 <= opacity <= 1.0):
                raise ValueError("Opacity must be between 0.0 and 1.0")
        except ValueError as exc:
            logger.debug("Opacity validation failed: %s", exc)
            raise InvalidArgumentError(
                f"Invalid opacity '{options['opacity']}'. Expected a number between 0.0 and 1.0."
            ) from exc

    if opacity < 1.0:
        resources["/ExtGState"] = pikepdf.Dictionary(
            {
                "/GS0": pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/ExtGState"),
                        "/ca": opacity,
                        "/CA": opacity,
                    }
                )
            }
        )
        content_lines.append(b"/GS0 gs")

    cm_str = f"{scale_x:.4f} 0 0 {scale_y:.4f} {trans_x:.4f} {trans_y:.4f} cm"
    # Apply the mapping matrix and execute the embedded form
    content_lines.append(cm_str.encode("ascii"))
    content_lines.append(b"/SrcPageForm Do")
    content_lines.append(b"Q")

    ap_stream.write(b"\n".join(content_lines))
    ap_stream.update(
        {
            "/Type": pikepdf.Name("/XObject"),
            "/Subtype": pikepdf.Name("/Form"),
            "/BBox": pikepdf.Array([0, 0, widget_w, widget_h]),
            "/Resources": resources,
        }
    )

    return ap_stream


def _stamp_single_field(pdf, form, field_name: str, foreign_xobj, options: dict, pikepdf):
    """Locates the field, embeds the source stamp, and injects the Appearance Stream."""
    target_field = next((f for f in form if f.fully_qualified_name == field_name), None)
    if not target_field:
        raise InvalidArgumentError(f"Form field '{field_name}' not found in the document.")

    widgets = _get_field_widgets(target_field.obj, pikepdf)
    if not widgets:
        raise InvalidArgumentError(f"Form field '{field_name}' has no visual widgets to stamp.")

    for widget in widgets:
        rect = widget.Rect
        x1, y1, x2, y2 = [float(x) for x in rect]
        widget_w = x2 - x1
        widget_h = y2 - y1

        ap_stream = _build_appearance_stream(
            pdf, foreign_xobj, widget_w, widget_h, options, pikepdf
        )

        if "/AP" not in widget:
            widget.AP = pikepdf.Dictionary()
        widget.AP.N = ap_stream


STAMP_FIELDS_ARG_STRING = "<source.pdf> <field_name>... [<option>=<value>]..."


@register_operation(
    "stamp_fields",
    tags=["in_place", "forms", "stamp", "overlay"],
    type="single input operation",
    desc="Stamp PDF content into form fields",
    long_desc=_STAMP_FIELDS_LONG_DESC,
    usage=f"<input> stamp_fields {STAMP_FIELDS_ARG_STRING} output <file>",
    examples=_STAMP_FIELDS_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def stamp_fields(pdf: "pikepdf.Pdf", args: list[str]) -> OpResult:
    """Entry point for the stamp_fields operation."""
    if not args:
        raise InvalidArgumentError(
            f"Missing arguments. Expected format: {STAMP_FIELDS_ARG_STRING}"
        )

    import pikepdf

    try:
        from pikepdf.form import Form

        form = Form(pdf)
        fields = list(form)
    except pikepdf.PdfError as exc:
        # Handle edge cases where pikepdf Form parsing throws due to broken AcroForm.
        logger.debug("Error initializing Form or iterating fields: %s", exc)
        fields = []

    if not fields:
        raise InvalidArgumentError("The input PDF does not contain any form fields.")

    source_path, target_fields, options = _parse_args(args)

    try:
        with smart_pikepdf_open(source_path) as stamp_pdf:
            if not stamp_pdf.pages:
                raise InvalidArgumentError(f"Source PDF '{source_path}' has no pages.")

            for i, field_name in enumerate(target_fields):
                # Multistamp behavior: clamp to the last page if there are more fields than pages
                page_idx = min(i, len(stamp_pdf.pages) - 1)
                stamp_page = stamp_pdf.pages[page_idx]
                foreign_xobj = pdf.copy_foreign(stamp_page.as_form_xobject())

                _stamp_single_field(pdf, fields, field_name, foreign_xobj, options, pikepdf)

    except OSError as exc:
        logger.debug("Failed to open source stamp PDF: %s", exc)
        raise InvalidArgumentError(f"Could not open source PDF '{source_path}': {exc}") from exc

    return OpResult(success=True, pdf=pdf)
