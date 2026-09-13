# src/pdftl/utils/images/finders.py
from typing import TYPE_CHECKING
import math
import logging

from pdftl.utils.colorspaces import image_colorspace
from pdftl.utils.pdf_resources import get_resources
from pdftl.utils.graphics_state import GraphicsStateStack, multiply_matrices

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Inline images (BI ... ID ... EI) use abbreviated dictionary keys and
# abbreviated filter/colorspace NAMES per PDF 32000-1 Table 93/94 -- e.g.
# /F /Fl instead of /Filter /FlateDecode, /CS /RGB instead of /ColorSpace
# /DeviceRGB. These map the abbreviated spellings back to the same
# lowercase/full-name conventions _get_format()/entry["colorspace"] already
# use for XObject images, so a caller consuming this lister's output can't
# tell an inline image's format/colorspace field apart from an XObject
# image's by spelling alone.
#
# In practice pikepdf's PdfInlineImage.obj already normalizes /ColorSpace
# to a full device name (and normalizes short *keys* like /W -> /Width),
# but leaves /Filter spelled however the content stream wrote it -- so the
# colorspace map below mostly hits the pass-through branch, while the
# filter map is what actually does the work.
_INLINE_FILTER_ABBREVIATIONS = {
    "ahx": "asciihexdecode",
    "a85": "ascii85decode",
    "lzw": "lzwdecode",
    "fl": "flatedecode",
    "rl": "runlengthdecode",
    "ccf": "ccittfaxdecode",
    "dct": "dctdecode",
}

_INLINE_COLORSPACE_ABBREVIATIONS = {
    "g": "DeviceGray",
    "rgb": "DeviceRGB",
    "cmyk": "DeviceCMYK",
    "i": "Indexed",
}

# Guards _process_form_xobject's recursion against a cyclic or merely very
# deep chain of nested Form XObjects (a malformed or adversarial PDF can
# make a Form draw itself, directly or indirectly, via /Do). Past this
# depth, _handle_do_operator simply declines to descend into a further
# Form -- images already found at shallower depths are unaffected, only
# the (pathological) deeper nesting is left unvisited.
MAX_FORM_DEPTH = 12


def _read_stream_bytes(xobj):
    return len(xobj.read_raw_bytes())


def _page_content_stream_objects(page_obj):
    """Returns the raw Stream object(s) backing a page's /Contents, IN
    ORDER, without merging them into one combined token stream the way
    pikepdf.parse_content_stream(page) does internally.

    /Contents may be a single Stream or an Array of Streams. Each is kept
    distinct here so an inline image found inside it can be tagged with
    the objgen of the *exact* stream object it physically lives in --
    needed later to re-parse that same object and locate the same
    instruction for rewriting. Returns [] if the page has no /Contents
    at all (a blank/malformed page), matching the prior no-op behavior.
    """
    import pikepdf

    contents = page_obj.get("/Contents")
    if contents is None:
        return []
    if isinstance(contents, pikepdf.Array):
        return list(contents)
    return [contents]


def extract_pdf_images(pdf, target_pages: list[int]) -> list:
    """Crawls the specified pages to calculate bounding boxes and effective PPI
    for all drawn images. Returns a list of image metadata dictionaries.
    """
    result: list = []
    for page_num in target_pages:
        page = pdf.pages[page_num - 1]
        images_on_page: list = []
        identity_ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

        page_resources = get_resources(page)
        if page_resources is not None:
            # A single GraphicsStateStack is carried across every stream
            # object in /Contents (rather than a fresh one per stream) so
            # the CTM stays continuous over array boundaries -- a 'cm'
            # near the end of one segment must still apply to images
            # drawn at the start of the next one.
            gs_stack = GraphicsStateStack()
            gs_stack.current.ctm = identity_ctm
            for stream_obj in _page_content_stream_objects(page.obj):
                _parse_stream(
                    stream_obj,
                    page_resources,
                    gs_stack.current.ctm,
                    images_on_page,
                    gs_stack=gs_stack,
                    host_objgen=stream_obj.objgen,
                )

        if images_on_page:
            for img_meta in images_on_page:
                img_meta.update({"page": page_num})
            result.extend(images_on_page)

    return result


def _parse_stream(
    content_stream,
    resources,
    initial_ctm,
    image_list,
    depth: int = 0,
    gs_stack: "GraphicsStateStack | None" = None,
    host_objgen: tuple | None = None,
) -> None:
    import pikepdf

    # gs_stack is only created fresh here when the caller didn't already
    # have one to carry in (direct/test calls, and each Form XObject's
    # own call, which starts its own local coordinate space). When a
    # caller (extract_pdf_images, across sibling /Contents entries) does
    # pass one in, initial_ctm is ignored -- the stack already carries
    # the correct running CTM from whatever was parsed before it.
    if gs_stack is None:
        gs_stack = GraphicsStateStack()
        gs_stack.current.ctm = tuple(float(x) for x in initial_ctm)
    try:
        for idx, inst in enumerate(pikepdf.parse_content_stream(content_stream)):
            op = str(inst.operator)
            if op == "q":
                gs_stack.push()
            elif op == "Q":
                gs_stack.pop()
            elif op == "cm":
                gs_stack.current.apply_cm(inst.operands)
            elif op == "Do":
                obj_name_node = inst.operands[0]
                _handle_do_operator(
                    obj_name_node, resources, gs_stack.current.ctm, image_list, depth
                )
            elif op == "INLINE IMAGE":
                # pikepdf normalizes a whole BI/ID/EI block into ONE
                # instruction with this operator string -- there is no
                # /XObject resource lookup involved (the image dict and
                # data live inline in the instruction itself), so this is
                # handled directly rather than routed through
                # _handle_do_operator.
                _handle_inline_image(
                    inst.operands, gs_stack.current.ctm, image_list, host_objgen, idx
                )
    except (pikepdf.PdfError, KeyError, TypeError, ValueError, AttributeError) as err:
        logger.warning("Error parsing content stream: %s", err)


def _inline_header_dict(iimage):
    """The mapping to read an inline image's own parameter keys off of.

    A real pikepdf inline image operand is a `PdfInlineImage`, whose
    parsed BI-dict lives on its `.obj` attribute (with short keys like
    /W already normalized to /Width, though values such as /Filter keep
    whatever spelling the content stream used) -- `.get()` on the
    `PdfInlineImage` object itself is not defined. Anything else passed
    in (e.g. a plain dict-like test double) is assumed to already BE
    that mapping.
    """
    return getattr(iimage, "obj", iimage)


def _inline_dict_get(header, *keys):
    """Reads the first present key of `keys` off an inline image's own
    header mapping (see `_inline_header_dict`), trying both the full and
    abbreviated spelling (callers pass both, full name first)."""
    for key in keys:
        try:
            value = header.get(key)
        except (AttributeError, TypeError):
            value = None
        if value is not None:
            return value
    return None


def _read_inline_data_bytes(iimage, data_operand) -> int:
    """The inline image's own raw (still-filtered) byte count.

    pikepdf hands the whole BI/ID/EI block back as a single operand (a
    `PdfInlineImage`), not a separate data operand -- but callers/tests
    may still pass one, so that shape is tried first for compatibility.
    `PdfInlineImage.read_bytes()`/`get_stream_buffer()` round-trip
    through a throwaway one-page PDF sized to the image and raise for
    very small (<3pt) images, so they're not usable here; `.unparse()`
    has no such limit and always returns the literal
    ``b"BI\\n<params>\\nID\\n<data> EI"`` bytes, so the data length is
    recovered by slicing between the first "ID\\n" and the trailing
    " EI" unparse() always appends. Never raises -- 0 means "couldn't
    read it", matching _read_stream_bytes' own callers' fallback
    convention.
    """
    for candidate in (data_operand, getattr(iimage, "data", None)):
        if candidate is None:
            continue
        try:
            return len(bytes(candidate))
        except (TypeError, ValueError, AttributeError):
            continue

    try:
        blob = iimage.unparse()
        start = blob.index(b"ID\n") + len(b"ID\n")
        return max(len(blob) - len(b" EI") - start, 0)
    except (AttributeError, ValueError, TypeError):
        pass

    try:
        return len(bytes(iimage))
    except (TypeError, ValueError, AttributeError):
        return 0


def _get_inline_format(filt) -> str:
    """Like _get_format(), but for an inline image's own /F (or /Filter)
    value, which may use the PDF spec's abbreviated filter names (Table
    93) instead of the full ones an XObject's /Filter always uses."""
    import pikepdf

    if filt is None:
        return "unknown"
    if isinstance(filt, (pikepdf.Array, list, tuple)):
        filt = filt[0] if len(filt) else None
    if filt is None:
        return "unknown"
    name = str(filt).lstrip("/").lower()
    return _INLINE_FILTER_ABBREVIATIONS.get(name, name)


def _inline_colorspace(cs) -> str:
    """Like image_colorspace(), but for an inline image's own /CS (or
    /ColorSpace) value, which may be one of the spec's abbreviated device
    colorspace names (/G, /RGB, /CMYK, /I) rather than a full
    /DeviceGray-style name or a /Resources /ColorSpace lookup. Inline
    images cannot reference an indirect, named colorspace resource the
    way an XObject can, so there is no resources-dict lookup to do here.
    (In practice pikepdf's PdfInlineImage.obj already expands this to a
    full device name, so this is mostly a pass-through / defensive path.)
    """
    if cs is None:
        return "unknown"
    name = str(cs).lstrip("/")
    return _INLINE_COLORSPACE_ABBREVIATIONS.get(name.lower(), name)


def _handle_inline_image(
    operands,
    ctm,
    image_list,
    host_objgen: tuple | None = None,
    instruction_index: int | None = None,
) -> None:
    if not operands:
        return
    iimage = operands[0]
    data_operand = operands[1] if len(operands) > 1 else None
    _extract_inline_image_metadata(
        iimage, data_operand, ctm, image_list, host_objgen, instruction_index
    )


def _extract_inline_image_metadata(
    iimage,
    data_operand,
    ctm,
    image_list,
    host_objgen: tuple | None = None,
    instruction_index: int | None = None,
) -> None:
    """Builds a metadata entry for one inline image, mirroring
    _extract_image_metadata()'s shape as closely as an inline image's own
    (more limited) data allows. There is no indirect object behind an
    inline image -- its bytes live in the content stream itself -- so
    there is no `obj_id`/`xobj` to hand back to a downstream mutator;
    `inline: True` and the absence of those two keys is how a caller
    distinguishes this entry shape from an XObject image's.

    `host_objgen` (the objgen of the physical Stream object this BI/ID/EI
    block was parsed out of) and `instruction_index` (its position within
    that stream's own instruction list) together stand in for the
    missing obj_id/xobj -- they're what a rewriter needs to re-parse the
    same stream and locate the same instruction later. Both are None when
    the caller didn't supply them (e.g. direct/unit-test calls to
    _handle_inline_image), which a rewriter must treat as "not
    rewritable" rather than guessing.
    """
    bbox = _calculate_bbox(ctm)
    stream_bytes = _read_inline_data_bytes(iimage, data_operand)

    header = _inline_header_dict(iimage)
    width_px = int(_inline_dict_get(header, "/Width", "/W") or 0)
    height_px = int(_inline_dict_get(header, "/Height", "/H") or 0)
    bits = int(_inline_dict_get(header, "/BitsPerComponent", "/BPC") or 8)

    a, b, c, d, _, _ = ctm
    drawn_width_pts = math.hypot(a, b)
    drawn_height_pts = math.hypot(c, d)

    entry = {
        "name": None,
        "inline": True,
        "host_objgen": tuple(host_objgen) if host_objgen is not None else None,
        "instruction_index": instruction_index,
        "bbox": bbox,
        "width_px": width_px,
        "height_px": height_px,
        "ppi_x": round(width_px / drawn_width_pts * 72) if drawn_width_pts > 0 else 0,
        "ppi_y": round(height_px / drawn_height_pts * 72) if drawn_height_pts > 0 else 0,
        "colorspace": _inline_colorspace(_inline_dict_get(header, "/ColorSpace", "/CS")),
        "bits": bits,
        "stream_bytes": stream_bytes,
        "format": _get_inline_format(_inline_dict_get(header, "/Filter", "/F")),
    }
    image_list.append(entry)


def _handle_do_operator(obj_name_node, resources, current_ctm, image_list, depth: int = 0) -> None:
    if resources is None or "/XObject" not in resources:
        return

    xobjects = resources["/XObject"]
    if obj_name_node not in xobjects:
        return

    xobj = xobjects[obj_name_node]
    subtype = str(xobj.get("/Subtype", ""))
    obj_name_str = str(obj_name_node)

    if subtype == "/Image":
        _extract_image_metadata(xobj, obj_name_str, current_ctm, resources, image_list)
    elif subtype == "/Form":
        if depth >= MAX_FORM_DEPTH:
            return
        _process_form_xobject(xobj, resources, current_ctm, image_list, depth)


def _process_form_xobject(xobj, parent_resources, current_ctm, image_list, depth: int = 0) -> None:
    form_matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    if "/Matrix" in xobj:
        form_matrix = tuple(float(x) for x in xobj.Matrix)

    form_ctm = multiply_matrices(form_matrix, tuple(current_ctm))
    form_resources = xobj.get("/Resources", parent_resources)

    # Forms are always a single stream object (never a /Contents array),
    # so xobj.objgen is unambiguously this content's host.
    _parse_stream(xobj, form_resources, form_ctm, image_list, depth + 1, host_objgen=xobj.objgen)


def _extract_mask_metadata(xobj, drawn_width_pts: float, drawn_height_pts: float) -> dict | None:
    """Extracts metadata for an image's /Mask or /SMask stencil, if present.

    The mask has no CTM of its own -- it is always drawn through the same
    placement matrix as its parent image (PDF 32000-1, 8.9.6.2/8.9.6.3), so
    we reuse the parent's drawn size in points to derive the mask's own
    effective ppi, which can legitimately differ from the parent's (e.g. a
    300ppi stencil riding on a 75ppi color layer).

    Returns None if there is no mask, or if /Mask is a color-key masking
    array rather than a stencil stream (PDF 8.9.6.4 -- an array of ranges,
    not an image XObject, and has no meaningful width/height/format of its
    own).
    """
    import pikepdf

    smask = xobj.get("/SMask")
    mask_key = (
        "/SMask" if smask is not None else "/Mask" if xobj.get("/Mask") is not None else None
    )
    if mask_key is None:
        return None

    mask_xobj = smask if mask_key == "/SMask" else xobj.get("/Mask")
    if not isinstance(mask_xobj, pikepdf.Stream):
        # Color-key masking: an Array of component ranges, not a stencil image.
        return None

    try:
        stream_bytes = _read_stream_bytes(mask_xobj)
    except (pikepdf.PdfError, ValueError):
        stream_bytes = 0

    width_px = int(mask_xobj.get("/Width", 0))
    height_px = int(mask_xobj.get("/Height", 0))

    return {
        "role": "smask" if mask_key == "/SMask" else "stencil",
        "obj_id": mask_xobj.objgen[0],
        "width_px": width_px,
        "height_px": height_px,
        "ppi_x": round(width_px / drawn_width_pts * 72) if drawn_width_pts > 0 else 0,
        "ppi_y": round(height_px / drawn_height_pts * 72) if drawn_height_pts > 0 else 0,
        "bits": int(mask_xobj.get("/BitsPerComponent", 1)),
        "stream_bytes": stream_bytes,
        "format": _get_format(mask_xobj),
    }


def _extract_image_metadata(xobj, obj_name_str, ctm, resources, image_list) -> None:
    import pikepdf

    bbox = _calculate_bbox(ctm)

    try:
        stream_bytes = _read_stream_bytes(xobj)
    except (pikepdf.PdfError, ValueError):
        stream_bytes = 0

    width_px = int(xobj.get("/Width", 0))
    height_px = int(xobj.get("/Height", 0))
    a, b, c, d, _, _ = ctm
    drawn_width_pts = math.hypot(a, b)
    drawn_height_pts = math.hypot(c, d)

    entry = {
        "name": obj_name_str,
        "obj_id": xobj.objgen[0],
        "bbox": bbox,
        "width_px": width_px,
        "height_px": height_px,
        "ppi_x": round(width_px / drawn_width_pts * 72) if drawn_width_pts > 0 else 0,
        "ppi_y": round(height_px / drawn_height_pts * 72) if drawn_height_pts > 0 else 0,
        "colorspace": image_colorspace(xobj, resources, pikepdf),
        "bits": int(xobj.get("/BitsPerComponent", 8)),
        "stream_bytes": stream_bytes,
        "format": _get_format(xobj),
        "xobj": xobj,  # Preserved so downstream operations can modify the exact stream
    }

    mask_meta = _extract_mask_metadata(xobj, drawn_width_pts, drawn_height_pts)
    if mask_meta is not None:
        entry["mask"] = mask_meta

    image_list.append(entry)


def _calculate_bbox(ctm) -> list[float]:
    a, b, c, d, e, f = ctm
    x_coords = [e, a + e, c + e, a + c + e]
    y_coords = [f, b + f, d + f, b + d + f]
    return [
        round(min(x_coords), 2),
        round(min(y_coords), 2),
        round(max(x_coords), 2),
        round(max(y_coords), 2),
    ]


def _get_format(xobj) -> str:
    import pikepdf

    f = xobj.get("/Filter")
    if f is None:
        return "unknown"
    if isinstance(f, pikepdf.Array):
        f = f[0]
    return str(f).lstrip("/").lower()
