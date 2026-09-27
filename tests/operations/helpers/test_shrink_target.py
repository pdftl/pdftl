# tests/operations/helpers/test_shrink_target.py

import io
import logging
import os
import subprocess
import sys
import zlib
from pathlib import Path

import numpy as np
import pikepdf
import pytest
from pikepdf import Name
from PIL import Image

import pdftl.core.constants as c
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.helpers import shrink_target as st
from pdftl.operations.shrink import LEVEL_PASSES, LEVELS, shrink

ROOT = Path(__file__).resolve().parents[3]


def _photo(seed):
    rng = np.random.default_rng(seed)
    coarse = rng.integers(0, 256, (6, 8, 3), dtype=np.uint8)
    smooth = np.asarray(Image.fromarray(coarse).resize((320, 240), Image.BICUBIC), dtype=np.int16)
    noisy = np.clip(smooth + rng.normal(0, 4, smooth.shape), 0, 255).astype(np.uint8)
    return Image.fromarray(noisy, "RGB")


def _photo_pdf(n=3):
    """n pages, each one Flate-stored photo drawn at 72 dpi."""
    pdf = pikepdf.new()
    photos = []
    for seed in range(n):
        pil = _photo(seed)
        photos.append(pil)
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
        page.Contents = pdf.make_stream(b"q 320 0 0 240 0 0 cm /Im0 Do Q")
    return pdf, photos


def _written(pdf, path):
    """Save as the pipeline would and return the size on disk."""
    from pdftl.output.save import save_pdf

    save_pdf(pdf, str(path), input_context=None)
    return os.path.getsize(path)


def _worst_psnr(path, photos):
    worst = float("inf")
    with pikepdf.open(path) as pdf:
        for page, pil in zip(pdf.pages, photos):
            (xobj,) = page.Resources.XObject.values()
            got = pikepdf.PdfImage(xobj).as_pil_image().convert("RGB").resize(pil.size)
            a = np.asarray(pil, dtype=np.float64)
            b = np.asarray(got, dtype=np.float64)
            mse = ((a - b) ** 2).mean()
            worst = min(worst, float("inf") if mse == 0 else 10 * np.log10(255**2 / mse))
    return worst


@pytest.fixture
def probes(monkeypatch):
    """Record the rung of every probe."""
    seen = []
    real = st._probe

    def spy(source, hints, rung, target, out):
        seen.append(rung)
        return real(source, hints, rung, target, out)

    monkeypatch.setattr(st, "_probe", spy)
    return seen


# --- arguments ---


def test_no_target_is_an_ordinary_shrink():
    assert st.parse_target(["strong", "dpi=100"]) is None


@pytest.mark.parametrize(
    "raw,size,percent",
    [
        ("2M", 2 * 1024 * 1024, None),
        ("1500", 1500, None),
        ("40%", None, 40.0),
        ("100%", None, 100.0),
    ],
)
def test_parse_target_sizes(raw, size, percent):
    target = st.parse_target([f"target={raw}"])
    assert (target.size, target.percent) == (size, percent)


@pytest.mark.parametrize(
    "args,match",
    [
        (["target=0"], "positive"),
        (["target=0%"], "percentage"),
        (["target=101%"], "percentage"),
        (["target=x%"], "percentage"),
        (["target=lots"], "invalid size"),
        (["target=1M", "bogus"], "expected one level"),
        (["target=1M", "strong", "extreme"], "expected one level"),
        (["target=1M", "quality=50"], "leave out quality"),
        (["target=1M", "dpi=100", "tolerance=1"], "leave out dpi, tolerance"),
        (["target=1M", "skip=nonsense"], "unknown: nonsense"),
    ],
)
def test_parse_target_rejects(args, match):
    with pytest.raises(InvalidArgumentError, match=match):
        st.parse_target(args)


def test_a_level_caps_the_ladder():
    def levels(args):
        return [r.level for r in st.parse_target(["target=1M", *args]).rungs]

    assert levels(["lossless"]) == ["lossless"]
    assert levels(["balanced"]) == ["lossless", "balanced"]
    assert levels(["strong"]) == ["lossless", "balanced", "strong"]
    capped = st.parse_target(["target=1M", "extreme"]).rungs
    assert capped[-1] == st.Rung(("extreme",))  # plain extreme, nothing lossier
    assert st.parse_target(["target=1M"]).rungs == st.LADDER


def test_ladder_is_ordered_by_level_and_checks_mrc_until_the_last_rung():
    ranks = [LEVELS.index(r.level) for r in st.LADDER]
    assert ranks == sorted(ranks)
    assert [r.mrc_guard for r in st.LADDER] == [True] * (len(st.LADDER) - 1) + [False]
    assert str(st.LADDER[-1]).endswith("(MRC unchecked)")
    assert str(st.Rung(("strong", "quality=60"))) == "strong quality=60"


def test_pass_selection_only_reaches_rungs_it_changes():
    target = st.parse_target(
        ["target=1M", "skip=simplify_vectors,drop_meta", "include=drop_thumbnails"]
    )
    lossless = st._rung_args(st.Rung(("lossless",)), target)
    assert lossless == ["lossless", "include=drop_thumbnails"]
    balanced = st._rung_args(st.Rung(("balanced",)), target)
    assert balanced == ["balanced"]  # runs drop_thumbnails, not the skipped passes
    deep = st._rung_args(st.LADDER[-1], target)
    assert "tolerance=0.5" not in deep  # simplify_vectors is skipped
    assert "text_tolerance=0.2" in deep
    assert "skip=drop_meta,simplify_vectors" in deep


def test_extra_parameters_reach_only_rungs_that_use_them():
    target = st.parse_target(["target=1M", "max_stream_size=1M", "deflate=zlib"])
    assert st._rung_args(st.Rung(("balanced",)), target) == ["balanced", "deflate=zlib"]
    assert "max_stream_size=1M" in st._rung_args(st.Rung(("strong",)), target)
    for rung in st.LADDER:  # no rung warns about its own arguments
        st._rung_args(rung, target)


# --- search ---


def test_a_target_lossless_meets_is_met_losslessly(tmp_path, probes):
    pdf, photos = _photo_pdf()
    result = shrink(pdf, ["target=10M"], "out.pdf")
    assert [r.level for r in probes] == ["lossless"]
    _written(result.pdf, tmp_path / "out.pdf")
    assert _worst_psnr(tmp_path / "out.pdf", photos) == float("inf")


def test_looser_targets_are_met_with_no_less_fidelity(tmp_path):
    pdf, photos = _photo_pdf()
    source = io.BytesIO()
    pdf.save(source)
    lossless = _written(shrink(pikepdf.open(source), ["lossless"], "x").pdf, tmp_path / "l.pdf")
    results = []
    for share in (0.5, 0.2, 0.08):
        goal = int(lossless * share)
        result = shrink(pikepdf.open(io.BytesIO(source.getvalue())), [f"target={goal}"], "x")
        path = tmp_path / f"{share}.pdf"
        assert _written(result.pdf, path) <= goal  # measured on disk
        results.append(_worst_psnr(path, photos))
    assert results == sorted(results, reverse=True)
    assert results[-1] < float("inf")


def test_percentage_target_is_of_the_input_file(tmp_path):
    pdf, _ = _photo_pdf()
    pdf.save(tmp_path / "in.pdf")
    size = os.path.getsize(tmp_path / "in.pdf")
    with pikepdf.open(tmp_path / "in.pdf") as src:
        result = shrink(src, ["target=30%"], "x")
        assert _written(result.pdf, tmp_path / "out.pdf") <= size * 0.3


def test_percentage_target_of_an_unsaved_document(tmp_path):
    pdf, _ = _photo_pdf()
    assert not os.path.exists(pdf.filename)
    in_memory = io.BytesIO()
    pdf.save(in_memory)
    result = shrink(pdf, ["target=30%"], "x")
    assert _written(result.pdf, tmp_path / "out.pdf") <= in_memory.tell() * 0.3


def test_unreachable_target_gives_the_smallest_output_and_warns(tmp_path, caplog, probes):
    pdf, photos = _photo_pdf()
    lossless = _written(shrink(_photo_pdf()[0], ["lossless"], "x").pdf, tmp_path / "l.pdf")
    with caplog.at_level(logging.WARNING):
        result = shrink(pdf, ["target=100"], "x")
    assert "shrink: cannot reach 100 B" in caplog.text
    assert "Rasterising" not in caplog.text
    assert probes[-1] == st.LADDER[-1]  # bisection ended on the bottom rung
    assert _written(result.pdf, tmp_path / "out.pdf") < lossless / 5


def test_capped_search_never_probes_past_its_level(caplog, probes):
    with caplog.at_level(logging.WARNING):
        shrink(_photo_pdf()[0], ["balanced", "target=100"], "x")
    assert {r.level for r in probes} == {"lossless", "balanced"}
    assert "cannot reach" in caplog.text


def test_lossless_cap_probes_once(caplog, probes):
    with caplog.at_level(logging.WARNING):
        shrink(_photo_pdf()[0], ["lossless", "target=100"], "x")
    assert probes == [st.Rung(("lossless",))]
    assert "cannot reach" in caplog.text


def _xfa_pdf(nbytes):
    """A page with a hybrid form whose XFA packet is incompressible."""
    pdf, _ = _photo_pdf(1)
    xfa = pdf.make_stream(np.random.default_rng(3).bytes(nbytes))
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array(), XFA=xfa)
    return pdf


def test_target_below_what_no_rung_shrinks_skips_to_the_bottom_rung(caplog, probes):
    with caplog.at_level(logging.WARNING):
        shrink(_xfa_pdf(200_000), ["target=50K"], "x")
    assert probes == [st.LADDER[0], st.LADDER[-1]]
    assert "of it forms, which no setting makes smaller" in caplog.text
    assert "Rasterising the pages" in caplog.text


def test_no_raster_hint_when_what_is_left_would_survive_rendering(caplog):
    pdf, _ = _photo_pdf(1)
    blob = pdf.make_stream(np.random.default_rng(4).bytes(100_000))
    pdf.Root.Names = pikepdf.Dictionary(
        EmbeddedFiles=pikepdf.Dictionary(
            Names=pikepdf.Array(
                [
                    pikepdf.String("a.bin"),
                    pikepdf.Dictionary(Type=Name.Filespec, EF=pikepdf.Dictionary(F=blob)),
                ]
            )
        )
    )
    with caplog.at_level(logging.WARNING):
        shrink(pdf, ["target=20K"], "x")
    assert "of it embedded files" in caplog.text
    assert "Rasterising" not in caplog.text


def test_mrc_is_checked_on_every_rung_but_the_last(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "pdftl.operations.mrc_compress.mrc_compress",
        lambda pdf, args, guard_fidelity: seen.append((tuple(args), guard_fidelity)),
    )
    source = io.BytesIO()
    _photo_pdf(1)[0].save(source)
    for rung in (st.Rung(("balanced",)), st.Rung(("extreme",)), st.LADDER[-1]):
        st._probe(source.getvalue(), {}, rung, st.parse_target(["target=1"]), "x")
    assert seen == [
        ((), True),
        (("bg_div=4", "bg_quality=50", "fg_quality=30"), True),
        (st.LADDER[-1].mrc_args, False),
    ]


def test_earlier_save_hints_reach_the_probes_and_the_result():
    pdf, _ = _photo_pdf(1)
    pdf.docinfo["/Title"] = "kept unless dropped"
    setattr(pdf, c.PDFTL_SAVE_HINTS_ATTR, {"drop_info": True})
    result = shrink(pdf, ["target=10M"], "x")
    assert result.pdf.docinfo.get("/Title") is None  # the probe's save dropped it
    hints = getattr(result.pdf, c.PDFTL_SAVE_HINTS_ATTR)
    assert hints["drop_info"] is True
    assert all(hints[name] for name in LEVEL_PASSES["lossless"] & {"recompress", "compress_xmp"})


def test_the_written_file_meets_the_target(tmp_path):
    pdf, _ = _photo_pdf()
    pdf.save(tmp_path / "in.pdf")
    goal = os.path.getsize(tmp_path / "in.pdf") // 6
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    subprocess.run(
        [sys.executable, "-m", "pdftl", str(tmp_path / "in.pdf"), "shrink", f"target={goal}"]
        + ["output", str(tmp_path / "out.pdf")],
        check=True,
        env=env,
    )
    assert os.path.getsize(tmp_path / "out.pdf") <= goal


def _hybrid_xfa_pdf(nbytes):
    """A hybrid form: a widget on the page, plus incompressible XFA packets."""
    pdf, _ = _photo_pdf(1)
    field = pdf.make_indirect(
        pikepdf.Dictionary(
            FT=Name.Tx, T=pikepdf.String("f"), Subtype=Name.Widget, Rect=[0, 0, 9, 9]
        )
    )
    pdf.pages[0].Annots = pdf.make_indirect(pikepdf.Array([field]))
    packets = pikepdf.Array(
        [pikepdf.String("template"), pdf.make_stream(np.random.default_rng(5).bytes(nbytes))]
    )
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([field]), XFA=packets)
    return pdf


def test_hybrid_xfa_counts_as_shrinkable_where_a_rung_drops_it(tmp_path, probes):
    result = shrink(_hybrid_xfa_pdf(200_000), ["target=150K"], "x")
    assert len(probes) > 2  # searched, not sent straight to the bottom rung
    assert _written(result.pdf, tmp_path / "out.pdf") <= 150 * 1024
    with pikepdf.open(tmp_path / "out.pdf") as out:
        assert "/XFA" not in out.Root.AcroForm


@pytest.mark.parametrize("args", [["balanced"], ["skip=drop_xfa"]])
def test_xfa_counts_as_fixed_where_no_rung_drops_it(caplog, probes, args):
    with caplog.at_level(logging.WARNING):
        shrink(_hybrid_xfa_pdf(200_000), ["target=150K", *args], "x")
    assert probes == [st.LADDER[0], st.parse_target(["target=1", *args]).rungs[-1]]
    assert "of it forms" in caplog.text


def test_hybrid_xfa_bytes_of_documents_without_a_form():
    for acro_form in (None, pikepdf.Array()):
        pdf, _ = _photo_pdf(1)
        if acro_form is not None:
            pdf.Root.AcroForm = acro_form
        buf = io.BytesIO()
        pdf.save(buf)
        assert st._hybrid_xfa_bytes(buf.getvalue()) == 0


def _knobs(rung):
    """What a rung does to fidelity, each as a number that falls as loss grows."""
    from pdftl.operations.shrink import _MRC_ARGS

    plan = st._parse_args(list(rung.args))
    mrc = dict(a.split("=") for a in (rung.mrc_args or _MRC_ARGS.get(rung.level, ())))
    simplify = "simplify_vectors" in plan.passes
    return {
        "dpi": plan.dpi or float("inf"),
        "quality": plan.quality or 100,
        "mono_dpi": plan.mono_dpi or float("inf"),
        "text_tolerance": -(plan.text_tolerance or 0),
        "tolerance": -(float(plan.tolerance or 0.15) if simplify else 0),
        "optimize": -["low", "medium", "high"].index(plan.optimize_strength or "low"),
        "guards": plan.guard_fidelity,
        "mrc_guard": rung.mrc_guard,
        "bg_div": -int(mrc.get("bg_div", 3)),
        "bg_quality": int(mrc.get("bg_quality", 75)),
        "fg_quality": int(mrc.get("fg_quality", 45)),
    }


@pytest.mark.parametrize("i", range(1, len(st.LADDER) - 1))
def test_no_knob_gets_more_faithful_down_the_ladder(i):
    here, below = _knobs(st.LADDER[i]), _knobs(st.LADDER[i + 1])
    assert all(below[k] <= here[k] for k in here), (st.LADDER[i], st.LADDER[i + 1])
    assert below != here


def test_a_fit_past_extreme_warns(tmp_path, caplog):
    source = io.BytesIO()
    _photo_pdf()[0].save(source)
    extreme = shrink(pikepdf.open(io.BytesIO(source.getvalue())), ["extreme"], "x").pdf
    goal = _written(extreme, tmp_path / "extreme.pdf") - 1
    with caplog.at_level(logging.WARNING):
        result = shrink(pikepdf.open(io.BytesIO(source.getvalue())), [f"target={goal}"], "x")
    assert _written(result.pdf, tmp_path / "out.pdf") <= goal
    assert "took settings past extreme" in caplog.text
    assert "expect visible loss" in caplog.text


def test_a_fit_at_or_above_extreme_does_not_warn(caplog):
    with caplog.at_level(logging.WARNING):
        shrink(_photo_pdf()[0], ["target=10M"], "x")
    assert "shrink:" not in caplog.text


def _tagged_pdf(nbytes, claim_ua=False):
    """A page with a structure tree holding `nbytes` of incompressible alt text."""
    pdf, _ = _photo_pdf(1)
    alt = pikepdf.String(np.random.default_rng(7).bytes(nbytes))
    elem = pdf.make_indirect(pikepdf.Dictionary(Type=Name.StructElem, S=Name.Figure, Alt=alt))
    pdf.Root.StructTreeRoot = pdf.make_indirect(
        pikepdf.Dictionary(Type=Name.StructTreeRoot, K=elem)
    )
    if claim_ua:
        with pdf.open_metadata() as meta:
            meta["pdfuaid:part"] = "1"
    return pdf


def test_tags_count_as_shrinkable_where_a_rung_deletes_them(tmp_path, probes):
    result = shrink(_tagged_pdf(200_000), ["target=150K"], "x")
    assert len(probes) > 2  # searched, not sent straight to the bottom rung
    assert _written(result.pdf, tmp_path / "out.pdf") <= 150 * 1024
    with pikepdf.open(tmp_path / "out.pdf") as out:
        assert "/StructTreeRoot" not in out.Root


@pytest.mark.parametrize("args", [["strong"], ["skip=delete_tags"]])
def test_tags_count_as_fixed_where_no_rung_deletes_them(caplog, probes, args):
    with caplog.at_level(logging.WARNING):
        shrink(_tagged_pdf(200_000), ["target=150K", *args], "x")
    assert probes == [st.LADDER[0], st.parse_target(["target=1", *args]).rungs[-1]]
    assert "of it tagged structure" in caplog.text


def test_lost_conformance_is_warned_once_for_the_chosen_rung(caplog, probes):
    with caplog.at_level(logging.WARNING):
        shrink(_tagged_pdf(200_000, claim_ua=True), ["target=150K"], "x")
    assert len(probes) > 2
    assert caplog.text.count("claims PDF/UA") == 1


def test_no_conformance_warning_when_the_chosen_rung_keeps_the_tags(caplog):
    with caplog.at_level(logging.WARNING):
        shrink(_tagged_pdf(2_000, claim_ua=True), ["target=10MB"], "x")
    assert "claims" not in caplog.text


def test_quiet_restores_the_level():
    log = logging.getLogger("pdftl.test.quiet")
    log.setLevel(logging.INFO)
    with pytest.raises(KeyError), st._quiet(log):
        assert not log.isEnabledFor(logging.WARNING)
        raise KeyError
    assert log.level == logging.INFO
