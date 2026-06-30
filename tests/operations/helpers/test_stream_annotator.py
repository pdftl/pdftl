# tests/operations/helpers/test_stream_annotator.py

import pikepdf
import pytest

from pdftl.operations.helpers.stream_annotator import (
    _interpret_operands,
    _resolve_tf_extras,
    _resolve_do_extras,
    _resolve_gs_extras,
    _resolve_resource_extras,
    _build_comment,
    annotate_stream,
)
from pdftl.utils.scope_tracker import ScopeTracker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pdf():
    p = pikepdf.new()
    p.add_blank_page()
    return p


def make_font_resources(pdf, font_name="/F1", base_font="/Helvetica"):
    """Build a real page Resources dict containing a Type1 font."""
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name("/Type1"),
            BaseFont=pikepdf.Name(base_font),
        )
    )
    pdf.pages[0].Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary({font_name: font}))
    return pdf.pages[0].Resources


def make_xobj_resources(pdf, xobj_name="/Fm0", subtype="/Form"):
    """Build a real page Resources dict containing a Form XObject."""
    xobj = pdf.make_stream(b"")
    xobj.Subtype = pikepdf.Name(subtype)
    pdf.pages[0].Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary({xobj_name: xobj}))
    return pdf.pages[0].Resources


def make_gs_resources(pdf, gs_name="/GS0", ca=None, CA=None, BM=None):
    """Build a real page Resources dict containing a graphics state dict."""
    gs = pdf.make_indirect(pikepdf.Dictionary())
    if ca is not None:
        gs["/ca"] = ca
    if CA is not None:
        gs["/CA"] = CA
    if BM is not None:
        gs["/BM"] = pikepdf.Name(BM)
    pdf.pages[0].Resources = pikepdf.Dictionary(ExtGState=pikepdf.Dictionary({gs_name: gs}))
    return pdf.pages[0].Resources


# ---------------------------------------------------------------------------
# _interpret_operands
# ---------------------------------------------------------------------------


class TestInterpretOperands:
    def test_line_width(self):
        assert _interpret_operands("w", ["1", "w"]) == "width=1"

    def test_line_join_miter(self):
        assert "Miter" in _interpret_operands("j", ["0", "j"])

    def test_line_join_round(self):
        assert "Round" in _interpret_operands("j", ["1", "j"])

    def test_line_cap_butt(self):
        assert "Butt" in _interpret_operands("J", ["0", "J"])

    def test_operator_not_in_args_table_returns_empty(self):
        # BT is in PDF_OPERATORS but not PDF_OPERATOR_ARGS
        assert _interpret_operands("BT", ["BT"]) == ""

    def test_too_few_tokens_returns_empty(self):
        assert _interpret_operands("w", ["w"]) == ""

    def test_unknown_operator_returns_empty(self):
        assert _interpret_operands("FAKE", ["FAKE"]) == ""


# ---------------------------------------------------------------------------
# _resolve_tf_extras
# ---------------------------------------------------------------------------


class TestResolveTfExtras:
    def test_resolves_base_font(self, pdf):
        resources = make_font_resources(pdf, "/F1", "/Helvetica")
        result = _resolve_tf_extras(["/F1", "12", "Tf"], resources, {})
        assert result == ["-> /Helvetica"]

    def test_no_resources_returns_empty(self):
        assert _resolve_tf_extras(["/F1", "12", "Tf"], None, {}) == []

    def test_font_not_found_returns_empty(self, pdf):
        resources = make_font_resources(pdf, "/F1", "/Helvetica")
        assert _resolve_tf_extras(["/F2", "12", "Tf"], resources, {}) == []

    def test_font_missing_base_font_returns_empty(self, pdf):
        font = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Font"),
            )
        )
        pdf.pages[0].Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary({"/F1": font}))
        resources = pdf.pages[0].Resources
        assert _resolve_tf_extras(["/F1", "12", "Tf"], resources, {}) == []

    def test_too_few_tokens_returns_empty(self, pdf):
        resources = make_font_resources(pdf)
        assert _resolve_tf_extras(["Tf"], resources, {}) == []

    def test_broken_resources_returns_empty(self):
        class Broken:
            def __contains__(self, _):
                return True

            @property
            def Font(self):
                raise AttributeError("boom")

        assert _resolve_tf_extras(["/F1", "12", "Tf"], Broken(), {}) == []

    def test_caches_by_font_name(self, pdf):
        resources = make_font_resources(pdf, "/F1", "/Helvetica")
        cache = {}
        first = _resolve_tf_extras(["/F1", "12", "Tf"], resources, cache)
        assert cache == {"/F1": ["-> /Helvetica"]}
        # Mutate the underlying resource after caching — a second call with the
        # same cache should still return the cached (now stale) value, proving
        # the cache short-circuits the resource lookup rather than re-resolving.
        second = _resolve_tf_extras(["/F1", "12", "Tf"], resources, cache)
        assert second == first


# ---------------------------------------------------------------------------
# _resolve_do_extras
# ---------------------------------------------------------------------------


class TestResolveDoExtras:
    def test_resolves_form_subtype(self, pdf):
        resources = make_xobj_resources(pdf, "/Fm0", "/Form")
        result = _resolve_do_extras(["/Fm0", "Do"], resources)
        assert result == ["Subtype: /Form"]

    def test_resolves_image_subtype(self, pdf):
        resources = make_xobj_resources(pdf, "/Im0", "/Image")
        result = _resolve_do_extras(["/Im0", "Do"], resources)
        assert result == ["Subtype: /Image"]

    def test_no_resources_returns_empty(self):
        assert _resolve_do_extras(["/Fm0", "Do"], None) == []

    def test_xobject_not_found_returns_empty(self, pdf):
        resources = make_xobj_resources(pdf, "/Fm0", "/Form")
        assert _resolve_do_extras(["/Fm1", "Do"], resources) == []

    def test_too_few_tokens_returns_empty(self, pdf):
        resources = make_xobj_resources(pdf)
        assert _resolve_do_extras(["Do"], resources) == []

    def test_broken_resources_returns_empty(self):
        class Broken:
            def __contains__(self, _):
                return True

            @property
            def XObject(self):
                raise AttributeError("boom")

        assert _resolve_do_extras(["/Fm0", "Do"], Broken()) == []


# ---------------------------------------------------------------------------
# _resolve_gs_extras
# ---------------------------------------------------------------------------


class TestResolveGsExtras:
    def test_all_fields(self, pdf):
        resources = make_gs_resources(pdf, "/GS0", ca=0.5, CA=0.8, BM="/Multiply")
        result = _resolve_gs_extras(["/GS0", "gs"], resources)
        assert "fill-alpha: 0.5" in result
        assert "stroke-alpha: 0.8" in result
        assert "blend: /Multiply" in result

    def test_only_ca(self, pdf):
        resources = make_gs_resources(pdf, "/GS0", ca=1.0)
        result = _resolve_gs_extras(["/GS0", "gs"], resources)
        assert any("fill-alpha" in s for s in result)
        assert not any("stroke-alpha" in s for s in result)
        assert not any("blend" in s for s in result)

    def test_only_CA(self, pdf):
        resources = make_gs_resources(pdf, "/GS0", CA=0.3)
        result = _resolve_gs_extras(["/GS0", "gs"], resources)
        assert any("stroke-alpha" in s for s in result)
        assert not any("fill-alpha" in s for s in result)

    def test_only_BM(self, pdf):
        resources = make_gs_resources(pdf, "/GS0", BM="/Screen")
        result = _resolve_gs_extras(["/GS0", "gs"], resources)
        assert any("blend: /Screen" in s for s in result)

    def test_no_fields_returns_empty_list(self, pdf):
        resources = make_gs_resources(pdf, "/GS0")
        assert _resolve_gs_extras(["/GS0", "gs"], resources) == []

    def test_no_resources_returns_empty(self):
        assert _resolve_gs_extras(["/GS0", "gs"], None) == []

    def test_gs_not_found_returns_empty(self, pdf):
        resources = make_gs_resources(pdf, "/GS0", ca=1.0)
        assert _resolve_gs_extras(["/GS1", "gs"], resources) == []

    def test_too_few_tokens_returns_empty(self, pdf):
        resources = make_gs_resources(pdf, "/GS0", ca=1.0)
        assert _resolve_gs_extras(["gs"], resources) == []

    def test_broken_resources_returns_empty(self):
        class Broken:
            def __contains__(self, _):
                return True

            @property
            def ExtGState(self):
                raise AttributeError("boom")

        assert _resolve_gs_extras(["/GS0", "gs"], Broken()) == []


# ---------------------------------------------------------------------------
# _resolve_resource_extras (dispatch)
# ---------------------------------------------------------------------------


class TestResolveResourceExtras:
    def test_dispatches_tf(self, pdf):
        resources = make_font_resources(pdf, "/F1", "/Helvetica")
        result = _resolve_resource_extras("Tf", ["/F1", "12", "Tf"], resources, {})
        assert result == ["-> /Helvetica"]

    def test_dispatches_do(self, pdf):
        resources = make_xobj_resources(pdf, "/Fm0", "/Form")
        result = _resolve_resource_extras("Do", ["/Fm0", "Do"], resources, {})
        assert result == ["Subtype: /Form"]

    def test_dispatches_gs(self, pdf):
        resources = make_gs_resources(pdf, "/GS0", ca=0.5)
        result = _resolve_resource_extras("gs", ["/GS0", "gs"], resources, {})
        assert any("fill-alpha" in s for s in result)

    def test_non_resource_operator_returns_empty(self):
        assert _resolve_resource_extras("BT", ["BT"], None, {}) == []

    def test_w_returns_empty(self):
        assert _resolve_resource_extras("w", ["1", "w"], None, {}) == []


# ---------------------------------------------------------------------------
# _build_comment
# ---------------------------------------------------------------------------


class TestBuildComment:
    def _tracker(self):
        return ScopeTracker()

    def test_basic_operator(self):
        comment = _build_comment("w", ["1", "w"], None, self._tracker(), {})
        assert "state/stroke" in comment
        assert "Set line width" in comment
        assert "width=1" in comment

    def test_scope_open_label(self):
        comment = _build_comment("q", ["q"], None, self._tracker(), {})
        assert "gs#1 open" in comment

    def test_scope_close_label(self):
        t = self._tracker()
        _build_comment("q", ["q"], None, t, {})
        comment = _build_comment("Q", ["Q"], None, t, {})
        assert "gs#1 close" in comment

    def test_bt_et_scope(self):
        t = self._tracker()
        assert "bt#1 open" in _build_comment("BT", ["BT"], None, t, {})
        assert "bt#1 close" in _build_comment("ET", ["ET"], None, t, {})

    def test_bdc_emc_scope(self):
        t = self._tracker()
        assert "mc#1 open" in _build_comment("BDC", ["/Tag", "/MC0", "BDC"], None, t, {})
        assert "mc#1 close" in _build_comment("EMC", ["EMC"], None, t, {})

    def test_nested_scope_ids(self):
        t = self._tracker()
        _build_comment("q", ["q"], None, t, {})
        assert "gs#2 open" in _build_comment("q", ["q"], None, t, {})
        assert "gs#2 close" in _build_comment("Q", ["Q"], None, t, {})
        assert "gs#1 close" in _build_comment("Q", ["Q"], None, t, {})

    def test_interior_line_indented(self):
        t = self._tracker()
        _build_comment("q", ["q"], None, t, {})
        comment = _build_comment("w", ["1", "w"], None, t, {})
        # After one q, interior comment should be indented two spaces
        assert comment.startswith("%   ")

    def test_open_and_close_at_same_indent(self):
        t = self._tracker()
        open_c = _build_comment("q", ["q"], None, t, {})
        _build_comment("w", ["1", "w"], None, t, {})  # interior
        close_c = _build_comment("Q", ["Q"], None, t, {})
        # Both q and Q should start at the same indent level
        open_indent = len(open_c) - len(open_c.lstrip("%").lstrip())
        close_indent = len(close_c) - len(close_c.lstrip("%").lstrip())
        assert open_indent == close_indent

    def test_tf_extends_operand_with_basefont(self, pdf):
        resources = make_font_resources(pdf, "/F1", "/Times-Roman")
        comment = _build_comment("Tf", ["/F1", "12", "Tf"], resources, self._tracker(), {})
        assert "font=/F1 size=12" in comment
        assert "-> /Times-Roman" in comment

    def test_no_extras_for_scope_only_operator(self):
        # q has a scope label but no operands or resource extras
        comment = _build_comment("q", ["q"], None, self._tracker(), {})
        assert "gs#1 open" in comment
        # Should not have a second bracketed section
        assert comment.count("[") == 1

    def test_operator_with_no_extras_has_no_brackets(self):
        comment = _build_comment("S", ["S"], None, self._tracker(), {})
        assert "[" not in comment


# ---------------------------------------------------------------------------
# annotate_stream (end-to-end)
# ---------------------------------------------------------------------------


class TestAnnotateStream:
    def test_returns_bytes(self):
        assert isinstance(annotate_stream(b"BT\nET\n"), bytes)

    def test_empty_stream(self):
        assert annotate_stream(b"") == b""

    def test_blank_lines_preserved(self):
        result = annotate_stream(b"BT\n\nET\n")
        assert b"\n\n" in result

    def test_comment_lines_preserved(self):
        result = annotate_stream(b"% existing comment\nBT\nET\n")
        assert b"% existing comment" in result

    def test_unknown_operator_preserved_without_annotation(self):
        result = annotate_stream(b"FAKEOP\n")
        lines = [l for l in result.decode("latin-1").splitlines() if "FAKEOP" in l]
        assert len(lines) == 1
        assert "%" not in lines[0]

    def test_known_operator_gets_comment(self):
        result = annotate_stream(b"BT\nET\n")
        assert b"begin/text" in result
        assert b"end/text" in result

    def test_scope_ids_paired(self):
        result = annotate_stream(b"q\nQ\n")
        assert b"gs#1 open" in result
        assert b"gs#1 close" in result

    def test_nested_scope_ids(self):
        result = annotate_stream(b"q\nq\nQ\nQ\n")
        assert b"gs#1 open" in result
        assert b"gs#2 open" in result
        assert b"gs#2 close" in result
        assert b"gs#1 close" in result

    def test_marked_content_scope(self):
        result = annotate_stream(b"/Tag /MC0 BDC\nEMC\n")
        assert b"mc#1 open" in result
        assert b"mc#1 close" in result

    def test_bt_et_scope(self):
        result = annotate_stream(b"BT\nET\n")
        assert b"bt#1 open" in result
        assert b"bt#1 close" in result

    def test_line_width_operand(self):
        result = annotate_stream(b"2 w\n")
        assert b"width=2" in result

    def test_dash_pattern_solid(self):
        result = annotate_stream(b"[ ] 0 d\n")
        assert b"solid" in result

    def test_tf_with_resources(self, pdf):
        resources = make_font_resources(pdf, "/F1", "/Helvetica")
        result = annotate_stream(b"/F1 12 Tf\n", resources=resources)
        assert b"/Helvetica" in result

    def test_gs_resource_lookup(self, pdf):
        resources = make_gs_resources(pdf, "/GS0", ca=0.5, BM="/Multiply")
        result = annotate_stream(b"/GS0 gs\n", resources=resources)
        assert b"fill-alpha: 0.5" in result
        assert b"blend: /Multiply" in result

    def test_do_resource_lookup(self, pdf):
        resources = make_xobj_resources(pdf, "/Fm0", "/Form")
        result = annotate_stream(b"/Fm0 Do\n", resources=resources)
        assert b"Subtype: /Form" in result

    def test_long_line_gets_two_space_separator(self):
        # Line must exceed max_comment_col (80) to hit the else branch.
        # 90 x's + " cm" = 93 chars, which is > 80.
        long = ("x " * 45 + "cm\n").encode("latin-1")
        result = annotate_stream(long, min_comment_col=40, max_comment_col=80)
        lines = result.decode("latin-1").splitlines()
        annotated = next(l for l in lines if "cm" in l and "%" in l)
        # The comment should be separated by two spaces, not padding
        assert "  %" in annotated
        assert not annotated.startswith(" " * 81)

    def test_interior_lines_indented_in_comment(self):
        result = annotate_stream(b"q\n1 w\nQ\n")
        lines = result.decode("latin-1").splitlines()
        w_line = next(l for l in lines if "1 w" in l)
        comment = w_line[w_line.index("%") :]
        assert comment.startswith("%   ")  # two-space indent inside q/Q


# ---------------------------------------------------------------------------
# annotate_stream alignment column behavior
# (formerly unit-tested in isolation via the now-removed
# _comment_alignment_col helper; alignment is now computed inline within
# annotate_stream, so these are exercised end-to-end instead.)
# ---------------------------------------------------------------------------


class TestAnnotateStreamAlignment:
    def test_short_known_operator_stays_at_min_col(self):
        result = annotate_stream(b"BT\n", min_comment_col=40, max_comment_col=80)
        line = result.decode("latin-1").splitlines()[0]
        # Comment starts exactly at column 40 (0-indexed) when padded to min_col.
        assert line.index("%") == 40

    def test_long_line_expands_alignment_col(self):
        long_line = ("1 0 0 1 " + "100 " * 10 + "cm\n").encode("latin-1")
        short_line = b"BT\n"
        result = annotate_stream(short_line + long_line, min_comment_col=40, max_comment_col=80)
        lines = result.decode("latin-1").splitlines()
        bt_line = next(l for l in lines if l.strip().startswith("BT"))
        # BT's comment column should have expanded beyond min_col(40) because
        # the long "cm" line in the same stream pushed the shared alignment up.
        assert bt_line.index("%") > 40

    def test_alignment_capped_at_max_col(self):
        long_line = ("x " * 50 + "cm\n").encode("latin-1")
        result = annotate_stream(long_line, min_comment_col=40, max_comment_col=80)
        line = result.decode("latin-1").splitlines()[0]
        # Line itself exceeds max_comment_col, so the two-space-separator
        # fallback kicks in rather than padding to a column beyond max.
        assert "  %" in line
        assert not line.startswith(" " * 81)

    def test_unknown_and_blank_lines_dont_affect_alignment(self):
        result = annotate_stream(b"FAKEOP\nBT\n", min_comment_col=40, max_comment_col=80)
        lines = result.decode("latin-1").splitlines()
        bt_line = next(l for l in lines if l.strip().startswith("BT"))
        # FAKEOP isn't a known operator, so it shouldn't widen the column —
        # BT should still land at the min column.
        assert bt_line.index("%") == 40
