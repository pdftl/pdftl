# tests/operations/test_grep.py
"""
Tests for src/pdftl/operations/grep.py.

All PDFium I/O and the TextProvider are mocked — no real PDF is needed.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

# codeql[py/import-and-import-from]
import pdftl.operations.grep as grep_mod
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.grep import (
    _build_hit,
    _build_line_map,
    _compile_regex,
    _evaluate_fonts,
    _extract_captures,
    _get_line_bounds,
    _parse_args,
    _parse_bool,
    _parse_non_negative_int,
    _parse_positive_int,
    _passes_font_filters,
    _write_json_output,
    grep_cli_hook,
)

# ===========================================================================
# _parse_bool
# ===========================================================================


class TestParseBool:
    def test_true_values(self):
        for v in ("true", "1", "yes"):
            assert _parse_bool({"k": v}, "k", False) is True

    def test_false_value(self):
        assert _parse_bool({"k": "false"}, "k", True) is False

    def test_missing_key_returns_default(self):
        assert _parse_bool({}, "k", True) is True

    def test_alias_used_when_key_missing(self):
        assert _parse_bool({"i": "true"}, "ignore_case", False, alias="i") is True

    def test_alias_not_used_when_key_present(self):
        assert _parse_bool({"k": "false", "a": "true"}, "k", True, alias="a") is False

    def test_alias_none_no_error(self):
        assert _parse_bool({}, "k", False, alias=None) is False


# ===========================================================================
# _parse_positive_int
# ===========================================================================


class TestParsePositiveInt:
    def test_missing_key_returns_none(self):
        assert _parse_positive_int({}, "n") is None

    def test_valid_positive(self):
        assert _parse_positive_int({"n": "5"}, "n") == 5

    def test_zero_raises(self):
        with pytest.raises(InvalidArgumentError, match="positive integer"):
            _parse_positive_int({"n": "0"}, "n")

    def test_negative_raises(self):
        with pytest.raises(InvalidArgumentError):
            _parse_positive_int({"n": "-1"}, "n")

    def test_non_integer_raises(self):
        with pytest.raises(InvalidArgumentError):
            _parse_positive_int({"n": "abc"}, "n")


# ===========================================================================
# _parse_non_negative_int
# ===========================================================================


class TestParseNonNegativeInt:
    def test_missing_returns_default(self):
        assert _parse_non_negative_int({}, "n", 3) == 3

    def test_zero_is_valid(self):
        assert _parse_non_negative_int({"n": "0"}, "n", 1) == 0

    def test_positive_valid(self):
        assert _parse_non_negative_int({"n": "4"}, "n", 0) == 4

    def test_negative_raises(self):
        with pytest.raises(InvalidArgumentError, match="non-negative integer"):
            _parse_non_negative_int({"n": "-1"}, "n", 0)

    def test_non_integer_raises(self):
        with pytest.raises(InvalidArgumentError):
            _parse_non_negative_int({"n": "x"}, "n", 0)


# ===========================================================================
# _parse_args
# ===========================================================================


class TestParseArgs:
    def _call(self, args):
        return _parse_args(args)

    def test_missing_pattern_raises(self):
        with pytest.raises(InvalidArgumentError, match="Missing search pattern"):
            _parse_args([])

    def test_minimal_pattern(self):
        result = _parse_args(["hello"])
        (
            pattern,
            page_specs,
            use_regex,
            ignore_case,
            max_count,
            before_context,
            after_context,
            multiline,
            dotall,
            extract_fonts,
            kv,
        ) = result
        assert pattern == "hello"
        assert page_specs == []
        assert use_regex is True
        assert ignore_case is False
        assert max_count is None
        assert before_context == 0
        assert after_context == 0
        assert multiline is True
        assert dotall is False
        assert extract_fonts is False

    def test_all_flags_explicit(self):
        args = [
            "pat",
            "regex=false",
            "ignore_case=true",
            "multiline=false",
            "dotall=true",
            "max_count=10",
            "context=2",
            "fonts=true",
        ]
        result = _parse_args(args)
        (
            pattern,
            page_specs,
            use_regex,
            ignore_case,
            max_count,
            before_context,
            after_context,
            multiline,
            dotall,
            extract_fonts,
            kv,
        ) = result
        assert use_regex is False
        assert ignore_case is True
        assert multiline is False
        assert dotall is True
        assert max_count == 10
        assert before_context == 2
        assert after_context == 2
        assert extract_fonts is True

    def test_before_after_context_override_base(self):
        result = _parse_args(["pat", "context=3", "before_context=1", "after_context=2"])
        *_, before_context, after_context, _m, _d, _f, _kv = result
        assert before_context == 1
        assert after_context == 2

    def test_alias_i_for_ignore_case(self):
        result = _parse_args(["pat", "i=true"])
        *_, ignore_case, _mc, _bc, _ac, _m, _d, _f, _kv = result[2:]
        assert ignore_case is True

    def test_alias_m_for_multiline(self):
        result = _parse_args(["pat", "m=false"])
        (
            pattern,
            page_specs,
            use_regex,
            ignore_case,
            max_count,
            before_context,
            after_context,
            multiline,
            dotall,
            extract_fonts,
            kv,
        ) = result
        assert multiline is False

    def test_alias_s_for_dotall(self):
        result = _parse_args(["pat", "s=true"])
        *_, dotall, extract_fonts, kv = result
        assert dotall is True

    def test_font_filters_auto_enable_fonts(self):
        result = _parse_args(["pat", "min_size=10"])
        *_, extract_fonts, kv = result
        assert extract_fonts is True

    def test_page_specs_passed_through(self):
        result = _parse_args(["pat", "1-3"])
        _, page_specs, *_ = result
        assert page_specs == ["1-3"]


# ===========================================================================
# _compile_regex
# ===========================================================================


class TestCompileRegex:
    def test_literal_escaped(self):
        rx = _compile_regex(
            "a.b", use_regex=False, ignore_case=False, multiline=False, dotall=False
        )
        assert rx.search("a.b") is not None
        assert rx.search("axb") is None

    def test_regex_pattern(self):
        rx = _compile_regex(
            r"\d+", use_regex=True, ignore_case=False, multiline=False, dotall=False
        )
        assert rx.search("abc123") is not None

    def test_ignore_case_flag(self):
        rx = _compile_regex(
            "hello", use_regex=True, ignore_case=True, multiline=False, dotall=False
        )
        assert rx.search("HELLO") is not None

    def test_multiline_flag(self):
        rx = _compile_regex("^b", use_regex=True, ignore_case=False, multiline=True, dotall=False)
        assert rx.search("a\nb") is not None

    def test_dotall_flag(self):
        rx = _compile_regex("a.b", use_regex=True, ignore_case=False, multiline=False, dotall=True)
        assert rx.search("a\nb") is not None

    def test_invalid_regex_raises(self):
        with pytest.raises(InvalidArgumentError, match="Invalid regular expression"):
            _compile_regex(
                "[invalid", use_regex=True, ignore_case=False, multiline=False, dotall=False
            )


# ===========================================================================
# _build_line_map
# ===========================================================================


class TestBuildLineMap:
    def test_single_line(self):
        lines, starts = _build_line_map("hello")
        assert lines == ["hello"]
        assert starts == [0]

    def test_two_lines(self):
        lines, starts = _build_line_map("foo\nbar")
        assert lines == ["foo", "bar"]
        assert starts == [0, 4]

    def test_trailing_newline(self):
        lines, starts = _build_line_map("a\n")
        assert lines == ["a", ""]
        assert starts == [0, 2]

    def test_empty_string(self):
        lines, starts = _build_line_map("")
        assert lines == [""]
        assert starts == [0]


# ===========================================================================
# _passes_font_filters
# ===========================================================================


class TestPassesFontFilters:
    def _fi(self, size=12.0, name="Arial", bold=False, italic=False):
        return {"font_size": size, "font_name": name, "bold": bold, "italic": italic}

    def test_passes_all(self):
        assert _passes_font_filters(self._fi(), {}) is True

    def test_min_size_fail(self):
        assert _passes_font_filters(self._fi(size=8.0), {"min_size": "10"}) is False

    def test_min_size_pass(self):
        assert _passes_font_filters(self._fi(size=12.0), {"min_size": "10"}) is True

    def test_max_size_fail(self):
        assert _passes_font_filters(self._fi(size=20.0), {"max_size": "14"}) is False

    def test_max_size_pass(self):
        assert _passes_font_filters(self._fi(size=12.0), {"max_size": "14"}) is True

    def test_font_match_fail(self):
        assert _passes_font_filters(self._fi(name="Arial"), {"font_match": "Bold"}) is False

    def test_font_match_pass_case_insensitive(self):
        assert _passes_font_filters(self._fi(name="ArialBold"), {"font_match": "bold"}) is True

    def test_require_bold_fail(self):
        assert _passes_font_filters(self._fi(bold=False), {"require_bold": "true"}) is False

    def test_require_bold_pass(self):
        assert _passes_font_filters(self._fi(bold=True), {"require_bold": "true"}) is True

    def test_require_italic_fail(self):
        assert _passes_font_filters(self._fi(italic=False), {"require_italic": "true"}) is False

    def test_require_italic_pass(self):
        assert _passes_font_filters(self._fi(italic=True), {"require_italic": "true"}) is True


# ===========================================================================
# _evaluate_fonts
# ===========================================================================


class TestEvaluateFonts:
    def _tp(self, font_info):
        tp = MagicMock()
        tp.get_font_info.return_value = font_info
        return tp

    def test_no_filters_no_extract_returns_true_none(self):
        tp = self._tp({})
        ok, fi = _evaluate_fonts(tp, 0, 0, 5, extract_fonts=False, kv={})
        assert ok is True
        assert fi is None

    def test_extract_fonts_true_returns_font_info(self):
        fi = {"font_size": 12.0, "font_name": "A", "bold": False, "italic": False}
        tp = self._tp(fi)
        ok, result = _evaluate_fonts(tp, 0, 0, 5, extract_fonts=True, kv={})
        assert ok is True
        assert result is fi

    def test_filter_fails_returns_false_none(self):
        fi = {"font_size": 8.0, "font_name": "A", "bold": False, "italic": False}
        tp = self._tp(fi)
        ok, result = _evaluate_fonts(tp, 0, 0, 5, extract_fonts=False, kv={"min_size": "10"})
        assert ok is False
        assert result is None

    def test_filter_passes_returns_true_with_info(self):
        fi = {"font_size": 14.0, "font_name": "A", "bold": False, "italic": False}
        tp = self._tp(fi)
        ok, result = _evaluate_fonts(tp, 0, 0, 5, extract_fonts=False, kv={"min_size": "10"})
        assert ok is True
        assert result is fi


# ===========================================================================
# _get_line_bounds
# ===========================================================================


class TestGetLineBounds:
    def _starts(self):
        # "foo\nbar\nbaz"
        # line 0 starts at 0, line 1 at 4, line 2 at 8
        return [0, 4, 8]

    def test_first_line(self):
        assert _get_line_bounds(0, 3, self._starts(), 3) == (0, 0)

    def test_second_line(self):
        assert _get_line_bounds(4, 7, self._starts(), 3) == (1, 1)

    def test_cross_line_match(self):
        start, end = _get_line_bounds(2, 6, self._starts(), 3)
        assert start == 0
        assert end == 1

    def test_empty_match_same_line(self):
        start, end = _get_line_bounds(4, 4, self._starts(), 3)
        assert start == end

    def test_clamps_to_num_lines_minus_one(self):
        start, end = _get_line_bounds(0, 100, self._starts(), 3)
        assert end == 2


# ===========================================================================
# _extract_captures
# ===========================================================================


class TestExtractCaptures:
    def _tp_with_boxes(self, boxes):
        tp = MagicMock()
        tp.get_bboxes_for_lines.return_value = boxes
        return tp

    def test_no_groups_returns_empty(self):
        rx = re.compile(r"hello")
        m = rx.search("hello world")
        result = _extract_captures(m, 0, MagicMock())
        assert result == []

    def test_one_group_captured(self):
        rx = re.compile(r"(\d+)")
        m = rx.search("abc 42 def")
        tp = self._tp_with_boxes([[0, 0, 5, 10]])
        captures = _extract_captures(m, 0, tp)
        assert len(captures) == 1
        assert captures[0]["group"] == 1
        assert captures[0]["text"] == "42"

    def test_empty_group_skipped(self):
        # Optional group that didn't participate
        rx = re.compile(r"(\d+)?x")
        m = rx.search("x")
        tp = self._tp_with_boxes([])
        captures = _extract_captures(m, 0, tp)
        assert captures == []

    def test_index_error_on_group_is_skipped(self):
        # re.Pattern.groups is a readonly C attribute so we can't patch it.
        # Instead build a fake match whose .re.groups claims 2 groups but
        # whose .start()/.end()/.group() delegate to a real 1-group match,
        # raising IndexError when i=2 is requested.
        real = re.compile(r"(\d+)").search("42")

        fake_re = MagicMock()
        fake_re.groups = 2

        fake_match = MagicMock()
        fake_match.re = fake_re
        fake_match.start.side_effect = lambda i: real.start(i)
        fake_match.end.side_effect = lambda i: real.end(i)
        fake_match.group.side_effect = lambda i: real.group(i)

        tp = self._tp_with_boxes([[0, 0, 5, 5]])
        captures = _extract_captures(fake_match, 0, tp)
        # group 1 captured fine; group 2 raised IndexError and was skipped
        assert len(captures) == 1
        assert captures[0]["group"] == 1


# ===========================================================================
# _build_hit
# ===========================================================================


def _make_tp_for_hit(bboxes=None, font_info=None):
    tp = MagicMock()
    tp.get_bboxes_for_lines.return_value = bboxes or [[0, 0, 10, 10]]
    tp.get_font_info.return_value = font_info or {}
    return tp


class TestBuildHit:
    def _match(self, pattern, text, flags=0):
        return re.compile(pattern, flags).search(text)

    def test_zero_length_match_returns_none(self):
        text = "hello"
        lines, line_starts = _build_line_map(text)
        rx = re.compile(r"(?=l)")  # zero-width lookahead
        m = rx.search(text)
        tp = _make_tp_for_hit()
        result = _build_hit(1, 0, m, lines, line_starts, 0, 0, tp, False, {})
        assert result is None

    def test_font_filter_fails_returns_none(self):
        text = "hello world"
        lines, line_starts = _build_line_map(text)
        m = re.compile(r"hello").search(text)
        fi = {"font_size": 6.0, "font_name": "X", "bold": False, "italic": False}
        tp = _make_tp_for_hit(font_info=fi)
        result = _build_hit(1, 0, m, lines, line_starts, 0, 0, tp, False, {"min_size": "10"})
        assert result is None

    def test_basic_hit_structure(self):
        text = "find me here"
        lines, line_starts = _build_line_map(text)
        m = re.compile(r"me").search(text)
        tp = _make_tp_for_hit()
        hit = _build_hit(1, 0, m, lines, line_starts, 0, 0, tp, False, {})
        assert hit is not None
        assert hit["page"] == 1
        assert hit["line"] == 1
        assert hit["text"] == "me"
        assert "bboxes" in hit
        assert hit["context_match"] == "find me here"
        assert hit["match_start_idx"] == text.index("me")
        assert hit["match_end_idx"] == text.index("me") + 2

    def test_before_context_included(self):
        text = "line1\nline2\nline3"
        lines, line_starts = _build_line_map(text)
        m = re.compile(r"line3").search(text)
        tp = _make_tp_for_hit()
        hit = _build_hit(1, 0, m, lines, line_starts, 2, 0, tp, False, {})
        assert "context_before" in hit
        assert hit["context_before"] == ["line1", "line2"]

    def test_after_context_included(self):
        text = "line1\nline2\nline3"
        lines, line_starts = _build_line_map(text)
        m = re.compile(r"line1").search(text)
        tp = _make_tp_for_hit()
        hit = _build_hit(1, 0, m, lines, line_starts, 0, 2, tp, False, {})
        assert "context_after" in hit
        assert hit["context_after"] == ["line2", "line3"]

    def test_no_context_keys_absent(self):
        text = "hello"
        lines, line_starts = _build_line_map(text)
        m = re.compile(r"hello").search(text)
        tp = _make_tp_for_hit()
        hit = _build_hit(1, 0, m, lines, line_starts, 0, 0, tp, False, {})
        assert "context_before" not in hit
        assert "context_after" not in hit

    def test_captures_included_when_groups(self):
        text = "Invoice: 1234"
        lines, line_starts = _build_line_map(text)
        m = re.compile(r"Invoice:\s*(\d+)").search(text)
        tp = _make_tp_for_hit()
        hit = _build_hit(1, 0, m, lines, line_starts, 0, 0, tp, False, {})
        assert "captures" in hit
        assert hit["captures"][0]["text"] == "1234"

    def test_font_info_merged_into_hit_when_extract_fonts(self):
        text = "heading"
        lines, line_starts = _build_line_map(text)
        m = re.compile(r"heading").search(text)
        fi = {"font_size": 18.0, "font_name": "Bold", "bold": True, "italic": False, "weight": 700}
        tp = _make_tp_for_hit(font_info=fi)
        hit = _build_hit(1, 0, m, lines, line_starts, 0, 0, tp, True, {})
        assert hit["font_size"] == 18.0
        assert hit["bold"] is True

    def test_multiline_match_spans_two_lines(self):
        text = "foo\nbar"
        lines, line_starts = _build_line_map(text)
        m = re.compile(r"foo\nbar", re.DOTALL).search(text)
        tp = _make_tp_for_hit()
        hit = _build_hit(1, 0, m, lines, line_starts, 0, 0, tp, False, {})
        assert hit is not None
        assert hit["context_match"] == "foo\nbar"


# ===========================================================================
# _write_json_output
# ===========================================================================


class TestWriteJsonOutput:
    @patch("pdftl.operations.grep.dump")
    @patch("pdftl.operations.grep.compact_json_string", side_effect=lambda s, **kw: s)
    def test_calls_dump(self, mock_compact, mock_dump):
        _write_json_output({"hits": []}, "out.json")
        mock_dump.assert_called_once()
        args = mock_dump.call_args[0]
        assert "hits" in args[0]


# ===========================================================================
# grep_cli_hook
# ===========================================================================


class TestGrepCliHook:
    @patch("pdftl.operations.grep._write_json_output")
    @patch("pdftl.operations.grep.dump")
    def test_calls_write_json_output(self, mock_dump, mock_write):
        import pdftl.core.constants as c
        from pdftl.core.core_types import OpResult

        result = OpResult(success=True, data={"hits": []}, meta={c.META_OUTPUT_FILE: "out.json"})
        stage = MagicMock()
        pipeline = MagicMock()

        # from_result_meta is imported lazily inside grep_cli_hook; patch at source
        with patch("pdftl.utils.hooks.from_result_meta", return_value="out.json"):
            grep_cli_hook(result, stage, pipeline)
            mock_write.assert_called_once_with({"hits": []}, "out.json")


# ===========================================================================
# grep() integration — full operation with mocked TextProvider + PDFium
# ===========================================================================


class TestGrepOperation:
    """
    Tests for the main grep() function. PDFium and TextProvider are fully mocked.
    """

    def _make_pdf(self, num_pages=1):
        pdf = MagicMock()
        pdf.pages = [MagicMock()] * num_pages
        return pdf

    def _patch_tp(self, text_by_page: dict[int, str]):
        """
        Patch TextProvider at its source module (it's a lazy import inside grep()).
        Returns (context_manager, tp_instance).
        """
        tp_instance = MagicMock()

        def get_text(page_num):
            return text_by_page.get(page_num, "")

        tp_instance.get_text.side_effect = get_text
        tp_instance.get_bboxes_for_lines.return_value = [[0.0, 0.0, 10.0, 10.0]]
        tp_instance.get_font_info.return_value = {
            "font_size": 12.0,
            "font_name": "Arial",
            "bold": False,
            "italic": False,
            "weight": 400,
        }

        # TextProvider is imported inside grep() as:
        #   from pdftl.utils.pdf_text.text_provider import TextProvider
        # so we patch it at the source, not on the grep module.
        ctx = patch("pdftl.utils.pdf_text.text_provider.TextProvider", return_value=tp_instance)
        return ctx, tp_instance

    def _call_grep(self, pdf, args, text_by_page):
        ctx, tp_instance = self._patch_tp(text_by_page)
        with (
            ctx,
            patch.object(grep_mod, "ensure_dependencies"),
            patch("pypdfium2.PdfDocument") as mock_pdfium_doc,
        ):
            mock_pdfium_doc.return_value = MagicMock()
            result = grep_mod.grep(pdf, args)
        return result, tp_instance

    def test_simple_match(self):
        pdf = self._make_pdf()
        result, _ = self._call_grep(pdf, ["hello"], {0: "say hello world"})
        assert result.success is True
        assert result.data["count"]["total"] == 1
        assert result.data["hits"][0]["text"] == "hello"

    def test_no_match(self):
        pdf = self._make_pdf()
        result, _ = self._call_grep(pdf, ["xyz"], {0: "nothing here"})
        assert result.data["count"]["total"] == 0
        assert result.data["hits"] == []

    def test_max_count_respected(self):
        pdf = self._make_pdf()
        result, _ = self._call_grep(pdf, ["a", "max_count=2"], {0: "a a a a a"})
        assert result.data["count"]["total"] == 2

    def test_multiple_pages(self):
        pdf = self._make_pdf(num_pages=2)
        result, _ = self._call_grep(pdf, ["hit"], {0: "hit on page 1", 1: "hit on page 2"})
        assert result.data["count"]["total"] == 2

    def test_page_spec_limits_search(self):
        pdf = self._make_pdf(num_pages=3)
        ctx, tp = self._patch_tp({0: "match", 1: "match", 2: "match"})

        with (
            ctx,
            patch.object(grep_mod, "ensure_dependencies"),
            patch("pypdfium2.PdfDocument") as mock_pdfium_doc,
            patch.object(grep_mod, "page_numbers_matching_page_specs", return_value={1}),
        ):
            mock_pdfium_doc.return_value = MagicMock()
            result = grep_mod.grep(pdf, ["match", "1"])

        assert result.data["count"]["total"] == 1

    def test_regex_false_treats_as_literal(self):
        pdf = self._make_pdf()
        result, _ = self._call_grep(pdf, ["a.b", "regex=false"], {0: "a.b"})
        assert result.data["count"]["total"] == 1

    def test_ignore_case(self):
        pdf = self._make_pdf()
        result, _ = self._call_grep(pdf, ["HELLO", "ignore_case=true"], {0: "hello"})
        assert result.data["count"]["total"] == 1

    def test_output_data_structure(self):
        pdf = self._make_pdf()
        result, _ = self._call_grep(pdf, ["x"], {0: "no match"})
        data = result.data
        assert "query" in data
        assert "is_regex" in data
        assert "ignore_case" in data
        assert "count" in data
        assert "hits" in data
        assert data["query"] == "x"

    def test_stop_searching_across_pages(self):
        """max_count=1 should stop after first page once limit is hit."""
        pdf = self._make_pdf(num_pages=2)
        result, tp = self._call_grep(pdf, ["x", "max_count=1"], {0: "x x x", 1: "x x x"})
        assert result.data["count"]["total"] == 1

    def test_by_page_count_reported(self):
        pdf = self._make_pdf(num_pages=2)
        result, _ = self._call_grep(pdf, ["hit"], {0: "hit hit", 1: "hit"})
        by_page = dict(result.data["count"]["by_page"])
        assert by_page[1] == 2
        assert by_page[2] == 1

    def test_none_hit_skipped_in_loop(self):
        # grep() must skip matches where _build_hit returns None (line 333).
        # Patch _build_hit to always return None so every match is discarded.
        pdf = self._make_pdf()
        ctx, tp_instance = self._patch_tp({0: "hello"})
        with (
            ctx,
            patch.object(grep_mod, "ensure_dependencies"),
            patch("pypdfium2.PdfDocument") as mock_doc,
            patch.object(grep_mod, "_build_hit", return_value=None),
        ):
            mock_doc.return_value = MagicMock()
            result = grep_mod.grep(pdf, ["hello"])
        assert result.data["count"]["total"] == 0

    def test_invalid_pattern_raises(self):
        pdf = self._make_pdf()
        with (
            patch.object(grep_mod, "ensure_dependencies"),
            patch("pypdfium2.PdfDocument") as mock_pdfium_doc,
        ):
            mock_pdfium_doc.return_value = MagicMock()
            with pytest.raises(InvalidArgumentError, match="Invalid regular expression"):
                grep_mod.grep(pdf, ["[bad"])
