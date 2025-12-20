# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/dump_dests.py

"""Dump information about destinations in a PDF file"""

import json
import logging
import re

logger = logging.getLogger(__name__)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from pdftl.core.registry import register_operation
from pdftl.output.dump import dump

# FIXME: compare this with json.py


def _pdf_obj_to_json(obj, page_object_to_num_map, visited=None):
    """
    A helper function to recursively convert a PDF object to a
    JSON-serializable Python object. This provides a detailed dump
    of PDF structures. It handles circular references and maps page
    objects to their page numbers for readability.
    """
    from pikepdf import Array, Dictionary

    if visited is None:
        visited = set()

    # Handle indirect objects and prevent cycles, ignoring placeholder (0, 0)
    if hasattr(obj, "objgen") and obj.objgen != (0, 0):
        if obj.objgen in visited:
            return f"<<Circular Reference to {obj.objgen}>>"
        visited.add(obj.objgen)

    # Check if the object is a page and replace with a readable string
    if hasattr(obj, "objgen") and obj.objgen in page_object_to_num_map:
        return f"<<Page {page_object_to_num_map[obj.objgen]}>>"

    if isinstance(obj, (Array, Dictionary)):
        return _compound_obj_to_json(obj, page_object_to_num_map, visited)

    return _atomic_obj_to_json(obj)


def _compound_obj_to_json(obj, page_object_to_num_map, visited):
    from pikepdf import Array, Dictionary

    if isinstance(obj, Dictionary):
        return {
            str(k): _pdf_obj_to_json(v, page_object_to_num_map, visited) for k, v in obj.items()
        }
    if isinstance(obj, Array):
        return [_pdf_obj_to_json(item, page_object_to_num_map, visited) for item in obj]
    raise ValueError("Unknown compound PDF object, cannot convert to JSON")


def _atomic_obj_to_json(obj):
    from pikepdf import Name, Stream, String

    if isinstance(obj, Name):
        return str(obj)
    if isinstance(obj, (String, Stream)):
        return str(obj)
    if isinstance(obj, (int, float, bool, str)):
        return obj
    if obj is None:
        return None

    # For any other object type, return its representation
    return repr(obj)


_DUMP_DESTS_LONG_DESC = """

The `dump_dests` operation prints document-level metadata to the console, in JSON format.

"""

_DUMP_DESTS_EXAMPLES = [
    {"cmd": "in.pdf dump_dests", "desc": "Print destinations for in.pdf"},
    {
        "cmd": "in.pdf dump_dests output out.txt",
        "desc": "Save destinations for in.pdf to out.txt",
    },
]


@register_operation(
    "dump_dests",
    tags=["info", "links"],
    type="single input operation",
    desc="Print PDF named destinations data to the console",
    long_desc=_DUMP_DESTS_LONG_DESC,
    usage="<input> dump_dests",
    examples=_DUMP_DESTS_EXAMPLES,
    args=(["input_pdf"], {"output_file": "output"}),
)
def dump_dests(pdf, output_file=None):
    """
    Traverses the /Dests name tree of a PDF using pikepdf.NameTree.
    This provides a robust, iterable interface to the destinations.
    """
    from pikepdf import NameTree

    logger.debug("Dumping Dests name tree for PDF with %s pages.", len(pdf.pages))

    output_data = {"dests": [], "errors": []}

    # Create the page map *once*
    page_object_to_num_map = {p.obj.objgen: i + 1 for i, p in enumerate(pdf.pages)}

    # Check if the Dests name tree exists
    dests_tree_obj = None
    if hasattr(pdf.Root, "Names") and hasattr(pdf.Root.Names, "Dests"):
        dests_tree_obj = pdf.Root.Names.Dests

    if not dests_tree_obj:
        logger.debug("No /Dests name tree found in the document root.")
    else:
        try:
            # 1. Instantiate the NameTree object
            # This handles all tree-walking logic internally.
            dests_tree = NameTree(dests_tree_obj)

            # 2. Iterate over the flattened name tree like a dict
            for name, dest_obj in dests_tree.items():
                try:
                    # 'name' is a string key
                    # 'dest_obj' is the raw PDF object (usually an Array)
                    output_data["dests"].append(
                        {
                            "name": name,
                            "value": _pdf_obj_to_json(dest_obj, page_object_to_num_map),
                        }
                    )
                except ValueError as e:
                    # Error processing a *single* destination
                    output_data["errors"].append(
                        {
                            "error": f"Failed to process destination value for key '{name}'",
                            "details": str(e),
                            "raw_value": repr(dest_obj),
                        }
                    )

        except ValueError as e:
            # Error iterating the tree itself (e.g., malformed structure)
            output_data["errors"].append(
                {"error": "Failed to parse the /Dests name tree.", "details": str(e)}
            )

    # Sort destinations alphabetically by name for consistent output
    output_data["dests"].sort(key=lambda x: x["name"])

    _write_json_output(output_data, output_file)


def _write_json_output(output_data, output_file):
    """Helper function to format and write the final JSON output."""
    # Generate the standard indented JSON string
    json_string = json.dumps(output_data, indent=2)

    # Define a replacer function for our regex substitution
    def compact_simple_array(match):
        array_content = match.group(2)
        compacted_content = " ".join(array_content.strip().split())
        return f"{match.group(1)}{compacted_content}{match.group(3)}"

    # Use regex to compact simple arrays and objects for better readability
    compacted_json_string = re.sub(
        r"(\[)\s*([^\[\]\{\}]*?)\s*(\])", compact_simple_array, json_string
    )
    compacted_json_string = re.sub(
        r"(\{)\s*([^\{\}]*?)\s*(\})",
        compact_simple_array,
        compacted_json_string,
    )

    dump(compacted_json_string, dest=output_file)
