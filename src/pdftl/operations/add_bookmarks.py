# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/add_bookmarks.py

"""Add one or more top-level bookmarks to a PDF."""

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pikepdf

from pdftl.exceptions import InvalidArgumentError
import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.utils.text_templates import build_page_context, build_static_context, render_template
from pdftl.utils.page_specs import parse_specs
from pdftl.utils.page_specs.resolver import _filter_page_numbers

logger = logging.getLogger(__name__)

_LONG_DESC = """
Add one or more top-level bookmarks to a PDF.

Each `<spec>` has the format:

    <page_range><delimiter><title><delimiter>[(<options>)]

`<delimiter>` must be a single non-alphanumeric, non-parenthesis character
(e.g., `/`, `!`, `#`). The same delimiter must be used on both sides of the title.

### Page range

The page range selects which pages receive bookmarks. Each matched page gets
its own bookmark. Standard pdftl page-spec syntax applies (see `pdftl help pages`).

### Title

The title string supports `{variable}` substitution, using the same variables
as `add_text`:

  - `{page}`            current page number (1-based)
  - `{n}` or `{count}`  1-based sequential counter within the matched
                         pages of the current spec
  - `{total}`           total number of pages in the PDF
  - `{filename}`        input filename including extension
  - `{filename_base}`   input filename without extension
  - `{filepath}`        full path to the input file

Variables are expanded per page: `{filename_base} p.{page}` on a 10-page
document produces ten distinct titles.

### Options

Options are comma-separated `key=value` pairs inside parentheses:

  - `position=<pos>`  Where in the top-level bookmark list to insert this batch.
     - `head`  prepend before all existing bookmarks (default).
     - `tail`  append after all existing bookmarks.
  - `uri=<url>`         Create a web link instead of an internal page destination.
  - `launch=<file>`     Create a launch action to open an external file.
  - `named=<action>`    Create a named action (e.g., NextPage, PrevPage).
  - `dest=<name>`       Link to an internal named destination instead of a page number.
  - `color=<r> <g> <b>` Set the text color (RGB format: e.g. 1.0 0.0 0.0 for red).
  - `bold=true/false`
  - `italic=true/false`

### Ordering of multiple specs

All `head` specs are collected in argument order and prepended as a batch.
All `tail` specs are collected in argument order and appended as a batch.
The final outline is:

    <head batch> <existing bookmarks> <tail batch>

Within each batch, bookmarks are ordered by the sequence the page range produces
them.
"""

_EXAMPLES = [
    {
        "cmd": "in.pdf add_bookmarks '1/{filename_base}/' output out.pdf",
        "desc": "Add a top-level bookmark for page 1 titled with the filename (prepended)",
    },
    {
        "cmd": (
            "in.pdf add_bookmarks '1/{filename_base}/(position=tail, bold=true)' output out.pdf"
        ),
        "desc": "Same but appended after existing bookmarks and styled bold",
    },
    {
        "cmd": (
            "in.pdf add_bookmarks '1/Website/(uri=https://example.com, color=0.0 0.5 1.0)' "
            "output out.pdf"
        ),
        "desc": "Add an external blue URI link triggered from page 1's iteration",
    },
    {
        "cmd": "in.pdf add_bookmarks '1-3/Chapter {page}/' output out.pdf",
        "desc": "Add a bookmark for each of pages 1-3, titled 'Chapter N' for N=1,2,3",
    },
    {
        "cmd": "in.pdf add_bookmarks '3,5,6/Chapter {n+1}/' output out.pdf",
        "desc": "Add a bookmark for each of pages 2,5,6, titled 'Chapter N' for N=2,3,4",
    },
]

_SPEC_RE = re.compile(
    r"^(?P<range>[^()\s]+?)"  # page range (non-greedy, no parens/spaces)
    r"(?P<delim>[^a-zA-Z0-9()\s])"  # delimiter
    r"(?P<title>.*?)"  # title (may be empty)
    r"(?P=delim)"  # closing delimiter
    r"(?:\((?P<opts>[^)]*)\))?$",  # optional (key=value,...) — no nesting
    re.DOTALL,
)

_POSITION_HEAD = "head"
_POSITION_TAIL = "tail"
_VALID_POSITIONS = {_POSITION_HEAD, _POSITION_TAIL}


def _parse_option(key: str, val: str, options: dict, raw_spec: str):
    """Parses a single key-value option to maintain low cognitive complexity."""
    if key == "position":
        if val not in _VALID_POSITIONS:
            raise InvalidArgumentError(
                f"Invalid position={val!r} in spec {raw_spec!r}. "
                f"Valid values: {', '.join(sorted(_VALID_POSITIONS))}"
            )
        options["position"] = val
    elif key in ("uri", "launch", "named", "dest"):
        options[key] = val
    elif key in ("bold", "italic"):
        options[key] = val.lower() == "true"
    elif key == "color":
        try:
            color_vals = [float(c) for c in val.split()]
            if len(color_vals) != 3:
                raise ValueError
            options["color"] = color_vals
        except (ValueError, TypeError) as err:
            raise InvalidArgumentError(
                f"Invalid color {val!r} in spec {raw_spec!r}. Expected 3 space-separated numbers."
            ) from err
    else:
        raise InvalidArgumentError(f"Unknown option {key!r} in spec {raw_spec!r}")


def _parse_spec(raw: str) -> tuple[str, str, dict]:
    """
    Parse a single bookmark spec string.

    Returns (page_range_str, title_template, options_dict).
    Raises ValueError on malformed input.
    """
    m = _SPEC_RE.match(raw.strip())
    if m is None:
        raise InvalidArgumentError(
            f"Invalid add_bookmarks spec {raw!r}. "
            "Expected format: <page_range><delim><title><delim>[(options)]  "
            "e.g. '1/{filename_base}/' or '1-3/Chapter {page}/(position=tail)'"
        )

    page_range = m.group("range")
    title_template = m.group("title")
    opts_str = m.group("opts") or ""

    options = {"position": _POSITION_HEAD}  # default
    for part in opts_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise InvalidArgumentError(
                f"Invalid option {part!r} in spec {raw!r}, expected key=value"
            )
        key, _, val = part.partition("=")
        _parse_option(key.strip().lower(), val.strip(), options, raw)

    return page_range, title_template, options


def _build_node_dict(title: str, page_num: int, options: dict) -> dict:
    """Constructs a structural dictionary for the builder helper."""
    node = {"title": title}
    if "uri" in options:
        node["uri"] = options["uri"]
    elif "launch" in options:
        # A simple string represents a standard filespec value per ISO 32000-2 §7.11.2,
        # which our reconstructor natively and properly builds into outline item dictionaries.
        node["action"] = {"S": {"__name__": "/Launch"}, "F": options["launch"]}
    elif "named" in options:
        node["action"] = {"S": {"__name__": "/Named"}, "N": {"__name__": f"/{options['named']}"}}
    elif "dest" in options:
        node["dest"] = options["dest"]
    else:
        node["page"] = page_num

    for k in ("bold", "italic", "color"):
        if k in options:
            node[k] = options[k]
    return node


def _build_bookmarks(
    pdf: "pikepdf.Pdf",
    specs: list[str],
) -> tuple[list, list]:
    """Parse all specs and return (head_items, tail_items) as lists of OutlineItem."""
    from pdftl.info.toc import _build_item

    num_pages = len(pdf.pages)
    head_items: list = []
    tail_items: list = []
    static_context = build_static_context(pdf)

    for raw in specs:
        page_range, title_template, options = _parse_spec(raw)
        position = options.get("position", _POSITION_HEAD)
        options_clean = {k: v for k, v in options.items() if k != "position"}

        matched_pages = []
        for page_spec in parse_specs([page_range], num_pages):
            direction = -1 if page_spec.end < page_spec.start else 1
            initial_pages = list(
                range(page_spec.start, page_spec.end + direction, direction * page_spec.step)
            )
            final_pages = _filter_page_numbers(
                initial_pages,
                page_spec.qualifiers,
                page_spec.omissions,
                pdf,
            )
            matched_pages.extend(final_pages)

        for n, page_num in enumerate(matched_pages, 1):
            page = pdf.pages[page_num - 1]
            context = build_page_context(static_context, page, page_num)
            context["n"] = n
            title = render_template(title_template, context)

            node = _build_node_dict(title, page_num, options_clean)
            item = _build_item(node, pdf)

            if position == _POSITION_HEAD:
                head_items.append(item)
            else:
                tail_items.append(item)

    return head_items, tail_items


def _apply_bookmarks(
    pdf: "pikepdf.Pdf",
    head_items: list,
    tail_items: list,
) -> None:
    """Mutate the PDF outline in-place: prepend head, append tail."""
    with pdf.open_outline() as outline:
        existing = list(outline.root)
        outline.root[:] = head_items + existing + tail_items


@register_operation(
    "add_bookmarks",
    tags=["in_place", "bookmarks", "outlines", "add"],
    type="single input operation",
    desc="Add top-level bookmarks",
    long_desc=_LONG_DESC,
    usage="<input> add_bookmarks <spec>... output <file> [<option>...]",
    examples=_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def add_bookmarks(pdf: "pikepdf.Pdf", op_args: list) -> OpResult:
    """Add one or more top-level bookmarks to a PDF outline."""
    if not op_args:
        raise InvalidArgumentError("add_bookmarks requires at least one spec argument.")

    head_items, tail_items = _build_bookmarks(pdf, op_args)
    _apply_bookmarks(pdf, head_items, tail_items)

    return OpResult(success=True, pdf=pdf)
