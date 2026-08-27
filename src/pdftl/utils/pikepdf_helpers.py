# src/pdftl/utils/pikepdf_helpers.py

from typing import Union, Optional

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pikepdf


# ==============================================================================
# PDF INHERITABLE ATTRIBUTES: READ & WRITE RULES
# ==============================================================================
# Page tree nodes inherit attributes (/Resources, /MediaBox, /CropBox, /Rotate).
#
# READ RULE:
#   Always use get_inheritable(page, key) to resolve inherited values.
#   Direct key checks ('key in page') miss parent node definitions.
#
# WRITE RULES:
#   * Atomic Values (/MediaBox, /CropBox, /Rotate):
#     Write directly to the page ('page[key] = val'). Local setting cleanly
#     overrides parent inheritance for that specific page.
#
#   * Container Dictionaries (/Resources):
#     Always use ensure_page_resources(page) before mutation. Initializing a fresh
#     local dict shadows parent keys (hiding fonts/XObjects), while direct parent
#     mutation leaks side-effects across sibling pages.
# ==============================================================================


def get_inheritable(
    page: Union["pikepdf.Page", "pikepdf.Dictionary"], key: str, max_depth: int = 50
) -> Optional["pikepdf.Object"]:
    """Walk up the PDF /Parent tree to resolve an inheritable page attribute."""
    import pikepdf

    node = page.obj if isinstance(page, pikepdf.Page) else page
    seen = set()
    depth = 0

    while node is not None and depth < max_depth:
        depth += 1

        # Track indirect object IDs to prevent infinite loops on malformed cyclic trees
        obj_id = getattr(node, "objgen", None)
        if obj_id and obj_id != (0, 0):
            if obj_id in seen:
                break
            seen.add(obj_id)

        if key in node:
            return node[key]

        node = node.get("/Parent")

    return None
