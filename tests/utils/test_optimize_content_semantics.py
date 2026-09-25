# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/utils/test_optimize_content_semantics.py
#
# optimize_positioning_ops must not change the graphics state any paint
# operator sees. The oracle below is a small reference interpreter of the
# relevant state (ISO 32000-2 8.4, 8.6.8, 9.3) that shares no code with the
# optimizer.

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pdftl.utils.optimize_content import optimize_positioning_ops

_SPACE_OF = {"g": "DeviceGray", "rg": "DeviceRGB", "k": "DeviceCMYK"}
_LINE_OPS = {"w", "J", "j", "d", "M"}
_STROKE_STATE = ("stroke", *_LINE_OPS)
# State each painting operator reads. A Form XObject inherits all of it.
_READS = {
    **{op: ("fill",) for op in ("f", "F", "f*")},
    **{op: _STROKE_STATE for op in ("S", "s")},
    **{op: ("fill", *_STROKE_STATE) for op in ("B", "B*", "b", "b*")},
    **{op: ("font", "fill", *_STROKE_STATE) for op in ("Tj", "TJ", "'", '"')},
    "Do": ("font", "fill", *_STROKE_STATE),
    "INLINE IMAGE": ("fill",),  # an image mask paints in the fill color
}


def _num(values):
    return tuple(round(float(v), 3) for v in values)


def painted_states(instructions):
    """(paint op, state) for every paint operator, per the spec's state model."""
    initial = {
        "fill": ("DeviceGray", (0.0,)),
        "stroke": ("DeviceGray", (0.0,)),
        "font": None,
        **{op: None for op in _LINE_OPS},
    }
    state, stack, seen = dict(initial), [], []
    for operands, op in instructions:
        op = str(op)
        if op == "q":
            stack.append(dict(state))
        elif op == "Q":
            if stack:
                state = stack.pop()
        elif op in _SPACE_OF:
            state["fill"] = (_SPACE_OF[op], _num(operands))
        elif op.lower() in _SPACE_OF and op.isupper():
            state["stroke"] = (_SPACE_OF[op.lower()], _num(operands))
        elif op == "cs":
            state["fill"] = (str(operands[0]), "initial")
        elif op == "CS":
            state["stroke"] = (str(operands[0]), "initial")
        elif op in ("sc", "scn"):
            state["fill"] = (state["fill"][0], tuple(map(str, operands)))
        elif op in ("SC", "SCN"):
            state["stroke"] = (state["stroke"][0], tuple(map(str, operands)))
        elif op in _LINE_OPS:
            state[op] = tuple(map(str, operands))
        elif op == "Tf":
            state["font"] = tuple(map(str, operands))
        elif op in _READS:
            seen.append((op, tuple((k, state[k]) for k in _READS[op])))
    return seen


def _i(op, *operands):
    return (list(operands), op)


def _assert_preserved(instructions):
    expected = painted_states(instructions)
    for aggressive in (False, True):
        out = optimize_positioning_ops(list(instructions), aggressive_tf=aggressive)
        assert painted_states(out) == expected


# --- Regression shapes (all corrupted rendering before the fix) ---


def test_fill_color_used_inside_q_block_is_kept():
    # Beamer-style: set white, paint inside q...Q, then change color.
    _assert_preserved(
        [_i("g", 1), _i("q"), _i("re", 0, 0, 10, 10), _i("f"), _i("Q"), _i("g", 0), _i("f")]
    )


def test_line_width_used_inside_q_block_is_kept():
    _assert_preserved([_i("w", 5), _i("q"), _i("S"), _i("Q"), _i("w", 1), _i("S")])


def test_font_used_inside_q_block_is_kept():
    _assert_preserved(
        [
            _i("Tf", "/F1", 12),
            _i("q"),
            _i("BT"),
            _i("Tj", "a"),
            _i("ET"),
            _i("Q"),
            _i("Tf", "/F2", 10),
            _i("BT"),
            _i("Tj", "b"),
            _i("ET"),
        ]
    )


def test_gray_after_rgb_is_not_redundant():
    # 1 g, then black via rg, then 1 g again must stay: it changes the fill.
    _assert_preserved([_i("g", 1), _i("f"), _i("rg", 0, 0, 0), _i("f"), _i("g", 1), _i("f")])


def test_gray_after_colorspace_change_is_not_redundant():
    _assert_preserved(
        [_i("g", 1), _i("f"), _i("cs", "/Pattern"), _i("scn", "/P0"), _i("f"), _i("g", 1), _i("f")]
    )


def test_stroke_slot_independent_of_fill_slot():
    _assert_preserved([_i("G", 1), _i("S"), _i("rg", 0, 0, 0), _i("f"), _i("G", 1), _i("S")])


def test_redundant_repeat_still_removed():
    out = optimize_positioning_ops([_i("g", 1), _i("f"), _i("g", 1), _i("f")])
    assert [op for _, op in out].count("g") == 1


# --- Property: random balanced streams keep every painted state ---

_colors = st.sampled_from([0, 0.5, 1])
_state_op = st.one_of(
    st.builds(lambda v: _i("g", v), _colors),
    st.builds(lambda v: _i("G", v), _colors),
    st.builds(lambda r, g, b: _i("rg", r, g, b), _colors, _colors, _colors),
    st.builds(lambda r, g, b: _i("RG", r, g, b), _colors, _colors, _colors),
    st.builds(lambda c: _i("k", c, 0, 0, 0), _colors),
    st.just(_i("cs", "/Pattern")),
    st.just(_i("scn", "/P0")),
    st.just(_i("CS", "/DeviceRGB")),
    st.builds(lambda v: _i("SC", v, 0, 0), _colors),
    st.builds(lambda v: _i("w", v), st.sampled_from([1, 2])),
    st.builds(lambda v: _i("J", v), st.sampled_from([0, 1])),
    st.builds(lambda v: _i("d", [], v), st.sampled_from([0, 3])),
)
_paint_op = st.sampled_from(
    [_i("f"), _i("S"), _i("B"), _i("Do", "/X0"), _i("n"), _i("INLINE IMAGE", "img")]
)
_text_obj = st.lists(
    st.one_of(
        st.builds(lambda f: _i("Tf", f, 12), st.sampled_from(["/F1", "/F2"])),
        st.just(_i("Tj", "x")),
        _state_op,
    ),
    max_size=4,
).map(lambda body: [_i("BT"), *body, _i("ET")])


def _block(children):
    return st.lists(
        st.one_of(
            _state_op.map(lambda x: [x]),
            _paint_op.map(lambda x: [x]),
            _text_obj,
            children.map(lambda body: [_i("q"), *body, _i("Q")]),
        ),
        max_size=6,
    ).map(lambda parts: [ins for part in parts for ins in part])


_stream = st.recursive(st.just([]), _block, max_leaves=25)


@settings(max_examples=400, deadline=None)
@given(_stream)
def test_random_streams_preserve_painted_state(instructions):
    _assert_preserved(instructions)


def test_inline_image_block_survives_real_pikepdf_roundtrip():
    # Ghostscript/dvips shape: each bitmap drawn as `q cm BI...EI Q`.
    import pikepdf

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(100, 100))
    page = pdf.pages[0]
    page.Contents = pdf.make_stream(
        b"q 10 0 0 10 5 5 cm BI /W 1 /H 1 /BPC 8 /CS /G ID \x00 EI Q"
        b" q 10 0 0 10 50 50 cm BI /W 1 /H 1 /BPC 8 /CS /G ID \xff EI Q"
    )
    instructions = list(pikepdf.parse_content_stream(page))
    out = optimize_positioning_ops(instructions, aggressive_tf=True)
    assert pikepdf.unparse_content_stream(out) == pikepdf.unparse_content_stream(instructions)


@pytest.mark.parametrize(
    "instructions",
    [
        [_i("g", 1), _i("q"), _i("q"), _i("f"), _i("Q"), _i("Q"), _i("g", 0), _i("f")],
        [_i("BT"), _i("Tf", "/F1", 12), _i("ET"), _i("Do", "/X0")],
        [_i("RG", 1, 0, 0), _i("q"), _i("Do", "/X0"), _i("Q"), _i("RG", 0, 0, 1), _i("S")],
    ],
)
def test_nested_and_xobject_inheritance(instructions):
    _assert_preserved(instructions)
