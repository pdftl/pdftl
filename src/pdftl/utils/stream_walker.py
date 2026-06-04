# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/stream_walker.py

"""Content stream traversal and graphics state management with lineage tracing."""

import logging
import copy

from pdftl.utils.colorspaces import (
    family_from_device_op,
    resolve_named_cs,
    resolve_shading_cs,
    image_colorspace,
)

logger = logging.getLogger(__name__)


# Graphics state entry initialization
def DEFAULT_GS():
    return {
        "fill_cs": {"family": "unknown"},
        "stroke_cs": {"family": "unknown"},
        "rendering_intent": "RelativeColorimetric",
        "overprint_fill": False,
        "overprint_stroke": False,
        "overprint_mode": 0,
        "blend_mode": "Normal",
    }


def _safe_operands(operands) -> list:
    """Safely extract and format operands (numbers or names) for JSON output."""
    safe_list = []
    for val in operands:
        try:
            # Round numbers to 4 decimals to clean up floating point noise (e.g., 0.999999)
            safe_list.append(round(float(val), 4))
        except (TypeError, ValueError):
            safe_list.append(str(val))
    return safe_list


def process_cs_op(
    op: str,
    operands,
    resources,
    pikepdf_module,
    gs: dict,
    detail: dict,
    op_idx: int,
    in_text: bool,
    stream_id: str,
):
    """Handle explicit color space activation operators (cs/CS) with resource tracking."""
    if not operands:
        return
    try:
        name = str(operands[0])
        resolved = resolve_named_cs(name, resources, pikepdf_module)
        if resolved is None:
            return
        resolved = resolved.copy()
    except (KeyError, ValueError, TypeError, AttributeError) as err:
        logger.debug("Failed to resolve named colorspace %s: %s", operands[0], err)
        return
    except pikepdf_module.PdfError as err:
        logger.debug("Failed to resolve named colorspace %s: %s", operands[0], err)
        return

    # Inject traceability
    resolved["resource_id"] = name
    resolved["operator_index"] = op_idx
    resolved["stream_id"] = stream_id
    resolved["context"] = "text" if in_text else "vector"

    if op == "cs":
        gs["fill_cs"] = resolved
        detail["fills"].append(resolved)
    else:
        gs["stroke_cs"] = resolved
        detail["strokes"].append(resolved)


def process_shorthand_op(
    op: str, operands, gs: dict, detail: dict, op_idx: int, in_text: bool, stream_id: str
):
    """Handle device-space fallback shorthand operators (g/rg/k/G/RG/K)."""
    cs = {
        "family": family_from_device_op(op),
        "operator": op,
        "operator_index": op_idx,
        "context": "text" if in_text else "vector",
        "stream_id": stream_id,
    }
    if operands:
        cs["operands"] = _safe_operands(operands)

    if op.islower():
        gs["fill_cs"] = cs
        detail["fills"].append(cs)
    else:
        gs["stroke_cs"] = cs
        detail["strokes"].append(cs)


def _execute_pattern_walk(
    pattern_obj, resources, pikepdf_module, detail, seen_stream_ids, full, new_stream_id, obj_id
):
    """Encapsulated context wrapper for pattern target graph evaluation."""
    if obj_id is not None:
        if obj_id in seen_stream_ids:
            return
        seen_stream_ids.add(obj_id)

    try:
        pattern_resources = None
        if hasattr(pattern_obj, "get"):
            pattern_resources = pattern_obj.get("/Resources")

        if pattern_resources is None:
            pattern_resources = resources

        walk_stream(
            pattern_obj,
            pattern_resources,
            pikepdf_module,
            detail=detail,
            seen_stream_ids=seen_stream_ids,
            full=full,
            stream_id=new_stream_id,
        )
    finally:
        if obj_id is not None:
            seen_stream_ids.discard(obj_id)


def recurse_pattern(
    pattern_name: str, resources, pikepdf_module, detail: dict, seen_stream_ids: set, full: bool
):
    """Secure recursive tracking context for Tiling Patterns triggered via scn/SCN."""
    if resources is None:
        return

    try:
        patterns = resources.get("/Pattern")
        if patterns is None:
            return
        pattern_obj = patterns.get(pattern_name)
        if pattern_obj is None:
            return
    except (AttributeError, TypeError):
        return

    try:
        pattern_type_obj = pattern_obj.get("/PatternType")
        pattern_type = int(pattern_type_obj) if pattern_type_obj is not None else None

        if pattern_type != 1:
            return

        obj_id = pattern_obj.objgen[0] if hasattr(pattern_obj, "objgen") else None
        new_stream_id = f"{obj_id} 0 obj" if obj_id is not None else str(pattern_name)
        _execute_pattern_walk(
            pattern_obj,
            resources,
            pikepdf_module,
            detail,
            seen_stream_ids,
            full,
            new_stream_id,
            obj_id,
        )
    except (ValueError, TypeError, AttributeError) as err:
        logger.debug("Error processing recursive pattern %s: %s", pattern_name, err)
    except pikepdf_module.PdfError as err:
        logger.debug("Error processing recursive pattern %s: %s", pattern_name, err)


def process_color_val_op(
    op: str,
    operands,
    resources,
    pikepdf_module,
    gs: dict,
    detail: dict,
    seen_stream_ids: set,
    full: bool,
    op_idx: int,
    in_text: bool,
    stream_id: str,
):
    """Handle coordinate color modifications (sc/scn/SC/SCN) and intercept pattern triggers."""
    if not full:
        return

    if op in ("sc", "scn"):
        cs = dict(gs.get("fill_cs", {"family": "unknown"}))
    else:
        cs = dict(gs.get("stroke_cs", {"family": "unknown"}))

    cs["operator"] = op
    cs["operator_index"] = op_idx
    cs["stream_id"] = stream_id
    cs["context"] = "text" if in_text else "vector"

    if operands:
        cs["operands"] = _safe_operands(operands)

        # SCN/scn interpretation depends on current graphics state color space.
        # Only when the active color space is Pattern do we treat the final operand
        # as a pattern name; otherwise this behaves like SC/sc (normal color set).
        # This code follows spec-compliant behavior (no heuristic recovery).
        if op in ("scn", "SCN") and cs.get("family") == "pattern":
            pattern_name = str(operands[-1])
            if not pattern_name.startswith("/"):
                pattern_name = "/" + pattern_name
            recurse_pattern(pattern_name, resources, pikepdf_module, detail, seen_stream_ids, full)

    if op in ("sc", "scn"):
        detail["fills"].append(cs)
    else:
        detail["strokes"].append(cs)


def recurse_form_xobject(
    xobj, obj_id, resources, pikepdf_module, detail, seen_stream_ids, full, stream_id
):
    """Secure recursive tracking context for nested Form XObjects."""
    if obj_id is not None:
        if obj_id in seen_stream_ids:
            return
        seen_stream_ids.add(obj_id)

    try:
        form_resources = None
        if hasattr(xobj, "get"):
            form_resources = xobj.get("/Resources")

        if form_resources is None:
            form_resources = resources

        walk_stream(
            xobj,
            form_resources,
            pikepdf_module,
            detail=detail,
            seen_stream_ids=seen_stream_ids,
            full=full,
            stream_id=stream_id,
        )
    finally:
        if obj_id is not None:
            seen_stream_ids.discard(obj_id)


def _extract_xobject(obj_name, resources):
    """Safely lookup an XObject variant configuration reference."""
    try:
        xobjects = resources.get("/XObject")
        if xobjects is None:
            return None
        if hasattr(xobjects, "get"):
            return xobjects.get(obj_name)
        return xobjects[obj_name] if obj_name in xobjects else None
    except (AttributeError, TypeError, KeyError):
        return None


def _handle_image_subtype(
    xobj, resources, pikepdf_module, detail, name_str, op_idx, stream_id, obj_id
):
    """Resolve and map color spaces specifically bound to embedded raster image objects."""
    try:
        img_cs = image_colorspace(xobj, resources, pikepdf_module).copy()
    except (KeyError, ValueError, TypeError, AttributeError) as err:
        logger.debug("Error resolving colorspace for image %s: %s", name_str, err)
        img_cs = {"family": "unknown"}
    except pikepdf_module.PdfError as err:
        logger.debug("Error resolving colorspace for image %s: %s", name_str, err)
        img_cs = {"family": "unknown"}

    img_cs["resource_id"] = name_str
    img_cs["operator_index"] = op_idx
    img_cs["context"] = "image"
    img_cs["stream_id"] = stream_id
    if obj_id is not None:
        img_cs["pdf_object_id"] = f"{obj_id} 0 obj"

    detail["images"].append(img_cs)


def handle_do(
    obj_name,
    resources,
    pikepdf_module,
    *,
    detail,
    seen_stream_ids,
    full,
    op_idx: int,
    stream_id: str,
):
    """Handle a Do operator: dispatch safely to image or form handles with reference maps."""
    if resources is None:
        return

    xobj = _extract_xobject(obj_name, resources)
    if xobj is None:
        return

    subtype = ""
    if hasattr(xobj, "get"):
        subtype_obj = xobj.get("/Subtype")
        subtype = str(subtype_obj) if subtype_obj is not None else ""

    if not subtype.startswith("/"):
        subtype = "/" + subtype

    obj_id = xobj.objgen[0] if hasattr(xobj, "objgen") else None
    name_str = str(obj_name)

    if subtype == "/Image":
        _handle_image_subtype(
            xobj, resources, pikepdf_module, detail, name_str, op_idx, stream_id, obj_id
        )
    elif subtype == "/Form":
        new_stream_id = f"{obj_id} 0 obj" if obj_id is not None else str(obj_name)
        recurse_form_xobject(
            xobj, obj_id, resources, pikepdf_module, detail, seen_stream_ids, full, new_stream_id
        )


def _process_shading_op(operands, resources, pikepdf_module, detail, op_idx, stream_id):
    """Process explicit vector smooth shading operator targets."""
    try:
        shading_name = str(operands[0])
        cs = resolve_shading_cs(operands[0], resources, pikepdf_module).copy()
        cs["resource_id"] = shading_name
        cs["operator_index"] = op_idx
        cs["stream_id"] = stream_id
        cs["context"] = "vector"
        detail["patterns"].append(cs)
    except (KeyError, ValueError, TypeError, AttributeError) as err:
        logger.debug("Failed to resolve shading operator target: %s", err)
    except pikepdf_module.PdfError as err:
        logger.debug("Failed to resolve shading operator target: %s", err)


def process_operator(
    op: str,
    operands,
    resources,
    pikepdf_module,
    gs: dict,
    gs_stack: list,
    detail: dict,
    seen_stream_ids: set,
    full: bool,
    op_idx: int,
    in_text: bool,
    stream_id: str,
) -> dict:
    """Dispatch a single content stream operator to the appropriate handler."""
    if op == "q":
        gs_stack.append(copy.deepcopy(gs))
    elif op == "Q" and gs_stack:
        new_gs = gs_stack.pop()
        gs.clear()
        gs.update(new_gs)
    elif op in ("cs", "CS"):
        process_cs_op(
            op, operands, resources, pikepdf_module, gs, detail, op_idx, in_text, stream_id
        )
    elif op in ("g", "rg", "k", "G", "RG", "K"):
        process_shorthand_op(op, operands, gs, detail, op_idx, in_text, stream_id)
    elif op in ("sc", "scn", "SC", "SCN"):
        process_color_val_op(
            op,
            operands,
            resources,
            pikepdf_module,
            gs,
            detail,
            seen_stream_ids,
            full,
            op_idx,
            in_text,
            stream_id,
        )
    elif op == "Do" and operands:
        handle_do(
            operands[0],
            resources,
            pikepdf_module,
            detail=detail,
            seen_stream_ids=seen_stream_ids,
            full=full,
            op_idx=op_idx,
            stream_id=stream_id,
        )
    elif op == "sh" and operands:
        _process_shading_op(operands, resources, pikepdf_module, detail, op_idx, stream_id)
    elif op == "ri" and operands:
        gs["rendering_intent"] = str(operands[0]).lstrip("/")
    elif op == "op" and operands:  # lowercase: fill overprint (PDF 1.2)
        gs["overprint_fill"] = bool(operands[0])
    elif op == "OP" and operands:  # uppercase: stroke overprint
        gs["overprint_stroke"] = bool(operands[0])
    elif op == "gs" and operands:  # ExtGState resource reference
        _apply_ext_gstate(str(operands[0]), resources, pikepdf_module, gs)


def _apply_ext_gstate(name: str, resources, pikepdf_module, gs: dict):
    """Apply a named ExtGState resource to the current graphics state."""
    try:
        ext_gstates = resources.get("/ExtGState")
        if ext_gstates is None:
            return
        gstate = ext_gstates.get(name if name.startswith("/") else f"/{name}")
        if gstate is None:
            return

        if (ri := gstate.get("/RI")) is not None:
            gs["rendering_intent"] = str(ri).lstrip("/")

        # Per spec: OP sets both stroke and fill overprint, UNLESS op is also
        # present in the same dictionary, in which case OP sets stroke only.
        op_entry = gstate.get("/op")  # fill overprint (PDF 1.3+)
        OP_entry = gstate.get("/OP")  # stroke overprint (and fill if op absent)

        if OP_entry is not None:
            gs["overprint_stroke"] = bool(OP_entry)
            if op_entry is None:
                gs["overprint_fill"] = bool(OP_entry)

        if op_entry is not None:
            gs["overprint_fill"] = bool(op_entry)

        if (opm := gstate.get("/OPM")) is not None:
            gs["overprint_mode"] = int(opm)

        if (bm := gstate.get("/BM")) is not None:
            gs["blend_mode"] = str(bm).lstrip("/")

    except (AttributeError, TypeError, ValueError) as err:
        logger.debug("ExtGState application failed for %s: %s", name, err)


def walk_stream(
    content_stream_obj,
    resources,
    pikepdf_module,
    *,
    detail: dict,
    seen_stream_ids: set,
    full: bool,
    stream_id: str,
):
    """Walk a content stream operator by operator while managing the stack."""
    gs_stack = []
    gs = DEFAULT_GS()
    op_idx = 0
    in_text = False

    try:
        raw_stream = pikepdf_module.parse_content_stream(content_stream_obj)
        stream_iterator = iter(raw_stream)
    except (KeyError, ValueError, TypeError, AttributeError) as err:
        logger.warning("Could not open content stream iterator for %s: %s", stream_id, err)
        return
    except pikepdf_module.PdfError as err:
        logger.warning("Could not open content stream iterator for %s: %s", stream_id, err)
        return

    while True:
        try:
            inst = next(stream_iterator)
        except StopIteration:
            break
        except (KeyError, ValueError, TypeError, AttributeError) as err:
            logger.warning(
                "Partial parsing truncation or failure in stream %s: %s", stream_id, err
            )
            break
        except pikepdf_module.PdfError as err:
            logger.warning(
                "Partial parsing truncation or failure in stream %s: %s", stream_id, err
            )
            break

        op_idx += 1
        if not hasattr(inst, "operator"):
            continue

        op = str(inst.operator)
        if op == "BT":
            in_text = True
        elif op == "ET":
            in_text = False

        process_operator(
            op,
            inst.operands,
            resources,
            pikepdf_module,
            gs,
            gs_stack,
            detail,
            seen_stream_ids,
            full,
            op_idx,
            in_text,
            stream_id,
        )


def _determine_array_like(contents) -> bool:
    """Helper method to determine contents layout structure with flattened complexity."""
    import pikepdf

    return isinstance(contents, pikepdf.Array | list)


def walk_page(page, resources, pikepdf_module, *, detail: dict, seen_stream_ids: set, full: bool):
    """
    Entry point for a complete PDF page.
    Safely inspects the page dictionary to extract exact /Contents stream IDs.
    """
    if page is None:
        return

    try:
        contents = page.get("/Contents")
    except (AttributeError, KeyError):
        return

    if contents is None:
        return

    if _determine_array_like(contents):
        for stream in contents:
            obj_id = stream.objgen[0] if hasattr(stream, "objgen") else "unknown"
            walk_stream(
                stream,
                resources,
                pikepdf_module,
                detail=detail,
                seen_stream_ids=seen_stream_ids,
                full=full,
                stream_id=f"{obj_id} 0 obj",
            )
    else:
        obj_id = contents.objgen[0] if hasattr(contents, "objgen") else "unknown"
        walk_stream(
            contents,
            resources,
            pikepdf_module,
            detail=detail,
            seen_stream_ids=seen_stream_ids,
            full=full,
            stream_id=f"{obj_id} 0 obj",
        )
