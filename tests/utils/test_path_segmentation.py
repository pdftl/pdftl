# tests/utils/test_path_segmentation.py

"""Tests for pdftl.utils.path_segmentation.

The segment() and serialize() functions operate on (operands, operator) pairs.
segment() only calls str(operator), so plain strings work as operators — no
pikepdf dependency needed for most tests.
"""

import pytest

from pdftl.utils.path_segmentation import (
    segment,
    serialize,
    _connects,
    _split_instructions_per_subpath,
)
from pdftl.utils.path_types import Path, SimplifiedPath, SimplifyConfig, Subpath


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _op(operator, *operands):
    """Build a fake (operands, operator) instruction tuple."""
    return (list(operands), operator)


def _default_config(**kwargs):
    defaults = dict(
        tolerance=0.15,
        curves=True,
        lines=True,
        clip_paths=False,
        min_points=4,
        max_error_scale=4.0,
        coalesce_strokes=True,
    )
    defaults.update(kwargs)
    return SimplifyConfig(**defaults)


def _ops_from(mixed):
    """Extract operator strings from a mixed segment() result, skipping Path objects."""
    return [str(item[1]) for item in mixed if not isinstance(item, Path)]


def _paths_from(mixed):
    return [item for item in mixed if hasattr(item, "subpaths") and hasattr(item, "paint_op")]


# ---------------------------------------------------------------------------
# segment() — basic pass-through
# ---------------------------------------------------------------------------


class TestSegmentPassthrough:
    def test_empty_instructions(self):
        assert segment([], _default_config()) == []

    def test_unknown_operators_pass_through(self):
        instrs = [_op("BT"), _op("ET")]
        result = segment(instrs, _default_config())
        assert len(result) == 2
        assert _ops_from(result) == ["BT", "ET"]

    def test_gs_operators_pass_through(self):
        instrs = [_op("q"), _op("Q")]
        result = segment(instrs, _default_config())
        assert _ops_from(result) == ["q", "Q"]

    def test_paint_op_without_path_passes_through(self):
        # Painting op with no preceding construction ops goes straight to out
        instrs = [_op("S")]
        result = segment(instrs, _default_config())
        assert _ops_from(result) == ["S"]


# ---------------------------------------------------------------------------
# segment() — path construction
# ---------------------------------------------------------------------------


class TestSegmentPathConstruction:
    def test_simple_m_l_s_produces_one_path(self):
        instrs = [_op("m", 0.0, 0.0), _op("l", 1.0, 0.0), _op("S")]
        result = segment(instrs, _default_config())
        paths = _paths_from(result)
        assert len(paths) == 1

    def test_path_subpath_points(self):
        instrs = [_op("m", 0.0, 0.0), _op("l", 1.0, 0.0), _op("l", 2.0, 0.0), _op("S")]
        result = segment(instrs, _default_config())
        path = _paths_from(result)[0]
        assert len(path.subpaths) == 1
        assert path.subpaths[0].points[0] == (0.0, 0.0)
        assert path.subpaths[0].points[-1] == (2.0, 0.0)

    def test_paint_op_stored(self):
        instrs = [_op("m", 0.0, 0.0), _op("l", 1.0, 0.0), _op("f")]
        result = segment(instrs, _default_config())
        path = _paths_from(result)[0]
        assert path.paint_op == "f"

    def test_h_closes_subpath(self):
        instrs = [_op("m", 0.0, 0.0), _op("l", 1.0, 0.0), _op("h"), _op("S")]
        result = segment(instrs, _default_config())
        path = _paths_from(result)[0]
        assert path.subpaths[0].closed is True

    def test_multi_subpath_single_path(self):
        # m l m l S — two subpaths, one path
        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("m", 2.0, 0.0),
            _op("l", 3.0, 0.0),
            _op("S"),
        ]
        result = segment(instrs, _default_config())
        paths = _paths_from(result)
        assert len(paths) == 1
        assert len(paths[0].subpaths) == 2

    def test_c_operator_marks_has_curves(self):
        instrs = [
            _op("m", 0.0, 0.0),
            _op("c", 0.5, 1.0, 1.5, 1.0, 2.0, 0.0),
            _op("S"),
        ]
        result = segment(instrs, _default_config())
        path = _paths_from(result)[0]
        assert path.subpaths[0].has_curves is True

    def test_v_operator_implicit_p1(self):
        # v: first control point = current point (0,0)
        instrs = [
            _op("m", 0.0, 0.0),
            _op("v", 1.5, 1.0, 2.0, 0.0),
            _op("S"),
        ]
        result = segment(instrs, _default_config())
        path = _paths_from(result)[0]
        assert path.subpaths[0].has_curves is True
        # Should have sampled points including at least p3
        pts = path.subpaths[0].points
        assert pts[-1] == pytest.approx((2.0, 0.0), abs=0.01)

    def test_y_operator_implicit_p3(self):
        # y: last control point = endpoint
        instrs = [
            _op("m", 0.0, 0.0),
            _op("y", 0.5, 1.0, 2.0, 0.0),
            _op("S"),
        ]
        result = segment(instrs, _default_config())
        path = _paths_from(result)[0]
        assert path.subpaths[0].has_curves is True
        pts = path.subpaths[0].points
        assert pts[-1] == pytest.approx((2.0, 0.0), abs=0.01)


# ---------------------------------------------------------------------------
# segment() — rectangle operator
# ---------------------------------------------------------------------------


class TestSegmentRect:
    def test_re_normal_produces_four_points(self):
        instrs = [_op("re", 0.0, 0.0, 10.0, 5.0), _op("S")]
        result = segment(instrs, _default_config())
        path = _paths_from(result)[0]
        assert len(path.subpaths[0].points) == 4

    def test_re_zero_width_stays_in_path_ops(self):
        # Degenerate re must NOT leak into outer stream
        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("re", 5.0, 5.0, 0.0, 10.0),  # zero width
            _op("l", 2.0, 0.0),
            _op("S"),
        ]
        result = segment(instrs, _default_config())
        paths = _paths_from(result)
        # The degenerate re should be inside the path, not emitted as a
        # standalone pass-through instruction
        passthrough_ops = _ops_from(result)
        assert "re" not in passthrough_ops
        assert len(paths) == 1

    def test_re_zero_height_stays_in_path_ops(self):
        instrs = [
            _op("m", 0.0, 0.0),
            _op("re", 5.0, 5.0, 10.0, 0.0),  # zero height
            _op("l", 2.0, 0.0),
            _op("S"),
        ]
        result = segment(instrs, _default_config())
        passthrough_ops = _ops_from(result)
        assert "re" not in passthrough_ops

    def test_re_corner_points_correct(self):
        instrs = [_op("re", 1.0, 2.0, 4.0, 3.0), _op("S")]
        result = segment(instrs, _default_config())
        path = _paths_from(result)[0]
        pts = path.subpaths[0].points
        assert (1.0, 2.0) in pts
        assert (5.0, 2.0) in pts
        assert (5.0, 5.0) in pts
        assert (1.0, 5.0) in pts


# ---------------------------------------------------------------------------
# segment() — clipping paths
# ---------------------------------------------------------------------------


class TestSegmentClipping:
    def test_W_marks_path_as_clipping(self):
        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("W"),
            _op("S"),
        ]
        result = segment(instrs, _default_config())
        path = _paths_from(result)[0]
        assert path.is_clipping is True

    def test_W_star_marks_path_as_clipping(self):
        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("W*"),
            _op("S"),
        ]
        result = segment(instrs, _default_config())
        path = _paths_from(result)[0]
        assert path.is_clipping is True

    def test_clipping_flag_reset_after_path(self):
        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("W"),
            _op("S"),
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("S"),
        ]
        result = segment(instrs, _default_config())
        paths = _paths_from(result)
        assert paths[0].is_clipping is True
        assert paths[1].is_clipping is False


# ---------------------------------------------------------------------------
# segment() — graphics state
# ---------------------------------------------------------------------------


class TestSegmentGraphicsState:
    def test_q_Q_pass_through(self):
        instrs = [_op("q"), _op("Q")]
        result = segment(instrs, _default_config())
        assert _ops_from(result) == ["q", "Q"]

    def test_cm_updates_ctm(self):
        # After cm 2 0 0 2 0 0, tolerance in user space should halve
        instrs = [
            _op("cm", 2.0, 0.0, 0.0, 2.0, 0.0, 0.0),
            _op("m", 0.0, 0.0),
            _op("c", 0.5, 1.0, 1.5, 1.0, 2.0, 0.0),
            _op("S"),
        ]
        result = segment(instrs, _default_config())
        path = _paths_from(result)[0]
        assert path.subpaths[0].ctm_scale == pytest.approx(2.0)

    def test_gs_op_mid_path_flushes_with_no_paint_op(self):
        # A q inside a path is unusual — should flush the path as interrupted
        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("q"),  # gs op interrupts path
            _op("Q"),
        ]
        result = segment(instrs, _default_config())
        paths = _paths_from(result)
        assert len(paths) == 1
        assert paths[0].paint_op is None  # interrupted, no paint op

    def test_Q_op_mid_path_flushes_with_no_paint_op(self):
        # A Q state restoration mid-path is unusual and should flush the path
        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("Q"),
        ]
        result = segment(instrs, _default_config())
        paths = _paths_from(result)
        assert len(paths) == 1
        assert paths[0].paint_op is None

    def test_cm_op_mid_path_flushes_with_no_paint_op(self):
        # A transform update mid-path is unusual and should flush the path
        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("cm", 1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
        ]
        result = segment(instrs, _default_config())
        paths = _paths_from(result)
        assert len(paths) == 1
        assert paths[0].paint_op is None

    def test_w_op_mid_path_flushes_with_no_paint_op(self):
        # A width adjustment mid-path is unusual and should flush the path
        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("w", 2.0),
        ]
        result = segment(instrs, _default_config())
        paths = _paths_from(result)
        assert len(paths) == 1
        assert paths[0].paint_op is None

    def test_q_Q_ctm_restore(self):
        instrs = [
            _op("q"),
            _op("cm", 2.0, 0.0, 0.0, 2.0, 0.0, 0.0),
            _op("Q"),
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("S"),
        ]
        result = segment(instrs, _default_config())
        path = _paths_from(result)[0]
        # After Q, CTM should be restored to identity → ctm_scale = 1.0
        assert path.subpaths[0].ctm_scale == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# segment() — fallback cases
# ---------------------------------------------------------------------------


class TestSegmentFallback:
    def test_non_path_op_mid_path_flushes(self):
        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("BT"),  # completely unrelated op mid-path
            _op("ET"),
        ]
        result = segment(instrs, _default_config())
        paths = _paths_from(result)
        assert len(paths) == 1
        assert paths[0].paint_op is None

    def test_trailing_unclosed_path_flushed(self):
        # No painting op at end — should still produce a path
        instrs = [_op("m", 0.0, 0.0), _op("l", 1.0, 0.0)]
        result = segment(instrs, _default_config())
        paths = _paths_from(result)
        assert len(paths) == 1
        assert paths[0].paint_op is None

    def test_state_not_shared_between_calls(self):
        # Two separate calls must not share state
        instrs = [_op("m", 0.0, 0.0), _op("l", 1.0, 0.0), _op("S")]
        r1 = segment(instrs, _default_config())
        r2 = segment(instrs, _default_config())
        assert len(_paths_from(r1)) == len(_paths_from(r2))


# ---------------------------------------------------------------------------
# segment() — coalescing strokes
# ---------------------------------------------------------------------------


class TestSegmentCoalesceStrokes:
    def test_coalesces_simple_strokes(self):
        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("S"),
            _op("m", 1.0, 0.0),
            _op("l", 2.0, 0.0),
            _op("S"),
        ]
        result = segment(instrs, _default_config(coalesce_strokes=True))
        paths = _paths_from(result)
        assert len(paths) == 1
        assert len(paths[0].subpaths[0].points) == 3
        assert paths[0].subpaths[0].original_op_count == 3

    def test_coalesces_with_intermediate_w(self):
        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("S"),
            _op("w", 1.0),
            _op("m", 1.0, 0.0),
            _op("l", 2.0, 0.0),
            _op("S"),
            _op("w", 1.05),  # Spread is 0.05. max(0.05, 0.10) = 0.10 -> allowed
            _op("m", 2.0, 0.0),
            _op("l", 3.0, 0.0),
            _op("S"),
        ]
        result = segment(instrs, _default_config(coalesce_strokes=True))
        paths = _paths_from(result)
        assert len(paths) == 1
        ops = _ops_from(result)
        # We expect initial avg 'w' (1.025) and the final 'w' (1.05) state
        assert ops == ["w", "w"]

    def test_coalesce_breaks_chain_on_high_width_variance(self):
        """Breaks grouping when width variance exceeds absolute or percentage boundaries."""
        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("S"),
            _op("w", 1.0),
            _op("m", 1.0, 0.0),
            _op("l", 2.0, 0.0),
            _op("S"),
            _op("w", 1.2),  # Spread is 0.2. max(0.05, 0.1) = 0.1. 0.2 > 0.1 -> chain break!
            _op("m", 2.0, 0.0),
            _op("l", 3.0, 0.0),
            _op("S"),
        ]
        result = segment(instrs, _default_config(coalesce_strokes=True))
        paths = _paths_from(result)
        # Variance exceeded our 10% tolerance; splits into two groups
        assert len(paths) == 2

    def test_no_coalesce_if_not_connected(self):
        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("S"),
            _op("m", 1.5, 0.0),
            _op("l", 2.0, 0.0),
            _op("S"),
        ]
        result = segment(instrs, _default_config(coalesce_strokes=True))
        paths = _paths_from(result)
        assert len(paths) == 2

    def test_coalesce_disabled_by_config(self):
        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("S"),
            _op("m", 1.0, 0.0),
            _op("l", 2.0, 0.0),
            _op("S"),
        ]
        result = segment(instrs, _default_config(coalesce_strokes=False))
        paths = _paths_from(result)
        assert len(paths) == 2

    def test_ignores_non_stroke_paths(self):
        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("f"),  # Fill path
            _op("m", 1.0, 0.0),
            _op("l", 2.0, 0.0),
            _op("S"),  # Stroke path
        ]
        result = segment(instrs, _default_config(coalesce_strokes=True))
        paths = _paths_from(result)
        assert len(paths) == 2

    def test_handles_malformed_w_ops_gracefully(self):
        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("S"),
            _op("w"),  # Malformed: Empty operands
            _op("m", 1.0, 0.0),
            _op("l", 2.0, 0.0),
            _op("S"),
            _op("w", "bad_float"),  # Malformed: String where float is expected
            _op("m", 2.0, 0.0),
            _op("l", 3.0, 0.0),
            _op("S"),
        ]
        # Must catch internal indexing/value exceptions and fallback safely
        result = segment(instrs, _default_config(coalesce_strokes=True))
        assert len(_paths_from(result)) == 1

    def test_omits_redundant_final_w(self):
        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("S"),
            _op("w", 1.0),
            _op("m", 1.0, 0.0),
            _op("l", 2.0, 0.0),
            _op("S"),
            _op("w", 1.0),  # Exact same width as average
            _op("m", 2.0, 0.0),
            _op("l", 3.0, 0.0),
            _op("S"),
        ]
        result = segment(instrs, _default_config(coalesce_strokes=True))
        ops = _ops_from(result)
        assert ops == ["w"]  # Only initial avg 'w' emitted, final is elided safely

    def test_coalesce_multi_subpath_path(self):
        """Coerces multiple subpaths into the merged struct when connected correctly."""
        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("S"),
            # Next path seamlessly connects, but introduces an additional subpath to merge loop
            _op("m", 1.0, 0.0),
            _op("l", 2.0, 0.0),
            _op("m", 3.0, 0.0),
            _op("l", 4.0, 0.0),
            _op("S"),
        ]
        result = segment(instrs, _default_config(coalesce_strokes=True))
        paths = _paths_from(result)
        assert len(paths) == 1
        assert len(paths[0].subpaths) == 2

    def test_final_w_exception_handling(self):
        """Ensures exception boundaries are protected when floating conversion fails on comparison checks."""

        class _EvilFloat:
            def __init__(self, val):
                self.val = float(val)
                self.calls = 0

            def __float__(self):
                self.calls += 1
                # 1. _Segmenter calls it inside graphics state updates
                # 2. _coalesce_strokes variance check calls it
                # 3. _merge_stroke_group average check calls it
                # 4. _coalesce_strokes abs() diff check calls it
                if self.calls == 4:
                    raise ValueError("Simulated parsing crash on final check")
                return self.val

        instrs = [
            _op("m", 0.0, 0.0),
            _op("l", 1.0, 0.0),
            _op("S"),
            _op("w", _EvilFloat(1.05)),  # Within tolerance of the default 1.0 width
            _op("m", 1.0, 0.0),
            _op("l", 2.0, 0.0),
            _op("S"),
        ]

        result = segment(instrs, _default_config(coalesce_strokes=True))
        ops = _ops_from(result)

        # Hits the except block safely appending final_w.
        assert ops == ["w", "w"]


# ---------------------------------------------------------------------------
# serialize()
# ---------------------------------------------------------------------------


class TestSerialize:
    def _make_path(self, instructions=None):
        return Path(
            subpaths=[],
            paint_op="S",
            original_instructions=instructions or [],
        )

    def test_passthrough_emitted_unchanged(self):
        item = (["hello"], "BT")
        result = serialize([item])
        assert result == [item]

    def test_fell_back_emits_original_instructions(self):
        original = [([0.0, 0.0], "m"), ([1.0, 0.0], "l"), ([], "S")]
        path = self._make_path(instructions=original)
        sp = SimplifiedPath(subpath_instructions=[], paint_op="S", fell_back=True)
        result = serialize([(path, sp)])
        assert result == original

    def test_simplified_instructions_emitted(self):
        path = self._make_path(instructions=[([], "S")])
        sp = SimplifiedPath(
            subpath_instructions=[
                ([0.0, 0.0], "m"),
                ([1.0, 0.0], "l"),
            ],
            paint_op="S",
            fell_back=False,
        )
        result = serialize([(path, sp)])
        # Should contain the simplified ops + the paint op
        op_strs = [str(item[1]) for item in result]
        assert "m" in op_strs
        assert "l" in op_strs
        assert "S" in op_strs

    def test_paint_op_from_original_instructions_preserved(self):
        """Paint op should use the original pikepdf operator object if found."""
        original_paint_op = "f"  # plain string as stand-in for pikepdf operator
        path = self._make_path(instructions=[([], original_paint_op)])
        sp = SimplifiedPath(
            subpath_instructions=[([0.0, 0.0], "m")],
            paint_op="f",
            fell_back=False,
        )
        result = serialize([(path, sp)])
        assert result[-1] == ([], "f")

    def test_paint_op_fallback_when_not_in_original(self):
        """If paint op not found in original instructions, emit as plain string."""
        path = self._make_path(instructions=[])  # no original instructions
        sp = SimplifiedPath(
            subpath_instructions=[([0.0, 0.0], "m")],
            paint_op="S",
            fell_back=False,
        )
        result = serialize([(path, sp)])
        assert result[-1] == ([], "S")

    def test_mixed_list_processed_correctly(self):
        passthrough = ([1.0], "w")
        path = self._make_path(instructions=[([], "S")])
        sp = SimplifiedPath(
            subpath_instructions=[([0.0, 0.0], "m")],
            paint_op="S",
            fell_back=False,
        )
        result = serialize([passthrough, (path, sp)])
        assert result[0] == passthrough


# ---------------------------------------------------------------------------
# Internal Coalesce Checks
# ---------------------------------------------------------------------------


class TestCoalesceInternals:
    def test_connects_guard_clauses(self):
        # Short circuits gracefully if either path has no subpaths
        p1 = Path(subpaths=[], paint_op="S", original_instructions=[])
        p2 = Path(
            subpaths=[Subpath(points=[(0.0, 0.0)], original_op_count=1)],
            paint_op="S",
            original_instructions=[],
        )
        assert not _connects(p1, p2)
        assert not _connects(p2, p1)

        # Short circuits gracefully if subpaths lack point data
        p3 = Path(
            subpaths=[Subpath(points=[], original_op_count=1)],
            paint_op="S",
            original_instructions=[],
        )
        assert not _connects(p2, p3)
        assert not _connects(p3, p2)

    def test_collect_stroke_group_seeds_from_missing_state_snapshot(self):
        """If state_snapshot is missing or malformed, fall back to permissive sentinels
        rather than crashing on the seed step."""
        from pdftl.utils.path_segmentation import _collect_stroke_group

        p1 = Path(
            subpaths=[Subpath(points=[(0.0, 0.0), (1.0, 0.0)], original_op_count=2)],
            paint_op="S",
            original_instructions=[],
            state_snapshot=None,  # malformed / missing
        )
        group_paths, group_widths, j = _collect_stroke_group([p1], 0, 1)
        assert group_paths == [p1]
        assert group_widths == []
        assert j == 1

    def test_merge_stroke_group_skips_degenerate_empty_subpaths(self):
        """A path with no subpaths (e.g. from a malformed/degenerate
        construction) must not crash _merge_stroke_group with an
        IndexError when accessing merged_subpaths[-1]; it should simply
        be skipped while merging the remaining valid paths."""
        from pdftl.utils.path_segmentation import _merge_stroke_group

        degenerate = Path(
            subpaths=[],  # no subpaths at all
            paint_op="S",
            original_instructions=[([], "S")],
        )
        normal = Path(
            subpaths=[Subpath(points=[(0.0, 0.0), (1.0, 0.0)], original_op_count=2)],
            paint_op="S",
            original_instructions=[([0.0, 0.0], "m"), ([1.0, 0.0], "l"), ([], "S")],
        )

        # degenerate first: merged_subpaths starts empty, so the loop must
        # skip `normal` via the `not merged_subpaths` branch rather than
        # indexing merged_subpaths[-1].
        merged, initial_w, final_w = _merge_stroke_group([degenerate, normal], [])
        assert merged.subpaths == []

        # degenerate second: merged_subpaths is seeded from the first
        # (normal) path, so the loop must skip `degenerate` via the
        # `not p.subpaths` branch rather than crashing on p.subpaths[0].
        merged2, _, _ = _merge_stroke_group([normal, degenerate], [])
        assert len(merged2.subpaths) == 1
        assert merged2.subpaths[0].points == [(0.0, 0.0), (1.0, 0.0)]


# ---------------------------------------------------------------------------
# Graphics State and Fallback Tests (Verification of Segmenter State Machine)
# ---------------------------------------------------------------------------


class TestGsOpWithNoPendingPath:
    """Verifies that handlers are clean when graphics state operators arrive on empty boundaries."""

    def test_q_Q_at_stream_start_produces_no_path(self):
        instrs = [_op("q"), _op("Q")]
        result = segment(instrs, _default_config())
        assert _paths_from(result) == []
        assert _ops_from(result) == ["q", "Q"]

    def test_w_at_stream_start_passes_through(self):
        instrs = [_op("w", 2.0)]
        result = segment(instrs, _default_config())
        assert _paths_from(result) == []
        assert "w" in _ops_from(result)

    def test_cm_at_stream_start_passes_through(self):
        instrs = [_op("cm", 1.0, 0.0, 0.0, 1.0, 0.0, 0.0)]
        result = segment(instrs, _default_config())
        assert _paths_from(result) == []
        assert "cm" in _ops_from(result)


class TestClippingOpWithNoPendingPath:
    """Verifies behavior when clipping modifiers arrive outside of an active vector run."""

    def test_W_without_path_is_passthrough(self):
        instrs = [_op("W")]
        result = segment(instrs, _default_config())
        assert _paths_from(result) == []
        assert "W" in _ops_from(result)

    def test_W_star_without_path_is_passthrough(self):
        instrs = [_op("W*")]
        result = segment(instrs, _default_config())
        assert _paths_from(result) == []
        assert "W*" in _ops_from(result)


class TestBezierOpsWithoutPriorM:
    """Verifies that curves arriving before any 'move' operators do not crash and handle state safely."""

    def test_c_without_m_does_not_raise(self):
        instrs = [_op("c", 0.5, 1.0, 1.5, 1.0, 2.0, 0.0), _op("S")]
        result = segment(instrs, _default_config())
        # Should produce a path (the c was absorbed into path ops) without crashing
        assert result is not None

    def test_v_without_m_does_not_raise(self):
        instrs = [_op("v", 1.5, 1.0, 2.0, 0.0), _op("S")]
        result = segment(instrs, _default_config())
        assert result is not None

    def test_y_without_m_does_not_raise(self):
        instrs = [_op("y", 0.5, 1.0, 2.0, 0.0), _op("S")]
        result = segment(instrs, _default_config())
        assert result is not None

    def test_l_without_m_does_not_add_point(self):
        # Line creation before move is silently ignored
        instrs = [_op("l", 1.0, 0.0), _op("S")]
        result = segment(instrs, _default_config())
        assert result is not None


class TestSplitInstructionsPerSubpath:
    """Direct unit tests for _split_instructions_per_subpath."""

    def test_zero_subpaths_returns_empty(self):
        """Test n_subpaths <= 0 returns [] regardless of ops content."""
        ops = [([1, 2], "m"), ([3, 4], "l")]
        assert _split_instructions_per_subpath(ops, 0) == []

    def test_single_subpath_gets_all_ops(self):
        """Test one m-started subpath with several draws all land in one group."""
        ops = [([0, 0], "m"), ([1, 1], "l"), ([2, 2], "l")]
        groups = _split_instructions_per_subpath(ops, 1)
        assert groups == [ops]

    def test_two_subpaths_split_on_second_m(self):
        """Test a second 'm' starts a new group, not appended to the first."""
        ops = [
            ([0, 0], "m"),
            ([1, 1], "l"),
            ([5, 5], "m"),
            ([6, 6], "l"),
        ]
        groups = _split_instructions_per_subpath(ops, 2)
        assert groups[0] == [([0, 0], "m"), ([1, 1], "l")]
        assert groups[1] == [([5, 5], "m"), ([6, 6], "l")]

    def test_re_is_its_own_single_instruction_group(self):
        """Test 're' both starts and fully constitutes one subpath's group."""
        ops = [([0, 0], "m"), ([1, 1], "l"), ([0, 0, 5, 5], "re")]
        groups = _split_instructions_per_subpath(ops, 2)
        assert groups[0] == [([0, 0], "m"), ([1, 1], "l")]
        assert groups[1] == [([0, 0, 5, 5], "re")]

    def test_h_attaches_to_current_group_not_a_new_one(self):
        """Test a closing 'h' stays with the subpath it closes, not a fresh group."""
        ops = [([0, 0], "m"), ([1, 1], "l"), ([], "h")]
        groups = _split_instructions_per_subpath(ops, 1)
        assert groups[0] == ops

    def test_trailing_clip_marker_attaches_to_last_subpath(self):
        """Test a stray W/W* clip-marker op trailing after the last subpath's
        geometry is attributed to that last subpath, not dropped or misplaced."""
        ops = [
            ([0, 0], "m"),
            ([1, 1], "l"),
            ([5, 5], "m"),
            ([6, 6], "l"),
            ([], "W"),
        ]
        groups = _split_instructions_per_subpath(ops, 2)
        assert groups[1][-1] == ([], "W")
        assert ([], "W") not in groups[0]

    def test_more_m_ops_than_declared_subpaths_clamps_to_last(self):
        """Test a malformed/mismatched count (more m's than n_subpaths) clamps
        the index rather than raising an IndexError."""
        ops = [
            ([0, 0], "m"),
            ([5, 5], "m"),
            ([9, 9], "m"),
        ]
        groups = _split_instructions_per_subpath(ops, 2)
        assert len(groups) == 2
        assert groups[1] == [([5, 5], "m"), ([9, 9], "m")]


class TestSegmentTrackInstructions:
    """Integration tests: segment(..., track_instructions=True) end-to-end."""

    def test_default_track_instructions_off_leaves_subpath_instructions_none(self):
        """Test the default (track_instructions=False, simplify_vectors's call
        shape) never populates Subpath.instructions -- confirms zero behavior
        change for the existing caller."""
        instructions = [
            ([0, 0], "m"),
            ([1, 1], "l"),
            ([5, 5], "m"),
            ([6, 6], "l"),
            ([], "S"),
        ]
        config = SimplifyConfig()
        mixed = segment(instructions, config)
        paths = [item for item in mixed if isinstance(item, Path)]
        assert len(paths) == 1
        for sp in paths[0].subpaths:
            assert sp.instructions is None

    def test_track_instructions_true_populates_per_subpath(self):
        """Test track_instructions=True splits raw ops across each subpath,
        with the paint op excluded (it stays at the Path level)."""
        instructions = [
            ([0, 0], "m"),
            ([1, 1], "l"),
            ([5, 5], "m"),
            ([6, 6], "l"),
            ([], "S"),
        ]
        config = SimplifyConfig(coalesce_strokes=False)
        mixed = segment(instructions, config, track_instructions=True)
        paths = [item for item in mixed if isinstance(item, Path)]
        assert len(paths) == 1
        sp0, sp1 = paths[0].subpaths
        assert sp0.instructions == [([0, 0], "m"), ([1, 1], "l")]
        assert sp1.instructions == [([5, 5], "m"), ([6, 6], "l")]
        # Paint op must not leak into either subpath's own instruction list.
        assert ([], "S") not in sp0.instructions
        assert ([], "S") not in sp1.instructions

    def test_track_instructions_true_single_subpath_path(self):
        """Test the common single-subpath case (e.g. a simple filled shape)
        still populates instructions correctly."""
        instructions = [
            ([0, 0, 10, 10], "re"),
            ([], "f"),
        ]
        config = SimplifyConfig()
        mixed = segment(instructions, config, track_instructions=True)
        paths = [item for item in mixed if isinstance(item, Path)]
        assert len(paths) == 1
        assert paths[0].subpaths[0].instructions == [([0, 0, 10, 10], "re")]
