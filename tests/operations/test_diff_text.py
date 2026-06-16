import pytest
from unittest.mock import MagicMock, patch, ANY

from pdftl.operations.diff_text import diff_text
from pdftl.core.core_types import OpResult


@pytest.fixture
def mock_pikepdf():
    """Mocks the pikepdf.Pdf object and its save behavior."""
    mock_pdf = MagicMock()

    def mock_save(stream):
        stream.write(b"mock pdf bytes")

    mock_pdf.save.side_effect = mock_save
    return mock_pdf


@patch("pdftl.operations.diff_text.compact_json_string")
@patch("pdftl.operations.diff_text.process_diff_stream")
@patch("pdftl.operations.diff_text.compute_diff_chunks")
@patch("pdftl.operations.diff_text.GlobalStreamMapper")
@patch("pdftl.utils.pdf_text.text_provider.TextProvider")
@patch("pdftl.operations.diff_text.ensure_dependencies")
@patch.dict("sys.modules", {"pypdfium2": MagicMock()})
def test_diff_text_success(
    mock_deps,
    mock_text_provider,
    mock_mapper,
    mock_compute,
    mock_process,
    mock_compact,
    mock_pikepdf,
):
    """Tests the standard execution path and argument parsing."""

    # Setup Mocks
    mock_compute.return_value = [(0, "test chunk")]
    mock_process.return_value = [{"type": "insert", "file_b": []}]
    mock_compact.return_value = '{"compacted": "json"}'

    # Mapper mocks
    mock_mapper_a = MagicMock(full_stream="stream a")
    mock_mapper_b = MagicMock(full_stream="stream b")
    mock_mapper.side_effect = [mock_mapper_a, mock_mapper_b]

    # TextProvider mocks
    mock_tp_instance_a = MagicMock()
    mock_tp_instance_b = MagicMock()
    mock_text_provider.side_effect = [mock_tp_instance_a, mock_tp_instance_b]

    # Operation Arguments
    operation_args = [
        "granularity=char",
        "ignore_whitespace=false",
        "ignore_soft_hyphens=true",
        "include_bboxes=false",
        "merge_bboxes=false",
        "margin_top=15",
        "margin_bottom=20.5",
    ]

    # Execute
    result = diff_text(
        pdf_a=mock_pikepdf,
        file_b_path="dummy/path/b.pdf",
        operation_args=operation_args,
    )

    # Assertions
    mock_deps.assert_called_once()

    # Check if arguments were parsed and passed to the mappers correctly
    expected_margins = {"top": 15.0, "bottom": 20.5, "left": 0.0, "right": 0.0}
    assert mock_mapper.call_count == 2

    # Use ANY for the dynamically generated pdfium mock and its length
    mock_mapper.assert_any_call(mock_tp_instance_a, ANY, ANY, expected_margins)
    mock_mapper.assert_any_call(mock_tp_instance_b, ANY, ANY, expected_margins)

    # Check if processing delegates were called with correct mapped args
    mock_compute.assert_called_once_with("stream a", "stream b", "char", True)
    mock_process.assert_called_once_with(
        [(0, "test chunk")],
        mock_mapper_a,
        mock_mapper_b,
        False,  # ignore_whitespace
        False,  # include_bboxes
        False,  # merge_bboxes
    )

    # Check if cleanup hooks fired
    mock_tp_instance_a.close.assert_called_once()
    mock_tp_instance_b.close.assert_called_once()

    # Check Return
    assert isinstance(result, OpResult)
    assert result.success is True
    assert result.data == '{"compacted": "json"}'


@patch("pdftl.operations.diff_text.GlobalStreamMapper")
@patch("pdftl.utils.pdf_text.text_provider.TextProvider")
@patch("pdftl.operations.diff_text.ensure_dependencies")
@patch.dict("sys.modules", {"pypdfium2": MagicMock()})
def test_diff_text_exception_cleanup(
    mock_deps,
    mock_text_provider,
    mock_mapper,
    mock_pikepdf,
):
    """Tests that text providers are securely closed even if an exception occurs."""

    # Setup Mocks to throw an error during execution
    mock_mapper.side_effect = Exception("Intentional Crash")

    mock_tp_instance_a = MagicMock()
    mock_tp_instance_b = MagicMock()
    mock_text_provider.side_effect = [mock_tp_instance_a, mock_tp_instance_b]

    # Execute
    with pytest.raises(Exception, match="Intentional Crash"):
        diff_text(
            pdf_a=mock_pikepdf,
            file_b_path="dummy/path/b.pdf",
            operation_args=[],
        )

    # Assertions to guarantee try/finally execution
    mock_tp_instance_a.close.assert_called_once()
    mock_tp_instance_b.close.assert_called_once()
