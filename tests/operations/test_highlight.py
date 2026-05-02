import re
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.highlight import (
    _apply_highlight_spec,
    _build_highlight_annotation,
    _find_options_part,
    _generate_annotations_for_text,
    _parse_color,
    _parse_highlight_spec,
    _parse_options,
    _process_highlight_page,
    highlight_pdf,
)


@pytest.fixture
def mock_pdf():
    pdf = MagicMock()
    pdf.pages = [MagicMock(), MagicMock()]
    return pdf


@pytest.fixture
def mock_pdfium_doc():
    doc = MagicMock()
    return doc


# --- Tests for _find_options_part ---


def test_find_options_part_no_options():
    opts, rest = _find_options_part("/test/")
    assert opts == ""
    assert rest == "/test/"


def test_find_options_part_with_options():
    opts, rest = _find_options_part("/test/(author=Jane)")
    assert opts == "(author=Jane)"
    assert rest == "/test/"


def test_find_options_part_nested_parens():
    opts, rest = _find_options_part("/test/(color=(1 0 0))")
    assert opts == "(color=(1 0 0))"
    assert rest == "/test/"


def test_find_options_part_unbalanced():
    # If trailing paren isn't matched appropriately or missing
    opts, rest = _find_options_part("/test/(color=1 0 0")
    assert opts == ""
    assert rest == "/test/(color=1 0 0"


# --- Tests for _parse_color ---


def test_parse_color_valid():
    assert _parse_color("1 0 0") == [1.0, 0.0, 0.0]
    assert _parse_color("0.5") == [0.5]
    assert _parse_color("0.1 0.2 0.3 0.4") == [0.1, 0.2, 0.3, 0.4]


def test_parse_color_invalid_chars():
    with pytest.raises(InvalidArgumentError, match="Invalid characters in color string"):
        _parse_color("1 red 0")


def test_parse_color_invalid_length():
    with pytest.raises(InvalidArgumentError, match="must have 1, 3, or 4"):
        _parse_color("1 0")


# --- Tests for _parse_options ---


def test_parse_options_empty():
    assert _parse_options("") == {}


@patch("pdftl.operations.highlight.parse_keyval_string")
def test_parse_options_valid(mock_parse_keyval):
    mock_parse_keyval.return_value = {
        "author": "'Jane Doe'",
        "contents": '"Fix this"',
        "color": "0 1 0",
        "print": "no",
        "opacity": "0.5",
    }

    opts = _parse_options(
        "(author='Jane Doe', color=0 1 0, print=no, opacity=0.5, contents=\"Fix this\")"
    )

    assert opts["author"] == "Jane Doe"
    assert opts["contents"] == "Fix this"
    assert opts["color"] == [0.0, 1.0, 0.0]
    assert opts["print"] is False
    assert opts["opacity"] == 0.5


@patch("pdftl.operations.highlight.parse_keyval_string")
def test_parse_options_invalid_opacity(mock_parse_keyval):
    mock_parse_keyval.return_value = {"opacity": "half"}
    with pytest.raises(InvalidArgumentError, match="Invalid opacity value"):
        _parse_options("(opacity=half)")


# --- Tests for _parse_highlight_spec ---


def test_parse_highlight_spec_empty():
    with pytest.raises(InvalidArgumentError, match="Empty highlight specification"):
        _parse_highlight_spec("")


def test_parse_highlight_spec_missing_regex():
    with pytest.raises(InvalidArgumentError, match="Missing regex"):
        _parse_highlight_spec("(author=Jane)")


@patch("pdftl.operations.highlight.split_escaped")
def test_parse_highlight_spec_wrong_parts(mock_split_escaped):
    mock_split_escaped.return_value = ["1-3", "test"]  # Only 2 parts
    with pytest.raises(InvalidArgumentError, match="does not look correct"):
        _parse_highlight_spec("1-3/test")


@patch("pdftl.operations.highlight._parse_options")
@patch("pdftl.operations.highlight.split_escaped")
def test_parse_highlight_spec_valid(mock_split_escaped, mock_parse_options):
    mock_split_escaped.return_value = ["1-3", "REGEX", ""]
    mock_parse_options.return_value = {"author": "Test"}

    page_spec, regex_str, options = _parse_highlight_spec("1-3/REGEX/(author=Test)")

    assert page_spec == "1-3"
    assert regex_str == "REGEX"
    assert options == {"author": "Test"}


@patch("pdftl.operations.highlight.split_escaped")
def test_parse_highlight_spec_default_page_spec(mock_split_escaped):
    mock_split_escaped.return_value = ["", "REGEX", ""]
    page_spec, _, _ = _parse_highlight_spec("/REGEX/")
    assert page_spec == "1-end"


# --- Tests for _apply_highlight_spec ---


@patch("pdftl.operations.highlight._parse_highlight_spec")
def test_apply_highlight_spec_invalid_regex(mock_parse):
    mock_parse.return_value = ("1-end", "[invalid regex", {})

    with pytest.raises(InvalidArgumentError, match="Invalid regular expression"):
        _apply_highlight_spec(MagicMock(), MagicMock(), "/[invalid regex/")


@patch("pdftl.operations.highlight._process_highlight_page")
@patch("pdftl.operations.highlight.page_numbers_matching_page_spec")
@patch("pdftl.operations.highlight._parse_highlight_spec")
def test_apply_highlight_spec_valid(
    mock_parse, mock_page_nums, mock_process, mock_pdf, mock_pdfium_doc
):
    mock_parse.return_value = ("1", "test", {"color": [1, 0, 0]})
    mock_page_nums.return_value = [1]

    _apply_highlight_spec(mock_pdf, mock_pdfium_doc, "1/test/")

    mock_process.assert_called_once()
    args = mock_process.call_args[0]
    assert args[0] == mock_pdf
    assert args[1] == mock_pdfium_doc
    assert args[2] == 0  # 0-indexed page
    assert isinstance(args[3], re.Pattern)
    assert args[4] == {"color": [1, 0, 0]}


# --- Tests for _process_highlight_page ---


@patch("pdftl.operations.highlight._generate_annotations_for_text")
def test_process_highlight_page_no_text(mock_generate, mock_pdf, mock_pdfium_doc):
    mock_page = MagicMock()
    mock_textpage = MagicMock()
    mock_textpage.get_text_range.return_value = ""
    mock_page.get_textpage.return_value = mock_textpage
    mock_pdfium_doc.get_page.return_value = mock_page

    _process_highlight_page(mock_pdf, mock_pdfium_doc, 0, re.compile("test"), {})

    mock_generate.assert_not_called()
    mock_textpage.close.assert_called_once()
    mock_page.close.assert_called_once()


@patch("pdftl.operations.highlight._generate_annotations_for_text")
def test_process_highlight_page_with_annots_no_existing(mock_generate, mock_pdf, mock_pdfium_doc):
    # Setup pdfium mocks
    mock_page = MagicMock()
    mock_textpage = MagicMock()
    mock_textpage.get_text_range.return_value = "hello test"
    mock_page.get_textpage.return_value = mock_textpage
    mock_pdfium_doc.get_page.return_value = mock_page

    # Setup returned annotations
    mock_annot = MagicMock()
    mock_generate.return_value = [mock_annot]

    # Setup pike_page (no /Annots initially)
    pike_page = MagicMock()
    pike_page.__contains__.side_effect = lambda key: key != "/Annots"
    mock_pdf.pages = [pike_page]

    _process_highlight_page(mock_pdf, mock_pdfium_doc, 0, re.compile("test"), {})

    # The function creates the list and appends the annot,
    # so we just assert the list contains our mock_annot
    assert pike_page.Annots == [mock_annot]


@patch("pdftl.operations.highlight._generate_annotations_for_text")
def test_process_highlight_page_with_annots_existing(mock_generate, mock_pdf, mock_pdfium_doc):
    mock_page = MagicMock()
    mock_textpage = MagicMock()
    mock_textpage.get_text_range.return_value = "hello test"
    mock_page.get_textpage.return_value = mock_textpage
    mock_pdfium_doc.get_page.return_value = mock_page

    mock_annot = MagicMock()
    mock_generate.return_value = [mock_annot]

    # Setup pike_page (/Annots exists)
    pike_page = MagicMock()
    pike_page.__contains__.side_effect = lambda key: key == "/Annots"
    pike_page.Annots = []
    mock_pdf.pages = [pike_page]

    _process_highlight_page(mock_pdf, mock_pdfium_doc, 0, re.compile("test"), {})

    # Since it's a standard list, we check its contents instead of using assert_called_once_with
    assert pike_page.Annots == [mock_annot]


# --- Tests for _generate_annotations_for_text ---


@patch("pdftl.operations.highlight._build_highlight_annotation")
def test_generate_annotations_for_text(mock_build):
    mock_build.return_value = "MockAnnot"
    textpage = MagicMock()
    textpage.count_rects.return_value = 2
    textpage.get_rect.side_effect = [(0, 0, 10, 10), (10, 10, 20, 20)]

    compiled_pattern = re.compile("test")
    page_text = "this is a test string"

    annots = _generate_annotations_for_text(textpage, page_text, compiled_pattern, {})

    assert len(annots) == 2
    assert annots == ["MockAnnot", "MockAnnot"]
    textpage.count_rects.assert_called_once()
    assert textpage.get_rect.call_count == 2


@patch("pdftl.operations.highlight._build_highlight_annotation")
def test_generate_annotations_for_text_zero_length(mock_build):
    # Regex that matches with 0 length (e.g. boundary)
    compiled_pattern = re.compile(r"\b")
    page_text = "test"
    textpage = MagicMock()

    annots = _generate_annotations_for_text(textpage, page_text, compiled_pattern, {})

    assert len(annots) == 0
    mock_build.assert_not_called()


# --- Tests for _build_highlight_annotation ---


def test_build_highlight_annotation_defaults():
    annot = _build_highlight_annotation(0, 0, 10, 10, {})

    assert annot.Type == pikepdf.Name("/Annot")
    assert annot.Subtype == pikepdf.Name("/Highlight")
    assert annot.Rect == [0, 0, 10, 10]
    assert annot.QuadPoints == [0, 10, 10, 10, 0, 0, 10, 0]
    assert annot.C == [1.0, 1.0, 0.0]
    assert annot.F == 4


def test_build_highlight_annotation_custom_options():
    opts = {
        "color": [0.0, 1.0, 0.0],
        "print": False,
        "author": "Jane",
        "contents": "Pop-up",
        "opacity": 0.5,
    }
    annot = _build_highlight_annotation(0, 0, 10, 10, opts)

    assert annot.C == [0.0, 1.0, 0.0]
    assert "/F" not in annot  # print=False -> no F=4 flag
    assert annot.T == pikepdf.String("Jane")
    assert annot.Contents == pikepdf.String("Pop-up")
    assert annot.CA == 0.5


# --- Tests for highlight_pdf ---


def test_highlight_pdf_empty_specs(mock_pdf):
    result = highlight_pdf(mock_pdf, [])
    assert result.success is True
    assert result.pdf == mock_pdf


@patch("pdftl.operations.highlight.ensure_dependencies")
@patch("pdftl.operations.highlight._apply_highlight_spec")
def test_highlight_pdf_valid(mock_apply, mock_ensure, mock_pdf):
    # We must patch pypdfium2 at the module level because it is imported dynamically
    with patch.dict("sys.modules", {"pypdfium2": MagicMock()}):
        specs = ["1/test/", "2/other/"]

        result = highlight_pdf(mock_pdf, specs)

        assert result.success is True
        assert result.pdf == mock_pdf
        mock_pdf.save.assert_called_once()
        assert mock_apply.call_count == 2
