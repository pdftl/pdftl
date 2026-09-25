# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/utils/test_optimize_content.py

import pytest

from pdftl.utils.optimize_content import (
    optimize_positioning_ops,
    _drop_dead_tf,
    _collapse_text_positioning,
    _eliminate_redundant_tz,
    _eliminate_redundant_color,
    _drop_dead_q_blocks,
    _drop_empty_bt_et,
    _drop_dead_state_stores,
    _drop_paintless_stream,
)


def _ops(instructions):
    return [op for _, op in instructions]


class TestDropDeadTf:
    def test_dead_tf_dropped_when_superseded_before_any_show(self):
        instructions = [
            ([], "BT"),
            (["/F44", 11.9552], "Tf"),
            ([10.0, 0.0], "Td"),
            (["/F27", 11.9552], "Tf"),
            ([[]], "TJ"),
            ([], "ET"),
        ]
        out = _drop_dead_tf(instructions)
        assert _ops(out) == ["BT", "Td", "Tf", "TJ", "ET"]
        assert out[2][0] == ["/F27", 11.9552]

    def test_tf_kept_when_show_intervenes(self):
        instructions = [
            (["/F42", 7.9701], "Tf"),
            ([[]], "TJ"),
            (["/F27", 11.9552], "Tf"),
            ([[]], "TJ"),
        ]
        out = _drop_dead_tf(instructions)
        assert _ops(out) == ["Tf", "TJ", "Tf", "TJ"]

    def test_tf_kept_across_q_block_boundary(self):
        """A Tf right before a q/Q block that itself sets a different
        font must not be treated as dead just because nothing shows
        before the next Tf lexically -- Q restores whatever font was
        active at the matching q, not the last Tf executed textually."""
        instructions = [
            (["/F1", 12.0], "Tf"),
            ([], "q"),
            (["/F2", 12.0], "Tf"),
            ([[]], "TJ"),
            ([], "Q"),
            ([[]], "TJ"),  # shown under /F1, restored by Q
        ]
        out = _drop_dead_tf(instructions)
        assert _ops(out) == ["Tf", "q", "Tf", "TJ", "Q", "TJ"]

    def test_trailing_dead_tf_with_nothing_after_is_dropped(self):
        instructions = [
            (["/F1", 12.0], "Tf"),
            ([[]], "TJ"),
            (["/F2", 12.0], "Tf"),  # nothing ever shows under this
        ]
        out = _drop_dead_tf(instructions)
        assert _ops(out) == ["Tf", "TJ"]


class TestCollapseTextPositioning:
    def test_adjacent_td_merged(self):
        instructions = [
            ([], "BT"),
            ([4.0, 0.0], "Td"),
            ([3.0, 1.0], "Td"),
            ([[]], "TJ"),
            ([], "ET"),
        ]
        out = _collapse_text_positioning(instructions)
        assert _ops(out) == ["BT", "Td", "TJ", "ET"]
        assert out[1][0] == pytest.approx([7.0, 1.0])

    def test_td_hops_over_intervening_tf(self):
        instructions = [
            ([], "BT"),
            ([10.0, 0.0], "Td"),
            (["/F27", 11.9552], "Tf"),
            ([5.0, 2.0], "Td"),
            ([[]], "TJ"),
            ([], "ET"),
        ]
        out = _collapse_text_positioning(instructions)
        # Tf stays put, unmoved; the two Td's land merged just before TJ.
        assert _ops(out) == ["BT", "Tf", "Td", "TJ", "ET"]
        assert out[2][0] == pytest.approx([15.0, 2.0])

    def test_dead_position_chain_before_et_dropped_entirely(self):
        """Everything between BT and ET here is a chain of Tm/Td with no
        show at all -- must vanish completely, not merge into a no-op
        that still gets emitted."""
        instructions = [
            ([], "BT"),
            ([1, 0, 0, 1, 100.0, 200.0], "Tm"),
            ([5.0, 5.0], "Td"),
            ([3.0, -1.0], "Td"),
            ([], "ET"),
        ]
        out = _collapse_text_positioning(instructions)
        assert _ops(out) == ["BT", "ET"]

    def test_tm_invalidates_earlier_pending_td(self):
        """A Td before a Tm, with nothing shown between them, did
        nothing observable -- the Tm's absolute value makes it moot, so
        it must not survive even buffered."""
        instructions = [
            ([], "BT"),
            ([50.0, 50.0], "Td"),  # dead -- Tm below supersedes it
            ([1, 0, 0, 1, 10.0, 20.0], "Tm"),
            ([1.0, 1.0], "Td"),
            ([[]], "TJ"),
            ([], "ET"),
        ]
        out = _collapse_text_positioning(instructions)
        assert _ops(out) == ["BT", "Tm", "Td", "TJ", "ET"]
        assert out[1][0] == [1, 0, 0, 1, 10.0, 20.0]
        assert out[2][0] == [1.0, 1.0]

    def test_td_after_show_not_merged_with_td_before_it(self):
        """A show op between two Td's must flush-and-reset the buffer --
        confirms this pass doesn't merge across a show boundary."""
        instructions = [
            ([], "BT"),
            ([4.0, 0.0], "Td"),
            ([[]], "TJ"),
            ([3.0, 0.0], "Td"),
            ([[]], "TJ"),
            ([], "ET"),
        ]
        out = _collapse_text_positioning(instructions)
        assert _ops(out) == ["BT", "Td", "TJ", "Td", "TJ", "ET"]
        assert out[1][0] == [4.0, 0.0]
        assert out[3][0] == [3.0, 0.0]

    def test_td_not_arithmetically_merged_with_adjacent_td(self):
        """TD is buffered (and gets the drop/reorder benefits) but is
        never summed with a neighboring Td -- it also sets /TL, which
        this pass doesn't attempt to reason about."""
        instructions = [
            ([], "BT"),
            ([4.0, 0.0], "Td"),
            ([3.0, -12.0], "TD"),
            ([[]], "TJ"),
            ([], "ET"),
        ]
        out = _collapse_text_positioning(instructions)
        assert _ops(out) == ["BT", "Td", "TD", "TJ", "ET"]

    def test_position_ops_outside_text_object_pass_through_unchanged(self):
        """Defensive case for malformed/unusual input: a Td appearing
        outside any BT/ET must not be buffered or dropped."""
        instructions = [([5.0, 0.0], "Td")]
        out = _collapse_text_positioning(instructions)
        assert out == instructions

    def test_unterminated_text_object_surfaces_pending_defensively(self):
        instructions = [([], "BT"), ([5.0, 0.0], "Td")]
        out = _collapse_text_positioning(instructions)
        assert _ops(out) == ["BT", "Td"]


class TestEliminateRedundantTz:
    def test_redundant_tz_dropped(self):
        instructions = [([90.0], "Tz"), ([90.0], "Tz"), ([[]], "TJ")]
        out = _eliminate_redundant_tz(instructions)
        assert _ops(out) == ["Tz", "TJ"]

    def test_changed_tz_kept(self):
        instructions = [([80.0], "Tz"), ([90.0], "Tz")]
        out = _eliminate_redundant_tz(instructions)
        assert _ops(out) == ["Tz", "Tz"]

    def test_first_tz_is_kept_even_at_the_page_default(self):
        """A Form XObject inherits its caller's Tz, so 100 may change it."""
        instructions = [([100.0], "Tz")]
        out = _eliminate_redundant_tz(instructions)
        assert _ops(out) == ["Tz"]

    def test_tz_set_inside_q_is_restored_by_q_close(self):
        instructions = [
            ([], "q"),
            ([50.0], "Tz"),
            ([], "Q"),
            ([50.0], "Tz"),  # Q restored the earlier value, so this changes it
        ]
        out = _eliminate_redundant_tz(instructions)
        assert _ops(out) == ["q", "Tz", "Q", "Tz"]

    def test_unmatched_q_close_makes_tz_unknown(self):
        instructions = [([50.0], "Tz"), ([], "Q"), ([50.0], "Tz")]
        out = _eliminate_redundant_tz(instructions)
        assert _ops(out) == ["Tz", "Q", "Tz"]

    def test_malformed_tz_makes_the_value_unknown(self):
        instructions = [([50.0], "Tz"), (["x"], "Tz"), ([50.0], "Tz")]
        out = _eliminate_redundant_tz(instructions)
        assert _ops(out) == ["Tz", "Tz", "Tz"]

    def test_malformed_tz_operand_kept_defensively(self):
        instructions = [([None], "Tz")]
        out = _eliminate_redundant_tz(instructions)
        assert _ops(out) == ["Tz"]


class TestOptimizePositioningOpsIntegration:
    def test_dead_block_from_real_world_shape(self):
        """Reproduces the pasted-example shape: a Tm/Td chain broken up
        by font switches, with nothing shown before ET. No paint op
        (Tj/TJ/etc.) appears ANYWHERE in this snippet, so
        _drop_paintless_stream now correctly collapses the whole thing
        to nothing, before dead-Tf/positioning collapse even run --
        superseding this test's original (pre-paintless-pass) claim
        that the lone Tf would survive."""
        instructions = [
            ([], "BT"),
            ([1, 0, 0, 1, 315.378, 356.648], "Tm"),
            ([-186.965, -14.446], "Td"),
            (["/F44", 11.9552], "Tf"),
            ([148.603, -29.341], "Td"),
            (["/F42", 7.9701], "Tf"),
            ([5.633, 4.936], "Td"),
            (["/F44", 11.9552], "Tf"),
            ([17.73, 8.088], "Td"),
            ([], "ET"),
        ]
        out = optimize_positioning_ops(instructions)
        assert out == []

    def test_td_tf_chain_collapses_to_single_td_before_show(self):
        instructions = [
            ([], "BT"),
            (["/F44", 11.9552], "Tf"),
            ([-128.265, -29.048], "Td"),
            (["/F27", 11.9552], "Tf"),
            ([10.898, 0.0], "Td"),
            ([13.621, 8.088], "Td"),
            ([[pytest.approx(1)]], "TJ"),
            ([], "ET"),
        ]
        out = optimize_positioning_ops(instructions)
        # /F44 Tf is dead (dropped), /F27 Tf survives, both Td's merge.
        assert _ops(out) == ["BT", "Tf", "Td", "TJ", "ET"]
        assert out[1][0] == ["/F27", 11.9552]
        # -128.265 + 10.898 + 13.621 = -103.746; -29.048 + 0.0 + 8.088 = -20.96
        assert out[2][0] == pytest.approx([-103.746, -20.96])


class TestCollapseTextPositioningMalformedOperands:
    def test_malformed_td_operand_buffered_separately_not_merged(self):
        """A Td whose operands can't convert to float must not raise --
        it's buffered as its own entry rather than summed into the
        preceding Td."""
        instructions = [
            ([], "BT"),
            ([4.0, 0.0], "Td"),
            ([None, 0.0], "Td"),
            ([[]], "TJ"),
            ([], "ET"),
        ]
        out = _collapse_text_positioning(instructions)
        assert _ops(out) == ["BT", "Td", "Td", "TJ", "ET"]


class TestDropDeadTfAdditional:
    def test_two_tf_in_a_row_no_barrier(self):
        instructions = [
            (["/F1", 12.0], "Tf"),
            (["/F2", 12.0], "Tf"),
            ([[]], "TJ"),
        ]
        out = _drop_dead_tf(instructions)
        assert _ops(out) == ["Tf", "TJ"]
        assert out[0][0] == ["/F2", 12.0]

    def test_single_trailing_tf_with_nothing_else(self):
        instructions = [(["/F1", 12.0], "Tf")]
        out = _drop_dead_tf(instructions)
        assert out == []


class TestCollapseTextPositioningAdditional:
    def test_consecutive_tm_second_clears_first(self):
        instructions = [
            ([], "BT"),
            ([1, 0, 0, 1, 1.0, 1.0], "Tm"),
            ([1, 0, 0, 1, 2.0, 2.0], "Tm"),
            ([5.0, 5.0], "Td"),
            ([[]], "TJ"),
            ([], "ET"),
        ]
        out = _collapse_text_positioning(instructions)
        assert _ops(out) == ["BT", "Tm", "Td", "TJ", "ET"]
        assert out[1][0] == [1, 0, 0, 1, 2.0, 2.0]
        assert out[2][0] == [5.0, 5.0]

    def test_td_after_td_but_preceded_by_td_not_merged_across_td(self):
        """TD followed by Td: confirms Td only merges with an
        IMMEDIATELY preceding Td, never a preceding TD."""
        instructions = [
            ([], "BT"),
            ([4.0, 0.0], "TD"),
            ([3.0, -12.0], "Td"),
            ([[]], "TJ"),
            ([], "ET"),
        ]
        out = _collapse_text_positioning(instructions)
        assert _ops(out) == ["BT", "TD", "Td", "TJ", "ET"]
        assert out[1][0] == [4.0, 0.0]
        assert out[2][0] == [3.0, -12.0]

    def test_multiple_text_objects_do_not_leak_state(self):
        """Dead position state left dangling in one BT..ET must not
        survive into, or affect, a second BT..ET in the same list."""
        instructions = [
            ([], "BT"),
            ([5.0, 5.0], "Td"),  # dead -- nothing shown before ET
            ([], "ET"),
            ([], "BT"),
            ([1.0, 1.0], "Td"),
            ([[]], "TJ"),  # live -- must survive untouched
            ([], "ET"),
        ]
        out = _collapse_text_positioning(instructions)
        assert _ops(out) == ["BT", "ET", "BT", "Td", "TJ", "ET"]
        assert out[3][0] == [1.0, 1.0]

    def test_q_block_nested_inside_bt_et_does_not_crash_or_misbehave(self):
        """Illegal per spec but seen in the wild. q/Q must not crash --
        but unlike Tf (real graphics state, saved/restored by q/Q), Td/
        Tm are text-object state that q/Q never touches, so this is
        NOT a flush barrier here: the pending Td correctly merges
        across it. See test_q_block_nested_inside_bt_et_merges_across_it_correctly
        (same input) for the merged-value assertion."""
        instructions = [
            ([], "BT"),
            ([5.0, 5.0], "Td"),
            ([], "q"),
            ([1.0, 1.0], "Td"),
            ([[]], "TJ"),
            ([], "Q"),
            ([], "ET"),
        ]
        out = _collapse_text_positioning(instructions)
        assert _ops(out) == ["BT", "q", "Td", "TJ", "Q", "ET"]

    def test_malformed_tm_operands_do_not_poison_later_merge(self):
        instructions = [
            ([None, 0, 0, 1, 0.0, 0.0], "Tm"),
            ([], "BT"),
            ([4.0, 0.0], "Td"),
            ([3.0, 1.0], "Td"),
            ([[]], "TJ"),
            ([], "ET"),
        ]
        out = _collapse_text_positioning(instructions)
        assert _ops(out) == ["Tm", "BT", "Td", "TJ", "ET"]
        assert out[2][0] == pytest.approx([7.0, 1.0])

    def test_q_block_nested_inside_bt_et_merges_across_it_correctly(self):
        """Illegal per spec but seen in the wild. Unlike Tf (which IS
        graphics state, saved/restored by q/Q), Td/Tm only ever affect
        Tm/Tlm -- text-object state that q/Q never touches and BT alone
        resets. So a Td before q and a Td after it may correctly merge
        and execute after the q with no observable change; q/Q is a
        pass-through here, not a flush barrier."""
        instructions = [
            ([], "BT"),
            ([5.0, 5.0], "Td"),
            ([], "q"),
            ([1.0, 1.0], "Td"),
            ([[]], "TJ"),
            ([], "Q"),
            ([], "ET"),
        ]
        out = _collapse_text_positioning(instructions)
        assert _ops(out) == ["BT", "q", "Td", "TJ", "Q", "ET"]
        assert out[2][0] == pytest.approx([6.0, 6.0])


class TestEliminateRedundantTzNotBarrierScoped:
    def test_tz_redundancy_persists_across_bt_et_and_q_q(self):
        """BT/ET leave Tz alone, and a q...Q that never sets it
        restores the same value."""
        instructions = [
            ([90.0], "Tz"),
            ([], "BT"),
            ([], "ET"),
            ([], "q"),
            ([], "Q"),
            ([90.0], "Tz"),  # still redundant -- nothing changed it
        ]
        out = _eliminate_redundant_tz(instructions)
        assert _ops(out) == ["Tz", "BT", "ET", "q", "Q"]


class TestOptimizePositioningOpsMultiObjectAndTjArrays:
    def test_tj_array_operands_pass_through_untouched(self):
        """TJ is never a buffered op -- its (possibly multi-element,
        numeric-and-string) operand array must be inert to every pass,
        confirmed with real array contents rather than a placeholder."""
        import pikepdf

        array = pikepdf.Array([pikepdf.String(b"Hello"), -250, pikepdf.String(b"World")])
        instructions = [
            ([], "BT"),
            (["/F1", 12.0], "Tf"),
            ([array], "TJ"),
            ([], "ET"),
        ]
        out = optimize_positioning_ops(instructions)
        assert _ops(out) == ["BT", "Tf", "TJ", "ET"]
        assert out[2][0] == [array]

    def test_full_multi_object_stream_all_passes_combined(self):
        instructions = [
            ([], "BT"),
            (["/F1", 12.0], "Tf"),
            ([10.0, 0.0], "Td"),  # dead -- superseded before ET
            ([90.0], "Tz"),
            ([], "ET"),
            ([], "BT"),
            (["/F1", 12.0], "Tf"),  # dead -- superseded by /F2 below
            (["/F2", 12.0], "Tf"),
            ([5.0, 0.0], "Td"),
            ([1.0, 1.0], "Td"),
            ([90.0], "Tz"),  # redundant -- unchanged since above
            ([[]], "TJ"),
            ([], "ET"),
        ]
        out = optimize_positioning_ops(instructions)
        assert _ops(out) == ["BT", "Tf", "Tz", "ET", "BT", "Tf", "Td", "TJ", "ET"]
        second_bt_idx = _ops(out).index("Tf", 2)
        assert out[second_bt_idx][0] == ["/F2", 12.0]


class TestOptimizePositioningOpsRealPikepdfRoundTrip:
    def test_round_trips_through_real_content_stream_parse_unparse(self):
        """Slower end-to-end check against pikepdf's actual object types
        (Operator/String/Array/Name), not hand-built plain tuples -- the
        earlier example that motivated this whole pass, reduced to a
        minimal reproducer: a dead Tm/Td/Tf chain with no show before
        ET, followed by a live text object."""
        import pikepdf

        content = (
            b"BT "
            b"1 0 0 1 315.378 356.648 Tm "
            b"-186.965 -14.446 Td "
            b"/F44 11.9552 Tf "
            b"148.603 -29.341 Td "
            b"ET "
            b"BT /F27 11.9552 Tf 10.0 0.0 Td 3.0 1.0 Td (hi) Tj ET"
        )
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(300, 300))
        stream = pdf.make_stream(content)

        instructions = list(pikepdf.parse_content_stream(stream))
        out = optimize_positioning_ops(instructions)

        raw = pikepdf.unparse_content_stream(out)
        stream.write(raw)
        reparsed = list(pikepdf.parse_content_stream(stream))
        ops = [str(op) for _, op in reparsed]

        # First object: Tm/Td chain is dead (nothing shown) and dropped;
        # its lone Tf survives -- _drop_dead_tf can't prove it dead, since
        # a later text object with no Tf of its own would inherit it.
        assert ops == ["BT", "Tf", "ET", "BT", "Tf", "Td", "Tj", "ET"]
        assert str(reparsed[1][0][0]) == "/F44"
        tf_operands = reparsed[4][0]
        assert str(tf_operands[0]) == "/F27"
        td_operands = reparsed[5][0]
        assert [float(x) for x in td_operands] == pytest.approx([13.0, 1.0])
        tj_operands = reparsed[6][0]
        assert bytes(tj_operands[0]) == b"hi"


class TestIdempotency:
    """optimize_positioning_ops should be a fixed point: running it a
    second time on its own output must change nothing further. Reuses
    representative instruction shapes from earlier test classes rather
    than inventing new fixtures, so this doubles as a cross-check that
    those shapes were exercised correctly the first time."""

    @pytest.mark.parametrize(
        "instructions",
        [
            [],
            [([1, 0, 0, 1, 1.0, 2.0], "cm"), ([1, 0, 0, 1, 3.0, 4.0], "cm")],
            [([], "BT"), (["/F1", 12.0], "Tf"), ([[]], "TJ"), ([], "ET")],
            [
                ([], "BT"),
                ([1, 0, 0, 1, 315.378, 356.648], "Tm"),
                ([-186.965, -14.446], "Td"),
                (["/F44", 11.9552], "Tf"),
                ([148.603, -29.341], "Td"),
                (["/F42", 7.9701], "Tf"),
                ([5.633, 4.936], "Td"),
                (["/F44", 11.9552], "Tf"),
                ([17.73, 8.088], "Td"),
                ([], "ET"),
            ],
            [
                ([], "BT"),
                (["/F44", 11.9552], "Tf"),
                ([-128.265, -29.048], "Td"),
                (["/F27", 11.9552], "Tf"),
                ([10.898, 0.0], "Td"),
                ([13.621, 8.088], "Td"),
                ([[]], "TJ"),
                ([], "ET"),
            ],
            [
                ([], "BT"),
                ([5.0, 5.0], "Td"),
                ([], "q"),
                ([1.0, 1.0], "Td"),
                ([[]], "TJ"),
                ([], "Q"),
                ([], "ET"),
            ],
            [
                ([90.0], "Tz"),
                ([], "BT"),
                (["/F1", 12.0], "Tf"),
                (["/F1", 12.0], "Tf"),
                ([5.0, 0.0], "Td"),
                ([1.0, 1.0], "Td"),
                ([90.0], "Tz"),
                ([[]], "TJ"),
                ([], "ET"),
            ],
        ],
    )
    def test_second_pass_is_a_no_op(self, instructions):
        once = optimize_positioning_ops(instructions)
        twice = optimize_positioning_ops(once)
        assert twice == once


class TestDropDeadTfAggressiveModeDefaultUnchanged:
    def test_default_is_conservative_trailing_tf_before_et_kept(self):
        """aggressive_tf defaults to False -- a Tf immediately before ET
        with nothing shown must still be KEPT by default, exactly as
        before this flag existed."""
        instructions = [
            ([], "BT"),
            (["/F1", 12.0], "Tf"),
            ([[]], "TJ"),
            ([], "ET"),
            ([], "BT"),
            (["/F2", 12.0], "Tf"),  # nothing shows before ET -- dead only in aggressive mode
            ([], "ET"),
        ]
        out = _drop_dead_tf(instructions)
        assert _ops(out) == ["BT", "Tf", "TJ", "ET", "BT", "Tf", "ET"]


class TestDropDeadTfAggressiveMode:
    def test_trailing_tf_before_et_dropped_when_nothing_shows_in_later_text_object(self):
        """aggressive_tf=True: a Tf right before ET, with a LATER BT/ET
        text object that never sets its own Tf and never shows either,
        is provably dead -- BT/ET no longer block the liveness check."""
        instructions = [
            ([], "BT"),
            (["/F1", 12.0], "Tf"),
            ([], "ET"),
            ([], "BT"),
            ([], "ET"),
        ]
        out = _drop_dead_tf(instructions, aggressive_tf=True)
        assert _ops(out) == ["BT", "ET", "BT", "ET"]

    def test_trailing_tf_before_et_kept_when_later_text_object_shows_with_no_tf_of_its_own(self):
        """The whole point of aggressive_tf's danger zone: a later BT
        with no Tf of its own inherits the font from before ET. If that
        later text object actually shows something, the earlier Tf is
        alive and must be kept even in aggressive mode."""
        instructions = [
            ([], "BT"),
            (["/F1", 12.0], "Tf"),
            ([], "ET"),
            ([], "BT"),
            ([[]], "TJ"),  # shown under inherited /F1 -- Tf is alive
            ([], "ET"),
        ]
        out = _drop_dead_tf(instructions, aggressive_tf=True)
        assert _ops(out) == ["BT", "Tf", "ET", "BT", "TJ", "ET"]

    def test_tf_superseded_across_bt_et_dropped_in_aggressive_mode(self):
        """A Tf superseded by a later Tf in a SUBSEQUENT text object,
        with nothing shown in between -- only provable dead when BT/ET
        aren't barriers."""
        instructions = [
            ([], "BT"),
            (["/F1", 12.0], "Tf"),
            ([], "ET"),
            ([], "BT"),
            (["/F2", 12.0], "Tf"),
            ([[]], "TJ"),
            ([], "ET"),
        ]
        out = _drop_dead_tf(instructions, aggressive_tf=True)
        assert _ops(out) == ["BT", "ET", "BT", "Tf", "TJ", "ET"]
        assert out[3][0] == ["/F2", 12.0]

    def test_q_still_a_barrier_in_aggressive_mode(self):
        """aggressive_tf only relaxes BT/ET -- q/Q remain unconditional
        barriers either way, since Tf really is graphics state saved
        and restored by q/Q."""
        instructions = [
            (["/F1", 12.0], "Tf"),
            ([], "q"),
            (["/F2", 12.0], "Tf"),
            ([[]], "TJ"),
            ([], "Q"),
            ([[]], "TJ"),  # shown under /F1, restored by Q
        ]
        out = _drop_dead_tf(instructions, aggressive_tf=True)
        assert _ops(out) == ["Tf", "q", "Tf", "TJ", "Q", "TJ"]


class TestOptimizePositioningOpsAggressiveTfIntegration:
    def test_aggressive_tf_flag_threads_through_to_full_pipeline(self):
        pass  # superseded by TestOptimizePositioningOpsAggressiveTfIntegrationCorrected

    def test_aggressive_tf_is_still_idempotent(self):
        instructions = [
            ([], "BT"),
            (["/F1", 12.0], "Tf"),
            ([], "ET"),
            ([], "BT"),
            (["/F2", 12.0], "Tf"),
            ([[]], "TJ"),
            ([], "ET"),
        ]
        once = optimize_positioning_ops(instructions, aggressive_tf=True)
        twice = optimize_positioning_ops(once, aggressive_tf=True)
        assert twice == once


class TestEliminateRedundantColor:
    def test_redundant_g_dropped(self):
        instructions = [([0.0], "g"), ([0.0], "g"), ([], "f")]
        out = _eliminate_redundant_color(instructions)
        assert _ops(out) == ["g", "f"]

    def test_changed_g_kept(self):
        instructions = [([0.0], "g"), ([1.0], "g")]
        out = _eliminate_redundant_color(instructions)
        assert _ops(out) == ["g", "g"]

    def test_g_and_capital_g_tracked_independently(self):
        """Fill (g) and stroke (G) gray are independent -- setting one
        must never suppress a first occurrence of the other."""
        instructions = [([0.0], "g"), ([0.0], "G")]
        out = _eliminate_redundant_color(instructions)
        assert _ops(out) == ["g", "G"]

    def test_rg_redundant_dropped(self):
        instructions = [([1.0, 0.0, 0.0], "rg"), ([1.0, 0.0, 0.0], "rg")]
        out = _eliminate_redundant_color(instructions)
        assert _ops(out) == ["rg"]

    def test_rg_and_g_tracked_independently(self):
        """A g op must not be considered redundant against a same-valued
        rg component or vice versa -- different color space families."""
        instructions = [([0.0], "g"), ([0.0, 0.0, 0.0], "rg")]
        out = _eliminate_redundant_color(instructions)
        assert _ops(out) == ["g", "rg"]

    def test_first_occurrence_always_kept_no_default_assumed(self):
        """Unlike Tz, color ops have no spec-default starting value to
        assume -- the very first g/rg/etc. must always survive."""
        instructions = [([0.0], "g")]
        out = _eliminate_redundant_color(instructions)
        assert _ops(out) == ["g"]

    def test_k_redundant_across_q_q_still_dropped(self):
        """Color redundancy persists across q/Q, unlike Tf -- a value
        that comes back to what it already was is provably a no-op
        regardless of what happened inside the block."""
        instructions = [
            ([0.0, 0.0, 0.0, 1.0], "k"),
            ([], "q"),
            ([0.0, 0.0, 0.0, 0.5], "k"),
            ([], "Q"),
            ([0.0, 0.0, 0.0, 1.0], "k"),  # redundant -- back to the outer value
        ]
        out = _eliminate_redundant_color(instructions)
        assert _ops(out) == ["k", "q", "k", "Q"]

    def test_malformed_operands_kept_defensively(self):
        instructions = [([None], "g")]
        out = _eliminate_redundant_color(instructions)
        assert _ops(out) == ["g"]

    def test_wrong_arity_operands_kept_defensively(self):
        """rg with only 2 operands (malformed) must not raise or be
        silently treated as valid -- kept untouched."""
        instructions = [([1.0, 0.0], "rg")]
        out = _eliminate_redundant_color(instructions)
        assert _ops(out) == ["rg"]


class TestDropDeadQBlocks:
    def test_empty_q_block_with_no_paint_dropped_entirely(self):
        instructions = [
            ([], "q"),
            ([1, 0, 0, 1, 110.854, 113.332], "cm"),
            ([[]], "d"),
            ([0], "J"),
            ([0.398], "w"),
            ([], "Q"),
        ]
        out = _drop_dead_q_blocks(instructions)
        assert out == []

    def test_q_block_with_fill_survives_whole(self):
        instructions = [
            ([], "q"),
            ([1, 0, 0, 1, 0.0, 0.0], "cm"),
            ([0, 0, 10, 10], "re"),
            ([], "f"),
            ([], "Q"),
        ]
        out = _drop_dead_q_blocks(instructions)
        assert _ops(out) == ["q", "cm", "re", "f", "Q"]

    def test_nested_dead_block_inside_live_block_only_inner_survives_as_dead(self):
        """Outer block paints directly (not via the nested block) -- the
        nested dead q..Q inside it is NOT independently dropped by this
        pass (it only matches spans at the point they close), but the
        outer span survives since it contains a paint op overall."""
        instructions = [
            ([], "q"),
            ([], "q"),
            ([1, 0, 0, 1, 0.0, 0.0], "cm"),  # inner: no paint
            ([], "Q"),
            ([0, 0, 10, 10], "re"),
            ([], "f"),
            ([], "Q"),
        ]
        out = _drop_dead_q_blocks(instructions)
        # Inner q..Q (indices 1-3) has no paint of its own -> dropped;
        # outer survives since 'f' is present overall.
        assert _ops(out) == ["q", "re", "f", "Q"]

    def test_nested_paint_keeps_outer_span_alive_too(self):
        """Paint occurring only inside a NESTED q..Q must keep the OUTER
        span alive as well -- the outer block genuinely painted
        something via its child, even though nothing painted at the
        outer level directly."""
        instructions = [
            ([], "q"),
            ([1, 0, 0, 1, 0.0, 0.0], "cm"),
            ([], "q"),
            ([0, 0, 10, 10], "re"),
            ([], "f"),
            ([], "Q"),
            ([], "Q"),
        ]
        out = _drop_dead_q_blocks(instructions)
        assert _ops(out) == ["q", "cm", "q", "re", "f", "Q", "Q"]

    def test_multiple_sibling_dead_blocks_both_dropped(self):
        instructions = [
            ([], "q"),
            ([1, 0, 0, 1, 0.0, 0.0], "cm"),
            ([], "Q"),
            ([0.0], "g"),
            ([], "q"),
            ([0.398], "w"),
            ([], "Q"),
        ]
        out = _drop_dead_q_blocks(instructions)
        assert _ops(out) == ["g"]

    def test_dead_block_between_live_ones_only_middle_dropped(self):
        instructions = [
            ([], "q"),
            ([0, 0, 10, 10], "re"),
            ([], "f"),
            ([], "Q"),
            ([], "q"),
            ([1, 0, 0, 1, 0.0, 0.0], "cm"),  # dead
            ([], "Q"),
            ([], "q"),
            ([0, 0, 5, 5], "re"),
            ([], "S"),
            ([], "Q"),
        ]
        out = _drop_dead_q_blocks(instructions)
        assert _ops(out) == ["q", "re", "f", "Q", "q", "re", "S", "Q"]

    def test_unmatched_q_left_open_passes_through_untouched(self):
        instructions = [([], "q"), ([1, 0, 0, 1, 0.0, 0.0], "cm")]
        out = _drop_dead_q_blocks(instructions)
        assert out == instructions

    def test_unmatched_q_with_paint_inside_still_untouched(self):
        """A q with no matching Q anywhere -- never closes, so this pass
        (which only ever acts at Q) must leave it completely alone."""
        instructions = [([], "q"), ([0, 0, 10, 10], "re"), ([], "f")]
        out = _drop_dead_q_blocks(instructions)
        assert out == instructions

    def test_stray_q_close_without_matching_open_passes_through(self):
        instructions = [([0.0], "g"), ([], "Q")]
        out = _drop_dead_q_blocks(instructions)
        assert _ops(out) == ["g", "Q"]

    def test_text_show_counts_as_paint(self):
        """Tj/TJ/'/" inside a q..Q block (unusual but legal) must count
        as paint -- text is visible content just like a filled path."""
        instructions = [([], "q"), ([[]], "TJ"), ([], "Q")]
        out = _drop_dead_q_blocks(instructions)
        assert _ops(out) == ["q", "TJ", "Q"]

    def test_do_and_sh_count_as_paint(self):
        instructions = [
            ([], "q"),
            (["/Im0"], "Do"),
            ([], "Q"),
            ([], "q"),
            (["/Sh0"], "sh"),
            ([], "Q"),
        ]
        out = _drop_dead_q_blocks(instructions)
        assert _ops(out) == ["q", "Do", "Q", "q", "sh", "Q"]


class TestOptimizePositioningOpsRealWorldQBlockShapeFullCollapse:
    pass  # superseded by TestOptimizePositioningOpsRealWorldQBlockShapeFullCollapseCorrected


class TestIdempotencyExtended:
    def test_color_and_dead_q_passes_are_idempotent(self):
        instructions = [
            ([0.0], "g"),
            ([0.0], "g"),
            ([], "q"),
            ([1, 0, 0, 1, 0.0, 0.0], "cm"),
            ([], "Q"),
            ([0, 0, 10, 10], "re"),
            ([], "f"),
        ]
        once = optimize_positioning_ops(instructions)
        twice = optimize_positioning_ops(once)
        assert twice == once


class TestEliminateRedundantColorQBlockScoping:
    def test_value_set_inside_q_block_does_not_leak_past_matching_q(self):
        """The bug this guards against: a color value changed INSIDE a
        q..Q block must not be treated as 'current' for comparisons
        AFTER the matching Q -- Q reverts it. Without q/Q-scoped
        tracking, the outer k below would be wrongly compared against
        the INNER block's value (0.3) instead of the correct restored
        value (0.9), and incorrectly kept as 'different' when it's
        actually identical to what was current before q."""
        instructions = [
            ([0.0, 0.0, 0.0, 0.9], "k"),
            ([], "q"),
            ([0.0, 0.0, 0.0, 0.3], "k"),
            ([], "Q"),
            ([0.0, 0.0, 0.0, 0.9], "k"),  # must be seen as redundant
        ]
        out = _eliminate_redundant_color(instructions)
        assert _ops(out) == ["k", "q", "k", "Q"]

    def test_nested_q_blocks_restore_correctly_at_each_level(self):
        instructions = [
            ([0.0], "g"),
            ([], "q"),
            ([0.5], "g"),
            ([], "q"),
            ([0.9], "g"),
            ([], "Q"),
            ([0.5], "g"),  # redundant -- matches inner-q's own current
            ([], "Q"),
            ([0.0], "g"),  # redundant -- matches outermost value
        ]
        out = _eliminate_redundant_color(instructions)
        assert _ops(out) == ["g", "q", "g", "q", "g", "Q", "Q"]

    def test_stray_q_close_without_matching_open_does_not_crash(self):
        instructions = [([0.0], "g"), ([], "Q"), ([0.0], "g")]
        out = _eliminate_redundant_color(instructions)
        assert _ops(out) == ["g", "Q"]


class TestDropEmptyBtEt:
    def test_empty_bt_et_dropped(self):
        instructions = [([], "BT"), ([], "ET")]
        out = _drop_empty_bt_et(instructions)
        assert out == []

    def test_non_empty_bt_et_kept(self):
        instructions = [([], "BT"), ([[]], "TJ"), ([], "ET")]
        out = _drop_empty_bt_et(instructions)
        assert _ops(out) == ["BT", "TJ", "ET"]

    def test_multiple_empty_pairs_all_dropped(self):
        instructions = [
            ([0.0], "g"),
            ([], "BT"),
            ([], "ET"),
            ([], "BT"),
            ([], "ET"),
            ([0.0], "G"),
        ]
        out = _drop_empty_bt_et(instructions)
        assert _ops(out) == ["g", "G"]

    def test_adjacent_empty_and_nonempty_pairs_only_empty_dropped(self):
        instructions = [
            ([], "BT"),
            ([], "ET"),
            ([], "BT"),
            ([[]], "Tj"),
            ([], "ET"),
        ]
        out = _drop_empty_bt_et(instructions)
        assert _ops(out) == ["BT", "Tj", "ET"]

    def test_unterminated_trailing_bt_kept(self):
        """A BT with nothing after it at all (no ET, malformed/truncated
        input) must not be dropped -- there's no ET to pair it with."""
        instructions = [([0.0], "g"), ([], "BT")]
        out = _drop_empty_bt_et(instructions)
        assert _ops(out) == ["g", "BT"]

    def test_empty_instructions_list(self):
        assert _drop_empty_bt_et([]) == []


class TestOptimizePositioningOpsRealWorldQBlockShapeFullCollapseV2:
    def test_full_reported_shape_now_collapses_completely(self):
        """Same shape as the earlier real-world regression test, now
        with _drop_empty_bt_et added: the two now-empty text objects
        (left behind once dead-Tf strips their only content) are
        themselves dropped too, so this collapses all the way to just
        the two color ops."""
        instructions = [
            ([0.0], "g"),
            ([0.0], "G"),
            ([], "BT"),
            ([0.0], "g"),
            ([0.0], "G"),
            (["/F27", 11.9552], "Tf"),
            ([0.0], "g"),
            ([0.0], "G"),
            ([], "ET"),
            ([], "q"),
            ([1, 0, 0, 1, 110.854, 113.332], "cm"),
            ([[]], "d"),
            ([0], "J"),
            ([0.398], "w"),
            ([], "Q"),
            ([], "BT"),
            ([0.0], "g"),
            ([0.0], "G"),
            ([0.0], "g"),
            ([0.0], "G"),
            ([0.0], "g"),
            ([0.0], "G"),
            ([], "ET"),
        ]
        out = optimize_positioning_ops(instructions, aggressive_tf=True)
        # Nothing in this ENTIRE snippet ever paints (no f/S/Do/text-show
        # anywhere) -- with _drop_dead_state_stores AND
        # _drop_paintless_stream both now in the pipeline, this collapses
        # to nothing at all, not just the emptied text objects.
        assert out == []


class TestEliminateRedundantColorStrayQEdgeCase:
    def test_stray_q_with_empty_stack_and_no_prior_color_leaves_out_unchanged(self):
        """A Q reached with an empty q_stack (no matching q at all) must
        take the `current` (unchanged) branch of the ternary, not crash
        and not alter tracking state -- covers the previously-uncovered
        false side of `q_stack.pop() if q_stack else current`."""
        instructions = [([], "Q"), ([0.0], "g"), ([0.0], "g")]
        out = _eliminate_redundant_color(instructions)
        assert _ops(out) == ["Q", "g"]


# Superseded expectations, corrected for _drop_empty_bt_et joining the
# pipeline (see class docstrings for why the old expected values are
# now stale, not wrong-then and not a regression).


class TestOptimizePositioningOpsRealWorldQBlockShapeFullCollapseCorrected:
    def test_full_reported_shape_collapses_to_nothing(self):
        """Same shape as
        TestOptimizePositioningOpsRealWorldQBlockShapeFullCollapse's
        original test -- now that _drop_empty_bt_et runs too, the two
        text objects (already emptied by dead-Tf) are themselves
        dropped, so this collapses all the way to just the two color
        ops, matching TestOptimizePositioningOpsRealWorldQBlockShapeFullCollapseV2
        added alongside it."""
        instructions = [
            ([0.0], "g"),
            ([0.0], "G"),
            ([], "BT"),
            ([0.0], "g"),
            ([0.0], "G"),
            (["/F27", 11.9552], "Tf"),
            ([0.0], "g"),
            ([0.0], "G"),
            ([], "ET"),
            ([], "q"),
            ([1, 0, 0, 1, 110.854, 113.332], "cm"),
            ([[]], "d"),
            ([0], "J"),
            ([0.398], "w"),
            ([], "Q"),
            ([], "BT"),
            ([0.0], "g"),
            ([0.0], "G"),
            ([0.0], "g"),
            ([0.0], "G"),
            ([0.0], "g"),
            ([0.0], "G"),
            ([], "ET"),
        ]
        out = optimize_positioning_ops(instructions, aggressive_tf=True)
        # See TestOptimizePositioningOpsRealWorldQBlockShapeFullCollapseV2 --
        # nothing here ever paints, so the whole thing is provably dead.
        assert out == []


class TestOptimizePositioningOpsAggressiveTfIntegrationCorrected:
    def test_aggressive_tf_flag_threads_through_to_full_pipeline(self):
        """Same shape as
        TestOptimizePositioningOpsAggressiveTfIntegration's original
        test. No paint op appears ANYWHERE in this snippet (no
        Tj/TJ/etc. at all), so _drop_paintless_stream now collapses the
        whole thing to nothing in BOTH conservative and aggressive
        modes -- aggressive_tf's BT/ET-barrier relaxation only matters
        for reasoning _drop_dead_tf itself does internally, and never
        gets a chance to run here since the paintless short-circuit
        fires first."""
        instructions = [
            ([], "BT"),
            (["/F1", 12.0], "Tf"),
            ([], "ET"),
            ([], "BT"),
            ([1, 0, 0, 1, 315.378, 356.648], "Tm"),
            ([-186.965, -14.446], "Td"),
            (["/F44", 11.9552], "Tf"),
            ([148.603, -29.341], "Td"),
            ([], "ET"),
        ]
        conservative = optimize_positioning_ops(instructions)
        assert conservative == []

        aggressive = optimize_positioning_ops(instructions, aggressive_tf=True)
        assert aggressive == []


class TestDropDeadTfStrayQEmptyStack:
    def test_stray_q_with_empty_stack_and_no_pending_tf(self):
        """A Q reached with an empty q_stack (no matching q) and no
        pending Tf at the time -- covers the `else None` side of
        `q_stack.pop() if q_stack else None`, distinct from
        TestDropDeadTfQBlockScoping's stray-Q test (which has a
        pending Tf active when Q hits)."""
        instructions = [([], "Q"), ([[]], "TJ")]
        out = _drop_dead_tf(instructions)
        assert _ops(out) == ["Q", "TJ"]


class TestDropDeadTfQBlockScopingReapplied:
    def test_tf_set_inside_q_block_with_no_show_before_q_is_dropped(self):
        """A Tf set INSIDE a q..Q block, never shown before the matching
        Q closes the block, is dead -- Q reverts it before it's ever
        used, exactly like a trailing dead Tf at end-of-stream. This is
        the line-234 case: `del out[pending_tf_index]` inside the Q
        handler's dead-pending-Tf branch."""
        instructions = [
            ([], "q"),
            (["/F2", 12.0], "Tf"),  # dead -- nothing shows before Q
            ([], "Q"),
            ([[]], "TJ"),
        ]
        out = _drop_dead_tf(instructions)
        assert _ops(out) == ["q", "Q", "TJ"]


class TestDropDeadStateStores:
    def test_overwritten_stroke_gray_dropped_even_with_different_value(self):
        """The real-world shape: 1 G immediately followed by 0 G, with
        nothing painted in between -- dead regardless of differing
        values, which _eliminate_redundant_color alone can't catch."""
        instructions = [([1.0], "G"), ([0.0], "G"), ([], "f")]
        out = _drop_dead_state_stores(instructions)
        assert _ops(out) == ["G", "f"]
        assert out[0][0] == [0.0]

    def test_nonstroke_and_stroke_color_tracked_independently(self):
        instructions = [([0.0], "g"), ([0.0], "G"), ([], "f")]
        out = _drop_dead_state_stores(instructions)
        assert _ops(out) == ["g", "G", "f"]

    def test_different_families_within_color_still_share_one_family_slot(self):
        """g and rg are different operators but the SAME family
        (nonstroke color) -- a g immediately superseded by an rg, with
        nothing painted between them, is dead."""
        instructions = [([0.5], "g"), ([1.0, 0.0, 0.0], "rg"), ([], "f")]
        out = _drop_dead_state_stores(instructions)
        assert _ops(out) == ["rg", "f"]

    def test_line_width_join_cap_dash_each_independent(self):
        instructions = [
            ([5.0], "w"),
            ([1], "j"),
            ([0], "J"),
            ([[], 0], "d"),
            ([], "S"),
        ]
        out = _drop_dead_state_stores(instructions)
        assert _ops(out) == ["w", "j", "J", "d", "S"]

    def test_paint_confirms_all_pending_families_alive(self):
        instructions = [
            ([0.0], "g"),
            ([5.0], "w"),
            ([], "f"),  # confirms BOTH g and w alive
            ([0.0], "g"),  # new pending, unrelated to the confirmed one
        ]
        out = _drop_dead_state_stores(instructions)
        # Trailing 'g' has nothing after it -- dead at end-of-stream.
        assert _ops(out) == ["g", "w", "f"]

    def test_trailing_dead_store_with_nothing_after_is_dropped(self):
        """Both are dead: the first is superseded by the second with no
        paint in between, and the second is itself trailing -- nothing
        ever paints under it either, so it too is dead at end-of-stream,
        exactly like _drop_dead_tf's trailing-Tf rule."""
        instructions = [([1.0], "w"), ([2.0], "w")]
        out = _drop_dead_state_stores(instructions)
        assert out == []

    def test_last_setter_in_family_survives_when_paint_follows(self):
        """Same shape, but with a paint op after -- NOW the second w is
        confirmed alive, while the first is still dead (superseded)."""
        instructions = [([1.0], "w"), ([2.0], "w"), ([], "S")]
        out = _drop_dead_state_stores(instructions)
        assert _ops(out) == ["w", "S"]
        assert out[0][0] == [2.0]

    def test_dead_store_inside_q_block_dropped_at_q(self):
        """A store made INSIDE a q..Q block, never painted under before
        the matching Q, is dead -- Q reverts it before use."""
        instructions = [
            ([], "q"),
            ([0.0], "g"),  # dead -- nothing paints before Q
            ([], "Q"),
            ([], "f"),
        ]
        out = _drop_dead_state_stores(instructions)
        assert _ops(out) == ["q", "Q", "f"]

    def test_pending_before_q_survives_and_confirmed_by_paint_after_q(self):
        """An unconfirmed store from before q must still be provably
        alive if something paints AFTER the matching Q -- Q restores
        exactly that state, so the paint confirms it."""
        instructions = [
            ([0.0], "g"),
            ([], "q"),
            ([1, 0, 0, 1, 0.0, 0.0], "cm"),
            ([], "Q"),
            ([], "f"),
        ]
        out = _drop_dead_state_stores(instructions)
        assert _ops(out) == ["g", "q", "cm", "Q", "f"]

    def test_outer_pending_restored_after_inner_dead_store_dropped(self):
        instructions = [
            ([0.0], "g"),
            ([], "q"),
            ([1.0], "g"),  # dead -- nothing paints before Q
            ([], "Q"),
            ([], "f"),  # confirms outer g (0.0) alive
        ]
        out = _drop_dead_state_stores(instructions)
        assert _ops(out) == ["g", "q", "Q", "f"]
        assert out[0][0] == [0.0]

    def test_stray_q_close_without_matching_open_degrades_gracefully(self):
        """A stray Q (no matching q) still clears whatever's pending --
        same defensive posture _drop_dead_tf already takes for Tf: a Q
        is treated as reverting pending state regardless of whether
        there's a real q_stack entry to restore."""
        instructions = [([0.0], "g"), ([], "Q"), ([], "f")]
        out = _drop_dead_state_stores(instructions)
        assert _ops(out) == ["Q", "f"]

    def test_scn_sets_value_only_so_preceding_space_setter_stays(self):
        """scn sets a value in the current color space; the g that chose
        that space is still used by the fill (ISO 32000-2 8.6.8)."""
        instructions = [([0.0], "g"), (["/P1"], "scn"), ([], "f")]
        out = _drop_dead_state_stores(instructions)
        assert _ops(out) == ["g", "scn", "f"]

    def test_space_setter_superseded_by_cs_is_dropped(self):
        instructions = [([0.0], "g"), (["/Pattern"], "cs"), (["/P1"], "scn"), ([], "f")]
        out = _drop_dead_state_stores(instructions)
        assert _ops(out) == ["cs", "scn", "f"]

    def test_empty_instructions_list(self):
        assert _drop_dead_state_stores([]) == []


class TestOptimizePositioningOpsRealWorldColorDashJoinShape:
    def test_uploaded_stream_shape_collapses(self):
        """Reproduces the reported real-world shape, plus a trailing
        paint to confirm survivors are exactly the LAST setter per
        family. Note G and RG share the SAME family (stroke_color, like
        g/rg/k share nonstroke_color) -- so the second G (0.0) is ALSO
        superseded and dropped once the later RG comes along with
        nothing painted in between, leaving RG as stroke_color's sole
        survivor here, not G."""
        instructions = [
            ([1.0], "g"),
            ([5.0], "w"),
            ([1], "j"),
            ([1.0], "G"),
            ([0.0], "G"),
            ([0.0], "g"),
            ([0.0, 0.0, 1.0], "RG"),
            ([], "S"),
        ]
        out = _drop_dead_state_stores(instructions)
        assert _ops(out) == ["w", "j", "g", "RG", "S"]

    def test_uploaded_stream_shape_with_no_paint_at_all_collapses_fully(self):
        """The literal reported snippet, with no paint op anywhere --
        genuinely everything is dead, collapsing entirely."""
        instructions = [
            ([1.0], "g"),
            ([5.0], "w"),
            ([1], "j"),
            ([1.0], "G"),
            ([0.0], "G"),
            ([0.0], "g"),
            ([0.0, 0.0, 1.0], "RG"),
        ]
        out = _drop_dead_state_stores(instructions)
        assert out == []


class TestDropPaintlessStream:
    def test_pure_cm_chain_with_no_paint_collapses_to_nothing(self):
        """The exact reported real-world shape: a run of cm's with no
        q/Q, no paint, nothing else -- the whole page's content stream
        has zero observable effect."""
        instructions = [
            ([1, 0, 0, 1, 110.854, 556.303], "cm"),
            ([1, 0, 0, 1, 128.239, 0.0], "cm"),
            ([1, 0, 0, 1, 128.238, 0.0], "cm"),
            ([1, 0, 0, 1, -256.477, -118.095], "cm"),
            ([1, 0, 0, 1, 128.239, 0.0], "cm"),
            ([1, 0, 0, 1, 128.238, 0.0], "cm"),
            ([1, 0, 0, 1, -367.331, -438.208], "cm"),
        ]
        out = _drop_paintless_stream(instructions)
        assert out == []

    def test_stream_with_a_single_paint_op_anywhere_survives_untouched(self):
        instructions = [
            ([1, 0, 0, 1, 0.0, 0.0], "cm"),
            ([0.0], "g"),
            ([0, 0, 10, 10], "re"),
            ([], "f"),
        ]
        out = _drop_paintless_stream(instructions)
        assert out == instructions

    def test_paint_deep_inside_q_blocks_still_counts(self):
        """No q/Q-awareness needed at all -- a single membership check
        anywhere in the stream is sufficient."""
        instructions = [
            ([], "q"),
            ([], "q"),
            ([], "q"),
            ([[]], "TJ"),
            ([], "Q"),
            ([], "Q"),
            ([], "Q"),
        ]
        out = _drop_paintless_stream(instructions)
        assert out == instructions

    def test_empty_instructions_list(self):
        assert _drop_paintless_stream([]) == []

    def test_bt_et_with_no_show_and_no_other_paint_collapses(self):
        instructions = [
            ([], "BT"),
            (["/F1", 12.0], "Tf"),
            ([10.0, 0.0], "Td"),
            ([], "ET"),
        ]
        out = _drop_paintless_stream(instructions)
        assert out == []


class TestOptimizePositioningOpsPaintlessShortCircuit:
    def test_full_pipeline_short_circuits_on_paintless_input(self):
        instructions = [
            ([1, 0, 0, 1, 110.854, 556.303], "cm"),
            ([1, 0, 0, 1, 128.239, 0.0], "cm"),
            ([1, 0, 0, 1, 128.238, 0.0], "cm"),
            ([1, 0, 0, 1, -256.477, -118.095], "cm"),
        ]
        out = optimize_positioning_ops(instructions)
        assert out == []

    def test_full_pipeline_untouched_when_paint_present(self):
        instructions = [
            ([1, 0, 0, 1, 0.0, 0.0], "cm"),
            ([0.0], "g"),
            ([0.0], "g"),  # redundant -- still exercises the other passes
            ([0, 0, 10, 10], "re"),
            ([], "f"),
        ]
        out = optimize_positioning_ops(instructions)
        assert _ops(out) == ["cm", "g", "re", "f"]


class TestCollapseTextPositioningKeepsLeading:
    """TD sets the leading as well as the position; the leading persists."""

    def test_td_dropped_at_et_leaves_its_leading(self):
        instructions = [([], "BT"), ([0, -14], "TD"), ([], "ET")]
        out = _collapse_text_positioning(instructions)
        assert out == [([], "BT"), ([14], "TL"), ([], "ET")]

    def test_td_dropped_before_tm_leaves_its_leading(self):
        instructions = [
            ([], "BT"),
            ([0, -14], "TD"),
            ([1, 0, 0, 1, 50, 150], "Tm"),
            (["a"], "Tj"),
            ([], "ET"),
        ]
        out = _collapse_text_positioning(instructions)
        assert _ops(out) == ["BT", "TL", "Tm", "Tj", "ET"]
        assert out[1][0] == [14]

    def test_last_of_several_dropped_tds_sets_the_leading(self):
        instructions = [([], "BT"), ([0, -14], "TD"), ([3, -9], "TD"), ([], "ET")]
        out = _collapse_text_positioning(instructions)
        assert out[1] == ([9], "TL")

    def test_decimal_leading_keeps_its_type(self):
        from decimal import Decimal

        instructions = [([], "BT"), ([0, Decimal("-12.5")], "TD"), ([], "ET")]
        out = _collapse_text_positioning(instructions)
        assert out[1] == ([Decimal("12.5")], "TL")

    @pytest.mark.parametrize("operands", [[0, "x"], [0], [0, None]])
    def test_malformed_td_is_kept_rather_than_guessed(self, operands):
        instructions = [([], "BT"), (operands, "TD"), ([], "ET")]
        out = _collapse_text_positioning(instructions)
        assert out == instructions

    def test_bt_with_moves_still_buffered_keeps_the_leading(self):
        # An unterminated text object: the next BT ends it.
        instructions = [([], "BT"), ([0, -14], "TD"), ([], "BT"), (["a"], "Tj"), ([], "ET")]
        out = _collapse_text_positioning(instructions)
        assert _ops(out) == ["BT", "TL", "BT", "Tj", "ET"]

    def test_td_without_td_buffered_drops_silently(self):
        instructions = [([], "BT"), ([5, 5], "Td"), ([1, 0, 0, 1, 0, 0], "Tm"), ([], "ET")]
        out = _collapse_text_positioning(instructions)
        assert _ops(out) == ["BT", "ET"]


class TestCollapseTextPositioningFlushes:
    def test_t_star_flushes_a_buffered_tm(self):
        instructions = [
            ([], "BT"),
            ([1, 0, 0, 1, 50, 150], "Tm"),
            ([], "T*"),
            (["a"], "Tj"),
            ([], "ET"),
        ]
        out = _collapse_text_positioning(instructions)
        assert _ops(out) == ["BT", "Tm", "T*", "Tj", "ET"]

    @pytest.mark.parametrize("barrier", [([10], "TL"), ([], "q"), ([], "Q"), (["/X0"], "Do")])
    def test_leading_ops_flush_a_buffered_td(self, barrier):
        instructions = [([], "BT"), ([0, -14], "TD"), barrier, (["a"], "Tj"), ([], "ET")]
        out = _collapse_text_positioning(instructions)
        assert _ops(out) == ["BT", "TD", barrier[1], "Tj", "ET"]

    @pytest.mark.parametrize("barrier", [([10], "TL"), ([], "q"), (["/X0"], "Do")])
    def test_leading_ops_let_a_plain_td_through(self, barrier):
        # Td touches only the text matrix, which none of these reads or saves.
        instructions = [
            ([], "BT"),
            ([5, 5], "Td"),
            barrier,
            ([1, 1], "Td"),
            (["a"], "Tj"),
            ([], "ET"),
        ]
        out = _collapse_text_positioning(instructions)
        assert _ops(out) == ["BT", barrier[1], "Td", "Tj", "ET"]
        assert out[2][0] == pytest.approx([6, 6])


class TestDropDeadQBlocksKeepsOperatorPairs:
    @pytest.mark.parametrize(
        "inside",
        [
            [([], "BT")],
            [([], "ET")],
            [(["/Span"], "BMC")],
            [(["/P", "dict"], "BDC")],
            [([], "EMC")],
            [([], "EMC"), (["/Span"], "BMC")],  # nets to zero but opens with a close
        ],
    )
    def test_span_that_would_orphan_a_partner_is_kept(self, inside):
        instructions = [([], "q"), *inside, ([], "Q")]
        assert _drop_dead_q_blocks(instructions) == instructions

    def test_balanced_marked_content_without_paint_is_dropped(self):
        instructions = [([], "q"), (["/Span"], "BMC"), ([], "EMC"), ([], "Q")]
        assert _drop_dead_q_blocks(instructions) == []

    def test_imbalance_in_an_inner_span_keeps_the_outer(self):
        instructions = [([], "q"), ([], "q"), ([], "BT"), ([], "Q"), ([], "Q")]
        assert _drop_dead_q_blocks(instructions) == instructions
