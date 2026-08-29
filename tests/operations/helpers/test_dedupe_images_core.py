# tests/operations/helpers/test_dedupe_images_core.py
"""Tests for deduplicate_image_xobjects: real duplicate images get
merged, references get rewritten correctly, non-duplicate images are
left alone, threshold excludes small images, a shared SMask is handled,
and the merged PDF is still structurally valid after a save/reopen
round trip.
"""

from __future__ import annotations

import pytest
import pikepdf
from pikepdf import Dictionary, Name

from pdftl.operations.helpers.dedupe_images_core import (
    deduplicate_image_xobjects,
    _find_image_candidates,
)
from pdftl.utils.stream_dedup import stream_length as _stream_length


@pytest.fixture
def pdf():
    p = pikepdf.Pdf.new()
    yield p
    p.close()


def _make_image(pdf, data: bytes, **extra):
    return pdf.make_indirect(
        pikepdf.Stream(
            pdf,
            data,
            Type=Name("/XObject"),
            Subtype=Name("/Image"),
            Width=10,
            Height=10,
            BitsPerComponent=8,
            ColorSpace=Name("/DeviceGray"),
            **extra,
        )
    )


def test_two_identical_images_merge(pdf, tmp_path):
    data = b"\xff\xd8\xff" + b"A" * 100
    img1 = _make_image(pdf, data)
    img2 = _make_image(pdf, data)
    page1 = pdf.add_blank_page()
    page1.Resources = Dictionary(XObject=Dictionary(Im1=img1))
    page2 = pdf.add_blank_page()
    page2.Resources = Dictionary(XObject=Dictionary(Im2=img2))

    result = deduplicate_image_xobjects(pdf)

    assert result == {"merged": 1, "bytes_saved": 103}
    assert page1.Resources.XObject.Im1.objgen == page2.Resources.XObject.Im2.objgen


def test_non_duplicate_images_left_alone(pdf):
    img1 = _make_image(pdf, b"\xff\xd8\xff" + b"A" * 100)
    img2 = _make_image(pdf, b"\xff\xd8\xff" + b"B" * 100)  # same length, different bytes
    img3 = _make_image(pdf, b"\xff\xd8\xff" + b"C" * 50)  # different length entirely
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=img1, Im2=img2, Im3=img3))

    result = deduplicate_image_xobjects(pdf)

    assert result == {"merged": 0, "bytes_saved": 0}
    assert page.Resources.XObject.Im1.objgen != page.Resources.XObject.Im2.objgen
    assert page.Resources.XObject.Im1.objgen != page.Resources.XObject.Im3.objgen


def test_three_way_duplicate_merges_to_one_master(pdf):
    data = b"same bytes " * 20
    img1 = _make_image(pdf, data)
    img2 = _make_image(pdf, data)
    img3 = _make_image(pdf, data)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=img1, Im2=img2, Im3=img3))

    result = deduplicate_image_xobjects(pdf)

    assert result == {"merged": 2, "bytes_saved": 440}
    xobj = page.Resources.XObject
    assert xobj.Im1.objgen == xobj.Im2.objgen == xobj.Im3.objgen


def test_threshold_excludes_small_images(pdf):
    small_data = b"x" * 10
    img1 = _make_image(pdf, small_data)
    img2 = _make_image(pdf, small_data)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=img1, Im2=img2))

    result = deduplicate_image_xobjects(pdf, threshold=1000)

    assert result == {"merged": 0, "bytes_saved": 0}
    assert page.Resources.XObject.Im1.objgen != page.Resources.XObject.Im2.objgen


def test_threshold_zero_merges_everything(pdf):
    small_data = b"x" * 10
    img1 = _make_image(pdf, small_data)
    img2 = _make_image(pdf, small_data)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=img1, Im2=img2))

    result = deduplicate_image_xobjects(pdf, threshold=0)

    assert result == {"merged": 1, "bytes_saved": 10}


def test_shared_smask_between_otherwise_identical_images_merges(pdf):
    smask = pdf.make_indirect(pikepdf.Stream(pdf, b"mask data"))
    img1 = _make_image(pdf, b"image content here", SMask=smask)
    img2 = _make_image(pdf, b"image content here", SMask=smask)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=img1, Im2=img2))

    result = deduplicate_image_xobjects(pdf)

    assert result == {"merged": 1, "bytes_saved": 18}
    assert page.Resources.XObject.Im1.objgen == page.Resources.XObject.Im2.objgen


def test_distinct_smasks_with_same_content_still_merges_images(pdf):
    # Two separate SMask objects that are themselves equivalent -- the
    # images referencing them should still be considered duplicates,
    # since check_object_equivalence recurses into the SMask reference
    # rather than comparing SMask object identity.
    smask1 = pdf.make_indirect(pikepdf.Stream(pdf, b"mask data"))
    smask2 = pdf.make_indirect(pikepdf.Stream(pdf, b"mask data"))
    img1 = _make_image(pdf, b"image content here", SMask=smask1)
    img2 = _make_image(pdf, b"image content here", SMask=smask2)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=img1, Im2=img2))

    result = deduplicate_image_xobjects(pdf)

    assert result == {"merged": 1, "bytes_saved": 18}


def test_images_with_different_smasks_not_merged(pdf):
    smask1 = pdf.make_indirect(pikepdf.Stream(pdf, b"mask A"))
    smask2 = pdf.make_indirect(pikepdf.Stream(pdf, b"mask B"))
    img1 = _make_image(pdf, b"image content here", SMask=smask1)
    img2 = _make_image(pdf, b"image content here", SMask=smask2)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=img1, Im2=img2))

    result = deduplicate_image_xobjects(pdf)

    assert result == {"merged": 0, "bytes_saved": 0}


def test_candidate_already_claimed_by_earlier_master_is_skipped(pdf):
    # Coverage gap found via `coverage run`: the `if cand_og in
    # replacements: continue` branch (guarding against re-comparing a
    # candidate that a *previous* master in the same same-length run
    # already claimed) was never exercised. Layout: A, B, D are mutually
    # identical; C is a same-length-but-different-content image sorted
    # between them. Master=A claims both B and D. When the outer loop
    # then reaches master=C, its only remaining candidate is D -- which
    # is already claimed -- so that comparison must be skipped rather
    # than redone.
    same_len_dup = b"AAAAAAAAAAAA"  # 12 bytes
    same_len_other = b"CCCCCCCCCCCC"  # 12 bytes, different content
    a = _make_image(pdf, same_len_dup)
    b = _make_image(pdf, same_len_dup)
    c = _make_image(pdf, same_len_other)
    d = _make_image(pdf, same_len_dup)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(A=a, B=b, C=c, D=d))

    result = deduplicate_image_xobjects(pdf)

    assert result == {"merged": 2, "bytes_saved": 24}
    xobj = page.Resources.XObject
    assert xobj.A.objgen == xobj.B.objgen == xobj.D.objgen
    assert xobj.C.objgen != xobj.A.objgen


def test_rewrite_references_inside_indirect_array(pdf):
    # Coverage gap: _rewrite_references' `elif isinstance(node,
    # pikepdf.Array)` branch, and specifically rewriting an indirect
    # reference held directly as an array *element* (not a dict value),
    # was never exercised by the dict-based Resources/XObject fixtures
    # used elsewhere in this file. Duplicate image references can appear
    # inside a bare array in real PDFs (e.g. some non-standard resource
    # lists), so this constructs one directly.
    from pikepdf import Array

    data = b"duplicate image bytes"
    img1 = _make_image(pdf, data)
    img2 = _make_image(pdf, data)
    holder = pdf.make_indirect(Array([img1, img2]))

    result = deduplicate_image_xobjects(pdf)

    assert result == {"merged": 1, "bytes_saved": 21}
    assert holder[0].objgen == holder[1].objgen


def test_rewrite_references_recurses_into_direct_nested_array(pdf):
    # Coverage gap: the recursive call for a *non-indirect* nested
    # array/dict found while walking an array (as opposed to a
    # dictionary) was never exercised. Builds an indirect array whose
    # element is a direct (not made-indirect) sub-array, which itself
    # holds the reference that needs rewriting.
    from pikepdf import Array

    data = b"nested duplicate bytes"
    img1 = _make_image(pdf, data)
    img2 = _make_image(pdf, data)
    direct_nested = Array([img2])  # deliberately not made indirect
    wrapper = pdf.make_indirect(Array([img1, direct_nested]))

    result = deduplicate_image_xobjects(pdf)

    assert result == {"merged": 1, "bytes_saved": 22}
    assert wrapper[0].objgen == wrapper[1][0].objgen


def test_no_images_in_document_returns_zero(pdf):
    pdf.add_blank_page()
    result = deduplicate_image_xobjects(pdf)
    assert result == {"merged": 0, "bytes_saved": 0}


def test_rewrite_references_skips_indirect_pikepdf_scalar_objects(pdf):
    # Regression test for a real bug found during coverage work:
    # pdf.objects can yield a bare Python int (not a pikepdf.Object
    # wrapper) for an indirect scalar, and the top-level rewrite loop in
    # deduplicate_image_xobjects called `.objgen` on every entry
    # unconditionally, raising AttributeError on such an object. Fixed
    # with an `isinstance(obj, pikepdf.Object)` guard in the production
    # code; this test covers that guard's False branch (the bare-int
    # case) below.
    data = b"duplicate scalar-sibling bytes"
    img1 = _make_image(pdf, data)
    img2 = _make_image(pdf, data)
    # An indirect object that is neither Dictionary, Stream, nor Array.
    pdf.make_indirect(42)
    # A genuine pikepdf.Object indirect scalar (unlike the bare int
    # above, this one DOES reach _rewrite_references, covering its
    # top-level if/elif both being False -- neither Dictionary/Stream
    # nor Array).
    pdf.make_indirect(pikepdf.String("unrelated indirect string"))
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=img1, Im2=img2))

    result = deduplicate_image_xobjects(pdf)

    assert result == {"merged": 1, "bytes_saved": len(data)}


def test_single_image_inner_loop_has_no_candidates_to_compare(pdf):
    # Coverage gap: with exactly one image candidate, the outer loop's
    # only iteration (i=0) has an empty `candidates[i + 1:]` slice, so
    # the inner `for candidate in ...:` loop body never runs at all --
    # it falls straight through without ever reaching `break`. None of
    # the other tests exercise this zero-iteration inner-loop path,
    # since they all have at least one same-length successor to compare.
    img = _make_image(pdf, b"only one image, nothing to compare against")
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=img))

    result = deduplicate_image_xobjects(pdf)

    assert result == {"merged": 0, "bytes_saved": 0}


def test_non_image_streams_are_not_candidates(pdf):
    # A content stream and a font-descriptor-like stream should never be
    # treated as image candidates, even if their raw bytes happen to
    # coincide with an actual image's bytes -- scope is image XObjects
    # only, per the qpdf upstream feature this was scoped from.
    data = b"shared bytes"
    img = _make_image(pdf, data)
    non_image_stream = pdf.make_indirect(pikepdf.Stream(pdf, data))  # no /Subtype /Image
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=img), Font=Dictionary(F1=non_image_stream))

    candidates = _find_image_candidates(pdf)
    assert len(candidates) == 1
    assert candidates[0].objgen == img.objgen


def test_find_image_candidates_deduped_by_objgen(pdf):
    # Regression test for a real bug found during verification: pdf.objects
    # (installed pikepdf 10.5.1) was observed yielding more than one Python
    # wrapper for the same underlying object -- same .objgen, different
    # id() -- which corrupted the merge count (an image compared against
    # itself looked like two candidates). _find_image_candidates must
    # dedupe by objgen so each real image appears exactly once.
    _make_image(pdf, b"some image bytes")
    candidates = _find_image_candidates(pdf)
    objgens = [c.objgen for c in candidates]
    assert len(objgens) == len(set(objgens)), "duplicate objgen in candidate list"


def test_bytes_saved_sums_only_merged_away_duplicates_not_masters(pdf):
    # Two separate duplicate groups of different sizes -- bytes_saved
    # must count each merged-away *duplicate's* length, not the
    # surviving master's, and must not double count the masters
    # themselves (which are kept, not "saved").
    small_dup = b"S" * 40
    big_dup = b"B" * 4000
    unique = b"U" * 4000  # same length as big_dup, but distinct content

    s1 = _make_image(pdf, small_dup)
    s2 = _make_image(pdf, small_dup)
    b1 = _make_image(pdf, big_dup)
    b2 = _make_image(pdf, big_dup)
    u = _make_image(pdf, unique)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(S1=s1, S2=s2, B1=b1, B2=b2, U=u))

    result = deduplicate_image_xobjects(pdf)

    assert result == {"merged": 2, "bytes_saved": len(small_dup) + len(big_dup)}


def test_stream_length_matches_declared_length(pdf):
    data = b"12345678901234567890"
    assert len(data) == 20
    img = _make_image(pdf, data)
    assert _stream_length(img) == 20


def test_merged_pdf_is_structurally_valid_after_save_and_reopen(pdf, tmp_path):
    data = b"round trip content " * 5
    img1 = _make_image(pdf, data)
    img2 = _make_image(pdf, data)
    page1 = pdf.add_blank_page()
    page1.Resources = Dictionary(XObject=Dictionary(Im1=img1))
    page2 = pdf.add_blank_page()
    page2.Resources = Dictionary(XObject=Dictionary(Im2=img2))

    result = deduplicate_image_xobjects(pdf)
    assert result == {"merged": 1, "bytes_saved": 95}

    out_path = tmp_path / "merged.pdf"
    pdf.save(str(out_path))

    reopened = pikepdf.Pdf.open(str(out_path))
    try:
        assert len(reopened.pages) == 2
        im1 = reopened.pages[0].Resources.XObject.Im1
        im2 = reopened.pages[1].Resources.XObject.Im2
        assert im1.objgen == im2.objgen
        assert im1.read_bytes() == data
    finally:
        reopened.close()


def test_merge_preserves_image_data_integrity(pdf, tmp_path):
    # The surviving (master) copy's bytes must be exactly the original
    # image data -- not corrupted or truncated by the merge/rewrite.
    data = bytes(range(256)) * 4  # 1024 bytes, deterministic content
    img1 = _make_image(pdf, data)
    img2 = _make_image(pdf, data)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=img1, Im2=img2))

    deduplicate_image_xobjects(pdf)

    out_path = tmp_path / "integrity.pdf"
    pdf.save(str(out_path))
    reopened = pikepdf.Pdf.open(str(out_path))
    try:
        merged_img = reopened.pages[0].Resources.XObject.Im1
        assert merged_img.read_bytes() == data
    finally:
        reopened.close()
