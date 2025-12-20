# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/info/output_info.py

"""Output PDF metadata in a text based format.

Public methods:

write_info

"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from pikepdf import NameTree

logger = logging.getLogger(__name__)

from pdftl.core.constants import PAGE_LABEL_STYLE_MAP
from pdftl.info.read_info import (
    get_named_destinations,
    pdf_id_metadata_as_strings,
    resolve_page_number,
)
from pdftl.utils.string import (
    pdf_num_to_string,
    pdf_rect_to_string,
    xml_encode_for_info,
)


@dataclass
class BookmarkWriterContext:
    """State dataclass for _write_bookmarks_recursive"""

    outline_items: list
    pages: list
    named_destinations: Union["NameTree", None]


def write_info(writer, pdf, input_filename, escape_xml=True, extra_info=False):
    """Write metadata info in style of pdftk dump_data"""
    if extra_info:
        _write_extra_info(writer, pdf, input_filename)

    _write_docinfo(writer, pdf, escape_xml)
    _write_id_info(writer, pdf)
    _write_pages_info(writer, pdf)
    _write_bookmarks(writer, pdf, escape_xml)
    _write_page_media_info(writer, pdf)
    _write_page_labels(writer, pdf)


def _write_pages_info(writer, pdf):
    """Write the number of pages"""
    writer(f"NumberOfPages: {len(pdf.pages)}")


def _write_bookmarks_recursive(writer, context: BookmarkWriterContext, level=1, escape_xml=True):
    """
    Recursively writes PDF bookmarks to a writer function.

    This function orchestrates the process, delegating complex page resolution
    to helper functions.
    """
    for item in context.outline_items:
        page_num = None
        try:
            page_num = resolve_page_number(item, context.pages, context.named_destinations)
        except AssertionError as exc:
            logger.warning(
                "Could not resolve page number for bookmark '%s': %s.\n  Using page number 0.",
                item.title,
                exc,
            )
            page_num = 0

        title_string = str(item.title)
        title = xml_encode_for_info(title_string) if escape_xml else title_string
        for output in [
            "BookmarkBegin",
            f"BookmarkTitle: {title}",
            f"BookmarkLevel: {level}",
            f"BookmarkPageNumber: {page_num}",
        ]:
            writer(output)

        if item.children:
            child_context = context
            child_context.outline_items = item.children
            _write_bookmarks_recursive(
                writer,
                context,
                level=level + 1,
                escape_xml=escape_xml,
            )


def _write_page_media_info(writer, pdf):
    """Writes the media box and rotation information for each page."""
    for i, page in enumerate(pdf.pages):
        rotation = int(page.get("/Rotate", 0))
        mediabox = page.mediabox
        width = pdf_num_to_string(abs(float(mediabox[2] - mediabox[0])))
        height = pdf_num_to_string(abs(float(mediabox[3] - mediabox[1])))
        page_label = str(i + 1)

        writer(
            "PageMediaBegin\n"
            f"PageMediaNumber: {page_label}\n"
            f"PageMediaRotation: {rotation}\n"
            f"PageMediaRect: {pdf_rect_to_string(mediabox)}\n"
            f"PageMediaDimensions: {width} {height}"
        )

        if page.cropbox != mediabox:
            writer(f"PageMediaCropRect: {pdf_rect_to_string(page.cropbox)}")


def _write_page_labels(writer, pdf):
    """Writes the document's page label definitions."""
    from pikepdf import NumberTree, String

    if not hasattr(pdf.Root, "PageLabels"):
        return

    labels = NumberTree(pdf.Root.PageLabels)
    assert isinstance(labels, NumberTree)

    for page_idx, entry in labels.items():
        start = getattr(entry, "St", 1)
        prefix = getattr(entry, "P", None)
        style_code = getattr(entry, "S", None)

        writer(
            f"PageLabelBegin\nPageLabelNewIndex: {int(page_idx) + 1}\nPageLabelStart: {int(start)}"
        )

        if isinstance(prefix, (String, str)):
            writer(f"PageLabelPrefix: {str(prefix)}")

        try:
            found_style = next(
                it[0] for it in PAGE_LABEL_STYLE_MAP.items() if it[1] == str(style_code)
            )
            writer(f"PageLabelNumStyle: {found_style}")
        except StopIteration:
            writer("PageLabelNumStyle: NoNumber")


def _write_id_info(writer, pdf):
    for i, id_str in enumerate(pdf_id_metadata_as_strings(pdf)):
        writer(f"PdfID{i}: {id_str}")


def _write_extra_info(writer, pdf, input_filename):
    writer(f"File: {input_filename}")
    writer(f"PDF version: {pdf.pdf_version}")
    writer(f"Encrypted: {pdf.is_encrypted}")


def _write_docinfo(writer, pdf, escape_xml):
    """Writes the document's Info dictionary (DocInfo) to the output."""
    if not pdf.docinfo:
        return

    from pikepdf import String

    def output_item(key, value):
        if not isinstance(value, (String, str)) or not str(value):
            return
        value_str = xml_encode_for_info(str(value)) if escape_xml else str(value)
        writer(f"InfoBegin\nInfoKey: {str(key)[1:]}\nInfoValue: {value_str}")

    for key, value in pdf.docinfo.items():
        output_item(key, value)


def _write_bookmarks(writer, pdf, escape_xml=True):
    """Writes the document's bookmarks (outline) to the output."""
    from pikepdf.exceptions import OutlineStructureError

    try:
        with pdf.open_outline() as outline:
            if outline.root:
                named_destinations = get_named_destinations(pdf)
                pages_list = list(pdf.pages)
                context = BookmarkWriterContext(
                    outline_items=list(outline.root),
                    pages=pages_list,
                    named_destinations=named_destinations,
                )
                _write_bookmarks_recursive(
                    writer,
                    context,
                    escape_xml=escape_xml,
                )
    except OutlineStructureError as exc:
        logger.warning(
            "Warning: Could not read bookmarks. Outline may be corrupted. Error: %s",
            exc,
        )
