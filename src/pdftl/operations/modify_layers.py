import logging

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.operations.parsers.modify_layers_parser import parse_modify_layers_rules
from pdftl.utils.ocg import (
    clean_ocproperties,
    get_page_layer_map,
    set_layer_state,
    set_layer_usage,
)

logger = logging.getLogger(__name__)

_MODIFY_LAYERS_LONG_DESC = """

The `modify_layers` operation allows you to alter Optional Content Groups
(layers) in a PDF. You can permanently merge/strip them, or change their
default visibility and behavior.

The command reads a sequence of action-target pairs. If no target is provided
after an action, it defaults to affecting "all" layers.

### Available Actions

**Structural (Permanent):**
* `merge`: The visual content of the layer is permanently baked into the
  page. The layer is removed from the PDF's layer menu.
* `strip`: The visual content of the layer is completely deleted from the
  document, and the layer is removed from the PDF's layer menu.

**State & Behavior (Non-destructive):**
* `show` / `hide`: Sets the default visibility when the document is opened.
* `lock` / `unlock`: Prevents/allows the user from toggling the layer in viewers.
* `print` / `noprint`: Overrides behavior to force layer visibility ON or OFF when printing.
* `screen` / `noscreen`:
  Overrides behavior to force layer visibility ON or OFF on digital displays.

**Utility:**
* `keep`: Used primarily to exclude a specific layer when targeting "all" others.

*Note on State vs. Usage: Layer visibility on screen (`show`/`hide`) is independent
of its visibility when printing. Modifying a usage state (like `noprint`) without
changing its base state will leave its on-screen visibility unchanged.*

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
        "cmd": "in.pdf modify_layers hide all show name=Draft output out.pdf",
        "desc": "Hide all layers by default, except 'Draft'.",
    },
    {
        "cmd": "in.pdf modify_layers noprint name=Watermark output out.pdf",
        "desc": "Prevent the 'Watermark' layer from showing up on paper.",
    },
]


def _ensure_auto_state(pdf):
    """
    Injects or updates the Auto-State (/AS) array in the Default Configuration (/D)
    to ensure viewers respect layer Usage dictionaries for Print and View events.
    """
    import pikepdf

    oc_props = pdf.Root.get("/OCProperties")
    if not oc_props or "/D" not in oc_props:
        return

    d_dict = oc_props.D
    if "/AS" not in d_dict:
        d_dict.AS = pikepdf.Array()

    as_array = d_dict.AS

    # Grab all OCGs to ensure the event listeners cover the entire document
    all_ocgs = oc_props.get("/OCGs", pikepdf.Array())

    has_print = False
    has_view = False

    # Check if the events already exist and update their OCG arrays
    for as_dict in as_array:
        event = str(as_dict.get("/Event", ""))
        if event == "/Print":
            as_dict.OCGs = all_ocgs
            has_print = True
        elif event == "/View":
            as_dict.OCGs = all_ocgs
            has_view = True

    # If the events don't exist, append them to the AS array
    if not has_print:
        as_array.append(
            pikepdf.Dictionary(
                {
                    "/Event": pikepdf.Name("/Print"),
                    "/Category": pikepdf.Array([pikepdf.Name("/Print")]),
                    "/OCGs": all_ocgs,
                }
            )
        )

    if not has_view:
        as_array.append(
            pikepdf.Dictionary(
                {
                    "/Event": pikepdf.Name("/View"),
                    "/Category": pikepdf.Array([pikepdf.Name("/View")]),
                    "/OCGs": all_ocgs,
                }
            )
        )


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
        for xobj in resources.XObject.values():
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
    operands,
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

        # If merged, delete the /OC dict so it becomes a permanent object
        if "merge" in actions:
            xobj = resources.XObject[local_alias]
            if "/OC" in xobj:
                del xobj["/OC"]

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
            operands,
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
    tags=["layers", "modify", "merge", "strip", "visibility", "print"],
    type="single input operation",
    desc="Modify state, merge, or strip specific layers",
    long_desc=_MODIFY_LAYERS_LONG_DESC,
    usage="<input> modify_layers [action] [target]... output <output> [<option>...]",
    examples=_MODIFY_LAYERS_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {"output_file": c.OUTPUT}),
)
def modify_layers(pdf, *args, output_file=None) -> OpResult:
    cli_args = args[0] if len(args) == 1 and isinstance(args[0], (list, tuple)) else args

    rules_by_id, rules_by_name, default_actions = parse_modify_layers_rules(cli_args)
    resolved_targets = _resolve_targets(pdf, rules_by_id, rules_by_name, default_actions)

    if not resolved_targets:
        return OpResult(
            success=True,
            data="No matching layers found to modify.",
            meta={c.META_OUTPUT_FILE: output_file},
        )

    # 1. Filter out structural actions (require expensive stream parsing)
    _modify_structural_targets(pdf, resolved_targets)
    # 2. Process state/usage actions (fast dictionary operations)
    _modify_state_or_usage(pdf, resolved_targets)

    return OpResult(
        success=True,
        data=f"Successfully modified layers. Targets matched: {len(resolved_targets)}",
        meta={c.META_OUTPUT_FILE: output_file},
    )


def _modify_structural_targets(pdf, resolved_targets):
    structural_targets = {}
    for oid, actions in resolved_targets.items():
        if "strip" in actions:
            structural_targets[oid] = "strip"
        elif "merge" in actions:
            structural_targets[oid] = "merge"

    if structural_targets:
        processed_xobjs = set()
        for page in pdf.pages:
            _process_content_stream(pdf, page, structural_targets, processed_xobjs)
        clean_ocproperties(pdf, set(structural_targets.keys()))


def _modify_state_or_usage(pdf, resolved_targets):
    targets_by_action = {}
    for oid, actions in resolved_targets.items():
        for action in actions:
            targets_by_action.setdefault(action, set()).add(oid)

    state_actions = {"show", "hide", "lock", "unlock"}
    usage_actions = {"print", "noprint", "screen", "noscreen"}

    usage_modified = False

    for action, oids in targets_by_action.items():
        if action in state_actions:
            set_layer_state(pdf, oids, action)
        elif action in usage_actions:
            set_layer_usage(pdf, oids, action)
            usage_modified = True

    # Apply the fix: Wire up the AS array if we touched usage dictionaries
    if usage_modified:
        _ensure_auto_state(pdf)


def _resolve_targets(pdf, rules_by_id: dict, rules_by_name: dict, default_actions: set) -> dict:
    """Maps strict IDs and sloppy names to their final global action."""
    from pikepdf import NamePath

    final_targets: dict[int, set] = {}
    ocgs = pdf.Root.get(NamePath.OCProperties.OCGs)

    if not ocgs:
        return final_targets

    for ocg in ocgs:
        _resolve_targets_for_ocg(ocg, default_actions, rules_by_id, rules_by_name, final_targets)

    return final_targets


def _resolve_targets_for_ocg(ocg, default_actions: set, rules_by_id, rules_by_name, final_targets):
    obj_id = int(ocg.objgen[0])

    # Initialize the set directly with our default actions
    actions_for_this_ocg = set(default_actions)

    # 1. Strict ID Match
    if obj_id in rules_by_id:
        actions = rules_by_id[obj_id]
        # Assuming the parser returns a list/set of actions for the ID
        actions_for_this_ocg.update(actions if isinstance(actions, (list, set)) else [actions])
    else:
        # 2. Sloppy Name Match
        name_obj = ocg.get("/Name")
        if name_obj is not None:
            name = str(name_obj)
            clean_name = name[1:] if name.startswith("/") else name

            if clean_name in rules_by_name:
                actions = rules_by_name[clean_name]
                actions_for_this_ocg.update(
                    actions if isinstance(actions, (list, set)) else [actions]
                )

    # Only track if there are actual actions to perform
    actions_for_this_ocg.discard("keep")
    if actions_for_this_ocg:
        final_targets[obj_id] = actions_for_this_ocg
