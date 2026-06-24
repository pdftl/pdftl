# src/pdftl/operations/data/pdf_operator_args.py

"""
Operand interpreters for PDF content stream operators.

Each entry maps an operator string to a callable that receives the token list
for that line (operator is last) and returns a human-readable string describing
the operands. Used by _annotate_stream to enrich operator comments.

These live alongside pdf_operators.py since they are tightly coupled to the
same operator set, but are kept in a separate file because they contain
callables rather than pure data.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Named constant maps
# ---------------------------------------------------------------------------

_LINE_JOIN = {
    "0": "Miter",
    "1": "Round",
    "2": "Bevel",
}

_LINE_CAP = {
    "0": "Butt",
    "1": "Round",
    "2": "Projecting Square",
}

_TEXT_RENDERING_MODE = {
    "0": "Fill",
    "1": "Stroke",
    "2": "Fill & Stroke",
    "3": "Invisible",
    "4": "Fill & Clip",
    "5": "Stroke & Clip",
    "6": "Fill, Stroke & Clip",
    "7": "Clip",
}

_BLEND_MODES = {
    "/Normal": "Normal",
    "/Multiply": "Multiply",
    "/Screen": "Screen",
    "/Overlay": "Overlay",
    "/Darken": "Darken",
    "/Lighten": "Lighten",
    "/ColorDodge": "Color Dodge",
    "/ColorBurn": "Color Burn",
    "/HardLight": "Hard Light",
    "/SoftLight": "Soft Light",
    "/Difference": "Difference",
    "/Exclusion": "Exclusion",
}

_COLOR_RENDERING_INTENT = {
    "/AbsoluteColorimetric": "Absolute Colorimetric",
    "/RelativeColorimetric": "Relative Colorimetric",
    "/Saturation": "Saturation",
    "/Perceptual": "Perceptual",
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _fmt_color_rgb(tokens: list[str], label: str) -> str:
    """Format r g b tokens as hex and named components."""
    try:
        r, g, b = float(tokens[-4]), float(tokens[-3]), float(tokens[-2])
        hex_col = f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"
        return f"r={r} g={g} b={b} ({hex_col})"
    except (ValueError, IndexError):
        return ""


def _fmt_color_cmyk(tokens: list[str]) -> str:
    try:
        c, m, y, k = float(tokens[-5]), float(tokens[-4]), float(tokens[-3]), float(tokens[-2])
        return f"c={c} m={m} y={y} k={k}"
    except (ValueError, IndexError):
        return ""


def _fmt_color_gray(tokens: list[str]) -> str:
    try:
        g = float(tokens[-2])
        hex_col = f"#{int(g * 255):02x}{int(g * 255):02x}{int(g * 255):02x}"
        return f"gray={g} ({hex_col})"
    except (ValueError, IndexError):
        return ""


def _fmt_dash_pattern(tokens: list[str]) -> str:
    """
    Dash pattern: [ array ] phase d
    tokens[-1] = "d", tokens[-2] = phase, tokens[-3..] = array including brackets.
    Reconstruct the array by scanning backwards for the matching '['.
    """
    try:
        phase = tokens[-2]
        # Find the bracket-enclosed array in the token list
        array_tokens = []
        in_array = False
        for t in tokens[:-2]:  # exclude phase and operator
            if t == "[":
                in_array = True
                array_tokens = []
            elif t == "]":
                break
            elif in_array:
                array_tokens.append(t)

        if not array_tokens:
            return f"solid, phase={phase}"
        pattern = " ".join(array_tokens)
        return f"dash=[{pattern}], phase={phase}"
    except (ValueError, IndexError):
        return ""


def _fmt_matrix(tokens: list[str]) -> str:
    """cm: a b c d e f — detect common cases (translation, scale, identity)."""
    try:
        a, b, c, d, e, f = (float(tokens[i]) for i in range(-7, -1))
        if a == 1 and b == 0 and c == 0 and d == 1:
            return f"translate x={e} y={f}"
        if b == 0 and c == 0 and e == 0 and f == 0:
            return f"scale x={a} y={d}"
        return f"a={a} b={b} c={c} d={d} e={e} f={f}"
    except (ValueError, IndexError):
        return ""


def _fmt_text_matrix(tokens: list[str]) -> str:
    """Tm: a b c d e f — same matrix as cm but sets text matrix."""
    try:
        a, b, c, d, e, f = (float(tokens[i]) for i in range(-7, -1))
        if a == 1 and b == 0 and c == 0 and d == 1:
            return f"translate x={e} y={f}"
        if b == 0 and c == 0 and e == 0 and f == 0:
            return f"scale x={a} y={d}"
        return f"a={a} b={b} c={c} d={d} e={e} f={f}"
    except (ValueError, IndexError):
        return ""


# ---------------------------------------------------------------------------
# Interpreter table
# ---------------------------------------------------------------------------

PDF_OPERATOR_ARGS: dict[str, callable] = {
    # Line geometry
    "w": lambda t: f"width={t[-2]}",
    "M": lambda t: f"limit={t[-2]}",
    "j": lambda t: f"style={_LINE_JOIN.get(t[-2], t[-2])}",
    "J": lambda t: f"cap={_LINE_CAP.get(t[-2], t[-2])}",
    "d": _fmt_dash_pattern,
    "i": lambda t: f"flatness={t[-2]}",
    # Color — stroking
    "G": lambda t: _fmt_color_gray(t),
    "RG": lambda t: _fmt_color_rgb(t, "stroke"),
    "K": lambda t: _fmt_color_cmyk(t),
    # Color — nonstroking
    "g": lambda t: _fmt_color_gray(t),
    "rg": lambda t: _fmt_color_rgb(t, "fill"),
    "k": lambda t: _fmt_color_cmyk(t),
    # Text state
    "Tc": lambda t: f"spacing={t[-2]}",
    "Tw": lambda t: f"spacing={t[-2]}",
    "Tz": lambda t: f"scale={t[-2]}%",
    "TL": lambda t: f"leading={t[-2]}",
    "Ts": lambda t: f"rise={t[-2]}",
    "Tr": lambda t: _TEXT_RENDERING_MODE.get(t[-2], t[-2]),
    # Tf note: BaseFont appended separately by resource lookup
    "Tf": lambda t: f"font={t[-3]} size={t[-2]}",
    "Td": lambda t: f"x={t[-3]} y={t[-2]}",
    "TD": lambda t: f"x={t[-3]} y={t[-2]}",
    "Tm": _fmt_text_matrix,
    # Path construction
    "m": lambda t: f"x={t[-3]} y={t[-2]}",
    "l": lambda t: f"x={t[-3]} y={t[-2]}",
    "re": lambda t: f"x={t[-5]} y={t[-4]} w={t[-3]} h={t[-2]}",
    # Graphics state
    "cm": _fmt_matrix,
    "ri": lambda t: _COLOR_RENDERING_INTENT.get(t[-2], t[-2]),
}
