# tests/operations/helpers/test_dedupe_icc_core.py
"""Tests for deduplicate_icc_profiles core: real duplicate ICC profile
streams get merged wherever /ColorSpace can appear (image XObjects,
Resources/ColorSpace dicts, /Separation and /DeviceN alternate spaces),
non-duplicates and dict-key mismatches are correctly rejected, shared
profiles aren't double-counted, and output intents are ignored.
"""

from __future__ import annotations

import pytest
import pikepdf
from pikepdf import Array, Dictionary, Name

from pdftl.operations.helpers.dedupe_icc_core import (
    deduplicate_icc_profiles,
    _find_icc_candidates,
    _extract_icc_streams,
)


@pytest.fixture
def pdf():
    p = pikepdf.Pdf.new()
    yield p
    p.close()


def _make_icc_stream(pdf, data: bytes, n: int = 3, extra_keys=None):
    kwargs = dict(N=n, **(extra_keys or {}))
    return pdf.make_indirect(pikepdf.Stream(pdf, data, **kwargs))


def _make_image_with_icc(pdf, data: bytes, n: int = 3, extra_keys=None):
    """An image XObject whose own /ColorSpace is [/ICCBased ref]."""
    icc = _make_icc_stream(pdf, data, n=n, extra_keys=extra_keys)
    image = pdf.make_indirect(
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
    )
    return image, icc


# --- _extract_icc_streams / _find_icc_candidates ------------------------


def test_iccbased_in_image_colorspace_found(pdf):
    image, icc = _make_image_with_icc(pdf, b"icc profile bytes " * 10)
    candidates = _find_icc_candidates(pdf)
    assert len(candidates) == 1
    assert candidates[0].objgen == icc.objgen


def test_iccbased_in_resources_colorspace_dict_found(pdf):
    icc = _make_icc_stream(pdf, b"resources dict icc bytes " * 10)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(ColorSpace=Dictionary(CS0=Array([Name("/ICCBased"), icc])))
    candidates = _find_icc_candidates(pdf)
    assert len(candidates) == 1
    assert candidates[0].objgen == icc.objgen


def test_iccbased_nested_in_separation_alternate_found(pdf):
    icc = _make_icc_stream(pdf, b"separation alternate icc bytes " * 8)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(
        ColorSpace=Dictionary(
            CS0=Array(
                [
                    Name("/Separation"),
                    Name("/Spot1"),
                    Array([Name("/ICCBased"), icc]),
                    Array([Name("/Identity")]),
                ]
            )
        )
    )
    candidates = _find_icc_candidates(pdf)
    assert len(candidates) == 1
    assert candidates[0].objgen == icc.objgen


def test_iccbased_nested_in_devicen_alternate_found(pdf):
    icc = _make_icc_stream(pdf, b"devicen alternate icc bytes " * 8)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(
        ColorSpace=Dictionary(
            CS0=Array(
                [
                    Name("/DeviceN"),
                    Array([Name("/Spot1"), Name("/Spot2")]),
                    Array([Name("/ICCBased"), icc]),
                    Array([Name("/Identity")]),
                ]
            )
        )
    )
    candidates = _find_icc_candidates(pdf)
    assert len(candidates) == 1
    assert candidates[0].objgen == icc.objgen


def test_device_gray_name_colorspace_ignored(pdf):
    page = pdf.add_blank_page()
    page.Resources = Dictionary(ColorSpace=Dictionary(CS0=Name("/DeviceGray")))
    candidates = _find_icc_candidates(pdf)
    assert candidates == []


def test_indexed_colorspace_ignored(pdf):
    # /Indexed base spaces are a real shape we deliberately don't chase
    # into -- only /Separation and /DeviceN alternates are recursed.
    page = pdf.add_blank_page()
    page.Resources = Dictionary(
        ColorSpace=Dictionary(
            CS0=Array([Name("/Indexed"), Name("/DeviceRGB"), 255, b"\x00" * 768])
        )
    )
    candidates = _find_icc_candidates(pdf)
    assert candidates == []


def test_no_colorspace_key_ignored(pdf):
    pdf.make_indirect(Dictionary({"/Type": Name("/Whatever")}))
    candidates = _find_icc_candidates(pdf)
    assert candidates == []


def test_iccbased_tag_without_stream_reference_ignored(pdf):
    page = pdf.add_blank_page()
    page.Resources = Dictionary(ColorSpace=Dictionary(CS0=Array([Name("/ICCBased")])))
    candidates = _find_icc_candidates(pdf)
    assert candidates == []


def test_iccbased_second_element_not_a_stream_ignored(pdf):
    page = pdf.add_blank_page()
    page.Resources = Dictionary(ColorSpace=Dictionary(CS0=Array([Name("/ICCBased"), 42])))
    candidates = _find_icc_candidates(pdf)
    assert candidates == []


def test_empty_array_colorspace_ignored(pdf):
    page = pdf.add_blank_page()
    page.Resources = Dictionary(ColorSpace=Dictionary(CS0=Array([])))
    candidates = _find_icc_candidates(pdf)
    assert candidates == []


def test_shared_icc_profile_across_multiple_images_not_double_counted(pdf):
    data = b"one profile shared by two images " * 5
    icc = _make_icc_stream(pdf, data)
    for _ in range(2):
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
        )
    candidates = _find_icc_candidates(pdf)
    assert len(candidates) == 1
    assert candidates[0].objgen == icc.objgen


def test_output_intent_profile_not_a_candidate(pdf):
    # Root/OutputIntents/*/DestOutputProfile is deliberately excluded
    # from candidate discovery -- confirm it's genuinely invisible to
    # _find_icc_candidates, not merely untested.
    profile = pdf.make_indirect(pikepdf.Stream(pdf, b"output intent profile " * 10, N=4))
    pdf.Root.OutputIntents = Array(
        [Dictionary(Type=Name("/OutputIntent"), DestOutputProfile=profile)]
    )
    candidates = _find_icc_candidates(pdf)
    assert candidates == []


def test_extract_icc_streams_ignores_non_array_value():
    out = []
    _extract_icc_streams(Name("/DeviceGray"), out, set())
    assert out == []


# --- deduplicate_icc_profiles: merge behavior ----------------------------


def test_two_identical_icc_profiles_merge(pdf):
    data = b"\x00\x01icc profile payload " * 20
    image1, icc1 = _make_image_with_icc(pdf, data)
    image2, icc2 = _make_image_with_icc(pdf, data)

    result = deduplicate_icc_profiles(pdf)

    assert result == {"merged": 1, "bytes_saved": len(data)}
    assert image1.ColorSpace[1].objgen == image2.ColorSpace[1].objgen


def test_non_duplicate_icc_profiles_left_alone(pdf):
    image1, icc1 = _make_image_with_icc(pdf, b"profile A payload " * 20)
    image2, icc2 = _make_image_with_icc(pdf, b"profile B payload " * 20)  # same length

    result = deduplicate_icc_profiles(pdf)

    assert result == {"merged": 0, "bytes_saved": 0}
    assert image1.ColorSpace[1].objgen != image2.ColorSpace[1].objgen


def test_different_n_component_count_not_merged(pdf):
    # Same raw bytes, different /N (e.g. one claims RGB, one claims
    # CMYK) -- a real identity difference, must not merge. Mirrors the
    # FontFile-vs-FontFile2 dict-key-set caution.
    data = b"identical bytes, different N " * 10
    image1, icc1 = _make_image_with_icc(pdf, data, n=3)
    image2, icc2 = _make_image_with_icc(pdf, data, n=4)

    result = deduplicate_icc_profiles(pdf)

    assert result == {"merged": 0, "bytes_saved": 0}
    assert image1.ColorSpace[1].objgen != image2.ColorSpace[1].objgen


def test_alternate_key_presence_prevents_merge(pdf):
    # Same bytes, same /N, but one profile additionally carries
    # /Alternate -- this is the deliberate strict-pass rejection
    # described in the module docstring, not a bug.
    data = b"same bytes, one has alternate " * 10
    image1, icc1 = _make_image_with_icc(pdf, data, n=3)
    image2, icc2 = _make_image_with_icc(
        pdf, data, n=3, extra_keys={"Alternate": Name("/DeviceRGB")}
    )

    result = deduplicate_icc_profiles(pdf)

    assert result == {"merged": 0, "bytes_saved": 0}
    assert image1.ColorSpace[1].objgen != image2.ColorSpace[1].objgen


def test_three_way_duplicate_merges_to_one_master(pdf):
    data = b"shared across three images " * 15
    image1, _ = _make_image_with_icc(pdf, data)
    image2, _ = _make_image_with_icc(pdf, data)
    image3, _ = _make_image_with_icc(pdf, data)

    result = deduplicate_icc_profiles(pdf)

    assert result == {"merged": 2, "bytes_saved": 2 * len(data)}
    assert (
        image1.ColorSpace[1].objgen == image2.ColorSpace[1].objgen == image3.ColorSpace[1].objgen
    )


def test_threshold_excludes_small_profiles(pdf):
    small_data = b"tiny"
    image1, _ = _make_image_with_icc(pdf, small_data)
    image2, _ = _make_image_with_icc(pdf, small_data)

    result = deduplicate_icc_profiles(pdf, threshold=1000)

    assert result == {"merged": 0, "bytes_saved": 0}
    assert image1.ColorSpace[1].objgen != image2.ColorSpace[1].objgen


def test_merge_across_separation_alternate_spaces(pdf):
    data = b"shared spot-color alternate profile " * 8

    def make_separation_page(name):
        icc = _make_icc_stream(pdf, data)
        page = pdf.add_blank_page()
        page.Resources = Dictionary(
            ColorSpace=Dictionary(
                CS0=Array(
                    [
                        Name("/Separation"),
                        Name(name),
                        Array([Name("/ICCBased"), icc]),
                        Array([Name("/Identity")]),
                    ]
                )
            )
        )
        return page

    page1 = make_separation_page("/Spot1")
    page2 = make_separation_page("/Spot2")

    result = deduplicate_icc_profiles(pdf)

    assert result == {"merged": 1, "bytes_saved": len(data)}
    assert (
        page1.Resources.ColorSpace.CS0[2][1].objgen == page2.Resources.ColorSpace.CS0[2][1].objgen
    )


def test_output_intent_profile_never_merged_even_if_bytewise_identical(pdf):
    # Confirms the exclusion holds through the full merge pass too, not
    # just candidate discovery: an output intent profile that happens
    # to share bytes with an image's ICC profile must be left alone.
    data = b"shared bytes between image and output intent " * 5
    image, image_icc = _make_image_with_icc(pdf, data)
    oi_profile = pdf.make_indirect(pikepdf.Stream(pdf, data, N=3))
    pdf.Root.OutputIntents = Array(
        [Dictionary(Type=Name("/OutputIntent"), DestOutputProfile=oi_profile)]
    )

    result = deduplicate_icc_profiles(pdf)

    assert result == {"merged": 0, "bytes_saved": 0}
    assert pdf.Root.OutputIntents[0].DestOutputProfile.objgen == oi_profile.objgen


def test_no_icc_profiles_in_document_returns_zero(pdf):
    pdf.add_blank_page()
    result = deduplicate_icc_profiles(pdf)
    assert result == {"merged": 0, "bytes_saved": 0}


def test_merged_pdf_is_structurally_valid_after_save_and_reopen(pdf, tmp_path):
    data = b"round trip icc profile content " * 10
    image1, _ = _make_image_with_icc(pdf, data)
    image2, _ = _make_image_with_icc(pdf, data)
    page = pdf.add_blank_page()
    page.Resources = Dictionary(XObject=Dictionary(Im1=image1, Im2=image2))

    result = deduplicate_icc_profiles(pdf)
    assert result == {"merged": 1, "bytes_saved": len(data)}

    out_path = tmp_path / "merged_icc.pdf"
    pdf.save(str(out_path))

    reopened = pikepdf.Pdf.open(str(out_path))
    try:
        im1 = reopened.pages[0].Resources.XObject.Im1
        im2 = reopened.pages[0].Resources.XObject.Im2
        assert im1.ColorSpace[1].objgen == im2.ColorSpace[1].objgen
        assert im1.ColorSpace[1].read_bytes() == data
    finally:
        reopened.close()


# add to tests/operations/helpers/test_dedupe_icc_core.py


def test_iccbased_found_via_inherited_page_resources(pdf):
    # /Resources set on the shared /Pages node, NOT on the page itself
    # -- must still be found. This is the inheritance case that a raw
    # container.get("/Resources") lookup would silently miss.
    #
    # NOTE: pdf.add_blank_page() always sets an empty /Resources dict
    # directly on the page it creates, so it can never be used to
    # construct a genuinely-inherited fixture -- the page must be built
    # manually here, with /Resources deliberately omitted.
    icc = _make_icc_stream(pdf, b"inherited resources icc bytes " * 10)
    pdf.Root.Pages.Resources = Dictionary(
        ColorSpace=Dictionary(CS0=Array([Name("/ICCBased"), icc]))
    )

    page_dict = Dictionary(
        {
            "/Type": Name("/Page"),
            "/Parent": pdf.Root.Pages,
            "/MediaBox": Array([0, 0, 612, 792]),
        }
    )
    page = pdf.make_indirect(page_dict)
    pdf.Root.Pages.Kids.append(page)
    pdf.Root.Pages.Count = len(pdf.Root.Pages.Kids)

    assert "/Resources" not in page  # sanity check: confirm it's not set directly

    candidates = _find_icc_candidates(pdf)
    assert len(candidates) == 1
    assert candidates[0].objgen == icc.objgen


def test_iccbased_found_via_inherited_resources_merges(pdf):
    # Same as above, but two pages inheriting the SAME icc profile from
    # the shared /Pages node -- confirms end-to-end merge behavior
    # (not just discovery) works through inheritance too. Since both
    # pages inherit the identical resources dict, there's only one
    # underlying ICC stream to begin with -- this mainly guards against
    # a future regression where inheritance handling double-visits or
    # miscounts rather than proving a "merge", since there's nothing
    # here to actually merge.
    data = b"shared inherited profile " * 10
    icc = _make_icc_stream(pdf, data)
    pdf.add_blank_page()
    pdf.add_blank_page()
    pdf.Root.Pages.Resources = Dictionary(
        ColorSpace=Dictionary(CS0=Array([Name("/ICCBased"), icc]))
    )

    result = deduplicate_icc_profiles(pdf)

    assert result == {"merged": 0, "bytes_saved": 0}
    candidates = _find_icc_candidates(pdf)
    assert len(candidates) == 1


# add to tests/operations/helpers/test_dedupe_icc_core.py


def test_non_dictionary_objects_in_pdf_are_skipped_without_error(pdf):
    # pdf.objects can contain plain scalars/arrays alongside real
    # dictionaries and streams; the top-level isinstance guard in
    # _find_icc_candidates must skip these cleanly rather than raising.
    pdf.make_indirect(42)
    pdf.make_indirect(Array([1, 2, 3]))
    data = b"still finds real icc profiles despite noise " * 5
    image1, _ = _make_image_with_icc(pdf, data)
    image2, _ = _make_image_with_icc(pdf, data)

    result = deduplicate_icc_profiles(pdf)

    assert result == {"merged": 1, "bytes_saved": len(data)}
