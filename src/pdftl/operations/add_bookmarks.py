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

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation

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

A bare range without a delimiter adds a bookmark pointing to page 1 using
an empty title — use the full spec form for any real usage.

### Title

The title string supports `{variable}` substitution, using the same variables
as `add_text`:

  - `{page}`           — current page number (1-based)
  - `{total}`          — total number of pages in the PDF
  - `{filename}`       — input filename including extension
  - `{filename_base}`  — input filename without extension
  - `{filepath}`       — full path to the input file

Variables are expanded per page: `{filename_base} p.{page}` on a 10-page
document produces ten distinct titles.

### Options

Options are comma-separated `key=value` pairs inside parentheses:

  `position=<pos>`  Where in the top-level bookmark list to insert this batch.
                    `head` — prepend before all existing bookmarks (default).
                    `tail` — append after all existing bookmarks.

No other options are currently supported, but the parenthesised syntax is
an extension point (e.g., `bold=true`, `color=...` may be added later).

### Ordering of multiple specs

All `head` specs are collected in argument order and prepended as a batch.
All `tail` specs are collected in argument order and appended as a batch.
The final outline is:

    <head batch> <existing bookmarks> <tail batch>

Within each batch, bookmarks are ordered by the sequence the page range produces
them — so a reversed range `3-1/{title}/` produces three bookmarks in the order
page 3, page 2, page 1.

### Worked examples

    add_bookmarks '1/{filename_base}/'
        Add a bookmark pointing to page 1, titled with the base filename,
        prepended before any existing bookmarks (position=head is default).

    add_bookmarks '1/{filename_base}/(position=tail)'
        Same but appended after existing bookmarks.

    add_bookmarks '1-3/Chapter {page}/'
        Add three bookmarks for pages 1, 2, 3 titled "Chapter 1",
        "Chapter 2", "Chapter 3". All prepended as a batch.

    add_bookmarks '1/{filename_base}/(position=head)' '5/Appendix/(position=tail)'
        Prepend a title bookmark and append an appendix bookmark in one pass.
"""

_EXAMPLES = [
    {
        "cmd": "in.pdf add_bookmarks '1/{filename_base}/' output out.pdf",
        "desc": "Add a top-level bookmark for page 1 titled with the filename (prepended)",
    },
    {
        "cmd": "in.pdf add_bookmarks '1/{filename_base}/(position=tail)' output out.pdf",
        "desc": "Same but appended after existing bookmarks",
    },
    {
        "cmd": "in.pdf add_bookmarks '1-3/Chapter {page}/' output out.pdf",
        "desc": "Add a bookmark for each of pages 1-3, titled 'Chapter N'",
    },
    {
        "cmd": (
            "in.pdf add_bookmarks '1/{filename_base}/(position=head)' "
            "'5/Appendix/(position=tail)' output out.pdf"
        ),
        "desc": "Prepend a title bookmark and append an appendix bookmark",
    },
    {
        "cmd": (
            r"in.pdf add_bookmarks '1-end/{page}: {filename_base}/(position=tail)' "
            "output out.pdf"
        ),
        "desc": "Add one bookmark per page, appended",
    },
]

# ---------------------------------------------------------------------------
# Spec parsing
# ---------------------------------------------------------------------------

# Matches: <page_range><delim><title><delim>[(options)]
# The delimiter is any single non-alphanumeric, non-paren character.
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


def _parse_spec(raw: str) -> tuple[str, str, str]:
    """
    Parse a single bookmark spec string.

    Returns (page_range_str, title_template, position).
    Raises ValueError on malformed input.
    """
    m = _SPEC_RE.match(raw.strip())
    if not m:
        raise ValueError(
            f"Invalid add_bookmarks spec {raw!r}. "
            "Expected format: <page_range><delim><title><delim>[(options)]  "
            "e.g. '1/{filename_base}/' or '1-3/Chapter {page}/(position=tail)'"
        )

    page_range = m.group("range")
    title_template = m.group("title")
    opts_str = m.group("opts") or ""

    position = _POSITION_HEAD  # default
    for part in opts_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"Invalid option {part!r} in spec {raw!r} — expected key=value")
        key, _, val = part.partition("=")
        key = key.strip().lower()
        val = val.strip()
        if key == "position":
            if val not in _VALID_POSITIONS:
                raise ValueError(
                    f"Invalid position={val!r} in spec {raw!r}. "
                    f"Valid values: {', '.join(sorted(_VALID_POSITIONS))}"
                )
            position = val
        else:
            raise ValueError(f"Unknown option {key!r} in spec {raw!r}")

    return page_range, title_template, position


# ---------------------------------------------------------------------------
# Title variable expansion
# ---------------------------------------------------------------------------


def _expand_title(template: str, page_num: int, pdf: "pikepdf.Pdf") -> str:
    """
    Expand {variable} placeholders in a title template.

    Supports {page}, {total}, {filename}, {filename_base}, {filepath}.
    Unknown variables are left unexpanded rather than raising.
    """
    total = len(pdf.pages)

    # Retrieve source-file metadata stashed by the pipeline, if available.
    # The pipeline stashes this in pdf.docinfo or a custom attribute; fall back
    # gracefully to empty strings so the operation is self-contained.
    source_filename = getattr(pdf, "_source_filename", None) or ""
    source_filepath = getattr(pdf, "_source_filepath", None) or source_filename

    import os

    filename = os.path.basename(source_filepath) if source_filepath else source_filename
    filename_base = os.path.splitext(filename)[0] if filename else ""
    filepath = source_filepath or ""

    replacements = {
        "page": str(page_num),
        "total": str(total),
        "filename": filename,
        "filename_base": filename_base,
        "filepath": filepath,
    }

    def _replace(m):
        key = m.group(1).strip()
        return replacements.get(key, m.group(0))  # leave unknown vars intact

    return re.sub(r"\{([^}]+)\}", _replace, template)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _build_bookmarks(
    pdf: "pikepdf.Pdf",
    specs: list[str],
) -> tuple[list, list]:
    """
    Parse all specs and return (head_items, tail_items) as lists of OutlineItem.

    Items are ready to insert into an outline root.
    """
    from pikepdf import OutlineItem
    from pdftl.utils.page_specs import parse_specs
    from pdftl.utils.page_specs.resolver import _filter_page_numbers

    num_pages = len(pdf.pages)
    head_items: list = []
    tail_items: list = []

    for raw in specs:
        page_range, title_template, position = _parse_spec(raw)

        # Resolve specs sequentially while preserving custom order/steps
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

        for page_num in matched_pages:
            title = _expand_title(title_template, page_num, pdf)
            # OutlineItem destination is a 0-based page index
            item = OutlineItem(title, page_num - 1)
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
    """Mutate the PDF outline in-place: prepend head_items, append tail_items."""
    with pdf.open_outline() as outline:
        existing = list(outline.root)
        outline.root[:] = head_items + existing + tail_items


# ---------------------------------------------------------------------------
# Operation registration
# ---------------------------------------------------------------------------


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
    """
    Add one or more top-level bookmarks to a PDF outline.
    """
    if not op_args:
        raise ValueError("add_bookmarks requires at least one spec argument.")

    head_items, tail_items = _build_bookmarks(pdf, op_args)
    _apply_bookmarks(pdf, head_items, tail_items)

    return OpResult(success=True, pdf=pdf)
