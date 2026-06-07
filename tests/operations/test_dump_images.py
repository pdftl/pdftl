# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Tests for pdftl.operations.dump_images"""

import json
import zlib
from io import StringIO
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.utils.image_utils import (
    _calculate_bbox,
    _extract_image_metadata,
    _get_format,
    _handle_do_operator,
    _multiply_matrices,
    _parse_stream,
    _process_form_xobject,
    extract_pdf_images,
)
from pdftl.operations.dump_images import (
    dump_images,
    dump_images_cli_hook,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_image_stream(pdf):
    """Return a minimal 1x1 red pixel /Image XObject."""
    raw = bytes([255, 0, 0])
    compressed = zlib.compress(raw)
    image = pikepdf.Stream(pdf, compressed)
    image["/Type"] = pikepdf.Name("/Image")
    image["/Subtype"] = pikepdf.Name("/Image")
    image["/Width"] = 1
    image["/Height"] = 1
    image["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
    image["/BitsPerComponent"] = 8
    image["/Filter"] = pikepdf.Name("/FlateDecode")
    return image


def _make_simple_pdf(x=100, y=200, w=300, h=150, page_w=612, page_h=792):
    """Return an in-memory PDF with one image placed at (x,y) size (w,h)."""
    pdf = pikepdf.Pdf.new()
    image = _make_image_stream(pdf)
    content = f"q {w} 0 0 {h} {x} {y} cm /Im1 Do Q".encode()
    page_obj = pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"),
        MediaBox=pikepdf.Array([0, 0, page_w, page_h]),
        Contents=pikepdf.Stream(pdf, content),
        Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=image)),
    )
    pdf.pages.append(pikepdf.Page(page_obj))
    return pdf


def _make_pdf_no_resources():
    """Return a PDF whose page has no /Resources key."""
    pdf = pikepdf.Pdf.new()
    content = b"q Q"
    page_obj = pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"),
        MediaBox=pikepdf.Array([0, 0, 612, 792]),
        Contents=pikepdf.Stream(pdf, content),
    )
    pdf.pages.append(pikepdf.Page(page_obj))
    return pdf


def _make_resources_with_image(pdf):
    image = _make_image_stream(pdf)
    return pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=image))


# ---------------------------------------------------------------------------
# _multiply_matrices
# ---------------------------------------------------------------------------


class TestMultiplyMatrices:
    def test_identity_times_identity(self):
        I = [1, 0, 0, 1, 0, 0]
        assert _multiply_matrices(I, I) == [1, 0, 0, 1, 0, 0]

    def test_identity_times_arbitrary(self):
        I = [1, 0, 0, 1, 0, 0]
        m = [2, 3, 4, 5, 6, 7]
        assert _multiply_matrices(I, m) == m

    def test_translation_composition(self):
        t1 = [1, 0, 0, 1, 10, 20]
        t2 = [1, 0, 0, 1, 5, 15]
        result = _multiply_matrices(t1, t2)
        assert result == [1, 0, 0, 1, 15, 35]

    def test_scale_composition(self):
        s1 = [2, 0, 0, 3, 0, 0]
        s2 = [4, 0, 0, 5, 0, 0]
        result = _multiply_matrices(s1, s2)
        assert result == [8, 0, 0, 15, 0, 0]

    def test_non_commutative(self):
        m1 = [1, 2, 3, 4, 5, 6]
        m2 = [7, 8, 9, 10, 11, 12]
        assert _multiply_matrices(m1, m2) != _multiply_matrices(m2, m1)

    def test_known_result(self):
        m1 = [1, 2, 3, 4, 5, 6]
        m2 = [7, 8, 9, 10, 11, 12]
        result = _multiply_matrices(m1, m2)
        assert result == [
            1 * 7 + 2 * 9,
            1 * 8 + 2 * 10,
            3 * 7 + 4 * 9,
            3 * 8 + 4 * 10,
            5 * 7 + 6 * 9 + 11,
            5 * 8 + 6 * 10 + 12,
        ]


# ---------------------------------------------------------------------------
# _calculate_bbox
# ---------------------------------------------------------------------------


class TestCalculateBbox:
    def test_simple_scale_and_translate(self):
        ctm = [300, 0, 0, 150, 100, 200]
        assert _calculate_bbox(ctm) == [100.0, 200.0, 400.0, 350.0]

    def test_identity_ctm(self):
        ctm = [1, 0, 0, 1, 0, 0]
        assert _calculate_bbox(ctm) == [0, 0, 1, 1]

    def test_negative_scale_still_gives_correct_min_max(self):
        ctm = [-300, 0, 0, 150, 100, 200]
        result = _calculate_bbox(ctm)
        assert result[0] < result[2]
        assert result[1] < result[3]

    def test_rounding_to_two_decimal_places(self):
        ctm = [100 / 3, 0, 0, 100 / 3, 0, 0]
        result = _calculate_bbox(ctm)
        for v in result:
            assert round(v, 2) == v

    def test_rotation_90_degrees(self):
        ctm = [0, 1, -1, 0, 0, 0]
        result = _calculate_bbox(ctm)
        assert result[0] <= result[2]
        assert result[1] <= result[3]


# ---------------------------------------------------------------------------
# image_colorspace (replaces _get_colorspace_name)
# ---------------------------------------------------------------------------


class TestImageColorspace:
    """image_colorspace() returns a resolved dict, not a bare string."""

    def test_device_rgb(self):
        from pdftl.utils.colorspaces import image_colorspace

        pdf = pikepdf.Pdf.new()
        xobj = pikepdf.Stream(pdf, b"")
        xobj["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
        result = image_colorspace(xobj, None, pikepdf)
        assert result["family"] == "rgb"

    def test_device_cmyk(self):
        from pdftl.utils.colorspaces import image_colorspace

        pdf = pikepdf.Pdf.new()
        xobj = pikepdf.Stream(pdf, b"")
        xobj["/ColorSpace"] = pikepdf.Name("/DeviceCMYK")
        result = image_colorspace(xobj, None, pikepdf)
        assert result["family"] == "cmyk"

    def test_iccbased_array_reports_icc_family(self):
        from pdftl.utils.colorspaces import image_colorspace

        pdf = pikepdf.Pdf.new()
        xobj = pikepdf.Stream(pdf, b"")
        icc_stream = pikepdf.Stream(pdf, b"\x00" * 10)
        icc_stream["/N"] = 3  # 3-component = RGB
        xobj["/ColorSpace"] = pikepdf.Array([pikepdf.Name("/ICCBased"), icc_stream])
        result = image_colorspace(xobj, None, pikepdf)
        assert result["family"] == "icc"
        assert result["icc_family"] == "rgb"
        assert result["components"] == 3

    def test_indexed_array(self):
        from pdftl.utils.colorspaces import image_colorspace

        pdf = pikepdf.Pdf.new()
        xobj = pikepdf.Stream(pdf, b"")
        xobj["/ColorSpace"] = pikepdf.Array(
            [
                pikepdf.Name("/Indexed"),
                pikepdf.Name("/DeviceRGB"),
                pikepdf.Integer(255),
                pikepdf.String(b"\x00" * 768),
            ]
        )
        result = image_colorspace(xobj, None, pikepdf)
        assert result["family"] == "indexed"
        assert result["base_family"] == "rgb"

    def test_missing_colorspace_returns_unknown(self):
        from pdftl.utils.colorspaces import image_colorspace

        pdf = pikepdf.Pdf.new()
        xobj = pikepdf.Stream(pdf, b"")
        result = image_colorspace(xobj, None, pikepdf)
        assert result["family"] == "unknown"


# ---------------------------------------------------------------------------
# _extract_image_metadata
# ---------------------------------------------------------------------------


class TestExtractImageMetadata:
    def test_appends_correct_metadata(self):
        pdf = pikepdf.Pdf.new()
        xobj = _make_image_stream(pdf)
        xobj["/Width"] = 640
        xobj["/Height"] = 480
        ctm = [300, 0, 0, 150, 100, 200]
        resources = _make_resources_with_image(pdf)
        image_list = []
        _extract_image_metadata(xobj, "/Im1", ctm, resources, image_list, pikepdf)
        assert len(image_list) == 1
        img = image_list[0]
        assert img["name"] == "/Im1"
        assert img["bbox"] == [100.0, 200.0, 400.0, 350.0]
        assert img["width_px"] == 640
        assert img["height_px"] == 480
        # colorspace is now a resolved dict
        assert img["colorspace"]["family"] == "rgb"
        assert img["bits"] == 8

    def test_missing_width_height_defaults_to_zero(self):
        pdf = pikepdf.Pdf.new()
        xobj = pikepdf.Stream(pdf, b"")
        xobj["/Subtype"] = pikepdf.Name("/Image")
        xobj["/ColorSpace"] = pikepdf.Name("/DeviceGray")
        xobj["/BitsPerComponent"] = 8
        ctm = [1, 0, 0, 1, 0, 0]
        image_list = []
        _extract_image_metadata(xobj, "/ImX", ctm, None, image_list, pikepdf)
        assert image_list[0]["width_px"] == 0
        assert image_list[0]["height_px"] == 0

    def test_missing_bits_defaults_to_8(self):
        pdf = pikepdf.Pdf.new()
        xobj = pikepdf.Stream(pdf, b"")
        xobj["/Subtype"] = pikepdf.Name("/Image")
        xobj["/Width"] = 10
        xobj["/Height"] = 10
        xobj["/ColorSpace"] = pikepdf.Name("/DeviceGray")
        ctm = [1, 0, 0, 1, 0, 0]
        image_list = []
        _extract_image_metadata(xobj, "/ImX", ctm, None, image_list, pikepdf)
        assert image_list[0]["bits"] == 8


# ---------------------------------------------------------------------------
# _handle_do_operator
# ---------------------------------------------------------------------------


class TestHandleDoOperator:
    def test_image_xobject_appends_to_list(self):
        pdf = pikepdf.Pdf.new()
        resources = _make_resources_with_image(pdf)
        image_list = []
        ctm = [300, 0, 0, 150, 100, 200]
        _handle_do_operator(pikepdf.Name("/Im1"), resources, ctm, image_list, pikepdf)
        assert len(image_list) == 1
        assert image_list[0]["name"] == "/Im1"

    def test_no_xobject_dict_does_nothing(self):
        resources = pikepdf.Dictionary()
        image_list = []
        _handle_do_operator(
            pikepdf.Name("/Im1"), resources, [1, 0, 0, 1, 0, 0], image_list, pikepdf
        )
        assert image_list == []

    def test_none_resources_does_nothing(self):
        image_list = []
        _handle_do_operator(pikepdf.Name("/Im1"), None, [1, 0, 0, 1, 0, 0], image_list, pikepdf)
        assert image_list == []

    def test_missing_name_in_xobjects_does_nothing(self):
        pdf = pikepdf.Pdf.new()
        resources = _make_resources_with_image(pdf)
        image_list = []
        _handle_do_operator(
            pikepdf.Name("/Missing"), resources, [1, 0, 0, 1, 0, 0], image_list, pikepdf
        )
        assert image_list == []

    def test_unknown_subtype_does_nothing(self):
        pdf = pikepdf.Pdf.new()
        xobj = pikepdf.Stream(pdf, b"")
        xobj["/Subtype"] = pikepdf.Name("/PS")
        resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=xobj))
        image_list = []
        _handle_do_operator(
            pikepdf.Name("/Im1"), resources, [1, 0, 0, 1, 0, 0], image_list, pikepdf
        )
        assert image_list == []

    def test_form_xobject_recurses(self):
        pdf = pikepdf.Pdf.new()
        inner_image = _make_image_stream(pdf)
        inner_content = b"q 100 0 0 100 10 20 cm /InnerIm Do Q"
        form = pikepdf.Stream(pdf, inner_content)
        form["/Type"] = pikepdf.Name("/XObject")
        form["/Subtype"] = pikepdf.Name("/Form")
        form["/Resources"] = pikepdf.Dictionary(XObject=pikepdf.Dictionary(InnerIm=inner_image))
        resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(MyForm=form))
        image_list = []
        _handle_do_operator(
            pikepdf.Name("/MyForm"), resources, [1, 0, 0, 1, 0, 0], image_list, pikepdf
        )
        assert len(image_list) == 1


# ---------------------------------------------------------------------------
# _process_form_xobject
# ---------------------------------------------------------------------------


class TestProcessFormXobject:
    def test_applies_form_matrix(self):
        pdf = pikepdf.Pdf.new()
        inner_image = _make_image_stream(pdf)
        inner_content = b"q 100 0 0 100 0 0 cm /Im1 Do Q"
        form = pikepdf.Stream(pdf, inner_content)
        form["/Subtype"] = pikepdf.Name("/Form")
        form["/Matrix"] = pikepdf.Array([1, 0, 0, 1, 50, 60])
        form["/Resources"] = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=inner_image))
        image_list = []
        _process_form_xobject(form, pikepdf.Dictionary(), [1, 0, 0, 1, 0, 0], image_list)
        assert len(image_list) == 1
        assert image_list[0]["bbox"][0] == pytest.approx(50.0)

    def test_identity_matrix_when_no_matrix_key(self):
        pdf = pikepdf.Pdf.new()
        inner_image = _make_image_stream(pdf)
        inner_content = b"q 100 0 0 100 10 20 cm /Im1 Do Q"
        form = pikepdf.Stream(pdf, inner_content)
        form["/Subtype"] = pikepdf.Name("/Form")
        form["/Resources"] = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=inner_image))
        image_list = []
        _process_form_xobject(form, pikepdf.Dictionary(), [1, 0, 0, 1, 0, 0], image_list)
        assert len(image_list) == 1
        assert image_list[0]["bbox"] == [10.0, 20.0, 110.0, 120.0]

    def test_falls_back_to_parent_resources(self):
        pdf = pikepdf.Pdf.new()
        inner_image = _make_image_stream(pdf)
        inner_content = b"q 50 0 0 50 5 5 cm /Im1 Do Q"
        form = pikepdf.Stream(pdf, inner_content)
        form["/Subtype"] = pikepdf.Name("/Form")
        # No /Resources on the form itself
        parent_resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=inner_image))
        image_list = []
        _process_form_xobject(form, parent_resources, [1, 0, 0, 1, 0, 0], image_list)
        assert len(image_list) == 1


# ---------------------------------------------------------------------------
# _parse_stream
# ---------------------------------------------------------------------------


class TestParseStream:
    def test_simple_image_found(self):
        pdf = _make_simple_pdf(x=100, y=200, w=300, h=150)
        page = pdf.pages[0]
        image_list = []
        _parse_stream(page, page.Resources, [1, 0, 0, 1, 0, 0], image_list)
        assert len(image_list) == 1
        assert image_list[0]["bbox"] == [100.0, 200.0, 400.0, 350.0]

    def test_ctm_stack_push_pop(self):
        pdf = pikepdf.Pdf.new()
        image = _make_image_stream(pdf)
        content = b"q 200 0 0 100 50 50 cm /Im1 Do Q"
        page_obj = pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=pikepdf.Array([0, 0, 612, 792]),
            Contents=pikepdf.Stream(pdf, content),
            Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=image)),
        )
        pdf.pages.append(pikepdf.Page(page_obj))
        page = pdf.pages[0]
        image_list = []
        _parse_stream(page, page.Resources, [1, 0, 0, 1, 0, 0], image_list)
        assert image_list[0]["bbox"] == [50.0, 50.0, 250.0, 150.0]

    def test_multiple_images_on_page(self):
        pdf = pikepdf.Pdf.new()
        img1 = _make_image_stream(pdf)
        img2 = _make_image_stream(pdf)
        content = b"q 100 0 0 100 10 10 cm /Im1 Do Q q 200 0 0 200 300 300 cm /Im2 Do Q"
        page_obj = pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=pikepdf.Array([0, 0, 612, 792]),
            Contents=pikepdf.Stream(pdf, content),
            Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=img1, Im2=img2)),
        )
        pdf.pages.append(pikepdf.Page(page_obj))
        page = pdf.pages[0]
        image_list = []
        _parse_stream(page, page.Resources, [1, 0, 0, 1, 0, 0], image_list)
        assert len(image_list) == 2

    def test_empty_ctm_stack_Q_is_safe(self):
        pdf = pikepdf.Pdf.new()
        content = b"Q"
        page_obj = pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=pikepdf.Array([0, 0, 612, 792]),
            Contents=pikepdf.Stream(pdf, content),
            Resources=pikepdf.Dictionary(),
        )
        pdf.pages.append(pikepdf.Page(page_obj))
        page = pdf.pages[0]
        image_list = []
        _parse_stream(page, page.Resources, [1, 0, 0, 1, 0, 0], image_list)
        assert image_list == []

    def test_parse_error_is_caught(self):
        mock_stream = MagicMock()
        with patch(
            "pikepdf.parse_content_stream",
            side_effect=pikepdf.PdfError("bad stream"),
        ):
            image_list = []
            _parse_stream(mock_stream, pikepdf.Dictionary(), [1, 0, 0, 1, 0, 0], image_list)
            assert image_list == []


# ---------------------------------------------------------------------------
# extract_pdf_images (replaces _extract_image_info)
# ---------------------------------------------------------------------------


class TestExtractPdfImages:
    def test_all_pages_processed_by_default(self):
        pdf = _make_simple_pdf()
        result = extract_pdf_images(pdf, [1])
        assert len(result) == 1
        assert result[0]["page"] == 1

    def test_page_with_no_images_excluded(self):
        pdf = pikepdf.Pdf.new()
        content = b"q Q"
        page_obj = pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=pikepdf.Array([0, 0, 612, 792]),
            Contents=pikepdf.Stream(pdf, content),
            Resources=pikepdf.Dictionary(),
        )
        pdf.pages.append(pikepdf.Page(page_obj))
        result = extract_pdf_images(pdf, [1])
        assert result == []

    def test_page_specs_filter_pages(self):
        pdf = _make_simple_pdf()
        image = _make_image_stream(pdf)
        content = b"q 50 0 0 50 5 5 cm /Im1 Do Q"
        page_obj = pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=pikepdf.Array([0, 0, 612, 792]),
            Contents=pikepdf.Stream(pdf, content),
            Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=image)),
        )
        pdf.pages.append(pikepdf.Page(page_obj))
        result = extract_pdf_images(pdf, [1])
        assert len(result) == 1
        assert result[0]["page"] == 1

    def test_page_without_resources_is_skipped(self):
        pdf = _make_pdf_no_resources()
        result = extract_pdf_images(pdf, [1])
        assert result == []

    def test_returns_correct_structure(self):
        pdf = _make_simple_pdf()
        result = extract_pdf_images(pdf, [1])
        assert isinstance(result, list)
        assert len(result) == 1
        img = result[0]
        assert "page" in img
        for key in ("name", "bbox", "width_px", "height_px", "colorspace", "bits"):
            assert key in img
        # colorspace is now a resolved dict
        assert isinstance(img["colorspace"], dict)
        assert "family" in img["colorspace"]

    def test_empty_target_pages_returns_nothing(self):
        pdf = _make_simple_pdf()
        result = extract_pdf_images(pdf, [])
        assert result == []


# ---------------------------------------------------------------------------
# dump_images_cli_hook
# ---------------------------------------------------------------------------


class TestDumpImagesCliHook:
    def test_writes_json_to_stdout(self):
        import pdftl.core.constants as c
        from pdftl.core.core_types import OpResult

        data = []
        result = OpResult(success=True, data=data, meta={c.META_OUTPUT_FILE: None})

        with patch("pdftl.operations.dump_images.smart_open_maybe_dash") as mock_open:
            mock_file = StringIO()
            mock_open.return_value.__enter__ = lambda s: mock_file
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            dump_images_cli_hook(result, None, None)
            output = mock_file.getvalue()

        parsed = json.loads(output)
        assert parsed == {"images": data}

    def test_writes_json_to_file(self):
        import pdftl.core.constants as c
        from pdftl.core.core_types import OpResult

        data = []
        result = OpResult(success=True, data=data, meta={c.META_OUTPUT_FILE: "out.json"})

        with patch("pdftl.operations.dump_images.smart_open_maybe_dash") as mock_open:
            mock_file = StringIO()
            mock_open.return_value.__enter__ = lambda s: mock_file
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            dump_images_cli_hook(result, None, None)
            mock_open.assert_called_once_with("out.json")


# ---------------------------------------------------------------------------
# dump_images (registered operation)
# ---------------------------------------------------------------------------


class TestDumpImages:
    def test_returns_op_result_success(self):
        from pdftl.core.core_types import OpResult

        pdf = _make_simple_pdf()
        result = dump_images(pdf, specs=[], output_file=None)
        assert isinstance(result, OpResult)
        assert result.success is True

    def test_result_data_is_list(self):
        pdf = _make_simple_pdf()
        result = dump_images(pdf, specs=[], output_file=None)
        assert isinstance(result.data, list)

    def test_output_file_stored_in_meta(self):
        import pdftl.core.constants as c

        pdf = _make_simple_pdf()
        result = dump_images(pdf, specs=[], output_file="my_output.json")
        assert result.meta[c.META_OUTPUT_FILE] == "my_output.json"

    def test_no_specs_extracts_all_pages(self):
        pdf = _make_simple_pdf()
        result = dump_images(pdf, specs=None, output_file=None)
        assert len(result.data) == 1

    def test_known_bbox_end_to_end(self):
        pdf = _make_simple_pdf(x=100, y=200, w=300, h=150)
        result = dump_images(pdf, specs=[], output_file=None)
        bbox = result.data[0]["bbox"]
        assert bbox == [100.0, 200.0, 400.0, 350.0]

    def test_colorspace_is_resolved_dict(self):
        """colorspace field is now a resolved dict, not a bare string."""
        pdf = _make_simple_pdf()
        result = dump_images(pdf, specs=[], output_file=None)
        cs = result.data[0]["colorspace"]
        assert isinstance(cs, dict)
        assert cs["family"] == "rgb"

    def test_get_format_array_filter(self):
        pdf = pikepdf.Pdf.new()
        xobj = pikepdf.Stream(pdf, b"")
        xobj["/Filter"] = pikepdf.Array(
            [
                pikepdf.Name("/FlateDecode"),
                pikepdf.Name("/DCTDecode"),
            ]
        )
        assert _get_format(xobj, pikepdf) == "flatedecode"

    def test_extract_image_metadata_read_raw_bytes_fails(self):
        pdf = pikepdf.Pdf.new()
        xobj = _make_image_stream(pdf)
        with patch(
            "pdftl.utils.image_utils._read_stream_bytes",
            side_effect=pikepdf.PdfError("fail"),
        ):
            image_list = []
            _extract_image_metadata(
                xobj, "/Im1", [100, 0, 0, 100, 0, 0], None, image_list, pikepdf
            )
            assert image_list[0]["stream_bytes"] == 0

    def test_min_dpi_filters_low_resolution_images(self):
        # Image placed at 300x150 pixels in a 300x150 point bbox = 72 PPI — below threshold
        pdf = _make_simple_pdf(x=0, y=0, w=72, h=72)
        result = dump_images(pdf, specs=["min_dpi=150"], output_file=None)
        assert result.data == []

    def test_min_dpi_keeps_high_resolution_images(self):
        # 300px in a 72pt box = 300 PPI — above threshold
        pdf = pikepdf.Pdf.new()
        image = _make_image_stream(pdf)
        image["/Width"] = 300
        image["/Height"] = 300
        content = b"q 72 0 0 72 0 0 cm /Im1 Do Q"
        page_obj = pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=pikepdf.Array([0, 0, 612, 792]),
            Contents=pikepdf.Stream(pdf, content),
            Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=image)),
        )
        pdf.pages.append(pikepdf.Page(page_obj))
        result = dump_images(pdf, specs=["min_dpi=150"], output_file=None)
        assert len(result.data) == 1

    def test_max_dpi_excludes_high_resolution_images(self):
        # Same 300 PPI image — should be excluded when max_dpi=150
        pdf = pikepdf.Pdf.new()
        image = _make_image_stream(pdf)
        image["/Width"] = 300
        image["/Height"] = 300
        content = b"q 72 0 0 72 0 0 cm /Im1 Do Q"
        page_obj = pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=pikepdf.Array([0, 0, 612, 792]),
            Contents=pikepdf.Stream(pdf, content),
            Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=image)),
        )
        pdf.pages.append(pikepdf.Page(page_obj))
        result = dump_images(pdf, specs=["max_dpi=150"], output_file=None)
        assert result.data == []

    def test_max_dpi_keeps_low_resolution_images(self):
        # 1px in a 72pt box = 1 PPI — well under any max_dpi
        pdf = _make_simple_pdf(x=0, y=0, w=72, h=72)
        result = dump_images(pdf, specs=["max_dpi=150"], output_file=None)
        assert len(result.data) == 1

    def test_dump_images_output_is_json_not_pdf(self, run_pdftl, tmp_path):
        """Regression: output file must contain JSON, not a PDF stream."""
        pdf_path = tmp_path / "in.pdf"
        output_path = tmp_path / "out.json"
        _make_simple_pdf().save(str(pdf_path))

        run_pdftl([str(pdf_path), "dump_images", "output", str(output_path)])

        content = output_path.read_bytes()
        assert not content.startswith(b"%PDF"), (
            "Output is a PDF stream, not JSON — skip_pipeline_save missing?"
        )
        parsed = json.loads(content)
        assert "images" in parsed

    def test_page_spec_combined_with_dpi_filter(self):
        # Two-page PDF; restrict to page 1 and apply min_dpi
        pdf = _make_simple_pdf()
        image = _make_image_stream(pdf)
        content = b"q 72 0 0 72 0 0 cm /Im1 Do Q"
        page_obj = pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=pikepdf.Array([0, 0, 612, 792]),
            Contents=pikepdf.Stream(pdf, content),
            Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=image)),
        )
        pdf.pages.append(pikepdf.Page(page_obj))
        result = dump_images(pdf, specs=["1", "min_dpi=0"], output_file=None)
        assert len(result.data) == 1
        assert result.data[0]["page"] == 1
