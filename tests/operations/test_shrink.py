# tests/operations/test_shrink.py

import zlib
from unittest.mock import MagicMock

import pikepdf
import pytest
from pikepdf import Name

import pdftl.core.constants as c
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.shrink import _parse_args, shrink
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


@pytest.mark.parametrize(
    "args",
    [
        ["extreme"],
        ["lossless", "max"],
        ["max", "dpi=0"],
        ["max", "quality=101"],
        ["max", "quality=x"],
        ["bogus=1"],
        ["max", "max_stream_size=lots"],
    ],
)
def test_parse_rejects_bad_args(args):
    with pytest.raises(InvalidArgumentError):
        _parse_args(args)


@pytest.mark.parametrize(
    "args",
    [
        ["dpi=150"],
        ["lossless", "quality=80"],
        ["mono_dpi=300"],
        ["max_stream_size=2MB"],
        ["balanced", "max_stream_size=2MB"],
    ],
)
def test_parse_rejects_parameters_the_level_does_not_use(args):
    with pytest.raises(InvalidArgumentError, match="has no effect"):
        _parse_args(args)


def test_parse_keeps_max_stream_size_for_simplify_vectors():
    assert _parse_args(["max", "max_stream_size=2MB"]).max_stream_size == "2MB"


@pytest.mark.parametrize(
    "args,expected",
    [(["balanced"], 300), (["max"], 300), (["max", "mono_dpi=600"], 600), ([], None)],
)
def test_parse_mono_dpi(args, expected):
    assert _parse_args(args).mono_dpi == expected


def test_parse_rejects_bad_mono_dpi():
    with pytest.raises(InvalidArgumentError):
        _parse_args(["max", "mono_dpi=0"])


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
def test_failing_pass_is_reported_and_others_still_run(monkeypatch, exc):
    def boom(pdf, args):
        raise exc

    monkeypatch.setattr("pdftl.operations.subset_fonts.subset_fonts", boom)
    pdf = _pdf_with_duplicate_images()
    result = shrink(pdf, [], "out.pdf")
    assert "failed subset_fonts" in result.summary
    assert "deduplicate_images" in result.summary.split("; failed")[0]
    xobjects = pdf.pages[0].Resources.XObject
    assert xobjects["/Im0"].objgen == xobjects["/Im1"].objgen


@pytest.fixture
def calls(monkeypatch):
    """Replace every step pass with a recorder of (function name, args)."""
    recorded = []

    def recorder(name):
        return lambda pdf, args, *rest: recorded.append((name, list(args)))

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
    return recorded


def test_levels_run_expected_passes(calls):
    shrink(_pdf_with_duplicate_images(), [], "out.pdf")
    assert [n for n, _ in calls] == [
        "subset_fonts",
        "deduplicate_fonts",
        "deduplicate_images",
        "deduplicate_icc_profiles",
    ]

    calls.clear()
    shrink(_pdf_with_duplicate_images(), ["balanced"], "out.pdf")
    assert calls[0] == (
        "resample_images",
        ["dpi=200", "mono_dpi=300", "threshold=1.5", "quality=85"],
    )
    assert calls[1] == ("optimize_images_pdf", ["low", "jpeg_quality=85"])
    assert "simplify_vectors_in_content_streams" not in [n for n, _ in calls]

    calls.clear()
    shrink(_pdf_with_duplicate_images(), ["max"], "out.pdf")
    assert calls[1] == ("optimize_images_pdf", ["medium", "jpeg_quality=75"])
    assert ("simplify_vectors_in_content_streams", []) in calls

    calls.clear()
    shrink(_pdf_with_duplicate_images(), ["max", "max_stream_size=2MB"], "out.pdf")
    assert ("simplify_vectors_in_content_streams", ["(max_stream_size=2MB)"]) in calls


def test_max_stream_size_reaches_simplify_vectors(caplog):
    # Real pass-through: simplify_vectors itself skips the over-size stream.
    pdf = _pdf_with_duplicate_images()
    before = pdf.pages[0].Contents.read_bytes()
    shrink(pdf, ["max", "max_stream_size=10"], "out.pdf")
    assert pdf.pages[0].Contents.read_bytes() == before
    assert "over max_stream_size" in caplog.text


# --- skip= and include= ---


def _names(calls):
    return [n for n, _ in calls]


def test_skip_leaves_out_a_step_pass(calls):
    shrink(_pdf_with_duplicate_images(), ["max", "skip=simplify_vectors"], "out.pdf")
    assert "simplify_vectors_in_content_streams" not in _names(calls)
    assert "resample_images" in _names(calls)


def test_skip_leaves_out_save_passes():
    pdf = _pdf_with_duplicate_images()
    shrink(pdf, ["max", "skip=drop_meta,drop_vendor_extensions"], "out.pdf")
    hints = getattr(pdf, c.PDFTL_SAVE_HINTS_ATTR)
    assert set(hints) == {"prune_resources", "recompress"}


def test_skip_recompress_keeps_original_encoding(tmp_path):
    pdf = _pdf_with_duplicate_images()
    raw = pdf.pages[0].Contents.read_raw_bytes()  # compressed at level 1
    shrink(pdf, ["skip=recompress"], str(tmp_path / "out.pdf"))
    with _save(pdf, tmp_path / "out.pdf") as out:
        assert out.pages[0].Contents.read_raw_bytes() == raw


def test_include_adds_simplify_vectors_in_pass_order(calls):
    shrink(
        _pdf_with_duplicate_images(),
        ["include=simplify_vectors", "tolerance=0.3", "max_stream_size=2MB"],
        "out.pdf",
    )
    assert calls[0] == (
        "simplify_vectors_in_content_streams",
        ["(tolerance=0.3,max_stream_size=2MB)"],
    )
    assert _names(calls)[1:] == [
        "subset_fonts",
        "deduplicate_fonts",
        "deduplicate_images",
        "deduplicate_icc_profiles",
    ]


def test_include_image_pass_in_lossless_uses_balanced_settings(calls):
    shrink(_pdf_with_duplicate_images(), ["include=resample_images"], "out.pdf")
    assert calls[0] == (
        "resample_images",
        ["dpi=200", "mono_dpi=300", "threshold=1.5", "quality=85"],
    )
    assert "optimize_images_pdf" not in _names(calls)


def test_include_save_pass():
    pdf = _pdf_with_duplicate_images()
    shrink(pdf, ["max", "include=drop_xmp_streams"], "out.pdf")
    assert getattr(pdf, c.PDFTL_SAVE_HINTS_ATTR)["drop_xmp_streams"] is True


def test_quality_reaches_optimize_images_without_resample(calls):
    shrink(
        _pdf_with_duplicate_images(),
        ["balanced", "skip=resample_images", "quality=70"],
        "out.pdf",
    )
    assert "resample_images" not in _names(calls)
    assert calls[0] == ("optimize_images_pdf", ["low", "jpeg_quality=70"])


@pytest.mark.parametrize("raw,shown", [("1.2", "threshold=1.2"), ("1", "threshold=1.0")])
def test_threshold_reaches_resample_images(calls, raw, shown):
    shrink(_pdf_with_duplicate_images(), ["max", f"threshold={raw}"], "out.pdf")
    assert calls[0][1][2] == shown


@pytest.mark.parametrize(
    "args,match",
    [
        (["skip=bogus"], "unknown: bogus"),
        (["include=simplify_vectors,nope"], "unknown: nope"),
        (["skip=,"], "takes pass names"),
        (["max", "skip=drop_meta", "include=drop_meta"], "in both"),
        (["skip=simplify_vectors"], "'lossless' does not run it"),
        (["max", "include=simplify_vectors"], "'max' runs it"),
        (["balanced", "tolerance=0.3"], "without simplify_vectors"),
        (["max", "skip=simplify_vectors", "max_stream_size=2MB"], "without simplify_vectors"),
        (["max", "skip=resample_images", "dpi=100"], "without resample_images"),
        (["max", "skip=resample_images,optimize_images", "quality=60"], "without"),
        (["max", "threshold=0.9"], "at least 1"),
        (["max", "threshold=x"], "at least 1"),
        (["max", "tolerance=0"], "above 0"),
        (["max", "tolerance=-1"], "above 0"),
        (["max", "tolerance=x"], "above 0"),
    ],
)
def test_parse_rejects_bad_pass_selection(args, match):
    with pytest.raises(InvalidArgumentError, match=match):
        _parse_args(args)


def test_parse_skip_all_image_passes_leaves_no_image_target():
    plan = _parse_args(["max", "skip=resample_images,optimize_images"])
    assert (plan.dpi, plan.quality, plan.threshold) == (None, None, None)
