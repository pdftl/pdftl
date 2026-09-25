# tests/fonts/test_font_subsetting_keeps_font_data.py
#
# Subsetting keeps font-level data that renderers use at small pixel sizes:
# embedded bitmap strikes (EBLC/EBDT) and the head table's font-wide bbox.

import io

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from pdftl.fonts.font_subsetting import run_subsetter

BITMAPS = {"A": "ff81", "B": "8142"}  # 8x2 at 1 bpp: one byte per row

_METRICS = """<SmallGlyphMetrics>
  <height value="2"/><width value="8"/><BearingX value="0"/><BearingY value="2"/><Advance value="9"/>
</SmallGlyphMetrics>"""

_LINE_METRICS = "".join(
    f'<{k} value="{v}"/>'
    for k, v in [
        ("ascender", 10),
        ("descender", -2),
        ("widthMax", 5),
        ("caretSlopeNumerator", 1),
        ("caretSlopeDenominator", 0),
        ("caretOffset", 0),
        ("minOriginSB", 0),
        ("minAdvanceSB", 0),
        ("maxBeforeBL", 10),
        ("minAfterBL", -2),
        ("pad1", 0),
        ("pad2", 0),
    ]
)


def _strike_xml():
    locs = "".join(f'<glyphLoc id="{i}" name="{n}"/>' for i, n in enumerate(BITMAPS, 1))
    bitmaps = "".join(
        f'<ebdt_bitmap_format_2 name="{n}">{_METRICS}<rawimagedata>{hexdata}</rawimagedata>'
        "</ebdt_bitmap_format_2>"
        for n, hexdata in BITMAPS.items()
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ttFont sfntVersion="\\x00\\x01\\x00\\x00">
<EBLC><header version="2.0"/><strike index="0"><bitmapSizeTable>
<sbitLineMetrics direction="hori">{_LINE_METRICS}</sbitLineMetrics>
<sbitLineMetrics direction="vert">{_LINE_METRICS}</sbitLineMetrics>
<colorRef value="0"/><startGlyphIndex value="1"/><endGlyphIndex value="2"/>
<ppemX value="12"/><ppemY value="12"/><bitDepth value="1"/><flags value="1"/>
</bitmapSizeTable>
<eblc_index_sub_table_1 imageFormat="2" firstGlyphIndex="1" lastGlyphIndex="2">{locs}</eblc_index_sub_table_1>
</strike></EBLC>
<EBDT><header version="2.0"/><strikedata index="0">{bitmaps}</strikedata></EBDT>
</ttFont>"""


GLYPH_EXTENTS = {"A": (0, 0, 500, 500), "B": (-300, -250, 1800, 1400)}
FONT_BBOX = (-300, -250, 1800, 1400)  # union of the glyphs drawn


def _box(x0, y0, x1, y1):
    pen = TTGlyphPen(None)
    pen.moveTo((x0, y0))
    pen.lineTo((x0, y1))
    pen.lineTo((x1, y1))
    pen.lineTo((x1, y0))
    pen.closePath()
    return pen.glyph()


def _font_with_strike():
    order = [".notdef", *BITMAPS]
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({ord(n): n for n in BITMAPS})
    fb.setupGlyf({n: _box(*GLYPH_EXTENTS.get(n, (0, 0, 500, 500))) for n in order})
    fb.setupHorizontalMetrics({n: (600, 0) for n in order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "BitmapTest", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    buf = io.BytesIO()
    fb.save(buf)
    font = TTFont(io.BytesIO(buf.getvalue()))
    font.importXML(io.StringIO(_strike_xml()))
    buf = io.BytesIO()
    font.save(buf)
    return TTFont(io.BytesIO(buf.getvalue()))


def _strike_bitmaps(font):
    return {name: glyph.imageData.hex() for name, glyph in font["EBDT"].strikeData[0].items()}


def test_fixture_has_the_strike():
    assert _strike_bitmaps(_font_with_strike()) == BITMAPS


def test_subset_keeps_bitmap_strike_for_kept_glyphs():
    font = _font_with_strike()
    assert run_subsetter(font, unicodes={"A"}, gids=set())
    buf = io.BytesIO()
    font.save(buf)
    subset = TTFont(io.BytesIO(buf.getvalue()))
    assert {"EBLC", "EBDT"} <= set(subset.keys())
    assert _strike_bitmaps(subset) == {"A": BITMAPS["A"]}


def _head_bbox(font):
    head = font["head"]
    return (head.xMin, head.yMin, head.xMax, head.yMax)


def test_fixture_head_bbox_spans_all_glyphs():
    assert _head_bbox(_font_with_strike()) == FONT_BBOX


def test_subset_keeps_original_head_bbox():
    # Dropping the large glyph B must not shrink the font-wide bbox to A's.
    font = _font_with_strike()
    assert run_subsetter(font, unicodes={"A"}, gids=set())
    buf = io.BytesIO()
    font.save(buf)
    assert _head_bbox(TTFont(io.BytesIO(buf.getvalue()))) == FONT_BBOX
