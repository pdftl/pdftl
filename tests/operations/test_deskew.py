# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/test_deskew.py

import pikepdf
import pytest
from PIL import Image, ImageDraw

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.deskew import (
    _apply_deskew_angle,
    _binarize_page_image,
    _calculate_deskew_matrix,
    _coarse_angle_search,
    _compute_padded_crop,
    _crop_margins_to_pdf_points,
    _detect_angle_for_page,
    _fine_angle_search,
    _foreground_too_small_for_analysis,
    _maybe_request_higher_res,
    _parse_deskew_args,
    _parse_positive_float_arg,
    _resolve_target_pages,
    # _update_annotations,
    deskew_pages,
    determine_skew_angle,
)


@pytest.fixture
def one_page_pdf(tmp_path):
    pdf_path = tmp_path / "1_page.pdf"
    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(200, 300))
        pdf.save(pdf_path)
    return pdf_path


def create_skewed_text_image(
    angle: float, dark_text: bool = True, size: tuple[int, int] = (400, 400)
) -> Image.Image:
    """Generates a synthetic image containing skewed text lines for testing.

    Line placement scales with `size` so this remains valid for small images
    (e.g. tests exercising the "region too small, re-render" path) as well
    as the default full-page-sized case.
    """
    bg = 255 if dark_text else 0
    fg = 0 if dark_text else 255

    img = Image.new("L", size, bg)
    draw = ImageDraw.Draw(img)

    margin_x = max(10, size[0] // 8)
    margin_y = max(10, size[1] // 8)
    line_h = 10
    gap = 30

    y = margin_y
    while y + line_h <= size[1] - margin_y:
        draw.rectangle([margin_x, y, size[0] - margin_x, y + line_h], fill=fg)
        y += gap

    if y == margin_y:
        # Image too small for the loop to place even one line — draw a
        # single centered line so callers always get detectable foreground.
        mid_y = max(0, size[1] // 2 - line_h // 2)
        draw.rectangle([margin_x, mid_y, size[0] - margin_x, mid_y + line_h], fill=fg)

    # PIL rotate matches counter-clockwise orientation.
    # To introduce skew at `angle`, we rotate by `-angle`.
    return img.rotate(-angle, fillcolor=bg)


# --- End-to-end determine_skew_angle coverage (unchanged behavior) ---


def test_determine_skew_angle_dark_text():
    img = create_skewed_text_image(3.0, dark_text=True)
    angle = determine_skew_angle(img, max_skew=10.0, current_dpi=300.0)
    assert angle == pytest.approx(3.0, 0.2)


def test_determine_skew_angle_fractional():
    img = create_skewed_text_image(4.3, dark_text=True)
    angle = determine_skew_angle(img, max_skew=10.0, current_dpi=300.0)
    assert angle == pytest.approx(4.3, 0.25)


def test_determine_skew_angle_light_text():
    img = create_skewed_text_image(-4.0, dark_text=False)
    angle = determine_skew_angle(img, max_skew=10.0, current_dpi=300.0)
    assert angle == pytest.approx(-4.0, 0.2)


def test_determine_skew_angle_blank():
    img = Image.new("L", (100, 100), 255)
    angle = determine_skew_angle(img, max_skew=10.0)
    assert angle == 0.0


def test_determine_skew_angle_physical_size_guard():
    # Tiny smudge: 10x5 px at 75 DPI is ~9.6pt x 4.8pt, below the current
    # minimum-size guardrail (w_pt<15 or h_pt<6) — i.e. genuine noise, not
    # a legitimate small text line, which the guardrail is tuned to allow.
    img = Image.new("L", (200, 200), 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 20, 15], fill=0)
    angle = determine_skew_angle(img, max_skew=10.0, current_dpi=75.0)
    assert angle == 0.0


def test_determine_skew_angle_requests_revised_dpi_when_small():
    img = create_skewed_text_image(3.0, size=(150, 100))
    angle, revised_dpi, crop_box_pts = determine_skew_angle(
        img, max_skew=10.0, current_dpi=75.0, return_revised_dpi=True
    )
    assert angle == 0.0
    assert revised_dpi is not None
    assert revised_dpi > 75.0
    assert revised_dpi <= 600.0
    assert crop_box_pts is not None
    assert len(crop_box_pts) == 4


def test_determine_skew_angle_respects_max_render_dpi_cap():
    img = create_skewed_text_image(3.0, size=(150, 100))
    _, revised_dpi, _ = determine_skew_angle(
        img,
        max_skew=10.0,
        current_dpi=75.0,
        return_revised_dpi=True,
        max_render_dpi=200.0,
    )
    assert revised_dpi is not None
    assert revised_dpi <= 200.0


def test_determine_skew_angle_coarse_and_fine_targets_do_not_break_accuracy():
    # Small search footprints should still land in the right ballpark, just faster.
    img = create_skewed_text_image(4.0, dark_text=True)
    angle = determine_skew_angle(
        img, max_skew=10.0, current_dpi=300.0, coarse_target=100.0, fine_target=200.0
    )
    assert angle == pytest.approx(4.0, 0.5)


# --- Unit tests for extracted helper functions ---


def test_parse_positive_float_arg_default():
    assert _parse_positive_float_arg({}, "dpi", 75.0) == 75.0


def test_parse_positive_float_arg_valid():
    assert _parse_positive_float_arg({"dpi": "150"}, "dpi", 75.0) == 150.0


def test_parse_positive_float_arg_invalid_raises():
    with pytest.raises(InvalidArgumentError, match="invalid dpi"):
        _parse_positive_float_arg({"dpi": "-5"}, "dpi", 75.0)


def test_parse_positive_float_arg_non_numeric_raises():
    with pytest.raises(InvalidArgumentError, match="invalid max_skew"):
        _parse_positive_float_arg({"max_skew": "abc"}, "max_skew", 10.0)


def test_parse_deskew_args_defaults():
    page_specs, settings = _parse_deskew_args([])
    assert page_specs == []
    assert settings["dpi"] == 75.0
    assert settings["max_skew"] == 10.0
    assert settings["coarse_res"] == 300.0
    assert settings["fine_res"] == 600.0
    assert settings["max_render_dpi"] == 600.0


def test_parse_deskew_args_custom_values():
    page_specs, settings = _parse_deskew_args(
        ["1-3", "dpi=150", "coarse_res=100", "fine_res=200", "max_render_dpi=300"]
    )
    assert page_specs == ["1-3"]
    assert settings["dpi"] == 150.0
    assert settings["coarse_res"] == 100.0
    assert settings["fine_res"] == 200.0
    assert settings["max_render_dpi"] == 300.0


def test_parse_deskew_args_malformed_raises():
    with pytest.raises(InvalidArgumentError, match="Could not parse"):
        _parse_deskew_args(["invalid_arg=foo=bar"])


def test_resolve_target_pages_defaults_to_all(one_page_pdf):
    with pikepdf.open(one_page_pdf) as pdf:
        assert _resolve_target_pages(pdf, []) == [1]


def test_resolve_target_pages_out_of_range_returns_empty(one_page_pdf):
    with pikepdf.open(one_page_pdf) as pdf:
        assert _resolve_target_pages(pdf, ["5"]) == []


def test_binarize_page_image_no_downscale_for_small_image():
    img = Image.new("RGB", (100, 100), (255, 255, 255))
    bin_img, scale = _binarize_page_image(img, skip_downscale=False)
    assert scale == 1.0
    assert bin_img.size == (100, 100)


def test_binarize_page_image_downscales_large_image():
    img = Image.new("RGB", (2000, 1000), (255, 255, 255))
    bin_img, scale = _binarize_page_image(img, skip_downscale=False)
    assert scale < 1.0
    assert max(bin_img.size) == 1200


def test_binarize_page_image_skip_downscale_keeps_full_size():
    img = Image.new("RGB", (2000, 1000), (255, 255, 255))
    bin_img, scale = _binarize_page_image(img, skip_downscale=True)
    assert scale == 1.0
    assert bin_img.size == (2000, 1000)


def test_compute_padded_crop_larger_padding_for_larger_max_skew():
    img = Image.new("L", (400, 400), 0)
    bbox = (100, 100, 300, 300)
    _, small_pad_bbox = _compute_padded_crop(img, bbox, max_skew=1.0)
    _, large_pad_bbox = _compute_padded_crop(img, bbox, max_skew=45.0)
    small_pad_width = small_pad_bbox[2] - small_pad_bbox[0]
    large_pad_width = large_pad_bbox[2] - large_pad_bbox[0]
    assert large_pad_width > small_pad_width


def test_compute_padded_crop_clamps_to_image_bounds():
    img = Image.new("L", (100, 100), 0)
    bbox = (5, 5, 95, 95)
    _, padded_bbox = _compute_padded_crop(img, bbox, max_skew=10.0)
    assert padded_bbox[0] >= 0
    assert padded_bbox[1] >= 0
    assert padded_bbox[2] <= 100
    assert padded_bbox[3] <= 100


def test_crop_margins_to_pdf_points_left_top_origin():
    # A bbox flush against the top-left corner should have zero left/top margin
    margins = _crop_margins_to_pdf_points((0, 0, 50, 50), (100, 100), (200.0, 200.0))
    left, bottom, right, top = margins
    assert left == pytest.approx(0.0)
    assert top == pytest.approx(0.0)
    assert right == pytest.approx(100.0)
    assert bottom == pytest.approx(100.0)


def test_crop_margins_to_pdf_points_centered_box():
    margins = _crop_margins_to_pdf_points((25, 25, 75, 75), (100, 100), (200.0, 200.0))
    left, bottom, right, top = margins
    assert left == pytest.approx(50.0)
    assert right == pytest.approx(50.0)
    assert top == pytest.approx(50.0)
    assert bottom == pytest.approx(50.0)


def test_maybe_request_higher_res_triggers_for_small_area():
    revised = _maybe_request_higher_res(max_dim=100.0, current_dpi=75.0, max_render_dpi=600.0)
    assert revised is not None
    assert revised > 75.0


def test_maybe_request_higher_res_none_for_large_area():
    revised = _maybe_request_higher_res(max_dim=2000.0, current_dpi=75.0, max_render_dpi=600.0)
    assert revised is None


def test_maybe_request_higher_res_none_when_current_dpi_already_high():
    revised = _maybe_request_higher_res(max_dim=100.0, current_dpi=500.0, max_render_dpi=600.0)
    assert revised is None


def test_maybe_request_higher_res_respects_cap():
    revised = _maybe_request_higher_res(max_dim=10.0, current_dpi=75.0, max_render_dpi=150.0)
    assert revised is not None
    assert revised <= 150.0


def test_foreground_too_small_for_analysis_rejects_dust():
    assert _foreground_too_small_for_analysis(w_pt=5.0, h_pt=2.0) is True


def test_foreground_too_small_for_analysis_accepts_single_text_line():
    assert _foreground_too_small_for_analysis(w_pt=50.0, h_pt=10.0) is False


def test_coarse_angle_search_finds_approximate_angle():
    img = create_skewed_text_image(5.0)
    angle = _coarse_angle_search(img, max_skew=10.0, coarse_target=300.0)
    assert angle == pytest.approx(5.0, abs=1.0)


def test_fine_angle_search_refines_coarse_estimate():
    img = create_skewed_text_image(4.3)
    fine_angle = _fine_angle_search(img, coarse_best_angle=4.0, fine_target=600.0)
    assert fine_angle == pytest.approx(4.3, abs=0.3)


def test_detect_angle_for_page_returns_float(one_page_pdf):
    with pikepdf.open(one_page_pdf) as pdf:
        img = Image.new("RGB", (200, 300), (255, 255, 255))
        settings = {
            "dpi": 75.0,
            "max_skew": 10.0,
            "coarse_res": 300.0,
            "fine_res": 600.0,
            "max_render_dpi": 600.0,
        }
        angle = _detect_angle_for_page(pdf, 0, img, settings)
        assert angle == 0.0


def test_apply_deskew_angle_no_op_for_zero_angle(one_page_pdf):
    with pikepdf.open(one_page_pdf) as pdf:
        # Identity rotation shouldn't touch content streams
        _apply_deskew_angle(pdf, 0, 0.0)
        # No exception, page still valid
        assert pdf.pages[0] is not None


# --- Original integration-level coverage ---


def test_deskew_pages_performs_high_res_re_rendering(one_page_pdf):
    with pikepdf.open(one_page_pdf) as pdf:
        res = deskew_pages(pdf, ["1", "dpi=75"])
        assert res.success


def test_deskew_invalid_dpi(one_page_pdf):
    with pikepdf.open(one_page_pdf) as pdf:
        with pytest.raises(InvalidArgumentError, match="invalid dpi"):
            deskew_pages(pdf, ["dpi=-1"])


def test_deskew_invalid_max_skew(one_page_pdf):
    with pikepdf.open(one_page_pdf) as pdf:
        with pytest.raises(InvalidArgumentError, match="invalid max_skew"):
            deskew_pages(pdf, ["max_skew=0"])


def test_deskew_invalid_coarse_res(one_page_pdf):
    with pikepdf.open(one_page_pdf) as pdf:
        with pytest.raises(InvalidArgumentError, match="invalid coarse_res"):
            deskew_pages(pdf, ["coarse_res=0"])


def test_deskew_invalid_fine_res(one_page_pdf):
    with pikepdf.open(one_page_pdf) as pdf:
        with pytest.raises(InvalidArgumentError, match="invalid fine_res"):
            deskew_pages(pdf, ["fine_res=-10"])


def test_deskew_invalid_max_render_dpi(one_page_pdf):
    with pikepdf.open(one_page_pdf) as pdf:
        with pytest.raises(InvalidArgumentError, match="invalid max_render_dpi"):
            deskew_pages(pdf, ["max_render_dpi=0"])


def test_deskew_malformed_arguments(one_page_pdf):
    with pikepdf.open(one_page_pdf) as pdf:
        with pytest.raises(InvalidArgumentError, match="Could not parse"):
            deskew_pages(pdf, ["invalid_arg=foo=bar"])


def test_deskew_no_pages_matched(one_page_pdf):
    with pikepdf.open(one_page_pdf) as pdf:
        result = deskew_pages(pdf, ["5"])
        assert result.success


def test_deskew_calculate_matrix_empty_page_dimensions():
    class DummyPage:
        def get(self, *args, **kwargs):
            return 0

    page = DummyPage()
    matrix = _calculate_deskew_matrix(page, 5.0)
    assert matrix == pikepdf.Matrix()


def test_deskew_updates_annotations(one_page_pdf):
    with pikepdf.open(one_page_pdf) as pdf:
        page = pdf.pages[0]

        annot = pikepdf.Dictionary(
            Type=pikepdf.Name("/Annot"),
            Subtype=pikepdf.Name("/Link"),
            Rect=[10, 10, 20, 20],
            QuadPoints=[10, 10, 20, 10, 20, 20, 10, 20],
            AP=pikepdf.Dictionary(N=pikepdf.Stream(pdf, b"dummy")),
        )
        page["/Annots"] = pikepdf.Array([annot])

        deskew_pages(pdf, ["1", "max_skew=5"])

        from pdftl.utils.geometry import update_annotations_for_matrix

        matrix = pikepdf.Matrix().rotated(90)
        update_annotations_for_matrix(page, matrix)

        updated_annot = page["/Annots"][0]
        assert "/AP" not in updated_annot
        assert len(updated_annot["/Rect"]) == 4
        assert len(updated_annot["/QuadPoints"]) == 8


# --- Additional high-fidelity coverage expansion tests ---


def test_determine_skew_angle_with_page_pts():
    """Verify that determine_skew_angle respects pre-calculated page dimensions when mapping crop boxes."""
    img = create_skewed_text_image(3.0, size=(150, 100))
    angle, revised_dpi, crop_box_pts = determine_skew_angle(
        img, max_skew=10.0, current_dpi=75.0, return_revised_dpi=True, page_pts=(200.0, 150.0)
    )
    assert angle == 0.0
    assert revised_dpi is not None
    assert crop_box_pts is not None


def test_maybe_request_higher_res_none_fallback():
    """Verify that no high-resolution re-render is requested if the revised DPI is too close to the current DPI."""
    res = _maybe_request_higher_res(max_dim=500.0, current_dpi=100.0, max_render_dpi=110.0)
    assert res is None


def test_detect_angle_for_page_re_render_success(one_page_pdf):
    """Verify that a small active region triggers regional re-rendering and uses the refined angle."""
    from unittest.mock import patch

    with pikepdf.open(one_page_pdf) as pdf:
        img = create_skewed_text_image(3.0, size=(150, 100))
        settings = {
            "dpi": 75.0,
            "max_skew": 10.0,
            "coarse_res": 300.0,
            "fine_res": 600.0,
            "max_render_dpi": 600.0,
        }
        mock_crop = create_skewed_text_image(4.0, size=(600, 400))
        with patch(
            "pdftl.utils.page_images.render_page_region_to_pil", return_value=mock_crop
        ) as mock_render:
            angle = _detect_angle_for_page(pdf, 0, img, settings)
            assert angle == pytest.approx(4.0, 0.2)
            mock_render.assert_called_once()


def test_detect_angle_for_page_re_render_failure(one_page_pdf):
    """Verify that _detect_angle_for_page falls back gracefully to low-resolution angle on regional rendering errors."""
    from unittest.mock import patch

    with pikepdf.open(one_page_pdf) as pdf:
        img = create_skewed_text_image(3.0, size=(150, 100))
        settings = {
            "dpi": 75.0,
            "max_skew": 10.0,
            "coarse_res": 300.0,
            "fine_res": 600.0,
            "max_render_dpi": 600.0,
        }
        with patch(
            "pdftl.utils.page_images.render_page_region_to_pil",
            side_effect=RuntimeError("Simulated failure"),
        ):
            angle = _detect_angle_for_page(pdf, 0, img, settings)
            assert angle == 0.0


def test_deskew_pages_applies_skew_angle(one_page_pdf):
    """Verify that deskew_pages successfully applies detected skew angles exceeding the threshold."""
    from unittest.mock import patch

    with pikepdf.open(one_page_pdf) as pdf:
        mock_img = create_skewed_text_image(3.0, size=(400, 400))
        with patch("pdftl.operations.deskew.iter_pages_as_pil", return_value=[(0, mock_img)]):
            res = deskew_pages(pdf, ["1"])
            assert res.success


def test_calculate_deskew_matrix_visual_dims_none():
    """Verify that _calculate_deskew_matrix returns an identity matrix if visual dimensions cannot be retrieved."""
    from unittest.mock import patch

    with patch("pdftl.operations.deskew.get_visible_page_dimensions") as mock_get:
        mock_get.side_effect = [(0, 0, 100, 100), None]

        class DummyPage:
            rotation = 0

            def get(self, *args, **kwargs):
                return 0

        page = DummyPage()
        matrix = _calculate_deskew_matrix(page, 5.0)
        assert matrix == pikepdf.Matrix()


def test_apply_deskew_angle_applies_nonidentity_rotation(one_page_pdf):
    """Verify that _apply_deskew_angle rewrites content streams and annotations for a non-zero angle."""
    from unittest.mock import patch

    with pikepdf.open(one_page_pdf) as pdf:
        page = pdf.pages[0]
        annot = pikepdf.Dictionary(
            Type=pikepdf.Name("/Annot"),
            Subtype=pikepdf.Name("/Link"),
            Rect=[10, 10, 20, 20],
        )
        page["/Annots"] = pikepdf.Array([annot])

        non_identity = pikepdf.Matrix().rotated(5.0)
        with patch("pdftl.operations.deskew._calculate_deskew_matrix", return_value=non_identity):
            _apply_deskew_angle(pdf, 0, 5.0)

        contents = pdf.pages[0].Contents
        if isinstance(contents, pikepdf.Array):
            content = b"".join(s.read_bytes() for s in contents)
        else:
            content = contents.read_bytes()
        assert b"cm" in content
        assert b"Q" in content
        assert "/AP" not in pdf.pages[0]["/Annots"][0]


def test_deskew_pages_applies_detected_angle_end_to_end(one_page_pdf):
    """Verify that deskew_pages applies an angle that clears the significance threshold."""
    from unittest.mock import patch

    with pikepdf.open(one_page_pdf) as pdf:
        with patch("pdftl.operations.deskew.iter_pages_as_pil", return_value=[(0, object())]):
            with patch("pdftl.operations.deskew._detect_angle_for_page", return_value=5.0):
                with patch("pdftl.operations.deskew._apply_deskew_angle") as mock_apply:
                    res = deskew_pages(pdf, ["1"])
                    assert res.success
                    mock_apply.assert_called_once_with(pdf, 0, 5.0)


def test_update_annotations_no_annots():
    """Verify that update_annotations_for_matrix returns immediately if the page has no annotations."""
    from pdftl.utils.geometry import update_annotations_for_matrix

    class DummyPage(dict):
        pass

    page = DummyPage()
    update_annotations_for_matrix(page, pikepdf.Matrix())
