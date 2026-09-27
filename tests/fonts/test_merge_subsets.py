# tests/fonts/test_merge_subsets.py
#
# Ground truth: pdfium renders before and after (each glyph is a box of its
# own size, so a wrong glyph shows), and glyph counts in the merged program.

import io
import os

import numpy as np
import pikepdf
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable

import pdftl.fonts.merge_subsets as ms
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.merge_font_subsets import merge_font_subsets_op

BOXES = {"A": (50, 0, 450, 600), "B": (50, 0, 450, 300), "C": (200, 0, 300, 700)}


def _box(x0, y0, x1, y1):
    pen = TTGlyphPen(None)
    pen.moveTo((x0, y0))
    pen.lineTo((x0, y1))
    pen.lineTo((x1, y1))
    pen.lineTo((x1, y0))
    pen.closePath()
    return pen.glyph()


def _subset(
    names,
    upem=1000,
    fpgm=None,
    composite=False,
    hdmx=False,
    cmap=None,
    keep_names=False,
    boxes=None,
) -> bytes:
    """A TrueType program holding .notdef, a blank space and `names`, in that order."""
    order = [".notdef", "space", *names]
    glyphs = {".notdef": _box(0, 0, 0, 0), "space": TTGlyphPen(None).glyph()}
    glyphs.update({n: _box(*(boxes or BOXES)[n]) for n in names})
    if composite:
        pen = TTGlyphPen(glyphs)
        pen.addComponent(names[0], (1, 0, 0, 1, 300, 0))
        glyphs["AC"] = pen.glyph()
        order.append("AC")
    fb = FontBuilder(upem, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap or {})
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({n: (500, 0) for n in order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Boxes", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost(keepGlyphNames=keep_names)
    if fpgm is not None:
        table = newTable("fpgm")
        from fontTools.ttLib.tables import ttProgram

        table.program = ttProgram.Program()
        table.program.fromBytecode(fpgm)
        fb.font["fpgm"] = table
    if hdmx:
        table = newTable("hdmx")
        table.hdmx = {12: {n: 6 for n in order}}
        fb.font["hdmx"] = table
    buf = io.BytesIO()
    fb.save(buf)
    return buf.getvalue()


def _type0(pdf, tag, program: bytes, **cid_extra):
    stream = pdf.make_stream(program, Length1=len(program))
    descriptor = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.FontDescriptor,
            FontName=pikepdf.Name(f"/{tag}+Boxes"),
            Flags=4,
            FontBBox=[0, -200, 1000, 800],
            ItalicAngle=0,
            Ascent=800,
            Descent=-200,
            CapHeight=700,
            StemV=80,
            FontFile2=stream,
        )
    )
    cid_font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.CIDFontType2,
            BaseFont=pikepdf.Name(f"/{tag}+Boxes"),
            CIDSystemInfo=pikepdf.Dictionary(Registry="Adobe", Ordering="Identity", Supplement=0),
            FontDescriptor=descriptor,
            DW=500,
            CIDToGIDMap=pikepdf.Name.Identity,
            **cid_extra,
        )
    )
    return pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type0,
            BaseFont=pikepdf.Name(f"/{tag}+Boxes"),
            Encoding=pikepdf.Name("/Identity-H"),
            DescendantFonts=[cid_font],
        )
    )


def _doc(*fonts_and_text):
    """One page per (program, cids) pair, each drawing its CIDs."""
    pdf = pikepdf.new()
    for i, (program, cids) in enumerate(fonts_and_text):
        tag = "ABCDE" + "FGHIJKLMNOP"[i]
        page = pdf.add_blank_page(page_size=(300, 100))
        page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=_type0(pdf, tag, program)))
        text = "".join(f"{c:04X}" for c in cids)
        page.Contents = pdf.make_stream(f"BT /F1 40 Tf 10 30 Td <{text}> Tj ET".encode())
    return pdf


def _render(pdf):
    import pypdfium2

    buf = io.BytesIO()
    pdf.save(buf)
    doc = pypdfium2.PdfDocument(buf.getvalue())
    try:
        return [np.asarray(p.render(scale=2).to_pil().convert("L")) for p in doc]
    finally:
        doc.close()


def _programs(pdf):
    return {
        font.DescendantFonts[0].FontDescriptor.FontFile2.objgen
        for page in pdf.pages
        for font in page.Resources.Font.values()
    }


def test_subsets_merge_into_one_program_and_render_the_same():
    pdf = _doc(
        (_subset(["A", "B"], hdmx=True), [2, 3, 1]),  # A=2 B=3, then a space
        (_subset(["B", "C"]), [2, 3]),  # B=2 C=3: B's GID differs
        (_subset(["C", "A"]), [3, 2]),
    )
    before = _render(pdf)
    stats = ms.merge_font_subsets(pdf)
    assert (stats.groups, stats.programs_merged) == (1, 3)
    assert len(_programs(pdf)) == 1
    merged = TTFont(
        io.BytesIO(
            next(iter(pdf.pages[0].Resources.Font.values()))
            .DescendantFonts[0]
            .FontDescriptor.FontFile2.read_bytes()
        )
    )
    assert merged["maxp"].numGlyphs == 5  # .notdef, space, A, B, C once each
    after = _render(pdf)
    assert all((a == b).all() for a, b in zip(before, after))
    assert any(a.min() < 128 for a in before)  # glyphs really are drawn


def _set_modified(program: bytes, modified: int) -> bytes:
    """`program` with its head.modified set to `modified` and kept on save."""
    font = TTFont(io.BytesIO(program))
    font["head"].modified = modified
    font.recalcTimestamp = False
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()


def test_merge_is_deterministic_and_keeps_the_latest_input_timestamp(monkeypatch):
    # Real mac-epoch timestamps: head.decompile() treats a smaller raw value as a
    # (buggy) unix timestamp and offsets it, which would defeat a low fixture value.
    first = _set_modified(_subset(["A", "B"]), 3_000_000_000)
    second = _set_modified(_subset(["B", "C"]), 3_100_000_000)

    def merged_program(seed):
        pdf = _doc((first, [2, 3]), (second, [2, 3]))
        monkeypatch.setattr("fontTools.ttLib.tables._h_e_a_d.timestampNow", lambda: seed)
        assert ms.merge_font_subsets(pdf).groups == 1
        font = next(iter(pdf.pages[0].Resources.Font.values())).DescendantFonts[0]
        return font.FontDescriptor.FontFile2.read_bytes()

    merged_a = merged_program(111)
    merged_b = merged_program(999)  # a different "current time" between the two runs
    assert merged_a == merged_b
    # the later of the two inputs' timestamps, not the mocked "current time"
    assert TTFont(io.BytesIO(merged_a))["head"].modified == 3_100_000_000


def _polygon(points):
    pen = TTGlyphPen(None)
    pen.moveTo(points[0])
    for point in points[1:]:
        pen.lineTo(point)
    pen.closePath()
    return pen.glyph()


def _cid_program_with_extra(names, extra_name=None, extra_glyph=None, extra_metrics=(500, 0)):
    """A CID-drawn program like `_subset`, plus one glyph with its own metrics."""
    order = [".notdef", "space", *names]
    glyphs = {".notdef": _box(0, 0, 0, 0), "space": TTGlyphPen(None).glyph()}
    glyphs.update({n: _box(*BOXES[n]) for n in names})
    metrics = {n: (500, 0) for n in order}
    if extra_name:
        order.append(extra_name)
        glyphs[extra_name] = extra_glyph
        metrics[extra_name] = extra_metrics
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({})
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Boxes", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost(keepGlyphNames=False)
    buf = io.BytesIO()
    fb.save(buf)
    return buf.getvalue()


def test_union_maxp_and_hhea_cover_every_member_not_only_member_zero():
    # A 12-point, 1-contour polygon, counted here by hand rather than by the code under test.
    wide_points = [
        (0, 0),
        (100, 0),
        (150, 50),
        (200, 100),
        (150, 150),
        (100, 200),
        (50, 200),
        (0, 150),
        (-50, 100),
        (-30, 60),
        (10, 30),
        (30, 10),
    ]
    assert len(wide_points) == 12

    first = _cid_program_with_extra(["A", "B"])  # BOXES glyphs: 4 points, 1 contour each
    second = _cid_program_with_extra(
        ["B", "C"], extra_name="Z", extra_glyph=_polygon(wide_points), extra_metrics=(900, -50)
    )
    pdf = _doc((first, [2, 3]), (second, [2, 3, 4]))
    assert ms.merge_font_subsets(pdf).groups == 1
    font = next(iter(pdf.pages[0].Resources.Font.values())).DescendantFonts[0]
    merged = TTFont(io.BytesIO(font.FontDescriptor.FontFile2.read_bytes()))
    assert merged["maxp"].maxPoints == 12  # "Z", from the second member, not "A"/"B"'s 4
    assert merged["maxp"].maxContours == 1
    assert merged["hhea"].advanceWidthMax == 900  # "Z"'s width, wider than any box's 500
    assert merged["hhea"].minLeftSideBearing == -50  # "Z"'s (negative) left side bearing


@pytest.mark.parametrize(
    "first,second",
    [
        (["A", "B"], ["B", "A"]),  # AC draws A shifted, then B shifted: two composites
        (["A", "B"], ["A", "C"]),  # both AC draw A shifted: one shared composite
    ],
    ids=["distinct", "shared"],
)
def test_composite_glyphs_keep_their_components(first, second):
    pdf = _doc(
        (_subset(first, composite=True), [4]),
        (_subset(second, composite=True), [4]),
    )
    before = _render(pdf)
    assert ms.merge_font_subsets(pdf).groups == 1
    assert all((a == b).all() for a, b in zip(before, _render(pdf)))


@pytest.mark.parametrize(
    "first,other",
    [
        (_subset(["A", "B"]), _subset(["B", "C"], upem=2048)),
        (_subset(["A", "B"]), _subset(["B", "C"], fpgm=b"\xb0\x00")),  # only one has one
        (_subset(["A", "B"], fpgm=b"\xb0\x01"), _subset(["B", "C"], fpgm=b"\xb0\x00")),
    ],
    ids=["units-per-em", "fpgm-missing", "fpgm-differs"],
)
def test_programs_that_disagree_outside_the_glyphs_stay_apart(first, other):
    pdf = _doc((first, [2]), (other, [2]))
    assert ms.merge_font_subsets(pdf).groups == 0
    assert len(_programs(pdf)) == 2


def test_only_identity_cid_truetype_fonts_are_candidates():
    pdf = _doc((_subset(["A", "B"]), [1]), (_subset(["B", "C"]), [1]))
    fonts = [next(iter(p.Resources.Font.values())) for p in pdf.pages]
    fonts[0].Encoding = pikepdf.Name("/UniGB-UCS2-H")
    assert ms._candidates(pdf) == {}
    fonts[0].Encoding = pikepdf.Name("/Identity-H")
    fonts[1].DescendantFonts[0].CIDToGIDMap = pdf.make_stream(b"\x00\x01")
    assert ms._candidates(pdf) == {}
    fonts[1].DescendantFonts[0].CIDToGIDMap = pikepdf.Name.Identity
    assert len(ms._candidates(pdf)) == 1
    fonts[1].DescendantFonts[0].Subtype = pikepdf.Name.CIDFontType0
    assert ms._candidates(pdf) == {}
    fonts[1].DescendantFonts[0].Subtype = pikepdf.Name.CIDFontType2
    del fonts[1].DescendantFonts[0].FontDescriptor["/FontFile2"]
    assert ms._candidates(pdf) == {}
    fonts[1].DescendantFonts = pikepdf.Array()
    assert ms._candidates(pdf) == {}


def _without_hmtx(program: bytes) -> bytes:
    font = TTFont(io.BytesIO(program))
    del font["hmtx"]
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()


@pytest.mark.parametrize(
    "damage",
    [
        lambda s: s.write(b"not a font"),
        lambda s: s.write(s.read_bytes()[:-60]),  # the directory reads; a table is cut short
        lambda s: s.write(b"garbage", filter=pikepdf.Name.FlateDecode),
        lambda s: s.write(_without_hmtx(s.read_bytes())),
    ],
    ids=["not-a-font", "truncated", "undecodable", "no-hmtx"],
)
def test_damaged_program_is_not_merged(damage):
    pdf = _doc((_subset(["A", "B"]), [1]), (_subset(["B", "C"]), [1]))
    fonts = [next(iter(p.Resources.Font.values())) for p in pdf.pages]
    damage(fonts[1].DescendantFonts[0].FontDescriptor.FontFile2)
    assert ms.merge_font_subsets(pdf).groups == 0
    assert len(_programs(pdf)) == 2


def test_group_whose_merge_fails_or_does_not_pay_is_left_alone(monkeypatch):
    pdf = _doc((_subset(["A", "B"]), [1]), (_subset(["B", "C"]), [1]))

    def boom(members):
        raise ValueError("cannot build")

    monkeypatch.setattr(ms, "_build_union", boom)
    assert ms.merge_font_subsets(pdf).groups == 0

    def too_big(members):
        for member in members:
            member.gid_map = {0: 0, 1: 1}
        return os.urandom(100000)  # incompressible

    monkeypatch.setattr(ms, "_build_union", too_big)
    assert ms.merge_font_subsets(pdf).groups == 0
    assert len(_programs(pdf)) == 2


@pytest.mark.parametrize(
    "name,base",
    [
        ("ABCDEF+Calibri", "Calibri"),
        ("Calibri", "Calibri"),
        ("ABC+Calibri", "ABC+Calibri"),
        ("abcdef+Calibri", "abcdef+Calibri"),
    ],
)
def test_base_name(name, base):
    assert ms._base_name(name) == base


def test_cid_to_gid_map_bytes():
    assert ms._cid_to_gid({0: 0, 2: 5, 1: 258}) == b"\x00\x00\x01\x02\x00\x05"


def test_operation_reports_and_rejects_arguments():
    pdf = _doc((_subset(["A", "B"]), [1]), (_subset(["B", "C"]), [1]))
    result = merge_font_subsets_op(pdf, [])
    assert result.data["groups"] == 1 and result.data["programs_merged"] == 2
    with pytest.raises(InvalidArgumentError, match="takes no arguments"):
        merge_font_subsets_op(pdf, ["x"])


# --- simple TrueType fonts ---

CODES = {"A": 0x41, "B": 0x42, "C": 0x43}


def _simple_program(names, codes=None, **kw):
    codes = codes or {n: CODES[n] for n in names}
    return _subset(names, cmap={code: n for n, code in codes.items()}, **kw)


def _simple_doc(*programs_and_text):
    pdf = pikepdf.new()
    for i, (program, text) in enumerate(programs_and_text):
        tag = "ABCDE" + "FGHIJKLMNOP"[i]
        descriptor = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.FontDescriptor,
                FontName=pikepdf.Name(f"/{tag}+Boxes"),
                Flags=32,
                FontBBox=[0, -200, 1000, 800],
                ItalicAngle=0,
                Ascent=800,
                Descent=-200,
                CapHeight=700,
                StemV=80,
                FontFile2=pdf.make_stream(program, Length1=len(program)),
            )
        )
        font = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.TrueType,
                BaseFont=pikepdf.Name(f"/{tag}+Boxes"),
                Encoding=pikepdf.Name.WinAnsiEncoding,
                FirstChar=65,
                LastChar=67,
                Widths=[500, 500, 500],
                FontDescriptor=descriptor,
            )
        )
        page = pdf.add_blank_page(page_size=(300, 100))
        page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        page.Contents = pdf.make_stream(f"BT /F1 40 Tf 10 30 Td ({text}) Tj ET".encode())
    return pdf


def _simple_programs(pdf):
    return {
        font.FontDescriptor.FontFile2.objgen
        for page in pdf.pages
        for font in page.Resources.Font.values()
    }


@pytest.mark.parametrize("keep_names", [False, True], ids=["no-names", "names"])
def test_simple_subsets_merge_when_their_codes_agree(keep_names):
    pdf = _simple_doc(
        (_simple_program(["A", "B"], keep_names=keep_names), "AB"),
        (_simple_program(["B", "C"], keep_names=keep_names), "BC"),
    )
    before = _render(pdf)
    stats = ms.merge_font_subsets(pdf)
    assert (stats.groups, stats.programs_merged) == (1, 2)
    assert len(_simple_programs(pdf)) == 1
    merged = TTFont(
        io.BytesIO(
            next(iter(pdf.pages[0].Resources.Font.values())).FontDescriptor.FontFile2.read_bytes()
        )
    )
    assert merged["post"].formatType == (2.0 if keep_names else 3.0)
    assert all((a == b).all() for a, b in zip(before, _render(pdf)))


def test_simple_subsets_whose_codes_conflict_stay_apart():
    pdf = _simple_doc(
        (_simple_program(["A", "B"]), "AB"),
        (_simple_program(["C"], codes={"C": 0x41}), "A"),  # code 0x41 draws C here
    )
    assert ms.merge_font_subsets(pdf).groups == 0
    assert len(_simple_programs(pdf)) == 2


def test_glyph_names_that_disagree_stop_a_merge():
    wide = {"A": (0, 0, 480, 100), "B": BOXES["B"]}
    pdf = _simple_doc(
        (_simple_program(["A", "B"], keep_names=True), "AB"),
        (_simple_program(["A", "B"], keep_names=True, boxes=wide), "AB"),  # "A", another glyph
    )
    assert ms.merge_font_subsets(pdf).groups == 0
    pdf = _simple_doc(
        (_simple_program(["A", "B"], keep_names=True), "AB"),
        (
            _simple_program(["B", "A"], keep_names=True, boxes={"A": BOXES["B"], "B": BOXES["A"]}),
            "BA",
        ),
    )
    assert ms.merge_font_subsets(pdf).groups == 0  # one outline, two names


def test_simple_subsets_need_the_same_cmap_layout():
    pdf = _simple_doc(
        (_simple_program(["A", "B"]), "AB"),
        (_subset(["B", "C"]), "BC"),  # no cmap entries: an empty format 4 subtable only
    )
    fonts = [next(iter(p.Resources.Font.values())) for p in pdf.pages]
    program = TTFont(io.BytesIO(fonts[1].FontDescriptor.FontFile2.read_bytes()))
    del program["cmap"]
    buf = io.BytesIO()
    program.save(buf)
    fonts[1].FontDescriptor.FontFile2.write(buf.getvalue())
    assert ms.merge_font_subsets(pdf).groups == 0


def test_a_program_drawn_both_ways_is_merged_per_kind():
    pdf = _doc((_subset(["A", "B"]), [2]), (_subset(["B", "C"]), [2]))
    cid_font = next(iter(pdf.pages[0].Resources.Font.values())).DescendantFonts[0]
    original = cid_font.FontDescriptor.FontFile2
    simple = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.TrueType,
        BaseFont=pikepdf.Name("/ABCDEF+Boxes"),
        FontDescriptor=pikepdf.Dictionary(FontFile2=original),
    )
    pdf.pages[1].Resources.Font.F2 = pdf.make_indirect(simple)
    kinds = {simple_kind for simple_kind, _ in ms._candidates(pdf)}
    assert kinds == {False}  # one simple use of the program is no group
    assert ms.merge_font_subsets(pdf).groups == 1
    assert pdf.pages[1].Resources.Font.F2.FontDescriptor.FontFile2.objgen == original.objgen


def test_fonts_without_an_embedded_program_are_ignored():
    pdf = _simple_doc((_simple_program(["A", "B"]), "AB"), (_simple_program(["B", "C"]), "BC"))
    pdf.pages[0].Resources.Font.F9 = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.TrueType, BaseFont=pikepdf.Name.Arial
        )
    )
    assert [len(m) for m in ms._candidates(pdf).values()] == [2]


def _without_post(program: bytes) -> bytes:
    font = TTFont(io.BytesIO(program))
    del font["post"]
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()


def test_programs_without_a_post_table_merge():
    pdf = _doc(
        (_without_post(_subset(["A", "B"])), [2, 3]),
        (_without_post(_subset(["B", "C"])), [2, 3]),
    )
    before = _render(pdf)
    assert ms.merge_font_subsets(pdf).groups == 1
    assert all((a == b).all() for a, b in zip(before, _render(pdf)))
