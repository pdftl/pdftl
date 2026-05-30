# tests/cli/test_each_pipeline.py

from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.cli.main import main
from pdftl.cli.parser import _recursive_group_pipelines, split_args_by_separator
from pdftl.cli.pipeline import CliStage, EachSubPipeline, PipelineManager
from pdftl.exceptions import InvalidArgumentError, UserCommandLineError


def run_main(args):
    return main(["pdftl"] + args)


# ===========================================================================
# Parser-level tests
# ===========================================================================


class TestEachParsing:
    def test_each_produces_each_sub_pipeline(self):
        """EACH token creates EachSubPipeline in grouped args."""
        result = split_args_by_separator(["a.pdf", "EACH", "rotate", "right", "DONE"])
        # One top-level stage
        assert len(result) == 1
        stage_args = result[0]
        # First item is the filename, second is the EachSubPipeline
        assert stage_args[0] == "a.pdf"
        assert isinstance(stage_args[1], EachSubPipeline)

    def test_each_inner_stages_parsed_with_is_first_false(self):
        """Stages inside EACH have _ injected (is_first_stage=False)."""
        result = split_args_by_separator(["a.pdf", "EACH", "rotate", "right", "DONE"])
        each = result[0][1]
        assert isinstance(each, EachSubPipeline)
        assert len(each.stages) == 1
        assert "_" in each.stages[0].inputs

    def test_each_with_pipeline_separator_inside(self):
        """--- inside EACH creates multiple inner stages."""
        result = split_args_by_separator(
            ["a.pdf", "EACH", "rotate", "right", "---", "cat", "DONE"]
        )
        each = result[0][1]
        assert isinstance(each, EachSubPipeline)
        assert len(each.stages) == 2

    def test_each_repr(self):
        each = EachSubPipeline(stages=[])
        assert repr(each) == "<each_sub_pipeline>"

    def test_unclosed_each_raises(self):
        """EACH without DONE raises InvalidArgumentError."""
        with pytest.raises(InvalidArgumentError, match="Unclosed sub-pipeline"):
            list(_recursive_group_pipelines(iter(["EACH", "rotate", "right"])))

    def test_each_and_job_can_coexist(self):
        """EACH and JOB can appear in the same arg list."""
        result = split_args_by_separator(
            [
                "a.pdf",
                "R=JOB",
                "b.pdf",
                "rotate",
                "right",
                "DONE",
                "EACH",
                "cat",
                "DONE",
            ]
        )
        assert len(result) == 1
        args = result[0]
        # Should have: a.pdf, InlineSubPipeline (R=JOB...), EachSubPipeline
        from pdftl.cli.pipeline import InlineSubPipeline

        assert any(isinstance(a, InlineSubPipeline) for a in args)
        assert any(isinstance(a, EachSubPipeline) for a in args)


# ===========================================================================
# Unit tests for _expand_each_in_place and _get_each_output_pdf
# ===========================================================================


class TestExpandEachInPlace:
    def test_expands_opened_pdfs(self):
        """Each input PDF is run through the sub-pipeline."""
        mock_pdf_a = MagicMock()
        mock_pdf_b = MagicMock()
        result_a = MagicMock()
        result_b = MagicMock()

        each = EachSubPipeline(stages=[CliStage(operation="cat")])
        manager = PipelineManager(stages=[], input_context=MagicMock())

        call_results = iter([result_a, result_b])
        manager._get_each_output_pdf = MagicMock(side_effect=lambda item, pdf: next(call_results))

        opened_pdfs = [mock_pdf_a, mock_pdf_b]
        effective_inputs = ["a.pdf", "b.pdf"]

        manager._expand_each_in_place(each, opened_pdfs, effective_inputs)

        assert opened_pdfs == [result_a, result_b]
        assert effective_inputs == ["a.pdf", "b.pdf"]

    def test_raises_when_no_preceding_inputs(self):
        """EACH with no preceding inputs raises UserCommandLineError."""
        each = EachSubPipeline(stages=[])
        manager = PipelineManager(stages=[], input_context=MagicMock())

        with pytest.raises(UserCommandLineError, match="EACH requires at least one input"):
            manager._expand_each_in_place(each, [], [])

    def test_filenames_preserved_after_expansion(self):
        """effective_inputs retains original filenames after EACH expansion."""
        mock_pdf = MagicMock()
        result_pdf = MagicMock()
        each = EachSubPipeline(stages=[])
        manager = PipelineManager(stages=[], input_context=MagicMock())
        manager._get_each_output_pdf = MagicMock(return_value=result_pdf)

        opened_pdfs = [mock_pdf]
        effective_inputs = ["myfile.pdf"]
        manager._expand_each_in_place(each, opened_pdfs, effective_inputs)

        assert effective_inputs == ["myfile.pdf"]


class TestGetEachOutputPdf:
    def test_returns_sub_pipeline_result(self):
        """_get_each_output_pdf runs sub-pipeline and returns its result."""
        result_pdf = MagicMock()
        source_pdf = MagicMock()

        inner_stage = CliStage(operation="cat", inputs=["_"], input_passwords=[None])
        each = EachSubPipeline(stages=[inner_stage])

        manager = PipelineManager(stages=[], input_context=MagicMock())

        with patch("pdftl.cli.pipeline.PipelineManager.run") as mock_run:

            def fake_run(self):
                self.pipeline_pdf = result_pdf

            mock_run.side_effect = lambda: fake_run(
                manager._get_each_output_pdf.__self__
                if hasattr(manager._get_each_output_pdf, "__self__")
                else manager
            )

            # Patch at class level so the sub_manager created inside gets it
            with patch.object(
                PipelineManager, "run", lambda self: setattr(self, "pipeline_pdf", result_pdf)
            ):
                output = manager._get_each_output_pdf(each, source_pdf)

        assert output is result_pdf

    def test_raises_when_sub_pipeline_returns_none(self):
        """_get_each_output_pdf raises if inner pipeline has no output."""
        source_pdf = MagicMock()
        each = EachSubPipeline(stages=[CliStage(operation="cat")])
        manager = PipelineManager(stages=[], input_context=MagicMock())

        with patch.object(
            PipelineManager, "run", lambda self: setattr(self, "pipeline_pdf", None)
        ):
            with pytest.raises(
                UserCommandLineError, match="EACH sub-pipeline returned no output PDF"
            ):
                manager._get_each_output_pdf(each, source_pdf)

    def test_sub_manager_inherits_handles(self):
        """_get_each_output_pdf passes outer handles to sub-manager."""
        outer_pdf = MagicMock()
        source_pdf = MagicMock()
        result_pdf = MagicMock()
        each = EachSubPipeline(stages=[])
        manager = PipelineManager(stages=[], input_context=MagicMock(), handles={"X": outer_pdf})

        created_managers = []

        original_init = PipelineManager.__init__

        def capturing_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            created_managers.append(self)

        with patch.object(PipelineManager, "__init__", capturing_init):
            with patch.object(
                PipelineManager, "run", lambda self: setattr(self, "pipeline_pdf", result_pdf)
            ):
                manager._get_each_output_pdf(each, source_pdf)

        sub_manager = created_managers[-1]
        assert "X" in sub_manager.handles
        assert sub_manager.is_each is True
        assert sub_manager.is_inline is True


# ===========================================================================
# Integration tests using real PDFs
# ===========================================================================


class TestEachIntegration:
    def test_each_basic_passthrough(self, dummy_pdfs, tmp_path):
        """EACH cat DONE passes each file through unchanged, then outer cat merges."""
        a = dummy_pdfs["a.pdf"]  # 20 pages
        b = dummy_pdfs["b.pdf"]  # 20 pages
        output = tmp_path / "out.pdf"

        exit_code = run_main(
            [
                str(a),
                str(b),
                "EACH",
                "cat",
                "DONE",
                "cat",
                "output",
                str(output),
            ]
        )

        assert exit_code == 0
        with pikepdf.open(output) as pdf:
            assert len(pdf.pages) == 40

    def test_each_applies_operation_to_each_input(self, dummy_pdfs, tmp_path):
        """EACH rotate right DONE rotates each file independently."""
        a = dummy_pdfs["a.pdf"]
        b = dummy_pdfs["b.pdf"]
        output = tmp_path / "rotated.pdf"

        exit_code = run_main(
            [
                str(a),
                str(b),
                "EACH",
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
            assert len(pdf.pages) == 40
            # All pages should be rotated 90 degrees
            assert pdf.pages[0].get("/Rotate") == 90
            assert pdf.pages[20].get("/Rotate") == 90

    def test_each_single_input(self, dummy_pdfs, tmp_path):
        """EACH works with a single input file."""
        a = dummy_pdfs["a.pdf"]
        output = tmp_path / "single.pdf"

        exit_code = run_main(
            [
                str(a),
                "EACH",
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
            assert len(pdf.pages) == 20
            assert pdf.pages[0].get("/Rotate") == 90

    def test_each_with_page_selection_inside(self, dummy_pdfs, tmp_path):
        """EACH can contain page specs — cat 1 selects first page of each."""
        a = dummy_pdfs["a.pdf"]  # 20 pages
        b = dummy_pdfs["b.pdf"]  # 20 pages
        output = tmp_path / "firstpages.pdf"

        exit_code = run_main(
            [
                str(a),
                str(b),
                "EACH",
                "cat",
                "1",
                "DONE",
                "cat",
                "output",
                str(output),
            ]
        )

        assert exit_code == 0
        with pikepdf.open(output) as pdf:
            assert len(pdf.pages) == 2

    def test_each_with_multistage_inner_pipeline(self, dummy_pdfs, tmp_path):
        """EACH with --- inside applies a multi-stage pipeline to each input."""
        a = dummy_pdfs["a.pdf"]
        b = dummy_pdfs["b.pdf"]
        output = tmp_path / "multi.pdf"

        exit_code = run_main(
            [
                str(a),
                str(b),
                "EACH",
                "cat",
                "1",
                "---",
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
            assert len(pdf.pages) == 2
            assert pdf.pages[0].get("/Rotate") == 90

    def test_each_three_inputs(self, dummy_pdfs, tmp_path):
        """EACH scales correctly with three inputs."""
        a = dummy_pdfs["a.pdf"]
        b = dummy_pdfs["b.pdf"]
        output = tmp_path / "three.pdf"

        exit_code = run_main(
            [
                str(a),
                str(b),
                str(a),
                "EACH",
                "cat",
                "1",
                "DONE",
                "cat",
                "output",
                str(output),
            ]
        )

        assert exit_code == 0
        with pikepdf.open(output) as pdf:
            assert len(pdf.pages) == 3

    def test_each_no_preceding_inputs_raises(self, tmp_path, capsys):
        """EACH with no preceding inputs gives a clear error."""
        output = tmp_path / "out.pdf"
        exit_code = run_main(
            [
                "EACH",
                "rotate",
                "right",
                "DONE",
                "cat",
                "output",
                str(output),
            ]
        )
        # Should fail with exit code 1 (UserCommandLineError)
        assert exit_code == 1
        assert "EACH requires at least one input" in capsys.readouterr().err

    def test_each_in_pipeline_chain(self, dummy_pdfs, tmp_path):
        """EACH works as part of a larger --- chain."""
        a = dummy_pdfs["a.pdf"]
        b = dummy_pdfs["b.pdf"]
        output = tmp_path / "chained.pdf"

        exit_code = run_main(
            [
                str(a),
                str(b),
                "EACH",
                "cat",
                "1",
                "DONE",
                "cat",
                "---",
                "rotate",
                "right",
                "output",
                str(output),
            ]
        )

        assert exit_code == 0
        with pikepdf.open(output) as pdf:
            assert len(pdf.pages) == 2
            assert pdf.pages[0].get("/Rotate") == 90


def test_open_pdf_from_file_emfile_error(tmp_path):
    """errno 24 (EMFILE) gives a helpful hint about ulimit."""
    manager = PipelineManager(stages=[], input_context=MagicMock())
    err = OSError(24, "Too many open files")
    with patch("pdftl.cli.pipeline.smart_pikepdf_open", side_effect=err):
        with pytest.raises(UserCommandLineError, match="ulimit"):
            manager._open_pdf_from_file("a.pdf", None)


def test_open_pdf_from_file_other_oserror(tmp_path):
    """Other OSErrors are wrapped in a clean UserCommandLineError."""
    manager = PipelineManager(stages=[], input_context=MagicMock())
    err = OSError(13, "Permission denied")
    with patch("pdftl.cli.pipeline.smart_pikepdf_open", side_effect=err):
        with pytest.raises(UserCommandLineError, match="a.pdf"):
            manager._open_pdf_from_file("a.pdf", None)
