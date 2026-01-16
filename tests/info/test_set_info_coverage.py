import logging

import pytest

# Import the PROPER datatypes
from pdftl.info.info_types import PageLabelEntry, PageMediaEntry
from pdftl.info.set_info import _make_page_label, _set_page_media_entry


def test_info_page_media_errors(minimal_pdf, caplog):
    """
    Covers set_info.py logic: Handle exceptions when setting Rotation, MediaBox, and other boxes.
    """
    caplog.set_level(logging.WARNING)
    if len(minimal_pdf.pages) == 0:
        minimal_pdf.add_blank_page()

    # 1. Rotation Error
    # Use proper datatype
    bad_rot = PageMediaEntry(page_number=1, rotation=45)
    _set_page_media_entry(minimal_pdf, bad_rot)
    assert "Failed to set Rotation" in caplog.text
    caplog.clear()

    # 2. MediaBox Error
    # [0, 0] is invalid (needs 4 coordinates)
    # Note: info_types maps 'dimensions' or 'media_rect'.
    bad_media = PageMediaEntry(page_number=1, media_rect=[0, 0, 100])
    _set_page_media_entry(minimal_pdf, bad_media)
    assert "Failed to set MediaBox" in caplog.text
    caplog.clear()

    # 3. Other Box (CropBox) Error
    # Fix: Attribute in PageMediaEntry is 'crop_rect', not 'crop_box'
    # We intentionally pass invalid types to trigger the catch block in the code under test
    bad_crop = PageMediaEntry(page_number=1, crop_rect=["Not", "A", "Rect"])
    _set_page_media_entry(minimal_pdf, bad_crop)

    # The error message in the logs will likely refer to "CropBox" because that is the PDF key
    assert "Failed to set CropBox" in caplog.text


def test_info_page_label_validation(minimal_pdf, caplog):
    """
    Covers set_info.py logic: Validate inputs for Page Labels.
    """
    caplog.set_level(logging.WARNING)

    # 1. Invalid Index
    # Use proper datatype
    bad_idx = PageLabelEntry(new_index=0)
    idx, _ = _make_page_label(minimal_pdf, bad_idx)
    assert idx is None
    assert "Skipping PageLabel with invalid PageLabelNewIndex" in caplog.text
    caplog.clear()

    # 2. Invalid Style
    # Use proper datatype
    bad_style = PageLabelEntry(new_index=1, num_style="InvalidStyleName")
    idx, _ = _make_page_label(minimal_pdf, bad_style)
    assert idx is None
    assert "Skipping PageLabel with invalid PageLabelNumStyle" in caplog.text
    caplog.clear()

    # 3. Invalid Start Number
    # Fix: 'decimal' was causing a style error before the start error could trigger.
    # We must use a VALID style (e.g. 'DecimalArabicNumerals') to test the INVALID start.
    bad_start = PageLabelEntry(
        new_index=1, start=0, num_style="DecimalArabicNumerals"  # VALID style
    )
    _make_page_label(minimal_pdf, bad_start)

    # Now this assertion should pass because the style check passed
    assert "Skipping PageLabel with invalid PageLabelStart" in caplog.text
