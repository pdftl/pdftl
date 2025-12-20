# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/info/set_info.py

"""Set metadata in a PDF.

Public: set_metadata_in_pdf"""

import logging

logger = logging.getLogger(__name__)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import OutlineItem

from pdftl.core.constants import PAGE_LABEL_STYLE_MAP

CANNOT_SET_PDFID1 = (
    "Cannot set PdfID1. This is a limitation of pikepdf."
    " See also PDF 32000-1:2008 section 14.4."
)


def set_metadata_in_pdf(pdf, meta_dict):
    """Set metadata fields in a PDF"""
    if "Info" in meta_dict:
        _set_docinfo(pdf, meta_dict["Info"])
    if "PdfID0" in meta_dict:
        _set_id_info(pdf, 0, meta_dict["PdfID0"])
    if "PdfID1" in meta_dict:
        _set_id_info(pdf, 1, meta_dict["PdfID1"])
    if "BookmarkList" in meta_dict:
        _set_bookmarks(pdf, meta_dict["BookmarkList"])
    if "PageMediaList" in meta_dict:
        _set_page_media(pdf, meta_dict["PageMediaList"])
    if "PageLabelList" in meta_dict:
        _set_page_labels(pdf, meta_dict["PageLabelList"])


def _set_docinfo(pdf, info_dict):
    """Set fields in a PDF's Info dictionary"""
    from pikepdf import Name

    for key, value in info_dict.items():
        pdf.docinfo[Name("/" + key)] = value


def _set_page_media(pdf, page_media_list):
    """Set page media in a PDF to the given list."""
    for page_media in page_media_list:
        _set_page_media_entry(pdf, page_media)


def _set_page_media_entry(pdf, page_media):
    try:
        page_number = int(page_media["Number"])
    except KeyError:
        logger.warning(
            "Skipping PageMedia metadata with missing page number (PageMediaNumber)."
            " Metadata entry details:\n  %s",
            page_media,
        )
        return
    if len(pdf.pages) < page_number:
        logger.warning(
            "Nonexistent page %s requested for PageMedia metadata. Skipping.",
            page_number,
        )
        return
    page = pdf.pages[page_number - 1]

    if "Rotation" in page_media:
        page.rotate(page_media["Rotation"], relative=False)
    if "Rect" in page_media:
        page.mediabox = page_media["Rect"]
    if "CropRect" in page_media:
        page.cropbox = page_media["CropRect"]
    elif "Dimensions" in page_media:
        page.mediabox = [0, 0, *page_media["Dimensions"]]


def _set_bookmarks(pdf, bookmark_list, delete_existing_bookmarks=True):
    """Sets bookmarks in a PDF to the given list."""
    with pdf.open_outline() as outline:
        if delete_existing_bookmarks:
            outline.root = []
        bookmark_oi_ancestors = []
        for bookmark in bookmark_list:
            bookmark_oi_ancestors = _add_bookmark(pdf, bookmark, outline, bookmark_oi_ancestors)


def _add_bookmark(pdf, bookmark, outline, bookmark_oi_ancestors: list["OutlineItem"]):
    """Add a bookmark (given as a dict) to the PDF document.

    Returns the ancestors of this bookmark, including this bookmark
    itself (as an OutlineItem).

    """
    try:
        level, pagenumber, title = (
            bookmark["Level"],
            bookmark["PageNumber"],
            bookmark["Title"],
        )
    except KeyError:
        logger.warning(
            "Skipping incomplete bookmark, we need Level, PageNumber and Title."
            " Bookmark details:\n  %s",
            bookmark,
        )
        return bookmark_oi_ancestors

    assert isinstance(level, int) and isinstance(title, str) and isinstance(pagenumber, int)

    if pagenumber > len(pdf.pages):
        logger.warning(
            "Nonexistent page %s requested for bookmark with title '%s'. Skipping.",
            pagenumber,
            title,
        )
        return bookmark_oi_ancestors

    from pikepdf import OutlineItem

    new_bookmark_oi = OutlineItem(title, destination=pagenumber - 1)
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

        bookmark_parent = bookmark_oi_ancestors[level - 2]
        bookmark_parent.children.append(new_bookmark_oi)
    else:
        logger.warning(
            "Skipping invalid bookmark with level %s. Levels should be 1 or greater.",
            level,
        )
        return bookmark_oi_ancestors
    return bookmark_oi_ancestors[: level - 1] + [new_bookmark_oi]


def _set_page_labels(pdf, label_list, delete_existing=True):
    """Set a PDF document's page label definitions."""
    from pikepdf import NumberTree

    if hasattr(pdf.Root, "PageLabels") and not delete_existing:
        page_labels = NumberTree(pdf.Root.PageLabels)
    else:
        page_labels = NumberTree.new(pdf)

    for label_data in label_list:
        index, page_label = _make_page_label(pdf, label_data)
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


def _make_page_label(pdf, label_data):
    """Return a page label suitable for insertion into the document's
    page labels number tree, and its intended index in that number
    tree. Returns: index, page_label.

    """
    import pikepdf

    prefix = label_data.get("Prefix", None)
    style = label_data.get("NumStyle", None)
    start = label_data.get("Start", 1)
    index = label_data.get("NewIndex", 1) - 1
    ret = {}
    if prefix is not None:
        ret["/P"] = prefix
    if (style_name_string := PAGE_LABEL_STYLE_MAP.get(style, None)) is not None:
        ret["/S"] = pikepdf.Name(style_name_string)
    if start != 1:
        ret["/St"] = start

    return index, pdf.make_indirect(pikepdf.Dictionary(ret))
