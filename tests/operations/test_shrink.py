# tests/operations/test_shrink.py

import zlib
from unittest.mock import MagicMock

import pikepdf
import pytest
from pikepdf import Name

import pdftl.core.constants as c
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.shrink import (
    _keeping_smaller_content,
    _page_ranges,
    _parse_args,
    shrink,
)
from pdftl.output.save import save_pdf

CONTENT = b"".join(b"q 10 0 0 10 %d 0 cm /Im%d Do Q\n" % (i, i % 2) for i in range(300))


def _pdf_with_duplicate_images():
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    images = {}
    for n in range(2):  # two separate objects with identical content
        img = pdf.make_stream(zlib.compress(bytes(range(256)) * 4))
        img.Type, img.Subtype = Name.XObject, Name.Image
        img.Width, img.Height, img.BitsPerComponent = 32, 32, 8
        img.ColorSpace, img.Filter = Name.DeviceGray, Name.FlateDecode
        images[f"/Im{n}"] = img
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(images))
    page.Contents = pdf.make_stream(zlib.compress(CONTENT, 1))
    page.Contents.Filter = Name.FlateDecode
    return pdf


def _save(pdf, path, options=None):
    save_pdf(pdf, output_filename=str(path), input_context=MagicMock(), options=options or {})
    return pikepdf.open(path)


# --- arguments ---


def test_parse_defaults_to_lossless_without_image_target():
    plan = _parse_args([])
    assert (plan.level, plan.dpi, plan.quality) == ("lossless", None, None)


@pytest.mark.parametrize(
    "args,expected",
    [
        (["balanced"], (200, 85)),
        (["max"], (150, 75)),
        (["max", "dpi=120", "quality=60"], (120, 60)),
    ],
)
def test_parse_level_image_targets(args, expected):
    plan = _parse_args(args)
    assert (plan.dpi, plan.quality) == expected


def test_parse_vector_page_limit_size():
    assert _parse_args(["max", "vector_page_limit=2MB"]).vector_page_limit == 2 * 1024 * 1024


@pytest.mark.parametrize(
    "args",
    [["extreme"], ["lossless", "max"], ["dpi=0"], ["quality=101"], ["quality=x"], ["bogus=1"]],
)
def test_parse_rejects_bad_args(args):
    with pytest.raises(InvalidArgumentError):
        _parse_args(args)


@pytest.mark.parametrize(
    "pages,expected",
    [([], []), ([1], ["1"]), ([1, 2, 3, 5, 7, 8], ["1-3", "5", "7-8"]), ([2, 4], ["2", "4"])],
)
def test_page_ranges(pages, expected):
    assert _page_ranges(pages) == expected


# --- end to end ---


def test_lossless_merges_duplicates_and_recompresses(tmp_path):
    pdf = _pdf_with_duplicate_images()
    shrink(pdf, [], str(tmp_path / "out.pdf"))
    with _save(pdf, tmp_path / "out.pdf") as out:
        xobjects = out.pages[0].Resources.XObject
        assert xobjects["/Im0"].objgen == xobjects["/Im1"].objgen
        contents = out.pages[0].Contents
        assert contents.read_bytes() == CONTENT
        assert len(contents.read_raw_bytes()) == len(zlib.compress(CONTENT, 9))


def test_explicit_output_options_override_shrink_hints(tmp_path):
    # An explicit `uncompress` beats shrink's recompress hint.
    pdf = _pdf_with_duplicate_images()
    shrink(pdf, [], str(tmp_path / "out.pdf"))
    with _save(pdf, tmp_path / "out.pdf", {"uncompress": True}) as out:
        assert out.pages[0].Contents.read_raw_bytes() == CONTENT


def test_level_hints_left_for_save():
    pdf = _pdf_with_duplicate_images()
    shrink(pdf, ["max"], "out.pdf")
    hints = getattr(pdf, c.PDFTL_SAVE_HINTS_ATTR)
    assert hints["recompress"] and hints["prune_resources"] and hints["drop_meta"]


@pytest.mark.parametrize(
    "exc",
    [RuntimeError("font trouble\nmore detail"), RuntimeError()],  # pikepdf raises bare ones
)
def test_failing_pass_is_skipped_and_others_still_run(monkeypatch, exc):
    def boom(pdf, args):
        raise exc

    monkeypatch.setattr("pdftl.operations.subset_fonts.subset_fonts", boom)
    pdf = _pdf_with_duplicate_images()
    result = shrink(pdf, [], "out.pdf")
    assert "skipped subset_fonts" in result.summary
    assert "deduplicate_images" in result.summary.split("; skipped")[0]
    xobjects = pdf.pages[0].Resources.XObject
    assert xobjects["/Im0"].objgen == xobjects["/Im1"].objgen


def test_levels_run_expected_passes(monkeypatch):
    calls = []

    def recorder(name):
        return lambda pdf, args, *rest: calls.append((name, list(args)))

    for mod, fn in [
        ("resample_images", "resample_images"),
        ("optimize_images", "optimize_images_pdf"),
        ("simplify_vectors", "simplify_vectors_in_content_streams"),
        ("subset_fonts", "subset_fonts"),
        ("deduplicate_fonts", "deduplicate_fonts"),
        ("deduplicate_images", "deduplicate_images"),
        ("deduplicate_icc_profiles", "deduplicate_icc_profiles"),
    ]:
        monkeypatch.setattr(f"pdftl.operations.{mod}.{fn}", recorder(fn))

    shrink(_pdf_with_duplicate_images(), [], "out.pdf")
    assert [n for n, _ in calls] == [
        "subset_fonts",
        "deduplicate_fonts",
        "deduplicate_images",
        "deduplicate_icc_profiles",
    ]

    calls.clear()
    shrink(_pdf_with_duplicate_images(), ["balanced"], "out.pdf")
    assert calls[0] == ("resample_images", ["dpi=200", "quality=85"])
    assert calls[1] == ("optimize_images_pdf", ["low", "jpeg_quality=85"])
    assert "simplify_vectors_in_content_streams" not in [n for n, _ in calls]

    calls.clear()
    shrink(_pdf_with_duplicate_images(), ["max"], "out.pdf")
    assert calls[1] == ("optimize_images_pdf", ["medium", "jpeg_quality=75"])
    assert ("simplify_vectors_in_content_streams", ["1"]) in calls


def test_max_skips_vector_simplification_on_oversized_pages(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "pdftl.operations.simplify_vectors.simplify_vectors_in_content_streams",
        lambda pdf, specs: seen.append(list(specs)),
    )
    for mod, fn in [
        ("resample_images", "resample_images"),
        ("optimize_images", "optimize_images_pdf"),
    ]:
        monkeypatch.setattr(f"pdftl.operations.{mod}.{fn}", lambda *a: None)
    pdf = _pdf_with_duplicate_images()
    pdf.add_blank_page()
    pdf.pages[1].Contents = pdf.make_stream(b"0 0 m 1 1 l S\n")
    shrink(pdf, ["max", f"vector_page_limit={len(CONTENT) - 1}"], "out.pdf")
    assert seen == [["2"]]


# --- keep-if-smaller ---


def _single_stream_pdf(data):
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.pages[0].Contents = pdf.make_stream(data)
    return pdf


def test_keeping_smaller_content_restores_growth():
    pdf = _single_stream_pdf(CONTENT)

    def grow(p):
        p.pages[0].Contents.write(CONTENT + bytes(range(256)) * 50)

    _keeping_smaller_content(pdf, grow)
    assert pdf.pages[0].Contents.read_bytes() == CONTENT


def test_keeping_smaller_content_keeps_shrink():
    pdf = _single_stream_pdf(CONTENT)
    smaller = CONTENT[: len(CONTENT) // 2]

    _keeping_smaller_content(pdf, lambda p: p.pages[0].Contents.write(smaller))
    assert pdf.pages[0].Contents.read_bytes() == smaller


# --- content walkers ---


def _form(pdf, data):
    form = pdf.make_stream(data)
    form.Type, form.Subtype = Name.XObject, Name.Form
    form.BBox = [0, 0, 10, 10]
    return form


def test_page_content_bytes_counts_arrays_forms_and_empty_pages():
    from pdftl.operations.shrink import _page_content_bytes

    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.add_blank_page()
    first, second = pdf.pages
    first.Contents = pikepdf.Array([pdf.make_stream(b"a" * 10), pdf.make_stream(b"b" * 5)])
    first.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm0=_form(pdf, b"c" * 7)))
    assert _page_content_bytes(first) == 22
    if "/Contents" in second.obj:
        del second.obj["/Contents"]
    assert _page_content_bytes(second) == 0


def test_content_streams_cover_arrays_forms_and_tiling_patterns():
    from pdftl.operations.shrink import _content_streams

    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.add_blank_page()
    parts = [pdf.make_stream(b"0 0 m"), pdf.make_stream(b"1 1 l S")]
    pdf.pages[0].Contents = pikepdf.Array(parts)
    if "/Contents" in pdf.pages[1].obj:
        del pdf.pages[1].obj["/Contents"]  # a page with no content at all
    form = _form(pdf, b"f")
    pattern = pdf.make_stream(b"0 0 1 1 re f")
    pattern.PatternType = 1
    pdf.Root.Keep = pikepdf.Array([form, pattern])
    found = {s.objgen for s in _content_streams(pdf)}
    assert {p.objgen for p in parts} | {form.objgen, pattern.objgen} <= found


def test_keeping_smaller_content_handles_a_stream_shared_by_two_pages():
    pdf = _single_stream_pdf(CONTENT)
    pdf.add_blank_page()
    pdf.pages[1].Contents = pdf.pages[0].Contents

    def grow(p):
        p.pages[0].Contents.write(CONTENT + bytes(range(256)) * 50)

    _keeping_smaller_content(pdf, grow)
    assert pdf.pages[1].Contents.read_bytes() == CONTENT


def test_max_skips_simplify_entirely_when_every_page_is_too_big(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "pdftl.operations.simplify_vectors.simplify_vectors_in_content_streams",
        lambda pdf, specs: seen.append(specs),
    )
    for mod, fn in [
        ("resample_images", "resample_images"),
        ("optimize_images", "optimize_images_pdf"),
    ]:
        monkeypatch.setattr(f"pdftl.operations.{mod}.{fn}", lambda *a: None)
    shrink(_pdf_with_duplicate_images(), ["max", "vector_page_limit=1"], "out.pdf")
    assert seen == []
