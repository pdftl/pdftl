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


# ---------------------------------------------------------------------------
# Text state: BT/ET, Tm, Td/TD/T*
# ---------------------------------------------------------------------------


class TestTextObjectBracketing:
    def test_bt_resets_to_identity(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        assert gs.text_matrix == (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        assert gs.text_line_matrix == (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    def test_et_clears_matrices(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("ET", [])
        assert gs.text_matrix is None
        assert gs.text_line_matrix is None

    def test_unknown_operator_is_noop(self):
        gs = GraphicsState()
        gs.apply_text_op("XYZZY", [1, 2, 3])
        assert gs.text_matrix is None

    def test_render_matrix_none_outside_text_object(self):
        gs = GraphicsState()
        assert gs.text_render_matrix is None


class TestTm:
    def test_tm_sets_both_matrices(self):
        gs = GraphicsState()
        gs.apply_text_op("Tm", [1.0, 0.0, 0.0, 1.0, 10.0, 20.0])
        assert gs.text_matrix == pytest.approx((1.0, 0.0, 0.0, 1.0, 10.0, 20.0))
        assert gs.text_line_matrix == pytest.approx((1.0, 0.0, 0.0, 1.0, 10.0, 20.0))

    def test_tm_malformed_operands_ignored(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        before = gs.text_matrix
        gs.apply_text_op("Tm", ["bad", "operands"])
        assert gs.text_matrix == before

    def test_tm_without_bt_still_sets_matrix(self):
        # Malformed streams aside, Tm itself doesn't require a prior BT to
        # set the matrix fields directly (BT only resets to identity).
        gs = GraphicsState()
        gs.apply_text_op("Tm", [2.0, 0.0, 0.0, 2.0, 5.0, 5.0])
        assert gs.text_matrix == pytest.approx((2.0, 0.0, 0.0, 2.0, 5.0, 5.0))


class TestTdAndTD:
    def test_td_offsets_from_line_matrix(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("Td", [10.0, 5.0])
        assert gs.text_matrix == pytest.approx((1.0, 0.0, 0.0, 1.0, 10.0, 5.0))
        assert gs.text_line_matrix == pytest.approx((1.0, 0.0, 0.0, 1.0, 10.0, 5.0))

    def test_td_without_text_line_matrix_is_noop(self):
        gs = GraphicsState()
        gs.apply_text_op("Td", [10.0, 5.0])  # no BT first
        assert gs.text_matrix is None

    def test_td_malformed_operands_ignored(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        before = gs.text_matrix
        gs.apply_text_op("Td", [1.0])  # missing second operand
        assert gs.text_matrix == before

    def test_td_cumulative(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("Td", [10.0, 0.0])
        gs.apply_text_op("Td", [0.0, 5.0])
        assert gs.text_matrix == pytest.approx((1.0, 0.0, 0.0, 1.0, 10.0, 5.0))

    def test_TD_sets_leading_and_offsets(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("TD", [0.0, -14.0])
        assert gs.leading == pytest.approx(14.0)
        assert gs.text_matrix == pytest.approx((1.0, 0.0, 0.0, 1.0, 0.0, -14.0))

    def test_TD_malformed_operands_ignored(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        original_leading = gs.leading
        gs.apply_text_op("TD", [])
        assert gs.leading == original_leading

    def test_t_star_uses_leading(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("TL", [12.0])
        gs.apply_text_op("T*", [])
        assert gs.text_matrix == pytest.approx((1.0, 0.0, 0.0, 1.0, 0.0, -12.0))


# ---------------------------------------------------------------------------
# Text state: Tf, Tc, Tw, Tz, TL, Ts
# ---------------------------------------------------------------------------


class TestTextStateScalars:
    def test_tf_sets_font_and_size(self):
        gs = GraphicsState()
        gs.apply_text_op("Tf", ["/F1", 12.0])
        assert gs.font_name == "/F1"
        assert gs.font_size == pytest.approx(12.0)

    def test_tf_malformed_operands_ignored(self):
        gs = GraphicsState()
        gs.apply_text_op("Tf", ["/F1", 12.0])
        gs.apply_text_op("Tf", ["/F2"])  # missing size
        assert gs.font_name == "/F1"
        assert gs.font_size == pytest.approx(12.0)

    def test_tc_sets_char_spacing(self):
        gs = GraphicsState()
        gs.apply_text_op("Tc", [0.5])
        assert gs.char_spacing == pytest.approx(0.5)

    def test_tc_malformed_ignored(self):
        gs = GraphicsState()
        gs.apply_text_op("Tc", [])
        assert gs.char_spacing == pytest.approx(0.0)

    def test_tw_sets_word_spacing(self):
        gs = GraphicsState()
        gs.apply_text_op("Tw", [1.5])
        assert gs.word_spacing == pytest.approx(1.5)

    def test_tw_malformed_ignored(self):
        gs = GraphicsState()
        gs.apply_text_op("Tw", ["bad"])
        assert gs.word_spacing == pytest.approx(0.0)

    def test_tz_sets_horizontal_scale_fraction(self):
        gs = GraphicsState()
        gs.apply_text_op("Tz", [50.0])
        assert gs.horizontal_scale == pytest.approx(0.5)

    def test_tz_malformed_ignored(self):
        gs = GraphicsState()
        gs.apply_text_op("Tz", [])
        assert gs.horizontal_scale == pytest.approx(1.0)

    def test_tl_sets_leading(self):
        gs = GraphicsState()
        gs.apply_text_op("TL", [18.0])
        assert gs.leading == pytest.approx(18.0)

    def test_tl_malformed_ignored(self):
        gs = GraphicsState()
        gs.apply_text_op("TL", ["bad"])
        assert gs.leading == pytest.approx(0.0)

    def test_ts_sets_rise(self):
        gs = GraphicsState()
        gs.apply_text_op("Ts", [3.0])
        assert gs.text_rise == pytest.approx(3.0)

    def test_ts_malformed_ignored(self):
        gs = GraphicsState()
        gs.apply_text_op("Ts", [])
        assert gs.text_rise == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Text showing: Tj, TJ, ', "
# ---------------------------------------------------------------------------


def _fixed_width_fn(_font_name, _code):
    """Every glyph advances by 500/1000 em, for easy arithmetic in tests."""
    return 500.0


class TestShowText:
    def test_tj_advances_matrix(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("Tf", ["/F1", 10.0])
        gs.apply_text_op("Tj", [b"AB"], glyph_width_fn=_fixed_width_fn)
        # 2 glyphs * 500/1000 * 10.0 = 10.0 total advance
        assert gs.text_matrix == pytest.approx((1.0, 0.0, 0.0, 1.0, 10.0, 0.0))

    def test_tj_no_glyph_width_fn_is_noop_for_position(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("Tf", ["/F1", 10.0])
        gs.apply_text_op("Tj", [b"AB"])  # no glyph_width_fn
        assert gs.text_matrix == pytest.approx((1.0, 0.0, 0.0, 1.0, 0.0, 0.0))

    def test_tj_no_font_selected_is_noop(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("Tj", [b"AB"], glyph_width_fn=_fixed_width_fn)
        assert gs.text_matrix == pytest.approx((1.0, 0.0, 0.0, 1.0, 0.0, 0.0))

    def test_tj_empty_operands_is_noop(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("Tf", ["/F1", 10.0])
        gs.apply_text_op("Tj", [], glyph_width_fn=_fixed_width_fn)
        assert gs.text_matrix == pytest.approx((1.0, 0.0, 0.0, 1.0, 0.0, 0.0))

    def test_tj_str_operand_encoded_latin1(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("Tf", ["/F1", 10.0])
        gs.apply_text_op("Tj", ["AB"], glyph_width_fn=_fixed_width_fn)
        assert gs.text_matrix == pytest.approx((1.0, 0.0, 0.0, 1.0, 10.0, 0.0))

    def test_tj_advance_without_text_matrix_is_noop(self):
        # No BT: text_matrix stays None, _advance_by_width_1000 must bail.
        gs = GraphicsState()
        gs.apply_text_op("Tf", ["/F1", 10.0])
        gs.apply_text_op("Tj", [b"A"], glyph_width_fn=_fixed_width_fn)
        assert gs.text_matrix is None

    def test_tj_char_and_word_spacing_applied(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("Tf", ["/F1", 10.0])
        gs.apply_text_op("Tc", [1.0])  # +1 unit per glyph
        gs.apply_text_op("Tw", [2.0])  # +2 units, only on code 32 (space)
        gs.apply_text_op("Tj", [b" "], glyph_width_fn=_fixed_width_fn)
        # base 500/1000*10=5.0, + Tc(1.0/10*1000/1000*10=1.0), + Tw(2.0)
        assert gs.text_matrix[4] == pytest.approx(5.0 + 1.0 + 2.0)

    def test_tj_word_spacing_not_applied_to_non_space(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("Tf", ["/F1", 10.0])
        gs.apply_text_op("Tw", [2.0])
        gs.apply_text_op("Tj", [b"A"], glyph_width_fn=_fixed_width_fn)
        assert gs.text_matrix[4] == pytest.approx(5.0)

    def test_tj_zero_font_size_skips_spacing_division(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("Tf", ["/F1", 0.0])
        gs.apply_text_op("Tc", [1.0])
        # Should not raise ZeroDivisionError, and glyph advance is 0.
        gs.apply_text_op("Tj", [b"A"], glyph_width_fn=_fixed_width_fn)
        assert gs.text_matrix[4] == pytest.approx(0.0)

    def test_tj_array_mixes_strings_and_kerning(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("Tf", ["/F1", 10.0])
        gs.apply_text_op("TJ", [[b"A", -100, b"B"]], glyph_width_fn=_fixed_width_fn)
        # A: 5.0, kerning -100/1000*10 = -1.0 subtractive -> +1.0 advance, B: 5.0
        assert gs.text_matrix[4] == pytest.approx(5.0 + 1.0 + 5.0)

    def test_tj_array_empty_operands_is_noop(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("Tf", ["/F1", 10.0])
        gs.apply_text_op("TJ", [], glyph_width_fn=_fixed_width_fn)
        assert gs.text_matrix == pytest.approx((1.0, 0.0, 0.0, 1.0, 0.0, 0.0))

    def test_tj_array_no_font_selected_still_noop(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("TJ", [[b"A"]], glyph_width_fn=_fixed_width_fn)
        assert gs.text_matrix == pytest.approx((1.0, 0.0, 0.0, 1.0, 0.0, 0.0))

    def test_quote_moves_to_next_line_then_shows(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("Tf", ["/F1", 10.0])
        gs.apply_text_op("TL", [12.0])
        gs.apply_text_op("'", [b"A"], glyph_width_fn=_fixed_width_fn)
        assert gs.text_matrix == pytest.approx((1.0, 0.0, 0.0, 1.0, 5.0, -12.0))

    def test_dquote_sets_spacing_then_moves_and_shows(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("Tf", ["/F1", 10.0])
        gs.apply_text_op("TL", [12.0])
        gs.apply_text_op('"', [2.0, 1.0, b"A"], glyph_width_fn=_fixed_width_fn)
        assert gs.word_spacing == pytest.approx(2.0)
        assert gs.char_spacing == pytest.approx(1.0)
        # base 5.0 + Tc(1.0) ; code 'A' != 32 so no Tw contribution
        assert gs.text_matrix == pytest.approx((1.0, 0.0, 0.0, 1.0, 6.0, -12.0))

    def test_dquote_malformed_operands_ignored(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op('"', [], glyph_width_fn=_fixed_width_fn)
        assert gs.word_spacing == pytest.approx(0.0)
        assert gs.char_spacing == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# text_render_matrix
# ---------------------------------------------------------------------------


class TestTextRenderMatrix:
    def test_render_matrix_combines_font_size_and_tm(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("Tf", ["/F1", 12.0])
        gs.apply_text_op("Td", [7.0, 3.0])
        m = gs.text_render_matrix
        assert m is not None
        assert m == pytest.approx((12.0, 0.0, 0.0, 12.0, 7.0, 3.0))

    def test_render_matrix_includes_rise_and_horizontal_scale(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("Tf", ["/F1", 10.0])
        gs.apply_text_op("Tz", [50.0])
        gs.apply_text_op("Ts", [2.0])
        m = gs.text_render_matrix
        assert m == pytest.approx((5.0, 0.0, 0.0, 10.0, 0.0, 2.0))

    def test_render_matrix_composes_with_ctm(self):
        gs = GraphicsState()
        gs.apply_cm([2.0, 0.0, 0.0, 2.0, 100.0, 100.0])
        gs.apply_text_op("BT", [])
        gs.apply_text_op("Tf", ["/F1", 10.0])
        m = gs.text_render_matrix
        # param_m @ Tm(identity) @ ctm(scale 2, translate 100,100)
        assert m == pytest.approx((20.0, 0.0, 0.0, 20.0, 100.0, 100.0))


# ---------------------------------------------------------------------------
# GraphicsStateStack: text-state fields save/restore correctly
# ---------------------------------------------------------------------------


class TestGraphicsStateStackTextState:
    def test_push_pop_preserves_text_matrix(self):
        stack = GraphicsStateStack()
        stack.current.apply_text_op("BT", [])
        stack.current.apply_text_op("Td", [5.0, 0.0])
        stack.push()
        stack.current.apply_text_op("Td", [5.0, 0.0])
        assert stack.current.text_matrix == pytest.approx((1.0, 0.0, 0.0, 1.0, 10.0, 0.0))
        stack.pop()
        assert stack.current.text_matrix == pytest.approx((1.0, 0.0, 0.0, 1.0, 5.0, 0.0))

    def test_clone_text_state_independent(self):
        gs = GraphicsState()
        gs.apply_text_op("BT", [])
        gs.apply_text_op("Tf", ["/F1", 12.0])
        clone = gs.clone()
        clone.apply_text_op("Tf", ["/F2", 24.0])
        assert gs.font_name == "/F1"
        assert gs.font_size == pytest.approx(12.0)
        assert clone.font_name == "/F2"
        assert clone.font_size == pytest.approx(24.0)
