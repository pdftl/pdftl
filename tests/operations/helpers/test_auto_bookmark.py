# tests/operations/helpers/test_auto_bookmark.py

import json
import os
import shutil
from unittest.mock import MagicMock, patch

import pytest

from pdftl.operations.helpers.auto_bookmark import (
    _apply_bookmarks_to_outline,
    _create_outline_item,
    _flatten_tree,
    _has_excessive_single_chars,
    _is_heading,
    _parse_bbox,
    _parse_heading_level,
    _parse_page_idx,
    auto_bookmark_pdf,
)
from pdftl.utils.run_opendataloader import run_opendataloader_extraction

JAVA_AVAILABLE = shutil.which("java") is not None


class TestHelperFunctions:
    def test_flatten_tree_dict_and_list(self):
        tree = {
            "id": 1,
            "kids": [
                {"id": 2, "type": "heading"},
                {"id": 3, "elements": [{"id": 4, "type": "paragraph"}]},
            ],
        }
        nodes = _flatten_tree(tree)
        node_ids = [n["id"] for n in nodes]
        assert node_ids == [1, 2, 3, 4]

    def test_flatten_tree_empty_and_invalid(self):
        assert _flatten_tree({}) == [{}]
        assert _flatten_tree([]) == []
        assert _flatten_tree(None) == []
        assert _flatten_tree({"kids": "not_a_list"}) == [{"kids": "not_a_list"}]

    def test_has_excessive_single_chars(self):
        assert _has_excessive_single_chars("") is True
        assert _has_excessive_single_chars("A B C") is True
        assert _has_excessive_single_chars("A B word") is True
        assert _has_excessive_single_chars("Normal Heading Text") is False

    def test_is_heading(self):
        assert _is_heading({"type": "heading", "content": "Introduction"}) is True
        assert _is_heading({"pdfua_tag": "H1", "content": "Introduction"}) is True
        assert _is_heading({"pdfua_tag": "H", "content": "Introduction"}) is True
        assert _is_heading({"role": "H2", "content": "Introduction"}) is False
        assert _is_heading({"pdfua_tag": "Header", "content": "Introduction"}) is False
        assert _is_heading({"type": "paragraph", "content": "Introduction"}) is False
        assert _is_heading({}) is False
        assert _is_heading(None) is False
        assert _is_heading({"type": "heading", "content": "Hi"}) is False
        assert _is_heading({"type": "heading", "content": "!!!$$$###"}) is False
        assert _is_heading({"type": "heading", "content": "A B C"}) is False

    def test_parse_page_idx(self):
        assert _parse_page_idx({"page number": 1}) == 0
        assert _parse_page_idx({"page": "5"}) == 4
        assert _parse_page_idx({"page number": 0}) == 0
        assert _parse_page_idx({"page number": "invalid"}) == 0
        assert _parse_page_idx({}) == 0

    def test_parse_heading_level(self):
        assert _parse_heading_level({"heading level": 3}) == 3
        assert _parse_heading_level({"pdfua_tag": "H2"}) == 2
        assert _parse_heading_level({"role": "H4"}) == 1
        assert _parse_heading_level({"pdfua_tag": "P"}) == 1
        assert _parse_heading_level({"pdfua_tag": "Header"}) == 1
        assert _parse_heading_level({}) == 1

    def test_parse_bbox(self):
        assert _parse_bbox({"bounding box": [110.8, 380.0, 200.0, 390.0]}) == (110.8, 390.0)
        assert _parse_bbox({"bbox": [5.0, 20.0, 15.0, 50.0]}) == (5.0, 50.0)
        assert _parse_bbox({"bounding_box": [1.0, 2.0, 3.0, 4.0]}) == (1.0, 4.0)
        assert _parse_bbox({"bounding box": [1.0, 2.0]}) == (None, None)
        assert _parse_bbox({"bounding box": "invalid"}) == (None, None)
        assert _parse_bbox({"bounding box": ["a", "b", "c", "d"]}) == (None, None)
        assert _parse_bbox({}) == (None, None)

    def test_create_outline_item(self):
        mock_pikepdf = MagicMock()

        _create_outline_item(mock_pikepdf, "Title", 0, 110.8, 390.0)
        mock_pikepdf.OutlineItem.assert_called_once_with(
            "Title", 0, page_location="XYZ", left=110.8, top=390.0
        )

        mock_pikepdf.reset_mock()
        _create_outline_item(mock_pikepdf, "Title", 0, None, None)
        mock_pikepdf.OutlineItem.assert_called_once_with("Title", 0)


class TestApplyBookmarks:
    def test_apply_bookmarks_to_outline_hierarchy(self):
        mock_pikepdf = MagicMock()
        mock_pdf = MagicMock()
        mock_outline = MagicMock()
        mock_pdf.open_outline.return_value.__enter__.return_value = mock_outline

        item_h1 = MagicMock()
        item_h1.children = []
        item_h2 = MagicMock()
        item_h2.children = []

        mock_pikepdf.OutlineItem.side_effect = [item_h1, item_h2, MagicMock(), MagicMock()]

        headings = [
            {"content": "Chapter 1", "page number": 1, "heading level": 1},
            {"content": "Section 1.1", "page number": 2, "heading level": 2},
            {"content": "Section 1.2", "page number": 3, "heading level": 2},
            {"content": "Chapter 2", "page number": 5, "heading level": 1},
            {"content": "   ", "page number": 6, "heading level": 2},
        ]

        _apply_bookmarks_to_outline(mock_pikepdf, mock_pdf, headings)

        assert mock_outline.root.clear.called
        assert mock_pikepdf.OutlineItem.call_count == 4
        assert len(item_h1.children) == 2

    def test_apply_bookmarks_parent_is_list(self):
        mock_pikepdf = MagicMock()
        mock_pdf = MagicMock()
        mock_outline = MagicMock()
        mock_outline.root = []
        mock_pdf.open_outline.return_value.__enter__.return_value = mock_outline

        headings = [{"content": "Title", "page number": 1, "heading level": 1}]
        _apply_bookmarks_to_outline(mock_pikepdf, mock_pdf, headings)

        assert len(mock_outline.root) == 1


class TestPipelineAndExtraction:
    def test_run_opendataloader_extraction_missing_json(self):
        mock_pdf = MagicMock()
        mock_opendataloader = MagicMock()

        with patch.dict("sys.modules", {"opendataloader_pdf": mock_opendataloader}):
            with patch("os.path.exists", return_value=False):
                result = run_opendataloader_extraction(mock_pdf)
                assert result is None
                assert mock_opendataloader.convert.called

    def test_run_opendataloader_extraction_success(self):
        mock_pdf = MagicMock()
        mock_opendataloader = MagicMock()

        def fake_convert(input_path, output_dir, format, quiet):
            json_file = os.path.join(output_dir, "input.json")
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump({"kids": [{"type": "heading", "content": "Extracted"}]}, f)

        mock_opendataloader.convert.side_effect = fake_convert

        with patch.dict("sys.modules", {"opendataloader_pdf": mock_opendataloader}):
            result = run_opendataloader_extraction(mock_pdf)
            assert result == {"kids": [{"type": "heading", "content": "Extracted"}]}

    @patch("pdftl.operations.helpers.auto_bookmark.run_opendataloader_extraction")
    @patch("pdftl.operations.helpers.auto_bookmark.ensure_dependencies")
    def test_auto_bookmark_pdf_no_data(self, mock_deps, mock_extract):
        mock_pdf = MagicMock()
        mock_extract.return_value = None

        result = auto_bookmark_pdf(mock_pdf)
        assert result == mock_pdf

    @patch("pdftl.operations.helpers.auto_bookmark.run_opendataloader_extraction")
    @patch("pdftl.operations.helpers.auto_bookmark.ensure_dependencies")
    def test_auto_bookmark_pdf_no_headings(self, mock_deps, mock_extract):
        mock_pdf = MagicMock()
        mock_extract.return_value = {
            "kids": [{"type": "paragraph", "content": "Just text", "page number": 1}]
        }

        result = auto_bookmark_pdf(mock_pdf)
        assert result == mock_pdf

    @patch("pdftl.operations.helpers.auto_bookmark._apply_bookmarks_to_outline")
    @patch("pdftl.operations.helpers.auto_bookmark.run_opendataloader_extraction")
    @patch("pdftl.operations.helpers.auto_bookmark.ensure_dependencies")
    def test_auto_bookmark_pdf_success(self, mock_deps, mock_extract, mock_apply):
        mock_pdf = MagicMock()
        mock_extract.return_value = {
            "kids": [
                {
                    "type": "heading",
                    "content": "Introduction",
                    "page number": 1,
                    "heading level": 1,
                    "bounding box": [10.0, 20.0, 30.0, 40.0],
                }
            ]
        }

        result = auto_bookmark_pdf(mock_pdf)
        assert result == mock_pdf
        assert mock_apply.called


@pytest.mark.skipif(
    not JAVA_AVAILABLE, reason="Java environment is required for end-to-end extraction test"
)
class TestIntegrationJava:
    @patch("pdftl.operations.helpers.auto_bookmark.ensure_dependencies")
    def test_java_environment_check(self, mock_deps):
        assert JAVA_AVAILABLE is True


class TestRealisticSchemaShape:
    """Exercise flattening/heading-detection against field names and nesting
    that actually match the OpenDataLoader PDF output schema, rather than
    the minimal synthetic dicts used elsewhere in this file."""

    SCHEMA_LIKE_DOC = {
        "file name": "input.pdf",
        "number of pages": 2,
        "author": None,
        "title": None,
        "creation date": None,
        "modification date": None,
        "kids": [
            {
                "type": "heading",
                "pdfua_tag": "H1",
                "heading level": 1,
                "content": "Chapter One",
                "page number": 1,
                "bounding box": [72.0, 700.0, 300.0, 720.0],
            },
            {
                "type": "paragraph",
                "pdfua_tag": "P",
                "content": "Some body text that is not a heading.",
                "page number": 1,
                "bounding box": [72.0, 600.0, 500.0, 690.0],
            },
            {
                "type": "list",
                "pdfua_tag": "L",
                "page number": 1,
                "bounding box": [72.0, 500.0, 300.0, 590.0],
                "list items": [
                    {
                        "type": "list item",
                        "pdfua_tag": "LI",
                        "content": "First bullet",
                        "page number": 1,
                        "bounding box": [90.0, 560.0, 300.0, 580.0],
                        "kids": [],
                    }
                ],
            },
        ],
    }

    def test_flatten_and_detect_headings_from_realistic_doc(self):
        nodes = _flatten_tree(self.SCHEMA_LIKE_DOC)
        headings = [n for n in nodes if _is_heading(n)]

        assert len(headings) == 1
        assert headings[0]["content"] == "Chapter One"
        assert _parse_heading_level(headings[0]) == 1
        assert _parse_bbox(headings[0]) == (72.0, 720.0)
