import pytest
import pikepdf
from PIL import Image
from pdftl.utils.images import core


def test_convert_image_dict_to_grayscale_aborts_on_missing_metadata():
    """Covers lines 28-29: Missing extraction metadata dict handles abort pathing cleanly."""
    bad_meta = {}  # Lacks 'xobj' asset structures entirely
    with pytest.raises(KeyError):
        core.convert_image_dict_to_grayscale(bad_meta, 75)


def test_convert_image_dict_to_grayscale_handles_invalid_selector_payloads(monkeypatch):
    """Covers line 45: Gracefully processes a terminal None outcome from extract_to_pil."""
    monkeypatch.setattr(core, "extract_to_pil", lambda x: None)

    img_meta = {"xobj": {"/Width": 10, "/Height": 10}, "format": "dctdecode"}
    res = core.convert_image_dict_to_grayscale(img_meta, 75)

    assert res is False


def test_convert_image_dict_to_grayscale_catches_encoder_stream_dropouts(monkeypatch):
    """Covers lines 59-61: Orchestrator aborts processing safely if serialization yields empty payloads."""
    from PIL import Image

    monkeypatch.setattr(core, "extract_to_pil", lambda x: Image.new("RGB", (10, 10)))
    monkeypatch.setattr(core, "serialize_grayscale_stream", lambda *a, **kw: False)

    img_meta = {"xobj": {"/Width": 10, "/Height": 10}, "format": "dctdecode"}
    res = core.convert_image_dict_to_grayscale(img_meta, 75)

    assert res is False


class MockBadArray(pikepdf.Array):
    """Triggers an internal pikepdf mutation exception."""

    def __setitem__(self, key, value):
        raise pikepdf.PdfError("Immutable matrix allocation failure")


def test_core_lines_28_29_handles_colorspace_mutation_failure():
    """Covers lines 28-29: Array mutation crashes abort safely with False."""
    import pikepdf
    from pdftl.utils.images import core

    # Define an object that can mimic a pikepdf Stream and handle type-checking assertions
    class MockStreamWithBadArray:
        def __init__(self):
            # /ColorSpace returns a pikepdf.Array instance to bypass line 24 check
            self._cs = pikepdf.Array([pikepdf.Name("/Separation")])

        def __getitem__(self, key):
            if key == "/ColorSpace":
                return self._cs
            return 10  # Fallback for width/height queries

        def get(self, key, default=None):
            if key in ("/ImageMask", "/BitsPerComponent"):
                return default
            if key == "/ColorSpace":
                return self._cs
            return 10

        def __setitem__(self, key, value):
            # REAL FIX: Throw a real pikepdf.PdfError when core.py tries to
            # execute xobj[pikepdf.Name("/ColorSpace")] = pikepdf.Name("/DeviceGray")
            if isinstance(key, pikepdf.Name) and key == pikepdf.Name("/ColorSpace"):
                raise pikepdf.PdfError("Simulated write block on stream dictionary layout table")

        def read_bytes(self):
            return b""

    img_meta = {"xobj": MockStreamWithBadArray(), "format": "dctdecode"}

    # Runs directly into the try/except block on lines 25-29 and safely returns False
    assert core.convert_image_dict_to_grayscale(img_meta, 75) is False


def test_core_line_45_missing_colorspace_after_decoding(monkeypatch):
    """Covers line 45: Handles late removal of ColorSpace cleanly."""
    fake_xobj = {"/Width": 10, "/Height": 10, "/ColorSpace": pikepdf.Name("/DeviceRGB")}

    # 1. Mock decoder to succeed
    monkeypatch.setattr(core, "extract_to_pil", lambda x: Image.new("RGB", (10, 10)))

    # 2. Maliciously pop /ColorSpace right before step 45 evaluation
    def mock_extract_and_sabotage(x):
        del fake_xobj["/ColorSpace"]
        return Image.new("RGB", (10, 10))

    monkeypatch.setattr(core, "extract_to_pil", mock_extract_and_sabotage)

    img_meta = {"xobj": fake_xobj, "format": "dctdecode"}
    assert core.convert_image_dict_to_grayscale(img_meta, 75) is False


def test_core_lines_59_61_neutralizes_nested_smask(monkeypatch):
    """Covers lines 59-61: Neutralizes internal transparency channels."""
    # Create a real, minimal document context to let pikepdf build a valid Stream object
    pdf = pikepdf.new()
    smask_stream = pdf.make_stream(b"")
    smask_stream.ColorSpace = pikepdf.Name("/DeviceRGB")

    fake_xobj = pdf.make_stream(b"")
    fake_xobj["/Width"] = 10
    fake_xobj["/Height"] = 10
    fake_xobj["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
    fake_xobj["/SMask"] = smask_stream

    monkeypatch.setattr(core, "extract_to_pil", lambda x: Image.new("RGB", (10, 10)))
    monkeypatch.setattr(core, "serialize_grayscale_stream", lambda *a: True)

    img_meta = {"xobj": fake_xobj, "format": "dctdecode"}

    assert core.convert_image_dict_to_grayscale(img_meta, 75) is True
    # Confirm the nested mask was forced to gray
    assert smask_stream.ColorSpace == pikepdf.Name("/DeviceGray")
