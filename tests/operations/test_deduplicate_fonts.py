# tests/operations/test_deduplicate_fonts.py
"""Operation-level tests for `deduplicate_fonts`: exercises the
registered operation function itself (argument parsing + wiring to
deduplicate_font_dicts / deduplicate_font_files), not just the core
helpers directly. Mirrors tests/operations/test_deduplicate_images.py's
shape.
"""

from __future__ import annotations

import pytest
import pikepdf
from pikepdf import Array, Dictionary, Name

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.deduplicate_fonts import (
    deduplicate_fonts,
    _parse_deduplicate_fonts_args,
)


@pytest.fixture
def pdf():
    p = pikepdf.Pdf.new()
    yield p
    p.close()


def _make_full_font(pdf, fontfile_data, base_font="/TestFont", widths=None):
    if widths is None:
        widths = [500, 500, 500]
    fontfile = pdf.make_indirect(pikepdf.Stream(pdf, fontfile_data))
    descriptor = pdf.make_indirect(
        Dictionary(
            {
                "/Type": Name("/FontDescriptor"),
                "/FontName": Name(base_font),
                "/FontFile2": fontfile,
            }
        )
    )
    font = pdf.make_indirect(
        Dictionary(
            {
                "/Type": Name("/Font"),
                "/Subtype": Name("/TrueType"),
                "/BaseFont": Name(base_font),
                "/FirstChar": 32,
                "/LastChar": 32 + len(widths) - 1,
                "/Widths": Array(widths),
                "/Encoding": Name("/WinAnsiEncoding"),
                "/FontDescriptor": descriptor,
            }
        )
    )
    return font, descriptor, fontfile


# --- argument parsing -------------------------------------------------


def test_parse_args_defaults_to_full_mode_and_zero_min_bytes():
    mode, min_bytes = _parse_deduplicate_fonts_args([])
    assert mode == "full"
    assert min_bytes == 0


def test_parse_args_accepts_fontfile_only_mode():
    mode, _ = _parse_deduplicate_fonts_args(["mode=fontfile_only"])
    assert mode == "fontfile_only"


def test_parse_args_invalid_mode_raises():
    with pytest.raises(InvalidArgumentError):
        _parse_deduplicate_fonts_args(["mode=bogus"])


def test_parse_args_min_bytes_with_suffix():
    _, min_bytes = _parse_deduplicate_fonts_args(["min_bytes=1KB"])
    assert min_bytes == 1024


def test_parse_args_unknown_key_raises():
    with pytest.raises(InvalidArgumentError):
        _parse_deduplicate_fonts_args(["bogus=1"])


# --- end-to-end through the operation ---------------------------------


def test_default_mode_merges_whole_identical_font_dicts(pdf):
    data = b"identical program bytes " * 10
    font1, _, _ = _make_full_font(pdf, data, base_font="/FontA")
    font2, _, _ = _make_full_font(pdf, data, base_font="/FontA")
    page = pdf.add_blank_page()
    page.Resources = Dictionary(Font=Dictionary(F1=font1, F2=font2))

    result = deduplicate_fonts(pdf, [])

    assert result.success
    assert result.pdf is pdf
    assert page.Resources.Font.F1.objgen == page.Resources.Font.F2.objgen


def test_default_mode_falls_back_to_fontfile_pass_when_widths_differ(pdf):
    data = b"same program, different widths " * 8
    font1, desc1, _ = _make_full_font(pdf, data, base_font="/FontA", widths=[500, 500, 500])
    font2, desc2, _ = _make_full_font(pdf, data, base_font="/FontA", widths=[600, 600, 600])
    page = pdf.add_blank_page()
    page.Resources = Dictionary(Font=Dictionary(F1=font1, F2=font2))

    result = deduplicate_fonts(pdf, [])

    assert result.success
    # Whole-dict pass must NOT merge these (differing /Widths) ...
    assert page.Resources.Font.F1.objgen != page.Resources.Font.F2.objgen
    # ... but the fontfile pass still merges the underlying program.
    assert desc1.FontFile2.objgen == desc2.FontFile2.objgen


def test_fontfile_only_mode_skips_whole_dict_merge(pdf):
    data = b"identical program bytes " * 10
    font1, desc1, _ = _make_full_font(pdf, data, base_font="/FontA")
    font2, desc2, _ = _make_full_font(pdf, data, base_font="/FontA")
    page = pdf.add_blank_page()
    page.Resources = Dictionary(Font=Dictionary(F1=font1, F2=font2))

    result = deduplicate_fonts(pdf, ["mode=fontfile_only"])

    assert result.success
    # Dict pass skipped -- /Font dicts stay distinct even though they
    # are, in fact, fully identical.
    assert page.Resources.Font.F1.objgen != page.Resources.Font.F2.objgen
    # Fontfile pass still runs and merges the program.
    assert desc1.FontFile2.objgen == desc2.FontFile2.objgen


def test_min_bytes_gates_fontfile_pass_only(pdf):
    # Widths differ, so only the fontfile pass is even eligible to
    # merge these two; min_bytes above the program size must suppress
    # that merge too, leaving nothing merged at all.
    data = b"small"
    font1, desc1, _ = _make_full_font(pdf, data, base_font="/FontA", widths=[500, 500, 500])
    font2, desc2, _ = _make_full_font(pdf, data, base_font="/FontA", widths=[600, 600, 600])
    page = pdf.add_blank_page()
    page.Resources = Dictionary(Font=Dictionary(F1=font1, F2=font2))

    result = deduplicate_fonts(pdf, ["min_bytes=1000"])

    assert result.success
    assert page.Resources.Font.F1.objgen != page.Resources.Font.F2.objgen
    assert desc1.FontFile2.objgen != desc2.FontFile2.objgen


def test_min_bytes_does_not_gate_whole_dict_pass(pdf):
    # min_bytes only applies to the fontfile pass per the docstring --
    # a fully identical, tiny-program pair must still merge in the
    # dict pass regardless of a huge min_bytes.
    data = b"tiny"
    font1, _, _ = _make_full_font(pdf, data, base_font="/FontA")
    font2, _, _ = _make_full_font(pdf, data, base_font="/FontA")
    page = pdf.add_blank_page()
    page.Resources = Dictionary(Font=Dictionary(F1=font1, F2=font2))

    result = deduplicate_fonts(pdf, ["min_bytes=1000000"])

    assert result.success
    assert page.Resources.Font.F1.objgen == page.Resources.Font.F2.objgen


def test_invalid_mode_raises_invalid_argument_error(pdf):
    with pytest.raises(InvalidArgumentError):
        deduplicate_fonts(pdf, ["mode=bogus"])


def test_no_duplicates_is_a_success_noop(pdf):
    font1, _, _ = _make_full_font(pdf, b"program one " * 5, base_font="/FontA")
    font2, _, _ = _make_full_font(pdf, b"program two, longer " * 5, base_font="/FontB")
    page = pdf.add_blank_page()
    page.Resources = Dictionary(Font=Dictionary(F1=font1, F2=font2))

    result = deduplicate_fonts(pdf, [])

    assert result.success
    assert page.Resources.Font.F1.objgen != page.Resources.Font.F2.objgen


def test_none_args_defaults_like_empty_list(pdf):
    data = b"identical program bytes " * 10
    font1, _, _ = _make_full_font(pdf, data, base_font="/FontA")
    font2, _, _ = _make_full_font(pdf, data, base_font="/FontA")
    page = pdf.add_blank_page()
    page.Resources = Dictionary(Font=Dictionary(F1=font1, F2=font2))

    result = deduplicate_fonts(pdf, None)

    assert result.success
    assert page.Resources.Font.F1.objgen == page.Resources.Font.F2.objgen
