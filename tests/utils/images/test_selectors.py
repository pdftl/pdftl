import pytest
import pikepdf
from PIL import Image
from pdftl.utils.images.selectors import extract_to_pil


class MockRuntimeError(RuntimeError):
    """Explicitly mimics a C++ nanobind runtime engine breakdown."""

    pass


class MockHifiPrintImageError(Exception):
    """Mimics pikepdf's internal high-resolution profile errors."""

    pass


def test_extract_to_pil_traps_nanobind_runtime_errors(monkeypatch):
    """Covers lines 24-32: Verifies unmapped C++ extensions trigger the low-level cascade."""

    class StubXObj:
        def __getitem__(self, key):
            return 10  # Standard Width / Height placeholder

        def read_bytes(self):
            return b"fake_pixel_bytes"

    # Force Block A to blow up with a simulated C++ runtime failure
    monkeypatch.setattr(
        pikepdf, "PdfImage", lambda x: (_ for _ in ()).throw(MockRuntimeError("C++ binding crash"))
    )

    # Verify execution rolls safely into Block B/C instead of crashing the process
    res = extract_to_pil(StubXObj())
    assert (
        res is None
    )  # Pillow returns None on fake_pixel_bytes, verifying the cascade hit Block C


def test_extract_to_pil_re_raises_actual_bugs(monkeypatch):
    """Covers line 31: Verifies critical developer bugs (e.g., NameError) bypass guards."""

    class StubXObj:
        def __getitem__(self, key):
            return 10

    monkeypatch.setattr(
        pikepdf, "PdfImage", lambda x: (_ for _ in ()).throw(NameError("developer_typo"))
    )

    with pytest.raises(NameError, match="developer_typo"):
        extract_to_pil(StubXObj())


def test_extract_to_pil_canvas_assembly_value_error_fallback(monkeypatch):
    """Covers lines 42-49: Verifies that frombytes value mismatches cascade safely into Image.open."""

    class StubXObj:
        def __getitem__(self, key):
            return 10

        def read_bytes(self):
            return b"corrupt_or_truncated_stream"

        def read_raw_bytes(self):
            return b"corrupt_or_truncated_stream"

    # Block A passes, moving execution into Block B/C
    monkeypatch.setattr(
        pikepdf, "PdfImage", lambda x: (_ for _ in ()).throw(ValueError("Native failed"))
    )

    # Mock Image.open to see if Block C executes its deepest rescue branch
    open_called = False

    def mock_open(fp):
        nonlocal open_called
        open_called = True
        raise OSError("Ultimate fallback failure")

    monkeypatch.setattr(Image, "open", mock_open)

    res = extract_to_pil(StubXObj())
    assert res is None
    assert open_called is True
