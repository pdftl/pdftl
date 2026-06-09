import pikepdf
from PIL import Image
from pdftl.utils.images import encoders


def test_serialize_grayscale_stream_handles_unsupported_pil_formats():
    """PIL fails to convert a 0x0 canvas to grayscale, serialize returns False."""
    pdf = pikepdf.Pdf.new()
    xobj = pdf.make_stream(b"")
    xobj[pikepdf.Name("/ColorSpace")] = pikepdf.Name("/DeviceRGB")

    broken_canvas = Image.new("RGB", (0, 0))

    result = encoders.serialize_grayscale_stream(xobj, broken_canvas, "dctdecode", 75)

    assert result is False


class ForbiddenDict(dict):
    """Triggers an error when setting a value."""

    def __setitem__(self, key, value):
        raise pikepdf.PdfError("Mutation locked")


def test_encoders_lines_18_20_handles_none_image_metadata_only():
    """Covers lines 18-20: If image data is missing, metadata fallback forces gray."""
    fake_xobj = {}
    status = encoders.serialize_grayscale_stream(fake_xobj, None, "dctdecode", 75)

    assert status is True
    assert fake_xobj[pikepdf.Name("/ColorSpace")] == pikepdf.Name("/DeviceGray")


def test_encoders_lines_21_22_handles_none_image_mutation_failure():
    """Covers lines 21-22: Dictionary block failures return False gracefully."""
    bad_xobj = ForbiddenDict()
    status = encoders.serialize_grayscale_stream(bad_xobj, None, "dctdecode", 75)

    assert status is False
