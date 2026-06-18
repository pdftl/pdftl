import pytest
import pikepdf
from pdftl.utils.images.selectors import extract_to_pil


def test_selectors_read_bytes_exception_handling(empty_pdf, monkeypatch):
    """Directly hits selectors.py lines 56-58 using a lightweight object proxy wrapper."""
    # Set up a generic dummy object with required geometry attributes
    img_xobj = empty_pdf.make_stream(b"")
    img_xobj["/Width"] = 10
    img_xobj["/Height"] = 10

    # Force Block A (high-level extraction) to fail via standard PdfError
    def mock_pdf_image_fail(*args, **kwargs):
        raise pikepdf.PdfError("Force native fallback")

    monkeypatch.setattr(pikepdf, "PdfImage", mock_pdf_image_fail)

    # Clean proxy pattern to intercept read_bytes safely without touching C++ state tables
    class StreamReadFailureProxy:
        def __init__(self, target):
            self._target = target

        def __getitem__(self, key):
            return self._target[key]

        def read_bytes(self):
            raise pikepdf.DataDecodingError("Simulated unfilterable stream data")

    proxy_obj = StreamReadFailureProxy(img_xobj)

    # Execution safely hits lines 56-58, logs the warning, and returns None
    assert extract_to_pil(proxy_obj) is None


def test_selectors_cpp_runtime_exception_isolation(empty_pdf, monkeypatch):
    """Directly hits selectors.py lines 32-51 by mimicking an unmapped C++ runtime error."""
    img_xobj = empty_pdf.make_stream(b"")
    img_xobj["/Width"] = 10
    img_xobj["/Height"] = 10

    # Force pikepdf.PdfImage to raise a raw RuntimeError to trigger the second except block
    def mock_cpp_explosion(*args, **kwargs):
        raise RuntimeError("Nanobind core-dump violation")

    monkeypatch.setattr(pikepdf, "PdfImage", mock_cpp_explosion)

    # We also want to let the test exit gracefully after it logs and drops down,
    # so we prevent the raw_bytes logic from executing fully.
    class StreamFallbackStopProxy:
        def __init__(self, target):
            self._target = target

        def __getitem__(self, key):
            return self._target[key]

        def read_bytes(self):
            # Abort here to confirm it cascaded past the isolation layer into block B
            raise pikepdf.PdfError("Cascaded correctly")

    proxy_obj = StreamFallbackStopProxy(img_xobj)

    # Execution hits line 32, runs the isolator string checks, prints the debug log,
    # drops into Block B, hits our custom abort, and returns None.
    assert extract_to_pil(proxy_obj) is None


def test_selectors_unexpected_exception_reraise(empty_pdf, monkeypatch):
    """Verifies lines 45-51 re-raise completely unhandled non-C++ system crashes."""
    img_xobj = empty_pdf.make_stream(b"")
    img_xobj["/Width"] = 10
    img_xobj["/Height"] = 10

    # Throw an exception that is NEITHER a pikepdf error nor contains "RuntimeError"/"HifiPrintImage"
    def mock_unexpected_crash(*args, **kwargs):
        raise KeyError("Unexpected system environment error")

    monkeypatch.setattr(pikepdf, "PdfImage", mock_unexpected_crash)

    # The unexpected exception block should trap it, error-log it, and re-raise it
    with pytest.raises(KeyError):
        extract_to_pil(img_xobj)
