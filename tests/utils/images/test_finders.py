# tests/utils/images/test_finders.py
import io
import pikepdf
import pytest
from PIL import Image

from pdftl.utils.images.finders import extract_pdf_images, _get_format, _extract_image_metadata


@pytest.fixture
def empty_pdf():
    with pikepdf.Pdf.new() as pdf:
        yield pdf


def create_real_image_stream(pdf, mode="RGB", size=(10, 10), fmt="JPEG", extra_entries=None):
    img = Image.new(mode, size, color="red" if mode == "RGB" else 128)
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
        {"/XObject": pikepdf.Dictionary({"/ImgInForm": image_xobj})}
    )
    return form


def test_extract_pdf_images_empty(empty_pdf):
    empty_pdf.add_blank_page()
    assert extract_pdf_images(empty_pdf, [1]) == []


def test_extract_pdf_images_standard_image(empty_pdf):
    page = empty_pdf.add_blank_page()
    img_xobj = create_real_image_stream(empty_pdf)
    page.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/DirectImg": img_xobj})})
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
                [pikepdf.Name("/FlateDecode"), pikepdf.Name("/ASCII85Decode")]
            )
        },
    )
    page.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/ArrayImg": img_xobj})})
    page.Contents = empty_pdf.make_stream(b"/ArrayImg Do")

    result = extract_pdf_images(empty_pdf, [1])
    assert len(result) == 1
    assert result[0]["format"] == "flatedecode"


def test_extract_pdf_images_unreadable_stream_bytes(empty_pdf):
    page = empty_pdf.add_blank_page()
    bad_img = pikepdf.Stream(empty_pdf, b"CORRUPTED")
    bad_img["/Type"] = pikepdf.Name("/XObject")
    bad_img["/Subtype"] = pikepdf.Name("/Image")
    bad_img["/Width"] = 10
    bad_img["/Height"] = 10
    bad_img["/BitsPerComponent"] = 8
    bad_img["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
    bad_img["/Filter"] = pikepdf.Name("/FlateDecode")

    page.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/BadImg": bad_img})})
    page.Contents = empty_pdf.make_stream(b"/BadImg Do")

    result = extract_pdf_images(empty_pdf, [1])
    assert len(result) == 1


def test_extract_pdf_images_malformed_stream_catch(empty_pdf):
    page = empty_pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary()})
    page.Contents = empty_pdf.make_stream(b"1 cm")
    assert extract_pdf_images(empty_pdf, [1]) == []


def test_extract_pdf_images_form_recursion(empty_pdf):
    page = empty_pdf.add_blank_page()
    img_xobj = create_real_image_stream(empty_pdf)
    form_xobj = create_form_xobject(empty_pdf, img_xobj)
    form_xobj.write(b"/ImgInForm Do")

    page.Resources = pikepdf.Dictionary(
        {"/XObject": pikepdf.Dictionary({"/NestedForm": form_xobj})}
    )
    page.Contents = empty_pdf.make_stream(b"q 3 0 0 3 0 0 cm /NestedForm Do Q")

    result = extract_pdf_images(empty_pdf, [1])
    assert len(result) == 1
    assert result[0]["name"] == "/ImgInForm"


def test_get_format_unknown_filter(empty_pdf):
    img_xobj = create_real_image_stream(empty_pdf, fmt="RAW")
    if "/Filter" in img_xobj:
        del img_xobj["/Filter"]
    assert _get_format(img_xobj) == "unknown"


def test_extract_image_metadata_stream_bytes_exception(empty_pdf):
    class MockXObj:
        objgen = (99, 0)

        def get(self, key, default=None):
            if key in ("/Width", "/Height"):
                return 10
            return default

        def read_raw_bytes(self):
            raise ValueError("Simulated read crash")

    image_list = []
    _extract_image_metadata(MockXObj(), "Im1", [1, 0, 0, 1, 0, 0], None, image_list)
    assert image_list[0]["stream_bytes"] == 0


def test_handle_do_operator_edge_cases(empty_pdf):
    page = empty_pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary()
    page.Contents = empty_pdf.make_stream(b"/MissingObject Do")
    assert extract_pdf_images(empty_pdf, [1]) == []

    page.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary()})
    assert extract_pdf_images(empty_pdf, [1]) == []


def test_parse_stream_exception_catch(empty_pdf, monkeypatch):
    def mock_parse_content_stream(*args, **kwargs):
        raise TypeError("Simulated content stream corruption")

    monkeypatch.setattr(pikepdf, "parse_content_stream", mock_parse_content_stream)
    page = empty_pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary()
    page.Contents = empty_pdf.make_stream(b"q Q")
    assert extract_pdf_images(empty_pdf, [1]) == []
