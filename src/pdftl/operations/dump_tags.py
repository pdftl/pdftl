# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/dump_tags.py

"""Inspect the PDF structure tree: reading-order view with stream correlation.

Three modes:
  tree          — YAML/JSON dump of the raw structure tree hierarchy
  reading_order — walk in logical order; for each leaf, show the
                  corresponding marked-content lines from the normalized
                  page stream, with line numbers (default)
  issues        — flag structural accessibility problems
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.operations.helpers.tags_linter import _run_issues
from pdftl.operations.helpers.tags_tree_walker import _reading_order_lines, _run_tree
from pdftl.utils.io_helpers import smart_open_maybe_dash
from pdftl.utils.page_specs import page_numbers_matching_page_specs
from pdftl.utils.hooks import from_result_meta

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Long description
# ---------------------------------------------------------------------------

_DUMP_TAGS_LONG_DESC = """
The `dump_tags` operation inspects the PDF structure tree (the logical
document model used by screen readers and accessibility validators) and
reports it in one of three modes.

### Modes

**`reading_order`** *(default)*

Walk the structure tree in logical reading order. For each leaf node that
carries a Marked Content ID (MCID), show the corresponding marked-content
block from the normalized page content stream, along with its 1-based
line numbers within that stream. This is the primary tool for verifying
that auto-tagging (e.g. from the `tag` operation) has produced the right
structure and that the reading order matches visual intent.

Output format:

```
[H1] page=1  stream_lines=11-15
  /H1 << /MCID 2 >> BDC
  0 -40 Td
  /F1 18 Tf
  (Section One) Tj
  EMC
```

Container nodes (with no direct MCID) are shown as headers with their
depth, page number, custom role (if mapped or unmapped), and child count.

Standard tags with no role mapping are displayed in their standard format:
```
[Document]  (4 children)
  [P] page=1  stream_lines=3-6
    ...
```

For custom tags mapped to standard types or unmapped tags, they are annotated
with their role attributes (e.g. `role=text-unit` or `role=Speaker`):
```
[P] page=3 role=text-unit (2 children)
  [Span] page=3 role=Speaker
```

**`tree`**

Dump the raw structure tree as YAML (default) or JSON. Each node
contains: `tag`, `role` (if a custom tag is present), `page`, `mcid`,
`alt`, `actual_text`, `title`, `lang`, and `children`.

If a custom tag maps to a standard tag via `/RoleMap`, the node's
`tag` contains the mapped standard tag and its `role` holds the
original custom name. If a custom tag is unmapped, `tag` defaults to
`"unknown"` and `role` holds the custom name.

This is the foundation for tooling that needs to read or transform the
structure tree programmatically. Output is compatible with
`update_bookmarks` conventions for the `title` field.

**`issues`**

Walk the tree and report structural accessibility problems:

* Figures without `/Alt` text (checks standard and mapped Figures)
* Empty heading elements (no text in leaf MCIDs)
* Heading level skips (H1 → H3 with no H2, checked across standard and mapped headings)
* MCIDs referenced in the tree but not found in any page stream
* Orphaned marked content (in stream but not referenced by any tree node)
* Missing `/MarkInfo` or `/StructTreeRoot` on the document root
* `/Lang` attribute missing from root or individual elements

### Page specification

Standard page specs restrict which pages are included in the output
(e.g. `1`, `2-4`, `1 3-5`). Default is all pages. This applies to the
`reading_order` and `issues` modes; `tree` always dumps the full tree
but annotates which pages are within scope.

### Options

* `json` — use JSON instead of YAML for `tree` mode output
* `annotate` — in `reading_order` mode, append `%` operator comments to
  each stream line (same as `dump_streams annotate`); useful for
  understanding what each operator does
* `streams=false` — in `reading_order` mode, suppress the stream content
  and show only the structural metadata (tag, page, line range)

### Relationship to other operations

| Operation       | Purpose                                      |
|-----------------|----------------------------------------------|
| `dump_streams`  | Raw/normalized stream content; no structure  |
| `dump_tags`     | Structure tree + stream correlation          |
| `dump_bookmarks`| Outline (visible TOC); separate from tags    |
| `tag`           | Auto-tag a PDF using opendataloader-pdf      |

### Notes

The stream lines shown in `reading_order` mode are from the *normalized*
stream (one operator per line), exactly as `dump_streams` and `replace`
see them. Line numbers are per-page and reset to 1 at each new page.

If a page has multiple content streams (a PDF array `/Contents`), they
are concatenated before normalization, matching `replace` behavior.

Form XObjects that carry MCIDs are fully correlated. When stream
decompression is enabled, the matching operator lines inside the Form
XObject stream are parsed, located, and displayed inline in their
logical reading order sequence.
"""

_DUMP_TAGS_EXAMPLES = [
    {
        "cmd": "in.pdf dump_tags",
        "desc": "Reading-order view: structure + stream lines for all pages",
    },
    {
        "cmd": "in.pdf dump_tags reading_order 1-3 output tags.txt",
        "desc": "Reading-order view for pages 1-3, saved to file",
    },
    {
        "cmd": "in.pdf dump_tags reading_order annotate",
        "desc": "Reading-order view with PDF operator annotations on each stream line",
    },
    {
        "cmd": "in.pdf dump_tags reading_order streams=false",
        "desc": "Reading-order view without stream content (structure metadata only)",
    },
    {
        "cmd": "in.pdf dump_tags tree output tags.yaml",
        "desc": "Dump structure tree as YAML",
    },
    {
        "cmd": "in.pdf dump_tags tree json output tags.json",
        "desc": "Dump structure tree as JSON",
    },
    {
        "cmd": "in.pdf dump_tags issues",
        "desc": "Report structural accessibility problems",
    },
]

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

_MODES = {"reading_order", "tree", "issues"}
_KNOWN_FLAGS = {"json", "annotate"}
_KNOWN_KV = {"streams"}


def _parse_args(op_args: list[str], op_name: str) -> tuple[str, list[str], bool, bool, bool]:
    """
    Returns (mode, page_specs, json_output, annotate, show_streams).
    """
    from pdftl.utils.keyval_parser import parse_keyval_list

    bare: list[str] = []
    kvs = parse_keyval_list(
        op_args,
        bare_tokens=bare,
        allowed_keys=list(_KNOWN_KV),
        lowercase_values=True,
        context=op_name,
    )

    mode = "reading_order"
    page_specs: list[str] = []
    json_output = False
    annotate = False

    for token in bare:
        if token in _MODES:
            mode = token
        elif token == "json":
            json_output = True
        elif token == "annotate":
            annotate = True
        else:
            page_specs.append(token)

    show_streams = kvs.get("streams", "true") != "false"
    return mode, page_specs, json_output, annotate, show_streams


# ---------------------------------------------------------------------------
# Output Format Handlers
# ---------------------------------------------------------------------------


def _write_tree_yaml(f, data) -> None:
    """Attempt to write YAML, fallback to JSON if pyyaml is missing or serialization fails."""
    try:
        from pdftl.utils.dependencies import ensure_dependencies

        ensure_dependencies(
            feature_name="YAML tree output",
            dependencies={"yaml": "pyyaml"},
            extra_tag="yaml",
        )
        import yaml

        yaml.dump(data, f, sort_keys=False, default_flow_style=None)
    except (ImportError, AttributeError, TypeError):
        # Fallback to JSON if pyyaml not installed or serialization fails
        logger.warning("pyyaml not available; falling back to JSON output")
        f.write(json.dumps({"structure_tree": data}, indent=2))
        f.write("\n")


def _write_tree_output(f, data, json_output: bool) -> None:
    """Handle output for 'tree' mode."""
    if json_output:
        f.write(json.dumps(data, indent=2))
        f.write("\n")
    else:
        _write_tree_yaml(f, data)


def _write_reading_order_output(f, lines: list[str]) -> None:
    """Handle output for 'reading_order' mode."""
    f.write("\n".join(lines))
    f.write("\n")


def _format_plaintext_issues(f, issues: list[dict]) -> None:
    """Format and write structural issues as human-readable plaintext."""
    if not issues:
        f.write("No structural issues found.\n")
        return

    by_severity = {"error": [], "warning": [], "info": []}
    for iss in issues:
        sev = iss.get("severity", "info")
        by_severity.setdefault(sev, []).append(iss)

    symbols = {"error": "✗", "warning": "!", "info": "·"}
    for sev in ("error", "warning", "info"):
        for iss in by_severity.get(sev, []):
            sym = symbols.get(sev, "·")
            pg = f" [page {iss['page']}]" if iss.get("page") else ""
            tag = f" <{iss['tag']}>" if iss.get("tag") else ""
            f.write(f"[{sym}] [{iss['code']}]{pg}{tag} {iss['message']}\n")

    error_count = len(by_severity.get("error", []))
    warn_count = len(by_severity.get("warning", []))
    f.write(f"\n{len(issues)} issue(s): {error_count} error(s), {warn_count} warning(s)\n")


def _write_issues_output(f, issues: list[dict], json_output: bool) -> None:
    """Handle output for 'issues' mode."""
    if json_output:
        f.write(json.dumps({"issues": issues}, indent=2))
        f.write("\n")
    else:
        _format_plaintext_issues(f, issues)


# ---------------------------------------------------------------------------
# Main CLI Hook
# ---------------------------------------------------------------------------


def dump_tags_cli_hook(result, stage, _pipeline) -> None:
    output_file = from_result_meta(result, c.META_OUTPUT_FILE)
    mode = (result.meta or {}).get("mode", "reading_order")
    json_output = (result.meta or {}).get("json_output", False)

    with smart_open_maybe_dash(output_file) as f:
        if mode == "tree":
            _write_tree_output(f, result.data, json_output)
        elif mode == "reading_order":
            _write_reading_order_output(f, result.data)
        elif mode == "issues":
            _write_issues_output(f, result.data, json_output)


# ---------------------------------------------------------------------------
# Operation
# ---------------------------------------------------------------------------


@register_operation(
    "dump_tags",
    tags=["info", "accessibility", "structure", "tags", "tagging"],
    type="single input operation",
    desc="Inspect the PDF structure tree and reading order",
    long_desc=_DUMP_TAGS_LONG_DESC,
    examples=_DUMP_TAGS_EXAMPLES,
    cli_hook=dump_tags_cli_hook,
    usage=(
        "<input> dump_tags [reading_order|tree|issues] "
        "[json] [annotate] [streams=false] [<page_spec>...] [output <output>]"
    ),
    args=([c.OPERATION_NAME, c.INPUT_PDF, c.OPERATION_ARGS], {"output_file": c.OUTPUT}),
    skip_pipeline_save=True,
)
def dump_tags(op_name, pdf: pikepdf.Pdf, op_args, output_file=None) -> OpResult:
    """
    Inspect the PDF structure tree in one of three modes:
    reading_order (default), tree, or issues.
    """
    mode, page_specs, json_output, annotate, show_streams = _parse_args(op_args, op_name)

    # Resolve target pages
    num_pages = len(pdf.pages)
    if page_specs:
        target_page_nums: set[int] = set(page_numbers_matching_page_specs(page_specs, num_pages))
    else:
        target_page_nums = set(range(1, num_pages + 1))

    # Dispatch
    if mode == "tree":
        data = _run_tree(pdf, json_output, output_file)

    elif mode == "reading_order":
        data = _reading_order_lines(pdf, target_page_nums, annotate, show_streams)

    elif mode == "issues":
        data = _run_issues(pdf, target_page_nums)

    else:
        raise ValueError(f"Unknown dump_tags mode: {mode!r}")

    return OpResult(
        success=True,
        data=data,
        pdf=pdf,
        is_discardable=True,
        meta={
            c.META_OUTPUT_FILE: output_file,
            "mode": mode,
            "json_output": json_output,
        },
    )
