# tests/operations/helpers/test_dedupe_fonts_core.py
"""Tests for deduplicate_font_files: real duplicate embedded font
programs get merged, non-duplicates and cross-format collisions are
correctly rejected, shared /FontDescriptors are handled without double
counting, and the merged PDF survives a save/reopen round trip.
"""

from __future__ import annotations

import pytest
import pikepdf
from pikepdf import Array, Dictionary, Name

from pdftl.operations.helpers.dedupe_fonts_core import (
    deduplicate_font_files,
    _find_font_file_candidates,
    deduplicate_font_dicts,
    _find_font_dict_candidates,
    _font_dict_bucket_key,
)


@pytest.fixture
def pdf():
    p = pikepdf.Pdf.new()
    yield p
    p.close()


def _make_font(
    pdf, fontfile_data, fontfile_key="/FontFile", extra_stream_keys=None, base_font="/TestFont"
):
    """A minimal /Font -> /FontDescriptor -> FontFile* chain. Returns
    (font_dict, descriptor_dict, fontfile_stream), each already
    indirect."""
    stream_kwargs = dict(extra_stream_keys or {})
    fontfile = pdf.make_indirect(pikepdf.Stream(pdf, fontfile_data, **stream_kwargs))
    descriptor = pdf.make_indirect(
        Dictionary(
            {
                "/Type": Name("/FontDescriptor"),
                "/FontName": Name(base_font),
                fontfile_key: fontfile,
            }
        )
    )
    font = pdf.make_indirect(
        Dictionary(
            {
                "/Type": Name("/Font"),
                "/Subtype": Name("/TrueType"),
                "/BaseFont": Name(base_font),
                "/FontDescriptor": descriptor,
            }
        )
    )
    return font, descriptor, fontfile


def test_two_identical_fontfiles_merge(pdf):
    data = b"\x00\x01\x00\x00" + b"program bytes " * 20
    font1, desc1, ff1 = _make_font(pdf, data, base_font="/FontA")
    font2, desc2, ff2 = _make_font(pdf, data, base_font="/FontB")

    result = deduplicate_font_files(pdf)

    assert result == {"merged": 1, "bytes_saved": len(data)}
    assert desc1.FontFile.objgen == desc2.FontFile.objgen


def test_non_duplicate_fontfiles_left_alone(pdf):
    font1, desc1, ff1 = _make_font(pdf, b"program A " * 30, base_font="/FontA")
    font2, desc2, ff2 = _make_font(pdf, b"program B " * 30, base_font="/FontB")  # same length

    result = deduplicate_font_files(pdf)

    assert result == {"merged": 0, "bytes_saved": 0}
    assert desc1.FontFile.objgen != desc2.FontFile.objgen


def test_three_way_duplicate_merges_to_one_master(pdf):
    data = b"shared program data " * 15
    _, desc1, _ = _make_font(pdf, data, base_font="/FontA")
    _, desc2, _ = _make_font(pdf, data, base_font="/FontB")
    _, desc3, _ = _make_font(pdf, data, base_font="/FontC")

    result = deduplicate_font_files(pdf)

    assert result == {"merged": 2, "bytes_saved": 2 * len(data)}
    assert desc1.FontFile.objgen == desc2.FontFile.objgen == desc3.FontFile.objgen


def test_threshold_excludes_small_fontfiles(pdf):
    small_data = b"tiny"
    _, desc1, _ = _make_font(pdf, small_data, base_font="/FontA")
    _, desc2, _ = _make_font(pdf, small_data, base_font="/FontB")

    result = deduplicate_font_files(pdf, threshold=1000)

    assert result == {"merged": 0, "bytes_saved": 0}
    assert desc1.FontFile.objgen != desc2.FontFile.objgen


def test_fontfile_vs_fontfile2_same_bytes_not_merged(pdf):
    # Point 5 from design discussion, verified empirically: a Type1
    # /FontFile (real ones carry /Length1../3) and a TrueType
    # /FontFile2 (carries only /Length1) with byte-identical program
    # data must NOT merge, because their own stream dicts have
    # different key sets -- no special-casing needed in production
    # code, check_object_equivalence's existing dict comparison rejects
    # this for free.
    data = b"identical raw bytes, different format claims " * 5
    _, desc1, ff1 = _make_font(
        pdf,
        data,
        fontfile_key="/FontFile",
        extra_stream_keys={"Length1": 10, "Length2": 20, "Length3": 0},
        base_font="/FontA",
    )
    _, desc2, ff2 = _make_font(
        pdf,
        data,
        fontfile_key="/FontFile2",
        extra_stream_keys={"Length1": 30},
        base_font="/FontB",
    )

    result = deduplicate_font_files(pdf)

    assert result == {"merged": 0, "bytes_saved": 0}
    assert desc1.FontFile.objgen != desc2.FontFile2.objgen


def test_fontfile3_subtype_mismatch_not_merged(pdf):
    data = b"cff or opentype program bytes " * 4
    _, desc1, _ = _make_font(
        pdf,
        data,
        fontfile_key="/FontFile3",
        extra_stream_keys={"Subtype": Name("/Type1C")},
        base_font="/FontA",
    )
    _, desc2, _ = _make_font(
        pdf,
        data,
        fontfile_key="/FontFile3",
        extra_stream_keys={"Subtype": Name("/OpenType")},
        base_font="/FontB",
    )

    result = deduplicate_font_files(pdf)

    assert result == {"merged": 0, "bytes_saved": 0}
    assert desc1.FontFile3.objgen != desc2.FontFile3.objgen


def test_fontfile3_subtype_match_merges(pdf):
    data = b"cff program bytes " * 6
    _, desc1, _ = _make_font(
        pdf,
        data,
        fontfile_key="/FontFile3",
        extra_stream_keys={"Subtype": Name("/Type1C")},
        base_font="/FontA",
    )
    _, desc2, _ = _make_font(
        pdf,
        data,
        fontfile_key="/FontFile3",
        extra_stream_keys={"Subtype": Name("/Type1C")},
        base_font="/FontB",
    )

    result = deduplicate_font_files(pdf)

    assert result == {"merged": 1, "bytes_saved": len(data)}
    assert desc1.FontFile3.objgen == desc2.FontFile3.objgen


def test_shared_fontdescriptor_across_multiple_fonts_not_double_counted(pdf):
    data = b"one program shared by two /Font dicts " * 3
    font1, descriptor, fontfile = _make_font(pdf, data, base_font="/FontA")
    # A second /Font dict deliberately reusing the SAME /FontDescriptor
    # (and therefore the same FontFile) -- a legitimate real-world shape
    # (e.g. regular/bold sharing a descriptor in some producers).
    pdf.make_indirect(
        Dictionary(
            {
                "/Type": Name("/Font"),
                "/Subtype": Name("/TrueType"),
                "/BaseFont": Name("/FontA"),
                "/FontDescriptor": descriptor,
            }
        )
    )

    candidates = _find_font_file_candidates(pdf)

    assert len(candidates) == 1
    assert candidates[0].objgen == fontfile.objgen

    # And it must not be merged against itself.
    result = deduplicate_font_files(pdf)
    assert result == {"merged": 0, "bytes_saved": 0}


def test_font_without_fontdescriptor_ignored(pdf):
    # A non-embedded standard font (e.g. a bare Helvetica /Font dict
    # with no /FontDescriptor at all) is common and must be silently
    # skipped, not treated as an error.
    pdf.make_indirect(
        Dictionary(
            {
                "/Type": Name("/Font"),
                "/Subtype": Name("/Type1"),
                "/BaseFont": Name("/Helvetica"),
            }
        )
    )
    candidates = _find_font_file_candidates(pdf)
    assert candidates == []


def test_fontdescriptor_present_but_not_a_dictionary_ignored(pdf):
    pdf.make_indirect(
        Dictionary(
            {
                "/Type": Name("/Font"),
                "/Subtype": Name("/TrueType"),
                "/BaseFont": Name("/Broken"),
                "/FontDescriptor": 42,  # malformed: should be a dict
            }
        )
    )
    candidates = _find_font_file_candidates(pdf)
    assert candidates == []


def test_fontfile_key_present_but_not_a_stream_ignored(pdf):
    descriptor = pdf.make_indirect(
        Dictionary(
            {
                "/Type": Name("/FontDescriptor"),
                "/FontName": Name("/Broken"),
                "/FontFile": 99,  # malformed: should be a stream
            }
        )
    )
    pdf.make_indirect(
        Dictionary(
            {
                "/Type": Name("/Font"),
                "/Subtype": Name("/TrueType"),
                "/BaseFont": Name("/Broken"),
                "/FontDescriptor": descriptor,
            }
        )
    )
    candidates = _find_font_file_candidates(pdf)
    assert candidates == []


def test_non_font_dictionaries_are_not_candidates(pdf):
    # Any ordinary dictionary in the document (here, deliberately one
    # that even carries a /FontDescriptor-shaped key, to prove the
    # filter is genuinely on /Type == /Font and not just "has a
    # FontDescriptor key") must not be treated as a font.
    pdf.make_indirect(
        Dictionary(
            {
                "/Type": Name("/NotAFont"),
                "/FontDescriptor": Dictionary({"/FontFile": 1}),
            }
        )
    )
    candidates = _find_font_file_candidates(pdf)
    assert candidates == []


def test_non_dictionary_objects_are_skipped_without_error(pdf):
    # pdf.objects can contain plain scalars/arrays alongside real
    # dictionaries; the top-level isinstance(obj, Dictionary) guard
    # must skip these cleanly rather than raising.
    pdf.make_indirect(42)
    pdf.make_indirect(Array([1, 2, 3]))
    data = b"still finds real fonts despite noise " * 3
    _, desc1, _ = _make_font(pdf, data, base_font="/FontA")
    _, desc2, _ = _make_font(pdf, data, base_font="/FontB")

    result = deduplicate_font_files(pdf)

    assert result == {"merged": 1, "bytes_saved": len(data)}


def test_no_fonts_in_document_returns_zero(pdf):
    pdf.add_blank_page()
    result = deduplicate_font_files(pdf)
    assert result == {"merged": 0, "bytes_saved": 0}


def test_merged_pdf_is_structurally_valid_after_save_and_reopen(pdf, tmp_path):
    data = b"round trip font program content " * 10
    font1, desc1, _ = _make_font(pdf, data, base_font="/FontA")
    font2, desc2, _ = _make_font(pdf, data, base_font="/FontB")
    page = pdf.add_blank_page()
    page.Resources = Dictionary(Font=Dictionary(F1=font1, F2=font2))

    result = deduplicate_font_files(pdf)
    assert result == {"merged": 1, "bytes_saved": len(data)}

    out_path = tmp_path / "merged_fonts.pdf"
    pdf.save(str(out_path))

    reopened = pikepdf.Pdf.open(str(out_path))
    try:
        f1 = reopened.pages[0].Resources.Font.F1
        f2 = reopened.pages[0].Resources.Font.F2
        assert f1.FontDescriptor.FontFile.objgen == f2.FontDescriptor.FontFile.objgen
        assert f1.FontDescriptor.FontFile.read_bytes() == data
    finally:
        reopened.close()


# --- deduplicate_font_dicts: whole-/Font-dictionary merge pass ------------


def _make_full_font(pdf, fontfile_data, base_font="/TestFont", widths=None):
    """A complete /Font dict (BaseFont, FirstChar/LastChar/Widths,
    Encoding, FontDescriptor -> FontFile2), unlike _make_font's minimal
    FontFile-only chain -- needed so whole-dict equivalence has enough
    surface area (Widths in particular) to genuinely exercise."""
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


class TestDeduplicateFontDicts:
    def test_two_fully_identical_font_dicts_merge(self, pdf):
        data = b"identical program bytes " * 10
        font1, _, _ = _make_full_font(pdf, data, base_font="/FontA")
        font2, _, _ = _make_full_font(pdf, data, base_font="/FontA")
        page = pdf.add_blank_page()
        page.Resources = Dictionary(Font=Dictionary(F1=font1, F2=font2))

        result = deduplicate_font_dicts(pdf)

        assert result == {"merged": 1}
        assert page.Resources.Font.F1.objgen == page.Resources.Font.F2.objgen

    def test_identical_program_different_widths_does_not_merge(self, pdf):
        # This is the whole reason the fontfile-only pass exists as a
        # second, looser pass -- a regression here would silently make
        # the two passes redundant with each other.
        data = b"same program, different widths " * 8
        font1, _, _ = _make_full_font(pdf, data, base_font="/FontA", widths=[500, 500, 500])
        font2, _, _ = _make_full_font(pdf, data, base_font="/FontA", widths=[600, 600, 600])
        page = pdf.add_blank_page()
        page.Resources = Dictionary(Font=Dictionary(F1=font1, F2=font2))

        result = deduplicate_font_dicts(pdf)

        assert result == {"merged": 0}
        assert page.Resources.Font.F1.objgen != page.Resources.Font.F2.objgen

    def test_different_basefont_does_not_merge(self, pdf):
        data = b"same everything but basefont " * 6
        font1, _, _ = _make_full_font(pdf, data, base_font="/FontA")
        font2, _, _ = _make_full_font(pdf, data, base_font="/FontB")
        page = pdf.add_blank_page()
        page.Resources = Dictionary(Font=Dictionary(F1=font1, F2=font2))

        result = deduplicate_font_dicts(pdf)

        assert result == {"merged": 0}
        assert page.Resources.Font.F1.objgen != page.Resources.Font.F2.objgen

    def test_three_way_identical_font_dicts_merge_to_one_master(self, pdf):
        data = b"three way shared program " * 5
        f1, _, _ = _make_full_font(pdf, data, base_font="/FontA")
        f2, _, _ = _make_full_font(pdf, data, base_font="/FontA")
        f3, _, _ = _make_full_font(pdf, data, base_font="/FontA")
        page = pdf.add_blank_page()
        page.Resources = Dictionary(Font=Dictionary(F1=f1, F2=f2, F3=f3))

        result = deduplicate_font_dicts(pdf)

        assert result == {"merged": 2}
        assert (
            page.Resources.Font.F1.objgen
            == page.Resources.Font.F2.objgen
            == page.Resources.Font.F3.objgen
        )

    def test_bucket_prefilter_does_not_cause_false_negative(self, pdf):
        # Four fonts sharing the same bucket key (same key count, same
        # BaseFont) -- two genuine near-duplicates (differing only in
        # Widths, must NOT merge) crowding the bucket alongside the
        # real duplicate pair, to confirm the bucket prefilter doesn't
        # accidentally swallow or miss the real match.
        data = b"payload " * 12
        distractor1, _, _ = _make_full_font(pdf, data, base_font="/Shared", widths=[100] * 3)
        distractor2, _, _ = _make_full_font(pdf, data, base_font="/Shared", widths=[200] * 3)
        dup1, _, _ = _make_full_font(pdf, data, base_font="/Shared", widths=[300] * 3)
        dup2, _, _ = _make_full_font(pdf, data, base_font="/Shared", widths=[300] * 3)
        page = pdf.add_blank_page()
        page.Resources = Dictionary(
            Font=Dictionary(D1=distractor1, D2=distractor2, U1=dup1, U2=dup2)
        )

        result = deduplicate_font_dicts(pdf)

        assert result == {"merged": 1}
        assert page.Resources.Font.D1.objgen != page.Resources.Font.D2.objgen
        assert page.Resources.Font.U1.objgen == page.Resources.Font.U2.objgen

    def test_find_font_dict_candidates_dedupes_shared_reference(self, pdf):
        # A single /Font object referenced twice from the same
        # Resources dict must not be double-counted as two candidates.
        # This holds because pikepdf's own `pdf.objects` iterator
        # already yields each indirect object exactly once -- there is
        # no app-level dedup logic left to exercise here.
        data = b"shared font dict referenced twice " * 4
        font, _, _ = _make_full_font(pdf, data, base_font="/FontA")
        page = pdf.add_blank_page()
        page.Resources = Dictionary(Font=Dictionary(F1=font, F2=font))

        candidates = _find_font_dict_candidates(pdf)

        assert len(candidates) == 1
        assert candidates[0].objgen == font.objgen

        result = deduplicate_font_dicts(pdf)
        assert result == {"merged": 0}

    def test_replacement_map_skips_candidate_already_merged_by_earlier_master(self, pdf):
        # Bucket order [A, B, C] where A and C are fully equivalent but
        # B is not equivalent to either (different Widths, same bucket
        # key). Master A merges C in on its own pass; master B's inner
        # loop then encounters C again, already replaced -- exercising
        # the "cand_og in replacements: continue" guard directly rather
        # than incidentally.
        data = b"shared program for A and C " * 6
        font_a, _, _ = _make_full_font(pdf, data, base_font="/Shared", widths=[100] * 3)
        font_b, _, _ = _make_full_font(pdf, data, base_font="/Shared", widths=[200] * 3)
        font_c, _, _ = _make_full_font(pdf, data, base_font="/Shared", widths=[100] * 3)
        page = pdf.add_blank_page()
        page.Resources = Dictionary(Font=Dictionary(A=font_a, B=font_b, C=font_c))

        result = deduplicate_font_dicts(pdf)

        assert result == {"merged": 1}
        assert page.Resources.Font.A.objgen == page.Resources.Font.C.objgen
        assert page.Resources.Font.B.objgen != page.Resources.Font.A.objgen

    def test_no_font_dicts_returns_zero(self, pdf):
        pdf.add_blank_page()
        result = deduplicate_font_dicts(pdf)
        assert result == {"merged": 0}

    def test_bucket_key_uses_key_count_and_basefont(self, pdf):
        data = b"bucket key smoke test " * 4
        font, _, _ = _make_full_font(pdf, data, base_font="/FontA")
        key = _font_dict_bucket_key(font)
        assert key == (8, "/FontA")
