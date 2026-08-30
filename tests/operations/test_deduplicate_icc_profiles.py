# tests/operations/test_deduplicate_icc_profiles.py
"""Operation-level test for `deduplicate_icc_profiles`: exercises the
registered operation function itself (argument parsing + wiring to
deduplicate_icc_core.deduplicate_icc_profiles), not just the core
helper directly. Mirrors tests/operations/test_deduplicate_images.py's
shape.

Also includes end-to-end coverage that goes through the public
`pdftl.deduplicate_icc_profiles` API against a real file on disk
(synthetic fixture built by _build_icc_dedupe_fixture below), rather
than calling the operation function directly against an in-memory PDF.
No existing e2e harness/pattern was confirmed to exist in the suite as
of the ICC dedupe handoff, so this is added here rather than in a new
tests/e2e/ directory -- move it if/when such a convention is settled.

The real corpus file this fixture stands in for
(tests/files/private/pop/utf16le-annot.pdf) has unknown copyright
provenance and can't be committed to this MPL-2 repo, hence building a
clean-room equivalent instead. See _build_icc_dedupe_fixture's docstring
for full rationale, including which part of the real file's behavior
(orphaned-resource GC) this fixture does NOT reproduce.
"""

from __future__ import annotations

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name

import pdftl
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.deduplicate_icc_profiles import (
    deduplicate_icc_profiles,
    _parse_deduplicate_icc_profiles_args,
)


@pytest.fixture
def pdf():
    p = pikepdf.Pdf.new()
    yield p
    p.close()


def _make_image_with_icc(pdf, data: bytes, n: int = 3):
    icc = pdf.make_indirect(pikepdf.Stream(pdf, data, N=n))
    return (
        pdf.make_indirect(
            pikepdf.Stream(
                pdf,
                b"\x00" * 4,
                Type=Name("/XObject"),
                Subtype=Name("/Image"),
                Width=2,
                Height=2,
                BitsPerComponent=8,
                ColorSpace=Array([Name("/ICCBased"), icc]),
            )
        ),
        icc,
    )


# --- argument parsing -------------------------------------------------


def test_parse_args_defaults_to_zero_min_bytes():
    assert _parse_deduplicate_icc_profiles_args([]) == 0


def test_parse_args_min_bytes_with_suffix():
    assert _parse_deduplicate_icc_profiles_args(["min_bytes=1KB"]) == 1024


def test_parse_args_unknown_key_raises():
    with pytest.raises(InvalidArgumentError):
        _parse_deduplicate_icc_profiles_args(["bogus=1"])


# --- end-to-end through the operation, in-memory -----------------------


def test_operation_merges_duplicates_with_no_args(pdf):
    data = b"duplicate icc payload " * 10
    image1, icc1 = _make_image_with_icc(pdf, data)
    image2, icc2 = _make_image_with_icc(pdf, data)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=image1, Im2=image2))

    result = deduplicate_icc_profiles(pdf, [])

    assert result.success
    assert result.pdf is pdf
    assert (
        page.Resources.XObject.Im1.ColorSpace[1].objgen
        == page.Resources.XObject.Im2.ColorSpace[1].objgen
    )


def test_operation_respects_min_bytes_option(pdf):
    small_dup = b"x" * 10
    image1, _ = _make_image_with_icc(pdf, small_dup)
    image2, _ = _make_image_with_icc(pdf, small_dup)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=image1, Im2=image2))

    result = deduplicate_icc_profiles(pdf, ["min_bytes=1000"])

    assert result.success
    assert (
        page.Resources.XObject.Im1.ColorSpace[1].objgen
        != page.Resources.XObject.Im2.ColorSpace[1].objgen
    )


def test_operation_min_bytes_with_suffix_allows_merge(pdf):
    data = b"y" * 2000
    image1, _ = _make_image_with_icc(pdf, data)
    image2, _ = _make_image_with_icc(pdf, data)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=image1, Im2=image2))

    result = deduplicate_icc_profiles(pdf, ["min_bytes=1KB"])

    assert result.success
    assert (
        page.Resources.XObject.Im1.ColorSpace[1].objgen
        == page.Resources.XObject.Im2.ColorSpace[1].objgen
    )


def test_operation_no_args_defaults_to_no_minimum(pdf):
    tiny = b"z"
    image1, _ = _make_image_with_icc(pdf, tiny)
    image2, _ = _make_image_with_icc(pdf, tiny)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=image1, Im2=image2))

    result = deduplicate_icc_profiles(pdf, None)

    assert result.success
    assert (
        page.Resources.XObject.Im1.ColorSpace[1].objgen
        == page.Resources.XObject.Im2.ColorSpace[1].objgen
    )


def test_operation_unknown_option_raises():
    with pytest.raises(InvalidArgumentError):
        _parse_deduplicate_icc_profiles_args(["bogus=1"])


def test_operation_no_duplicates_is_a_success_noop(pdf):
    image1, _ = _make_image_with_icc(pdf, b"profile one")
    image2, _ = _make_image_with_icc(pdf, b"profile two, longer")
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=image1, Im2=image2))

    result = deduplicate_icc_profiles(pdf, [])

    assert result.success
    assert (
        page.Resources.XObject.Im1.ColorSpace[1].objgen
        != page.Resources.XObject.Im2.ColorSpace[1].objgen
    )


# --- end-to-end through the public API, against a real file on disk ---
#
# The fixture below is a clean-room stand-in for the real-world file
# tests/files/private/pop/utf16le-annot.pdf, which showed genuine ICC
# profile duplication during manual corpus testing (see handoff notes)
# but has unknown copyright provenance and can't be committed here.
#
# SCOPE NOTE: the real file also demonstrated that some raw ICC objects
# are reachable only through other orphaned /Resources dicts and get
# garbage-collected away by ANY rewrite (8 raw -> 4 live). That specific
# interaction is NOT reproduced below -- pikepdf's own writer prunes
# genuinely-unreferenced objects at construction time (confirmed
# empirically while building this), so there's no clean way to write a
# "raw but orphaned" object via pikepdf's normal API. The real file's
# orphaned objects most likely come from a hand-edited/malformed xref
# table, out of scope for a clean synthetic fixture. If that GC
# interaction specifically needs coverage later, it requires the real
# file or a raw-bytes xref hand-edit, not this fixture.
#
# What IS covered (the independently-testable core behavior of
# deduplicate_icc_profiles itself): two live pages with separate but
# byte-identical ICC profiles (must merge), a third live page with a
# distinct profile (must not merge), and an OutputIntents profile that
# happens to share bytes with the duplicate pair (must be excluded from
# candidate discovery entirely, per that design decision).


def _build_icc_dedupe_fixture(output_path) -> None:
    """Write a synthetic PDF with 4 live ICC-shaped stream objects:
    2 byte-identical duplicates referenced from two pages' /ColorSpace,
    1 distinct profile referenced from a third page's image XObject, and
    1 OutputIntents DestOutputProfile that shares bytes with the
    duplicate pair but must never be touched by the operation.
    """
    pdf = pikepdf.Pdf.new()
    while len(pdf.pages) > 0:
        del pdf.pages[0]

    profile_a_bytes = b"\x00\x00\x02\x10" + b"ADBE" + b"synthetic-profile-A" + bytes(200)
    profile_b_bytes = b"\x00\x00\x01\xf0" + b"ADBE" + b"synthetic-profile-B-distinct" + bytes(150)

    def make_blank_page():
        page = Dictionary(
            Type=Name.Page,
            MediaBox=Array([0, 0, 200, 200]),
            Contents=pikepdf.Stream(pdf, b"q Q"),
            Resources=Dictionary(),
        )
        return pdf.make_indirect(page)

    # Duplicate pair: two separate stream objects, same bytes, same /N.
    icc_dup_1 = pdf.make_indirect(pikepdf.Stream(pdf, profile_a_bytes, N=3))
    icc_dup_2 = pdf.make_indirect(pikepdf.Stream(pdf, profile_a_bytes, N=3))
    assert bytes(icc_dup_1.read_bytes()) == bytes(icc_dup_2.read_bytes())

    page1 = make_blank_page()
    page1.Resources[Name.ColorSpace] = Dictionary(CS0=Array([Name.ICCBased, icc_dup_1]))
    pdf.pages.append(pikepdf.Page(page1))

    page2 = make_blank_page()
    page2.Resources[Name.ColorSpace] = Dictionary(CS0=Array([Name.ICCBased, icc_dup_2]))
    pdf.pages.append(pikepdf.Page(page2))

    # Distinct (non-duplicate) profile, set directly as an image
    # XObject's own /ColorSpace key.
    icc_distinct = pdf.make_indirect(pikepdf.Stream(pdf, profile_b_bytes, N=4))
    image_xobject = pikepdf.Stream(pdf, bytes(64))
    image_xobject[Name.Type] = Name.XObject
    image_xobject[Name.Subtype] = Name.Image
    image_xobject[Name.Width] = 4
    image_xobject[Name.Height] = 4
    image_xobject[Name.BitsPerComponent] = 8
    image_xobject[Name.ColorSpace] = Array([Name.ICCBased, icc_distinct])

    page3 = make_blank_page()
    page3.Resources[Name.XObject] = Dictionary(Im0=image_xobject)
    pdf.pages.append(pikepdf.Page(page3))

    # Root/OutputIntents profile sharing bytes with the duplicate pair --
    # must be excluded from candidate discovery, never merged/replaced.
    output_intent_icc = pdf.make_indirect(pikepdf.Stream(pdf, profile_a_bytes, N=3))
    output_intent = Dictionary(
        Type=Name.OutputIntent,
        S=Name.GTS_PDFA1,
        OutputConditionIdentifier=pikepdf.String("sRGB synthetic"),
        DestOutputProfile=output_intent_icc,
    )
    pdf.Root[Name.OutputIntents] = Array([pdf.make_indirect(output_intent)])

    pdf.save(str(output_path))
    pdf.close()


def _count_icc_streams(pdf: pikepdf.Pdf) -> list[tuple[int, bytes]]:
    """Return (N, bytes) for every live ICC-shaped stream object in the file."""
    found = []
    for obj in pdf.objects:
        try:
            n = obj.get("/N")
        except Exception:
            n = None
        if n is not None:
            found.append((int(n), bytes(obj.read_bytes())))
    return found


@pytest.fixture()
def icc_fixture_path(tmp_path):
    fixture_path = tmp_path / "icc_dedupe_fixture.pdf"
    _build_icc_dedupe_fixture(fixture_path)
    return fixture_path


def test_e2e_api_merges_true_duplicates_from_real_file(icc_fixture_path, tmp_path):
    result_pdf = pdftl.deduplicate_icc_profiles(str(icc_fixture_path), operation_args=[])

    # The reference rewiring has already happened in-memory at this point
    # (see the page1/page2 objgen check below, which works directly off
    # result_pdf) -- but the now-unreferenced original duplicate stream
    # is NOT pruned from pdf.objects until an actual save+reopen cycle;
    # pikepdf only garbage-collects unreachable objects at write time, not
    # as a side effect of rewiring references in memory. Counting streams
    # directly off result_pdf would therefore still see the pre-merge
    # object and over-count. Save + reopen before counting to match what
    # a real caller who writes the output file would actually see.
    saved_path = tmp_path / "deduped_output.pdf"
    result_pdf.save(str(saved_path))
    with pikepdf.open(saved_path) as reopened:
        icc_streams = _count_icc_streams(reopened)

        # Before dedupe there were 4 live ICC-shaped objects (see fixture
        # docstring above): 2 true duplicates, 1 distinct, 1 OutputIntents
        # profile sharing bytes with the duplicate pair. After dedupe and
        # a save/reopen there should be exactly 3: the merged survivor,
        # the distinct one, and the untouched OutputIntents profile.
        assert len(icc_streams) == 3, (
            f"expected 3 live ICC-shaped streams after dedupe, found "
            f"{len(icc_streams)}: {[(n, len(b)) for n, b in icc_streams]}"
        )

        merged_survivors = [(n, b) for n, b in icc_streams if n == 3 and len(b) == 227]
        # There are legitimately TWO objects matching this shape: the
        # page-tree merge survivor, and the untouched OutputIntents
        # profile, which deliberately shares the same bytes/N by
        # construction but must stay a separate object (see
        # test_e2e_api_excludes_output_intents for the identity check
        # that actually distinguishes them). Content alone can't tell
        # them apart here, so this only checks that no THIRD copy of
        # the duplicate pair survived the merge.
        assert len(merged_survivors) == 2, (
            f"expected exactly 2 objects with the duplicate pair's shape "
            f"(1 merge survivor + 1 untouched OutputIntents profile), "
            f"found {len(merged_survivors)}"
        )

        distinct_survivors = [(n, b) for n, b in icc_streams if n == 4]
        assert len(distinct_survivors) == 1, "distinct ICC profile was lost or altered"

    # The reference-rewiring check itself doesn't need the save/reopen
    # round trip -- it's already true of result_pdf directly, and is the
    # thing that actually proves a merge happened rather than just an
    # object becoming unreferenced.
    page1_icc = result_pdf.pages[0].Resources.ColorSpace.CS0[1]
    page2_icc = result_pdf.pages[1].Resources.ColorSpace.CS0[1]
    assert page1_icc.objgen == page2_icc.objgen, (
        "page1 and page2 still reference different ICC profile objects "
        "after dedupe -- merge did not actually happen"
    )


def test_e2e_api_excludes_output_intents(icc_fixture_path):
    """
    The OutputIntents DestOutputProfile shares bytes with the page-tree
    duplicate pair, but must be excluded from candidate discovery
    entirely (per the design decision documented in the ICC dedupe
    handoff). Fails if a future change starts merging OutputIntents
    profiles into page-tree survivors.
    """
    with pikepdf.open(icc_fixture_path) as original:
        original_oi_bytes = bytes(original.Root.OutputIntents[0].DestOutputProfile.read_bytes())

    result_pdf = pdftl.deduplicate_icc_profiles(str(icc_fixture_path), operation_args=[])

    deduped_oi_bytes = bytes(result_pdf.Root.OutputIntents[0].DestOutputProfile.read_bytes())
    assert original_oi_bytes == deduped_oi_bytes, (
        "OutputIntents DestOutputProfile bytes changed -- it should be "
        "completely untouched by deduplicate_icc_profiles"
    )

    page1_icc_objgen = result_pdf.pages[0].Resources.ColorSpace.CS0[1].objgen
    oi_objgen = result_pdf.Root.OutputIntents[0].DestOutputProfile.objgen
    assert page1_icc_objgen != oi_objgen, (
        "OutputIntents profile was merged into the page-tree survivor -- "
        "it must stay a separate object per the exclusion design decision"
    )


def test_e2e_api_respects_min_bytes(icc_fixture_path):
    # The duplicate pair is 227 bytes each; a threshold above that
    # should suppress the merge entirely -- same behavior as the
    # in-memory min_bytes tests above, exercised here through the
    # public API + a real file.
    result_pdf = pdftl.deduplicate_icc_profiles(
        str(icc_fixture_path), operation_args=["min_bytes=1KB"]
    )

    icc_streams = _count_icc_streams(result_pdf)
    assert len(icc_streams) == 4, (
        "expected no merge with a min_bytes threshold above the "
        f"duplicate pair's size, found {len(icc_streams)} live ICC streams"
    )
    page1_icc = result_pdf.pages[0].Resources.ColorSpace.CS0[1]
    page2_icc = result_pdf.pages[1].Resources.ColorSpace.CS0[1]
    assert page1_icc.objgen != page2_icc.objgen
