# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/info/output_info.py

"""Output PDF metadata in a text based format.

Public methods:

write_info

"""

import logging
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

import pdftl.core.constants as c
from pdftl.info.info_types import (
    BookmarkEntry,
    DocInfoEntry,
    PageLabelEntry,
    PageMediaEntry,
    PdfInfo,
)
from pdftl.info.read_info import (
    pdf_id_metadata_as_strings,
    resolve_page_number,
)
from pdftl.utils.destinations import get_named_destinations
from pdftl.utils.string_utils import (
    pdf_num_to_string,
    pdf_rect_to_string,
    xml_encode_for_info,
)


def _get_extra_info(info, pdf, input_filename):
    info.file_path = input_filename
    info.version = pdf.pdf_version
    info.encrypted = pdf.is_encrypted


def _get_docinfo(info, pdf_docinfo):
    import pikepdf

    if info.doc_info is None:
        info.doc_info = []
        docinfo_keys_sorted = list(pdf_docinfo.keys())
        docinfo_keys_sorted.sort()
        for key in docinfo_keys_sorted:
            key_str = str(key)[1:]
            value = pdf_docinfo[key]
            if not isinstance(value, (str, pikepdf.String)):
                logger.warning(
                    "Skipping non-string InfoValue '%s' (type: %s) for InfoKey '%s'",
                    value,
                    type(value),
                    key_str,
                )
                continue
            if value:
                info.doc_info.append(DocInfoEntry(key=key_str, value=str(value)))


def _get_page_info(info, i, page):
    import pikepdf

    rotation = int(page.get("/Rotate", 0))

    if info.page_media is None:
        info.page_media = []
    page_media_dict: dict[str, Any] = {
        "page_number": i + 1,
        "rotation": rotation,
    }
    saved_boxes = {"media": None, "crop": None}
    for box, key in c.INFO_TO_PAGE_BOXES_MAP.items():
        box_obj = getattr(page, key, None)
        if not isinstance(box_obj, (pikepdf.Array, list)):
            continue
        _update_box_info_dict(page_media_dict, box, box_obj, saved_boxes)
    info.page_media.append(PageMediaEntry(**page_media_dict))


def _update_box_info_dict(page_media_dict, box, box_obj, saved_boxes):
    from collections.abc import Iterable

    box_list = [float(x) for x in cast(Iterable[Any], box_obj)]
    width_str = pdf_num_to_string(abs(box_list[2] - box_list[0]))
    height_str = pdf_num_to_string(abs(box_list[3] - box_list[1]))
    if box == "media_rect":
        page_media_dict["dimensions"] = (width_str, height_str)
        saved_boxes["media"] = box_list
    elif box == "crop_rect":
        if box_list == saved_boxes["media"]:
            return
        saved_boxes["crop"] = box_list
    else:
        if box_list == saved_boxes["crop"] or (
            saved_boxes["crop"] is None and box_list == saved_boxes["media"]
        ):
            return
    page_media_dict[box] = box_list


def _get_pagelabels(info, labels):
    for page_idx, entry in labels.items():
        style_code = getattr(entry, "S", None)
        try:
            found_style = next(
                k for k, v in c.PAGE_LABEL_STYLE_MAP.items() if v == str(style_code)
            )
        except StopIteration:
            found_style = "NoNumber"
        if info.page_labels is None:
            info.page_labels = []
        info.page_labels.append(
            PageLabelEntry(
                new_index=int(page_idx) + 1,
                start=int(getattr(entry, "St", 1)),
                prefix=str(getattr(entry, "P", "")) or None,
                num_style=found_style,
            )
        )


def _get_bookmarks(info, pdf, page_map):
    from pikepdf.exceptions import OutlineStructureError

    try:
        with pdf.open_outline() as outline:
            if outline.root:
                raw_dests = get_named_destinations(pdf)
                named_dest_dict = {str(k): v for k, v in raw_dests.items()} if raw_dests else {}
                info.bookmarks = _extract_bookmarks_recursive(
                    list(outline.root), page_map, named_dest_dict
                )
    except OutlineStructureError as exc:
        logger.warning(
            "Warning: Could not read bookmarks. Outline may be corrupted. Error: %s",
            exc,
        )


def get_info(pdf, input_filename, extra_info=False) -> PdfInfo:
    page_map = {page.objgen: i + 1 for i, page in enumerate(pdf.pages)}

    info = PdfInfo(pages=len(pdf.pages), ids=pdf_id_metadata_as_strings(pdf))
    if extra_info:
        _get_extra_info(info, pdf, input_filename)
    if pdf.docinfo:
        _get_docinfo(info, pdf.docinfo)
    for i, page in enumerate(pdf.pages):
        _get_page_info(info, i, page)

    if hasattr(pdf.Root, "PageLabels"):
        from pikepdf import NumberTree

        labels = NumberTree(pdf.Root.PageLabels)
        _get_pagelabels(info, labels)

    _get_bookmarks(info, pdf, page_map)

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
    for entry in info.page_media or {}:
        rot = entry.rotation or 0
        writer(
            "PageMediaBegin\n"
            f"PageMediaNumber: {entry.page_number}\n"
            f"PageMediaRotation: {rot}"
        )
        # breakpoint()
        if entry.media_rect is not None:
            writer(f"PageMediaRect: {pdf_rect_to_string(entry.media_rect)}")
        if entry.dimensions is not None:
            writer(f"PageMediaDimensions: {entry.dimensions[0]} {entry.dimensions[1]}")
        if entry.crop_rect is not None:
            writer(f"PageMediaCropRect: {pdf_rect_to_string(entry.crop_rect)}")
        if entry.trim_rect is not None:
            writer(f"PageMediaTrimRect: {pdf_rect_to_string(entry.trim_rect)}")
        if entry.bleed_rect is not None:
            writer(f"PageMediaBleedRect: {pdf_rect_to_string(entry.bleed_rect)}")


def _write_page_labels(writer, info):
    """Writes the document's page label definitions."""
    for entry in info.page_labels or {}:
        writer(
            f"PageLabelBegin\n"
            f"PageLabelNewIndex: {entry.new_index}\n"
            f"PageLabelStart: {entry.start}"
        )
        if entry.prefix:
            writer(f"PageLabelPrefix: {entry.prefix}")
        writer(f"PageLabelNumStyle: {entry.num_style}")


def _write_id_info(writer, info):
    for i, id_str in enumerate(info.ids or []):
        writer(f"PdfID{i}: {id_str}")


def _write_extra_info(writer, info):
    writer(f"File: {info.file_path}")
    writer(f"PDF version: {info.version}")
    writer(f"Encrypted: {info.encrypted}")


def _write_docinfo(writer, info, escape_xml):
    """Writes the document's Info dictionary (DocInfo) to the output."""

    for entry in info.doc_info or {}:
        key, value = entry.key, entry.value
        value_str = xml_encode_for_info(value) if escape_xml else value
        writer(f"InfoBegin\nInfoKey: {key}\nInfoValue: {value_str}")


def _write_bookmarks(writer, bookmarks: list[BookmarkEntry] | None, escape_xml=True):
    """Recursively write the bookmarks from the dataclass list."""
    for bm in bookmarks or {}:
        title = xml_encode_for_info(bm.title) if escape_xml else bm.title

        writer("BookmarkBegin")
        writer(f"BookmarkTitle: {title}")
        writer(f"BookmarkLevel: {bm.level}")
        writer(f"BookmarkPageNumber: {bm.page_number}")

        if bm.children:
            _write_bookmarks(writer, bm.children, escape_xml)


def _extract_bookmarks_recursive(
    items, page_map, named_destinations, level=1
) -> list[BookmarkEntry]:
    """Gather bookmarks into a list of dataclasses using original error handling."""

    results = []
    for item in items:
        page_num = 0
        try:
            page_num = resolve_page_number(item, page_map, named_destinations)
        except AssertionError as exc:
            logger.warning(
                "Could not resolve page number for bookmark '%s': %s.\n  Using page number 0.",
                item.title,
                exc,
            )
            page_num = 0
        if page_num is None:
            logger.warning(
                "Could not resolve page number for bookmark '%s'. Using page number 0.", item.title
            )
            page_num = 0
        entry = BookmarkEntry(title=str(item.title), level=level, page_number=page_num)

        if item.children:
            entry.children = _extract_bookmarks_recursive(
                item.children, page_map, named_destinations, level + 1
            )

        results.append(entry)
    return results
