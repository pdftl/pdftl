# tests/operations/data/test_pdf_operator_args.py

import pytest
from pdftl.operations.data.pdf_operator_args import (
    PDF_OPERATOR_ARGS,
    _fmt_color_cmyk,
    _fmt_color_gray,
    _fmt_color_rgb,
    _fmt_dash_pattern,
    _fmt_matrix,
    _fmt_text_matrix,
)


# ===========================================================================
# 1. Helper Functions Tests
# ===========================================================================


@pytest.mark.parametrize(
    "tokens,expected",
    [
        (["1.0", "0.0", "0.0", "rg"], "r=1.0 g=0.0 b=0.0 (#ff0000)"),
        (["0.5", "0.5", "0.5", "RG"], "r=0.5 g=0.5 b=0.5 (#7f7f7f)"),
        (["0.0", "1.0", "rg"], ""),  # IndexError (not enough tokens)
        (["invalid", "0.0", "0.0", "rg"], ""),  # ValueError (bad float conversion)
    ],
)
def test_fmt_color_rgb(tokens, expected):
    assert _fmt_color_rgb(tokens, "dummy_label") == expected


@pytest.mark.parametrize(
    "tokens,expected",
    [
        (["0.1", "0.2", "0.3", "0.4", "k"], "c=0.1 m=0.2 y=0.3 k=0.4"),
        (["0.1", "0.2", "0.3", "k"], ""),  # IndexError
        (["0.1", "bad_token", "0.3", "0.4", "k"], ""),  # ValueError
    ],
)
def test_fmt_color_cmyk(tokens, expected):
    assert _fmt_color_cmyk(tokens) == expected


@pytest.mark.parametrize(
    "tokens,expected",
    [
        (["0.5", "g"], "gray=0.5 (#7f7f7f)"),
        (["g"], ""),  # IndexError
        (["not_a_float", "G"], ""),  # ValueError
    ],
)
def test_fmt_color_gray(tokens, expected):
    assert _fmt_color_gray(tokens) == expected


@pytest.mark.parametrize(
    "tokens,expected",
    [
        (["[", "2", "1", "]", "0", "d"], "dash=[2 1], phase=0"),
        (["[", "]", "10", "d"], "solid, phase=10"),
        (["10", "d"], "solid, phase=10"),  # Valid output per current implementation logic
        (["d"], ""),  # IndexError (less than 2 tokens)
    ],
)
def test_fmt_dash_pattern(tokens, expected):
    assert _fmt_dash_pattern(tokens) == expected


@pytest.mark.parametrize(
    "tokens,expected",
    [
        (["1", "0", "0", "1", "20", "30", "cm"], "translate x=20.0 y=30.0"),
        (["2", "0", "0", "3", "0", "0", "cm"], "scale x=2.0 y=3.0"),
        (["2", "1", "3", "4", "5", "6", "cm"], "a=2.0 b=1.0 c=3.0 d=4.0 e=5.0 f=6.0"),
        (["1", "2", "cm"], ""),  # IndexError
        (["1", "2", "3", "4", "5", "abc", "cm"], ""),  # ValueError
    ],
)
def test_fmt_matrix(tokens, expected):
    assert _fmt_matrix(tokens) == expected


@pytest.mark.parametrize(
    "tokens,expected",
    [
        (["1", "0", "0", "1", "15", "25", "Tm"], "translate x=15.0 y=25.0"),
        (["1.5", "0", "0", "2.5", "0", "0", "Tm"], "scale x=1.5 y=2.5"),
        (["1", "2", "3", "4", "5", "6", "Tm"], "a=1.0 b=2.0 c=3.0 d=4.0 e=5.0 f=6.0"),
        (["1", "2", "Tm"], ""),  # IndexError
        (["1", "2", "3", "4", "5", "bad", "Tm"], ""),  # ValueError
    ],
)
def test_fmt_text_matrix(tokens, expected):
    assert _fmt_text_matrix(tokens) == expected


# ===========================================================================
# 2. PDF_OPERATOR_ARGS Map / Lambda Tests
# ===========================================================================


@pytest.mark.parametrize(
    "op,tokens,expected",
    [
        ("w", ["5", "w"], "width=5"),
        ("M", ["10", "M"], "limit=10"),
        ("j", ["1", "j"], "style=Round"),
        ("j", ["99", "j"], "style=99"),  # Fallback for unexpected mapping
        ("J", ["2", "J"], "cap=Projecting Square"),
        ("J", ["99", "J"], "cap=99"),  # Fallback
        ("i", ["3", "i"], "flatness=3"),
        ("Tc", ["0.5", "Tc"], "spacing=0.5"),
        ("Tw", ["1.5", "Tw"], "spacing=1.5"),
        ("Tz", ["80", "Tz"], "scale=80%"),
        ("TL", ["12", "TL"], "leading=12"),
        ("Ts", ["4", "Ts"], "rise=4"),
        ("Tr", ["1", "Tr"], "Stroke"),
        ("Tr", ["99", "Tr"], "99"),  # Fallback
        ("Tf", ["/F1", "12", "Tf"], "font=/F1 size=12"),
        ("Td", ["10", "20", "Td"], "x=10 y=20"),
        ("TD", ["30", "40", "TD"], "x=30 y=40"),
        ("m", ["100", "200", "m"], "x=100 y=200"),
        ("l", ["150", "250", "l"], "x=150 y=250"),
        ("re", ["10", "20", "200", "100", "re"], "x=10 y=20 w=200 h=100"),
        ("ri", ["/Perceptual", "ri"], "Perceptual"),
        ("ri", ["/UnknownIntent", "ri"], "/UnknownIntent"),  # Fallback
    ],
)
def test_operator_registry_lambdas(op, tokens, expected):
    """Ensures that pure data transformations and inline lambdas operate as expected."""
    interpreter = PDF_OPERATOR_ARGS[op]
    assert interpreter(tokens) == expected


def test_registry_delegates_to_helpers():
    """Verify registry delegates complex keys properly to verified functions."""
    assert PDF_OPERATOR_ARGS["d"](["[", "2", "]", "0", "d"]) == "dash=[2], phase=0"
    assert PDF_OPERATOR_ARGS["G"](["0.0", "G"]) == "gray=0.0 (#000000)"
    assert PDF_OPERATOR_ARGS["rg"](["1.0", "1.0", "0.0", "rg"]) == "r=1.0 g=1.0 b=0.0 (#ffff00)"
    assert PDF_OPERATOR_ARGS["K"](["0", "0", "0", "1", "K"]) == "c=0.0 m=0.0 y=0.0 k=1.0"
    assert PDF_OPERATOR_ARGS["cm"](["1", "0", "0", "1", "5", "5", "cm"]) == "translate x=5.0 y=5.0"
    assert PDF_OPERATOR_ARGS["Tm"](["1", "0", "0", "1", "5", "5", "Tm"]) == "translate x=5.0 y=5.0"
