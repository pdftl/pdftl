# tests/operations/test_shrink.py

import logging
import zlib
from unittest.mock import MagicMock

import pikepdf
import pytest
from pikepdf import Name

import pdftl.core.constants as c
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.shrink import LEVEL_PASSES, _parse_args, shrink
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
        (["strong"], (150, 75)),
        (["strong", "dpi=120", "quality=60"], (120, 60)),
        (["extreme"], (150, 60)),
        (["extreme", "dpi=100", "quality=90"], (100, 90)),
    ],
)
def test_parse_level_image_targets(args, expected):
    plan = _parse_args(args)
    assert (plan.dpi, plan.quality) == expected


@pytest.mark.parametrize(
    "args",
    [
        ["max"],  # renamed to 'strong'
        ["lossless", "strong"],
        ["strong", "dpi=0"],
        ["strong", "quality=101"],
        ["strong", "quality=x"],
        ["bogus=1"],
        ["strong", "max_stream_size=lots"],
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
def test_parameters_the_level_does_not_use_are_ignored_with_a_warning(args, caplog):
    with caplog.at_level(logging.WARNING):
        plan = _parse_args(args)
    assert "has no effect" in caplog.text
    assert plan.passes  # still a usable plan


def test_parse_keeps_max_stream_size_for_simplify_vectors():
    assert _parse_args(["strong", "max_stream_size=2MB"]).max_stream_size == "2MB"


@pytest.mark.parametrize(
    "args,expected",
    [(["balanced"], 300), (["strong"], 300), (["strong", "mono_dpi=600"], 600), ([], None)],
)
def test_parse_mono_dpi(args, expected):
    assert _parse_args(args).mono_dpi == expected


def test_parse_rejects_bad_mono_dpi():
    with pytest.raises(InvalidArgumentError):
        _parse_args(["strong", "mono_dpi=0"])


# --- end to end ---


def test_lossless_merges_duplicates_and_recompresses(tmp_path):
    pdf = _pdf_with_duplicate_images()
    shrink(pdf, ["skip=compact_content"], str(tmp_path / "out.pdf"))
    with _save(pdf, tmp_path / "out.pdf") as out:
        xobjects = out.pages[0].Resources.XObject
        assert xobjects["/Im0"].objgen == xobjects["/Im1"].objgen
        contents = out.pages[0].Contents
        assert contents.read_bytes() == CONTENT
        assert len(contents.read_raw_bytes()) == len(zlib.compress(CONTENT, 9))


XMP = (
    b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
    b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">'
    b"<dc:format>application/pdf</dc:format></rdf:Description></rdf:RDF></x:xmpmeta>"
    + b" " * 2000
    + b'<?xpacket end="w"?>'
)


def _pdf_with_duplicate_forms_and_xmp():
    pdf = pikepdf.new()
    for _ in range(2):
        form = pdf.make_stream(b"0 0 1 rg 0 0 9 9 re f", Type=Name.XObject, Subtype=Name.Form)
        form.BBox, form.Resources = [0, 0, 10, 10], pikepdf.Dictionary()
        page = pdf.add_blank_page(page_size=(100, 100))
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm0=form))
        page.Contents = pdf.make_stream(b"/Fm0 Do")
    pdf.Root.Metadata = pdf.make_stream(XMP, Type=Name.Metadata, Subtype=Name.XML)
    return pdf


@pytest.mark.parametrize("skip", [[], ["skip=deduplicate_xobjects,compress_xmp"]])
def test_lossless_merges_drawn_forms_and_compresses_xmp(tmp_path, skip):
    pdf = _pdf_with_duplicate_forms_and_xmp()
    shrink(pdf, skip, str(tmp_path / "out.pdf"))
    with _save(pdf, tmp_path / "out.pdf") as out:
        forms = {page.Resources.XObject.Fm0.objgen for page in out.pages}
        assert len(forms) == (2 if skip else 1)
        assert ("/Filter" in out.Root.Metadata) == (not skip)


def test_explicit_output_options_override_shrink_hints(tmp_path):
    # An explicit `uncompress` beats shrink's recompress hint.
    pdf = _pdf_with_duplicate_images()
    shrink(pdf, ["skip=compact_content"], str(tmp_path / "out.pdf"))
    with _save(pdf, tmp_path / "out.pdf", {"uncompress": True}) as out:
        assert out.pages[0].Contents.read_raw_bytes() == CONTENT


def test_level_hints_left_for_save():
    pdf = _pdf_with_duplicate_images()
    shrink(pdf, ["strong"], "out.pdf")
    hints = getattr(pdf, c.PDFTL_SAVE_HINTS_ATTR)
    assert hints["recompress"] and hints["prune_resources"] and hints["drop_meta"]
    assert hints["drop_xfa"] == "hybrid"


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
        return lambda pdf, args, *rest, **kw: recorded.append((name, list(args)))

    for mod, fn in [
        ("mrc_compress", "mrc_compress"),
        ("resample_images", "resample_images"),
        ("photos_to_jpeg", "photos_to_jpeg"),
        ("optimize_images", "optimize_images_pdf"),
        ("simplify_vectors", "simplify_vectors_in_content_streams"),
        ("round_text_positions", "round_text_positions_op"),
        ("compact_content", "compact_content"),
        ("subset_fonts", "subset_fonts"),
        ("deduplicate_fonts", "deduplicate_fonts"),
        ("merge_font_subsets", "merge_font_subsets_op"),
        ("deduplicate_images", "deduplicate_images"),
        ("deduplicate_icc_profiles", "deduplicate_icc_profiles"),
        ("deduplicate_xobjects", "deduplicate_xobjects"),
    ]:
        monkeypatch.setattr(f"pdftl.operations.{mod}.{fn}", recorder(fn))
    return recorded


def test_levels_run_expected_passes(calls):
    shrink(_pdf_with_duplicate_images(), [], "out.pdf")
    assert [n for n, _ in calls] == [
        "optimize_images_pdf",
        "compact_content",
        "subset_fonts",
        "deduplicate_fonts",
        "merge_font_subsets_op",
        "deduplicate_images",
        "deduplicate_icc_profiles",
        "deduplicate_xobjects",
    ]

    calls.clear()
    shrink(_pdf_with_duplicate_images(), ["balanced"], "out.pdf")
    assert calls[0] == ("mrc_compress", [])
    assert calls[1] == (
        "resample_images",
        ["dpi=200", "mono_dpi=300", "threshold=1.5", "quality=85"],
    )
    assert calls[2] == ("photos_to_jpeg", ["quality=85"])
    assert calls[3] == ("optimize_images_pdf", ["low"])  # lossless: no JPEG quality
    assert "simplify_vectors_in_content_streams" not in [n for n, _ in calls]

    calls.clear()
    shrink(_pdf_with_duplicate_images(), ["strong"], "out.pdf")
    assert calls[3] == ("optimize_images_pdf", ["medium", "jpeg_quality=75"])
    assert ("simplify_vectors_in_content_streams", []) in calls

    calls.clear()
    shrink(_pdf_with_duplicate_images(), ["strong", "max_stream_size=2MB"], "out.pdf")
    assert ("simplify_vectors_in_content_streams", ["(max_stream_size=2MB)"]) in calls

    calls.clear()
    shrink(_pdf_with_duplicate_images(), ["extreme"], "out.pdf")
    assert calls[0] == ("mrc_compress", ["bg_div=4", "bg_quality=50", "fg_quality=30"])
    assert calls[1] == (
        "resample_images",
        ["dpi=150", "mono_dpi=300", "threshold=1.5", "quality=60"],
    )
    assert calls[2] == ("photos_to_jpeg", ["quality=60", "max_ratio=0.8"])
    assert calls[3] == ("optimize_images_pdf", ["high", "jpeg_quality=60"])
    assert ("simplify_vectors_in_content_streams", []) in calls


def test_max_stream_size_reaches_simplify_vectors(caplog):
    # Real pass-through: simplify_vectors itself skips the over-size stream.
    pdf = _pdf_with_duplicate_images()
    before = pdf.pages[0].Contents.read_bytes()
    shrink(pdf, ["strong", "max_stream_size=10", "skip=compact_content"], "out.pdf")
    assert pdf.pages[0].Contents.read_bytes() == before
    assert "over max_stream_size" in caplog.text


# --- skip= and include= ---


def _names(calls):
    return [n for n, _ in calls]


def test_skip_leaves_out_a_step_pass(calls):
    shrink(_pdf_with_duplicate_images(), ["strong", "skip=simplify_vectors"], "out.pdf")
    assert "simplify_vectors_in_content_streams" not in _names(calls)
    assert "resample_images" in _names(calls)


def test_skip_leaves_out_save_passes():
    pdf = _pdf_with_duplicate_images()
    shrink(pdf, ["strong", "skip=drop_meta,drop_vendor_extensions"], "out.pdf")
    hints = getattr(pdf, c.PDFTL_SAVE_HINTS_ATTR)
    assert set(hints) == {"prune_resources", "recompress", "compress_xmp", "drop_xfa"}


def test_skip_recompress_keeps_original_encoding(tmp_path):
    pdf = _pdf_with_duplicate_images()
    raw = pdf.pages[0].Contents.read_raw_bytes()  # compressed at level 1
    shrink(pdf, ["skip=recompress,compact_content"], str(tmp_path / "out.pdf"))
    with _save(pdf, tmp_path / "out.pdf") as out:
        assert out.pages[0].Contents.read_raw_bytes() == raw


def test_include_adds_simplify_vectors_in_pass_order(calls):
    shrink(
        _pdf_with_duplicate_images(),
        ["include=simplify_vectors", "tolerance=0.3", "max_stream_size=2MB"],
        "out.pdf",
    )
    assert calls[1] == (
        "simplify_vectors_in_content_streams",
        ["(tolerance=0.3,max_stream_size=2MB)"],
    )
    assert _names(calls)[0] == "optimize_images_pdf"
    assert _names(calls)[2:] == [
        "compact_content",
        "subset_fonts",
        "deduplicate_fonts",
        "merge_font_subsets_op",
        "deduplicate_images",
        "deduplicate_icc_profiles",
        "deduplicate_xobjects",
    ]


def test_include_image_pass_in_lossless_uses_balanced_settings(calls):
    shrink(_pdf_with_duplicate_images(), ["include=resample_images"], "out.pdf")
    assert calls[0] == (
        "resample_images",
        ["dpi=200", "mono_dpi=300", "threshold=1.5", "quality=85"],
    )
    assert ("optimize_images_pdf", ["low"]) in calls  # lossless's own optimisation


def test_include_save_pass():
    pdf = _pdf_with_duplicate_images()
    shrink(pdf, ["strong", "include=drop_xmp_streams"], "out.pdf")
    assert getattr(pdf, c.PDFTL_SAVE_HINTS_ATTR)["drop_xmp_streams"] is True


def test_quality_reaches_lossy_optimize_images_without_resample(calls):
    shrink(
        _pdf_with_duplicate_images(),
        ["strong", "skip=resample_images", "quality=70"],
        "out.pdf",
    )
    assert "resample_images" not in _names(calls)
    assert ("optimize_images_pdf", ["medium", "jpeg_quality=70"]) in calls


@pytest.mark.parametrize(
    "args,quality",
    [
        (["balanced"], 85),
        (["strong"], 75),
        (["strong", "quality=60"], 60),
        (["extreme"], 60),
        (["include=photos_to_jpeg"], 85),
    ],
)
def test_photos_to_jpeg_gets_the_level_quality(calls, args, quality):
    shrink(_pdf_with_duplicate_images(), args, "out.pdf")
    ratio = ["max_ratio=0.8"] if {"strong", "extreme"} & set(args) else []
    assert ("photos_to_jpeg", [f"quality={quality}", *ratio]) in calls
    names = _names(calls)
    if "resample_images" in names:
        assert names.index("resample_images") < names.index("photos_to_jpeg")
    assert names.index("photos_to_jpeg") < names.index("optimize_images_pdf")


def test_resample_without_optimize_images(calls):
    shrink(_pdf_with_duplicate_images(), ["strong", "skip=optimize_images"], "out.pdf")
    assert (
        "resample_images",
        ["dpi=150", "mono_dpi=300", "threshold=1.5", "quality=75"],
    ) in calls
    assert "optimize_images_pdf" not in _names(calls)


@pytest.mark.parametrize(
    "args",
    [["quality=70"], ["balanced", "skip=resample_images,photos_to_jpeg", "quality=70"]],
)
def test_quality_warns_when_images_are_only_reencoded_losslessly(calls, caplog, args):
    with caplog.at_level(logging.WARNING):
        shrink(_pdf_with_duplicate_images(), args, "out.pdf")
    assert "'quality' has no effect" in caplog.text
    assert ("optimize_images_pdf", ["low"]) in calls


@pytest.mark.parametrize("raw,shown", [("1.2", "threshold=1.2"), ("1", "threshold=1.0")])
def test_threshold_reaches_resample_images(calls, raw, shown):
    shrink(_pdf_with_duplicate_images(), ["strong", f"threshold={raw}"], "out.pdf")
    assert calls[1][1][2] == shown


@pytest.mark.parametrize(
    "args,warning,passes_added,passes_removed",
    [
        (["skip=simplify_vectors"], "'lossless' does not run it", set(), set()),
        (["strong", "include=simplify_vectors"], "'strong' runs it", set(), set()),
        (["balanced", "tolerance=0.3"], "without simplify_vectors", set(), set()),
        (
            ["strong", "skip=resample_images", "dpi=100"],
            "without resample_images",
            set(),
            {"resample_images"},
        ),
        (["text_tolerance=0.01"], "without round_text_positions", set(), set()),
    ],
)
def test_ineffective_pass_selection_warns_and_carries_on(
    caplog, args, warning, passes_added, passes_removed
):
    level = next((a for a in args if "=" not in a), "lossless")
    with caplog.at_level(logging.WARNING):
        plan = _parse_args(args)
    assert warning in caplog.text
    assert plan.passes == (LEVEL_PASSES[level] | passes_added) - passes_removed


@pytest.mark.parametrize(
    "args,match",
    [
        (["skip=bogus"], "unknown: bogus"),
        (["include=simplify_vectors,nope"], "unknown: nope"),
        (["skip=,"], "takes pass names"),
        (["strong", "skip=drop_meta", "include=drop_meta"], "in both"),
        (["strong", "threshold=0.9"], "at least 1"),
        (["strong", "threshold=x"], "at least 1"),
        (["strong", "tolerance=0"], "above 0"),
        (["strong", "tolerance=-1"], "above 0"),
        (["strong", "tolerance=x"], "above 0"),
    ],
)
def test_parse_rejects_bad_pass_selection(args, match):
    with pytest.raises(InvalidArgumentError, match=match):
        _parse_args(args)


def test_parse_skip_all_image_passes_leaves_no_image_target():
    plan = _parse_args(["strong", "skip=resample_images,photos_to_jpeg,optimize_images"])
    assert (plan.dpi, plan.quality, plan.threshold) == (None, None, None)


# --- round_text_positions ---


@pytest.mark.parametrize(
    "args,expected",
    [
        (["balanced"], ["tolerance=0.005"]),
        (["strong"], ["tolerance=0.02"]),
        (["strong", "text_tolerance=0.001"], ["tolerance=0.001"]),
        (["extreme"], ["tolerance=0.02"]),
        (["include=round_text_positions"], ["tolerance=0.005"]),
    ],
)
def test_round_text_positions_tolerance_per_level(calls, args, expected):
    shrink(_pdf_with_duplicate_images(), args, "out.pdf")
    assert ("round_text_positions_op", expected) in calls


def test_round_text_positions_not_in_lossless(calls):
    shrink(_pdf_with_duplicate_images(), [], "out.pdf")
    assert "round_text_positions_op" not in _names(calls)


def test_round_text_positions_can_be_skipped(calls):
    shrink(_pdf_with_duplicate_images(), ["strong", "skip=round_text_positions"], "out.pdf")
    assert "round_text_positions_op" not in _names(calls)


@pytest.mark.parametrize(
    "args,match",
    [
        (["strong", "text_tolerance=0"], "above 0"),
        (["strong", "text_tolerance=x"], "above 0"),
    ],
)
def test_parse_rejects_bad_text_tolerance(args, match):
    with pytest.raises(InvalidArgumentError, match=match):
        _parse_args(args)


# --- mrc_compress ---


@pytest.mark.parametrize(
    "args,expected",
    [
        (["balanced"], []),
        (["strong"], ["bg_div=4", "bg_quality=60", "fg_quality=40"]),
        (["extreme"], ["bg_div=4", "bg_quality=50", "fg_quality=30"]),
        (["include=mrc_compress"], []),
    ],
)
def test_mrc_compress_runs_first_with_level_settings(calls, args, expected):
    shrink(_pdf_with_duplicate_images(), args, "out.pdf")
    assert calls[0] == ("mrc_compress", expected)


def test_mrc_compress_not_in_lossless_and_can_be_skipped(calls):
    shrink(_pdf_with_duplicate_images(), [], "out.pdf")
    shrink(_pdf_with_duplicate_images(), ["strong", "skip=mrc_compress"], "out.pdf")
    assert "mrc_compress" not in _names(calls)


@pytest.mark.parametrize("level", ["balanced", "strong", "extreme"])
def test_scanned_page_is_mrc_layered_and_still_looks_the_same(tmp_path, level):
    import io

    import numpy as np

    from pdftl.utils.page_images import render_page_to_pil
    from tests.operations.test_mrc_compress import _add_scan_page

    pdf = pikepdf.new()
    _add_scan_page(pdf)
    before = io.BytesIO()
    pdf.save(before)
    original = np.asarray(render_page_to_pil(pikepdf.open(before), 0, dpi=72).convert("L"), float)

    result = shrink(pdf, [level], str(tmp_path / "out.pdf"))
    assert "mrc_compress" in result.summary.split(";")[0]
    with _save(pdf, tmp_path / "out.pdf") as out:
        images = [x for x in out.pages[0].Resources.XObject.values() if "/Mask" in x]
        assert len(images) == 1 and images[0].Mask.ImageMask
        after = np.asarray(render_page_to_pil(out, 0, dpi=72).convert("L"), float)
        assert (tmp_path / "out.pdf").stat().st_size < len(before.getvalue())
    assert np.abs(after - original).mean() < 4  # gray levels, whole page


# --- optional extras and external encoders ---


def test_pass_without_its_extra_is_skipped_not_failed(monkeypatch, caplog):
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a: None if name == "ocrmypdf" else real(name, *a),
    )
    with caplog.at_level(logging.INFO):
        result = shrink(_pdf_with_duplicate_images(), [], "out.pdf")
    assert "not installed optimize_images" in result.summary
    assert "failed" not in result.summary
    assert "skipping optimize_images: needs ocrmypdf" in caplog.text


def test_photos_to_jpeg_is_skipped_without_numpy(monkeypatch, caplog):
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name, *a: None if name == "numpy" else real(name, *a)
    )
    with caplog.at_level(logging.INFO):
        result = shrink(_pdf_with_duplicate_images(), ["balanced"], "out.pdf")
    assert "photos_to_jpeg" in result.summary.split("not installed", 1)[1]
    assert "skipping photos_to_jpeg: needs numpy" in caplog.text


def _bitonal_pdf(path):
    import numpy as np

    ink = np.zeros((400, 800), dtype=bool)  # scan-like: strokes in lines, 1% noise
    for y in range(20, 380, 24):
        for x in range(20, 780, 14):
            ink[y : y + 12, x : x + 3 + (x * 7 + y) % 6] = True
    ink ^= np.random.default_rng(1).random(ink.shape) < 0.01
    bits = np.packbits(ink, axis=1)  # 1 = black (with /Decode [1 0])
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    img = pdf.make_stream(zlib.compress(bits.tobytes()))
    img.Type, img.Subtype = Name.XObject, Name.Image
    img.Width, img.Height, img.BitsPerComponent = 800, 400, 1
    img.ColorSpace, img.Filter = Name.DeviceGray, Name.FlateDecode
    img.Decode = [1, 0]
    pdf.pages[0].Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img))
    pdf.pages[0].Contents = pdf.make_stream(b"q 612 0 0 306 0 0 cm /Im0 Do Q")
    pdf.save(path)


def _shrink_cli(src, out, path_env):
    import os
    import subprocess
    import sys

    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=str(root / "src"), PATH=path_env)
    subprocess.run(
        [sys.executable, "-m", "pdftl", str(src), "shrink", "output", str(out)],
        check=True,
        env=env,
    )


def _pixels(path):
    import numpy as np

    with pikepdf.open(path) as pdf:
        x = pdf.pages[0].Resources.XObject.Im0
        return str(x.get("/Filter")), np.asarray(pikepdf.PdfImage(x).as_pil_image().convert("L"))


@pytest.mark.parametrize("with_jbig2", [False, True])
def test_lossless_bitonal_images_with_and_without_a_jbig2_encoder(tmp_path, with_jbig2):
    import os
    import shutil

    import numpy as np

    pytest.importorskip("ocrmypdf")
    jbig2 = shutil.which("jbig2")
    if with_jbig2 and not jbig2:
        pytest.skip("no jbig2 encoder installed")
    dirs = os.environ["PATH"].split(os.pathsep)
    if not with_jbig2:
        dirs = [d for d in dirs if not (jbig2 and os.path.dirname(jbig2) == d)]
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _bitonal_pdf(src)
    _shrink_cli(src, out, os.pathsep.join(dirs))
    before, after = _pixels(src), _pixels(out)
    assert np.array_equal(before[1], after[1])  # lossless either way
    assert (after[0] == "/JBIG2Decode") is with_jbig2


# --- deflate ---


def test_deflate_zopfli_is_handed_to_the_save(monkeypatch):
    from pdftl.output import recompress as rc

    monkeypatch.setattr(rc, "_zopfli", lambda: object())
    pdf = _pdf_with_duplicate_images()
    shrink(pdf, ["deflate=zopfli"], "out.pdf")
    assert getattr(pdf, c.PDFTL_SAVE_HINTS_ATTR)["deflate"] == "zopfli"


def test_deflate_zopfli_fails_before_any_pass_without_zopfli(monkeypatch):
    from pdftl.exceptions import PackageError
    from pdftl.output import recompress as rc

    monkeypatch.setattr(rc, "_zopfli", lambda: None)
    with pytest.raises(PackageError):
        _parse_args(["deflate=zopfli"])
    assert _parse_args(["deflate=zlib"]).deflate == "zlib"


def test_deflate_without_recompress_is_ignored(monkeypatch, caplog):
    from pdftl.output import recompress as rc

    monkeypatch.setattr(rc, "_zopfli", lambda: None)
    with caplog.at_level(logging.WARNING):
        plan = _parse_args(["skip=recompress", "deflate=zopfli"])
    assert plan.deflate is None
    assert "'deflate' has no effect" in caplog.text
    with pytest.raises(InvalidArgumentError, match="zlib, zopfli"):
        _parse_args(["skip=recompress", "deflate=lzma"])


@pytest.mark.parametrize(
    "level,keep_faithful",
    [("lossless", True), ("balanced", True), ("strong", True), ("extreme", False)],
)
def test_shrink_asks_optimize_images_to_keep_only_faithful_images(
    monkeypatch, level, keep_faithful
):
    seen = []
    monkeypatch.setattr(
        "pdftl.operations.optimize_images.optimize_images_pdf",
        lambda pdf, args, output, **kw: seen.append(kw),
    )
    shrink(_pdf_with_duplicate_images(), [level], "out.pdf")
    assert seen == [{"keep_faithful": keep_faithful}]


# --- extreme: fidelity guards off, safety guards kept ---


def _photo_image():
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(11)
    coarse = rng.integers(0, 256, (6, 8, 3), dtype=np.uint8)
    smooth = np.asarray(Image.fromarray(coarse).resize((320, 240), Image.BICUBIC), dtype=np.int16)
    noisy = np.clip(smooth + rng.normal(0, 1.3, smooth.shape), 0, 255).astype(np.uint8)
    return Image.fromarray(noisy, "RGB")


def _pdf_with_photo(pil):
    pdf = pikepdf.new()
    xobj = pdf.make_stream(
        zlib.compress(pil.tobytes(), 9),
        Type=Name.XObject,
        Subtype=Name.Image,
        Width=pil.width,
        Height=pil.height,
        BitsPerComponent=8,
        ColorSpace=Name.DeviceRGB,
        Filter=Name.FlateDecode,
    )
    page = pdf.add_blank_page(page_size=(pil.width, pil.height))
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=xobj))
    page.Contents = pdf.make_stream(f"q {pil.width} 0 0 {pil.height} 0 0 cm /Im0 Do Q".encode())
    return pdf


def _decoded_psnr(pil, raw_bytes: bytes) -> float:
    import io

    import numpy as np
    from PIL import Image

    a = np.asarray(pil, dtype=np.float64)
    b = np.asarray(Image.open(io.BytesIO(raw_bytes)).convert("RGB"), dtype=np.float64)
    mse = ((a - b) ** 2).mean()
    return float("inf") if mse == 0 else float(10 * np.log10(255**2 / mse))


@pytest.mark.parametrize("level,faithful", [("strong", True), ("extreme", False)])
def test_extreme_keeps_a_photo_reencode_the_psnr_gate_would_reject(level, faithful):
    photo = _photo_image()
    pdf = _pdf_with_photo(photo)
    shrink(
        pdf,
        [level, "skip=mrc_compress,resample_images,optimize_images", "quality=1"],
        "out.pdf",
    )
    xobj = pdf.pages[0].Resources.XObject.Im0
    assert str(xobj.Filter) == "/DCTDecode"  # the re-encode happened either way
    psnr = _decoded_psnr(photo, xobj.read_raw_bytes())
    assert (psnr >= 30) is faithful  # ground truth: independently decoded PSNR


@pytest.mark.parametrize("level", ["strong", "extreme"])
def test_every_level_keeps_the_mrc_edge_loss_guard(monkeypatch, tmp_path, level):
    from pdftl.utils.mrc import fidelity
    from tests.operations.test_mrc_compress import _add_scan_page

    monkeypatch.setattr(fidelity, "edge_loss", lambda *args: 0.57)  # above MAX_EDGE_LOSS
    pdf = pikepdf.new()
    _add_scan_page(pdf)
    shrink(
        pdf,
        [level, "skip=optimize_images,resample_images,photos_to_jpeg"],
        str(tmp_path / "out.pdf"),
    )
    with _save(pdf, tmp_path / "out.pdf") as out:
        masked = [x for x in out.pages[0].Resources.XObject.values() if "/Mask" in x]
    assert not masked


def test_extreme_still_discards_layers_that_are_not_smaller(tmp_path):
    # Safety guard ("keep smaller") holds even with the fidelity gate off: a
    # page whose MRC layers would not shrink it stays untouched.
    from PIL import Image

    from tests.operations.test_mrc_compress import IMG_H, IMG_W, _add_image_page

    pdf = pikepdf.new()
    white = Image.new("L", (IMG_W, IMG_H), 255)
    _add_image_page(pdf, zlib.compress(white.tobytes(), 9), "/DeviceGray", "/FlateDecode")
    shrink(
        pdf,
        ["extreme", "skip=resample_images,photos_to_jpeg,optimize_images"],
        str(tmp_path / "out.pdf"),
    )
    xobjects = pdf.pages[0].Resources.XObject
    assert list(xobjects.keys()) == ["/Im0"]
    assert xobjects.Im0.read_bytes() == white.tobytes()


# --- target= and plan overrides ---


def test_target_hands_off_to_the_target_search(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "pdftl.operations.helpers.shrink_target.shrink_to_target",
        lambda pdf, target, out: seen.append((target.size, [r.level for r in target.rungs], out)),
    )
    shrink(_pdf_with_duplicate_images(), ["strong", "target=2KB"], "out.pdf")
    assert seen == [(2048, ["lossless", "balanced", "strong"], "out.pdf")]


@pytest.mark.parametrize("override,expected", [(None, True), (False, False)])
def test_plan_mrc_guard_overrides_the_level(monkeypatch, override, expected):
    from pdftl.operations.shrink import _step_functions

    seen = []
    monkeypatch.setattr(
        "pdftl.operations.mrc_compress.mrc_compress",
        lambda pdf, args, guard_fidelity: seen.append((args, guard_fidelity)),
    )
    plan = _parse_args(["strong"])
    plan.mrc_guard, plan.mrc_args = override, ["bg_div=9"]
    _step_functions(plan, "out.pdf")["mrc_compress"](None)
    assert seen == [(["bg_div=9"], expected)]


# --- drop_xfa: hybrid forms only ---


def _form_pdf(widget=True, needs_rendering=False):
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    field = pdf.make_indirect(
        pikepdf.Dictionary(
            FT=Name.Tx, T=pikepdf.String("f"), Subtype=Name.Widget, Rect=[0, 0, 50, 20]
        )
    )
    if widget:
        page.Annots = pdf.make_indirect(pikepdf.Array([field]))
    xfa = pdf.make_stream(b"<xdp:xdp/>")
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([field]), XFA=xfa)
    if needs_rendering:
        pdf.Root.NeedsRendering = True
    return pdf


@pytest.mark.parametrize(
    "widget,needs_rendering,dropped",
    [(True, False, True), (False, False, False), (True, True, False)],
)
def test_strong_drops_xfa_only_from_hybrid_forms(tmp_path, widget, needs_rendering, dropped):
    pdf = _form_pdf(widget, needs_rendering)
    shrink(pdf, ["strong"], "out.pdf")
    with _save(pdf, tmp_path / "out.pdf") as out:
        assert ("/XFA" not in out.Root.AcroForm) is dropped


def test_explicit_drop_xfa_still_drops_xfa_from_any_form(tmp_path):
    pdf = _form_pdf(widget=False)
    shrink(pdf, ["strong"], "out.pdf")
    with _save(pdf, tmp_path / "out.pdf", {"drop_xfa": True}) as out:
        assert "/XFA" not in out.Root.AcroForm


def _tagged():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(100, 100))
    page.obj.Contents = pdf.make_stream(b"/P <</MCID 0>> BDC 0 0 1 rg 10 10 50 50 re f EMC")
    page.obj.StructParents = 0
    pdf.Root.StructTreeRoot = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot)
    )
    return pdf


@pytest.mark.parametrize(
    "args, deleted",
    [
        (["strong"], False),
        (["extreme"], True),
        (["strong", "include=delete_tags"], True),
        (["extreme", "skip=delete_tags"], False),
    ],
)
def test_only_extreme_deletes_tags_by_default(args, deleted):
    pdf = _tagged()
    shrink(pdf, args, "out.pdf")
    assert ("/StructTreeRoot" not in pdf.Root) is deleted
    assert (b"MCID" not in pdf.pages[0].obj.Contents.read_bytes()) is deleted


def test_only_lossless_keeps_type1_fonts(calls):
    shrink(_pdf_with_duplicate_images(), [], "out.pdf")
    assert ("subset_fonts", ["keep_type1"]) in calls
    calls.clear()
    shrink(_pdf_with_duplicate_images(), ["balanced"], "out.pdf")
    assert ("subset_fonts", []) in calls
