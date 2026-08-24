# src/pdftl/operations/helpers/auto_bookmark.py

"""Automatically add bookmarks to a PDF, using opendataloader."""

import logging
from pdftl.utils.dependencies import ensure_dependencies
from pdftl.utils.run_opendataloader import run_opendataloader_extraction

logger = logging.getLogger(__name__)

# Heading-relevant pdfua_tag values per the OpenDataLoader schema.
# "role" is not a field the schema (or observed output) actually produces.
_HEADING_PDFUA_TAGS = {"H", "H1", "H2", "H3", "H4", "H5", "H6"}


def auto_bookmark_pdf(pdf):
    """Extract headings using OpenDataLoader JSON streaming and inject bookmarks."""
    logger.debug("Starting auto-bookmark extraction pipeline")
    ensure_dependencies(
        feature_name="auto bookmarks",
        dependencies={"opendataloader_pdf": "opendataloader-pdf"},
        required_executables=["java"],
        extra_tag="tag",
    )
    import pikepdf

    data = run_opendataloader_extraction(pdf)
    if data is None:
        return pdf

    all_nodes = _flatten_tree(data)
    logger.debug("Flattened document tree into %d total nodes", len(all_nodes))

    headings = [n for n in all_nodes if _is_heading(n)]
    logger.debug("Identified %d heading elements matching bookmark criteria", len(headings))

    if not headings:
        logger.debug("No valid headings found in document structure; returning original PDF")
        return pdf

    _apply_bookmarks_to_outline(pikepdf, pdf, headings)
    return pdf


def _flatten_tree(obj):
    """Iteratively flatten OpenDataLoader JSON tree structures."""
    nodes = []
    stack = [obj]
    while stack:
        curr = stack.pop()
        if isinstance(curr, dict):
            nodes.append(curr)
            children = curr.get("kids") or curr.get("elements") or []
            if isinstance(children, list):
                stack.extend(reversed(children))
        elif isinstance(curr, list):
            stack.extend(reversed(curr))
    return nodes


def _has_excessive_single_chars(content: str) -> bool:
    THRESHOLD_RATIO = 0.4
    tokens = content.split()
    if not tokens:
        return True
    single_char_count = sum(1 for t in tokens if len(t) == 1)
    if all(len(t) == 1 for t in tokens):
        return True
    if len(tokens) > 1 and (single_char_count / len(tokens)) > THRESHOLD_RATIO:
        return True
    return False


def _is_heading(node):
    """Check if document node represents a heading."""
    MIN_CONTENT_LEN = 3
    MIN_CONTENT_ALNUMSP_RATIO = 0.70

    if not isinstance(node, dict):
        return False
    content = str(node.get("content") or "")

    if len(content) < MIN_CONTENT_LEN:
        return False

    letters_and_spaces = sum(1 for char in content if char.isalpha() or char.isspace())
    if letters_and_spaces / len(content) < MIN_CONTENT_ALNUMSP_RATIO:
        return False

    if _has_excessive_single_chars(content):
        return False

    n_type = str(node.get("type", ""))
    pdfua_tag = str(node.get("pdfua_tag", ""))
    return n_type == "heading" or pdfua_tag in _HEADING_PDFUA_TAGS


def _parse_page_idx(node):
    """Extract zero-based page index from node metadata."""
    page_num = node.get("page number") or node.get("page") or 1
    try:
        return max(0, int(page_num) - 1)
    except (ValueError, TypeError):
        return 0


def _parse_heading_level(node):
    """Extract numeric heading level from node metadata."""
    level = node.get("heading level")
    if isinstance(level, int):
        return max(1, level)

    tag = str(node.get("pdfua_tag") or "")
    if tag.startswith("H") and tag[1:].isdigit():
        return max(1, int(tag[1:]))
    return 1


def _parse_bbox(node):
    """Extract (left, top) coordinates from bounding box element.

    Per the OpenDataLoader schema, bounding box arrays are
    [left, bottom, right, top], so `top` is bbox[3] directly.
    """
    bbox = node.get("bounding box") or node.get("bbox") or node.get("bounding_box")
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return None, None
    try:
        left = float(bbox[0])
        top = float(bbox[3])
        return left, top
    except (ValueError, TypeError):
        return None, None


def _create_outline_item(pikepdf, title, page_idx, left, top):
    """Construct pikepdf.OutlineItem with XYZ destination when bounds are present."""
    if left is not None and top is not None:
        logger.debug(
            "Inserting bookmark with XYZ view: '%s' (Page: %d, Left: %.2f, Top: %.2f)",
            title,
            page_idx,
            left,
            top,
        )
        # Setting zoom=None results in [XYZ, x, y, 0].
        # Seems no way to get null instead of 0 using the pikepdf API.
        # Shouldn't matter per PDF spec.
        return pikepdf.OutlineItem(title, page_idx, page_location="XYZ", left=left, top=top)

    logger.debug("Inserting bookmark: '%s' (Page: %d)", title, page_idx)
    return pikepdf.OutlineItem(title, page_idx)


def _apply_bookmarks_to_outline(pikepdf, pdf, headings):
    """Build hierarchy stack and apply bookmarks to PDF outline root."""
    with pdf.open_outline() as outline:
        outline.root.clear()
        stack = [(0, outline.root)]
        added_count = 0

        for h in headings:
            title = str(h.get("content") or h.get("text") or "").strip()
            if not title:
                logger.debug("Skipping heading element with empty text content: %s", h)
                continue

            page_idx = _parse_page_idx(h)
            level = _parse_heading_level(h)
            left, top = _parse_bbox(h)

            while stack and stack[-1][0] >= level:
                stack.pop()

            parent = stack[-1][1]
            item = _create_outline_item(pikepdf, title, page_idx, left, top)

            if isinstance(parent, list):
                parent.append(item)
            else:
                parent.children.append(item)

            stack.append((level, item))
            added_count += 1

    logger.debug("Successfully applied %d bookmarks to PDF outline", added_count)
