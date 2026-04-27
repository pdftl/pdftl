import logging

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.utils.ocg import get_page_layer_map, clean_ocproperties
from pdftl.operations.parsers.modify_layers_parser import parse_modify_layers_rules

logger = logging.getLogger(__name__)

_MODIFY_LAYERS_LONG_DESC = """

The `modify_layers` operation allows you to permanently alter Optional Content Groups
(layers) in a PDF by either merging or stripping them.

The command reads a sequence of action-target pairs. If no target is provided
after an action, it defaults to affecting "all" layers.

### Available Actions

* `merge`: The visual content of the layer is permanently baked into the
  page. The layer is removed from the PDF's layer menu.
* `strip`: The visual content of the layer is completely deleted from the
  document, and the layer is removed from the PDF's layer menu.
* `keep`: Used primarily to exclude a specific layer when merging or
  stripping all others.

### Target Specifications

* `name=<string>`: Sloppy match. Applies the action to **all** layers with this name.
* `id=<integer>`: Strict match. Applies the action to the exact underlying PDF object.
* `all`: Explicitly targets all layers.
* `<string>`: If no `key=` prefix is provided, it defaults to a `name=` match.

"""

_MODIFY_LAYERS_EXAMPLES = [
    {
        "cmd": "in.pdf modify_layers strip output out.pdf",
        "desc": "Strip ALL layers from the PDF.",
    },
    {
        "cmd": "in.pdf modify_layers merge keep Background output out.pdf",
        "desc": 'Merge all layers EXCEPT the one named "Background".',
    },
    {
        "cmd": "in.pdf modify_layers strip name=English merge id=42 output out.pdf",
        "desc": "Chain multiple commands using explicit key=value syntax.",
    },
]


def _process_content_stream(pdf, stream_dict, resolved_targets, processed_xobjs=None):
    """Recursively parses a stream to strip/merge BDC tags and XObject Do calls."""
    import pikepdf

    if processed_xobjs is None:
        processed_xobjs = set()

    # Prevent infinite recursion if XObjects reference each other
    if hasattr(stream_dict, "objgen"):
        if stream_dict.objgen in processed_xobjs:
            return
        processed_xobjs.add(stream_dict.objgen)

    resources = stream_dict.get("/Resources")
    prop_map, xobj_map = get_page_layer_map(resources)

    # Recursively process Form XObjects found in the resources
    if resources and "/XObject" in resources:
        for local_name, xobj in resources.XObject.items():
            if xobj.get("/Subtype") == "/Form":
                _process_content_stream(pdf, xobj, resolved_targets, processed_xobjs)

    new_stream = []
    block_stack = []
    safe_ops = {"q", "Q"}

    try:
        parsed_stream = pikepdf.parse_content_stream(stream_dict)
    except (pikepdf.PdfError, ValueError):
        return  # Not a valid stream

    for operands, operator in parsed_stream:
        _process_stream_op(
            operands,
            operator,
            new_stream,
            block_stack,
            safe_ops,
            prop_map,
            xobj_map,
            resolved_targets,
            resources,
        )

    # Re-encode the stream depending on if it's a Page or a Form XObject
    new_data = pikepdf.unparse_content_stream(new_stream)
    if stream_dict.get("/Type") == "/Page":
        stream_dict.Contents = pdf.make_stream(new_data)
    else:
        stream_dict.write(new_data)


def _process_block_end(new_stream, block_stack, operands, operator):
    if block_stack:
        action = block_stack.pop()
        is_stripping = "strip" in block_stack
        if action in ("strip", "merge") or is_stripping:
            return
    new_stream.append((operands, operator))
    return


def _process_block_start(
    new_stream, block_stack, op_str, operator, operands, prop_map, resolved_targets, is_stripping
):
    action = "keep"
    if op_str == "BDC" and len(operands) > 1 and str(operands[0]) == "/OC":
        local_alias = str(operands[1])
        global_id = prop_map.get(local_alias)
        action = resolved_targets.get(global_id, "keep")

    block_stack.append(action)
    if action in ("strip", "merge") or is_stripping:
        return

    new_stream.append((operands, operator))
    return


def _process_xobject_invocation(
    new_stream,
    block_stack,
    op_str,
    operator,
    operands,
    prop_map,
    resolved_targets,
    is_stripping,
    xobj_map,
    resources,
):
    local_alias = str(operands[0])
    if local_alias in xobj_map:
        # Get actions for all OCGs attached to this XObject
        ocg_ids = xobj_map[local_alias]
        actions = [resolved_targets.get(oid, "keep") for oid in ocg_ids]

        # If any layer it belongs to is stripped, drop the 'Do' command
        if "strip" in actions or is_stripping:
            return True

        # If mergeed, delete the /OC dict so it becomes a permanent object
        if "merge" in actions:
            xobj = resources.XObject[local_alias]
            if "/OC" in xobj:
                del xobj.OC

    return False


def _process_stream_op(
    operands,
    operator,
    new_stream,
    block_stack,
    safe_ops,
    prop_map,
    xobj_map,
    resolved_targets,
    resources,
):
    op_str = str(operator)
    # --- 1. Block END (EMC) ---
    if op_str == "EMC":
        _process_block_end(new_stream, block_stack, operands, operator)
        return

    is_stripping = "strip" in block_stack

    # --- 2. Block START (BDC or BMC) ---
    if op_str in ("BDC", "BMC"):
        _process_block_start(
            new_stream,
            block_stack,
            op_str,
            operator,
            operands,
            prop_map,
            resolved_targets,
            is_stripping,
        )
        return

    # --- 3. XObject Invocation (Do) ---
    if (
        op_str == "Do"
        and len(operands) == 1
        and _process_xobject_invocation(
            new_stream,
            block_stack,
            op_str,
            operator,
            operands,
            prop_map,
            resolved_targets,
            is_stripping,
            xobj_map,
            resources,
        )
    ):
        return

    # --- 4. Standard Content ---
    if is_stripping and op_str not in safe_ops:
        return

    new_stream.append((operands, operator))


@register_operation(
    "modify_layers",
    tags=["layers", "modify", "merge", "strip"],
    type="single input operation",
    desc="Merge or strip specific layers",
    long_desc=_MODIFY_LAYERS_LONG_DESC,
    usage="<input> modify_layers [action] [target]... output <output> [<option>...]",
    examples=_MODIFY_LAYERS_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {"output_file": c.OUTPUT}),
)
def modify_layers(pdf, *args, output_file=None) -> OpResult:
    cli_args = args[0] if len(args) == 1 and isinstance(args[0], (list, tuple)) else args

    rules_by_id, rules_by_name, default_action = parse_modify_layers_rules(cli_args)
    resolved_targets = _resolve_targets(pdf, rules_by_id, rules_by_name, default_action)
    target_ids = set(resolved_targets.keys())

    if not target_ids:
        return OpResult(
            success=True,
            data="No matching layers found to modify.",
            meta={c.META_OUTPUT_FILE: output_file},
        )

    processed_xobjs = set()
    for page in pdf.pages:
        _process_content_stream(pdf, page, resolved_targets, processed_xobjs)

    clean_ocproperties(pdf, target_ids)

    return OpResult(
        success=True,
        data=f"Successfully modified layers. Targets matched: {len(target_ids)}",
        meta={c.META_OUTPUT_FILE: output_file},
    )


def _resolve_targets(pdf, rules_by_id: dict, rules_by_name: dict, default_action: str) -> dict:
    """Maps strict IDs and sloppy names to their final global action."""
    from pikepdf import NamePath

    final_targets = {}
    ocgs = pdf.Root.get(NamePath.OCProperties.OCGs)

    if not ocgs:
        return final_targets

    for ocg in ocgs:
        _resolve_targets_for_ocg(ocg, default_action, rules_by_id, rules_by_name, final_targets)

    return final_targets


def _resolve_targets_for_ocg(ocg, default_action, rules_by_id, rules_by_name, final_targets):
    obj_id = int(ocg.objgen[0])
    action_for_this_ocg = default_action

    # 1. Strict ID Match (Takes precedence)
    if obj_id in rules_by_id:
        action_for_this_ocg = rules_by_id[obj_id]
    else:
        # 2. Sloppy Name Match
        name_obj = ocg.get("/Name")
        if name_obj is not None:
            name = str(name_obj)
            clean_name = name[1:] if name.startswith("/") else name

            if clean_name in rules_by_name:
                action_for_this_ocg = rules_by_name[clean_name]

    # Only track layers that are actually being modified
    if action_for_this_ocg != "keep":
        final_targets[obj_id] = action_for_this_ocg
