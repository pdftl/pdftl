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


# --- Fallback path returns visual pixels, like the main path ---


def _force_fallback(monkeypatch):
    def unextractable(*args, **kwargs):
        raise pikepdf.HifiPrintImageNotTranscodableError()

    monkeypatch.setattr(pikepdf, "PdfImage", unextractable)


def _rgb_stream(pdf, samples, decode=None):
    stream = pdf.make_stream(bytes(samples))
    stream["/Width"], stream["/Height"] = 2, 1
    if decode is not None:
        stream["/Decode"] = pikepdf.Array(decode)
    return stream


def test_fallback_without_decode_returns_samples(empty_pdf, monkeypatch):
    _force_fallback(monkeypatch)
    img = extract_to_pil(_rgb_stream(empty_pdf, [10, 20, 30, 200, 210, 220]))
    assert list(img.tobytes()) == [10, 20, 30, 200, 210, 220]


def test_fallback_applies_inverting_decode(empty_pdf, monkeypatch):
    _force_fallback(monkeypatch)
    samples = [10, 20, 30, 200, 210, 220]
    img = extract_to_pil(_rgb_stream(empty_pdf, samples, [1, 0, 1, 0, 1, 0]))
    assert list(img.tobytes()) == [255 - v for v in samples]


def test_fallback_applies_partial_range_decode(empty_pdf, monkeypatch):
    _force_fallback(monkeypatch)
    img = extract_to_pil(_rgb_stream(empty_pdf, [0, 255, 0, 255, 0, 255], [0, 0.5, 0, 1, 0.5, 1]))
    assert list(img.tobytes()) == [0, 255, 128, 128, 0, 255]


@pytest.mark.parametrize(
    "samples,decode",
    [([1, 2, 3], [1, 0, 1, 0, 1, 0]), ([10, 20, 30, 40, 50, 60], [1, 0])],
)
def test_fallback_gives_up_when_decode_cannot_be_honoured(empty_pdf, monkeypatch, samples, decode):
    _force_fallback(monkeypatch)
    assert extract_to_pil(_rgb_stream(empty_pdf, samples, decode)) is None


def test_fallback_gives_up_on_non_numeric_decode(empty_pdf, monkeypatch):
    _force_fallback(monkeypatch)
    decode = [pikepdf.Name("/A")] * 6
    assert extract_to_pil(_rgb_stream(empty_pdf, [0] * 6, decode)) is None


def test_fallback_opens_self_describing_image_bytes(empty_pdf, monkeypatch):
    import io

    from PIL import Image

    _force_fallback(monkeypatch)
    buf = io.BytesIO()
    Image.new("L", (3, 2), 77).save(buf, format="PNG")
    stream = empty_pdf.make_stream(buf.getvalue())
    stream["/Width"], stream["/Height"] = 3, 2
    img = extract_to_pil(stream)
    assert img.size == (3, 2) and img.getpixel((0, 0)) == 77


def test_fallback_returns_none_for_unrecognised_bytes(empty_pdf, monkeypatch):
    _force_fallback(monkeypatch)
    stream = empty_pdf.make_stream(b"not an image")
    stream["/Width"], stream["/Height"] = 3, 2
    assert extract_to_pil(stream) is None
