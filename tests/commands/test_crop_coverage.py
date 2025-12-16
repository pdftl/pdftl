import logging
from unittest.mock import patch

import pikepdf
import pytest

from pdftl.commands.crop import crop_pages


def _read_page_content(page):
    """Helper to read page content whether it is a Stream or Array."""
    contents = page.Contents
    ret = []
    if isinstance(contents, pikepdf.Array):
        ret.append(b"".join(stream.read_bytes() for stream in contents))
    else:
        ret.append(contents.read_bytes())
    ret.extend(_read_xobject_content(page))
    return ret


def _read_xobject_content(container, visited=None):
    if visited is None:
        visited = set()

    resources = getattr(container, "Resources", None)
    if not isinstance(resources, pikepdf.Dictionary):
        return []  # Return empty list, not None

    xobjects = getattr(resources, "XObject", None)
    if not xobjects:  # Handle case where Resources exists but XObject doesn't
        return []

    streams = []
    for _, xobject_ref in xobjects.items():
        oid = xobject_ref.objgen
        if oid in visited:
            continue
        visited.add(xobject_ref.objgen)
        streams.append(xobject_ref.read_bytes())

        new_res = getattr(xobject_ref, "Resources", None)
        if new_res:
            streams.extend(_read_xobject_content(new_res, visited))

    return streams


@pytest.fixture
def pdf():
    p = pikepdf.new()
    p.add_blank_page(page_size=(100, 100))  # 100x100 box
    # Ensure content stream exists for preview test
    p.pages[0].Contents = p.make_stream(b"")
    return p


def test_crop_preview(pdf):
    """Test preview mode (Lines 141-147)."""
    specs = ["preview", "1-end(10)"]
    crop_pages(pdf, specs)

    # Use helper to handle array conversion
    content = _read_page_content(pdf.pages[0])
    assert any(b"re s" in x for x in content)


def test_crop_paper_size(pdf):
    """Test cropping to a paper size (Lines 122-127, 156)."""
    pdf.pages[0].mediabox = [0, 0, 1000, 1000]

    specs = ["1-end(a4)"]
    crop_pages(pdf, specs)

    mbox = pdf.pages[0].mediabox
    width = float(mbox[2]) - float(mbox[0])
    assert 590 < width < 600


def test_crop_invalid_dimensions(pdf):
    """Test cropping that results in negative size (Lines 96-101, 134)."""
    # 100 width - 60 left - 60 right = -20 width
    specs = ["1-end(60,0,60,0)"]

    crop_pages(pdf, specs)

    # MediaBox should remain unchanged
    mbox = pdf.pages[0].mediabox
    assert float(mbox[2]) == 100


def test_crop_missing_mediabox(pdf, caplog):
    """Test page with no MediaBox (Lines 90-91)."""
    # We mock _get_page_dimensions to return None, simulating a page
    # where MediaBox is missing or invalid.
    caplog.set_level(logging.DEBUG)

    with patch("pdftl.commands.crop._get_page_dimensions", return_value=None):
        crop_pages(pdf, ["1-end(10)"])

    assert "no valid MediaBox" in caplog.text
