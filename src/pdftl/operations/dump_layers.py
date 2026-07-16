# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Dump Layer (Optional Content Group) information"""

import json

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.utils.hooks import from_result_meta
from pdftl.utils.io_helpers import smart_open
from pdftl.utils.type_helpers import as_iterable

_DUMP_LAYERS_LONG_DESC = """

The `dump_layers` operation extracts high-fidelity information about Optional
Content Groups (OCGs), commonly known as 'Layers'.

This operation outputs a structured JSON object containing the layer definitions,
their default states, and the UI hierarchy (tree structure) used by PDF viewers.

### JSON Schema Details

The output JSON contains several top-level keys:

* `has_layers` (boolean):
    True if the document contains any Layer definitions.
* `layers` (list):
    A flat list of all Layer objects. Each contains:
    * `name`: The user-visible name of the layer.
    * `obj_id`: The PDF indirect object ID (used to link to the hierarchy).
    * `default_state`: "ON" or "OFF" based on the default configuration.
    * `intent`: Optional list of intents (e.g., "View", "Design").
    * `usage`: Specialized metadata for "Print", "View", "Export", or "Zoom" states.
        * **active** (boolean): Indicates if this usage category is currently
          governing the layer's behavior. A layer might have "Print" settings,
          but they only take effect if the layer is 'active' for the Print event
          via the document's Auto-State (`/AS`) settings.
* `ui_hierarchy` (list):
    A recursive tree structure representing the Layers panel in a PDF viewer.
    * **Nested Arrays**: A list following a layer ID indicates child layers.
    * **Labels**: Simple strings or 'label' objects representing headers.
* `default_config` (object):
    Details of the default display settings, including the `base_state`
    (usually "ON") and lists of IDs that are explicitly "OFF" or "ON".
* `alternate_configs` (list):
    Optional secondary profiles (e.g., "Technical View") defined by the author.

### Understanding the Hierarchy

Because PDFs store layers in a flat list but display them in a tree, you must
link the `ui_hierarchy` to the `layers` list using the `obj_id`.

Example hierarchy interpretation:
`[ {"obj_id": 4, "type": "layer"}, [ {"obj_id": 5, "type": "layer"} ] ]`
This indicates that Layer 5 is a child of Layer 4.
For convenience, layer names are added to hierarchy output where possible.

"""

_DUMP_LAYERS_EXAMPLES = [
    {
        "cmd": "in.pdf dump_layers",
        "desc": "Print layer information for in.pdf to the console",
    },
    {
        "cmd": "in.pdf dump_layers output layers.json",
        "desc": "Save layer information for in.pdf to layers.json",
    },
]


def _parse_config(config_dict):
    """Parses a configuration dictionary (like /D or an entry in /Configs)"""
    config_data = {
        "name": str(config_dict.get("/Name", "Unnamed Config")),
        "base_state": _clean_val(config_dict.get("/BaseState", "/ON")),
        "off_list_ids": [int(obj.objgen[0]) for obj in config_dict.get("/OFF", [])],
        "on_list_ids": [int(obj.objgen[0]) for obj in config_dict.get("/ON", [])],
    }
    if "/Order" in config_dict:
        config_data["ui_hierarchy"] = _parse_order(config_dict.Order)

    return config_data


def _parse_order(order_item):
    import pikepdf

    if isinstance(order_item, pikepdf.Array):
        return [_parse_order(i) for i in as_iterable(order_item)]

    if isinstance(order_item, pikepdf.Dictionary):
        # Fetch the name here so the tree is self-documenting
        name = str(getattr(order_item, "Name", "Unnamed"))
        return {"obj_id": int(order_item.objgen[0]), "name": name, "type": "layer"}

    if isinstance(order_item, (pikepdf.String, str)):
        return {"label": str(order_item), "type": "label"}

    return str(order_item)


def _clean_val(val):
    """Normalize PDF Name objects to simple strings (e.g. /ON -> ON)"""
    s = str(val)
    return s[1:] if s.startswith("/") else s


def _parse_usage(ocg, obj_id, active_usage_map):
    """Recursively convert the /Usage dictionary to a serializable dict."""
    import pikepdf

    if "/Usage" not in ocg:
        return None

    usage_dict = {}
    for key, val in ocg.Usage.items():
        clean_key = _clean_val(key)
        if isinstance(val, pikepdf.Dictionary):
            parsed_val = {_clean_val(k): _clean_val(v) for k, v in val.items()}

            # Inject the 'active' flag for standard usage categories
            if clean_key in ["Print", "View", "Export", "Zoom"]:
                parsed_val["active"] = obj_id in active_usage_map.get(clean_key, set())

            usage_dict[clean_key] = parsed_val
        else:
            usage_dict[clean_key] = _clean_val(val)
    return usage_dict


def _get_active_usage_map(d_dict):
    """
    Parses the /AS (Auto-State) array to determine which OCGs are actively
    bound to which usage categories.
    Returns a dict mapping category names to sets of OCG object IDs.
    """
    import pikepdf

    active_map = {}
    if "/AS" not in d_dict:
        return active_map

    for as_dict in d_dict.AS:
        if not isinstance(as_dict, pikepdf.Dictionary):
            continue

        event_name = _clean_val(as_dict.get("/Event", ""))

        # Determine the target usage category (defaults to Event name if missing)
        categories = as_dict.get("/Category")
        if categories:
            cat_names = [_clean_val(c) for c in categories]
        else:
            cat_names = [event_name]

        # Extract IDs of bound OCGs
        bound_ids = {int(o.objgen[0]) for o in as_dict.get("/OCGs", []) if hasattr(o, "objgen")}

        # Map the category to the set of IDs
        for cat in cat_names:
            if cat not in active_map:
                active_map[cat] = set()
            active_map[cat].update(bound_ids)

    return active_map


def dump_layers_cli_hook(result: OpResult, stage, _pipeline):
    # Use smart_open to handle stdout vs file correctly
    output_filename = from_result_meta(result, c.META_OUTPUT_FILE)
    with smart_open(output_filename) as f:
        json.dump(result.data, f, indent=2)
        print("", file=f)  # Ensure trailing newline


@register_operation(
    "dump_layers",
    cli_hook=dump_layers_cli_hook,
    tags=["diagnostic", "layers"],
    type="single input operation",
    desc="Dump layer info (JSON)",
    long_desc=_DUMP_LAYERS_LONG_DESC,
    usage="<input> dump_layers [output <output>]",
    examples=_DUMP_LAYERS_EXAMPLES,
    # Note the mapping of "output_file": c.OUTPUT to capture the CLI keyword
    args=([c.INPUT_PDF], {"output_file": c.OUTPUT}),
    skip_pipeline_save=True,
)
def dump_layers(pdf, output_file=None) -> OpResult:
    """
    Extract OCG (Layer) data and write as JSON.
    """
    results = {"has_layers": False, "layers": [], "default_config": {}}

    if "/OCProperties" in pdf.Root:
        results = _extract_ocproperties(pdf.Root.OCProperties)
    return OpResult(success=True, data=results, meta={c.META_OUTPUT_FILE: output_file})


def _extract_ocproperties(ocprops):
    results = {"has_layers": True, "layers": [], "default_config": {}}
    active_usage_map = {}

    # 1. Capture Default Configuration (D) first
    if "/D" in ocprops:
        results["default_config"] = _parse_config(ocprops.D)
        if "ui_hierarchy" in results["default_config"]:
            results["ui_hierarchy"] = results["default_config"]["ui_hierarchy"]

        # Grab the active usage mapping from the D dictionary
        active_usage_map = _get_active_usage_map(ocprops.D)

    # 2. Capture Alternate Configurations
    if "/Configs" in ocprops:
        results["alternate_configs"] = [_parse_config(c) for c in ocprops.Configs]

    # 3. NOW check legacy top-level Order ONLY if D didn't provide one
    if "/Order" in ocprops and "ui_hierarchy" not in results:
        results["ui_hierarchy"] = _parse_order(ocprops.Order)

    # Iterate OCGs
    if "/OCGs" in ocprops:
        off_ids = results["default_config"].get("off_list_ids", [])

        for ocg in ocprops.OCGs:
            obj_id = int(ocg.objgen[0])

            layer_data = {
                "name": str(ocg.get("/Name", "Unnamed")),
                "obj_id": obj_id,
                "default_state": "OFF" if obj_id in off_ids else "ON",
                "intent": (
                    [_clean_val(i) for i in ocg.get("/Intent", [])] if "/Intent" in ocg else None
                ),
                # Pass the id and active map down
                "usage": _parse_usage(ocg, obj_id, active_usage_map),
            }
            results["layers"].append(layer_data)
    return results
