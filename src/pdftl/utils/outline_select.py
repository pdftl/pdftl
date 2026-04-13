# src/pdftl/utils/outline_select.py

from typing import TYPE_CHECKING

from pdftl.utils.destinations import get_named_destinations, resolve_dest_to_page_num

if TYPE_CHECKING:
    import pikepdf


def get_outlines_to_level_pages(pdf: "pikepdf.Pdf", n: int, last_level_only=False) -> list:
    """Returns a list of all destination pages of outline items up to
    and including (or, only at) level n."""
    outlines = get_outlines_to_level(pdf, n, last_level_only)
    page_map = {page.objgen: i + 1 for i, page in enumerate(pdf.pages)}
    named_destinations = get_named_destinations(pdf)
    resolved = [resolve_dest_to_page_num(x, page_map, named_destinations) for x in outlines]
    return [x.page_num for x in resolved if x]


def get_outlines_to_level(pdf: "pikepdf.Pdf", n: int, last_level_only=False) -> list:
    """Returns a list of all outline items up to and including (or, only at) level n."""

    def extract(items, current_level):
        # If we exceed the target level, stop collecting
        if current_level > n:
            return []

        result = []
        for item in items:
            # 1. Add the current item (unless we only want the last level)
            if not (last_level_only and current_level != n):
                result.append(item)

            # 2. Immediately dig into its children to maintain document order
            result.extend(extract(item.children, current_level + 1))

        return result

    # Open the outline and start the recursion at level 1 (the root)
    with pdf.open_outline() as outline:
        return extract(outline.root, 1)
