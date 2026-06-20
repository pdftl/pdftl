# tests/visual/test_visual_modify_images.py
import pdftl.api

FIXTURE_PATH = "tests/files/pdfs/sample_multiformat.pdf"


def _modify_and_label(args, init_pdf=FIXTURE_PATH):
    if isinstance(args, list):
        operation_args = args
    else:
        operation_args = [args]
    result = pdftl.modify_images(pdf=str(init_pdf), operation_args=operation_args)
    result = pdftl.add_text(
        pdf=result, operation_args=[f"/{' '.join(operation_args)}/(position=top-center)"]
    )
    return result


def test_visual_modify_images_brightness(assert_pdf_match):
    assert_pdf_match(
        pdftl.cat(
            opened_pdfs=[
                _modify_and_label("(brightness=0.5)"),
                _modify_and_label("(brightness=1.5)"),
            ]
        ),
        suffix="after",
    )


def test_visual_modify_images_contrast(assert_pdf_match):
    assert_pdf_match(
        pdftl.cat(
            opened_pdfs=[_modify_and_label("(contrast=0.5)"), _modify_and_label("(contrast=1.5)")]
        ),
        suffix="after",
    )


def test_visual_modify_images_invert(assert_pdf_match):
    assert_pdf_match(
        pdftl.cat(
            opened_pdfs=[_modify_and_label("(invert)"), _modify_and_label("(invert;invert)")]
        ),
        suffix="after",
    )


def test_visual_modify_images_blur(assert_pdf_match):
    assert_pdf_match(
        pdftl.cat(opened_pdfs=[_modify_and_label("(blur=1.5)"), _modify_and_label("(blur=0)")]),
        suffix="after",
    )


def test_visual_modify_images_despeckle(assert_pdf_match):
    assert_pdf_match(
        pdftl.cat(
            opened_pdfs=[
                _modify_and_label("(despeckle=true)"),
                _modify_and_label("(despeckle=false)"),
            ]
        ),
        suffix="after",
    )


def test_visual_modify_images_autocontrast(assert_pdf_match):
    assert_pdf_match(
        pdftl.cat(
            opened_pdfs=[
                _modify_and_label("(autocontrast=true)"),
                _modify_and_label("(autocontrast=false)"),
            ]
        ),
        suffix="after",
    )


def test_visual_modify_images_saturation(assert_pdf_match):
    assert_pdf_match(
        pdftl.cat(
            opened_pdfs=[
                _modify_and_label("(saturation=0.0)"),  # Grayscale check
                _modify_and_label("(saturation=2.0)"),  # High-intensity vibrancy check
            ]
        ),
        suffix="after",
    )


def test_visual_modify_images_hue(assert_pdf_match):
    assert_pdf_match(
        pdftl.cat(opened_pdfs=[_modify_and_label("(hue=-90.0)"), _modify_and_label("(hue=90.0)")]),
        suffix="after",
    )


def test_visual_modify_images_lightness(assert_pdf_match):
    assert_pdf_match(
        pdftl.cat(
            opened_pdfs=[
                _modify_and_label("(lightness=0.4)"),  # Shadow compression
                _modify_and_label("(lightness=1.6)"),  # Highlight expansion
            ]
        ),
        suffix="after",
    )


def test_visual_modify_images_gamma(assert_pdf_match):
    result = pdftl.cat(
        opened_pdfs=[
            _modify_and_label("(gamma=0.5)"),  # Midtone lift
            _modify_and_label("(gamma=2.2)"),  # Midtone crush
        ]
    )
    assert_pdf_match(result, suffix="after")


def test_visual_modify_images_levels(assert_pdf_match):
    assert_pdf_match(
        pdftl.cat(
            opened_pdfs=[
                _modify_and_label("(levels=0,50,100)"),
                _modify_and_label("(levels=0,0,100)"),
                _modify_and_label("(levels=0,50,50,50,50,100)"),
            ]
        ),
        suffix="after",
    )


def test_visual_modify_images_posterize(assert_pdf_match):
    assert_pdf_match(
        pdftl.cat(
            opened_pdfs=[
                _modify_and_label("(posterize=2)"),  # Heavy 4-color channel quantization
                _modify_and_label("(posterize=5)"),  # Moderate 32-color channel quantization
            ]
        ),
        suffix="after",
    )


def test_visual_modify_images_solarize(assert_pdf_match):
    assert_pdf_match(
        pdftl.cat(
            opened_pdfs=[
                _modify_and_label("(solarize=25)"),  # Low-threshold wide polarization
                _modify_and_label("(solarize=75)"),  # High-threshold highlight-only inversion
            ]
        ),
        suffix="after",
    )


def test_visual_modify_images_threshold(assert_pdf_match):
    assert_pdf_match(
        pdftl.cat(
            opened_pdfs=[
                _modify_and_label("(threshold=35)"),  # White-biased 1-bit binarization
                _modify_and_label("(threshold=65)"),  # Black-biased 1-bit binarization
            ]
        ),
        suffix="after",
    )


def test_visual_modify_images_sharpen(assert_pdf_match):
    assert_pdf_match(
        pdftl.cat(
            opened_pdfs=[_modify_and_label("(sharpen=true)"), _modify_and_label("(sharpen=false)")]
        ),
        suffix="after",
    )


def test_visual_modify_images_unsharp_mask(assert_pdf_match):
    assert_pdf_match(
        pdftl.cat(
            opened_pdfs=[
                _modify_and_label("(unsharp_mask=1.0)"),
                _modify_and_label("(unsharp_mask=3.5)"),
            ]
        ),
        suffix="after",
    )
