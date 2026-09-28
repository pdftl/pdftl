# tests/fonts/test_merge_cff_subsets.py
#
# Ground truth: pdfium and MuPDF (and Poppler, when installed) render each
# page before and after the merge; every glyph is a box of its own shape,
# so a code reaching the wrong glyph shows. Expected encodings, glyph sets
# and widths are written out by hand.

import io
import shutil
import subprocess

import numpy as np
import pikepdf
import pymupdf
import pypdfium2
import pytest
from fontTools.cffLib import CFFFontSet, SubrsIndex
from fontTools.fontBuilder import FontBuilder
from fontTools.misc.psCharStrings import T2CharString, T2WidthExtractor
from fontTools.pens.t2CharStringPen import T2CharStringPen
from PIL import Image

import pdftl.fonts.merge_cff_subsets as mc
from pdftl.fonts.merge_subsets import merge_font_subsets
from pdftl.operations.merge_font_subsets import merge_font_subsets_op

BOXES = {
    "A": (50, 0, 450, 600),
    "B": (50, 0, 450, 300),
    "C": (200, 0, 300, 700),
    "D": (0, 400, 500, 700),
    "E": (300, 0, 500, 200),
}


def _outline(box) -> list:
    pen = T2CharStringPen(None, None)
    x0, y0, x1, y1 = box
    pen.moveTo((x0, y0))
    pen.lineTo((x1, y0))
    pen.lineTo((x1, y1))
    pen.lineTo((x0, y1))
    pen.closePath()
    return pen.getCharString().program  # ends in endchar


def _cff(
    names,
    *,
    builtin="StandardEncoding",
    widths=None,
    nominal=0,
    default=0,
    private=None,
    boxes=None,
    subrs=False,
    recursive=False,
) -> bytes:
    """A bare CFF program holding .notdef and `names`; each glyph a box."""
    boxes = {**BOXES, **(boxes or {})}
    widths = widths or {}
    fb = FontBuilder(1000, isTTF=False)
    order = [".notdef", *names]
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({})
    outlines = {n: (["endchar"] if n == ".notdef" else _outline(boxes[n])) for n in order}
    charstrings = {}
    for name in order:
        width = widths.get(name, 500)
        prefix = [] if width == default else [width - nominal]
        charstrings[name] = T2CharString(program=[*prefix, *outlines[name]])
    fb.setupCFF(
        "Boxes",
        {"FullName": "Boxes"},
        charstrings,
        {"nominalWidthX": nominal, "defaultWidthX": default, **(private or {})},
    )
    cff = fb.font["CFF "].cff
    top = cff[cff.fontNames[0]]
    top.Encoding = builtin
    if subrs or recursive:
        _move_outlines_into_subrs(top, order, outlines, widths, nominal, default, recursive)
    fb.font.recalcBBoxes = False  # a runaway subroutine must reach the merge
    buf = io.BytesIO()
    cff.compile(buf, fb.font)
    return buf.getvalue()


def _move_outlines_into_subrs(top, order, outlines, widths, nominal, default, recursive):
    """Each glyph calls a local subroutine holding its outline (bias 107)."""
    private = top.Private
    private.Subrs = SubrsIndex()
    for index, name in enumerate(order):
        body = [-107, "callsubr", "return"] if recursive else [*outlines[name][:-1], "return"]
        private.Subrs.append(T2CharString(program=body, private=private))
        width = widths.get(name, 500)
        prefix = [] if width == default else [width - nominal]
        top.CharStrings[name].program = [*prefix, index - 107, "callsubr", "endchar"]


def _font(pdf, tag, program, *, flags=4, encoding=pikepdf.Name.WinAnsiEncoding, charset=None):
    descriptor = pikepdf.Dictionary(
        Type=pikepdf.Name.FontDescriptor,
        FontName=pikepdf.Name(f"/{tag}+Boxes"),
        FontBBox=[0, 0, 500, 700],
        ItalicAngle=0,
        Ascent=700,
        Descent=0,
        CapHeight=700,
        StemV=80,
        FontFile3=pdf.make_stream(program, Subtype=pikepdf.Name.Type1C),
    )
    if flags is not None:
        descriptor.Flags = flags
    if charset is not None:
        descriptor.CharSet = pikepdf.String(charset)
    font = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.Type1,
        BaseFont=pikepdf.Name(f"/{tag}+Boxes"),
        FirstChar=0,
        LastChar=255,
        Widths=[600] * 256,
        FontDescriptor=pdf.make_indirect(descriptor),
    )
    if encoding is not None:
        font.Encoding = encoding
    return pdf.make_indirect(font)


def _doc(*fonts_and_text):
    """One page per (font kwargs, program, text) triple, drawing `text`."""
    pdf = pikepdf.new()
    for i, (program, text, kwargs) in enumerate(fonts_and_text):
        font = _font(pdf, "ABCDE" + "FGHIJKLMNOP"[i], program, **kwargs)
        pdf.add_blank_page(page_size=(400, 100))
        page = pdf.pages[-1]
        page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        page.Contents = pdf.make_stream(
            b"BT /F1 60 Tf 10 20 Td <" + text.hex().encode() + b"> Tj ET"
        )
    return pdf


def _save(pdf) -> bytes:
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _renders(data: bytes) -> list:
    out = []
    doc = pypdfium2.PdfDocument(data)
    mu = pymupdf.open(stream=data, filetype="pdf")
    for i in range(len(doc)):
        out.append(np.asarray(doc[i].render(scale=1).to_pil().convert("L")))
        pix = mu[i].get_pixmap(dpi=72)
        out.append(
            np.asarray(Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L"))
        )
    if shutil.which("pdftoppm"):
        run = subprocess.run(
            ["pdftoppm", "-r", "72", "-gray", "-"], input=data, capture_output=True, check=True
        )
        out.append(run.stdout)
    return out


def _assert_same_rendering(before: bytes, after: bytes):
    for a, b in zip(_renders(before), _renders(after), strict=True):
        assert np.array_equal(np.asarray(a), np.asarray(b))


def _programs(pdf) -> set:
    return {f.FontDescriptor.FontFile3.objgen for f in _fonts(pdf)}


def _fonts(pdf) -> list:
    return [p.Resources.Font.F1 for p in pdf.pages]


def _top(pdf):
    cff = CFFFontSet()
    cff.decompile(io.BytesIO(_fonts(pdf)[0].FontDescriptor.FontFile3.read_bytes()), None)
    return cff[cff.fontNames[0]]


def _width(top, name) -> float:
    charstring = top.CharStrings[name]
    extractor = T2WidthExtractor(
        [], [], top.Private.nominalWidthX, top.Private.defaultWidthX, private=top.Private
    )
    extractor.execute(charstring)
    return extractor.width


def _merge(pdf):
    before = _save(pdf)
    stats = merge_font_subsets(pdf)
    return before, stats


def test_disjoint_subsets_merge_and_render_the_same():
    pdf = _doc((_cff(["A", "B"]), b"AB", {}), (_cff(["C", "D"]), b"CD", {}))
    before, stats = _merge(pdf)
    assert (stats.groups, stats.programs_merged) == (1, 2)
    assert stats.bytes_saved > 0
    assert len(_programs(pdf)) == 1
    assert sorted(_top(pdf).charset) == [".notdef", "A", "B", "C", "D"]
    _assert_same_rendering(before, _save(pdf))


def test_shared_glyphs_are_stored_once():
    pdf = _doc((_cff(["A", "B"]), b"AB", {}), (_cff(["B", "C"]), b"BC", {}))
    before, stats = _merge(pdf)
    assert stats.programs_merged == 2
    assert _top(pdf).charset == [".notdef", "A", "B", "C"]
    _assert_same_rendering(before, _save(pdf))


def test_a_name_drawn_differently_stops_the_merge():
    other = _cff(["B"], boxes={"B": (0, 0, 100, 100)})
    pdf = _doc((_cff(["A", "B"]), b"AB", {}), (other, b"B", {}))
    _, stats = _merge(pdf)
    assert stats.programs_merged == 0
    assert len(_programs(pdf)) == 2


@pytest.mark.parametrize(
    "private",
    [{"BlueValues": [-10, 0, 700, 710]}, {"StdVW": [90]}],
    ids=["blue-values", "stem-width"],
)
def test_different_hinting_stops_the_merge(private):
    pdf = _doc((_cff(["A"]), b"A", {}), (_cff(["B"], private=private), b"B", {}))
    _, stats = _merge(pdf)
    assert stats.programs_merged == 0


def test_widths_survive_different_nominal_and_default_widths():
    first = _cff(["A", "B"], widths={"A": 610, "B": 480}, nominal=600, default=480)
    second = _cff(["C"], widths={"C": 350}, nominal=0, default=0)
    pdf = _doc((first, b"AB", {}), (second, b"C", {}))
    before, stats = _merge(pdf)
    assert stats.programs_merged == 2
    top = _top(pdf)
    assert {n: _width(top, n) for n in ("A", "B", "C")} == {"A": 610, "B": 480, "C": 350}
    _assert_same_rendering(before, _save(pdf))


def test_subroutines_are_inlined():
    pdf = _doc((_cff(["A", "B"], subrs=True), b"AB", {}), (_cff(["C"], subrs=True), b"C", {}))
    before, stats = _merge(pdf)
    assert stats.programs_merged == 2
    top = _top(pdf)
    assert not hasattr(top.Private, "Subrs")
    for name in top.charset:
        top.CharStrings[name].decompile()
        assert "callsubr" not in top.CharStrings[name].program
    _assert_same_rendering(before, _save(pdf))


def test_runaway_subroutines_leave_the_group_alone():
    pdf = _doc((_cff(["A"], recursive=True), b"A", {}), (_cff(["B"]), b"B", {}))
    _, stats = _merge(pdf)
    assert stats.programs_merged == 0


def _builtin(**codes) -> list:
    table = [".notdef"] * 256
    for name, code in codes.items():
        table[code] = name
    return table


def test_conflicting_builtin_encodings_get_differences():
    # Codes 0x41 and 0x42 are A and B in the first program, C and D in the second.
    first = _cff(["A", "B"], builtin=_builtin(A=0x41, B=0x42))
    second = _cff(["C", "D", "E"], builtin=_builtin(C=0x41, D=0x42, E=0x43))
    kwargs = {"encoding": None, "flags": 4}
    pdf = _doc((first, b"AB", kwargs), (second, b"ABC", kwargs))
    before, stats = _merge(pdf)
    assert stats.programs_merged == 2
    fonts = _fonts(pdf)
    assert "/Encoding" not in fonts[0]
    assert list(fonts[1].Encoding.Differences) == [0x41, pikepdf.Name.C, pikepdf.Name.D]
    _assert_same_rendering(before, _save(pdf))


def test_nonsymbolic_font_whose_readings_differ_is_left_alone():
    # pdfium reads 0x41 of the second font as A (StandardEncoding), the others as C.
    first = _cff(["A"], builtin=_builtin(A=0x41))
    second = _cff(["C"], builtin=_builtin(C=0x41))
    kwargs = {"encoding": None, "flags": 32}
    pdf = _doc((first, b"A", kwargs), (second, b"A", kwargs))
    _, stats = _merge(pdf)
    assert stats.programs_merged == 0


@pytest.mark.parametrize("flags", [32, None], ids=["nonsymbolic", "no-flags"])
def test_nonsymbolic_font_with_standard_builtin_merges(flags):
    kwargs = {"encoding": None, "flags": flags}
    pdf = _doc((_cff(["A", "B"]), b"AB", kwargs), (_cff(["C"]), b"C", kwargs))
    before, stats = _merge(pdf)
    assert stats.programs_merged == 2
    _assert_same_rendering(before, _save(pdf))


def test_differences_are_added_to_a_copy_of_a_shared_encoding():
    shared = pikepdf.Dictionary(Type=pikepdf.Name.Encoding, Differences=[0x44, pikepdf.Name.D])
    first = _cff(["A", "D"], builtin=_builtin(A=0x41))
    second = _cff(["C", "D"], builtin=_builtin(C=0x41))
    pdf = _doc((first, b"AD", {"encoding": None}), (second, b"AD", {"encoding": None}))
    enc = pdf.make_indirect(shared)
    for font in _fonts(pdf):
        font.Encoding = enc
    before, stats = _merge(pdf)
    assert stats.programs_merged == 2
    fonts = _fonts(pdf)
    assert fonts[0].Encoding.objgen == enc.objgen
    assert list(enc.Differences) == [0x44, pikepdf.Name.D]
    assert list(fonts[1].Encoding.Differences) == [0x41, pikepdf.Name.C, 0x44, pikepdf.Name.D]
    _assert_same_rendering(before, _save(pdf))


def test_named_encodings_need_no_differences():
    first = _cff(["A", "B"], builtin=_builtin(A=0x41, B=0x42))
    second = _cff(["C"], builtin=_builtin(C=0x41))
    base = pikepdf.Dictionary(BaseEncoding=pikepdf.Name.WinAnsiEncoding)
    pdf = _doc((first, b"AB", {}), (second, b"C", {"encoding": base}))
    before, stats = _merge(pdf)
    assert stats.programs_merged == 2
    assert _fonts(pdf)[0].Encoding == pikepdf.Name.WinAnsiEncoding
    assert "/Differences" not in _fonts(pdf)[1].Encoding
    _assert_same_rendering(before, _save(pdf))


def test_merged_builtin_encoding_is_standard_when_that_fits():
    pdf = _doc((_cff(["A"]), b"A", {"encoding": None}), (_cff(["B"]), b"B", {"encoding": None}))
    _merge(pdf)
    assert _top(pdf).Encoding == "StandardEncoding"


def test_merged_builtin_encoding_is_written_out_otherwise():
    first = _cff(["A"], builtin=_builtin(A=0x61))
    second = _cff(["B"], builtin=_builtin(B=0x62))
    pdf = _doc((first, b"a", {"encoding": None}), (second, b"b", {"encoding": None}))
    before, _ = _merge(pdf)
    encoding = _top(pdf).Encoding
    assert {c: n for c, n in enumerate(encoding) if n != ".notdef"} == {0x61: "A", 0x62: "B"}
    _assert_same_rendering(before, _save(pdf))


def test_charset_lists_the_merged_glyphs():
    kwargs = {"charset": "/A"}
    pdf = _doc((_cff(["A"]), b"A", kwargs), (_cff(["B"]), b"B", {"charset": "/B"}))
    _merge(pdf)
    for font in _fonts(pdf):
        assert bytes(font.FontDescriptor.CharSet) == b"/A/B"


@pytest.mark.parametrize(
    "encoding",
    [
        pikepdf.Name("/SymbolEncoding"),
        pikepdf.Dictionary(BaseEncoding=pikepdf.Name("/Custom")),
        pikepdf.Dictionary(Differences=[pikepdf.Name.A]),
        pikepdf.Dictionary(Differences=pikepdf.Name.A),
        pikepdf.Dictionary(Differences=[65, pikepdf.String("A")]),
        pikepdf.Array([1]),
    ],
    ids=["unknown-name", "unknown-base", "name-before-code", "not-an-array", "string", "array"],
)
def test_encodings_it_does_not_understand_leave_the_group_alone(encoding):
    pdf = _doc((_cff(["A"]), b"A", {}), (_cff(["B"]), b"B", {"encoding": encoding}))
    _, stats = _merge(pdf)
    assert stats.programs_merged == 0


def test_differences_parse():
    array = pikepdf.Array([65, pikepdf.Name.A, pikepdf.Name.B, 97, pikepdf.Name.a])
    assert mc._differences(array) == {65: "A", 66: "B", 97: "a"}
    assert mc._differences(None) == {}


@pytest.mark.parametrize(
    "program",
    [
        _cff(["B"])[:20],
        b"\x01\x00\x04\x01" + b"\x00\x00" * 4,
        _cff(["B"], builtin="ExpertEncoding"),
    ],
    ids=["truncated", "empty-font-set", "expert-encoding"],
)
def test_programs_it_cannot_read_leave_the_group_alone(program):
    pdf = _doc((_cff(["A"]), b"A", {}), (program, b"B", {}))
    _, stats = _merge(pdf)
    assert stats.programs_merged == 0


def test_undecodable_stream_leaves_the_group_alone():
    pdf = _doc((_cff(["A"]), b"A", {}), (_cff(["B"]), b"B", {}))
    stream = _fonts(pdf)[1].FontDescriptor.FontFile3
    stream.write(b"not flate", filter=pikepdf.Name.FlateDecode)
    _, stats = _merge(pdf)
    assert stats.programs_merged == 0


def test_cid_keyed_and_other_programs_are_not_candidates():
    pdf = _doc((_cff(["A"]), b"A", {}), (_cff(["B"]), b"B", {}), (_cff(["C"]), b"C", {}))
    fonts = _fonts(pdf)
    fonts[1].FontDescriptor.FontFile3.Subtype = pikepdf.Name.OpenType
    del fonts[2].FontDescriptor
    assert mc._candidates(pdf) == {}


def test_program_shared_by_two_fonts_is_one_member():
    pdf = _doc((_cff(["A"]), b"A", {}), (_cff(["B"]), b"B", {}), (_cff(["C"]), b"C", {}))
    fonts = _fonts(pdf)
    fonts[2].FontDescriptor.FontFile3 = fonts[1].FontDescriptor.FontFile3
    before, stats = _merge(pdf)
    assert stats.programs_merged == 2
    assert len(_programs(pdf)) == 1
    _assert_same_rendering(before, _save(pdf))


def test_charset_growth_counts_every_descriptor_once():
    parts = [(_cff([n]), n.encode(), {}) for n in "ABCD"]
    pdf = _doc(*parts)
    fonts = _fonts(pdf)
    # One program behind a descriptor without /CharSet and one with it.
    fonts[1].FontDescriptor.FontFile3 = fonts[0].FontDescriptor.FontFile3
    fonts[1].FontDescriptor.CharSet = pikepdf.String("/B")
    # One descriptor shared by two fonts.
    fonts[2].FontDescriptor.CharSet = pikepdf.String("/C")
    fonts[3].FontDescriptor = fonts[2].FontDescriptor
    members = mc._candidates(pdf)["Boxes"]
    assert len(members) == 2
    assert all(mc._load(m) for m in members)
    # "/B" and "/C" each become "/A/B/C": 4 bytes more, each descriptor once.
    assert mc._extra_bytes(members, [".notdef", "A", "B", "C"]) == 8


def test_group_that_does_not_pay_is_left_alone(monkeypatch):
    pdf = _doc((_cff(["A"]), b"A", {}), (_cff(["B"]), b"B", {}))
    monkeypatch.setattr(mc, "_extra_bytes", lambda members, order: 10**6)
    _, stats = _merge(pdf)
    assert stats.programs_merged == 0


def test_operation_counts_type1c_merges():
    pdf = _doc((_cff(["A"]), b"A", {}), (_cff(["B"]), b"B", {}))
    result = merge_font_subsets_op(pdf, [])
    assert result.data["programs_merged"] == 2
