# src/pdftl/utils/image_utils.py

from contextlib import suppress
import logging

logger = logging.getLogger(__name__)


def _multiply_matrices(m1, m2):
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return [
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    ]


def _calculate_bbox(ctm):
    a, b, c, d, e, f = ctm
    x_coords = [e, a + e, c + e, a + c + e]
    y_coords = [f, b + f, d + f, b + d + f]
    return [
        round(min(x_coords), 2),
        round(min(y_coords), 2),
        round(max(x_coords), 2),
        round(max(y_coords), 2),
    ]


def _get_format(xobj, pikepdf):
    f = xobj.get("/Filter")
    if f is None:
        return "unknown"
    if isinstance(f, pikepdf.Array):
        f = f[0]
    return str(f).lstrip("/").lower()


def _read_stream_bytes(xobj):
    return len(xobj.read_raw_bytes())


def _extract_image_metadata(xobj, obj_name_str, ctm, resources, image_list, pikepdf):
    from pdftl.utils.colorspaces import image_colorspace

    bbox = _calculate_bbox(ctm)

    try:
        stream_bytes = _read_stream_bytes(xobj)
    except (pikepdf.PdfError, ValueError):
        stream_bytes = 0

    width_px = int(xobj.get("/Width", 0))
    height_px = int(xobj.get("/Height", 0))
    bbox_width = bbox[2] - bbox[0]
    bbox_height = bbox[3] - bbox[1]

    image_list.append(
        {
            "name": obj_name_str,
            "obj_id": xobj.objgen[0],
            "bbox": bbox,
            "width_px": width_px,
            "height_px": height_px,
            "ppi_x": round(width_px / bbox_width * 72) if bbox_width > 0 else 0,
            "ppi_y": round(height_px / bbox_height * 72) if bbox_height > 0 else 0,
            "colorspace": image_colorspace(xobj, resources, pikepdf),
            "bits": int(xobj.get("/BitsPerComponent", 8)),
            "stream_bytes": stream_bytes,
            "format": _get_format(xobj, pikepdf),
            "xobj": xobj,  # Preserved so downstream operations can modify the exact stream
        }
    )


def _process_form_xobject(xobj, parent_resources, current_ctm, image_list):
    form_matrix = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    if "/Matrix" in xobj:
        form_matrix = [float(x) for x in xobj.Matrix]

    form_ctm = _multiply_matrices(form_matrix, current_ctm)
    form_resources = xobj.get("/Resources", parent_resources)

    _parse_stream(xobj, form_resources, form_ctm, image_list)


def _handle_do_operator(obj_name_node, resources, current_ctm, image_list, pikepdf):
    if resources is None or "/XObject" not in resources:
        return

    xobjects = resources["/XObject"]
    if obj_name_node not in xobjects:
        return

    xobj = xobjects[obj_name_node]
    subtype = str(xobj.get("/Subtype", ""))
    obj_name_str = str(obj_name_node)

    if subtype == "/Image":
        _extract_image_metadata(xobj, obj_name_str, current_ctm, resources, image_list, pikepdf)
    elif subtype == "/Form":
        _process_form_xobject(xobj, resources, current_ctm, image_list)


def _parse_stream(content_stream, resources, initial_ctm, image_list):
    ctm_stack = []
    current_ctm = list(initial_ctm)
    import pikepdf

    try:
        for inst in pikepdf.parse_content_stream(content_stream):
            op = str(inst.operator)
            if op == "q":
                ctm_stack.append(list(current_ctm))
            elif op == "Q":
                if ctm_stack:
                    current_ctm = ctm_stack.pop()
            elif op == "cm":
                operand_matrix = [float(x) for x in inst.operands]
                current_ctm = _multiply_matrices(operand_matrix, current_ctm)
            elif op == "Do":
                obj_name_node = inst.operands[0]
                _handle_do_operator(obj_name_node, resources, current_ctm, image_list, pikepdf)
    except (pikepdf.PdfError, KeyError, TypeError, ValueError, AttributeError) as err:
        logger.warning("Error parsing content stream: %s", err)


def extract_pdf_images(pdf, target_pages: list[int]) -> list:
    """
    Crawls the specified pages to calculate bounding boxes and effective PPI for all drawn images.
    Returns a list of image metadata dictionaries.
    """
    result: list = []
    for page_num in target_pages:
        page = pdf.pages[page_num - 1]
        images_on_page = []
        identity_ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        with suppress(AttributeError):
            _parse_stream(page, page.Resources, identity_ctm, images_on_page)

        if images_on_page:
            for x in images_on_page:
                x.update({"page": page_num})
            result.extend(images_on_page)

    return result
