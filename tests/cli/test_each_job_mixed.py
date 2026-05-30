# tests/cli/test_each_job_mixed.py
"""
Tests for interactions between JOB...DONE and EACH...DONE.

Both use DONE as their terminator. These tests verify that nesting
the two constructs in either direction parses and executes correctly,
and that DONE always closes the innermost open block regardless of
whether that block was opened by JOB or EACH.
"""

import pikepdf

from pdftl.cli.main import main
from pdftl.cli.parser import split_args_by_separator
from pdftl.cli.pipeline import EachSubPipeline, InlineSubPipeline


def run_main(args):
    return main(["pdftl"] + args)


# ===========================================================================
# Parser-level: verify DONE closes the right block in mixed nesting
# ===========================================================================


class TestMixedParsing:
    def test_each_inside_job_parses(self):
        """JOB containing EACH: outer DONE closes JOB, inner DONE closes EACH."""
        # R=JOB a.pdf b.pdf EACH cat DONE DONE
        # depth: JOB->1, EACH->2, first DONE->back to 1, second DONE->back to 0
        result = split_args_by_separator(
            [
                "R=JOB",
                "a.pdf",
                "b.pdf",
                "EACH",
                "cat",
                "DONE",
                "DONE",
                "cat",
                "R",
            ]
        )
        assert len(result) == 1
        stage_args = result[0]
        # R=JOB produces an InlineSubPipeline
        inline = next(a for a in stage_args if isinstance(a, InlineSubPipeline))
        assert inline.handle_name == "R"
        # The InlineSubPipeline's inner stage list contains the EACH
        inner_inputs = inline.stages[0].inputs
        each = next(a for a in inner_inputs if isinstance(a, EachSubPipeline))
        assert isinstance(each, EachSubPipeline)

    def test_job_inside_each_parses(self):
        """EACH containing JOB: inner DONE closes JOB, outer DONE closes EACH."""
        # a.pdf b.pdf EACH R=JOB cat 1 DONE cat DONE cat output final.pdf
        result = split_args_by_separator(
            [
                "a.pdf",
                "b.pdf",
                "EACH",
                "R=JOB",
                "cat",
                "1",
                "DONE",
                "cat",
                "R",
                "DONE",
                "cat",
            ]
        )
        assert len(result) == 1
        stage_args = result[0]
        each = next(a for a in stage_args if isinstance(a, EachSubPipeline))
        assert isinstance(each, EachSubPipeline)
        # Inside the EACH there should be a stage whose inputs contain an InlineSubPipeline
        each_stage_inputs = each.stages[0].inputs
        inline = next(
            (a for a in each_stage_inputs if isinstance(a, InlineSubPipeline)),
            None,
        )
        assert inline is not None

    def test_sibling_each_and_job_parse(self):
        """EACH and JOB as siblings (not nested): each gets its own DONE."""
        # a.pdf EACH cat DONE R=JOB b.pdf rotate right DONE cat R
        result = split_args_by_separator(
            [
                "a.pdf",
                "EACH",
                "cat",
                "DONE",
                "R=JOB",
                "b.pdf",
                "rotate",
                "right",
                "DONE",
                "cat",
                "R",
            ]
        )
        assert len(result) == 1
        stage_args = result[0]
        assert any(isinstance(a, EachSubPipeline) for a in stage_args)
        assert any(isinstance(a, InlineSubPipeline) for a in stage_args)

    def test_deeply_nested_job_each_job(self):
        """Three levels: JOB > EACH > JOB — each DONE closes the right level."""
        result = split_args_by_separator(
            [
                "R=JOB",
                "a.pdf",
                "EACH",
                "S=JOB",
                "cat",
                "1",
                "DONE",
                "cat",
                "S",
                "DONE",
                "DONE",
                "cat",
                "R",
            ]
        )
        assert len(result) == 1
        stage_args = result[0]
        outer = next(a for a in stage_args if isinstance(a, InlineSubPipeline))
        assert outer.handle_name == "R"
        # Outer JOB's first stage inputs should contain an EachSubPipeline
        outer_inputs = outer.stages[0].inputs
        each = next(a for a in outer_inputs if isinstance(a, EachSubPipeline))
        # EACH's first stage inputs should contain an InlineSubPipeline (the inner JOB)
        each_inputs = each.stages[0].inputs
        inner = next(a for a in each_inputs if isinstance(a, InlineSubPipeline))
        assert inner.handle_name == "S"


# ===========================================================================
# Integration: EACH inside JOB
# EACH expands multiple inputs; JOB wraps the whole thing into one PDF.
# The JOB result should be a single PDF (the merged output of all iterations).
# ===========================================================================


class TestEachInsideJob:
    def test_each_inside_job_produces_single_pdf(self, dummy_pdfs, tmp_path):
        """
        R=JOB a.pdf b.pdf EACH cat 1 DONE DONE
        JOB wraps an EACH that selects page 1 from each of a.pdf and b.pdf.
        The EACH expands to two 1-page PDFs; the implicit cat inside JOB
        merges them. R ends up as a 2-page PDF used in the outer cat.
        """
        a = dummy_pdfs["a.pdf"]
        b = dummy_pdfs["b.pdf"]
        output = tmp_path / "each_inside_job.pdf"

        exit_code = run_main(
            [
                "R=JOB",
                str(a),
                str(b),
                "EACH",
                "cat",
                "1",
                "DONE",
                "DONE",
                "cat",
                "R",
                "output",
                str(output),
            ]
        )

        assert exit_code == 0
        with pikepdf.open(output) as pdf:
            # EACH produced one page from a and one from b; JOB merged them → 2 pages
            assert len(pdf.pages) == 2

    def test_each_inside_job_with_rotation(self, dummy_pdfs, tmp_path):
        """
        R=JOB a.pdf b.pdf EACH rotate right DONE DONE
        Each file is rotated inside the EACH; JOB collects both results.
        All pages in R should be rotated.
        """
        a = dummy_pdfs["a.pdf"]
        b = dummy_pdfs["b.pdf"]
        output = tmp_path / "rotated_inside_job.pdf"

        exit_code = run_main(
            [
                "R=JOB",
                str(a),
                str(b),
                "EACH",
                "rotate",
                "right",
                "DONE",
                "DONE",
                "cat",
                "R",
                "output",
                str(output),
            ]
        )

        assert exit_code == 0
        with pikepdf.open(output) as pdf:
            assert len(pdf.pages) == 40
            assert pdf.pages[0].get("/Rotate") == 90
            assert pdf.pages[20].get("/Rotate") == 90


# ===========================================================================
# Integration: JOB inside EACH
# Each iteration receives _ plus the result of a JOB sub-pipeline.
# ===========================================================================


class TestJobInsideEach:
    def test_job_inside_each_basic(self, dummy_pdfs, tmp_path):
        """
        a.pdf b.pdf EACH R=JOB _ cat 1 DONE cat R DONE cat output final.pdf

        For each input file, a JOB selects its first page, then cat
        concatenates _ (the full file) with R (the first page).
        Each iteration: 20+1 = 21 pages (a) and 20+1 = 21 pages (b).
        Final cat merges both → 42 pages.
        """
        a = dummy_pdfs["a.pdf"]
        b = dummy_pdfs["b.pdf"]
        output = tmp_path / "job_inside_each.pdf"

        exit_code = run_main(
            [
                str(a),
                str(b),
                "EACH",
                "R=JOB",
                "_",
                "cat",
                "1",
                "DONE",
                "cat",
                "_",
                "R",
                "DONE",
                "cat",
                "output",
                str(output),
            ]
        )

        assert exit_code == 0
        with pikepdf.open(output) as pdf:
            assert len(pdf.pages) == 42

    def test_job_inside_each_same_job_for_every_iteration(self, dummy_pdfs, tmp_path):
        """
        a.pdf b.pdf EACH S=JOB c.pdf cat 1 DONE cat S DONE cat output final.pdf

        JOB uses a fixed file (c.pdf = a.pdf here) regardless of the EACH iteration.
        Each of the two input files gets one extra page prepended from c.pdf page 1.
        a: 20+1=21, b: 20+1=21 → 42 total.
        """
        a = dummy_pdfs["a.pdf"]
        b = dummy_pdfs["b.pdf"]
        output = tmp_path / "fixed_job_inside_each.pdf"

        exit_code = run_main(
            [
                str(a),
                str(b),
                "EACH",
                "S=JOB",
                str(a),
                "cat",
                "1",
                "DONE",
                "cat",
                "S",
                "_",
                "DONE",
                "cat",
                "output",
                str(output),
            ]
        )

        assert exit_code == 0
        with pikepdf.open(output) as pdf:
            assert len(pdf.pages) == 42


# ===========================================================================
# Integration: siblings — EACH and JOB in the same stage, not nested
# ===========================================================================


class TestSiblingEachAndJob:
    def test_each_and_job_as_siblings(self, dummy_pdfs, tmp_path):
        """
        a.pdf b.pdf EACH cat 1 DONE R=JOB c.pdf rotate right DONE cat R

        EACH selects page 1 from each of a and b (→ 2 PDFs, 1 page each).
        JOB rotates c.pdf and assigns it to R.
        Outer cat concatenates [a_p1, b_p1, R] → 1+1+20 = 22 pages.
        """
        a = dummy_pdfs["a.pdf"]
        b = dummy_pdfs["b.pdf"]
        output = tmp_path / "siblings.pdf"

        exit_code = run_main(
            [
                str(a),
                str(b),
                "EACH",
                "cat",
                "1",
                "DONE",
                "R=JOB",
                str(a),
                "rotate",
                "right",
                "DONE",
                "cat",
                "output",
                str(output),
            ]
        )

        assert exit_code == 0
        with pikepdf.open(output) as pdf:
            # 1 (a p1) + 1 (b p1) + 20 (R = rotated a) = 22
            assert len(pdf.pages) == 22
            # R pages should be rotated
            assert pdf.pages[2].get("/Rotate") == 90
