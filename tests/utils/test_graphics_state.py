# tests/utils/test_graphics_state.py

"""Tests for pdftl.utils.graphics_state."""

import pytest

from pdftl.utils.graphics_state import GraphicsState, GraphicsStateStack, ctm_scale


# ---------------------------------------------------------------------------
# ctm_scale
# ---------------------------------------------------------------------------


class TestCtmScale:
    def test_identity_returns_one(self):
        assert ctm_scale((1.0, 0.0, 0.0, 1.0, 0.0, 0.0)) == pytest.approx(1.0)

    def test_uniform_scale_two(self):
        # CTM = 2× uniform scale: a=2, b=0, c=0, d=2
        assert ctm_scale((2.0, 0.0, 0.0, 2.0, 0.0, 0.0)) == pytest.approx(2.0)

    def test_rotation_preserves_scale(self):
        # 90° rotation: a=0, b=1, c=-1, d=0 → RMS should be 1.0
        assert ctm_scale((0.0, 1.0, -1.0, 0.0, 0.0, 0.0)) == pytest.approx(1.0)

    def test_degenerate_matrix_returns_one(self):
        # All linear components zero — should return 1.0 not divide by zero
        assert ctm_scale((0.0, 0.0, 0.0, 0.0, 10.0, 20.0)) == pytest.approx(1.0)

    def test_translation_does_not_affect_scale(self):
        # Translation components (e, f) should not affect scale
        assert ctm_scale((1.0, 0.0, 0.0, 1.0, 100.0, 200.0)) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# GraphicsState
# ---------------------------------------------------------------------------


class TestGraphicsState:
    def test_default_ctm_is_identity(self):
        gs = GraphicsState()
        assert gs.ctm == (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    def test_default_scale_is_one(self):
        gs = GraphicsState()
        assert gs.scale == pytest.approx(1.0)

    def test_apply_cm_identity_unchanged(self):
        gs = GraphicsState()
        gs.apply_cm([1.0, 0.0, 0.0, 1.0, 0.0, 0.0])
        assert gs.ctm == pytest.approx((1.0, 0.0, 0.0, 1.0, 0.0, 0.0))

    def test_apply_cm_scale(self):
        gs = GraphicsState()
        gs.apply_cm([2.0, 0.0, 0.0, 2.0, 0.0, 0.0])
        assert gs.scale == pytest.approx(2.0)

    def test_apply_cm_nested(self):
        gs = GraphicsState()
        gs.apply_cm([2.0, 0.0, 0.0, 2.0, 0.0, 0.0])
        gs.apply_cm([3.0, 0.0, 0.0, 3.0, 0.0, 0.0])
        assert gs.scale == pytest.approx(6.0)

    def test_set_line_width(self):
        gs = GraphicsState()
        gs.set_line_width([2.5])
        assert gs.line_width == pytest.approx(2.5)

    def test_set_line_width_malformed_ignored(self):
        gs = GraphicsState()
        original = gs.line_width
        gs.set_line_width([])  # empty operands
        assert gs.line_width == original
        gs.set_line_width(["bad"])  # non-numeric
        assert gs.line_width == original

    def test_mark_and_consume_clipping(self):
        gs = GraphicsState()
        assert not gs.is_clipping
        gs.mark_clipping()
        assert gs.is_clipping
        result = gs.consume_clipping()
        assert result is True
        assert not gs.is_clipping

    def test_consume_clipping_without_mark(self):
        gs = GraphicsState()
        result = gs.consume_clipping()
        assert result is False

    def test_clone_is_independent(self):
        gs = GraphicsState()
        gs.apply_cm([2.0, 0.0, 0.0, 2.0, 0.0, 0.0])
        clone = gs.clone()
        clone.apply_cm([3.0, 0.0, 0.0, 3.0, 0.0, 0.0])
        # Original should be unaffected
        assert gs.scale == pytest.approx(2.0)
        assert clone.scale == pytest.approx(6.0)

    def test_clone_clipping_flag_independent(self):
        gs = GraphicsState()
        gs.mark_clipping()
        clone = gs.clone()
        clone.consume_clipping()
        assert gs.is_clipping  # original unaffected

    def test_user_space_tolerance_identity(self):
        gs = GraphicsState()
        assert gs.user_space_tolerance(0.15) == pytest.approx(0.15)

    def test_user_space_tolerance_scaled(self):
        gs = GraphicsState()
        gs.apply_cm([2.0, 0.0, 0.0, 2.0, 0.0, 0.0])
        # device_tol / scale = 0.15 / 2.0
        assert gs.user_space_tolerance(0.15) == pytest.approx(0.075)

    def test_user_space_tolerance_clamp_min(self):
        gs = GraphicsState()
        gs.apply_cm([1000.0, 0.0, 0.0, 1000.0, 0.0, 0.0])
        assert gs.user_space_tolerance(0.001) == pytest.approx(0.01)

    def test_user_space_tolerance_clamp_max(self):
        gs = GraphicsState()
        gs.apply_cm([0.001, 0.0, 0.0, 0.001, 0.0, 0.0])
        assert gs.user_space_tolerance(10.0) == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# GraphicsStateStack
# ---------------------------------------------------------------------------


class TestGraphicsStateStack:
    def test_initial_state_is_identity(self):
        stack = GraphicsStateStack()
        assert stack.current.scale == pytest.approx(1.0)
        assert len(stack) == 0

    def test_push_pop_round_trip(self):
        stack = GraphicsStateStack()
        stack.current.apply_cm([2.0, 0.0, 0.0, 2.0, 0.0, 0.0])
        stack.push()
        assert len(stack) == 1
        stack.current.apply_cm([3.0, 0.0, 0.0, 3.0, 0.0, 0.0])
        assert stack.current.scale == pytest.approx(6.0)
        stack.pop()
        assert len(stack) == 0
        assert stack.current.scale == pytest.approx(2.0)

    def test_push_isolates_state(self):
        stack = GraphicsStateStack()
        stack.push()
        stack.current.apply_cm([5.0, 0.0, 0.0, 5.0, 0.0, 0.0])
        stack.pop()
        assert stack.current.scale == pytest.approx(1.0)

    def test_stack_overflow_warns_and_ignores(self, caplog):
        import logging

        stack = GraphicsStateStack()
        # Push to the limit
        for _ in range(32):
            stack.push()
        assert len(stack) == 32
        with caplog.at_level(logging.WARNING):
            stack.push()  # one too many
        assert len(stack) == 32  # unchanged
        assert any("exceeded" in r.message for r in caplog.records)

    def test_stack_underflow_warns_and_ignores(self, caplog):
        import logging

        stack = GraphicsStateStack()
        original_scale = stack.current.scale
        with caplog.at_level(logging.WARNING):
            stack.pop()  # nothing to pop
        assert stack.current.scale == pytest.approx(original_scale)
        assert any("underflow" in r.message for r in caplog.records)

    def test_nested_push_pop(self):
        stack = GraphicsStateStack()
        stack.push()
        stack.current.apply_cm([2.0, 0.0, 0.0, 2.0, 0.0, 0.0])
        stack.push()
        stack.current.apply_cm([3.0, 0.0, 0.0, 3.0, 0.0, 0.0])
        assert stack.current.scale == pytest.approx(6.0)
        stack.pop()
        assert stack.current.scale == pytest.approx(2.0)
        stack.pop()
        assert stack.current.scale == pytest.approx(1.0)
