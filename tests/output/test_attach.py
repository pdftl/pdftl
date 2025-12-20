from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pikepdf
import pytest

# --- Import Exceptions ---
from pdftl.exceptions import InvalidArgumentError, MissingArgumentError

# --- Import module and functions to test (UPDATED) ---
from pdftl.output.attach import ParsedAttachment  # New "dumb" data class
from pdftl.output.attach import _resolve_attachments  # The new "impure" resolver
from pdftl.output.attach import _set_page_specs_in_parsed_attachments  # New helper
from pdftl.output.attach import (  # This is the main pipeline; The pure parser (tested by hypothesis)
    Attachment,
    _attach_attachment,
    _attach_attachment_to_page,
    _attachment_rect,
    _get_attachments_from_options,
    _raise_exception_if_invalid_after_keyword,
    _validate_topage_and_convert_to_ints,
    attach_files,
)

# --- Import Mocks ---
from pdftl.utils.user_input import UserInputContext

# --- Fixtures (Unchanged) ---


@pytest.fixture
def mock_input_context():
    """Mock for the UserInputContext."""
    mock = MagicMock(spec=UserInputContext)
    mock.get_input = MagicMock()
    return mock


@pytest.fixture
def mock_pdf():
    """Mock for a pikepdf.Pdf object."""
    pdf = MagicMock(spec=pikepdf.Pdf)
    pdf.pages = []
    pdf.attachments = {}
    return pdf


@pytest.fixture(autouse=True)
def patch_logging(mocker):
    """Patch logging for all tests in this module."""
    mocker.patch("pdftl.output.attach.logging")


# --- Test Cases ---


def test_raise_exception_if_invalid_after_keyword_missing_file():
    with pytest.raises(MissingArgumentError, match="Missing filename before 'to_page'"):
        _raise_exception_if_invalid_after_keyword(
            ["to_page", "1"], 0, attachments_is_empty=True, keyword="to_page"
        )


def test_raise_exception_if_invalid_after_keyword_missing_arg():
    with pytest.raises(MissingArgumentError, match="Missing argument after 'relation'"):
        _raise_exception_if_invalid_after_keyword(
            ["file.pdf", "relation"], 1, attachments_is_empty=False, keyword="relation"
        )


def test_set_page_specs_in_parsed_attachments():
    p1 = ParsedAttachment(path="a.pdf")
    p2 = ParsedAttachment(path="b.pdf", page_spec="1")
    p3 = ParsedAttachment(path="c.pdf")

    attachments_to_set = [p1, p2, p3]
    _set_page_specs_in_parsed_attachments("even", attachments_to_set)

    assert p1.page_spec == "even"
    assert p2.page_spec == "1"
    assert p3.page_spec == "even"


## _validate_topage_and_convert_to_ints ##


@patch("pdftl.output.attach.page_numbers_matching_page_spec", return_value=[1, 3, 5])
def test_validate_topage_and_convert_to_ints_success(mock_page_numbers):
    assert _validate_topage_and_convert_to_ints("1,3,5", 5) == [1, 3, 5]


@patch("pdftl.output.attach.page_numbers_matching_page_spec", return_value=[])
def test_validate_topage_and_convert_to_ints_no_matches(mock_page_numbers):
    with pytest.raises(InvalidArgumentError, match="did not yield any pages"):
        _validate_topage_and_convert_to_ints("99", 5)


@patch(
    "pdftl.output.attach.page_numbers_matching_page_spec",
    side_effect=ValueError("bad spec"),
)
def test_validate_topage_and_convert_to_ints_parser_error(mock_page_numbers):
    with pytest.raises(InvalidArgumentError, match="gave an error: bad spec"):
        _validate_topage_and_convert_to_ints("foo", 5)


## _resolve_attachments ##


@patch("pdftl.output.attach.can_read_file", return_value=True)
@patch("pdftl.output.attach._validate_topage_and_convert_to_ints", return_value=[1, 2])
def test_resolve_attachments_happy_path(mock_validate, mock_can_read, mock_input_context):
    parsed_items = [
        ParsedAttachment(path="a.pdf", page_spec="1-2"),
        ParsedAttachment(path="b.pdf", relationship="source"),
    ]
    num_pages = 5

    resolved = _resolve_attachments(parsed_items, num_pages, mock_input_context)

    assert len(resolved) == 2

    assert resolved[0].path == Path("a.pdf")
    assert resolved[0].pages == [1, 2]
    mock_validate.assert_called_once_with("1-2", num_pages)

    assert resolved[1].path == Path("b.pdf")
    assert resolved[1].relationship == "Source"


@patch("pdftl.output.attach.can_read_file", side_effect=[True, True])  # Needs two True values now
def test_resolve_attachments_file_prompt(mock_can_read, mock_input_context):
    """
    Tests that a 'PROMPT' path triggers the interactive prompter.
    """
    mock_input_context.get_input.return_value = "prompted.pdf"

    parsed_items = [ParsedAttachment(path="PROMPT")]

    resolved = _resolve_attachments(parsed_items, 5, mock_input_context)

    # Check that _can_read_file was called twice
    assert mock_can_read.call_count == 2
    mock_can_read.assert_has_calls(
        [
            call("prompted.pdf"),  # First call inside get_filename
            call("prompted.pdf"),  # Second call in main flow
        ]
    )

    mock_input_context.get_input.assert_called_once()

    assert len(resolved) == 1
    assert resolved[0].path == Path("prompted.pdf")


@patch("pdftl.output.attach.can_read_file", return_value=True)
def test_resolve_attachments_invalid_relation(mock_can_read, mock_input_context):
    parsed_items = [ParsedAttachment(path="a.pdf", relationship="Friend")]

    with pytest.raises(InvalidArgumentError, match="Invalid attachment relationship"):
        _resolve_attachments(parsed_items, 5, mock_input_context)


@patch("pdftl.output.attach._resolve_attachments")
@patch("pdftl.output.attach._parse_attach_specs_to_intent")
def test_get_attachments_from_options_pipeline(mock_parse, mock_resolve, mock_input_context):
    options = {"attach_files": ["a.pdf", "to_page", "1"]}
    num_pages = 5

    mock_parsed_items = [ParsedAttachment(path="a.pdf", page_spec="1")]
    mock_parse.return_value = mock_parsed_items

    mock_resolved_items = [Attachment(path=Path("a.pdf"), pages=[1])]
    mock_resolve.return_value = mock_resolved_items

    result = _get_attachments_from_options(options, num_pages, mock_input_context)

    mock_parse.assert_called_once_with(options["attach_files"])
    mock_resolve.assert_called_once_with(mock_parsed_items, num_pages, mock_input_context)
    assert result == mock_resolved_items


@patch("pdftl.output.attach._attach_attachment_to_page")
def test_attach_attachment_to_pages(mock_attach_to_page, mock_pdf):
    att = Attachment(Path("f.txt"), pages=[1, 3])
    num_attached = [0, 0, 0]

    _attach_attachment(mock_pdf, att, num_attached)

    assert mock_attach_to_page.call_count == 2
    mock_attach_to_page.assert_has_calls(
        [
            call(mock_pdf, att, 1, 0),
            call(mock_pdf, att, 3, 0),
        ]
    )
    assert num_attached == [1, 0, 1]


@patch("pikepdf.AttachedFileSpec.from_filepath")
def test_attach_attachment_to_pdf(mock_from_filepath, mock_pdf):
    mock_spec = MagicMock()
    mock_from_filepath.return_value = mock_spec
    att = Attachment(Path("dir/f.txt"), pages=None, relationship="Source")

    _attach_attachment(mock_pdf, att, [])

    mock_from_filepath.assert_called_once_with(mock_pdf, att.path)
    assert mock_spec.relationship == pikepdf.Name("/Source")
    assert mock_pdf.attachments["f.txt"] == mock_spec


@patch("pikepdf.Annotation")
@patch("pikepdf.Dictionary")
def test_attach_attachment_to_page_existing_spec(mock_Dict, mock_Annotation, mock_pdf):
    mock_page = MagicMock(cropbox=[0, 0, 100, 100])
    mock_page.Annots = MagicMock(spec=pikepdf.Array)
    mock_pdf.pages = [mock_page]
    mock_file_spec_obj = MagicMock()
    mock_pdf.attachments["f.txt"] = MagicMock(obj=mock_file_spec_obj)

    att = Attachment(Path("f.txt"))

    _attach_attachment_to_page(mock_pdf, att, 1, 0)

    expected_rect = _attachment_rect(mock_page.cropbox, 0)
    expected_dict = {
        "/Contents": pikepdf.String("f.txt"),
        "/Subtype": pikepdf.Name.FileAttachment,
        "/FS": mock_file_spec_obj,
        "/Rect": expected_rect,
    }

    mock_Dict.assert_called_once_with(expected_dict)
    mock_Annotation.assert_called_once_with(mock_Dict.return_value)
    mock_page.Annots.append.assert_called_once_with(mock_Annotation.return_value.obj)


@patch("pdftl.output.attach._attach_attachment")
@patch("pdftl.output.attach._get_attachments_from_options")
def test_attach_files_orchestration(mock_get_options, mock_attach, mock_input_context, mock_pdf):
    mock_pdf.pages = [MagicMock(), MagicMock(), MagicMock()]
    num_pages = len(mock_pdf.pages)

    att1 = Attachment(Path("a.txt"))
    att2 = Attachment(Path("b.txt"), pages=[1])
    mock_get_options.return_value = [att1, att2]

    options = {"attach_files": ["..."]}

    attach_files(mock_pdf, options, mock_input_context)

    mock_get_options.assert_called_once_with(options, num_pages, mock_input_context)
    assert mock_attach.call_count == 2


@patch("pikepdf.Annotation")
@patch("pikepdf.Dictionary")
@patch("pikepdf.AttachedFileSpec.from_filepath")
def test_page_attachment_with_relationship(
    mock_from_filepath, mock_Dictionary, mock_Annotation, mock_pdf
):
    mock_spec = MagicMock(spec=pikepdf.AttachedFileSpec)
    mock_from_filepath.return_value = mock_spec

    mock_page = MagicMock(cropbox=[0, 0, 100, 100])
    mock_page.Annots = MagicMock(spec=pikepdf.Array)
    mock_pdf.pages = [mock_page]
    mock_pdf.attachments = {}

    att = Attachment(path=Path("/tmp/t.pdf"), pages=[1], relationship="Source")

    _attach_attachment(mock_pdf, att, num_attached_by_page=[0])

    assert mock_spec.relationship == pikepdf.Name("/Source")
    mock_page.Annots.append.assert_called_once()
