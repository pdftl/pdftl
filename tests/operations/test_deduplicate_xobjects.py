# tests/operations/test_deduplicate_xobjects.py

from __future__ import annotations

import logging

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.deduplicate_xobjects import deduplicate_xobjects


def _pdf_with_forms(data=b"0 0 1 rg 0 0 50 50 re f" * 20, copies=2):
    pdf = pikepdf.new()
    for _ in range(copies):
        form = pdf.make_indirect(
            pikepdf.Stream(
                pdf,
                data,
                Type=Name.XObject,
                Subtype=Name.Form,
                BBox=Array([0, 0, 100, 100]),
                Resources=Dictionary(),
            )
        )
        page = pdf.add_blank_page(page_size=(100, 100))
        page.Resources = Dictionary(XObject=Dictionary(Fm0=form))
        page.Contents = pdf.make_stream(b"/Fm0 Do")
    return pdf


def _distinct(pdf):
    return len({page.Resources.XObject.Fm0.objgen for page in pdf.pages})


def test_merges_with_no_args(caplog):
    pdf = _pdf_with_forms()
    with caplog.at_level(logging.INFO):
        result = deduplicate_xobjects(pdf, [])
    assert result.success and result.pdf is pdf
    assert _distinct(pdf) == 1
    expected = 20 * len(b"0 0 1 rg 0 0 50 50 re f")  # 20 * 23
    assert f"merged 1 duplicate form XObject(s), saving approximately {expected} bytes" in (
        caplog.text
    )


def test_min_bytes_skips_small_forms(caplog):
    pdf = _pdf_with_forms()
    with caplog.at_level(logging.INFO):
        deduplicate_xobjects(pdf, ["min_bytes=1KB"])
    assert _distinct(pdf) == 2
    assert "no duplicate drawn form XObjects found" in caplog.text


def test_none_args_is_accepted():
    pdf = _pdf_with_forms()
    deduplicate_xobjects(pdf, None)
    assert _distinct(pdf) == 1


@pytest.mark.parametrize("args", [["bogus=1"], ["min_bytes=lots"]])
def test_bad_args_rejected(args):
    with pytest.raises(InvalidArgumentError):
        deduplicate_xobjects(_pdf_with_forms(), args)
