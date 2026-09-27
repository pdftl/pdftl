# tests/operations/test_round_text_positions.py
#
# Glyph positions are measured with MuPDF, before and after: no glyph may
# move more than the tolerance, and the extracted text must not change.

import io
import math
import random
import zlib

import numpy as np
import pikepdf
import pymupdf
import pytest

import pdftl.operations.round_text_positions as rtp
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.round_text_positions import _parse_args, round_text_positions_op

FORM = b"BT /F1 10 Tf 1 0 0 1 5 5 Tm [(Fo) 0.3 (rm)] TJ ET"


def _helvetica(pdf):
    return pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica,
            Encoding=pikepdf.Name.WinAnsiEncoding,
        )
    )


def _vertical_font(pdf):
    descriptor = pikepdf.Dictionary(
        Type=pikepdf.Name.FontDescriptor,
        FontName=pikepdf.Name("/HeiseiMin-W3"),
        Flags=6,
        FontBBox=[0, -141, 1000, 859],
        ItalicAngle=0,
        Ascent=859,
        Descent=-141,
        CapHeight=709,
        StemV=69,
    )
    cid = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.CIDFontType0,
        BaseFont=pikepdf.Name("/HeiseiMin-W3"),
        CIDSystemInfo=pikepdf.Dictionary(
            Registry=pikepdf.String("Adobe"), Ordering=pikepdf.String("Japan1"), Supplement=2
        ),
        FontDescriptor=pdf.make_indirect(descriptor),
    )
    return pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type0,
            BaseFont=pikepdf.Name("/HeiseiMin-W3"),
            Encoding=pikepdf.Name("/Identity-V"),
            DescendantFonts=[pdf.make_indirect(cid)],
        )
    )


def _doc(*contents: bytes, form: bytes | None = None, form_matrix=None, annot_form=False):
    pdf = pikepdf.new()
    fonts = pikepdf.Dictionary(F1=_helvetica(pdf), V1=_vertical_font(pdf))
    xobjects = pikepdf.Dictionary()
    if form is not None:
        fx = pdf.make_stream(form)
        fx.Type, fx.Subtype, fx.BBox = pikepdf.Name.XObject, pikepdf.Name.Form, [0, 0, 300, 300]
        fx.Resources = pikepdf.Dictionary(Font=fonts)
        if form_matrix is not None:
            fx.Matrix = form_matrix
        xobjects.X0 = fx
        xobjects.Im0 = pdf.make_stream(
            b"\x00", Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image, Width=1, Height=1
        )
    for content in contents:
        pdf.add_blank_page(page_size=(300, 300))
        page = pdf.pages[-1]
        page.Resources = pikepdf.Dictionary(Font=fonts, XObject=xobjects)
        page.Contents = pdf.make_stream(content)
    if annot_form:
        ap = pdf.make_stream(b"/X0 Do")
        ap.Type, ap.Subtype, ap.BBox = pikepdf.Name.XObject, pikepdf.Name.Form, [0, 0, 10, 10]
        ap.Resources = pikepdf.Dictionary(XObject=xobjects)
        annot = pikepdf.Dictionary(
            Type=pikepdf.Name.Annot,
            Subtype=pikepdf.Name.Square,
            Rect=[0, 0, 10, 10],
            AP=pikepdf.Dictionary(N=ap),
        )
        pdf.pages[0].Annots = pdf.make_indirect(pikepdf.Array([annot]))
    return pdf


def _bytes(pdf) -> bytes:
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _glyphs(pdf_bytes: bytes):
    out = []
    flags = pymupdf.TEXTFLAGS_RAWDICT | pymupdf.TEXT_INHIBIT_SPACES
    for page in pymupdf.open(stream=pdf_bytes, filetype="pdf"):
        for block in page.get_text("rawdict", flags=flags)["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    out += [(ch["c"], *ch["origin"]) for ch in span["chars"]]
    return out


def _text(pdf_bytes: bytes):
    return [page.get_text() for page in pymupdf.open(stream=pdf_bytes, filetype="pdf")]


def _max_shift(before: bytes, after: bytes) -> float:
    a, b = _glyphs(before), _glyphs(after)
    assert [g[0] for g in a] == [g[0] for g in b]
    return max((math.hypot(x1 - x2, y1 - y2) for (_, x1, y1), (_, x2, y2) in zip(a, b)), default=0)


def _contents(pdf, page=0) -> bytes:
    return pdf.pages[page].Contents.read_bytes()


def _dvips_like(seed: int, lines=30) -> bytes:
    """A page positioned like dvips + Ghostscript output: five-decimal kerns and Td."""
    rng = random.Random(seed)
    ops = [b"BT /F1 9.96264 Tf 1 0 0 1 40 280 Tm"]
    for _ in range(lines):
        items = []
        for word in rng.sample(["kerning", "glyph", "dvips", "a b", "TeX", "Wally"], 4):
            for ch in word:
                items.append(b"(%s) %.5f" % (ch.encode(), rng.uniform(-9, 9)))
            items.append(b"() %.5f" % rng.uniform(-360, -330))
        ops.append(b"[" + b" ".join(items) + b"] TJ")
        ops.append(b"%.4f %.4f Td" % (rng.uniform(-0.5, 0.5), -8 - rng.uniform(0, 0.01)))
    return b" ".join(ops + [b"ET"])


# --- arguments ---


def test_parse_defaults():
    assert _parse_args([], 3) == ([1, 2, 3], 0.005)


def test_parse_pages_and_tolerance():
    assert _parse_args(["2-3", "1", "tolerance=0.02"], 4) == ([1, 2, 3], 0.02)


@pytest.mark.parametrize("raw", ["0", "-1", "x", "nan", "inf"])
def test_parse_rejects_bad_tolerance(raw):
    with pytest.raises(InvalidArgumentError, match="positive number"):
        _parse_args([f"tolerance={raw}"], 1)


def test_parse_rejects_unknown_keys():
    with pytest.raises(InvalidArgumentError):
        _parse_args(["eps=1"], 1)


# --- pages, measured with MuPDF ---


@pytest.mark.parametrize("tolerance", [0.001, 0.005, 0.02])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_glyphs_stay_within_tolerance_and_text_is_unchanged(seed, tolerance):
    pdf = _doc(_dvips_like(seed))
    before = _bytes(pdf)
    result = round_text_positions_op(pdf, [f"tolerance={tolerance}"])
    after = _bytes(pdf)
    assert result.success and "rewrote 1 stream" in result.summary
    assert _max_shift(before, after) <= tolerance + 2e-4  # MuPDF reports float32 origins
    assert _text(after) == _text(before)
    assert len(after) < len(before)


def test_vertical_text_stays_within_tolerance():
    content = b"BT /V1 20 Tf 1 0 0 1 150 280 Tm [<0421> 0.1 <0422> 0.13 <0423> -0.4 <0424>] TJ ET"
    pdf = _doc(content)
    before = _bytes(pdf)
    round_text_positions_op(pdf, [])
    assert _contents(pdf) == b"BT\n/V1 20 Tf\n1 0 0 1 150 280 Tm\n<0421042204230424> Tj\nET"
    assert _max_shift(before, _bytes(pdf)) <= 0.005 + 2e-4


def test_only_chosen_pages_change():
    pdf = _doc(_dvips_like(1), _dvips_like(2))
    first, second = _contents(pdf, 0), _contents(pdf, 1)
    round_text_positions_op(pdf, ["1"])
    assert _contents(pdf, 0) != first
    assert _contents(pdf, 1) == second


def test_split_contents_are_coalesced_when_rewritten():
    pdf = _doc(b"")
    page = pdf.pages[0]
    page.Contents = pikepdf.Array(
        [pdf.make_stream(b"BT /F1 10 Tf"), pdf.make_stream(b"[(a) 0.3 (b)] TJ ET")]
    )
    round_text_positions_op(pdf, [])
    assert _contents(pdf) == b"BT\n/F1 10 Tf\n(ab) Tj\nET"


def test_stream_is_kept_when_the_rewrite_is_not_smaller():
    content = b"BT/F1 10 Tf[(a)1.1(b)]TJ ET"  # unparsing adds more than rounding saves
    pdf = _doc(content)
    result = round_text_positions_op(pdf, [])
    assert _contents(pdf) == content
    assert "kept 1 unchanged" in result.summary


def test_stream_with_nothing_to_round_is_not_touched():
    content = b"BT /F1 10 Tf [(a) -333 (b)] TJ 72 700 Td (c) Tj ET"
    pdf = _doc(content)
    round_text_positions_op(pdf, [])
    assert _contents(pdf) == content


def test_unparseable_stream_is_left_alone(caplog, monkeypatch):
    pdf = _doc(b"BT /F1 10 Tf [(a) 0.3 (b)] TJ ET")

    def broken(*_):
        raise pikepdf.PdfError("bad stream")

    monkeypatch.setattr(pikepdf, "parse_content_stream", broken)
    round_text_positions_op(pdf, [])
    assert "cannot parse" in caplog.text


def test_page_without_contents_is_skipped():
    pdf = _doc(b"BT /F1 10 Tf [(a) 0.3 (b)] TJ ET", b"")
    del pdf.pages[1].obj["/Contents"]
    assert round_text_positions_op(pdf, []).success
    assert _contents(pdf) == b"BT\n/F1 10 Tf\n(ab) Tj\nET"


def test_font_missing_from_resources_blocks_rounding():
    content = b"BT /Nope 10 Tf [(a) 0.3 (b)] TJ ET"
    pdf = _doc(content)
    round_text_positions_op(pdf, [])
    assert _contents(pdf) == content


def test_form_drawn_only_by_an_annotation_is_left_alone():
    assert _form_after(pages=[b"BT ET"], annot_form=True) == FORM


# --- forms ---


def _form_after(**kw) -> bytes:
    pages = kw.pop("pages", [b"/X0 Do"])
    args = kw.pop("args", [])
    pdf = _doc(*pages, form=kw.pop("form", FORM), **kw)
    round_text_positions_op(pdf, args)
    return pdf.pages[0].Resources.XObject.X0.read_bytes()


ROUNDED_FORM = b"BT\n/F1 10 Tf\n1 0 0 1 5 5 Tm\n(Form) Tj\nET"


def test_form_drawn_at_page_scale_is_rewritten():
    assert _form_after() == ROUNDED_FORM


def test_form_drawn_larger_somewhere_is_bounded_by_that_use():
    # At 10x a 0.3 kern (0.003 pt in the form) moves 0.03 pt on the page.
    assert _form_after(pages=[b"/X0 Do q 10 0 0 10 0 0 cm /X0 Do Q"]) == FORM


def test_form_matrix_counts_towards_the_scale():
    assert _form_after(form_matrix=[10, 0, 0, 10, 0, 0]) == FORM


def test_form_drawn_on_an_unchosen_page_is_left_alone():
    assert _form_after(pages=[b"/X0 Do", b"/X0 Do"], args=["1"]) == FORM


def test_form_used_by_an_annotation_is_left_alone():
    assert _form_after(annot_form=True) == FORM


@pytest.mark.parametrize(
    "page",
    [
        b"1 0 0 1 x 0 cm /X0 Do",  # matrix we cannot read
        b"Q /X0 Do",  # unmatched Q
    ],
)
def test_form_drawn_under_an_unknown_matrix_is_left_alone(page):
    assert _form_after(pages=[page]) == FORM


def test_form_with_unreadable_matrix_is_left_alone():
    assert _form_after(form_matrix=[1, 0, 0, pikepdf.Name.X, 0, 0]) == FORM


def test_form_inherits_horizontal_scaling_from_its_uses():
    # A 0.9 kern moves 0.0045 pt at 50 Tz, within 0.005, but 0.009 pt at 100 Tz.
    kerns = b" ".join(b"(%c) %.5f" % (97 + i % 26, 0.88 + i * 0.00137) for i in range(30))
    form = b"BT /F1 10 Tf [" + kerns + b" (z)] TJ ET"
    half = _form_after(form=form, pages=[b"50 Tz /X0 Do"])
    full = _form_after(form=form, pages=[b"/X0 Do"])
    assert half != form and full != form
    assert half.count(b"(") < full.count(b"(")
    # Uses disagree, or a Tz cannot be read: scaling unknown, kerns left alone.
    assert _form_after(form=form, pages=[b"q 50 Tz /X0 Do Q /X0 Do"]) == form
    assert _form_after(form=form, pages=[b"x Tz /X0 Do"]) == form


def test_nested_form_is_bounded_by_the_composed_matrix():
    outer = b"q 10 0 0 10 0 0 cm /X0 Do Q"
    pdf = _doc(b"/X1 Do", form=FORM)
    x0 = pdf.pages[0].Resources.XObject.X0
    x1 = pdf.make_stream(outer)
    x1.Type, x1.Subtype, x1.BBox = pikepdf.Name.XObject, pikepdf.Name.Form, [0, 0, 300, 300]
    x1.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(X0=x0))
    pdf.pages[0].Resources.XObject.X1 = x1
    round_text_positions_op(pdf, [])
    assert x0.read_bytes() == FORM


def test_self_drawing_form_is_left_alone():
    # A translation-only self-use would be safe (same scale); a scaling one never ends.
    pdf = _doc(b"/X0 Do", form=FORM + b" /X0 Do")
    x0 = pdf.pages[0].Resources.XObject.X0
    x0.Resources.XObject = pikepdf.Dictionary(X0=x0)
    x0.Matrix = [2, 0, 0, 2, 0, 0]  # every nested use is larger, so recursion runs to the cap
    round_text_positions_op(pdf, [])
    assert x0.read_bytes() == FORM + b" /X0 Do"


def test_form_with_too_many_distinct_uses_is_left_alone(monkeypatch):
    monkeypatch.setattr(rtp, "_MAX_FORM_USES", 1)
    assert _form_after(pages=[b"/X0 Do q 2 0 0 2 0 0 cm /X0 Do Q"]) == FORM


def test_repeated_identical_uses_are_followed_once():
    assert _form_after(pages=[b"/X0 Do " * 50]) == ROUNDED_FORM


def test_images_and_missing_xobjects_are_ignored():
    assert _form_after(pages=[b"/Im0 Do /Missing Do /X0 Do"]) == ROUNDED_FORM


def test_form_resources_fall_back_to_the_callers():
    pdf = _doc(b"/X0 Do", form=FORM)
    x0 = pdf.pages[0].Resources.XObject.X0
    del x0["/Resources"]
    round_text_positions_op(pdf, [])
    assert x0.read_bytes() == ROUNDED_FORM


def test_form_glyphs_on_the_page_stay_within_tolerance():
    pdf = _doc(b"q 3 0 0 3 10 10 cm /X0 Do Q", form=_dvips_like(4, lines=5))
    before = _bytes(pdf)
    round_text_positions_op(pdf, [])
    after = _bytes(pdf)
    assert after != before
    assert _max_shift(before, after) <= 0.005 + 2e-4
    assert np.array_equal(np.array(_text(after)), np.array(_text(before)))


def test_stream_the_parser_would_truncate_is_left_alone():
    # Trailing operands with no operator: the parser drops them without complaint.
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica
        )
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    body = b"".join(
        b"BT /F1 12 Tf 10 %d Td [(A) -0.00123 (B) 0.00456 (C)] TJ ET\n" % (10 + i)
        for i in range(40)
    )
    content = body + b"0.5 0.5"
    page.Contents = pdf.make_stream(content)
    round_text_positions_op(pdf, ["tolerance=0.02"])
    assert page.Contents.read_bytes() == content


def test_trial_tolerances_halve_down_to_the_default():
    assert rtp.trial_tolerances(0.02) == [0.02, 0.01, 0.005]
    assert rtp.trial_tolerances(0.005) == [0.005]
    assert rtp.trial_tolerances(0.001) == [0.001]


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_looser_tolerance_never_compresses_worse_than_the_default(seed):
    loose, tight = _doc(_dvips_like(seed)), _doc(_dvips_like(seed))
    round_text_positions_op(loose, ["tolerance=0.02"])
    round_text_positions_op(tight, ["tolerance=0.005"])
    size = lambda pdf: len(zlib.compress(_contents(pdf), 6))  # noqa: E731
    assert size(loose) <= size(tight)
    assert _max_shift(_bytes(_doc(_dvips_like(seed))), _bytes(loose)) <= 0.02 + 2e-4
