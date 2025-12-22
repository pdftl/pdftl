from unittest.mock import MagicMock, patch

import pikepdf
import pytest

import pdftl.core.constants as c
from pdftl.core.registry import registry
from pdftl.fluent import PdfPipeline


@pytest.fixture
def mock_pdf():
    return MagicMock(spec=pikepdf.Pdf)


def test_pipeline_initialization(mock_pdf):
    """Verify the pipeline wraps a PDF object correctly."""
    pipeline = PdfPipeline(mock_pdf)
    assert pipeline._pdf == mock_pdf
    assert pipeline.get() == mock_pdf


@patch("pdftl.fluent.api.call")
def test_fluent_method_dispatch(mock_api_call, mock_pdf):
    """
    Verify that calling a dynamic method on the pipeline
    routes correctly to the API and updates internal state.
    """
    mock_result_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_api_call.return_value = mock_result_pdf

    pipeline = PdfPipeline(mock_pdf)

    # We assume 'cat' exists in the registry
    result = pipeline.cat(operation_args=["1-5"])

    # CORRECTED ASSERTION:
    # The new fluent implementation passes the pipeline's PDF
    # as the first item in the 'inputs' list.
    mock_api_call.assert_called_once_with(
        "cat", operation_args=["1-5"], inputs=[mock_pdf]  # This is the key change
    )

    # Ensure state update happened
    assert pipeline._pdf == mock_result_pdf
    assert result is pipeline


def test_pipeline_chaining_flow(mock_pdf):
    """Verify multiple operations can be chained in one line."""
    with patch("pdftl.fluent.api.call") as mock_api:
        # Mocking a sequence of different PDF objects
        pdf2 = MagicMock(spec=pikepdf.Pdf)
        pdf3 = MagicMock(spec=pikepdf.Pdf)
        mock_api.side_effect = [pdf2, pdf3]

        pipeline = PdfPipeline(mock_pdf)
        pipeline.rotate(operation_args=["90"]).cat(operation_args=["1-2"])

        assert pipeline.get() == pdf3
        assert mock_api.call_count == 2


def test_save_terminal_operation(mock_pdf):
    """Verify that .save() triggers the underlying pikepdf save."""
    pipeline = PdfPipeline(mock_pdf)
    pipeline.save("out.pdf")
    mock_pdf.save.assert_called_once_with("out.pdf")


def test_registry_injection():
    """Ensure every registry operation exists as a method on PdfPipeline."""
    # This relies on registry being populated.
    pipeline = PdfPipeline(MagicMock())
    # We check a few known operations to ensure the dynamic dispatch is active
    known_ops = ["cat", "rotate", "dump_data"]
    for op in known_ops:
        if op in registry.operations:
            assert hasattr(pipeline, op), f"Operation {op} not found on PdfPipeline"
