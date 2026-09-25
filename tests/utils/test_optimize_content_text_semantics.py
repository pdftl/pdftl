# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/utils/test_optimize_content_text_semantics.py
#
# optimize_positioning_ops must not change anything a page paints. Two
# independent checks:
#
# * a reference interpreter of ISO 32000-2 8.4 (graphics state), 9.3
#   (text state) and 9.4 (text objects), sharing no code with the
#   optimizer, that lists every glyph, path, image and XObject painted
#   with the state each one reads;
# * MuPDF, rendering the stream before and after.
#
# A Form XObject inherits its caller's state, so the interpreter also
# runs from a non-default initial state: a rewrite may assume nothing
# about the state a stream starts in.

from dataclasses import dataclass, field, replace
from decimal import Decimal

import numpy as np
import pikepdf
import pymupdf
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pdftl.utils.optimize_content import optimize_positioning_ops

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_INITIAL_COLOR = {
    "/DeviceGray": (0.0,),
    "/DeviceRGB": (0.0, 0.0, 0.0),
    "/DeviceCMYK": (0.0, 0.0, 0.0, 1.0),
}
_DEVICE_OPS = {"g": "/DeviceGray", "rg": "/DeviceRGB", "k": "/DeviceCMYK"}
_FILL_PAINT = {"f", "F", "f*", "B", "B*", "b", "b*"}
_STROKE_PAINT = {"S", "s", "B", "B*", "b", "b*"}
_PATH_PAINT = _FILL_PAINT | _STROKE_PAINT | {"n"}
_PATH_BUILD = {"m", "l", "c", "v", "y", "h", "re"}


def _mul(m, n):
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a * a2 + b * c2,
        a * b2 + b * d2,
        c * a2 + d * c2,
        c * b2 + d * d2,
        e * a2 + f * c2 + e2,
        e * b2 + f * d2 + f2,
    )


def _translate(tx, ty):
    return (1.0, 0.0, 0.0, 1.0, tx, ty)


def _r(values):
    return tuple(round(float(v), 3) + 0.0 for v in values)


@dataclass(frozen=True)
class GState:
    """The graphics state q saves and Q restores (text state included)."""

    ctm: tuple = IDENTITY
    clip: tuple = ()
    fill: tuple = ("/DeviceGray", (0.0,))
    stroke: tuple = ("/DeviceGray", (0.0,))
    line: tuple = (1.0, 0, 0, (), 10.0)  # w, J, j, d, M
    font: tuple | None = None  # (name, size)
    tc: float = 0.0
    tw: float = 0.0
    tz: float = 100.0
    tl: float = 0.0
    tr: int = 0
    ts: float = 0.0


# What a Form XObject might inherit: every parameter away from its default.
INHERITED = GState(
    ctm=(1.0, 0.0, 0.0, 1.0, 3.0, 4.0),
    fill=("/DeviceRGB", (0.2, 0.4, 0.6)),
    stroke=("/DeviceGray", (0.3,)),
    line=(3.0, 1, 1, ((2.0,), 0.0), 4.0),
    font=("/F2", 9.0),
    tc=1.0,
    tw=2.0,
    tz=70.0,
    tl=9.0,
    tr=2,
    ts=1.0,
)


def fake_width(byte):
    """A glyph width (thousandths of text space) per byte, standing in for font metrics."""
    return 400 + (byte % 5) * 100


@dataclass
class Interpreter:
    width: object = fake_width
    gs: GState = field(default_factory=GState)
    stack: list = field(default_factory=list)
    tm: tuple = IDENTITY
    tlm: tuple = IDENTITY
    path: list = field(default_factory=list)
    clip_rule: str | None = None
    text_clip: list = field(default_factory=list)
    marked: tuple = ()
    events: list = field(default_factory=list)

    # --- helpers ---

    def _set(self, **kw):
        self.gs = replace(self.gs, **kw)

    def _paint_context(self):
        return (self.gs.clip, self.marked)

    def _stroke_state(self):
        return (self.gs.stroke, self.gs.line, _r(self.gs.ctm))

    def _move_line(self, tx, ty):
        self.tlm = _mul(_translate(tx, ty), self.tlm)
        self.tm = self.tlm

    def _show(self, items):
        gs = self.gs
        th = gs.tz / 100.0
        size = gs.font[1] if gs.font else 0.0
        for item in items:
            if not isinstance(item, bytes):
                self.tm = _mul(_translate(-float(item) / 1000.0 * size * th, 0.0), self.tm)
                continue
            for byte in item:
                trm = _mul(_mul((size * th, 0.0, 0.0, size, 0.0, gs.ts), self.tm), gs.ctm)
                paint = []
                if gs.tr in (0, 2, 4, 6):
                    paint.append(("fill", gs.fill))
                if gs.tr in (1, 2, 5, 6):
                    paint.append(("stroke", self._stroke_state()))
                glyph = (byte, gs.font and gs.font[0], _r(trm))
                if paint:
                    self.events.append(("glyph", glyph, tuple(paint), self._paint_context()))
                if gs.tr >= 4:
                    self.text_clip.append(glyph)
                advance = self.width(byte) / 1000.0 * size + gs.tc + (gs.tw if byte == 32 else 0.0)
                self.tm = _mul(_translate(advance * th, 0.0), self.tm)

    # --- dispatch ---

    def run(self, instructions):
        for operands, op in instructions:
            self.step([_py(x) for x in operands], str(op))
        return self.events

    def step(self, ops, op):  # noqa: C901 - one branch per operator is the clearest form
        gs = self.gs
        if op == "q":
            self.stack.append(gs)
        elif op == "Q":
            if self.stack:
                self.gs = self.stack.pop()
        elif op == "cm":
            self._set(ctm=_mul(tuple(map(float, ops)), gs.ctm))
        elif op in _DEVICE_OPS:
            self._set(fill=(_DEVICE_OPS[op], _r(ops)))
        elif op.lower() in _DEVICE_OPS and op.isupper():
            self._set(stroke=(_DEVICE_OPS[op.lower()], _r(ops)))
        elif op in ("cs", "CS"):
            color = (ops[0], _INITIAL_COLOR.get(ops[0]))
            self._set(**{"fill" if op == "cs" else "stroke": color})
        elif op in ("sc", "scn", "SC", "SCN"):
            slot = "fill" if op.islower() else "stroke"
            space = getattr(gs, slot)[0]
            value = tuple(x if isinstance(x, str) else round(float(x), 3) for x in ops)
            self._set(**{slot: (space, value)})
        elif op in ("w", "J", "j", "d", "M"):
            line = list(gs.line)
            index = "wJjdM".index(op)
            line[index] = (
                (tuple(map(float, ops[0])), float(ops[1])) if op == "d" else float(ops[0])
            )
            self._set(line=tuple(line))
        elif op in _PATH_BUILD:
            self.path.append((op, _r(ops), _r(gs.ctm)))
        elif op in ("W", "W*"):
            self.clip_rule = op
        elif op in _PATH_PAINT:
            if op != "n":
                paint = []
                if op in _FILL_PAINT:
                    paint.append(("fill", gs.fill))
                if op in _STROKE_PAINT:
                    paint.append(("stroke", self._stroke_state()))
                self.events.append(
                    ("path", op, tuple(self.path), tuple(paint), self._paint_context())
                )
            if self.clip_rule:
                self._set(clip=gs.clip + ((self.clip_rule, tuple(self.path)),))
            self.path, self.clip_rule = [], None
        elif op == "Do":
            self.events.append(("Do", ops[0], gs, self.marked))
        elif op == "sh":
            self.events.append(("sh", ops[0], _r(gs.ctm), self._paint_context()))
        elif op == "INLINE IMAGE":
            self.events.append(("image", _r(gs.ctm), gs.fill, self._paint_context()))
        elif op in ("BMC", "BDC"):
            self.marked = self.marked + (ops[0],)
        elif op == "EMC":
            self.marked = self.marked[:-1]
        elif op == "BT":
            self.tm = self.tlm = IDENTITY
            self.text_clip = []
        elif op == "ET":
            if self.text_clip:
                self._set(clip=gs.clip + (("text", tuple(self.text_clip)),))
            self.text_clip = []
        else:
            self.text_step(ops, op)

    def text_step(self, ops, op):
        gs = self.gs
        if op == "Td":
            self._move_line(float(ops[0]), float(ops[1]))
        elif op == "TD":
            self._set(tl=-float(ops[1]))
            self._move_line(float(ops[0]), float(ops[1]))
        elif op == "Tm":
            self.tm = self.tlm = tuple(map(float, ops))
        elif op == "T*":
            self._move_line(0.0, -gs.tl)
        elif op == "Tf":
            self._set(font=(ops[0], float(ops[1])))
        elif op in ("Tc", "Tw", "Tz", "TL", "Ts"):
            self._set(**{op.lower(): float(ops[0])})
        elif op == "Tr":
            self._set(tr=int(ops[0]))
        elif op == "Tj":
            self._show([ops[0]])
        elif op == "TJ":
            self._show(ops[0])
        elif op == "'":
            self._move_line(0.0, -gs.tl)
            self._show([ops[0]])
        elif op == '"':
            self._set(tw=float(ops[0]), tc=float(ops[1]))
            self._move_line(0.0, -self.gs.tl)
            self._show([ops[2]])


def _py(x):
    """pikepdf operand -> plain Python (names as '/X', strings as bytes)."""
    if isinstance(x, pikepdf.Name):
        return str(x)
    if isinstance(x, pikepdf.String):
        return bytes(x)
    if isinstance(x, pikepdf.Array):
        return [_py(v) for v in x]
    if isinstance(x, pikepdf.Dictionary):
        return "dict"
    if isinstance(x, (int, float, Decimal)):
        return x
    return x


def painted(instructions, initial=GState(), width=fake_width):
    return Interpreter(width=width, gs=initial).run(instructions)


# Parsed operands belong to a Pdf, which must outlive them.
_SCRATCH = pikepdf.new()


def _parse(content: bytes):
    return list(pikepdf.parse_content_stream(_SCRATCH.make_stream(content)))


def _optimized(content: bytes, aggressive: bool):
    """Optimize, then serialize and re-parse, as excise does."""
    out = optimize_positioning_ops(_parse(content), aggressive_tf=aggressive)
    return _parse(pikepdf.unparse_content_stream(out))


def assert_same_painting(content: bytes):
    original = _parse(content)
    for aggressive in (False, True):
        optimized = _optimized(content, aggressive)
        for initial in (GState(), INHERITED):
            assert painted(optimized, initial) == painted(original, initial), (
                f"aggressive_tf={aggressive}, initial={'inherited' if initial is INHERITED else 'default'}"
                f"\n in: {content!r}\nout: {pikepdf.unparse_content_stream(optimized)!r}"
            )


# --- The interpreter itself, against MuPDF ---


_HELV = pymupdf.Font("helv")
_TIMES = pymupdf.Font("tiro")
_FONTS = {"/F1": ("Helvetica", _HELV), "/F2": ("Times-Roman", _TIMES)}


def _real_width(font_name):
    return lambda byte: _FONTS[font_name][1].glyph_advance(byte) * 1000.0


def _page_pdf(content: bytes, form_content: bytes = b"BT T* (F) Tj ET 0 0 6 6 re f") -> bytes:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(300, 300))
    page = pdf.pages[0]
    fonts = {
        name: pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name("/" + base),
                Encoding=pikepdf.Name.WinAnsiEncoding,
            )
        )
        for name, (base, _) in _FONTS.items()
    }
    form = pdf.make_stream(form_content)
    form.Type, form.Subtype = pikepdf.Name.XObject, pikepdf.Name.Form
    form.BBox = [-50, -50, 300, 300]
    form.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(fonts))
    shading = pikepdf.Dictionary(
        ShadingType=2,
        ColorSpace=pikepdf.Name.DeviceRGB,
        Coords=[0, 0, 40, 0],
        Function=pikepdf.Dictionary(
            FunctionType=2, Domain=[0, 1], C0=[1, 0, 0], C1=[0, 0, 1], N=1
        ),
    )
    page.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(fonts),
        XObject=pikepdf.Dictionary(X0=form),
        Shading=pikepdf.Dictionary(Sh0=shading),
    )
    page.Contents = pdf.make_stream(content)
    import io

    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _render(pdf_bytes: bytes) -> np.ndarray:
    pix = pymupdf.open(stream=pdf_bytes, filetype="pdf")[0].get_pixmap(dpi=72, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n).astype(int)


def _mupdf_glyph_origins(pdf_bytes: bytes):
    page = pymupdf.open(stream=pdf_bytes, filetype="pdf")[0]
    out = []
    for block in page.get_text(
        "rawdict", flags=pymupdf.TEXTFLAGS_RAWDICT | pymupdf.TEXT_INHIBIT_SPACES
    )["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for ch in span["chars"]:
                    out.append((ch["c"], ch["origin"][0], 300 - ch["origin"][1]))
    return sorted(out)


@pytest.mark.parametrize(
    "content",
    [
        b"BT /F1 20 Tf 1 0 0 1 20 250 Tm (Ab c) Tj 0 -30 TD (Wd) Tj T* (x) Tj ET",
        b"BT /F2 18 Tf 30 TL 2 Tc 5 Tw 40 200 Td (a b) Tj (c) ' 3 1 (d e) \" ET",
        b"BT /F1 16 Tf 50 Tz 20 150 Td [(A) -500 (B) 250 (C)] TJ 4 Ts (D) Tj ET",
        b"q 2 0 0 2 10 10 cm BT /F2 10 Tf 5 60 Td (Mm) Tj ET Q BT /F1 10 Tf 5 30 Td (Q) Tj ET",
        b"BT /F1 12 Tf 70 Tz 1 0 0 1 10 100 Tm (a) Tj 5 0 Td (b) Tj -12 TL T* (c) Tj ET",
    ],
)
def test_interpreter_places_glyphs_where_mupdf_does(content):
    """Validates the reference interpreter's text maths against an independent renderer."""
    pdf_bytes = _page_pdf(content)
    expected = _mupdf_glyph_origins(pdf_bytes)
    predicted = []
    interp = Interpreter()
    for operands, op in _parse(content):
        if str(op) == "Tf":
            interp.width = _real_width(str(operands[0]))
        interp.step([_py(x) for x in operands], str(op))
    for event in interp.events:
        byte, _, trm = event[1]
        predicted.append((bytes([byte]).decode("cp1252"), trm[4], trm[5]))
    assert [c for c, _, _ in sorted(predicted)] == [c for c, _, _ in expected]
    for (c, x, y), (_, ex, ey) in zip(sorted(predicted), expected):
        assert abs(x - ex) <= 0.15 and abs(y - ey) <= 0.15, (c, (x, y), (ex, ey))


# --- Bugs found by rendering (each changed what the page shows) ---

_REGRESSIONS = {
    "TD whose show was removed loses its leading at ET": (
        b"BT /F1 20 Tf 0 -40 TD ET BT /F1 20 Tf 50 150 Td (a) Tj T* (b) Tj ET"
    ),
    "TD then Tm loses its leading": (
        b"BT /F1 20 Tf 0 -40 TD 1 0 0 1 50 150 Tm (a) Tj T* (b) Tj ET"
    ),
    "T* moved ahead of a buffered Tm": b"BT /F1 20 Tf 20 TL 1 0 0 1 50 150 Tm T* (a) Tj ET",
    "T* moved ahead of Tm and Td": (b"BT /F1 20 Tf 20 TL 1 0 0 1 50 150 Tm 10 0 Td T* (a) Tj ET"),
    "T* moved ahead of the TD that sets its leading": (
        b"BT /F1 20 Tf 14 TL 1 0 0 1 20 170 Tm 0 -40 TD T* (a) Tj ET"
    ),
    "Tz restored by Q is not redundant": (
        b"q BT /F1 20 Tf 50 Tz 20 100 Td (WWW) Tj ET Q BT /F1 20 Tf 50 Tz 20 150 Td (WWW) Tj ET"
    ),
    "a Form's leading 100 Tz overrides the caller's": b"BT /F1 20 Tf 100 Tz 20 150 Td (WWW) Tj ET",
    "TL written after a buffered TD": (
        b"BT /F1 20 Tf 1 0 0 1 20 170 Tm 0 -40 TD 10 TL (a) Tj T* (b) Tj ET"
    ),
    "Do inherits the leading of a buffered TD": (
        b"BT /F1 20 Tf 0 -40 TD ET /X0 Do BT /F1 20 Tf 50 150 Td (a) Tj ET"
    ),
}


@pytest.mark.parametrize("content", list(_REGRESSIONS.values()), ids=list(_REGRESSIONS))
def test_regression_same_painting(content):
    assert_same_painting(content)


@pytest.mark.parametrize("content", list(_REGRESSIONS.values()), ids=list(_REGRESSIONS))
def test_regression_renders_the_same(content):
    _assert_renders_same(content)


def _assert_renders_same(content: bytes, form_content: bytes | None = None):
    kw = {"form_content": form_content} if form_content is not None else {}
    before = _render(_page_pdf(content, **kw))
    for aggressive in (False, True):
        out = optimize_positioning_ops(_parse(content), aggressive_tf=aggressive)
        after = _render(_page_pdf(pikepdf.unparse_content_stream(out), **kw))
        diff = int((np.abs(before - after).max(axis=2) > 48).sum())
        assert diff == 0, f"{diff} pixels differ (aggressive_tf={aggressive})\n in: {content!r}"


def test_form_leading_tz_renders_the_same_under_a_caller_tz():
    # The page sets 50 Tz and draws a form whose own first op is 100 Tz.
    form = b"BT /F1 20 Tf 100 Tz 20 150 Td (WWW) Tj ET"
    before = _render(_page_pdf(b"50 Tz /X0 Do", form_content=form))
    form_out = pikepdf.unparse_content_stream(
        optimize_positioning_ops(_parse(form), aggressive_tf=True)
    )
    after = _render(_page_pdf(b"50 Tz /X0 Do", form_content=form_out))
    assert int((np.abs(before - after).max(axis=2) > 48).sum()) == 0


# --- Random streams ---

_num = st.sampled_from
_color = st.one_of(
    st.builds(lambda v: b"%s g" % v, _num([b"0", b"0.5"])),
    st.builds(lambda v: b"%s G" % v, _num([b"0", b"0.5"])),
    st.builds(lambda v: b"%s rg" % v, _num([b"1 0 0", b"0 0.6 0", b"0 0 0"])),
    st.builds(lambda v: b"%s RG" % v, _num([b"0 0 1", b"0 0 0"])),
    st.builds(lambda v: b"%s k" % v, _num([b"0 1 0 0", b"0 0 0 1"])),
    st.just(b"/DeviceRGB cs 0.2 0.2 0.8 sc"),
    st.just(b"/DeviceGray CS"),
    st.just(b"0.4 SC"),
)
_line = st.one_of(
    st.builds(lambda v: b"%s w" % v, _num([b"1", b"4"])),
    st.builds(lambda v: b"%s J" % v, _num([b"0", b"1"])),
    st.builds(lambda v: b"%s j" % v, _num([b"0", b"2"])),
    st.builds(lambda v: b"%s d" % v, _num([b"[] 0", b"[3] 0"])),
    st.builds(lambda v: b"%s M" % v, _num([b"10", b"2"])),
)
_text_state = st.one_of(
    st.builds(lambda v: b"%s Tc" % v, _num([b"0", b"1.5"])),
    st.builds(lambda v: b"%s Tw" % v, _num([b"0", b"4"])),
    st.builds(lambda v: b"%s Tz" % v, _num([b"50", b"100", b"130"])),
    st.builds(lambda v: b"%s TL" % v, _num([b"0", b"12", b"-8", b"20.5"])),
    st.builds(lambda v: b"%s Tf" % v, _num([b"/F1 12", b"/F2 12", b"/F1 16"])),
    st.builds(lambda v: b"%s Tr" % v, _num([b"0", b"0", b"0", b"1", b"2", b"3", b"7"])),
    st.builds(lambda v: b"%s Ts" % v, _num([b"0", b"3"])),
)
_position = st.one_of(
    st.builds(
        lambda x, y: b"%s %s Td" % (x, y), _num([b"0", b"6", b"-4.5"]), _num([b"0", b"-12", b"7"])
    ),
    st.builds(lambda x, y: b"%s %s TD" % (x, y), _num([b"0", b"5"]), _num([b"-14", b"9", b"0"])),
    st.builds(
        lambda m: b"%s Tm" % m,
        _num([b"1 0 0 1 20 200", b"1 0 0 1 60 120", b"0.8 0.2 -0.2 0.8 40 160"]),
    ),
    st.just(b"T*"),
)
_show = st.one_of(
    st.just(b"(Ab) Tj"),
    st.just(b"(a b) Tj"),
    st.just(b"[(W) -300 (i) 120 ( x)] TJ"),
    st.just(b"(Q) '"),
    st.just(b'3 1 (m n) "'),
)
_paint = st.sampled_from(
    [
        b"10 10 30 20 re f",
        b"5 5 m 60 40 l S",
        b"20 20 25 25 re B",
        b"0 0 80 80 re W n",
        b"/X0 Do",
        b"/X0 Do",
        b"/Sh0 sh",
        b"n",
        b"q 20 0 0 20 30 30 cm BI /W 2 /H 2 /BPC 8 /CS /G ID \x00\x80\xff\x40 EI Q",
    ]
)
_cm = st.sampled_from([b"1 0 0 1 8 -6 cm", b"1.5 0 0 1.5 0 0 cm"])


_TEXT_KINDS = ["position", "position", "show", "show", "text_state", "color", "marked"]
_KINDS = ["text", "text", "text", "q", "marked", "color", "line", "text_state", "cm", "paint"]
_OPS = {
    "position": _position,
    "show": _show,
    "text_state": _text_state,
    "color": _color,
    "line": _line,
    "cm": _cm,
    "paint": _paint,
}


def _text_ops(draw, depth):
    out = []
    for _ in range(draw(st.integers(1, 10))):
        kind = draw(st.sampled_from(_TEXT_KINDS if depth < 2 else _TEXT_KINDS[:-1]))
        if kind == "marked":
            out += [draw(st.sampled_from([b"/Span BMC", b"/P <</MCID 3>> BDC"]))]
            out += _text_ops(draw, depth + 1) + [b"EMC"]
        else:
            out.append(draw(_OPS[kind]))
    return out


def _page_ops(draw, depth):
    out = []
    for _ in range(draw(st.integers(1, 6))):
        kind = draw(st.sampled_from(_KINDS if depth < 3 else _KINDS[2:]))
        if kind == "text":
            out += [b"BT", *_text_ops(draw, 0), b"ET"]
        elif kind == "q":
            out += [b"q", *_page_ops(draw, depth + 1), b"Q"]
        elif kind == "marked":
            out += [draw(st.sampled_from([b"/Span BMC", b"/P <</MCID 3>> BDC"]))]
            out += _page_ops(draw, depth + 1) + [b"EMC"]
        else:
            out.append(draw(_OPS[kind]))
    return out


@st.composite
def _streams(draw):
    # The prefix makes text visible (a font, dark ink, on the page) for the renderer.
    return b"1 0 0 1 60 60 cm /F1 12 Tf 0 g " + b" ".join(_page_ops(draw, 0))


_stream = _streams()


@settings(max_examples=600, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_stream)
def test_random_streams_paint_the_same(content):
    assert_same_painting(content)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_stream)
def test_random_streams_render_the_same(content):
    _assert_renders_same(content)


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_stream)
def test_random_form_streams_render_the_same_under_an_inherited_state(content):
    # Page state away from every default, then the stream under test as a Form.
    caller = b"0.2 0.4 0.6 rg 3 w 1 Tc 2 Tw 70 Tz 9 TL /F2 9 Tf 2 Tr 1 Ts /X0 Do"
    before = _render(_page_pdf(caller, form_content=content))
    for aggressive in (False, True):
        out = pikepdf.unparse_content_stream(
            optimize_positioning_ops(_parse(content), aggressive_tf=aggressive)
        )
        after = _render(_page_pdf(caller, form_content=out))
        assert int((np.abs(before - after).max(axis=2) > 48).sum()) == 0, content
