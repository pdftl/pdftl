# tests/operations/test_booklet.py

import pikepdf
import pytest

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.booklet import (
    _build_booklet_permutation,
    _parse_booklet_config,
    booklet_pages,
)

# --- Helpers ---


def make_pdf(*sizes):
    pdf = pikepdf.new()
    for w, h in sizes:
        pdf.add_blank_page(page_size=(w, h))
    return pdf


def make_uniform_pdf(n, w=100, h=100):
    return make_pdf(*[(w, h)] * n)


# --- PART 1: Parser ---


@pytest.mark.parametrize(
    "specs, expected",
    [
        ([], {"sig": 0, "margin": 0.0, "gutter": 0.0, "rtl": False}),
        (["sig=4"], {"sig": 4}),
        (["signature=2"], {"sig": 2}),
        (["margin=10", "gutter=5"], {"margin": 10.0, "gutter": 5.0}),
        (["rtl=true"], {"rtl": True}),
        (["rtl=1"], {"rtl": True}),
        (["rtl=yes"], {"rtl": True}),
        (["rtl=false"], {"rtl": False}),
    ],
)
def test_parse_booklet_config(specs, expected):
    page_specs = []
    config = _parse_booklet_config(specs, page_specs)
    for key, val in expected.items():
        assert config[key] == val


def test_parse_booklet_config_canvas():
    page_specs = []
    config = _parse_booklet_config(["canvas=a4_l"], page_specs)
    assert config["canvas_size"] is not None


def test_parse_booklet_config_unknown_canvas():
    with pytest.raises(InvalidArgumentError, match="Unknown canvas"):
        _parse_booklet_config(["canvas=NONSENSE"], [])


def test_parse_booklet_config_page_specs_passthrough():
    page_specs = []
    _parse_booklet_config(["1-4", "sig=2"], page_specs)
    assert page_specs == ["1-4"]


# --- PART 2: Permutation Logic ---


def _make_pages(n):
    """Create a list of n distinct mock page objects."""
    pdf = pikepdf.new()
    for _ in range(n):
        pdf.add_blank_page(page_size=(100, 100))
    return list(pdf.pages)


def test_permutation_pads_to_multiple_of_4():
    pages = _make_pages(3)
    result = _build_booklet_permutation(pages, sig=0, rtl=False)
    # 3 pages -> padded to 4, then 4 pages yields 4 slots
    assert len(result) == 4


def test_permutation_4_pages_ltr_order():
    """For a 4-page booklet: sheet 1 front = [p4, p1], back = [p2, p3]."""
    pages = _make_pages(4)
    result = _build_booklet_permutation(pages, sig=0, rtl=False)
    assert result[0] is pages[3]  # Front Left
    assert result[1] is pages[0]  # Front Right
    assert result[2] is pages[1]  # Back Left
    assert result[3] is pages[2]  # Back Right


def test_permutation_4_pages_rtl_order():
    """RTL: sheet 1 front = [p1, p4], back = [p3, p2]."""
    pages = _make_pages(4)
    result = _build_booklet_permutation(pages, sig=0, rtl=True)
    assert result[0] is pages[0]  # Front Left
    assert result[1] is pages[3]  # Front Right
    assert result[2] is pages[2]  # Back Left
    assert result[3] is pages[1]  # Back Right


def test_permutation_sig_splits_into_chunks():
    """sig=1 (1 sheet = 4 pages) on 8 pages should give 2 independent signatures."""
    pages = _make_pages(8)
    result = _build_booklet_permutation(pages, sig=1, rtl=False)
    assert len(result) == 8
    # First signature: pages 1-4 reordered
    assert result[0] is pages[3]
    assert result[1] is pages[0]
    # Second signature: pages 5-8 reordered
    assert result[4] is pages[7]
    assert result[5] is pages[4]


def test_permutation_padding_inserts_none():
    """5 pages should be padded to 8 with None entries."""
    pages = _make_pages(5)
    result = _build_booklet_permutation(pages, sig=0, rtl=False)
    assert len(result) == 8
    assert None in result


# --- PART 3: Execution ---


def test_booklet_basic():
    """Basic 4-page booklet produces 2 output sheets."""
    pdf = make_uniform_pdf(4)
    result = booklet_pages(pdf, operation_args=[])
    assert result.success
    # 4 pages / 2 per sheet = 2 output pages
    assert len(result.pdf.pages) == 2


def test_booklet_pads_and_produces_correct_sheet_count():
    """6 pages padded to 8 produces 4 output sheets."""
    pdf = make_uniform_pdf(6)
    result = booklet_pages(pdf=pdf, operation_args=[])
    assert result.success
    assert len(result.pdf.pages) == 4


def test_booklet_explicit_canvas():
    pdf = make_uniform_pdf(4)
    result = booklet_pages(pdf=pdf, operation_args=["canvas=a4_l"])
    assert result.success
    page = result.pdf.pages[0]
    w = float(page.mediabox[2]) - float(page.mediabox[0])
    # A4 landscape width ~ 841.89
    assert w == pytest.approx(841.89, rel=1e-2)


def test_booklet_auto_canvas_is_double_width():
    """Auto canvas should be twice the source page width."""
    pdf = make_uniform_pdf(4, w=200, h=300)
    result = booklet_pages(pdf=pdf, operation_args=[])
    assert result.success
    page = result.pdf.pages[0]
    w = float(page.mediabox[2]) - float(page.mediabox[0])
    assert w == pytest.approx(400.0, rel=1e-2)


def test_booklet_rtl():
    """RTL booklet should not crash and should produce same sheet count."""
    pdf = make_uniform_pdf(4)
    result = booklet_pages(pdf=pdf, operation_args=["rtl=true"])
    assert result.success
    assert len(result.pdf.pages) == 2


def test_booklet_sig():
    """sig=1 on 8 pages: 2 signatures of 4 pages = 4 output sheets."""
    pdf = make_uniform_pdf(8)
    result = booklet_pages(pdf=pdf, operation_args=["sig=1"])
    assert result.success
    assert len(result.pdf.pages) == 4


# Add to test_booklet.py


def test_booklet_no_source_pages_after_resolution():
    """Cover line 101: valid inputs but spec resolves to no pages."""
    pdf = make_uniform_pdf(4)
    with pytest.raises(InvalidArgumentError, match="No source pages"):
        booklet_pages(
            pdf=pdf,
            operation_args=["1~1"],
        )


def test_booklet_invalid_integer_keys():
    """Cover invalid integer key handling."""
    pdf = make_uniform_pdf(4)
    with pytest.raises(InvalidArgumentError, match="sig.*oops.*integer"):
        booklet_pages(
            pdf=pdf,
            operation_args=["sig=oops"],
        )


def test_booklet_raises_when_page_dimensions_unavailable(tmp_path):
    """Tests InvalidArgumentError when page dimensions cannot be determined."""
    from unittest.mock import patch

    import pikepdf

    from pdftl.exceptions import InvalidArgumentError
    from pdftl.operations.booklet import booklet_pages

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))

    with patch("pdftl.operations.booklet.get_visible_page_dimensions", return_value=None):
        with pytest.raises(InvalidArgumentError, match="Could not determine page dimensions"):
            booklet_pages(pdf, [])
