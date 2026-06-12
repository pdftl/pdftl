import io

import pikepdf
import pytest

from PIL import Image

from pdftl.utils.images import (
    extract_pdf_images,
    convert_image_dict_to_grayscale,
)
from pdftl.utils.images.finders import _get_format, _extract_image_metadata


# ==============================================================================
# FIXTURES
# ==============================================================================


@pytest.fixture
def empty_pdf():
    with pikepdf.Pdf.new() as pdf:
        yield pdf


# ==============================================================================
# HELPERS
# ==============================================================================


def create_real_image_stream(
    pdf,
    mode="RGB",
    size=(10, 10),
    fmt="JPEG",
    extra_entries=None,
):
    """
    Create a genuine Image XObject using direct dictionary assignment.

    This avoids pikepdf-version-specific behaviour when passing PDF names
    through make_stream(..., **kwargs).
    """
    img = Image.new(
        mode,
        size,
        color="red" if mode == "RGB" else 128,
    )

    buf = io.BytesIO()

    if fmt == "JPEG":
        img.save(buf, format="JPEG")
        payload = buf.getvalue()
        filter_value = pikepdf.Name("/DCTDecode")
    else:
        payload = img.tobytes()
        filter_value = None

    stream = pdf.make_stream(payload)

    stream["/Type"] = pikepdf.Name("/XObject")
    stream["/Subtype"] = pikepdf.Name("/Image")
    stream["/Width"] = size[0]
    stream["/Height"] = size[1]
    stream["/BitsPerComponent"] = 8 if mode != "1" else 1
    stream["/ColorSpace"] = pikepdf.Name("/DeviceGray" if mode in ("L", "1") else "/DeviceRGB")

    if filter_value is not None:
        stream["/Filter"] = filter_value

    if extra_entries:
        for k, v in extra_entries.items():
            stream[k] = v

    return stream


def create_form_xobject(pdf, image_xobj):
    form = pdf.make_stream(b"")

    form["/Type"] = pikepdf.Name("/XObject")
    form["/Subtype"] = pikepdf.Name("/Form")
    form["/BBox"] = pikepdf.Array([0, 0, 100, 100])
    form["/Matrix"] = pikepdf.Array([2.0, 0.0, 0.0, 2.0, 10.0, 20.0])

    form["/Resources"] = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary(
                {
                    "/ImgInForm": image_xobj,
                }
            )
        }
    )

    return form


# ==============================================================================
# extract_pdf_images
# ==============================================================================


def test_extract_pdf_images_empty(empty_pdf):
    empty_pdf.add_blank_page()
    assert extract_pdf_images(empty_pdf, [1]) == []


def test_extract_pdf_images_standard_image(empty_pdf):
    """
    Tests the primary image extraction path without relying on recursive Form
    handling (which currently appears broken in images).
    """
    page = empty_pdf.add_blank_page()

    img_xobj = create_real_image_stream(empty_pdf)

    page.Resources = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary(
                {
                    "/DirectImg": img_xobj,
                }
            )
        }
    )

    page.Contents = empty_pdf.make_stream(b"q 2 0 0 2 5 5 cm /DirectImg Do Q")

    result = extract_pdf_images(empty_pdf, [1])

    assert len(result) == 1

    img = result[0]

    assert img["name"] == "/DirectImg"
    assert img["width_px"] == 10
    assert img["height_px"] == 10
    assert img["format"] == "dctdecode"


def test_extract_pdf_images_filter_array(empty_pdf):
    page = empty_pdf.add_blank_page()

    img_xobj = create_real_image_stream(
        empty_pdf,
        extra_entries={
            "/Filter": pikepdf.Array(
                [
                    pikepdf.Name("/FlateDecode"),
                    pikepdf.Name("/ASCII85Decode"),
                ]
            )
        },
    )

    page.Resources = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary(
                {
                    "/ArrayImg": img_xobj,
                }
            )
        }
    )

    page.Contents = empty_pdf.make_stream(b"/ArrayImg Do")

    result = extract_pdf_images(empty_pdf, [1])

    assert len(result) == 1
    assert result[0]["format"] == "flatedecode"


def test_extract_pdf_images_unreadable_stream_bytes(empty_pdf):
    """
    Regression test: extraction should not crash if stream-byte access fails.
    """
    page = empty_pdf.add_blank_page()

    bad_img = pikepdf.Stream(empty_pdf, b"CORRUPTED")

    bad_img["/Type"] = pikepdf.Name("/XObject")
    bad_img["/Subtype"] = pikepdf.Name("/Image")
    bad_img["/Width"] = 10
    bad_img["/Height"] = 10
    bad_img["/BitsPerComponent"] = 8
    bad_img["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
    bad_img["/Filter"] = pikepdf.Name("/FlateDecode")

    page.Resources = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary(
                {
                    "/BadImg": bad_img,
                }
            )
        }
    )

    page.Contents = empty_pdf.make_stream(b"/BadImg Do")

    result = extract_pdf_images(empty_pdf, [1])

    assert len(result) == 1


def test_extract_pdf_images_malformed_stream_catch(empty_pdf):
    page = empty_pdf.add_blank_page()

    page.Resources = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary(),
        }
    )

    page.Contents = empty_pdf.make_stream(b"1 cm")

    assert extract_pdf_images(empty_pdf, [1]) == []


# ==============================================================================
# convert_image_dict_to_grayscale
# ==============================================================================


@pytest.mark.parametrize(
    "extra_entries",
    [
        {"/ImageMask": True},
        {"/BitsPerComponent": 1},
    ],
)
def test_convert_grayscale_structural_guards(
    empty_pdf,
    extra_entries,
):
    img_xobj = create_real_image_stream(
        empty_pdf,
        extra_entries=extra_entries,
    )

    img_meta = {
        "xobj": img_xobj,
        "format": "png",
    }

    assert convert_image_dict_to_grayscale(img_meta, 75) is False


@pytest.mark.parametrize(
    "colorspace",
    [
        pikepdf.Array(
            [
                pikepdf.Name("/Separation"),
                pikepdf.Name("/Pantone Color"),
                pikepdf.Name("/DeviceRGB"),
            ]
        ),
        pikepdf.Array(
            [
                pikepdf.Name("/DeviceN"),
                pikepdf.Name("/Custom Spot"),
                pikepdf.Name("/DeviceCMYK"),
            ]
        ),
        pikepdf.Array(
            [
                pikepdf.Name("/Separation"),
                pikepdf.Name("/Pantone Custom"),
                pikepdf.Name("/DeviceGray"),
            ]
        ),
    ],
)
def test_convert_grayscale_neutralize_print_colorspace(
    empty_pdf,
    colorspace,
):
    """
    We only require graceful handling.

    Actual success depends on pikepdf's ability to decode the synthetic image.
    """
    img_xobj = create_real_image_stream(empty_pdf)

    img_xobj["/ColorSpace"] = colorspace

    result = convert_image_dict_to_grayscale(
        {
            "xobj": img_xobj,
            "format": "dctdecode",
        },
        75,
    )

    assert result is False


def test_convert_grayscale_decode_to_pil_errors(empty_pdf):
    # Provide 300 bytes of junk data so Pillow doesn't bail early on raw size checks,
    # but keep /Filter as /FlateDecode so decompression explodes natively.
    broken = empty_pdf.make_stream(b"JUNK_DATA" * 40)

    broken["/Type"] = pikepdf.Name("/XObject")
    broken["/Subtype"] = pikepdf.Name("/Image")
    broken["/Width"] = 10
    broken["/Height"] = 10
    broken["/BitsPerComponent"] = 8
    broken["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
    broken["/Filter"] = pikepdf.Name("/FlateDecode")  # Force extraction logic to decode it

    result = convert_image_dict_to_grayscale(
        {
            "xobj": broken,
            "format": "png",
        },
        75,
    )

    assert result is False


def test_convert_grayscale_already_grayscale(empty_pdf):
    img_xobj = create_real_image_stream(
        empty_pdf,
        mode="L",
    )

    result = convert_image_dict_to_grayscale(
        {
            "xobj": img_xobj,
            "format": "dctdecode",
        },
        75,
    )

    assert result is False


def test_convert_grayscale_jpeg_path_no_exception(empty_pdf):
    """
    Do not require successful mutation because PdfImage support varies
    significantly between pikepdf releases.
    """
    img_xobj = create_real_image_stream(
        empty_pdf,
        mode="RGB",
        fmt="JPEG",
    )

    result = convert_image_dict_to_grayscale(
        {
            "xobj": img_xobj,
            "format": "dctdecode",
        },
        80,
    )

    assert result is True


def test_convert_grayscale_flate_path_no_exception(empty_pdf):
    img_xobj = create_real_image_stream(
        empty_pdf,
        mode="RGB",
        fmt="RAW",
    )

    result = convert_image_dict_to_grayscale(
        {
            "xobj": img_xobj,
            "format": "png",
        },
        75,
    )

    assert result is True


def test_convert_grayscale_strip_decode_parms_when_successful(empty_pdf):
    img_xobj = create_real_image_stream(
        empty_pdf,
        mode="RGB",
        fmt="JPEG",
    )
    img_xobj["/DecodeParms"] = pikepdf.Dictionary(
        {
            "/Predictor": 1,
        }
    )
    result = convert_image_dict_to_grayscale(
        {
            "xobj": img_xobj,
            "format": "dctdecode",
        },
        75,
    )
    assert result is True
    assert "/DecodeParms" not in img_xobj


def test_extract_pdf_images_form_recursion(empty_pdf):
    """
    Verifies recursive tree traversal through Form XObjects into nested elements.
    Concurrently tests correct signature validation across _process_form_xobject.
    """
    page = empty_pdf.add_blank_page()

    img_xobj = create_real_image_stream(empty_pdf)
    form_xobj = create_form_xobject(empty_pdf, img_xobj)

    # Inject a draw operation inside the Form's contents payload to reference the child image
    form_xobj.write(b"/ImgInForm Do")

    page.Resources = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary(
                {
                    "/NestedForm": form_xobj,
                }
            )
        }
    )

    page.Contents = empty_pdf.make_stream(b"q 3 0 0 3 0 0 cm /NestedForm Do Q")

    result = extract_pdf_images(empty_pdf, [1])

    assert len(result) == 1
    assert result[0]["name"] == "/ImgInForm"
    assert result[0]["width_px"] == 10
    assert result[0]["height_px"] == 10


# ==============================================================================
# COVERAGE GAP TESTS
# ==============================================================================


def test_get_format_unknown_filter(empty_pdf):
    """Covers line 43: _get_format returns 'unknown' if /Filter is entirely absent."""
    img_xobj = create_real_image_stream(empty_pdf, fmt="RAW")
    if "/Filter" in img_xobj:
        del img_xobj["/Filter"]

    # Removed the second positional argument
    assert _get_format(img_xobj) == "unknown"


def test_extract_image_metadata_stream_bytes_exception(empty_pdf, monkeypatch):
    """Covers line: read_raw_bytes raising an exception sets stream_bytes to 0."""

    # Pure Python substitute that mimics a pikepdf Stream object without C++ bindings
    class MockXObj:
        objgen = (99, 0)

        def get(self, key, default=None):
            if key in ("/Width", "/Height"):
                return 10
            return default

        def read_raw_bytes(self):
            raise ValueError("Simulated read crash")

    image_list = []
    # Test the metadata extraction step directly with our mock object
    _extract_image_metadata(MockXObj(), "Im1", [1, 0, 0, 1, 0, 0], None, image_list)

    assert image_list[0]["stream_bytes"] == 0


def test_handle_do_operator_edge_cases(empty_pdf):
    """Covers lines 99 and 103: Handling empty resources or missing keys gracefully."""
    page = empty_pdf.add_blank_page()

    # Case 1: Page resources has no /XObject key at all (Covers line 99)
    page.Resources = pikepdf.Dictionary()
    page.Contents = empty_pdf.make_stream(b"/MissingObject Do")
    assert extract_pdf_images(empty_pdf, [1]) == []

    # Case 2: /XObject dict exists, but token key is missing (Covers line 103)
    page.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary()})
    assert extract_pdf_images(empty_pdf, [1]) == []


def test_parse_stream_exception_catch(empty_pdf, monkeypatch):
    """Covers line 133 (via 148 context): _parse_stream handles token processing exceptions gracefully."""

    # Force pikepdf to throw an error inside the token iterator
    def mock_parse_content_stream(*args, **kwargs):
        raise TypeError("Simulated content stream corruption")

    monkeypatch.setattr(pikepdf, "parse_content_stream", mock_parse_content_stream)

    page = empty_pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary()
    page.Contents = empty_pdf.make_stream(b"q Q")

    # Should catch error, log a warning, and return empty list cleanly without crashing
    assert extract_pdf_images(empty_pdf, [1]) == []


def test_decode_to_pil_not_implemented_exception(empty_pdf, monkeypatch):
    """Covers lines 196-202: Handling NotImplementedError when parsing a layout format."""

    def mock_as_pil_image(*args, **kwargs):
        raise NotImplementedError("Format unsupported by engine")

    monkeypatch.setattr(pikepdf.PdfImage, "as_pil_image", mock_as_pil_image)

    img_xobj = create_real_image_stream(empty_pdf)
    img_meta = {"xobj": img_xobj, "format": "dctdecode"}

    assert convert_image_dict_to_grayscale(img_meta, 75) is False


def test_convert_grayscale_pil_conversion_value_error(empty_pdf, monkeypatch):
    """Covers lines 244-248: PIL failing to downsample or convert color channels to 'L'."""
    from PIL import Image

    def mock_convert(*args, **kwargs):
        raise ValueError("Matrix downsample channel mismatch conversion failure")

    monkeypatch.setattr(Image.Image, "convert", mock_convert)

    img_xobj = create_real_image_stream(empty_pdf)
    img_meta = {"xobj": img_xobj, "format": "dctdecode"}

    assert convert_image_dict_to_grayscale(img_meta, 75) is False


def test_convert_grayscale_payload_none_guard(empty_pdf, monkeypatch):
    """Verifies that orchestrator handles serialization aborts cleanly."""
    # Target the orchestrator core module where execution happens
    import pdftl.utils.images.core as core

    monkeypatch.setattr(core, "serialize_grayscale_stream", lambda *a, **kw: False)

    img_xobj = create_real_image_stream(empty_pdf)
    img_meta = {"xobj": img_xobj, "format": "dctdecode"}

    assert core.convert_image_dict_to_grayscale(img_meta, 75) is False


def test_convert_grayscale_flush_mutation_pdf_error(empty_pdf, monkeypatch):
    """Covers lines 262, 264-268: Stream mutation commit throws a direct PdfError."""
    img_xobj = create_real_image_stream(empty_pdf)
    img_meta = {"xobj": img_xobj, "format": "dctdecode"}

    original_name_init = pikepdf.Name

    # Intercept Name creation inside the target block to safely raise the error
    def mock_name_constructor(value, *args, **kwargs):
        if value == "/DeviceGray":
            raise pikepdf.PdfError("Simulated write layout lock violation")
        return original_name_init(value, *args, **kwargs)

    monkeypatch.setattr(pikepdf, "Name", mock_name_constructor)

    assert convert_image_dict_to_grayscale(img_meta, 75) is False


def test_convert_grayscale_strip_decode_parms_unconditional(empty_pdf):
    """Covers line 262: Confirms /DecodeParms is actively deleted upon successful conversion."""
    img_xobj = create_real_image_stream(empty_pdf, mode="RGB", fmt="JPEG")
    img_xobj["/DecodeParms"] = pikepdf.Dictionary({"/Predictor": 15})
    img_meta = {"xobj": img_xobj, "format": "dctdecode"}

    assert convert_image_dict_to_grayscale(img_meta, 75) is True
    assert "/DecodeParms" not in img_xobj


def test_encode_grayscale_payload_oserror_catch(empty_pdf, monkeypatch):
    """Covers lines 218-220: Verifies re-encode loop catches standard system errors gracefully."""
    img_xobj = create_real_image_stream(empty_pdf, mode="RGB", fmt="JPEG")
    img_meta = {"xobj": img_xobj, "format": "dctdecode"}

    original_name_init = pikepdf.Name

    def mock_name_error(value, *args, **kwargs):
        # Intercept the target output token inside the serialization step
        if value in ("/DCTDecode", "/FlateDecode"):
            raise OSError("Simulated low-level disk buffer or serialization fault")
        return original_name_init(value, *args, **kwargs)

    monkeypatch.setattr(pikepdf, "Name", mock_name_error)

    assert convert_image_dict_to_grayscale(img_meta, 75) is False


def test_encode_grayscale_payload_exceptions(empty_pdf, monkeypatch):
    from PIL import Image

    img_xobj = create_real_image_stream(empty_pdf, fmt="JPEG")
    img_meta = {"xobj": img_xobj, "format": "png"}

    # Break PIL.tobytes instead (forces internal exception path)
    def broken_tobytes(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(Image.Image, "tobytes", broken_tobytes)

    assert convert_image_dict_to_grayscale(img_meta, 75) is False


def test_neutralize_print_colorspace_edge_case1(empty_pdf):
    """Covers lines 164 & 182: Missing ColorSpace, or deep fallback branch logic."""
    img_xobj = create_real_image_stream(empty_pdf)

    # Case 1: No ColorSpace dictionary exists at all (Covers line 164)
    del img_xobj["/ColorSpace"]
    img_meta = {"xobj": img_xobj, "format": "dctdecode"}
    assert convert_image_dict_to_grayscale(img_meta, 75) is False


def test_neutralize_print_colorspace_edge_case2(empty_pdf):
    # Case 2: Array color layout uses /Separation but lacks standard alt spaces
    img_xobj_alt = create_real_image_stream(empty_pdf)
    img_xobj_alt["/ColorSpace"] = pikepdf.Array(
        [
            pikepdf.Name("/Separation"),
            pikepdf.Name("/PantoneCustom"),
            pikepdf.Name("/Lab"),
        ]
    )

    # Bypass the crash: Remove the filter so pikepdf doesn't try to decompress dummy bytes
    if "/Filter" in img_xobj_alt:
        del img_xobj_alt["/Filter"]

    img_meta_alt = {"xobj": img_xobj_alt, "format": "dctdecode"}

    # Run the assertion natively without any patching!
    assert convert_image_dict_to_grayscale(img_meta_alt, 75) is True
