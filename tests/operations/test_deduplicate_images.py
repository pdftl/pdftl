# tests/operations/test_deduplicate_images.py
"""Operation-level test for `deduplicate_images`: exercises the registered
operation function itself (argument parsing + wiring to
deduplicate_image_xobjects), not just the core helper directly.

NOTE: this container has no access to the real pdftl.core.* / keyval_parser
framework, so this runs against small local stand-ins created for this
session only (see src/pdftl/core/*.py, src/pdftl/utils/keyval_parser.py --
each file-headed as a stand-in). The `deduplicate_images` operation module
itself is real and unmodified for that reason; only its framework
dependencies are stubbed. Rerun this (or the real repo's existing
operation-test pattern, if one exists for other operations) against the
genuine framework once this lands in the real repo.
"""

from __future__ import annotations

import pytest
import pikepdf
from pikepdf import Dictionary, Name

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.deduplicate_images import (
    deduplicate_images,
    _parse_byte_size,
)


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


# --- _parse_byte_size -----------------------------------------------------


def test_parse_byte_size_plain_number():
    assert _parse_byte_size("1024") == 1024


def test_parse_byte_size_kb_suffix():
    assert _parse_byte_size("64KB") == 64 * 1024


def test_parse_byte_size_mb_suffix_case_insensitive():
    assert _parse_byte_size("2mb") == 2 * 1024**2


def test_parse_byte_size_gb_suffix():
    assert _parse_byte_size("1GB") == 1024**3


def test_parse_byte_size_fractional():
    assert _parse_byte_size("1.5KB") == int(1.5 * 1024)


def test_parse_byte_size_rejects_garbage():
    with pytest.raises(InvalidArgumentError):
        _parse_byte_size("not-a-size")


def test_parse_byte_size_rejects_unknown_suffix():
    with pytest.raises(InvalidArgumentError):
        _parse_byte_size("5TB")  # not in the supported suffix set


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
    with pytest.raises(InvalidArgumentError):
        from pdftl.operations.deduplicate_images import _parse_deduplicate_images_args

        _parse_deduplicate_images_args(["bogus=1"])


def test_operation_no_duplicates_is_a_success_noop(pdf):
    img1 = _make_image(pdf, b"one")
    img2 = _make_image(pdf, b"two-longer")
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=img1, Im2=img2))

    result = deduplicate_images(pdf, [])

    assert result.success
    assert page.Resources.XObject.Im1.objgen != page.Resources.XObject.Im2.objgen
