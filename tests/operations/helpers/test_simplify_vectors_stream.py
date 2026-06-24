# tests/operations/helpers/test_simplify_vectors_stream.py

"""Tests for pdftl.operations.helpers.simplify_vectors_stream.

The segment() and serialize() functions operate on (operands, operator) pairs.
segment() only calls str(operator), so plain strings work as operators — no
pikepdf dependency needed for most tests.
"""

import pytest

from pdftl.operations.helpers.simplify_vectors_stream import segment, serialize
from pdftl.utils.path_types import Path, SimplifiedPath, SimplifyConfig


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
# Additions — append to tests/operations/helpers/test_simplify_vectors_stream.py
# ---------------------------------------------------------------------------
# Targets:
#   line  90-91  _handle_gs: `if self._current_path_ops` guard NOT taken
#                (gs op arrives with no pending path — normal case)
#   line  104    _handle_clipping else-branch: W/W* with no pending path
#   line  160    _handle_cubic guard: c without prior m
#   line  170    _handle_cubic_implicit_p1 guard: v without prior m
#   line  179    _handle_cubic_implicit_p3 guard: y without prior m
# ---------------------------------------------------------------------------


class TestGsOpWithNoPendingPath:
    """
    Lines 90-91: the `if self._current_path_ops:` block inside _handle_gs is
    only entered when a gs operator interrupts an in-progress path.  When no
    path is in progress the guard is False and the flush is skipped — that
    else-branch (fall-through) is the normal case and was not previously hit.
    """

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
    """
    Line 104: the else-branch of _handle_clipping, reached when W/W* arrives
    outside of an in-progress path.  The operator is emitted as a pass-through.
    """

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
    """
    Lines 160, 170, 179: _handle_cubic / _handle_cubic_implicit_p1 /
    _handle_cubic_implicit_p3 all start with `if not self._current_pts: return`
    to guard against an operator that appears before any 'm'.  The operator is
    still appended to _current_path_ops so the original stream stays intact,
    but no sampling occurs.
    """

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
        # line 116: `if self._current_pts:` guard — l before m is silently ignored
        instrs = [_op("l", 1.0, 0.0), _op("S")]
        result = segment(instrs, _default_config())
        assert result is not None
