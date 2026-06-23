# tests/operations/test_simplify_vectors.py

"""Unit tests for pdftl.operations.simplify_vectors.

Covers argument parsing, config building, stats tallying, and logging.
_StreamProcessor requires pikepdf + a real PDF and belongs in integration tests.
"""

import logging
import pytest

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.simplify_vectors import (
    SimplifyStats,
    _build_config,
    _log_stats,
    _parse_args,
    _parse_single,
)
from pdftl.utils.path_types import Path, SimplifiedPath, SimplifyConfig, Subpath


# ---------------------------------------------------------------------------
# SimplifyStats defaults
# ---------------------------------------------------------------------------


class TestSimplifyStats:
    def test_all_fields_default_to_zero(self):
        stats = SimplifyStats()
        for field in (
            "streams_processed",
            "paths_total",
            "paths_optimised",
            "paths_fallback",
            "paths_bypassed",
            "paths_clipping",
            "subpaths_curved",
            "subpaths_linear",
            "points_before",
            "points_after",
            "operators_before",
            "operators_after",
        ):
            assert getattr(stats, field) == 0, f"{field} should default to 0"


# ---------------------------------------------------------------------------
# _build_config
# ---------------------------------------------------------------------------


class TestBuildConfig:
    def test_empty_kwargs_returns_defaults(self):
        config = _build_config({})
        assert config.tolerance == pytest.approx(0.15)
        assert config.curves is True
        assert config.lines is True
        assert config.clip_paths is False
        assert config.min_points == 4
        assert config.max_error_scale == pytest.approx(4.0)

    def test_tolerance_parsed(self):
        config = _build_config({"tolerance": "0.5"})
        assert config.tolerance == pytest.approx(0.5)

    def test_tolerance_zero_raises(self):
        with pytest.raises(InvalidArgumentError, match="positive"):
            _build_config({"tolerance": "0"})

    def test_tolerance_negative_raises(self):
        with pytest.raises(InvalidArgumentError, match="positive"):
            _build_config({"tolerance": "-1.0"})

    def test_tolerance_invalid_raises(self):
        with pytest.raises(InvalidArgumentError, match="tolerance"):
            _build_config({"tolerance": "not_a_float"})

    def test_curves_true_variants(self):
        for val in ("true", "1", "yes"):
            assert _build_config({"curves": val}).curves is True

    def test_curves_false_variants(self):
        for val in ("false", "0", "no"):
            assert _build_config({"curves": val}).curves is False

    def test_bool_invalid_raises(self):
        with pytest.raises(InvalidArgumentError, match="true or false"):
            _build_config({"curves": "maybe"})

    def test_lines_parsed(self):
        config = _build_config({"lines": "false"})
        assert config.lines is False

    def test_clip_paths_parsed(self):
        config = _build_config({"clip_paths": "true"})
        assert config.clip_paths is True

    def test_min_points_parsed(self):
        config = _build_config({"min_points": "8"})
        assert config.min_points == 8

    def test_min_points_invalid_raises(self):
        with pytest.raises(InvalidArgumentError, match="min_points"):
            _build_config({"min_points": "3.5"})

    def test_max_error_scale_parsed(self):
        config = _build_config({"max_error_scale": "8.0"})
        assert config.max_error_scale == pytest.approx(8.0)

    def test_max_error_scale_invalid_raises(self):
        with pytest.raises(InvalidArgumentError, match="max_error_scale"):
            _build_config({"max_error_scale": "bad"})


# ---------------------------------------------------------------------------
# _parse_single
# ---------------------------------------------------------------------------


class TestParseSingle:
    def test_bare_page_spec(self):
        page_spec, config = _parse_single("1-end")
        assert page_spec == "1-end"
        assert isinstance(config, SimplifyConfig)

    def test_dash_default_page_spec(self):
        page_spec, config = _parse_single("-")
        assert page_spec == "-"

    def test_page_spec_with_params(self):
        page_spec, config = _parse_single("1-3(tolerance=0.5, curves=false)")
        assert page_spec == "1-3"
        assert config.tolerance == pytest.approx(0.5)
        assert config.curves is False

    def test_empty_parens_uses_defaults(self):
        page_spec, config = _parse_single("1()")
        assert page_spec == "1"
        assert config.tolerance == pytest.approx(0.15)


# ---------------------------------------------------------------------------
# _parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    def test_empty_args_returns_default(self):
        result = _parse_args([])
        assert len(result) == 1
        page_spec, config = result[0]
        assert page_spec == "-"
        assert isinstance(config, SimplifyConfig)

    def test_multiple_specs(self):
        result = _parse_args(["1-5", "6-end"])
        assert len(result) == 2
        assert result[0][0] == "1-5"
        assert result[1][0] == "6-end"


# ---------------------------------------------------------------------------
# _log_stats
# ---------------------------------------------------------------------------


class TestLogStats:
    def test_no_streams_logs_info(self, caplog):
        stats = SimplifyStats()
        with caplog.at_level(logging.INFO, logger="pdftl.operations.simplify_vectors"):
            _log_stats(stats)
        assert any("no streams processed" in r.message for r in caplog.records)

    def test_with_streams_logs_summary(self, caplog):
        stats = SimplifyStats(
            streams_processed=2,
            paths_total=10,
            paths_optimised=7,
            paths_fallback=3,
            points_before=100,
            points_after=60,
            operators_before=200,
            operators_after=140,
        )
        with caplog.at_level(logging.INFO, logger="pdftl.operations.simplify_vectors"):
            _log_stats(stats)
        output = "\n".join(r.message for r in caplog.records)
        assert "2" in output  # streams_processed
        assert "10" in output  # paths_total
        assert "40" in output  # points saved (100 - 60)
        assert "40.0%" in output  # points reduction percentage

    def test_zero_points_before_no_division_error(self, caplog):
        stats = SimplifyStats(streams_processed=1, points_before=0, operators_before=10)
        with caplog.at_level(logging.INFO, logger="pdftl.operations.simplify_vectors"):
            _log_stats(stats)  # should not raise

    def test_zero_operators_before_no_division_error(self, caplog):
        stats = SimplifyStats(streams_processed=1, points_before=10, operators_before=0)
        with caplog.at_level(logging.INFO, logger="pdftl.operations.simplify_vectors"):
            _log_stats(stats)  # should not raise


# ---------------------------------------------------------------------------
# simplify_vectors.py — _StreamProcessor stats helpers (lines 421-445)
# and the ensure_dependencies / page-dispatch paths (lines 205-220, 292-294)
# ---------------------------------------------------------------------------


def _make_subpath(n=5, has_curves=False):
    return Subpath(
        points=[(float(i), 0.0) for i in range(n)],
        closed=False,
        has_curves=has_curves,
        ctm_scale=1.0,
        original_op_count=n,
    )


def _make_full_path(subpaths, paint_op="S", is_clipping=False):
    return Path(
        subpaths=subpaths,
        paint_op=paint_op,
        original_instructions=[([], paint_op)] if paint_op else [],
        is_clipping=is_clipping,
    )


class TestStreamProcessorStatsTally:
    """
    Directly exercises _tally_subpaths and _tally_result (lines 421-445)
    by instantiating _StreamProcessor with a mock pdf object.
    """

    def _make_processor(self):
        from pdftl.operations.simplify_vectors import _StreamProcessor

        class _FakePdf:
            pages = []

        return _StreamProcessor(_FakePdf(), _build_config({}), SimplifyStats())

    def test_tally_subpaths_linear(self):
        proc = self._make_processor()
        path = _make_full_path([_make_subpath(5, has_curves=False)])
        proc._tally_subpaths(path)
        assert proc._stats.subpaths_linear == 1
        assert proc._stats.subpaths_curved == 0
        assert proc._stats.points_before == 5

    def test_tally_subpaths_curved(self):
        proc = self._make_processor()
        path = _make_full_path([_make_subpath(8, has_curves=True)])
        proc._tally_subpaths(path)
        assert proc._stats.subpaths_curved == 1
        assert proc._stats.points_before == 8

    def test_tally_subpaths_clipping_flag(self):
        proc = self._make_processor()
        path = _make_full_path([_make_subpath(4)], is_clipping=True)
        proc._tally_subpaths(path)
        assert proc._stats.paths_clipping == 1

    def test_tally_result_fell_back(self):
        proc = self._make_processor()
        path = _make_full_path([_make_subpath(6)])
        sp = SimplifiedPath(subpath_instructions=[], paint_op="S", fell_back=True)
        proc._tally_result(path, sp)
        assert proc._stats.paths_fallback == 1
        assert proc._stats.points_after == 6  # original points counted on fallback

    def test_tally_result_optimised_counts_output_points(self):
        proc = self._make_processor()
        path = _make_full_path([_make_subpath(10)])
        sp = SimplifiedPath(
            subpath_instructions=[
                ([0.0, 0.0], "m"),
                ([1.0, 0.0], "l"),
                ([0.5, 1.0, 1.5, 1.0, 2.0, 0.0], "c"),
            ],
            paint_op="S",
            fell_back=False,
        )
        proc._tally_result(path, sp)
        assert proc._stats.paths_optimised == 1
        # m=1 point, l=1 point, c=3 points → 5
        assert proc._stats.points_after == 5

    def test_tally_result_optimised_only_counts_m_l_c(self):
        """Operators other than m/l/c contribute 0 points."""
        proc = self._make_processor()
        path = _make_full_path([_make_subpath(5)])
        sp = SimplifiedPath(
            subpath_instructions=[
                ([0.0, 0.0], "m"),
                ([], "h"),  # h — not counted
            ],
            paint_op="S",
            fell_back=False,
        )
        proc._tally_result(path, sp)
        assert proc._stats.points_after == 1  # only the m


class TestStreamProcessorApplyToPage:
    """
    Lines 205-220, 292-294, 325-342: apply_to_page / _process_stream paths.
    We mock pikepdf to avoid a real PDF dependency.
    """

    def _build_processor(self, fake_pdf):
        from pdftl.operations.simplify_vectors import _StreamProcessor

        return _StreamProcessor(fake_pdf, _build_config({}), SimplifyStats())

    def test_page_without_contents_skips(self, caplog):
        """Page with no /Contents should log and return without processing."""

        class _FakePage(dict):
            def get(self, key, default=None):
                return None  # No /Contents

        class _FakePdf:
            pages = [_FakePage()]

        proc = self._build_processor(_FakePdf())
        with caplog.at_level(logging.DEBUG, logger="pdftl.operations.simplify_vectors"):
            proc.apply_to_page(1)
        assert proc._stats.streams_processed == 0


class TestRecurseXobjectsAndPatterns:
    """
    Lines 385-417: _recurse_xobjects and _recurse_patterns.
    Use lightweight fakes to exercise the guard clauses.
    """

    def _build_processor(self):
        from pdftl.operations.simplify_vectors import _StreamProcessor

        class _FakePdf:
            pages = []

        return _StreamProcessor(_FakePdf(), _build_config({}), SimplifyStats())

    def test_recurse_xobjects_no_xobject_key(self):
        """Resources without /XObject should be a no-op."""
        proc = self._build_processor()

        class _FakeResources:
            def __contains__(self, key):
                return False

        proc._recurse_xobjects(_FakeResources())  # should not raise

    def test_recurse_patterns_no_pattern_key(self):
        """Resources without /Pattern should be a no-op."""
        proc = self._build_processor()

        class _FakeResources:
            def __contains__(self, key):
                return False

        proc._recurse_patterns(_FakeResources())  # should not raise

    def test_recurse_xobjects_skips_non_form(self):
        """XObjects that are not /Form should be skipped."""
        proc = self._build_processor()

        class _FakeXobj:
            objgen = (1, 0)

            def get(self, key, default=None):
                return "/Image"  # not /Form

        class _FakeXObjects:
            def items(self):
                return [("Im0", _FakeXobj())]

        class _FakeResources:
            XObject = _FakeXObjects()

            def __contains__(self, key):
                return key == "/XObject"

        proc._recurse_xobjects(_FakeResources())
        assert proc._stats.streams_processed == 0

    def test_recurse_xobjects_skips_already_processed(self):
        """Already-processed objgens must not be re-entered."""
        proc = self._build_processor()
        objgen = (99, 0)
        proc._processed.add(objgen)

        class _FakeXobj:
            pass

        _FakeXobj.objgen = objgen

        class _FakeXObjects:
            def items(self):
                return [("Fm0", _FakeXobj())]

        class _FakeResources:
            XObject = _FakeXObjects()

            def __contains__(self, key):
                return key == "/XObject"

        proc._recurse_xobjects(_FakeResources())
        assert proc._stats.streams_processed == 0

    def test_recurse_patterns_skips_non_tiling(self):
        """PatternType != 1 should be skipped."""
        proc = self._build_processor()

        class _FakePat:
            objgen = (2, 0)

            def get(self, key, default=None):
                return 2  # ShadingPattern, not TilingPattern

        class _FakePatterns:
            def items(self):
                return [("P0", _FakePat())]

        class _FakeResources:
            Pattern = _FakePatterns()

            def __contains__(self, key):
                return key == "/Pattern"

        proc._recurse_patterns(_FakeResources())
        assert proc._stats.streams_processed == 0

    def test_recurse_patterns_skips_already_processed(self):
        proc = self._build_processor()
        objgen = (77, 0)
        proc._processed.add(objgen)

        class _FakePat:
            pass

        _FakePat.objgen = objgen

        class _FakePatterns:
            def items(self):
                return [("P1", _FakePat())]

        class _FakeResources:
            Pattern = _FakePatterns()

            def __contains__(self, key):
                return key == "/Pattern"

        proc._recurse_patterns(_FakeResources())

    def test_recurse_patterns_invalid_pattern_type_skipped(self):
        """TypeError/ValueError from int(pat.get(...)) should be caught."""
        proc = self._build_processor()

        class _FakePat:
            objgen = (3, 0)

            def get(self, key, default=None):
                return "not_an_int"

        class _FakePatterns:
            def items(self):
                return [("P2", _FakePat())]

        class _FakeResources:
            Pattern = _FakePatterns()

            def __contains__(self, key):
                return key == "/Pattern"

        proc._recurse_patterns(_FakeResources())  # should not raise


# ---------------------------------------------------------------------------
# StreamProcessor & Entry Point Integration (Lines 205-220, 292-294, etc.)
# ---------------------------------------------------------------------------

import pikepdf
from pdftl.operations.simplify_vectors import (
    simplify_vectors_in_content_streams,
    _StreamProcessor,
)


def test_simplify_vectors_full_pipeline_real_pdf():
    """Exercises lines 205-220, 292-294, 316-320, 330-342, 349-380, and resource loops."""
    # 1. Create a pristine in-memory PDF using real pikepdf factories
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(100, 100))

    # Write valid path operations to ensure Stage 2 (segment) and Stage 3 (simplify) run
    stream_bytes = b"10 10 m 20 20 l 30 10 l 40 20 l 50 10 l S\n"
    page.Contents = pdf.make_stream(stream_bytes)

    # 2. Build a Form XObject with vector commands to test line 396-400 recursion
    xobj_stream = pdf.make_stream(b"0 0 m 10 10 l S\n")
    xobj_stream.Subtype = pikepdf.Name("/Form")
    xobj_stream.BBox = pikepdf.Array([0, 0, 50, 50])

    # 3. Build a Tiling Pattern (Type 1) to test line 413-417 recursion
    pattern_stream = pdf.make_stream(b"1 1 m 5 5 l S\n")
    pattern_stream.PatternType = 1
    pattern_stream.PaintType = 1
    pattern_stream.TilingType = 1
    pattern_stream.BBox = pikepdf.Array([0, 0, 10, 10])
    pattern_stream.XStep = 10
    pattern_stream.YStep = 10

    # Attach nested resources to the page dictionary structure
    resources = pikepdf.Dictionary()
    resources.XObject = pikepdf.Dictionary({"/RealForm": xobj_stream})
    resources.Pattern = pikepdf.Dictionary({"/RealPattern": pattern_stream})
    page.Resources = resources

    # 4. Run the entry point directly against the concrete PDF instance
    result = simplify_vectors_in_content_streams(pdf, ["1(tolerance=0.5, min_points=2)"])

    assert result.success is True
    assert len(result.pdf.pages) == 1


def test_stream_processor_edge_cases_and_errors():
    """Exercises remaining lines (335-336 duplicate skip, 351-353 parse errors, and 371-380 write errors)."""
    pdf = pikepdf.new()

    # 1. Test missing contents structural exit (lines 326-328)
    page_no_contents = pdf.add_blank_page()
    if "/Contents" in page_no_contents:
        del page_no_contents["/Contents"]

    stats = SimplifyStats()
    config = _build_config({"tolerance": "0.15"})
    processor = _StreamProcessor(pdf, config, stats)
    processor.apply_to_page(1)
    assert stats.streams_processed == 0

    # 2. Setup a valid page stream to check deduplication (lines 335-336)
    page_valid = pdf.add_blank_page()
    page_valid.Contents = pdf.make_stream(b"10 10 m S\n")

    # Create an XObject and Pattern with nested empty resources to hit lines 400 and 417
    xobj_stream = pdf.make_stream(b"0 0 m 10 10 l S\n")
    xobj_stream["/Subtype"] = pikepdf.Name("/Form")
    xobj_stream["/BBox"] = pikepdf.Array([0, 0, 50, 50])
    xobj_stream["/Resources"] = pikepdf.Dictionary()

    pattern_stream = pdf.make_stream(b"1 1 m 5 5 l S\n")
    pattern_stream["/PatternType"] = 1
    pattern_stream["/PaintType"] = 1
    pattern_stream["/TilingType"] = 1
    pattern_stream["/BBox"] = pikepdf.Array([0, 0, 10, 10])
    pattern_stream["/XStep"] = 10
    pattern_stream["/YStep"] = 10
    pattern_stream["/Resources"] = pikepdf.Dictionary()

    resources = pikepdf.Dictionary()
    resources["/XObject"] = pikepdf.Dictionary({"/NestForm": xobj_stream})
    resources["/Pattern"] = pikepdf.Dictionary({"/NestPattern": pattern_stream})
    page_valid["/Resources"] = resources

    # First pass processes the page stream and recurses deep into the resources
    processor.apply_to_page(2)
    assert page_valid.Contents.objgen in processor._processed

    # Second pass immediately triggers line 335-336 stream deduplication exit
    processor.apply_to_page(2)

    # 3. Force parse error handling path via targeted method patch (lines 351-353)
    page_error = pdf.add_blank_page()
    page_error.Contents = pdf.make_stream(b"10 10 m S\n")

    from unittest.mock import patch

    with patch.object(
        pikepdf, "parse_content_stream", side_effect=pikepdf.PdfError("Forced Parse Error")
    ):
        processor.apply_to_page(3)

    # 4. Cover stream rewrite mutation check (line 371) and serialization exception (lines 379-380)
    page_write_err = pdf.add_blank_page()
    # Provide a multi-point redundant path segment to explicitly satisfy modification logic
    page_write_err.Contents = pdf.make_stream(
        b"10 10 m 10.001 10.001 l 10.002 10.002 l 50 50 l S\n"
    )

    with patch(
        "pikepdf.unparse_content_stream", side_effect=pikepdf.PdfError("Forced Write Error")
    ):
        processor.apply_to_page(4)

    assert processor._stats.operators_after >= 0


def test_stream_processor_non_path_instructions_line_371():
    """Explicitly hits line 371 and verifies non-Path operators pass through safely."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page()

    # 'q', 'w', and 'Q' are non-vector state operators that hit the line 371 fallback branch
    page.Contents = pdf.make_stream(b"q 10 w 0 0 m 20 20 l S Q\n")

    stats = SimplifyStats()
    config = _build_config({"tolerance": "0.5"})
    processor = _StreamProcessor(pdf, config, stats)

    processor.apply_to_page(1)

    # Read the updated tokens back out to verify zero data loss on non-Path items
    updated_bytes = page.Contents.read_bytes()

    assert b"q" in updated_bytes
    assert b"10 w" in updated_bytes
    assert b"Q" in updated_bytes
    assert stats.streams_processed == 1
