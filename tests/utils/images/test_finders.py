# tests/utils/images/test_finders.py
import io
import pikepdf
import pytest
from PIL import Image

from pdftl.utils.images.finders import (
    extract_pdf_images,
    _get_format,
    _extract_image_metadata,
    _read_stream_bytes,
)


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


def test_get_format_empty_filter_array(empty_pdf):
    img_xobj = create_real_image_stream(
        empty_pdf, fmt="RAW", extra_entries={"/Filter": pikepdf.Array([])}
    )
    assert _get_format(img_xobj) == "unknown"


def test_extract_pdf_images_empty_filter_array_does_not_crash(empty_pdf):
    """Regression: `/Filter []` used to raise IndexError, which the content
    stream parser doesn't catch, aborting the whole crawl."""
    page = empty_pdf.add_blank_page()
    img_xobj = create_real_image_stream(
        empty_pdf, fmt="RAW", extra_entries={"/Filter": pikepdf.Array([])}
    )
    page.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/EmptyF": img_xobj})})
    page.Contents = empty_pdf.make_stream(b"/EmptyF Do")

    result = extract_pdf_images(empty_pdf, [1])
    assert [r["format"] for r in result] == ["unknown"]


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


def test_extract_pdf_images_sees_inherited_page_resources(empty_pdf):
    """Fails on current code: a page whose /Resources is only inherited
    from a /Pages ancestor (not its own dict) raises AttributeError on
    `page.Resources`, which the old code suppressed -- silently reporting
    zero images for that page instead of finding the image."""
    page = empty_pdf.add_blank_page()
    parent = page.obj["/Parent"]

    img_xobj = create_real_image_stream(empty_pdf)
    parent["/Resources"] = pikepdf.Dictionary(
        {"/XObject": pikepdf.Dictionary({"/InheritedImg": img_xobj})}
    )
    if "/Resources" in page.obj:
        del page.obj["/Resources"]

    page.Contents = empty_pdf.make_stream(b"q 2 0 0 2 5 5 cm /InheritedImg Do Q")

    result = extract_pdf_images(empty_pdf, [1])
    assert len(result) == 1
    assert result[0]["name"] == "/InheritedImg"


def test_extract_pdf_images_page_with_no_resources_anywhere(empty_pdf):
    """Covers page_resources is None branch: no /Resources in the page's
    own dict and none inherited -- _parse_stream must simply be skipped."""
    page = empty_pdf.add_blank_page()
    if "/Resources" in page.obj:
        del page.obj["/Resources"]
    if "/Resources" in empty_pdf.Root.Pages:
        del empty_pdf.Root.Pages["/Resources"]
    page.Contents = empty_pdf.make_stream(b"q Q")

    assert extract_pdf_images(empty_pdf, [1]) == []


def test_parse_stream_unbalanced_q_pop_guard(empty_pdf):
    """Covers the 'Q with empty ctm_stack' branch: an unbalanced Q with no
    prior q must not raise (the pop is guarded by `if ctm_stack`)."""
    from pdftl.utils.images.finders import _parse_stream

    image_list = []
    stream = empty_pdf.make_stream(b"Q 1 0 0 1 0 0 cm")
    _parse_stream(stream, pikepdf.Dictionary(), [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], image_list)
    assert image_list == []


def test_parse_stream_do_missing_xobject_continues(empty_pdf):
    """Covers the loop-continues-after-Do branch: a Do referencing an
    XObject name absent from resources must be a no-op, and parsing must
    continue to subsequent instructions rather than stopping."""
    from pdftl.utils.images.finders import _parse_stream

    resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary()})
    image_list = []
    stream = empty_pdf.make_stream(b"/Missing Do 1 0 0 1 0 0 cm")
    _parse_stream(stream, resources, [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], image_list)
    assert image_list == []


def test_parse_stream_multiple_do_instructions_loop_continues(empty_pdf):
    """Covers the Do-branch-completes-then-loop-continues arc directly:
    two consecutive Do instructions, both resolving to a real Image
    XObject, must both be processed rather than the loop stopping after
    the first."""
    from pdftl.utils.images.finders import _parse_stream

    img1 = create_real_image_stream(empty_pdf)
    img2 = create_real_image_stream(empty_pdf)
    resources = pikepdf.Dictionary(
        {"/XObject": pikepdf.Dictionary({"/Img1": img1, "/Img2": img2})}
    )
    image_list = []
    stream = empty_pdf.make_stream(b"/Img1 Do /Img2 Do")
    _parse_stream(stream, resources, [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], image_list)
    assert len(image_list) == 2


def test_parse_stream_operator_outside_tracked_set_is_ignored(empty_pdf):
    """Covers the elif-chain falling all the way through: an operator
    that is none of q/Q/cm/Do (e.g. text-showing ops) must be silently
    ignored and the loop must simply continue to the next instruction."""
    from pdftl.utils.images.finders import _parse_stream

    image_list = []
    stream = empty_pdf.make_stream(b"BT (Hello) Tj ET")
    _parse_stream(stream, pikepdf.Dictionary(), [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], image_list)
    assert image_list == []


def test_handle_do_operator_skips_non_image_non_form_subtype(empty_pdf):
    """Covers the subtype-is-neither-Image-nor-Form fall-through in
    _handle_do_operator (e.g. a malformed XObject with no /Subtype)."""
    from pdftl.utils.images.finders import _handle_do_operator

    xobj = empty_pdf.make_stream(b"")
    xobj["/Type"] = pikepdf.Name("/XObject")
    # Deliberately no /Subtype set.
    resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/X1": xobj})})
    image_list = []
    _handle_do_operator(pikepdf.Name("/X1"), resources, [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], image_list)
    assert image_list == []


def test_process_form_xobject_no_matrix_uses_identity(empty_pdf):
    """Covers the 'no /Matrix key' branch: a Form XObject lacking /Matrix
    must fall back to the identity matrix rather than raising."""
    from pdftl.utils.images.finders import _process_form_xobject

    img_xobj = create_real_image_stream(empty_pdf)
    form = empty_pdf.make_stream(b"/Img Do")
    form["/Type"] = pikepdf.Name("/XObject")
    form["/Subtype"] = pikepdf.Name("/Form")
    form["/Resources"] = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Img": img_xobj})})
    assert "/Matrix" not in form

    image_list = []
    _process_form_xobject(form, pikepdf.Dictionary(), [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], image_list)
    assert len(image_list) == 1


def test_extract_image_metadata_with_smask(empty_pdf):
    """Covers the SMask stencil branch in _extract_mask_metadata: an image
    with a real /SMask stream must produce a populated 'mask' entry."""
    page = empty_pdf.add_blank_page()
    img_xobj = create_real_image_stream(empty_pdf)

    smask_xobj = create_real_image_stream(empty_pdf, mode="L", size=(10, 10), fmt="RAW")
    img_xobj["/SMask"] = smask_xobj

    page.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/DirectImg": img_xobj})})
    page.Contents = empty_pdf.make_stream(b"q 2 0 0 2 5 5 cm /DirectImg Do Q")

    result = extract_pdf_images(empty_pdf, [1])
    assert len(result) == 1
    mask = result[0]["mask"]
    assert mask["role"] == "smask"
    assert mask["width_px"] == 10
    assert mask["height_px"] == 10
    assert mask["bits"] == 8
    assert mask["format"] == "unknown"


def test_extract_image_metadata_with_mask_stencil(empty_pdf):
    """Covers the /Mask (not /SMask) stencil branch."""
    page = empty_pdf.add_blank_page()
    img_xobj = create_real_image_stream(empty_pdf)

    mask_xobj = empty_pdf.make_stream(b"CORRUPTED")
    mask_xobj["/Type"] = pikepdf.Name("/XObject")
    mask_xobj["/Subtype"] = pikepdf.Name("/Image")
    mask_xobj["/Width"] = 5
    mask_xobj["/Height"] = 5
    mask_xobj["/BitsPerComponent"] = 1
    img_xobj["/Mask"] = mask_xobj

    page.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/DirectImg": img_xobj})})
    page.Contents = empty_pdf.make_stream(b"/DirectImg Do")

    result = extract_pdf_images(empty_pdf, [1])
    assert len(result) == 1
    mask = result[0]["mask"]
    assert mask["role"] == "stencil"
    assert mask["width_px"] == 5
    assert mask["height_px"] == 5
    assert mask["stream_bytes"] == len(b"CORRUPTED")


class TestReadStreamBytes:
    def test_returns_raw_length(self, empty_pdf):
        assert _read_stream_bytes(empty_pdf.make_stream(b"abcde")) == 5

    @pytest.mark.parametrize("exc", [ValueError("boom"), pikepdf.PdfError("boom")])
    def test_unreadable_stream_returns_zero(self, exc):
        class Unreadable:
            def read_raw_bytes(self):
                raise exc

        assert _read_stream_bytes(Unreadable()) == 0


def test_extract_mask_metadata_color_key_array(empty_pdf):
    """Covers the color-key masking branch: /Mask as an Array of ranges
    rather than a stencil stream must yield no mask metadata."""
    img_xobj = create_real_image_stream(empty_pdf)
    img_xobj["/Mask"] = pikepdf.Array([0, 1, 0, 1, 0, 1])

    image_list = []
    _extract_image_metadata(img_xobj, "Im1", [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], None, image_list)
    assert "mask" not in image_list[0]


def _inline_image_stream_bytes(width=2, height=2, cs="RGB", bpc=8, filt=None):
    """Raw BI/ID/EI bytes for an UNFILTERED inline image -- exact-length
    raw data (no compression) so pikepdf's tokenizer can find EI by
    counting bytes rather than needing real filter-aware decoding, which
    these tests have no need to exercise."""
    channels = {"G": 1, "RGB": 3, "CMYK": 4}.get(cs, 3)
    data = bytes([128]) * (width * height * channels * max(bpc // 8, 1))
    filt_part = f" /F /{filt}" if filt else ""
    header = f"BI /W {width} /H {height} /BPC {bpc} /CS /{cs}{filt_part} ID ".encode("ascii")
    return header + data + b" EI"


def test_extract_pdf_images_inline_image_basic(empty_pdf):
    page = empty_pdf.add_blank_page()
    content = (
        b"q 2 0 0 2 5 5 cm "
        + _inline_image_stream_bytes(width=2, height=2, cs="RGB", bpc=8)
        + b" Q"
    )
    page.Resources = pikepdf.Dictionary()
    page.Contents = empty_pdf.make_stream(content)

    result = extract_pdf_images(empty_pdf, [1])
    assert len(result) == 1
    img = result[0]
    assert img["inline"] is True
    assert img.get("name") is None
    assert "obj_id" not in img
    assert "xobj" not in img
    assert img["width_px"] == 2
    assert img["height_px"] == 2
    assert img["bits"] == 8
    assert img["colorspace"] == "DeviceRGB"
    assert img["format"] == "unknown"  # no /F given
    assert img["stream_bytes"] == 2 * 2 * 3


def test_extract_pdf_images_inline_image_alongside_xobject_image(empty_pdf):
    """An inline image sharing a page with an ordinary XObject image must
    not suppress or corrupt the XObject's own entry -- both are listed."""
    page = empty_pdf.add_blank_page()
    img_xobj = create_real_image_stream(empty_pdf)
    page.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/DirectImg": img_xobj})})
    content = b"/DirectImg Do " + _inline_image_stream_bytes(width=1, height=1, cs="G", bpc=8)
    page.Contents = empty_pdf.make_stream(content)

    result = extract_pdf_images(empty_pdf, [1])
    assert len(result) == 2
    inline_entries = [r for r in result if r.get("inline")]
    xobject_entries = [r for r in result if not r.get("inline")]
    assert len(inline_entries) == 1
    assert len(xobject_entries) == 1
    assert inline_entries[0]["colorspace"] == "DeviceGray"
    assert xobject_entries[0]["name"] == "/DirectImg"


def test_extract_pdf_images_inline_image_inside_form(empty_pdf):
    """Inline images must also be found when they occur inside a Form
    XObject's own content stream, via the same recursive _parse_stream
    call forms already get for ordinary /Do images."""
    page = empty_pdf.add_blank_page()
    form = empty_pdf.make_stream(_inline_image_stream_bytes(width=3, height=1, cs="RGB", bpc=8))
    form["/Type"] = pikepdf.Name("/XObject")
    form["/Subtype"] = pikepdf.Name("/Form")
    form["/BBox"] = pikepdf.Array([0, 0, 100, 100])
    form["/Resources"] = pikepdf.Dictionary()

    page.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/InlineForm": form})})
    page.Contents = empty_pdf.make_stream(b"/InlineForm Do")

    result = extract_pdf_images(empty_pdf, [1])
    assert len(result) == 1
    assert result[0]["inline"] is True
    assert result[0]["width_px"] == 3


class TestGetInlineFormat:
    def test_abbreviated_filter_names_are_normalized(self):
        from pdftl.utils.images.finders import _get_inline_format

        assert _get_inline_format(pikepdf.Name("/Fl")) == "flatedecode"
        assert _get_inline_format(pikepdf.Name("/AHx")) == "asciihexdecode"
        assert _get_inline_format(pikepdf.Name("/DCT")) == "dctdecode"

    def test_full_filter_names_pass_through_lowercased(self):
        from pdftl.utils.images.finders import _get_inline_format

        assert _get_inline_format(pikepdf.Name("/FlateDecode")) == "flatedecode"

    def test_none_is_unknown(self):
        from pdftl.utils.images.finders import _get_inline_format

        assert _get_inline_format(None) == "unknown"

    def test_filter_array_uses_first_entry(self):
        from pdftl.utils.images.finders import _get_inline_format

        result = _get_inline_format(pikepdf.Array([pikepdf.Name("/A85"), pikepdf.Name("/Fl")]))
        assert result == "ascii85decode"

    def test_empty_filter_array_is_unknown(self):
        from pdftl.utils.images.finders import _get_inline_format

        assert _get_inline_format(pikepdf.Array([])) == "unknown"


class TestInlineColorspace:
    def test_abbreviated_names_are_expanded(self):
        from pdftl.utils.images.finders import _inline_colorspace

        assert _inline_colorspace(pikepdf.Name("/G")) == "DeviceGray"
        assert _inline_colorspace(pikepdf.Name("/RGB")) == "DeviceRGB"
        assert _inline_colorspace(pikepdf.Name("/CMYK")) == "DeviceCMYK"
        assert _inline_colorspace(pikepdf.Name("/I")) == "Indexed"

    def test_full_name_passes_through(self):
        from pdftl.utils.images.finders import _inline_colorspace

        assert _inline_colorspace(pikepdf.Name("/DeviceRGB")) == "DeviceRGB"

    def test_none_is_unknown(self):
        from pdftl.utils.images.finders import _inline_colorspace

        assert _inline_colorspace(None) == "unknown"


class TestReadInlineDataBytes:
    def test_reads_from_separate_data_operand(self):
        from pdftl.utils.images.finders import _read_inline_data_bytes

        assert _read_inline_data_bytes(object(), b"abcde") == 5

    def test_falls_back_to_data_attribute(self):
        from pdftl.utils.images.finders import _read_inline_data_bytes

        class WithData:
            data = b"abc"

        assert _read_inline_data_bytes(WithData(), None) == 3

    def test_falls_back_to_bytes_of_iimage_itself(self):
        from pdftl.utils.images.finders import _read_inline_data_bytes

        assert _read_inline_data_bytes(b"abcd", None) == 4

    def test_unreadable_everything_returns_zero(self):
        from pdftl.utils.images.finders import _read_inline_data_bytes

        assert _read_inline_data_bytes(object(), None) == 0


class TestHandleInlineImageEdgeCases:
    def test_no_operands_is_a_noop(self):
        from pdftl.utils.images.finders import _handle_inline_image

        image_list = []
        _handle_inline_image([], [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], image_list)
        assert image_list == []

    def test_missing_width_height_default_to_zero(self):
        from pdftl.utils.images.finders import _extract_inline_image_metadata

        class BareDict:
            def get(self, key, default=None):
                return default

        image_list = []
        _extract_inline_image_metadata(
            BareDict(), None, [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], image_list
        )
        assert image_list[0]["width_px"] == 0
        assert image_list[0]["height_px"] == 0
        assert image_list[0]["ppi_x"] == 0
        assert image_list[0]["ppi_y"] == 0


class TestInlineHeaderDict:
    def test_uses_obj_attribute_when_present(self):
        """A real PdfInlineImage-shaped operand exposes its header params
        via `.obj`, not via a `.get()` of its own -- `_inline_header_dict`
        must hand back that inner mapping, not the outer object."""
        from pdftl.utils.images.finders import _inline_header_dict

        class FakeInlineImage:
            obj = {"/Width": 5}

        assert _inline_header_dict(FakeInlineImage()) is FakeInlineImage.obj

    def test_falls_back_to_the_operand_itself(self):
        from pdftl.utils.images.finders import _inline_header_dict

        header = {"/Width": 5}
        assert _inline_header_dict(header) is header


class TestInlineDictGet:
    def test_none_when_get_is_entirely_unavailable(self):
        """Covers the except branch: a header with no usable `.get()`
        (e.g. plain object()) must yield None rather than raising, for
        every key tried."""
        from pdftl.utils.images.finders import _inline_dict_get

        assert _inline_dict_get(object(), "/Width", "/W") is None

    def test_falls_through_to_a_later_key_when_get_raises_on_type(self):
        """A header whose `.get()` exists but is picky about key types
        (raising TypeError for one spelling) must still find the value
        under a later, acceptable key rather than propagating."""
        from pdftl.utils.images.finders import _inline_dict_get

        class PickyHeader:
            def get(self, key):
                if key != "/W":
                    raise TypeError("unsupported key type")
                return 7

        assert _inline_dict_get(PickyHeader(), "/Width", "/W") == 7


class TestReadInlineDataBytesUnparseFallback:
    def test_uses_unparse_when_no_operand_or_data_attr_works(self):
        """Covers the unparse()-slicing path: no separate data operand and
        no `.data` attribute, but `.unparse()` returns the literal
        BI/ID/EI bytes pikepdf always produces."""
        from pdftl.utils.images.finders import _read_inline_data_bytes

        class FakeInlineImage:
            def unparse(self):
                return b"BI\n/W 2 /H 1 /BPC 8 /CS /G\nID\n\x01\x02 EI"

        assert _read_inline_data_bytes(FakeInlineImage(), None) == 2

    def test_candidate_present_but_unconvertible_is_skipped(self):
        """Covers the except-and-continue branch inside the
        data_operand/.data loop: a present-but-non-bytes-like candidate
        must be skipped (not raise), falling through to the next
        strategy rather than aborting."""
        from pdftl.utils.images.finders import _read_inline_data_bytes

        class WithBadData:
            data = object()  # present, but bytes(object()) raises TypeError

            def unparse(self):
                return b"BI\nID\n\x00\x00\x00 EI"

        assert _read_inline_data_bytes(WithBadData(), None) == 3


class TestFormRecursionDepthCap:
    def test_deep_form_chain_stops_descending_at_max_depth(self, empty_pdf):
        """A chain of nested Form XObjects deeper than MAX_FORM_DEPTH must
        not be walked past the cap -- images at or below the cap are
        still found; anything nested deeper is left unvisited rather than
        recursing without bound (guards against a cyclic or pathological
        /Do chain)."""
        from pdftl.utils.images.finders import MAX_FORM_DEPTH

        page = empty_pdf.add_blank_page()
        depth_total = MAX_FORM_DEPTH + 3
        prev_form = None
        # Innermost first: each level draws one image, then (for all but
        # the innermost) the next form.
        for level in range(depth_total - 1, -1, -1):
            im = create_real_image_stream(empty_pdf)
            xo = pikepdf.Dictionary({"/Im": im})
            content = b"/Im Do"
            if prev_form is not None:
                xo["/Fn"] = prev_form
                content += b" /Fn Do"
            form = empty_pdf.make_stream(content)
            form["/Type"] = pikepdf.Name("/XObject")
            form["/Subtype"] = pikepdf.Name("/Form")
            form["/Resources"] = pikepdf.Dictionary({"/XObject": xo})
            prev_form = empty_pdf.make_indirect(form)

        page.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/F0": prev_form})})
        page.Contents = empty_pdf.make_stream(b"/F0 Do")

        result = extract_pdf_images(empty_pdf, [1])
        # The page draws /F0 at depth 0 (allowed, 0 < MAX_FORM_DEPTH);
        # each descent bumps depth by one, so forms are entered for
        # depth 0..MAX_FORM_DEPTH-1 -- exactly MAX_FORM_DEPTH images.
        assert len(result) == MAX_FORM_DEPTH

    def test_shallow_chain_is_unaffected(self, empty_pdf):
        """A chain well within the cap must be walked in full -- the cap
        must not clip ordinary, non-pathological nesting."""
        from pdftl.utils.images.finders import MAX_FORM_DEPTH

        assert MAX_FORM_DEPTH > 3  # sanity: the fixture below must stay under it
        page = empty_pdf.add_blank_page()
        inner_img = create_real_image_stream(empty_pdf)
        inner_form = empty_pdf.make_stream(b"/Im Do")
        inner_form["/Type"] = pikepdf.Name("/XObject")
        inner_form["/Subtype"] = pikepdf.Name("/Form")
        inner_form["/Resources"] = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/Im": inner_img})}
        )

        outer_img = create_real_image_stream(empty_pdf)
        outer_form = empty_pdf.make_stream(b"/Im2 Do /Fi Do")
        outer_form["/Type"] = pikepdf.Name("/XObject")
        outer_form["/Subtype"] = pikepdf.Name("/Form")
        outer_form["/Resources"] = pikepdf.Dictionary(
            {
                "/XObject": pikepdf.Dictionary(
                    {"/Im2": outer_img, "/Fi": empty_pdf.make_indirect(inner_form)}
                )
            }
        )

        page.Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/Fo": empty_pdf.make_indirect(outer_form)})}
        )
        page.Contents = empty_pdf.make_stream(b"/Fo Do")

        result = extract_pdf_images(empty_pdf, [1])
        assert len(result) == 2


class TestMixedNestingDrawOrder:
    """Mined from spectra-pdf's TestWalkerAgreement: a page with a
    top-level image, a form containing an image and a nested inner form
    (itself containing an image), and another top-level image -- pins
    that draw order and nesting are preserved across the whole shape,
    not just the single-level cases the rest of the suite exercises."""

    def _mixed_page(self, empty_pdf):
        page = empty_pdf.add_blank_page()
        im1 = create_real_image_stream(empty_pdf)
        im2 = create_real_image_stream(empty_pdf)
        im3 = create_real_image_stream(empty_pdf)
        im4 = create_real_image_stream(empty_pdf)

        inner = empty_pdf.make_stream(b"q 10 0 0 10 0 0 cm /Im3 Do Q")
        inner["/Type"] = pikepdf.Name("/XObject")
        inner["/Subtype"] = pikepdf.Name("/Form")
        inner["/BBox"] = pikepdf.Array([0, 0, 10, 10])
        inner["/Resources"] = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Im3": im3})})

        outer = empty_pdf.make_stream(b"q 10 0 0 10 0 0 cm /Im2 Do Q q 1 0 0 1 20 0 cm /Fi Do Q")
        outer["/Type"] = pikepdf.Name("/XObject")
        outer["/Subtype"] = pikepdf.Name("/Form")
        outer["/BBox"] = pikepdf.Array([0, 0, 40, 10])
        outer["/Resources"] = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/Im2": im2, "/Fi": empty_pdf.make_indirect(inner)})}
        )

        page.Resources = pikepdf.Dictionary(
            {
                "/XObject": pikepdf.Dictionary(
                    {"/Im1": im1, "/Fo": empty_pdf.make_indirect(outer), "/Im4": im4}
                )
            }
        )
        page.Contents = empty_pdf.make_stream(
            b"q 50 0 0 50 0 700 cm /Im1 Do Q "
            b"q 1 0 0 1 100 400 cm /Fo Do Q "
            b"q 50 0 0 50 500 100 cm /Im4 Do Q"
        )
        return page

    def test_draw_order_and_names_preserved_across_nesting(self, empty_pdf):
        self._mixed_page(empty_pdf)
        result = extract_pdf_images(empty_pdf, [1])
        assert [r["name"] for r in result] == ["/Im1", "/Im2", "/Im3", "/Im4"]

    def test_bboxes_reflect_the_composed_ctm_at_each_depth(self, empty_pdf):
        self._mixed_page(empty_pdf)
        result = extract_pdf_images(empty_pdf, [1])
        # Im1: page cm only -> [0,700,50,750].
        assert result[0]["bbox"] == [0.0, 700.0, 50.0, 750.0]
        # Im4: page cm only -> [500,100,550,150].
        assert result[3]["bbox"] == [500.0, 100.0, 550.0, 150.0]
        # Im2 and Im3 sit inside /Fo's page-level placement (offset by
        # 100,400) composed with their own form-local cm's.
        assert result[1]["bbox"] == [100.0, 400.0, 110.0, 410.0]
        assert result[2]["bbox"] == [120.0, 400.0, 130.0, 410.0]


def test_extract_pdf_images_inline_image_tags_host_objgen(empty_pdf):
    page = empty_pdf.add_blank_page()
    content_stream = empty_pdf.make_stream(
        b"q 2 0 0 2 5 5 cm "
        + _inline_image_stream_bytes(width=2, height=2, cs="RGB", bpc=8)
        + b" Q"
    )
    page.Resources = pikepdf.Dictionary()
    page.Contents = content_stream

    result = extract_pdf_images(empty_pdf, [1])
    assert len(result) == 1
    assert result[0]["host_objgen"] == content_stream.objgen
    assert isinstance(result[0]["instruction_index"], int)


def test_extract_pdf_images_multi_stream_contents_array_ctm_carries_over(empty_pdf):
    """A page whose /Contents is an Array of two Streams: the 'cm' in the
    first stream must still be in effect for an inline image drawn at the
    start of the second stream, and each entry must be tagged with the
    objgen of the specific stream object it came from (not conflated)."""
    page = empty_pdf.add_blank_page()
    stream1 = empty_pdf.make_stream(b"q 2 0 0 2 5 5 cm")
    stream2 = empty_pdf.make_stream(
        _inline_image_stream_bytes(width=2, height=2, cs="RGB", bpc=8) + b" Q"
    )
    page.Resources = pikepdf.Dictionary()
    page.Contents = pikepdf.Array(
        [content_stream_ref(empty_pdf, stream1), content_stream_ref(empty_pdf, stream2)]
    )

    result = extract_pdf_images(empty_pdf, [1])
    assert len(result) == 1
    img = result[0]
    assert img["host_objgen"] == stream2.objgen
    # bbox reflects the cm applied in stream1, proving ctm carried across
    # the array boundary rather than resetting to identity for stream2.
    assert img["bbox"] == [5.0, 5.0, 7.0, 7.0]


def content_stream_ref(pdf, stream):
    return pdf.make_indirect(stream) if not stream.is_indirect else stream


def test_extract_pdf_images_page_with_no_contents_key_at_all(empty_pdf):
    """Covers _page_content_stream_objects' 'no /Contents key' branch
    directly -- distinct from the existing 'no /Resources anywhere'
    test, which still has a /Contents stream (just an empty one)."""
    page = empty_pdf.add_blank_page()
    if "/Contents" in page.obj:
        del page.obj["/Contents"]
    assert extract_pdf_images(empty_pdf, [1]) == []
