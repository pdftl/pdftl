# src/pdftl/info/set_info.py

"""Set metadata in a PDF.

Public: set_metadata_in_pdf"""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pikepdf
    from pikepdf import OutlineItem
    from pdftl.info.info_types import PdfInfo, BookmarkEntry, PageMediaEntry, PageLabelEntry


import pdftl.core.constants as c

logger = logging.getLogger(__name__)

CANNOT_SET_PDFID1 = (
    "Cannot set PdfID1. This is a limitation of pikepdf."
    " See also PDF 32000-1:2008 section 14.4."
)


def set_metadata_in_pdf(pdf: "pikepdf.Pdf", info: "PdfInfo"):
    """Set metadata fields in a PDF using a PdfInfo object."""

    # 1. Document Info (Title, Author, etc.)
    if info.doc_info:
        _set_docinfo(pdf, info.doc_info)

    # 2. IDs
    if info.ids:
        if len(info.ids) > 0 and info.ids[0]:
            _set_id_info(pdf, 0, info.ids[0])
        if len(info.ids) > 1 and info.ids[1]:
            _set_id_info(pdf, 1, info.ids[1])

    # 3. Bookmarks
    if info.bookmarks:
        _set_bookmarks(pdf, info.bookmarks)

    # 4. Page Media (CropBox, Rotation, etc.)
    if info.page_media:
        _set_page_media(pdf, info.page_media)

    # 5. Page Labels
    if info.page_labels:
        _set_page_labels(pdf, info.page_labels)


def _set_docinfo(pdf, doc_info_list):
    """Set fields in a PDF's Info dictionary from a list of DocInfoEntry."""
    from pikepdf import Name

    # We iterate the list to preserve the user's input order
    for entry in doc_info_list:
        pdf.docinfo[Name("/" + entry.key)] = entry.value


def _set_page_media(pdf, page_media_list: list["PageMediaEntry"]):
    """Set page media in a PDF from a list of PageMediaEntry."""
    for entry in page_media_list:
        _set_page_media_entry(pdf, entry)


def _set_page_media_entry(pdf, entry: "PageMediaEntry"):
    # entry.number is 1-based index from input
    page_number = entry.page_number

    if len(pdf.pages) < page_number:
        logger.warning(
            "Nonexistent page %s requested for PageMedia metadata. Skipping.",
            page_number,
        )
        return

    page = pdf.pages[page_number - 1]

    if entry.rotation is not None:
        page.rotate(entry.rotation, relative=False)

    if entry.media_rect is not None:
        page.mediabox = entry.media_rect
    elif entry.dimensions is not None:
        # Dimensions is a tuple/list, usually [width, height]
        page.mediabox = [0, 0, *entry.dimensions]

    for rect_name, box_name in c.INFO_TO_PAGE_BOXES_MAP.items():
        if box_name != "MediaBox" and (box_list := getattr(entry, rect_name, None)) is not None:
            setattr(page, box_name.lower(), box_list)


def _set_bookmarks(pdf, bookmark_list: list["BookmarkEntry"], delete_existing_bookmarks=True):
    """Sets bookmarks in a PDF from a flat list of BookmarkEntry objects."""
    from pikepdf import OutlineItem

    with pdf.open_outline() as outline:
        if delete_existing_bookmarks:
            outline.root = []

        bookmark_oi_ancestors: list[OutlineItem] = []
        for bookmark in bookmark_list:
            bookmark_oi_ancestors = _add_bookmark(pdf, bookmark, outline, bookmark_oi_ancestors)


def _add_bookmark(
    pdf, bookmark: "BookmarkEntry", outline, bookmark_oi_ancestors: list["OutlineItem"]
) -> list["pikepdf.OutlineItem"]:
    """Add a bookmark object to the PDF document."""

    level = bookmark.level
    pagenumber = bookmark.page_number
    title = bookmark.title

    # Basic Validation
    if pagenumber > len(pdf.pages):
        logger.warning(
            "Nonexistent page %s requested for bookmark with title '%s'. Skipping.",
            pagenumber,
            title,
        )
        return bookmark_oi_ancestors

    from pikepdf import OutlineItem

    new_bookmark_oi = OutlineItem(title, destination=pagenumber - 1)

    # Logic to attach to correct parent based on level
    if level == 1:
        outline.root.append(new_bookmark_oi)
    elif level > 1:
        if level > len(bookmark_oi_ancestors) + 1:
            logger.warning(
                "Bookmark level %s requested (with title '%s'),"
                "\nbut we are only at level %s in the bookmark tree. Skipping.",
                level,
                title,
                len(bookmark_oi_ancestors),
            )
            return bookmark_oi_ancestors

        # Parent is at level-2 index (e.g. level 2 has parent at index 0)
        bookmark_parent = bookmark_oi_ancestors[level - 2]
        bookmark_parent.children.append(new_bookmark_oi)
    else:
        logger.warning(
            "Skipping invalid bookmark with level %s. Levels should be 1 or greater.",
            level,
        )
        return bookmark_oi_ancestors

    # Update ancestor stack
    return bookmark_oi_ancestors[: level - 1] + [new_bookmark_oi]


def _set_page_labels(pdf, label_list: list["PageLabelEntry"], delete_existing=True):
    """Set a PDF document's page label definitions."""
    from pikepdf import NumberTree

    if hasattr(pdf.Root, "PageLabels") and not delete_existing:
        page_labels = NumberTree(pdf.Root.PageLabels)
    else:
        page_labels = NumberTree.new(pdf)

    for entry in label_list:
        index, page_label = _make_page_label(pdf, entry)
        page_labels[index] = page_label

    pdf.Root.PageLabels = page_labels.obj


def _set_id_info(pdf, id_index, hex_string):
    assert id_index in (0, 1)
    if id_index == 1:
        logger.warning(CANNOT_SET_PDFID1)
    if pdf.trailer and hasattr(pdf.trailer, "ID"):
        try:
            pdf.trailer.ID[id_index] = bytes.fromhex(hex_string)
        except ValueError:
            logger.warning(
                "Could not set PDFID%s to '%s'; invalid hex string?",
                id_index,
                hex_string,
            )


def _make_page_label(pdf, entry: "PageLabelEntry"):
    """Return a page label object and its index."""
    import pikepdf

    # entry.index is user-facing 1-based index
    idx = entry.index - 1

    ret: dict[str, Any] = {}

    if entry.prefix is not None:
        ret["/P"] = entry.prefix

    if entry.style:
        style_name_string = c.PAGE_LABEL_STYLE_MAP.get(entry.style)
        if style_name_string:
            ret["/S"] = pikepdf.Name(style_name_string)

    if entry.start is not None and entry.start != 1:
        ret["/St"] = entry.start

    return idx, pdf.make_indirect(pikepdf.Dictionary(ret))
