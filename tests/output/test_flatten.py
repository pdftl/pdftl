import importlib
import logging
import sys
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.output import flatten as flatten_module


@pytest.fixture
def fresh_flatten_module():
    """
    Sanitize the module and its logger to ensure isolation.
    """
    # 1. Aggressive Logger Reset
    #    Target both the specific module logger AND the package parent.
    #    If 'pdftl' parent logger has propagate=False, child logs die there
    #    and never reach the caplog (which watches Root).
    loggers_to_reset = [
        "pdftl.output.flatten",  # The module
        "pdftl",  # The package root
    ]

    for name in loggers_to_reset:
        logger = logging.getLogger(name)
        logger.setLevel(logging.NOTSET)
        logger.propagate = True  # FORCE propagation to root
        # Optional: Remove handlers that might be swallowing records
        # logger.handlers.clear()

    # 2. Reload the module (Logic Fix)
    importlib.reload(flatten_module)
    yield flatten_module
    importlib.reload(flatten_module)


def test_flatten_fallback_when_pypdfium_missing(mock_pdf, fresh_flatten_module, caplog):
    """
    Verifies fallback logic when pypdfium2 is missing.
    """
    mock_pdf.Root.__contains__.side_effect = lambda k: k == "/AcroForm"
    mock_pdf.Root.AcroForm = MagicMock()

    # Explicitly capture logs from the specific logger to avoid root logger noise/issues
    caplog.set_level(logging.DEBUG, logger="pdftl.output.flatten")

    with patch.dict(sys.modules, {"pypdfium2": None}):
        importlib.reload(fresh_flatten_module)

        fresh_flatten_module.flatten_pdf(mock_pdf)

        # Check records directly if .text is proving flaky
        assert "pypdfium2 not found" in caplog.text

        mock_pdf.flatten_annotations.assert_called_with(mode="all")


def test_flatten_uses_renderer_if_available(fresh_flatten_module):
    """
    Verifies that the renderer path is chosen if pypdfium2 exists.
    """
    mock_pdfium_mod = MagicMock()
    mock_pdfium_doc = mock_pdfium_mod.PdfDocument.return_value
    mock_pikepdf = MagicMock(spec=pikepdf.Pdf)

    # Patch pypdfium2 to be our mock
    with patch.dict(sys.modules, {"pypdfium2": mock_pdfium_mod}):
        with patch("pikepdf.Pdf.open") as mock_pikepdf_open:

            # Reload so the module imports our mock_pdfium_mod
            importlib.reload(fresh_flatten_module)

            fresh_flatten_module.flatten_pdf(mock_pikepdf)

            # Verify renderer usage
            mock_pdfium_doc.init_forms.assert_called_once()
            assert mock_pdfium_doc.__iter__.called
            mock_pikepdf_open.assert_called_once()


def test_flatten_pypdfium2_runtime_error(mock_pdf, caplog, fresh_flatten_module):
    """
    Simulates pypdfium2 crashing during execution.
    """
    mock_pypdfium = MagicMock()
    mock_pypdfium.PdfDocument.side_effect = RuntimeError("PDFium exploded")

    with patch.dict(sys.modules, {"pypdfium2": mock_pypdfium}):

        importlib.reload(fresh_flatten_module)

        fresh_flatten_module.flatten_pdf(mock_pdf)

        assert "pypdfium2 flattening failed" in caplog.text
        mock_pdf.flatten_annotations.assert_called()


def test_flatten_appearance_generation_error(mock_pdf, caplog, fresh_flatten_module):
    """
    Simulates failure to generate appearance streams during fallback.
    """
    mock_pdf.Root.__contains__.return_value = True
    mock_pdf.generate_appearance_streams.side_effect = pikepdf.PdfError("Bad stream")

    # Ensure we are in fallback mode (no pypdfium)
    with patch.dict(sys.modules, {"pypdfium2": None}):

        importlib.reload(fresh_flatten_module)

        fresh_flatten_module.flatten_pdf(mock_pdf)

        assert "Could not generate appearance streams" in caplog.text
        assert "Bad stream" in caplog.text
        mock_pdf.flatten_annotations.assert_called()
