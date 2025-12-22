# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/info/output_info.py

"""Output PDF metadata in a text based format.

Public methods:

write_info

"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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
class PageLabelEntry:
    index: int  # 1-based
    start: int
    prefix: str | None = None
    style: str | None = None


@dataclass
class DocInfoEntry:
    key: str
    value: str


@dataclass
class BookmarkEntry:
    title: str
    level: int
    page: int
    children: list["BookmarkEntry"] = field(default_factory=list)


@dataclass
class PageMediaEntry:
    number: int
    rotation: int
    rect: list[float]
    dimensions: tuple[str, str]
    crop_rect: list[float] | None = None


@dataclass
class PdfInfo:
    """The complete structured representation of a PDF's metadata."""

    pages: int
    ids: list[str]
    doc_info: list[DocInfoEntry] = field(default_factory=list)
    bookmarks: list[BookmarkEntry] = field(default_factory=list)
    page_media: list[PageMediaEntry] = field(default_factory=list)
    page_labels: list[PageLabelEntry] = field(default_factory=list)
    # Extra fields for --extra-info
    file_path: str | None = None
    version: str | None = None
    encrypted: bool | None = None


def get_info(pdf, input_filename, extra_info=False) -> PdfInfo:
    info = PdfInfo(pages=len(pdf.pages), ids=pdf_id_metadata_as_strings(pdf))
    if extra_info:
        info.file_path = input_filename
        info.version = pdf.pdf_version
        info.encrypted = pdf.is_encrypted
    if pdf.docinfo:
        for key, value in pdf.docinfo.items():
            info.doc_info.append(DocInfoEntry(key=str(key)[1:], value=str(value)))
    for i, page in enumerate(pdf.pages):
        rotation = int(page.get("/Rotate", 0))
        mediabox = page.mediabox
        width_str = pdf_num_to_string(abs(float(mediabox[2] - mediabox[0])))
        height_str = pdf_num_to_string(abs(float(mediabox[3] - mediabox[1])))
        info.page_media.append(
            PageMediaEntry(
                number=i + 1, rotation=rotation, rect=mediabox, dimensions=(width_str, height_str)
            )
        )

    if hasattr(pdf.Root, "PageLabels"):
        from pikepdf import NumberTree

        labels = NumberTree(pdf.Root.PageLabels)
        for page_idx, entry in labels.items():
            style_code = getattr(entry, "S", None)
            try:
                found_style = next(
                    k for k, v in PAGE_LABEL_STYLE_MAP.items() if v == str(style_code)
                )
            except StopIteration:
                found_style = "NoNumber"

            info.page_labels.append(
                PageLabelEntry(
                    index=int(page_idx) + 1,
                    start=int(getattr(entry, "St", 1)),
                    prefix=str(getattr(entry, "P", "")) or None,
                    style=found_style,
                )
            )

    from pikepdf.exceptions import OutlineStructureError

    try:
        with pdf.open_outline() as outline:
            if outline.root:
                named_destinations = get_named_destinations(pdf)
                pages_list = list(pdf.pages)
                info.bookmarks = _extract_bookmarks_recursive(
                    list(outline.root), pages_list, named_destinations
                )
    except OutlineStructureError as exc:
        logger.warning(
            "Warning: Could not read bookmarks. Outline may be corrupted. Error: %s",
            exc,
        )
    return info


def write_info(writer, info: PdfInfo, extra_info=False, escape_xml=True):
    """Write metadata info in style of pdftk dump_data"""
    if extra_info:
        _write_extra_info(writer, info)

    _write_docinfo(writer, info, escape_xml)
    _write_id_info(writer, info)
    _write_pages_info(writer, info)
    _write_bookmarks(writer, info.bookmarks, escape_xml)
    _write_page_media_info(writer, info)
    _write_page_labels(writer, info)


def _write_pages_info(writer, info):
    """Write the number of pages"""
    writer(f"NumberOfPages: {info.pages}")


def _write_page_media_info(writer, info):
    """Writes the media box and rotation information for each page."""
    for entry in info.page_media:
        writer(
            "PageMediaBegin\n"
            f"PageMediaNumber: {entry.number}\n"
            f"PageMediaRotation: {entry.rotation}\n"
            f"PageMediaRect: {pdf_rect_to_string(entry.rect)}\n"
            f"PageMediaDimensions: {entry.dimensions[0]} {entry.dimensions[1]}"
        )

        if entry.crop_rect is not None:
            writer(f"PageMediaCropRect: {pdf_rect_to_string(entry.crop_rect)}")


def _write_page_labels(writer, info):
    """Writes the document's page label definitions."""
    for entry in info.page_labels:
        writer(
            f"PageLabelBegin\n"
            f"PageLabelNewIndex: {entry.index}\n"
            f"PageLabelStart: {entry.start}"
        )
        if entry.prefix:
            writer(f"PageLabelPrefix: {entry.prefix}")
        writer(f"PageLabelNumStyle: {entry.style}")


def _write_id_info(writer, info):
    for i, id_str in enumerate(info.ids):
        writer(f"PdfID{i}: {id_str}")


def _write_extra_info(writer, info):
    writer(f"File: {info.file_path}")
    writer(f"PDF version: {info.version}")
    writer(f"Encrypted: {info.encrypted}")


def _write_docinfo(writer, info, escape_xml):
    """Writes the document's Info dictionary (DocInfo) to the output."""
    for entry in info.doc_info:
        key, value = entry.key, entry.value
        value_str = xml_encode_for_info(value) if escape_xml else value
        writer(f"InfoBegin\nInfoKey: {key}\nInfoValue: {value_str}")


def _write_bookmarks(writer, bookmarks: list[BookmarkEntry], escape_xml=True):
    """Recursively write the bookmarks from the dataclass list."""
    for bm in bookmarks:
        title = xml_encode_for_info(bm.title) if escape_xml else bm.title

        writer("BookmarkBegin")
        writer(f"BookmarkTitle: {title}")
        writer(f"BookmarkLevel: {bm.level}")
        writer(f"BookmarkPageNumber: {bm.page}")

        if bm.children:
            _write_bookmarks(writer, bm.children, escape_xml)


def _extract_bookmarks_recursive(
    items, pages_list, named_destinations, level=1
) -> list[BookmarkEntry]:
    """Gather bookmarks into a list of dataclasses using original error handling."""
    from pikepdf.exceptions import OutlineStructureError

    results = []
    for item in items:
        page_num = 0
        try:
            page_num = resolve_page_number(item, pages_list, named_destinations)
        except AssertionError as exc:
            logger.warning(
                "Could not resolve page number for bookmark '%s': %s.\n  Using page number 0.",
                item.title,
                exc,
            )
            page_num = 0

        entry = BookmarkEntry(title=str(item.title), level=level, page=page_num)

        if item.children:
            entry.children = _extract_bookmarks_recursive(
                item.children, pages_list, named_destinations, level + 1
            )

        results.append(entry)
    return results
