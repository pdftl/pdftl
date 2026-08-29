# tests/operations/test_deduplicate_images.py
"""Operation-level test for `deduplicate_images`: exercises the registered
operation function itself (argument parsing + wiring to
deduplicate_image_xobjects), not just the core helper directly.
"""

from __future__ import annotations

import pytest
import pikepdf
from pikepdf import Dictionary, Name

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.deduplicate_images import deduplicate_images


@pytest.fixture
def pdf():
    p = pikepdf.Pdf.new()
    yield p
    p.close()


def _make_image(pdf, data: bytes, **extra):
    return pdf.make_indirect(
        pikepdf.Stream(
            pdf,
            data,
            Type=Name("/XObject"),
            Subtype=Name("/Image"),
            Width=10,
            Height=10,
            BitsPerComponent=8,
            ColorSpace=Name("/DeviceGray"),
            **extra,
        )
    )


# --- deduplicate_images operation, end to end through this wrapper --------


def test_operation_merges_duplicates_with_no_args(pdf):
    data = b"duplicate payload " * 10
    img1 = _make_image(pdf, data)
    img2 = _make_image(pdf, data)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=img1, Im2=img2))

    result = deduplicate_images(pdf, [])

    assert result.success
    assert result.pdf is pdf
    assert page.Resources.XObject.Im1.objgen == page.Resources.XObject.Im2.objgen


def test_operation_respects_min_bytes_option(pdf):
    small_dup = b"x" * 10
    img1 = _make_image(pdf, small_dup)
    img2 = _make_image(pdf, small_dup)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=img1, Im2=img2))

    result = deduplicate_images(pdf, ["min_bytes=1000"])

    assert result.success
    # too small to qualify -- left distinct
    assert page.Resources.XObject.Im1.objgen != page.Resources.XObject.Im2.objgen


def test_operation_min_bytes_with_suffix_allows_merge(pdf):
    data = b"y" * 2000
    img1 = _make_image(pdf, data)
    img2 = _make_image(pdf, data)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=img1, Im2=img2))

    result = deduplicate_images(pdf, ["min_bytes=1KB"])

    assert result.success
    assert page.Resources.XObject.Im1.objgen == page.Resources.XObject.Im2.objgen


def test_operation_no_args_defaults_to_no_minimum(pdf):
    tiny = b"z"
    img1 = _make_image(pdf, tiny)
    img2 = _make_image(pdf, tiny)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=img1, Im2=img2))

    result = deduplicate_images(pdf, None)

    assert result.success
    assert page.Resources.XObject.Im1.objgen == page.Resources.XObject.Im2.objgen


def test_operation_unknown_option_raises():
    from pdftl.operations.deduplicate_images import _parse_deduplicate_images_args

    with pytest.raises(InvalidArgumentError):
        _parse_deduplicate_images_args(["bogus=1"])


def test_operation_no_duplicates_is_a_success_noop(pdf):
    img1 = _make_image(pdf, b"one")
    img2 = _make_image(pdf, b"two-longer")
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=img1, Im2=img2))

    result = deduplicate_images(pdf, [])

    assert result.success
    assert page.Resources.XObject.Im1.objgen != page.Resources.XObject.Im2.objgen
