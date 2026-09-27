# tests/utils/test_round_text.py
#
# round_text_positions may move a glyph by at most `tolerance` in the
# space the stream is drawn into, and may change nothing but positioning
# numbers. The reference interpreter below places glyphs per ISO 32000-2
# 9.4.2-9.4.4 and shares no code with the module under test.

import math
from decimal import Decimal

import pikepdf
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pdftl.utils.round_text import RoundTextStats, round_text_positions

VERTICAL_FONTS = {"/V1"}
_SCRATCH = pikepdf.new()  # parsed operands must not outlive their Pdf


def _parse(content: bytes):
    return list(pikepdf.parse_content_stream(_SCRATCH.make_stream(content)))


def _is_vertical(name):
    return name in VERTICAL_FONTS or (False if name.startswith("/F") else None)


def _run(content: bytes, tolerance=0.005, uses=((1, 0, 0, 1),), th=1.0, stats=None):
    out = round_text_positions(
        _parse(content),
        tolerance=tolerance,
        is_vertical=_is_vertical,
        uses=uses,
        horizontal_scale=th,
        stats=stats,
    )
    return pikepdf.unparse_content_stream(out)


# --- reference interpreter ---


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


def _width(byte):
    return 300 + (byte * 37) % 500  # thousandths of text space, standing in for metrics


def glyph_origins(instructions, th=1.0):
    """[(byte, font, (x, y))] for every glyph shown, in the stream's own space.

    Unknown initial horizontal scaling (th=None) is modelled as 0.83, an
    arbitrary value, so a rewrite that assumed 1.0 would be caught.
    """
    ctm, stack = (1, 0, 0, 1, 0, 0), []
    ts = {"size": 0.0, "th": 0.83 if th is None else th, "tc": 0.0, "tw": 0.0, "tl": 0.0}
    ts.update(rise=0.0, font=None)
    tm = tlm = (1, 0, 0, 1, 0, 0)
    out = []

    def move_line(tx, ty):
        nonlocal tm, tlm
        tlm = _mul((1, 0, 0, 1, tx, ty), tlm)
        tm = tlm

    def advance(tx, ty):
        nonlocal tm
        tm = _mul((1, 0, 0, 1, tx, ty), tm)

    def show(items):
        vertical = ts["font"] in VERTICAL_FONTS
        size, h = ts["size"], ts["th"]
        for item in items:
            if not isinstance(item, bytes):
                k = -float(item) / 1000 * size
                advance(0, k) if vertical else advance(k * h, 0)
                continue
            for byte in item:
                trm = _mul(_mul((size * h, 0, 0, size, 0, ts["rise"]), tm), ctm)
                out.append((byte, ts["font"], (trm[4], trm[5])))
                space = ts["tw"] if byte == 32 else 0.0
                if vertical:
                    advance(0, -1000 / 1000 * size + ts["tc"] + space)
                else:
                    advance((_width(byte) / 1000 * size + ts["tc"] + space) * h, 0)

    for operands, op in instructions:
        op, v = str(op), [_py(x) for x in operands]
        if op == "q":
            stack.append((ctm, dict(ts)))
        elif op == "Q":
            ctm, ts = stack.pop()
        elif op == "cm":
            ctm = _mul(tuple(map(float, v)), ctm)
        elif op == "BT":
            tm = tlm = (1, 0, 0, 1, 0, 0)
        elif op == "Tm":
            tm = tlm = tuple(map(float, v))
        elif op == "Td":
            move_line(float(v[0]), float(v[1]))
        elif op == "TD":
            ts["tl"] = -float(v[1])
            move_line(float(v[0]), float(v[1]))
        elif op == "T*":
            move_line(0, -ts["tl"])
        elif op == "Tf":
            ts["font"], ts["size"] = v[0], float(v[1])
        elif op in ("Tc", "Tw", "TL", "Ts"):
            ts[{"Tc": "tc", "Tw": "tw", "TL": "tl", "Ts": "rise"}[op]] = float(v[0])
        elif op == "Tz":
            ts["th"] = float(v[0]) / 100
        elif op == "Tj":
            show([v[0]])
        elif op == "TJ":
            show(v[0])
        elif op == "'":
            move_line(0, -ts["tl"])
            show([v[0]])
        elif op == '"':
            ts["tw"], ts["tc"] = float(v[0]), float(v[1])
            move_line(0, -ts["tl"])
            show([v[2]])
    return out


def _py(x):
    if isinstance(x, pikepdf.Name):
        return str(x)
    if isinstance(x, pikepdf.String):
        return bytes(x)
    if isinstance(x, pikepdf.Array):
        return [_py(i) for i in x]
    return x


def _non_positioning(instructions):
    """Everything except the numbers this pass may change, as comparable text."""
    out = []
    for operands, op in instructions:
        op = str(op)
        if op in ("Td", "TJ", "Tj"):
            out.append("show" if op != "Td" else "Td")
        else:
            out.append((op, repr([_py(x) for x in operands])))
    return out


def assert_within(content, out_bytes, tolerance, uses=((1, 0, 0, 1),), th=1.0):
    before = glyph_origins(_parse(content), th)
    after = glyph_origins(_parse(out_bytes), th)
    assert [g[:2] for g in after] == [g[:2] for g in before]
    for (_, _, (x0, y0)), (_, _, (x1, y1)) in zip(before, after):
        dx, dy = x1 - x0, y1 - y0
        for a, b, c, d in uses:
            moved = math.hypot(dx * a + dy * c, dx * b + dy * d)
            assert moved <= tolerance * (1 + 1e-6) + 1e-9, (moved, tolerance)
    assert _non_positioning(_parse(out_bytes)) == _non_positioning(_parse(content))


# --- worked examples ---


def test_small_kerns_carry_into_the_next_decision():
    # Size 10: a kern unit is 0.01 pt, so 0.005 pt is half a unit.
    content = b"BT /F1 10 Tf [(x) 0.3 (y) 0.35 (z) -10000 (a)] TJ ET"
    out = _run(content)
    assert out == b"BT\n/F1 10 Tf\n[ (xy) 1 (z) -10000 (a) ] TJ\nET"
    assert_within(content, out, 0.005)


def test_cancelling_kerns_are_both_dropped():
    content = b"BT /F1 10 Tf [(x) 0.3 (y) -0.35 (z) -10000 (a)] TJ ET"
    assert _run(content) == b"BT\n/F1 10 Tf\n[ (xyz) -10000 (a) ] TJ\nET"


def test_all_kerns_dropped_becomes_tj():
    content = b"BT /F1 10 Tf [(ab) 0.2 (c)] TJ ET"
    assert _run(content) == b"BT\n/F1 10 Tf\n(abc) Tj\nET"


def test_five_decimal_kerns_shorten():
    content = b"BT /F1 9.96264 Tf [(t) 0.78418 (h) 3.84003 (r) 9.57557] TJ ET"
    out = _run(content)
    # Carried error after "1" and "4" is +0.376 units; "10" would reach 0.80, past 0.50.
    assert out == b"BT\n/F1 9.96264 Tf\n[ (t) 1 (h) 4 (r) 9 ] TJ\nET"
    assert_within(content, out, 0.005)


def test_td_rounding_error_carries_to_the_next_line():
    content = b"BT /F1 10 Tf 1 0 0 1 0 0 Tm 56.5199 0 Td (a) Tj 38.8801 0 Td (b) Tj ET"
    out = _run(content)
    assert out == b"BT\n/F1 10 Tf\n1 0 0 1 0 0 Tm\n56.52 0 Td\n(a) Tj\n38.88 0 Td\n(b) Tj\nET"
    assert_within(content, out, 0.005)


def test_td_error_pays_back_on_the_next_move():
    # 0.3 + 0.3 would drift 0.6 past a 0.5 bound; the second move compensates.
    content = b"BT /F1 10 Tf 10.3 0 Td (a) Tj 10.3 0 Td (b) Tj ET"
    out = _run(content, tolerance=0.5)
    assert out == b"BT\n/F1 10 Tf\n10 0 Td\n(a) Tj\n11 0 Td\n(b) Tj\nET"
    assert_within(content, out, 0.5)


def test_td_operands_that_set_the_leading_are_never_rounded():
    content = b"BT /F1 10 Tf 0 -12.00001 TD (a) Tj T* (b) Tj ET"
    assert _run(content) == pikepdf.unparse_content_stream(_parse(content))


@pytest.mark.parametrize(
    "content",
    [
        b"BT /F1 10 Tf 10 0 0 10 0 0 cm [(a) 0.3 (b)] TJ ET",  # cm inside BT
        b"BT /F1 10 Tf q [(a) 0.3 (b)] TJ Q ET",
        b"BT /F1 10 Tf /GS1 gs [(a) 0.3 (b)] TJ ET",
        b"BT /F1 10 Tf /X0 Do [(a) 0.3 (b)] TJ ET",
        b"BT /F1 10 Tf [(a) 0.3 (b)] TJ",  # unterminated
        b"BT /F1 10 Tf BT [(a) 0.3 (b)] TJ ET",
    ],
)
def test_text_object_with_unmodelled_operators_is_left_alone(content):
    stats = RoundTextStats()
    out = _run(content + b" BT /F1 10 Tf [(c) 0.3 (d)] TJ ET", stats=stats)
    assert out == pikepdf.unparse_content_stream(
        _parse(content + b" BT /F1 10 Tf [(c) 0.3 (d)] TJ ET")
    )
    assert stats.text_objects_skipped == 1


def test_unterminated_last_text_object_is_left_alone():
    stats = RoundTextStats()
    content = b"BT /F1 10 Tf [(a) 0.3 (b)] TJ"
    assert _run(content, stats=stats) == pikepdf.unparse_content_stream(_parse(content))
    assert stats.text_objects_skipped == 1


@pytest.mark.parametrize(
    "content",
    [
        b"BT /F1 10 Tf 1 0 0 1 /x 0 Tm [(a) 0.3 (b)] TJ 10.3 0 Td (c) Tj ET",
        b"BT /F1 10 Tf true 0 Td [(a) 0.3 (b)] TJ 10.3 0 Td (c) Tj ET",
        b"BT /F1 10 Tf (abc) TJ ET",  # not an array
        b"BT /F1 10 Tf [(a) /x (b)] TJ ET",
    ],
)
def test_unreadable_operands_stop_rounding(content):
    assert _run(content) == pikepdf.unparse_content_stream(_parse(content))


def test_unchanged_numbers_are_not_counted():
    # Large integers are distinct objects on every pikepdf index access.
    stats = RoundTextStats()
    _run(b"BT /F1 10 Tf [(a) -3330 (b)] TJ 720 7000 Td (c) Tj ET", stats=stats)
    assert stats.numbers_shortened == 0


@pytest.mark.parametrize(
    "prefix",
    [
        b"",  # no font size yet
        b"/Unknown 10 Tf",  # font not in resources: writing mode unknown
        b"/F1 10 Tf /GS1 gs",  # an ExtGState may set the font
        b"/F1 10 Tf 1 0 0 1 x 0 cm",  # a matrix we cannot read
        b"/F1 10 Tf Q",  # unmatched Q: state unknown
    ],
)
def test_nothing_is_rounded_while_the_scale_is_unknown(prefix):
    content = prefix + b" BT [(a) 0.3 (b)] TJ 10.3 0 Td (c) Tj ET"
    assert _run(content) == pikepdf.unparse_content_stream(_parse(content))


def test_unknown_horizontal_scaling_blocks_kerns_but_not_td():
    content = b"BT /F1 10 Tf [(a) 0.3 (b)] TJ 10.3 0 Td (c) Tj ET"
    out = _run(content, tolerance=0.5, th=None)
    assert out == b"BT\n/F1 10 Tf\n[ (a) 0.3 (b) ] TJ\n10 0 Td\n(c) Tj\nET"


def test_tz_sets_the_kern_scale():
    # At 50 Tz a kern unit moves half as far, so 0.9 units fit within 0.005 pt.
    content = b"BT /F1 10 Tf 50 Tz [(a) 0.9 (b)] TJ ET"
    assert _run(content) == b"BT\n/F1 10 Tf\n50 Tz\n(ab) Tj\nET"
    assert _run(b"BT /F1 10 Tf [(a) 0.9 (b)] TJ ET") == b"BT\n/F1 10 Tf\n[ (a) 1 (b) ] TJ\nET"


def test_vertical_font_kerns_ignore_horizontal_scaling():
    content = b"BT /V1 10 Tf 50 Tz [(a) 0.9 (b)] TJ ET"
    assert _run(content) == b"BT\n/V1 10 Tf\n50 Tz\n[ (a) 1 (b) ] TJ\nET"


def test_bound_holds_for_every_use_of_a_form():
    # Drawn once at 1x and once at 10x: the 10x use decides.
    content = b"BT /F1 10 Tf [(a) 0.3 (b)] TJ ET"
    assert _run(content, uses=[(1, 0, 0, 1)]) == b"BT\n/F1 10 Tf\n(ab) Tj\nET"
    out = _run(content, uses=[(1, 0, 0, 1), (10, 0, 0, 10)])
    assert out == pikepdf.unparse_content_stream(_parse(content))


def test_scaled_text_matrix_scales_the_bound():
    content = b"BT /F1 1 Tf 10 0 0 10 0 0 Tm [(a) 0.3 (b)] TJ ET"
    assert _run(content) == b"BT\n/F1 1 Tf\n10 0 0 10 0 0 Tm\n(ab) Tj\nET"


def test_stats_count_kerns_and_shortened_numbers():
    stats = RoundTextStats()
    _run(b"BT /F1 10 Tf [(x) 0.3 (y) 0.35 (z) -10000 (a)] TJ 1.23456 0 Td ET", stats=stats)
    assert (stats.text_objects, stats.kerns_before, stats.kerns_after) == (1, 3, 2)
    assert stats.numbers_shortened == 3  # 0.3 dropped, 0.35 -> 1, the Td


def test_integer_kerns_are_left_as_they_are():
    content = b"BT /F1 10 Tf [(a) -333 (b) 250 (c)] TJ 72 700 Td (d) Tj ET"
    assert _run(content) == pikepdf.unparse_content_stream(_parse(content))


# --- random streams ---

_kern = st.one_of(
    st.integers(-900000, 900000).map(lambda i: Decimal(i).scaleb(-5)),
    st.sampled_from([Decimal(-333), Decimal(250), Decimal(-10000), Decimal("0.00001")]),
)
_coord = st.integers(-9000000, 9000000).map(lambda i: Decimal(i).scaleb(-4))
_string = st.sampled_from([b"(a)", b"(Wi)", b"( x )", b"()"])


def _tj(draw):
    parts = [draw(_string)]
    for _ in range(draw(st.integers(0, 8))):
        parts += [str(draw(_kern)).encode(), draw(_string)]
    return b"[" + b" ".join(parts) + b"] TJ"


_MATRICES = [
    b"1 0 0 1 72 700",
    b"10 0 0 10 50 50",
    b"0.7 0.7 -0.7 0.7 30 40",
    b"1 0 0 -1 72 709.2",
]


@st.composite
def _text_object(draw):
    ops = [b"BT"]
    for _ in range(draw(st.integers(1, 12))):
        kind = draw(
            st.sampled_from(["tj", "tj", "tj", "td", "td", "TD", "tm", "T*", "state", "quote"])
        )
        if kind == "tj":
            ops.append(_tj(draw))
        elif kind == "td":
            ops.append(b"%s %s Td" % (str(draw(_coord)).encode(), str(draw(_coord)).encode()))
        elif kind == "TD":
            ops.append(b"%s %s TD" % (str(draw(_coord)).encode(), str(draw(_coord)).encode()))
        elif kind == "tm":
            ops.append(draw(st.sampled_from(_MATRICES)) + b" Tm")
        elif kind == "T*":
            ops.append(b"T*")
        elif kind == "quote":
            ops.append(draw(st.sampled_from([b"(q) '", b'1 2 (r s) "'])))
        else:
            ops.append(draw(_text_state))
    return ops + [b"ET"]


_text_state = st.sampled_from(
    [
        b"/F1 9.96264 Tf",
        b"/F1 0.24 Tf",
        b"/F2 24 Tf",
        b"/V1 12 Tf",
        b"50 Tz",
        b"130 Tz",
        b"1.5 Tc",
        b"3 Tw",
        b"-11.955 TL",
        b"2 Ts",
        b"0.5 g",
    ]
)
_cm = st.sampled_from(
    [b"0.1 0 0 0.1 0 0 cm", b"10 0 0 10 0 0 cm", b"0 1 -1 0 300 0 cm", b"3 0 0 0.5 0 0 cm"]
)


@st.composite
def _stream(draw):
    ops = [b"/F1 10 Tf"]
    depth = 0
    for _ in range(draw(st.integers(1, 6))):
        kind = draw(st.sampled_from(["text", "text", "cm", "q", "Q", "state"]))
        if kind == "text":
            ops += draw(_text_object())
        elif kind == "cm":
            ops.append(draw(_cm))
        elif kind == "q":
            ops.append(b"q")
            depth += 1
        elif kind == "Q" and depth:
            ops.append(b"Q")
            depth -= 1
        elif kind == "state":
            ops.append(draw(_text_state))
    return b" ".join(ops + [b"Q"] * depth)


_uses = st.lists(
    st.sampled_from(
        [(1, 0, 0, 1), (2.5, 0, 0, 2.5), (0, 3, -3, 0), (0.2, 0, 0, 7), (1, 0.5, 0, 1)]
    ),
    min_size=1,
    max_size=3,
)


@settings(max_examples=800, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_stream(), st.sampled_from([0.001, 0.005, 0.02, 0.3]), _uses, st.sampled_from([1.0, None]))
def test_random_streams_move_no_glyph_beyond_the_tolerance(content, tolerance, uses, th):
    out = _run(content, tolerance=tolerance, uses=uses, th=th)
    assert_within(content, out, tolerance, uses, th)
