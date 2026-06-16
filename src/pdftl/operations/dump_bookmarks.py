# src/pdftl/operations/dump_bookmarks.py

"""Extract PDF Table of Contents to a YAML or JSON file with resolved destinations."""

import json
import logging
from typing import Any

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.info.toc import extract_toc_tree
from pdftl.utils.dependencies import ensure_dependencies
from pdftl.utils.dump import get_json_flag
from pdftl.utils.string_utils import compact_json_string
from pdftl.utils.io_helpers import smart_open_maybe_dash

# Import the destination resolution functions
from pdftl.utils.destinations import (
    get_named_destinations,
    get_page_map,
    resolve_dest_to_page_num,
)

logger = logging.getLogger(__name__)

_DUMP_TOC_LONG_DESC = """
Extracts the document's Table of Contents (bookmarks) into a highly readable
and editable YAML format (or JSON).

This faithful extraction preserves both the hierarchical structure and the
complex properties of your bookmarks. The resulting file can be easily edited
in any text editor and applied back to the same (or a different) PDF using
the `update_bookmarks` operation.

### Supported Bookmark Properties
Each bookmark node is represented as a dictionary (key-value mapping). The
extractor natively supports and outputs the following properties:

* **`title`**: The display text of the bookmark.
* **`page`**: The 1-indexed target page number.
* **`children`**: A nested list of sub-bookmarks to maintain outline hierarchy.
* **`color`**: An RGB array for the bookmark text color (e.g., `[1.0, 0.0, 0.0]` for red).
* **`bold`** / **`italic`**: Boolean flags for text styling (`true` or `false`).
* **`uri`**: An external web link (used if the bookmark points to a URL rather than a page).
* **`dest`**: A string reference to a Named Destination embedded inside the PDF.
* **`view`**: The precise zoom/viewport array (e.g., `["XYZ", 0, 700, 2.5]`, `["FitH", 500]`).

### Skipping Destination Resolution
By default, named destinations are automatically resolved into exact `page` and `view` parameters
to make the data more immediately useful. If you want to skip this step and only output the
original `dest` names without deriving their page targets, pass the `no_resolve` flag.

This may be useful if you want to edit the output from `dump_bookmarks` and pass it to the
`update_bookmarks` operation. See the `update_bookmarks` help for rules on `dest` versus `page` and
`view` precedence.

### Format Example
```yaml
- title: Chapter 1
  page: 1
  bold: true
  children:
    - title: Sub-section A
      page: 2
      view: ["FitH", 800]
    - title: Important Chart
      page: 3
      color: [1.0, 0.0, 0.0]
- title: External Resources
  uri: [https://example.com](https://example.com)
```

### Dependency note

YAML extraction requires the `pyyaml` library. If it is not installed, you can
install it via `pip install pdftl[yaml-bookmarks]`, or simply use the `json`
flag to extract using Python's standard JSON library instead.
"""

_DUMP_TOC_EXAMPLES = [
    {
        "cmd": "in.pdf dump_bookmarks",
        "desc": "Print YAML bookmark data to standard output",
    },
    {
        "cmd": "in.pdf dump_bookmarks output bookmarks.yaml",
        "desc": "Dump YAML bookmark data to bookmarks.yaml",
    },
    {
        "cmd": "in.pdf dump_bookmarks no_resolve output bookmarks.yaml",
        "desc": "Dump YAML bookmarks, keeping named destinations unresolved",
    },
    {
        "cmd": "in.pdf dump_bookmarks json output bookmarks.json",
        "desc": "Dump JSON bookmark data to bookmarks.json",
    },
]


def _resolve_single_node_dest(node: dict, page_map: dict, named_dests: Any) -> None:
    """Safely resolves a single bookmark node's destination properties if needed."""
    if "dest" not in node or "page" in node:
        return

    try:
        resolved = resolve_dest_to_page_num(node["dest"], page_map, named_dests)
        if not resolved:
            return

        node["page"] = resolved.page_num

        # Inject 'view' config if it wasn't extracted originally
        if "view" not in node:
            # Clean up pikepdf data types into native python primitives for encoders
            clean_args = [
                float(arg)
                if hasattr(arg, "__float__")
                else int(arg)
                if hasattr(arg, "__int__")
                else arg
                for arg in resolved.args
            ]
            node["view"] = [resolved.dest_type] + clean_args
    except (KeyError, IndexError, ValueError, TypeError, AttributeError) as e:
        logger.warning(f"Failed to resolve destination '{node['dest']}': {e}")


def _resolve_bookmarks_tree(nodes: list[dict], page_map: dict, named_dests: Any) -> None:
    """Recursively walks the tree to resolve destinations into page numbers and views."""
    for node in nodes:
        _resolve_single_node_dest(node, page_map, named_dests)

        # Deep-dive down the hierarchy tree
        if "children" in node and node["children"]:
            # Pop and reassign children so it prints at the bottom of the YAML block
            children = node.pop("children")
            _resolve_bookmarks_tree(children, page_map, named_dests)
            node["children"] = children


def dump_toc_cli_hook(result: OpResult, stage, _pipeline):
    """Writes the TOC tree to stdout or a file in YAML or JSON."""
    if result.meta is None:
        raise AttributeError("No result metadata")

    output_file = result.meta.get(c.META_OUTPUT_FILE)
    json_output = result.meta.get(c.META_JSON_OUTPUT, False)

    with smart_open_maybe_dash(output_file) as file:
        if json_output:
            file.write(compact_json_string(json.dumps({"bookmarks": result.data}, indent=2)))
            file.write("\n")
        else:
            ensure_dependencies(
                feature_name="YAML bookmarks output",
                dependencies={"yaml": "pyyaml"},
                extra_tag="yaml",
            )
            import yaml

            yaml.dump(result.data, file, sort_keys=False, default_flow_style=None)


@register_operation(
    "dump_bookmarks",
    tags=["info", "metadata", "bookmarks"],
    type="single input operation",
    desc="Extract PDF bookmarks into YAML or JSON",
    long_desc=_DUMP_TOC_LONG_DESC,
    examples=_DUMP_TOC_EXAMPLES,
    cli_hook=dump_toc_cli_hook,
    usage="<input> dump_bookmarks [json] [no_resolve] [output <output>]",
    args=([c.OPERATION_NAME, c.INPUT_PDF, c.OPERATION_ARGS], {"output_file": c.OUTPUT}),
    skip_pipeline_save=True,
)
def dump_toc(op_name, pdf, op_args, output_file=None) -> OpResult:
    no_resolve = "no_resolve" in op_args
    json_output = get_json_flag([x for x in op_args if x not in ("no_resolve",)], op_name)

    # 1. Pull the outline tree structure out
    toc_data = extract_toc_tree(pdf)

    # 2. Resolve inline named destinations if requested
    if toc_data and not no_resolve:
        # Extract layout lookup dictionaries from pikepdf
        page_map = get_page_map(pdf.pages)
        named_dests = get_named_destinations(pdf)

        _resolve_bookmarks_tree(toc_data, page_map, named_dests)

    return OpResult(
        success=True,
        pdf=pdf,
        data=toc_data,
        is_discardable=True,
        meta={
            c.META_OUTPUT_FILE: output_file,
            c.META_JSON_OUTPUT: json_output,
        },
    )
