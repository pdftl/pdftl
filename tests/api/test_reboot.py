from unittest.mock import MagicMock, patch

import pikepdf

import pdftl.core.constants as c
from pdftl import api, fluent


@patch("pikepdf.open")
@patch("pdftl.core.executor.run_operation")
def test_api_normalization_and_io(mock_run, mock_open):
    """
    API should normalize string inputs by opening them
    and passing objects to the executor.
    """
    mock_run.return_value = "success"
    # Mock return values for file opens
    pdf1 = MagicMock(spec=pikepdf.Pdf)
    pdf2 = MagicMock(spec=pikepdf.Pdf)
    mock_open.side_effect = [pdf1, pdf2]

    # Test with string paths
    api.cat(inputs=["a.pdf", "b.pdf"], operation_args=["1-5"])

    # Verify IO happened
    assert mock_open.call_count == 2
    mock_open.assert_any_call("a.pdf")

    # Verify the context structure passed to executor
    args, _ = mock_run.call_args
    op_name = args[0]
    context = args[1]

    assert op_name == "cat"
    # Inputs list should contain the filenames (or placeholders)
    assert context[c.INPUTS] == ["a.pdf", "b.pdf"]
    # Opened PDFs should be populated with our mocks
    assert context[c.OPENED_PDFS][0] == pdf1
    assert context[c.OPENED_PDFS][1] == pdf2
    assert context[c.OPERATION_ARGS] == ["1-5"]


@patch("pdftl.core.executor.run_operation")
def test_api_handles_memory_objects(mock_run):
    """
    API should accept existing pikepdf.Pdf objects directly.
    """
    mock_pdf = MagicMock(spec=pikepdf.Pdf)

    # Passing 'pdf' keyword (shortcut for single input)
    api.rotate(pdf=mock_pdf, operation_args=["90"])

    _, context = mock_run.call_args[0]

    # Should map to index 0
    assert context[c.OPENED_PDFS][0] == mock_pdf
    # operation_args should be preserved
    assert context[c.OPERATION_ARGS] == ["90"]


@patch("pdftl.api.call")
def test_fluent_eager_execution(mock_api_call):
    """
    Fluent interface is EAGER. It calls API immediately and updates state.
    """
    # Setup mock returns
    pdf_v1 = MagicMock(spec=pikepdf.Pdf)
    pdf_v2 = MagicMock(spec=pikepdf.Pdf)
    mock_api_call.side_effect = [pdf_v1, pdf_v2]

    # Start chain
    mock_start = MagicMock(spec=pikepdf.Pdf)
    pipeline = fluent.PdfPipeline(mock_start)

    # 1. Rotate
    res1 = pipeline.rotate(operation_args=["90"])
    assert res1 == pipeline  # Should return self
    assert pipeline._pdf == pdf_v1  # Internal state updated

    # 2. Cat
    res2 = pipeline.cat(operation_args=["1-5"])
    assert res2 == pipeline
    assert pipeline._pdf == pdf_v2

    # Verify API calls
    assert mock_api_call.call_count == 2
