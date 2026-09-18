# tests/operations/helpers/test_excise_text.py

import pikepdf
import pytest

from pdftl.operations.helpers.excise_text import (
    FontCache as _FontCache,
    rewrite_text_show as _rewrite_text_show,
    filter_show_elements as _filter_show_elements,
    glyph_should_delete as _glyph_should_delete,
)
from pdftl.operations.helpers.excise_types import ExciseRect, ExciseStats
from pdftl.operations.excise import excise_content
from pdftl.utils.graphics_state import GraphicsState


# ---------------------------------------------------------------------------
# Shared fixtures/helpers
# ---------------------------------------------------------------------------


def _make_pdf_with_content(content_bytes: bytes, page_size=(300, 300), resources=None):
    """Builds a one-page PDF with an arbitrary raw content stream body."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=page_size)
    page = pdf.pages[0]
    if resources is not None:
        page.Resources = resources
    page.Contents = pdf.make_stream(content_bytes)
    return pdf


def _get_content_ops(pdf):
    """Returns every operator string surviving in page 1's content
    stream after processing, in order."""
    instructions = pikepdf.parse_content_stream(pdf.pages[0].Contents)
    return [str(operator) for _, operator in instructions]


def _get_show_ops(pdf):
    """Returns [(operator_str, operands)] for Tj/TJ operators surviving
    in page 1's content stream, in order (Tm entries excluded)."""
    instructions = pikepdf.parse_content_stream(pdf.pages[0].Contents)
    return [(str(op), operands) for operands, op in instructions if str(op) in ("Tj", "TJ")]


def _get_text_ops(pdf):
    """Returns [(operator_str, operands)] for Tm/Tj/TJ operators
    surviving in page 1's content stream, in order."""
    instructions = pikepdf.parse_content_stream(pdf.pages[0].Contents)
    return [(str(op), operands) for operands, op in instructions if str(op) in ("Tm", "Tj", "TJ")]


def _simple_font_resources(width=500.0, first_char=65):
    """/F1 -- a simple (non-composite) font with a flat /Widths array."""
    font = pikepdf.Dictionary(
        {
            "/Subtype": pikepdf.Name("/TrueType"),
            "/FirstChar": first_char,
            "/Widths": pikepdf.Array([width, width]),  # covers 'A' (65) and 'B' (66)
        }
    )
    return pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})


def _composite_font_resources(width=500.0):
    """/F1 -- a Type0/Identity-H composite font with a /W range entry
    covering CIDs 0x0042 and 0x0043."""
    cid_font = pikepdf.Dictionary(
        {
            "/Subtype": pikepdf.Name("/CIDFontType2"),
            "/W": pikepdf.Array([0x0042, 0x0043, width]),
        }
    )
    font = pikepdf.Dictionary(
        {
            "/Subtype": pikepdf.Name("/Type0"),
            "/Encoding": pikepdf.Name("/Identity-H"),
            "/DescendantFonts": pikepdf.Array([cid_font]),
        }
    )
    return pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})


def _vertical_font_resources(w1y=-1000.0, vx=250.0, vy=880.0):
    """/F1 -- a Type0/Identity-V composite font with explicit /W2 metrics
    for CID 0x0042."""
    cid_font = pikepdf.Dictionary(
        {
            "/Subtype": pikepdf.Name("/CIDFontType2"),
            "/W2": pikepdf.Array([0x0042, 0x0043, w1y, vx, vy]),
        }
    )
    font = pikepdf.Dictionary(
        {
            "/Subtype": pikepdf.Name("/Type0"),
            "/Encoding": pikepdf.Name("/Identity-V"),
            "/DescendantFonts": pikepdf.Array([cid_font]),
        }
    )
    return pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})


# ---------------------------------------------------------------------------
# Text (glyph-level) deletion -- end-to-end via excise_content
#
# All content strings below deliberately omit an explicit position-setting
# Tm before the show operator (BT already resets the text matrix to
# identity) -- this keeps any Tm seen in the OUTPUT stream unambiguously
# ours (injected by rewrite_text_show), rather than confoundable with an
# author-supplied one.
# ---------------------------------------------------------------------------


class TestExciseContentTextDeletionSimpleFont:
    def test_partial_glyph_deletion_emits_tm_then_tj(self):
        # 'A' box: x in [0,50]; 'B' box: x in [50,100] (Tf 100, width 500).
        # Rect covers A's center (25) but not B's (75).
        content = b"BT /F1 100 Tf (AB) Tj ET"
        pdf = _make_pdf_with_content(content, resources=_simple_font_resources())
        excise_content(pdf, ["1(abs,0,0,50,120)"])
        text_ops = _get_text_ops(pdf)
        # Repositioning Tm past the deleted 'A', then a plain Tj for the
        # single surviving string 'B' -- no TJ array needed.
        # rewrite_text_show unconditionally also emits a restorative Tm
        # resetting Tlm back to its pre-deletion value, since it has no
        # lookahead into what follows in the stream -- but here nothing
        # does follow before ET, so optimize_positioning_ops's dead-
        # position-chain cleanup correctly prunes that trailing Tm as
        # dead (nothing shown before ET consumes it).
        assert [op for op, _ in text_ops] == ["Tm", "Tj"]
        tj_op, tj_operands = text_ops[1]
        assert bytes(tj_operands[0]) == b"B"

    def test_no_overlap_stays_plain_tj(self):
        content = b"BT /F1 100 Tf (AB) Tj ET"
        pdf = _make_pdf_with_content(content, resources=_simple_font_resources())
        excise_content(pdf, ["1(abs,500,500,600,600)"])
        show_ops = _get_show_ops(pdf)
        assert show_ops[0][0] == "Tj"  # untouched, no deletions
        assert bytes(show_ops[0][1][0]) == b"AB"

    def test_full_overlap_deletes_all_glyphs_emits_nothing(self):
        content = b"BT /F1 100 Tf (AB) Tj ET"
        pdf = _make_pdf_with_content(content, resources=_simple_font_resources())
        excise_content(pdf, ["1(abs,0,0,300,300)"])
        # Nothing survives -- no Tm, no Tj, no TJ at all for this show op.
        assert _get_text_ops(pdf) == []

    def test_kept_glyph_then_deleted_glyph_needs_no_tm(self):
        """'A' survives (outside rect), 'B' is deleted (inside rect) --
        the reverse order of the partial-deletion test above. Since the
        deletion happens AFTER everything that survives, no repositioning
        is needed at all -- 'A' is emitted as a plain Tj with no Tm."""
        content = b"BT /F1 100 Tf (AB) Tj ET"
        pdf = _make_pdf_with_content(content, resources=_simple_font_resources(width=500.0))
        excise_content(pdf, ["1(abs,55,0,300,300)"])  # covers B's center (75), not A's (25)
        text_ops = _get_text_ops(pdf)
        assert text_ops == [("Tj", text_ops[0][1])]
        assert bytes(text_ops[0][1][0]) == b"A"


class TestExciseContentTextDeletionCompositeFont:
    def test_two_byte_codes_partial_deletion_emits_tm_then_tj(self):
        # Two 2-byte CIDs (0x0042, 0x0043) shown via a hex string.
        content = b"BT /F1 100 Tf <00420043> Tj ET"
        pdf = _make_pdf_with_content(content, resources=_composite_font_resources())
        excise_content(pdf, ["1(abs,0,0,50,120)"])
        text_ops = _get_text_ops(pdf)
        # As in the simple-font case above, rewrite_text_show's
        # trailing restorative Tm is dead here (nothing shown before
        # ET) and is correctly pruned by optimize_positioning_ops.
        assert [op for op, _ in text_ops] == ["Tm", "Tj"]
        tj_op, tj_operands = text_ops[1]
        assert bytes(tj_operands[0]) == b"\x00\x43"  # surviving CID


class TestExciseContentTextDeletionVerticalFont:
    def test_vertical_glyph_full_deletion_emits_nothing(self):
        content = b"BT /F1 1000 Tf 1 0 0 1 0 0 Tm <00420043> Tj ET"
        pdf = _make_pdf_with_content(content, resources=_vertical_font_resources())
        excise_content(pdf, ["1(abs,-2000,-2000,2000,0)"])
        assert _get_show_ops(pdf) == []  # both glyphs deleted -- nothing survives


class TestExciseContentTextDeletionQuoteOperators:
    def test_quote_operator_rewritten(self):
        content = b"BT /F1 100 Tf 1 0 0 1 10 10 Tm 20 TL (A) Tj (AB) ' ET"
        pdf = _make_pdf_with_content(content, resources=_simple_font_resources())
        excise_content(pdf, ["1(abs,0,0,300,300)"])  # delete everything
        ops = _get_content_ops(pdf)
        # Every glyph in this stream is deleted, so nothing here paints
        # at all (no surviving Tj/TJ) -- the whole stream, including the
        # T*/Tf/Tm setup that only ever existed to support the now-gone
        # show ops, is provably inert and collapses via
        # optimize_content's whole-stream paintless pass.
        assert ops == []

    def test_dquote_operator_rewritten(self):
        content = b'BT /F1 100 Tf 1 0 0 1 10 10 Tm 1 2 (AB) " ET'
        pdf = _make_pdf_with_content(content, resources=_simple_font_resources())
        excise_content(pdf, ["1(abs,0,0,300,300)"])  # delete everything
        ops = _get_content_ops(pdf)
        # Same reasoning as test_quote_operator_rewritten -- nothing in
        # this stream paints once the glyph is deleted, so the whole
        # thing (including the Tw/Tc/T* prefix) collapses.
        assert ops == []


class TestExciseContentTextNoFontFallback:
    def test_tj_without_font_passes_through(self):
        """Tj shown with no preceding Tf -- font_name stays None, so the
        show operator must be passed through untouched rather than
        crashing on a missing font."""
        content = b"BT (A) Tj ET"
        pdf = _make_pdf_with_content(content, resources=_simple_font_resources())
        excise_content(pdf, ["1(abs,0,0,300,300)"])
        show_ops = _get_show_ops(pdf)
        assert show_ops[0][0] == "Tj"  # untouched fallback path


# ---------------------------------------------------------------------------
# _FontCache -- direct unit tests for branches not easily reachable through
# a full content-stream round trip (missing font name, no resources, etc.)
# ---------------------------------------------------------------------------


class TestFontCacheEdgeCases:
    def test_font_name_not_in_resources_returns_defaults(self):
        cache = _FontCache(_simple_font_resources())
        assert cache.is_composite("/NotThere") is False
        assert cache.is_vertical("/NotThere") is False
        assert cache.glyph_width("/NotThere", 65) == 0.0
        assert cache.vertical_metrics("/NotThere", 65) == (-1000.0, 0.0, 880.0)

    def test_no_resources_at_all(self):
        cache = _FontCache(None)
        assert cache.is_composite("/F1") is False
        assert cache.glyph_width("/F1", 65) == 0.0

    def test_resources_without_font_key(self):
        cache = _FontCache(pikepdf.Dictionary({}))
        assert cache.is_composite("/F1") is False

    def test_composite_and_vertical_and_widths_hit(self):
        cache = _FontCache(_composite_font_resources())
        assert cache.is_composite("/F1") is True
        assert cache.is_vertical("/F1") is False
        assert cache.glyph_width("/F1", 0x0042) == 500.0

    def test_vertical_metrics_hit(self):
        cache = _FontCache(_vertical_font_resources())
        assert cache.is_vertical("/F1") is True
        assert cache.vertical_metrics("/F1", 0x0042) == (-1000.0, 250.0, 880.0)

    def test_repeated_calls_reuse_cache(self):
        """Not directly observable from outside, but exercises the
        already-cached branch of each memoized method."""
        cache = _FontCache(_simple_font_resources())
        assert cache.is_composite("/F1") == cache.is_composite("/F1")
        assert cache.glyph_width("/F1", 65) == cache.glyph_width("/F1", 65)
        assert cache.is_vertical("/F1") == cache.is_vertical("/F1")


# ---------------------------------------------------------------------------
# _rewrite_text_show / _glyph_should_delete -- direct unit tests for
# branches not reachable through a normal parsed content stream.
# ---------------------------------------------------------------------------


class TestRewriteTextShowDirect:
    def test_no_font_name_set_passes_through(self):
        gs = GraphicsState()
        gs.text_matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        cache = _FontCache(_simple_font_resources())
        stats = ExciseStats()
        rect = ExciseRect(rect=[0, 0, 300, 300], delete="inside")
        result = _rewrite_text_show("Tj", [pikepdf.String(b"A")], gs, cache, rect, stats)
        assert result == [([pikepdf.String(b"A")], "Tj")]

    def test_empty_show_operands_passes_through(self):
        gs = GraphicsState()
        gs.font_name = "/F1"
        gs.font_size = 100.0
        gs.text_matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        cache = _FontCache(_simple_font_resources())
        stats = ExciseStats()
        rect = ExciseRect(rect=[0, 0, 300, 300], delete="inside")
        result = _rewrite_text_show("Tj", [], gs, cache, rect, stats)
        assert result == [([], "Tj")]


class TestGlyphShouldDeleteDirect:
    def test_none_render_matrix_never_deletes(self):
        rect = ExciseRect(rect=[0, 0, 300, 300], delete="inside")
        assert _glyph_should_delete(None, 0.0, False, rect) is False


class TestGlyphOverlapBoxVsCenter:
    """Glyph deletion used to ALWAYS test only the glyph's center point,
    silently ignoring `partial`, unlike images/paths. A glyph straddling
    a boundary -- box overlaps, center doesn't -- survived even under
    the documented default (delete=inside, partial=inside, "deletes
    anything touching the box at all"). These pin the fix:
    `glyph_overlap` now genuinely selects between the two behaviors
    instead of `partial` being silently dropped for text."""

    # trm = (100, 0, 0, 100, 10, 10): font-size-100 scale, positioned at
    # (10, 10). advance_1000=500 -> a 0.5-em glyph -> real box
    # x:[10, 60], y:[10, 110]. Center is (35, 60).
    _TRM = (100.0, 0.0, 0.0, 100.0, 10.0, 10.0)
    _ADVANCE_1000 = 500.0

    def test_straddling_glyph_survives_under_center_default_delete_inside(self):
        # rect x:[0,30] -- box [10,60] overlaps it (10-30), but the
        # glyph's own center (35) sits outside -- glyph_overlap="center"
        # must NOT delete it.
        rect = ExciseRect(rect=[0, 0, 30, 120], delete="inside", glyph_overlap="center")
        assert _glyph_should_delete(self._TRM, self._ADVANCE_1000, False, rect) is False

    def test_same_straddling_glyph_deleted_under_box_default(self):
        # Identical geometry, glyph_overlap="box" (the new default):
        # any overlap counts as "touching the box at all" under
        # partial=inside (default) -- matches excise's own documented
        # behavior, unlike the center-only test above.
        rect = ExciseRect(rect=[0, 0, 30, 120], delete="inside", glyph_overlap="box")
        assert _glyph_should_delete(self._TRM, self._ADVANCE_1000, False, rect) is True

    def test_box_mode_is_the_dataclass_default(self):
        # Constructing an ExciseRect without naming glyph_overlap at all
        # must get "box" -- confirms the default itself, not just that
        # "box" behaves correctly when explicitly requested.
        rect = ExciseRect(rect=[0, 0, 30, 120], delete="inside")
        assert rect.glyph_overlap == "box"
        assert _glyph_should_delete(self._TRM, self._ADVANCE_1000, False, rect) is True

    def test_box_mode_honors_partial_outside_full_containment_required(self):
        # Same straddling box [10,60] against rect x:[0,30]: under
        # partial="outside", a unit only counts as "inside" if it's
        # ENTIRELY contained -- the straddler must survive, same as
        # images/paths already do via excise_geometry.overlap_means_delete.
        rect = ExciseRect(
            rect=[0, 0, 30, 120], delete="inside", partial="outside", glyph_overlap="box"
        )
        assert _glyph_should_delete(self._TRM, self._ADVANCE_1000, False, rect) is False

    def test_box_mode_partial_outside_deletes_fully_contained_glyph(self):
        # Control for the previous test: a glyph fully inside the rect
        # (not straddling) is still deleted under partial="outside".
        rect = ExciseRect(
            rect=[0, 0, 200, 200], delete="inside", partial="outside", glyph_overlap="box"
        )
        assert _glyph_should_delete(self._TRM, self._ADVANCE_1000, False, rect) is True

    def test_center_mode_ignores_partial_entirely(self):
        # A point can't straddle a boundary -- partial="outside" must
        # make no difference to center-mode's result versus the default
        # partial="inside" used elsewhere in this class.
        rect = ExciseRect(
            rect=[0, 0, 30, 120], delete="inside", partial="outside", glyph_overlap="center"
        )
        assert _glyph_should_delete(self._TRM, self._ADVANCE_1000, False, rect) is False


class TestFilterShowElementsDirect:
    def _gs(self, font_size=100.0):
        gs = GraphicsState()
        gs.font_name = "/F1"
        gs.font_size = font_size
        gs.text_matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        return gs

    def test_numeric_adjustment_kept_verbatim_no_deletion(self):
        """A genuine (non-synthetic) kerning number between two survivors,
        with nothing deleted, stays in one segment with no Tm needed."""
        gs = self._gs()
        cache = _FontCache(_simple_font_resources())
        stats = ExciseStats()
        rect = ExciseRect(rect=[500, 500, 600, 600], delete="inside")  # no overlap
        elements = [pikepdf.String(b"A"), -100.0, pikepdf.String(b"B")]
        segments, any_deleted = _filter_show_elements(elements, gs, cache, rect, stats)
        assert any_deleted is False
        assert len(segments) == 1
        tm, seg_elements = segments[0]
        assert tm is None
        assert -100.0 in seg_elements

    def test_deletion_then_survivor_captures_tm_before_survivor_advance(self):
        """The Tm captured for a new segment must reflect position right
        after the deleted glyph -- NOT after the surviving glyph that
        follows it has also been advanced (that would double-advance)."""
        gs = self._gs()
        cache = _FontCache(_simple_font_resources(width=500.0))
        stats = ExciseStats()
        # 'A' box [0,50] (center 25, inside rect -> deleted); 'B' box
        # [50,100] (center 75, outside rect -> survives).
        rect = ExciseRect(rect=[0, 0, 50, 120], delete="inside")
        elements = [pikepdf.String(b"AB")]
        segments, any_deleted = _filter_show_elements(elements, gs, cache, rect, stats)
        assert any_deleted is True
        assert len(segments) == 1
        tm, seg_elements = segments[0]
        assert tm is not None
        # The captured Tm's x-translation must be A's advance only (50),
        # NOT A+B's combined advance (100) -- confirms capture happened
        # at the deletion boundary, not after B was also advanced.
        assert tm[4] == pytest.approx(50.0)
        assert bytes(seg_elements[0]) == b"B"

    def test_zero_font_size_skips_spacing_addition(self):
        """gs.font_size == 0 must skip the char/word-spacing addition
        entirely rather than dividing by zero."""
        gs = self._gs(font_size=0.0)
        gs.char_spacing = 5.0
        cache = _FontCache(_simple_font_resources())
        stats = ExciseStats()
        rect = ExciseRect(rect=[500, 500, 600, 600], delete="inside")
        # Should not raise ZeroDivisionError.
        _filter_show_elements([pikepdf.String(b"A")], gs, cache, rect, stats)

    def test_word_spacing_applied_to_code_32_simple_font(self):
        """Code 32 (space) in a simple (non-composite) font must add
        word_spacing on top of char_spacing."""
        gs = self._gs()
        gs.word_spacing = 50.0
        cache = _FontCache(_simple_font_resources())
        stats = ExciseStats()
        rect = ExciseRect(rect=[500, 500, 600, 600], delete="inside")
        # Byte 32 == ' ' -- simple font, not composite. Must not raise;
        # the resulting advance is an internal detail not asserted here.
        _filter_show_elements([pikepdf.String(b" ")], gs, cache, rect, stats)


class TestRewriteTextShowQuoteMalformedOperands:
    def test_dquote_malformed_operands_caught(self):
        """Non-numeric aw/ac operands to '"' must be caught rather than
        raising, leaving word/char spacing untouched."""
        gs = GraphicsState()
        gs.font_name = "/F1"
        gs.font_size = 100.0
        gs.text_matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        cache = _FontCache(_simple_font_resources())
        stats = ExciseStats()
        rect = ExciseRect(rect=[0, 0, 300, 300], delete="inside")
        operands = [pikepdf.Name("/Bad"), pikepdf.Name("/AlsoBad"), pikepdf.String(b"A")]
        result = _rewrite_text_show('"', operands, gs, cache, rect, stats)
        assert result  # didn't raise; produced some output (the T* prefix)


class TestFilterShowElementsNumericOnlySegmentDiscarded:
    def _gs(self, font_size=100.0):
        gs = GraphicsState()
        gs.font_name = "/F1"
        gs.font_size = font_size
        gs.text_matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        return gs

    def test_kerning_number_between_two_deleted_runs_is_discarded(self):
        """A genuine kerning number sandwiched between two fully-deleted
        glyph runs, with nothing visible surviving anywhere in the
        element list, must produce NO segments at all -- not a bare
        numeric-only TJ array. This is the exact real-world shape that
        slipped through initially: 'pending' (holding only the kerning
        number) was non-empty at close time and got emitted as its own
        segment even though it carried no visible content."""
        gs = self._gs()
        cache = _FontCache(_simple_font_resources())
        stats = ExciseStats()
        # Rect covers x in [0, 300] -- both glyph runs land inside it and
        # get deleted regardless of the kerning number's own advance.
        rect = ExciseRect(rect=[0, 0, 300, 120], delete="inside")
        elements = [pikepdf.String(b"A"), -100.0, pikepdf.String(b"B")]
        segments, any_deleted = _filter_show_elements(elements, gs, cache, rect, stats)
        assert any_deleted is True
        assert segments == []

    def test_kerning_number_between_deleted_and_surviving_run_still_repositions(self):
        """Same shape, but the SECOND run survives -- the discarded
        numeric-only pending run must not swallow or corrupt the Tm that
        eventually gets attached to the surviving segment."""
        gs = self._gs()
        cache = _FontCache(_simple_font_resources(width=500.0))
        stats = ExciseStats()
        # 'A' (first string) deleted; 'B' (second string) survives.
        rect = ExciseRect(rect=[0, 0, 60, 120], delete="inside")
        elements = [pikepdf.String(b"A"), -100.0, pikepdf.String(b"B")]
        segments, any_deleted = _filter_show_elements(elements, gs, cache, rect, stats)
        assert any_deleted is True
        assert len(segments) == 1
        tm, seg_elements = segments[0]
        assert tm is not None
        assert bytes(seg_elements[0]) == b"B"

    def test_multiple_numeric_only_runs_all_discarded_leaving_one_segment(self):
        """Several deleted runs separated by kerning numbers, with only
        one surviving string at the very end -- every numeric-only
        pending run in between must be discarded, leaving exactly one
        emitted segment for the survivor.

        Font size 100, width 500 -> each glyph advances 50pt; each -100
        (thousandths) kerning number advances a further 10pt. Positions:
        A1 [0,50] center 25 -- delete. Adjust -> pos 60.
        A2 [60,110] center 85 -- delete. Adjust -> pos 120.
        B  [120,170] center 145 -- survives (rect only covers up to 140).
        """
        gs = self._gs()
        cache = _FontCache(_simple_font_resources(width=500.0))
        stats = ExciseStats()
        # glyph_overlap=center: this test is about numeric-only-run
        # bookkeeping, using each glyph's CENTER (per the docstring
        # above) to decide survival -- box mode (the default) would
        # also catch B, since B's box [120,170] genuinely overlaps the
        # rect's right edge at 140.
        rect = ExciseRect(rect=[0, 0, 140, 120], delete="inside", glyph_overlap="center")
        elements = [
            pikepdf.String(b"A"),
            -100.0,
            pikepdf.String(b"A"),
            -100.0,
            pikepdf.String(b"B"),  # survives: center 145 is outside the rect
        ]
        segments, any_deleted = _filter_show_elements(elements, gs, cache, rect, stats)
        assert any_deleted is True
        assert len(segments) == 1
        tm, seg_elements = segments[0]
        assert tm is not None
        assert bytes(seg_elements[0]) == b"B"
        assert tm[4] == pytest.approx(120.0)  # confirms Tm captured AFTER both suppressed kerns


class TestExciseContentTextLineMatrixRestoration:
    """Regression tests for the Tlm-drift bug: rewrite_text_show's
    corrective Tm (emitted when a deletion breaks a run mid-show-op)
    resets BOTH Tm and Tlm on any real PDF renderer, per spec -- but our
    own GraphicsState simulation never replays that synthetic Tm, so
    gs.text_line_matrix still holds the author's untouched line anchor.
    Without restoring device Tlm to that same untouched value right
    after the rewritten show op, any subsequent Td/TD/T* in the
    ORIGINAL (unmodified) stream -- which is relative to Tlm -- drifts
    by the deleted glyph(s)' advance, and every later line in that text
    object inherits the offset. See git history for the real-world
    symptom (a whole paragraph shifted right after one redacted 'm')."""

    def test_td_after_mid_run_deletion_uses_restored_tlm(self):
        # Tm sets Tm=Tlm=(1,0,0,1,10,10). Font size 100, width 500/1000
        # each -> 'A' center (35,60), 'B' center (85,60). 'C' (after Td
        # 0,-20) sits at a different y (~40), so a y-restricted rect
        # isolates 'A' alone without also catching 'C's center.
        content = b"BT /F1 100 Tf 1 0 0 1 10 10 Tm (AB) Tj 0 -20 Td (C) Tj ET"
        pdf = _make_pdf_with_content(content, resources=_simple_font_resources(width=500.0))
        # glyph_overlap=center: this test isolates 'A' from 'B' by their
        # CENTER positions (see the comment above) -- unrelated to the
        # TLM-restoration mechanic under test, so pin the mode
        # explicitly rather than let the box-overlap default change
        # which glyphs get caught by this rect.
        excise_content(pdf, ["1(abs,10,50,60,120,glyph_overlap=center)"])

        instructions = pikepdf.parse_content_stream(pdf.pages[0].Contents)
        text_ops = [
            (str(op), operands)
            for operands, op in instructions
            if str(op) in ("Tm", "Td", "Tj", "TJ")
        ]

        # Expected sequence: the author's own Tm is immediately
        # superseded by the synthetic corrective Tm with nothing shown
        # between them, so optimize_positioning_ops's consecutive-Tm
        # collapse correctly drops the author's Tm as dead (its value
        # is fully superseded before anything observes it) -- only the
        # corrective Tm survives, then Tj 'B', a restorative Tm (back
        # to the author's real Tlm -- this one DOES survive, since the
        # ORIGINAL Td and Tj 'C' follow it), then that original Td
        # untouched, then Tj 'C'.
        ops_only = [op for op, _ in text_ops]
        assert ops_only == ["Tm", "Tj", "Tm", "Td", "Tj"]

        _, b_operands = text_ops[1]
        assert bytes(b_operands[0]) == b"B"

        restorative_tm = text_ops[2][1]
        assert [float(x) for x in restorative_tm] == pytest.approx(
            [1.0, 0.0, 0.0, 1.0, 10.0, 10.0]
        )

        # The original Td must be passed through byte-for-byte unmodified
        # -- it was never rewritten, only the show op before it was.
        td_operands = text_ops[3][1]
        assert [float(x) for x in td_operands] == pytest.approx([0.0, -20.0])

        _, c_operands = text_ops[4]
        assert bytes(c_operands[0]) == b"C"

    def test_no_restorative_tm_when_no_deletion_occurred(self):
        """When a show op has no deletions at all, it's re-emitted as the
        original operator with no corrective/restorative Tm at all --
        the restoration logic must not fire spuriously."""
        content = b"BT /F1 100 Tf 1 0 0 1 10 10 Tm (AB) Tj 0 -20 Td (C) Tj ET"
        pdf = _make_pdf_with_content(content, resources=_simple_font_resources(width=500.0))
        excise_content(pdf, ["1(abs,500,500,600,600)"])  # touches nothing

        instructions = pikepdf.parse_content_stream(pdf.pages[0].Contents)
        ops_only = [
            str(op) for operands, op in instructions if str(op) in ("Tm", "Td", "Tj", "TJ")
        ]
        # Only the author's own Tm/Td survive -- no injected Tm anywhere.
        assert ops_only == ["Tm", "Tj", "Td", "Tj"]

    def test_full_run_deletion_still_restores_tlm_for_following_td(self):
        """Even when EVERY glyph in the show op is deleted (segments is
        empty, nothing to reposition FOR), NO synthetic Tm/Tj is emitted
        for that show op at all -- so no restoration is needed either,
        since nothing was ever written to the output that could have
        drifted a real renderer's Tm/Tlm away from the author's own,
        untouched values."""
        content = b"BT /F1 100 Tf 1 0 0 1 10 10 Tm (AB) Tj 0 -20 Td (C) Tj ET"
        pdf = _make_pdf_with_content(content, resources=_simple_font_resources(width=500.0))
        # y-restricted to A/B's line (~y=60) so 'C' (~y=40, after Td) is
        # left untouched -- isolates the fully-deleted-run case cleanly.
        # glyph_overlap=center: C's CENTER (~40) sits below the rect, but
        # C's nominal 1-em box (baseline -10 to ~90 at this font size)
        # genuinely overlaps y=50..120 -- box mode (the default) would
        # correctly also delete C, which isn't what this test (a
        # TLM-restoration check, not a glyph_overlap policy check) is
        # about, so pin the mode explicitly.
        excise_content(pdf, ["1(abs,0,50,300,120,glyph_overlap=center)"])

        instructions = pikepdf.parse_content_stream(pdf.pages[0].Contents)
        text_ops = [
            (str(op), operands)
            for operands, op in instructions
            if str(op) in ("Tm", "Td", "Tj", "TJ")
        ]
        ops_only = [op for op, _ in text_ops]
        # Nothing at all survives from the AB show op -- only the
        # author's own Tm, the original Td, and C's Tj.
        assert ops_only == ["Tm", "Td", "Tj"]
        td_operands = text_ops[1][1]
        assert [float(x) for x in td_operands] == pytest.approx([0.0, -20.0])
        _, c_operands = text_ops[2]
        assert bytes(c_operands[0]) == b"C"


class TestRewriteTextShowSegmentWithKerning:
    def test_segment_with_kerning_after_deletion_emits_tj_array(self):
        """A surviving segment that contains MORE than one element (a
        kept kerning number sandwiched between two surviving strings)
        must be emitted as a TJ array, not a Tj -- exercises the `else`
        branch of rewrite_text_show's per-segment Tj/TJ choice, which a
        single-string segment (the common case in the other tests) never
        reaches."""
        gs = GraphicsState()
        gs.font_name = "/F1"
        gs.font_size = 100.0
        gs.text_matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        cache = _FontCache(_simple_font_resources(width=500.0))
        stats = ExciseStats()
        # 'A' box center 25 -- inside rect, deleted. 'B' box center 75,
        # 'C' (after B's advance + the -100 kerning) -- both outside the
        # rect, survive together as one segment with a Tm in front (since
        # a deletion preceded them).
        rect = ExciseRect(rect=[0, 0, 50, 120], delete="inside")
        operands = [
            pikepdf.Array(
                [pikepdf.String(b"A"), pikepdf.String(b"B"), -100.0, pikepdf.String(b"C")]
            )
        ]
        result = _rewrite_text_show("TJ", operands, gs, cache, rect, stats)

        ops = [op for _, op in result]
        assert "TJ" in ops

        tj_operands = next(
            operands_
            for operands_, op in result
            if op == "TJ" and any(isinstance(e, pikepdf.String) for e in operands_[0])
        )
        elements = list(tj_operands[0])
        assert bytes(elements[0]) == b"B"
        assert elements[1] == pytest.approx(-100.0)
        assert bytes(elements[2]) == b"C"


class TestExciseContentPeepholeOptimization:
    def test_dead_tf_chain_collapsed_after_excise(self):
        """End-to-end: excise deletes all glyphs under /F44 and /F27,
        leaving behind exactly the dead-Tf/dead-Td shape
        optimize_positioning_ops exists to clean up. Confirms the
        splice in process_stream actually fires on a real excise run,
        not just on hand-built instruction lists."""
        # Uses only 'A'/'B' -- _simple_font_resources() only defines
        # widths for those two codes (FirstChar=65, a 2-entry /Widths).
        # Any other letter falls back to a 0.0 default width, never
        # advances the pen, and would never actually leave the origin
        # box -- an earlier version of this test used real words and
        # silently excised 0/14 glyphs as a result.
        #
        # Td(100, 0) each: Td translates Tlm directly (independent of
        # the previous glyph's own advance, since it's not relative to
        # the post-Tj pen position) -- so 'A' sits at x=[0,50] (center
        # 25, outside the rect below), the first Td-under-/F44 'B' at
        # x=[100,150] (center 125, inside), and the second
        # Td-under-/F27 'B' at x=[200,250] (center 225, inside). A
        # smaller offset (e.g. 10) previously left both B's centers
        # under 50 and silently excised nothing.
        content = b"BT /F1 100 Tf (A) Tj /F44 12 Tf 100 0 Td (B) Tj /F27 12 Tf 100 0 Td (B) Tj ET"
        pdf = _make_pdf_with_content(content, resources=_simple_font_resources())
        excise_content(pdf, ["1(abs,50,0,300,300)"])  # deletes everything but 'A'

        ops = _get_content_ops(pdf)
        # /F44's Tf is dead (superseded by /F27's Tf with no
        # intervening show), and both Td's are dead (nothing survives
        # under either font, so the whole position chain is dropped
        # before ET). /F27's own Tf is ALSO dead here: process_stream
        # runs optimize_positioning_ops with aggressive_tf=True (safe
        # because excise.py's _process_page always calls
        # contents_coalesce() before this stream is reached, so BT/ET
        # aren't real scope boundaries for a single-stream Tf-liveness
        # check) -- and this stream has no later text object at all
        # that could inherit it, so nothing shows under it anywhere.
        assert ops == ["BT", "Tf", "Tj", "ET"]
        assert ops.count("Tf") == 1


class TestExciseContentPeepholeOptimizationAggressiveTf:
    def test_dead_tf_dropped_across_bt_et_boundary_end_to_end(self):
        """Regression test for the aggressive_tf=True wiring in
        process_stream: confirms cross-BT/ET dead-Tf elimination -- not
        just the conservative single-text-object case already covered
        above -- actually fires through the real excise_content
        pipeline, not merely at the optimize_content library level
        (already covered by
        TestOptimizePositioningOpsAggressiveTfIntegration in
        test_optimize_content.py). The two now-EMPTY trailing BT/ET
        pairs (left behind once the dead /F44 Tf between them is
        dropped) are themselves also dropped by _drop_empty_bt_et,
        which already runs later in the same pipeline -- so the final
        result collapses further than just the dead-Tf elimination
        alone would.

        Note this doesn't even need excise to delete any glyphs: the
        peephole pass runs unconditionally at the end of process_stream
        regardless of whether interpret_and_filter changed anything, so
        an excise spec that overlaps nothing is sufficient to exercise
        it -- the dead /F44 Tf below was already dead in the ORIGINAL
        stream, not something excise's deletion created.
        """
        content = (
            b"BT /F1 100 Tf (A) Tj ET "  # real content -- survives
            b"BT /F44 12 Tf ET "  # Tf with nothing shown in this
            # text object, AND no later text object ever shows
            # anything either -- only provably dead once BT/ET stop
            # acting as barriers (aggressive_tf=True)
            b"BT ET"  # trailing empty text object -- nothing to
            # inherit /F44 into, confirming the chain really is dead
        )
        pdf = _make_pdf_with_content(content, resources=_simple_font_resources())
        excise_content(pdf, ["1(abs,500,500,600,600)"])  # overlaps nothing

        ops = _get_content_ops(pdf)
        assert ops == ["BT", "Tf", "Tj", "ET"]
        assert ops.count("Tf") == 1

        # Confirm the survivor is /F1, not the dead /F44 -- guards
        # against the dead-Tf drop accidentally removing the wrong one.
        instructions = pikepdf.parse_content_stream(pdf.pages[0].Contents)
        tf_operands = next(operands for operands, op in instructions if str(op) == "Tf")
        assert str(tf_operands[0]) == "/F1"
