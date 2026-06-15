# tests/test_dump_tables.py

"""Tests for the dump_tables operation."""

import io
import json
from unittest.mock import MagicMock, patch


import pdftl.core.constants as c
from pdftl.operations.dump_tables import (
    _clean_cell_text,
    _extract_tables,
    _parse_dump_tables_args,
    _passes_filters,
    _table_to_dict,
    _tables_to_csv,
    _tables_to_markdown,
    dump_tables,
    dump_tables_cli_hook,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_cell(text, merged_left=False, merged_top=False):
    cell = MagicMock()
    cell.text = text
    cell.merged_left = merged_left
    cell.merged_top = merged_top
    return cell


def _make_tablers_table(rows_of_cells, bbox=(10.0, 20.0, 200.0, 100.0)):
    """Build a mock tablers Table object."""
    table = MagicMock()
    table.bbox = bbox
    table.rows = [MagicMock() for _ in rows_of_cells]
    table.columns = [MagicMock() for _ in rows_of_cells[0]] if rows_of_cells else []
    table.to_list.return_value = rows_of_cells
    return table


def _simple_table_dict(page=1, table_index=0):
    """A minimal well-formed table dict for filter/format tests."""
    return {
        "page": page,
        "table_index": table_index,
        "bbox": [10.0, 20.0, 200.0, 100.0],
        "rows": 2,
        "cols": 2,
        "data": [
            [
                {"text": "Name", "merged_left": False, "merged_top": False},
                {"text": "Value", "merged_left": False, "merged_top": False},
            ],
            [
                {"text": "foo", "merged_left": False, "merged_top": False},
                {"text": "bar", "merged_left": False, "merged_top": False},
            ],
        ],
    }


# ---------------------------------------------------------------------------
# _clean_cell_text
# ---------------------------------------------------------------------------


class TestCleanCellText:
    def test_none_returns_empty_string(self):
        assert _clean_cell_text(None) == ""

    def test_plain_text_unchanged(self):
        assert _clean_cell_text("hello") == "hello"

    def test_strips_surrounding_whitespace(self):
        assert _clean_cell_text("  hello  ") == "hello"

    def test_strips_surrounding_newlines(self):
        assert _clean_cell_text("\nhello\n") == "hello"

    def test_preserves_internal_newlines(self):
        assert _clean_cell_text("line1\nline2") == "line1\nline2"

    def test_preserves_internal_tabs(self):
        assert _clean_cell_text("col1\tcol2") == "col1\tcol2"

    def test_removes_null_bytes(self):
        assert _clean_cell_text("ab\x00cd") == "abcd"

    def test_removes_control_characters(self):
        assert _clean_cell_text("ab\x01\x02\x1fcd") == "abcd"

    def test_removes_surrogate_characters(self):
        # Surrogates from broken PDFium UCS-2
        text = "ab\ud800\udfff cd"
        result = _clean_cell_text(text)
        assert "\ud800" not in result
        assert "\udfff" not in result
        assert "ab" in result

    def test_empty_string_returns_empty(self):
        assert _clean_cell_text("") == ""

    def test_whitespace_only_returns_empty(self):
        assert _clean_cell_text("   \n\t  ") == ""

    def test_only_control_chars_returns_empty(self):
        assert _clean_cell_text("\x00\x01\x02") == ""

    def test_unicode_text_preserved(self):
        assert _clean_cell_text("α β γ") == "α β γ"

    def test_greek_letters_preserved(self):
        assert _clean_cell_text("α1 α2") == "α1 α2"


# ---------------------------------------------------------------------------
# _parse_dump_tables_args
# ---------------------------------------------------------------------------


class TestParseDumpTablesArgs:
    def test_empty_args(self):
        specs, fmt, filters = _parse_dump_tables_args([])
        assert specs == []
        assert fmt == "json"
        assert filters == {
            "min_rows": None,
            "min_cols": None,
            "min_area": None,
            "no_empty": False,
        }

    def test_none_args(self):
        specs, fmt, filters = _parse_dump_tables_args(None)
        assert specs == []
        assert fmt == "json"

    def test_csv_format(self):
        _, fmt, _ = _parse_dump_tables_args(["csv"])
        assert fmt == "csv"

    def test_markdown_format(self):
        _, fmt, _ = _parse_dump_tables_args(["markdown"])
        assert fmt == "markdown"

    def test_page_specs_passed_through(self):
        specs, fmt, _ = _parse_dump_tables_args(["1", "3-5"])
        assert specs == ["1", "3-5"]
        assert fmt == "json"

    def test_min_rows(self):
        _, _, filters = _parse_dump_tables_args(["min_rows=2"])
        assert filters["min_rows"] == 2

    def test_min_cols(self):
        _, _, filters = _parse_dump_tables_args(["min_cols=3"])
        assert filters["min_cols"] == 3

    def test_min_area(self):
        _, _, filters = _parse_dump_tables_args(["min_area=500.0"])
        assert filters["min_area"] == 500.0

    def test_no_empty(self):
        _, _, filters = _parse_dump_tables_args(["no_empty"])
        assert filters["no_empty"] is True

    def test_combined_args(self):
        specs, fmt, filters = _parse_dump_tables_args(
            ["csv", "1", "3-5", "min_rows=2", "min_cols=2", "no_empty", "min_area=100"]
        )
        assert fmt == "csv"
        assert specs == ["1", "3-5"]
        assert filters["min_rows"] == 2
        assert filters["min_cols"] == 2
        assert filters["min_area"] == 100.0
        assert filters["no_empty"] is True

    def test_format_and_page_specs(self):
        specs, fmt, _ = _parse_dump_tables_args(["markdown", "2", "4-6"])
        assert fmt == "markdown"
        assert specs == ["2", "4-6"]


# ---------------------------------------------------------------------------
# _table_to_dict
# ---------------------------------------------------------------------------


class TestTableToDict:
    def test_basic_structure(self):
        rows = [
            [_make_cell("A"), _make_cell("B")],
            [_make_cell("C"), _make_cell("D")],
        ]
        table = _make_tablers_table(rows, bbox=(10.0, 20.0, 200.0, 100.0))
        result = _table_to_dict(table, page_num=1, table_index=0)

        assert result["page"] == 1
        assert result["table_index"] == 0
        assert result["bbox"] == [10.0, 20.0, 200.0, 100.0]
        assert result["rows"] == 2
        assert result["cols"] == 2

    def test_cell_text_preserved(self):
        rows = [[_make_cell("hello"), _make_cell("world")]]
        table = _make_tablers_table(rows)
        result = _table_to_dict(table, page_num=1, table_index=0)
        assert result["data"][0][0]["text"] == "hello"
        assert result["data"][0][1]["text"] == "world"

    def test_merged_cell_flags(self):
        rows = [
            [_make_cell("A"), _make_cell(None, merged_left=True)],
        ]
        table = _make_tablers_table(rows)
        result = _table_to_dict(table, page_num=1, table_index=0)
        assert result["data"][0][0]["merged_left"] is False
        assert result["data"][0][1]["merged_left"] is True
        assert result["data"][0][1]["text"] is None

    def test_merged_top_flag(self):
        rows = [
            [_make_cell("A")],
            [_make_cell(None, merged_top=True)],
        ]
        table = _make_tablers_table(rows)
        result = _table_to_dict(table, page_num=2, table_index=1)
        assert result["data"][1][0]["merged_top"] is True

    def test_bbox_is_list(self):
        rows = [[_make_cell("x")]]
        table = _make_tablers_table(rows, bbox=(1.0, 2.0, 3.0, 4.0))
        result = _table_to_dict(table, page_num=1, table_index=0)
        assert isinstance(result["bbox"], list)

    def test_page_and_table_index(self):
        rows = [[_make_cell("x")]]
        table = _make_tablers_table(rows)
        result = _table_to_dict(table, page_num=7, table_index=3)
        assert result["page"] == 7
        assert result["table_index"] == 3


# ---------------------------------------------------------------------------
# _passes_filters
# ---------------------------------------------------------------------------


class TestPassesFilters:
    def _default_filters(self):
        return {"min_rows": None, "min_cols": None, "min_area": None, "no_empty": False}

    def test_no_filters_passes_everything(self):
        tbl = _simple_table_dict()
        assert _passes_filters(tbl, self._default_filters()) is True

    def test_min_rows_passes(self):
        tbl = _simple_table_dict()
        tbl["rows"] = 3
        filters = {**self._default_filters(), "min_rows": 2}
        assert _passes_filters(tbl, filters) is True

    def test_min_rows_fails(self):
        tbl = _simple_table_dict()
        tbl["rows"] = 1
        filters = {**self._default_filters(), "min_rows": 2}
        assert _passes_filters(tbl, filters) is False

    def test_min_cols_passes(self):
        tbl = _simple_table_dict()
        tbl["cols"] = 3
        filters = {**self._default_filters(), "min_cols": 2}
        assert _passes_filters(tbl, filters) is True

    def test_min_cols_fails(self):
        tbl = _simple_table_dict()
        tbl["cols"] = 1
        filters = {**self._default_filters(), "min_cols": 2}
        assert _passes_filters(tbl, filters) is False

    def test_min_area_passes(self):
        # bbox area = (200-10) * (100-20) = 190 * 80 = 15200
        tbl = _simple_table_dict()
        filters = {**self._default_filters(), "min_area": 1000.0}
        assert _passes_filters(tbl, filters) is True

    def test_min_area_fails(self):
        tbl = _simple_table_dict()
        tbl["bbox"] = [0.0, 0.0, 9.0, 9.0]  # area = 81
        filters = {**self._default_filters(), "min_area": 1000.0}
        assert _passes_filters(tbl, filters) is False

    def test_no_empty_passes_when_has_content(self):
        tbl = _simple_table_dict()
        filters = {**self._default_filters(), "no_empty": True}
        assert _passes_filters(tbl, filters) is True

    def test_no_empty_fails_when_all_empty(self):
        tbl = _simple_table_dict()
        tbl["data"] = [
            [
                {"text": "", "merged_left": False, "merged_top": False},
                {"text": "", "merged_left": False, "merged_top": False},
            ],
            [
                {"text": None, "merged_left": False, "merged_top": False},
                {"text": "   ", "merged_left": False, "merged_top": False},
            ],
        ]
        filters = {**self._default_filters(), "no_empty": True}
        assert _passes_filters(tbl, filters) is False

    def test_no_empty_passes_when_one_cell_has_content(self):
        tbl = _simple_table_dict()
        tbl["data"] = [
            [
                {"text": "", "merged_left": False, "merged_top": False},
                {"text": "something", "merged_left": False, "merged_top": False},
            ],
        ]
        filters = {**self._default_filters(), "no_empty": True}
        assert _passes_filters(tbl, filters) is True

    def test_combined_filters_all_pass(self):
        tbl = _simple_table_dict()
        filters = {"min_rows": 2, "min_cols": 2, "min_area": 100.0, "no_empty": True}
        assert _passes_filters(tbl, filters) is True

    def test_combined_filters_one_fails(self):
        tbl = _simple_table_dict()
        tbl["rows"] = 1
        filters = {"min_rows": 2, "min_cols": 2, "min_area": 100.0, "no_empty": True}
        assert _passes_filters(tbl, filters) is False


# ---------------------------------------------------------------------------
# _tables_to_csv
# ---------------------------------------------------------------------------


class TestTablesToCsv:
    def test_basic_output(self):
        tables = [_simple_table_dict()]
        result = _tables_to_csv(tables)
        assert "# Page 1, table 0" in result
        assert "Name" in result
        assert "Value" in result

    def test_header_format(self):
        tables = [_simple_table_dict(page=3, table_index=1)]
        result = _tables_to_csv(tables)
        assert "# Page 3, table 1" in result

    def test_multiple_tables_separated_by_dashes(self):
        tables = [_simple_table_dict(page=1), _simple_table_dict(page=2)]
        result = _tables_to_csv(tables)
        assert "---" in result
        parts = result.split("---")
        assert len(parts) == 2

    def test_empty_tables_list(self):
        assert _tables_to_csv([]) == ""

    def test_null_cell_becomes_empty(self):
        tables = [_simple_table_dict()]
        tables[0]["data"] = [
            [
                {"text": None, "merged_left": True, "merged_top": False},
                {"text": "val", "merged_left": False, "merged_top": False},
            ]
        ]
        result = _tables_to_csv(tables)
        assert ",val" in result or ',"val"' in result

    def test_cell_with_null_bytes_cleaned(self):
        tables = [_simple_table_dict()]
        tables[0]["data"] = [
            [
                {"text": "ab\x00cd", "merged_left": False, "merged_top": False},
                {"text": "ok", "merged_left": False, "merged_top": False},
            ]
        ]
        result = _tables_to_csv(tables)
        assert "\x00" not in result
        assert "abcd" in result

    def test_cell_with_control_chars_cleaned(self):
        tables = [_simple_table_dict()]
        tables[0]["data"] = [
            [
                {"text": "ab\x01\x1fcd", "merged_left": False, "merged_top": False},
                {"text": "ok", "merged_left": False, "merged_top": False},
            ]
        ]
        result = _tables_to_csv(tables)
        assert "\x01" not in result
        assert "\x1f" not in result

    def test_newlines_in_cells_quoted(self):
        tables = [_simple_table_dict()]
        tables[0]["data"] = [
            [
                {"text": "line1\nline2", "merged_left": False, "merged_top": False},
                {"text": "ok", "merged_left": False, "merged_top": False},
            ]
        ]
        result = _tables_to_csv(tables)
        # multiline cell must be quoted
        assert '"line1\nline2"' in result

    def test_whitespace_stripped_from_cells(self):
        tables = [_simple_table_dict()]
        tables[0]["data"] = [
            [
                {"text": "  hello  ", "merged_left": False, "merged_top": False},
                {"text": "\nworld\n", "merged_left": False, "merged_top": False},
            ]
        ]
        result = _tables_to_csv(tables)
        assert "hello" in result
        assert "world" in result
        # leading/trailing whitespace should be gone
        assert "  hello  " not in result

    def test_valid_csv_parseable(self):
        import csv

        tables = [_simple_table_dict()]
        result = _tables_to_csv(tables)
        # Strip comment header lines before parsing
        csv_lines = [l for l in result.splitlines() if not l.startswith("#") and l != "---"]
        rows = list(csv.reader(csv_lines))
        assert rows[0] == ["Name", "Value"]
        assert rows[1] == ["foo", "bar"]

    def test_commas_in_cell_content_quoted(self):
        tables = [_simple_table_dict()]
        tables[0]["data"] = [
            [
                {"text": "a, b, c", "merged_left": False, "merged_top": False},
                {"text": "ok", "merged_left": False, "merged_top": False},
            ]
        ]
        result = _tables_to_csv(tables)
        assert '"a, b, c"' in result


# ---------------------------------------------------------------------------
# _tables_to_markdown
# ---------------------------------------------------------------------------


class TestTablesToMarkdown:
    def test_basic_output(self):
        tables = [_simple_table_dict()]
        result = _tables_to_markdown(tables)
        assert "| Name | Value |" in result
        assert "| --- | --- |" in result
        assert "| foo | bar |" in result

    def test_comment_header(self):
        tables = [_simple_table_dict(page=3, table_index=1)]
        result = _tables_to_markdown(tables)
        assert "<!-- Page 3, table 1 -->" in result

    def test_multiple_tables_separated_by_blank_line(self):
        tables = [_simple_table_dict(page=1), _simple_table_dict(page=2)]
        result = _tables_to_markdown(tables)
        assert "\n\n" in result

    def test_empty_tables_list(self):
        assert _tables_to_markdown([]) == ""

    def test_pipe_in_cell_escaped(self):
        tables = [_simple_table_dict()]
        tables[0]["data"] = [
            [
                {"text": "a|b", "merged_left": False, "merged_top": False},
                {"text": "ok", "merged_left": False, "merged_top": False},
            ]
        ]
        result = _tables_to_markdown(tables)
        assert "a\\|b" in result

    def test_newlines_in_cells_replaced_with_space(self):
        tables = [_simple_table_dict()]
        tables[0]["data"] = [
            [
                {"text": "line1\nline2", "merged_left": False, "merged_top": False},
                {"text": "ok", "merged_left": False, "merged_top": False},
            ]
        ]
        result = _tables_to_markdown(tables)
        assert "line1 line2" in result
        # Cell newlines replaced with space, so the table row itself has no newline mid-cell
        table_rows = [l for l in result.splitlines() if l.startswith("|")]
        assert any("line1 line2" in row for row in table_rows)

    def test_none_cell_treated_as_empty(self):
        tables = [_simple_table_dict()]
        tables[0]["data"] = [
            [
                {"text": None, "merged_left": True, "merged_top": False},
                {"text": "val", "merged_left": False, "merged_top": False},
            ]
        ]
        result = _tables_to_markdown(tables)
        assert "|  | val |" in result

    def test_table_with_no_rows_skipped(self):
        tables = [_simple_table_dict()]
        tables[0]["data"] = []
        result = _tables_to_markdown(tables)
        assert result == ""

    def test_separator_row_matches_column_count(self):
        tables = [_simple_table_dict()]
        tables[0]["data"] = [
            [
                {"text": "A", "merged_left": False, "merged_top": False},
                {"text": "B", "merged_left": False, "merged_top": False},
                {"text": "C", "merged_left": False, "merged_top": False},
            ],
        ]
        tables[0]["cols"] = 3
        result = _tables_to_markdown(tables)
        sep_line = [l for l in result.splitlines() if "---" in l and "|" in l][0]
        assert sep_line.count("---") == 3


# ---------------------------------------------------------------------------
# dump_tables (operation function)
# ---------------------------------------------------------------------------


class TestDumpTablesOperation:
    def _make_pdf(self, num_pages=1):
        pdf = MagicMock()
        pdf.pages = [MagicMock() for _ in range(num_pages)]
        pdf.save = lambda buf: buf.write(b"%PDF-1.4 mock")
        return pdf

    @patch("pdftl.operations.dump_tables.ensure_dependencies")
    @patch("pdftl.operations.dump_tables._extract_tables")
    def test_returns_op_result(self, mock_extract, mock_ensure):
        mock_extract.return_value = [_simple_table_dict()]
        pdf = self._make_pdf()
        result = dump_tables(pdf, specs=[])
        assert result.success is True
        assert isinstance(result.data, list)

    @patch("pdftl.operations.dump_tables.ensure_dependencies")
    @patch("pdftl.operations.dump_tables._extract_tables")
    def test_fmt_stored_in_meta(self, mock_extract, mock_ensure):
        mock_extract.return_value = []
        pdf = self._make_pdf()
        result = dump_tables(pdf, specs=["csv"])
        assert result.meta["fmt"] == "csv"

    @patch("pdftl.operations.dump_tables.ensure_dependencies")
    @patch("pdftl.operations.dump_tables._extract_tables")
    def test_filters_applied(self, mock_extract, mock_ensure):
        # Return one table that fails min_rows filter
        tbl = _simple_table_dict()
        tbl["rows"] = 1
        mock_extract.return_value = [tbl]
        pdf = self._make_pdf()
        result = dump_tables(pdf, specs=["min_rows=2"])
        assert result.data == []

    @patch("pdftl.operations.dump_tables.ensure_dependencies")
    @patch("pdftl.operations.dump_tables._extract_tables")
    def test_no_empty_filter_applied(self, mock_extract, mock_ensure):
        tbl = _simple_table_dict()
        tbl["data"] = [
            [
                {"text": "", "merged_left": False, "merged_top": False},
                {"text": "", "merged_left": False, "merged_top": False},
            ],
        ]
        mock_extract.return_value = [tbl]
        pdf = self._make_pdf()
        result = dump_tables(pdf, specs=["no_empty"])
        assert result.data == []

    @patch("pdftl.operations.dump_tables.ensure_dependencies")
    @patch("pdftl.operations.dump_tables._extract_tables")
    def test_output_file_in_meta(self, mock_extract, mock_ensure):
        mock_extract.return_value = []
        pdf = self._make_pdf()
        result = dump_tables(pdf, specs=[], output_file="out.json")
        assert result.meta[c.META_OUTPUT_FILE] == "out.json"

    @patch("pdftl.operations.dump_tables.ensure_dependencies")
    @patch("pdftl.operations.dump_tables._extract_tables")
    def test_ensure_dependencies_called(self, mock_extract, mock_ensure):
        mock_extract.return_value = []
        pdf = self._make_pdf()
        dump_tables(pdf, specs=[])
        mock_ensure.assert_called_once()
        call_kwargs = mock_ensure.call_args
        assert "tablers" in str(call_kwargs)

    @patch("pdftl.operations.dump_tables.ensure_dependencies")
    @patch("pdftl.operations.dump_tables._extract_tables")
    def test_filters_passed_to_extract(self, mock_extract, mock_ensure):
        mock_extract.return_value = []
        pdf = self._make_pdf()
        dump_tables(pdf, specs=["min_rows=2", "min_cols=3"])
        call_args = mock_extract.call_args
        filters = call_args[0][2]  # third positional arg
        assert filters["min_rows"] == 2
        assert filters["min_cols"] == 3

    def test_warning_logged_on_serialise_error(self, caplog):
        import logging

        pdf = self._make_pdf()
        default_filters = {"min_rows": None, "min_cols": None, "min_area": None, "no_empty": False}

        with patch.dict("sys.modules", {"tablers": MagicMock()}):
            import tablers as mock_tablers

            mock_doc = MagicMock()
            mock_doc.__enter__ = lambda s: mock_doc
            mock_doc.__exit__ = MagicMock(return_value=False)
            mock_doc.get_page.return_value = MagicMock()
            mock_tablers.Document.return_value = mock_doc
            mock_tablers.find_tables.return_value = [MagicMock()]  # one table

            with patch(
                "pdftl.operations.dump_tables._table_to_dict", side_effect=ValueError("bad")
            ):
                with caplog.at_level(logging.WARNING, logger="pdftl.operations.dump_tables"):
                    result = _extract_tables(pdf, specs=None, filters=default_filters)

            assert result == []
            assert any("could not serialise table" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _extract_tables (integration-style, mocking tablers)
# ---------------------------------------------------------------------------


class TestExtractTables:
    def _make_pdf(self):
        pdf = MagicMock()
        pdf.pages = [MagicMock()]
        pdf.save = lambda buf: buf.write(b"%PDF-1.4 mock")
        return pdf

    def _default_filters(self):
        return {"min_rows": None, "min_cols": None, "min_area": None, "no_empty": False}

    @patch("pdftl.operations.dump_tables.page_numbers_matching_page_specs")
    def test_page_spec_passed_to_matcher(self, mock_specs):
        mock_specs.return_value = [1]
        pdf = self._make_pdf()

        mock_table = _make_tablers_table([[_make_cell("A"), _make_cell("B")]])

        with patch.dict("sys.modules", {"tablers": MagicMock()}):
            import tablers as mock_tablers

            mock_doc = MagicMock()
            mock_doc.__enter__ = lambda s: mock_doc
            mock_doc.__exit__ = MagicMock(return_value=False)
            mock_page = MagicMock()
            mock_doc.get_page.return_value = mock_page
            mock_tablers.Document.return_value = mock_doc
            mock_tablers.find_tables.return_value = [mock_table]

            _extract_tables(pdf, specs=["1"], filters=self._default_filters())
            mock_specs.assert_called_once()

    def test_warning_logged_on_extraction_error(self, caplog):
        import logging

        pdf = self._make_pdf()

        with patch.dict("sys.modules", {"tablers": MagicMock()}):
            import tablers as mock_tablers

            mock_doc = MagicMock()
            mock_doc.__enter__ = lambda s: mock_doc
            mock_doc.__exit__ = MagicMock(return_value=False)
            mock_doc.get_page.return_value = MagicMock()
            mock_tablers.Document.return_value = mock_doc
            mock_tablers.find_tables.side_effect = ValueError("bad page")

            with caplog.at_level(logging.WARNING, logger="pdftl.operations.dump_tables"):
                result = _extract_tables(pdf, specs=None, filters=self._default_filters())

            assert result == []
            assert any("table extraction failed" in r.message for r in caplog.records)

    def test_find_tables_called_with_text_use_text_flow(self):
        pdf = self._make_pdf()

        with patch.dict("sys.modules", {"tablers": MagicMock()}):
            import tablers as mock_tablers

            mock_doc = MagicMock()
            mock_doc.__enter__ = lambda s: mock_doc
            mock_doc.__exit__ = MagicMock(return_value=False)
            mock_doc.get_page.return_value = MagicMock()
            mock_tablers.Document.return_value = mock_doc
            mock_tablers.find_tables.return_value = []

            _extract_tables(pdf, specs=None, filters=self._default_filters())

            call_kwargs = mock_tablers.find_tables.call_args[1]
            assert call_kwargs.get("text_use_text_flow") is True

    def test_min_rows_min_cols_passed_to_find_tables(self):
        pdf = self._make_pdf()
        filters = {**self._default_filters(), "min_rows": 2, "min_cols": 3}

        with patch.dict("sys.modules", {"tablers": MagicMock()}):
            import tablers as mock_tablers

            mock_doc = MagicMock()
            mock_doc.__enter__ = lambda s: mock_doc
            mock_doc.__exit__ = MagicMock(return_value=False)
            mock_doc.get_page.return_value = MagicMock()
            mock_tablers.Document.return_value = mock_doc
            mock_tablers.find_tables.return_value = []

            _extract_tables(pdf, specs=None, filters=filters)

            call_kwargs = mock_tablers.find_tables.call_args[1]
            assert call_kwargs.get("min_rows") == 2
            assert call_kwargs.get("min_columns") == 3


class TestDumpTablesCliHook:
    from pdftl.operations.dump_tables import dump_tables_cli_hook

    def _make_result(self, tables, fmt="json", output_file=None):
        result = MagicMock()
        result.data = tables
        result.meta = {c.META_OUTPUT_FILE: output_file, "fmt": fmt}
        return result

    def test_json_output(self):
        result = self._make_result([_simple_table_dict()], fmt="json")
        buf = io.StringIO()
        with patch("pdftl.operations.dump_tables.smart_open_maybe_dash") as mock_open:
            mock_open.return_value.__enter__ = lambda s: buf
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            dump_tables_cli_hook(result, None, None)
        output = buf.getvalue()
        assert "tables" in json.loads(output)

    def test_csv_output(self):
        result = self._make_result([_simple_table_dict()], fmt="csv")
        buf = io.StringIO()
        with patch("pdftl.operations.dump_tables.smart_open_maybe_dash") as mock_open:
            mock_open.return_value.__enter__ = lambda s: buf
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            dump_tables_cli_hook(result, None, None)
        assert "# Page 1" in buf.getvalue()

    def test_markdown_output(self):
        result = self._make_result([_simple_table_dict()], fmt="markdown")
        buf = io.StringIO()
        with patch("pdftl.operations.dump_tables.smart_open_maybe_dash") as mock_open:
            mock_open.return_value.__enter__ = lambda s: buf
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            dump_tables_cli_hook(result, None, None)
        assert "| Name |" in buf.getvalue()
