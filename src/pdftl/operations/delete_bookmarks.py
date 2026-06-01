# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/delete_bookmarks.py

"""Delete bookmarks (outline entries) from a PDF."""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pikepdf

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.utils.destinations import get_named_destinations, get_page_map, resolve_dest_to_page_num

logger = logging.getLogger(__name__)

_LONG_DESC = """
Delete bookmarks (outline entries) from a PDF.

Without a page spec, all bookmarks are removed. With one or more page specs,
only bookmarks whose destination falls within the matched pages are targeted
for deletion.

Multiple page specs are treated as a union — a bookmark is matched if its
destination page falls within any of the given specs. Page specs use the
standard pdftl syntax; see `pdftl help pages` for details.

Flags (`recursive`, `no_dest`) apply to the entire operation, not per page spec.

### Deletion model

Bookmarks are matched by destination page. A bookmark is "matched" if the page
it points to falls within the union of all given page specs.

Two modes control how matched bookmarks with unmatched descendants are handled:

**safe (default)**
A matched bookmark is only deleted if all of its descendants are also matched
(and will therefore also be deleted). If a matched bookmark has any unmatched
descendant anywhere in its subtree, it is skipped and a warning is emitted.
The tree structure of surviving bookmarks is always preserved exactly — no
promotion occurs.

**recursive**
A matched bookmark and its entire subtree are deleted, regardless of whether
any descendants are matched. Use with care.

The algorithm processes the tree bottom-up (post-order), so children are
evaluated before their parents. This means a matched parent whose children
are all independently matched will be cleanly deleted in safe mode, even
without specifying `recursive`.

### Destination-less bookmarks

Some bookmarks have no page destination — they are purely structural, grouping
children without linking to any page. By default these are treated as unmatched:
they survive, and they block deletion of matched ancestors in safe mode.

**no_dest**
Treat destination-less bookmarks as matched. They no longer block ancestor
deletion in safe mode, and are themselves deleted if all their descendants are
also matched (or they have no surviving children after deletion).
With `recursive`, `no_dest` is redundant — descendants are swept up regardless.

### Worked examples

Given the outline tree (page numbers shown, nested lists are children):

    [1, [2, 3], 4, [5, [6]]]

    delete_bookmarks 2
        =>  [1, [3], 4, [5, [6]]]
            bookmark 2 has no unmatched children, deleted cleanly.

    delete_bookmarks 1
        =>  [1, [2, 3], 4, [5, [6]]]   (unchanged)
            safe: bookmark 1 skipped+warned because child 3 is unmatched.

    delete_bookmarks 1-3
        =>  [4, [5, [6]]]
            safe: 2 deleted first (no unmatched children); 3 deleted (no
            children); 1 now has no unmatched descendants, deleted too.

    delete_bookmarks 1 recursive
        =>  [4, [5, [6]]]
            bookmark 1 and entire subtree [2, 3] deleted.

    delete_bookmarks 5
        =>  [1, [2, 3], 4, [5, [6]]]   (unchanged)
            safe: bookmark 5 skipped+warned because child 6 is unmatched.

    delete_bookmarks 5-6
        =>  [1, [2, 3], 4]
            safe: 6 deleted first; 5 now has no unmatched descendants, deleted.

    delete_bookmarks 1-3 5-6
        =>  [4]
            multiple page specs, union of pages 1,2,3,5,6 matched.

### Clearing all bookmarks

To remove the entire outline, omit the page spec:

    pdftl in.pdf delete_bookmarks output out.pdf
"""

_EXAMPLES = [
    {
        "cmd": "in.pdf delete_bookmarks output out.pdf",
        "desc": "Delete all bookmarks",
    },
    {
        "cmd": "in.pdf delete_bookmarks 1-5 output out.pdf",
        "desc": "Delete bookmarks pointing to pages 1 through 5 (safe mode)",
    },
    {
        "cmd": "in.pdf delete_bookmarks 1-5 7-20odd output out.pdf",
        "desc": "Delete bookmarks pointing to pages 1-5 or odd pages 7-20",
    },
    {
        "cmd": "in.pdf delete_bookmarks 3 recursive output out.pdf",
        "desc": "Delete bookmarks pointing to page 3 and all their descendants",
    },
    {
        "cmd": "in.pdf delete_bookmarks 1-5 no_dest output out.pdf",
        "desc": (
            "Delete bookmarks pointing to pages 1-5, "
            "treating destination-less bookmarks as matched"
        ),
    },
    {
        "cmd": "in.pdf delete_bookmarks odd recursive output out.pdf",
        "desc": "Delete bookmarks pointing to odd pages, including all descendants",
    },
]


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

_FLAG_RECURSIVE = "recursive"
_FLAG_NO_DEST = "no_dest"
_KNOWN_FLAGS = {_FLAG_RECURSIVE, _FLAG_NO_DEST}


def _parse_args(op_args: list, num_pages: int) -> tuple[set[int] | None, bool, bool]:
    """
    Parse op_args into (matched_pages, recursive, no_dest).

    matched_pages is None when no page specs were given (delete all).
    Flags are consumed from op_args; remaining tokens are treated as page specs.
    """
    from pdftl.utils.page_specs import page_numbers_matching_page_specs

    flags = set()
    spec_tokens = []
    for token in op_args:
        if token in _KNOWN_FLAGS:
            flags.add(token)
        else:
            spec_tokens.append(token)

    recursive = _FLAG_RECURSIVE in flags
    no_dest = _FLAG_NO_DEST in flags

    if not spec_tokens:
        return None, recursive, no_dest

    # Union of all page specs
    matched_pages: set[int] = set(page_numbers_matching_page_specs(spec_tokens, num_pages))

    return matched_pages, recursive, no_dest


# ---------------------------------------------------------------------------
# Core deletion logic
# ---------------------------------------------------------------------------


def _item_page_num(item, page_map: dict, named_destinations: dict) -> int | None:
    """Resolve an OutlineItem to a 1-based page number, or None if no destination."""
    resolved = resolve_dest_to_page_num(item, page_map, named_destinations)
    return resolved.page_num if resolved else None


def _process_item_safe(
    item,
    matched_pages: set[int],
    page_map: dict,
    named_destinations: dict,
    no_dest: bool,
) -> tuple[bool, list]:
    """
    Post-order processing for safe mode.

    Returns (should_delete, surviving_children).

    A node is deletable iff:
      - it is matched (or is destination-less and no_dest is True), AND
      - all of its children are also deletable (post-order).

    If a node is matched but has any non-deletable child, it is skipped
    with a warning and its surviving children are kept in place.
    """
    # --- Recurse into children first (post-order) ---
    surviving_children = []
    all_children_deletable = True
    for child in item.children:
        child_deletable, child_survivors = _process_item_safe(
            child, matched_pages, page_map, named_destinations, no_dest
        )
        if child_deletable:
            # child will be deleted; its survivors (always empty in this branch)
            # are not added
            pass
        else:
            all_children_deletable = False
            # Rebuild child with its own surviving children
            child.children[:] = child_survivors
            surviving_children.append(child)

    # --- Determine whether this node is matched ---
    page_num = _item_page_num(item, page_map, named_destinations)
    if page_num is None:
        # Destination-less bookmark
        is_matched = no_dest
    else:
        is_matched = page_num in matched_pages

    # --- Deletion decision ---
    if is_matched and all_children_deletable:
        # All children gone, this node goes too
        return True, []

    if is_matched and not all_children_deletable:
        # Would like to delete but can't — warn and keep
        logger.warning(
            "Bookmark %r (page %s) was not deleted because it has descendants "
            "pointing to pages outside the specified range. "
            "Use 'recursive' to delete it and its entire subtree.",
            item.title,
            page_num,
        )
        return False, surviving_children

    # Not matched — keep, with updated children
    return False, surviving_children


def _process_item_recursive(
    item,
    matched_pages: set[int],
    page_map: dict,
    named_destinations: dict,
    no_dest: bool,
) -> tuple[bool, list]:
    """
    Post-order processing for recursive mode.

    If a node is matched, delete it and its entire subtree.
    Otherwise keep it, but recurse into children to delete matched descendants.
    """
    page_num = _item_page_num(item, page_map, named_destinations)
    if page_num is None:
        is_matched = no_dest
    else:
        is_matched = page_num in matched_pages

    if is_matched:
        # Delete this node and everything below — don't recurse
        return True, []

    # Not matched — recurse into children, keeping survivors
    surviving_children = []
    for child in item.children:
        child_deletable, child_survivors = _process_item_recursive(
            child, matched_pages, page_map, named_destinations, no_dest
        )
        if not child_deletable:
            child.children[:] = child_survivors
            surviving_children.append(child)

    return False, surviving_children


def _delete_from_outline(
    pdf: "pikepdf.Pdf",
    matched_pages: set[int] | None,
    recursive: bool,
    no_dest: bool,
) -> None:
    """
    Mutates the PDF outline in-place according to deletion rules.

    matched_pages=None means delete everything.
    """
    with pdf.open_outline() as outline:
        if matched_pages is None:
            # Fast path: clear everything
            outline.root.clear()
            return

        page_map = get_page_map(pdf.pages)
        named_destinations = get_named_destinations(pdf) or {}

        process_fn = _process_item_recursive if recursive else _process_item_safe

        new_root = []
        for item in outline.root:
            deletable, survivors = process_fn(
                item, matched_pages, page_map, named_destinations, no_dest
            )
            if deletable:
                pass  # drop it
            else:
                item.children[:] = survivors
                new_root.append(item)

        outline.root[:] = new_root


# ---------------------------------------------------------------------------
# Operation registration
# ---------------------------------------------------------------------------


@register_operation(
    "delete_bookmarks",
    tags=["in_place", "bookmarks", "outlines", "delete"],
    type="single input operation",
    desc="Delete bookmarks from a PDF",
    long_desc=_LONG_DESC,
    usage=(
        "<input> delete_bookmarks [<page_spec>...] [recursive] [no_dest] "
        "output <file> [<option>...]"
    ),
    examples=_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def delete_bookmarks(pdf: "pikepdf.Pdf", op_args: list) -> OpResult:
    """
    Delete bookmarks from a PDF outline, optionally filtered by page spec.
    """
    num_pages = len(pdf.pages)
    matched_pages, recursive, no_dest = _parse_args(op_args or [], num_pages)

    _delete_from_outline(pdf, matched_pages, recursive, no_dest)

    return OpResult(success=True, pdf=pdf)
