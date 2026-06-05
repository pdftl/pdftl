import pytest
from unittest.mock import MagicMock, patch

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.delete_attachments import (
    _parse_size_str,
    _get_params,
    _get_attachment_size,
    _attachment_matches,
    _evaluate_spec,
    delete_attachments,
)


# --- Tests for _parse_size_str ---


def test_parse_size_str():
    assert _parse_size_str("500") == 500
    assert _parse_size_str("1k") == 1024
    assert _parse_size_str("2M") == 2 * (1024**2)
    assert _parse_size_str("1.5g") == int(1.5 * (1024**3))
    assert _parse_size_str(" 100K ") == 102400

    with pytest.raises(ValueError):
        _parse_size_str("invalid")


# --- Tests for _get_params ---


@patch("pdftl.operations.delete_attachments.parse_keyval_string")
def test_get_params_valid(mock_parse):
    mock_parse.return_value = {"ext": "pdf", "minbytes": "1k"}
    params = _get_params("ext=pdf,minbytes=1k")
    assert params == {"ext": "pdf", "minbytes": "1k"}


@patch("pdftl.operations.delete_attachments.parse_keyval_string")
def test_get_params_invalid_size(mock_parse):
    mock_parse.return_value = {"maxbytes": "bad_size"}
    with pytest.raises(InvalidArgumentError, match="Expected a size"):
        _get_params("maxbytes=bad_size")


# --- Tests for _get_attachment_size ---


def test_get_attachment_size_from_metadata():
    mock_att = MagicMock()
    # Mock deeply nested metadata dictionary: obj.get("/EF").get("/F").get("/Length")
    mock_att.obj.get.return_value.get.return_value.get.return_value = 1048
    assert _get_attachment_size(mock_att) == 1048


def test_get_attachment_size_fallback():
    mock_att = MagicMock()
    # Force metadata fetch to fail
    mock_att.obj.get.side_effect = AttributeError("No dict")
    mock_att.get_file.return_value.read_bytes.return_value = b"x" * 123
    assert _get_attachment_size(mock_att) == 123


# --- Tests for _attachment_matches ---


def test_attachment_matches_empty_params():
    assert _attachment_matches("test.txt", MagicMock(), {}) is True


def test_attachment_matches_name():
    att = MagicMock()
    assert _attachment_matches("Draft.txt", att, {"name": "draft.txt"}) is True
    assert _attachment_matches("Draft.txt", att, {"name": "final.txt"}) is False


def test_attachment_matches_namehas():
    att = MagicMock()
    assert _attachment_matches("my_draft_v2.txt", att, {"namehas": "draft"}) is True
    assert _attachment_matches("my_final.txt", att, {"namehas": "draft"}) is False


def test_attachment_matches_ext():
    att = MagicMock()
    # Without dot
    assert _attachment_matches("data.csv", att, {"ext": "csv"}) is True
    assert _attachment_matches("data.csv", att, {"ext": "pdf"}) is False
    # With dot
    assert _attachment_matches("image.JPG", att, {"ext": ".jpg"}) is True


def test_attachment_matches_relation():
    att = MagicMock()

    # Matching relationship
    att.relationship = "/Source"
    assert _attachment_matches("code.py", att, {"relation": "source"}) is True
    assert _attachment_matches("code.py", att, {"relation": "data"}) is False

    # Unspecified relationship
    att.relationship = None
    assert _attachment_matches("code.py", att, {"relation": "unspecified"}) is True
    assert _attachment_matches("code.py", att, {"relation": "source"}) is False


@patch("pdftl.operations.delete_attachments._get_attachment_size")
def test_attachment_matches_size(mock_get_size):
    att = MagicMock()
    mock_get_size.return_value = 1024  # 1KB

    # Minbytes
    assert _attachment_matches("f.txt", att, {"minbytes": "500"}) is True
    assert _attachment_matches("f.txt", att, {"minbytes": "2k"}) is False

    # Maxbytes
    assert _attachment_matches("f.txt", att, {"maxbytes": "2k"}) is True
    assert _attachment_matches("f.txt", att, {"maxbytes": "500"}) is False


# --- Tests for _evaluate_spec ---


@patch("pdftl.operations.delete_attachments._get_params")
@patch("pdftl.operations.delete_attachments.page_numbers_matching_page_spec")
def test_evaluate_spec_routing(mock_page_match, mock_get_params):
    mock_get_params.return_value = {}  # Allow all
    mock_page_match.return_value = [1, 2]

    mock_pdf = MagicMock()
    mock_pdf.pages = [MagicMock(), MagicMock(), MagicMock()]

    att1, att2 = MagicMock(), MagicMock()
    att1.obj.objgen = 101
    att2.obj.objgen = 102
    mock_pdf.attachments = {"doc_only.pdf": att1, "on_page.pdf": att2}

    # Annot map: objgen 102 is on page 1
    annot_map = {102: {1}}

    # 1. Test "doc" spec (should skip files that are on a page)
    res_doc = _evaluate_spec(mock_pdf, "doc()", annot_map)
    assert res_doc == {"doc_only.pdf"}

    # 2. Test specific page range (should skip files not on that page)
    res_page = _evaluate_spec(mock_pdf, "1-2()", annot_map)
    assert res_page == {"on_page.pdf"}

    # 3. Test global spec (should match everything)
    res_global = _evaluate_spec(mock_pdf, "-", annot_map)
    assert res_global == {"doc_only.pdf", "on_page.pdf"}


# --- Tests for delete_attachments (Main Operation) ---


def test_delete_attachments_no_attachments():
    mock_pdf = MagicMock()
    mock_pdf.attachments = {}

    result = delete_attachments(mock_pdf, [])

    assert result.success is True
    assert result.pdf == mock_pdf


@patch("pdftl.operations.delete_attachments._evaluate_spec")
def test_delete_attachments_no_matches(mock_eval):
    mock_pdf = MagicMock()
    mock_pdf.attachments = {"dummy.txt": MagicMock()}
    mock_pdf.pages = []

    mock_eval.return_value = set()  # No matches found

    result = delete_attachments(mock_pdf, [""])
    assert result.success is True


@patch("pikepdf.Array", new=list)
@patch("pdftl.operations.delete_attachments._evaluate_spec")
def test_delete_attachments_scrubs_annotations(mock_eval):
    mock_pdf = MagicMock()

    # Setup document attachment
    att_to_delete = MagicMock()
    att_to_delete.obj.objgen = 999

    att_to_keep = MagicMock()
    att_to_keep.obj.objgen = 111

    # We use a standard dict instead of MagicMock for attachments to allow `del`
    mock_pdf.attachments = {"bad_file.zip": att_to_delete, "good_file.zip": att_to_keep}

    # Setup Pages with Annotations
    page1 = MagicMock()
    # FIX: Explicitly mock __contains__ so `if "/Annots" in page:` evaluates to True
    page1.__contains__.side_effect = lambda key: key == "/Annots"

    # Annot 1: Matches the file to delete
    annot_delete = MagicMock()
    annot_delete.get.side_effect = (
        lambda k: "/FileAttachment" if k == "/Subtype" else MagicMock(objgen=999)
    )

    # Annot 2: Matches the file to keep
    annot_keep = MagicMock()
    annot_keep.get.side_effect = (
        lambda k: "/FileAttachment" if k == "/Subtype" else MagicMock(objgen=111)
    )

    # Annot 3: Not a file attachment (should be ignored and kept)
    annot_other = MagicMock()
    annot_other.get.return_value = "/Link"

    page1.Annots = [annot_delete, annot_keep, annot_other]

    # Setup a second page whose only annotation gets deleted, resulting in the `/Annots` key being removed
    page2 = MagicMock()
    page2.Annots = [annot_delete]
    page2.__contains__.side_effect = lambda key: key == "/Annots"

    mock_pdf.pages = [page1, page2]

    # Force the spec evaluator to mark "bad_file.zip" for deletion
    mock_eval.return_value = {"bad_file.zip"}

    # --- Execute ---
    result = delete_attachments(mock_pdf, [""])

    # --- Verify ---
    assert result.success is True

    # 1. Ensure file was removed from the PDF NameTree
    assert "bad_file.zip" not in mock_pdf.attachments
    assert "good_file.zip" in mock_pdf.attachments

    # 2. Ensure page 1 scrubbed the bad annotation but kept the good ones
    assert len(page1.Annots) == 2
    assert annot_delete not in page1.Annots
    assert annot_keep in page1.Annots
    assert annot_other in page1.Annots

    # 3. Ensure page 2 had its only annotation scrubbed and the /Annots key deleted
    assert len(page2.Annots) == 0
    page2.__delitem__.assert_called_with("/Annots")
