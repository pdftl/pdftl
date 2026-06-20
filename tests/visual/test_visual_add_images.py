# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/visual/test_visual_add_images.py

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

import pdftl.api

# FIXTURE_PDF = "tests/files/sample_multiformat.pdf"


@pytest.fixture
def test_stamp_png(tmp_path):
    """Generates a high-contrast geometric stamp image in PNG format."""
    stamp_path = tmp_path / "stamp_pattern.png"
    img = Image.new("RGBA", (150, 150), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Bright high-contrast visual target elements
    draw.ellipse([10, 10, 140, 140], fill=(0, 191, 255, 255), outline=(255, 69, 0, 255), width=6)
    draw.rectangle([45, 45, 105, 105], fill=(255, 215, 0, 255))
    img.save(stamp_path, format="PNG")
    return stamp_path


def _stamp_and_label(img_path, rule_str, pdf):
    """
    Applies the specified add_images rule to a copy of the multi-format base PDF,
    and stamps a visual label on top for clean self-documenting regression outputs.
    """
    import re

    # 1. Apply image stamping operation
    result_pdf = pdftl.api.add_images(pdf=pdf, operation_args=[rule_str])
    rule_display_str = re.sub(r"^.*[/\\]", "!", rule_str)
    # 2. Append a clean text descriptor at the top of page 1 to declare the test case
    labeled_pdf = pdftl.api.add_text(
        pdf=result_pdf,
        operation_args=[
            f"/Rule: {rule_display_str}/(position=top-center, offset-y=-30, size=12, color=0 0 1)"
        ],
    )
    return labeled_pdf


def test_visual_add_images_positions(assert_pdf_match, test_stamp_png, two_page_pdf):
    """
    Verifies that all outer bounds anchoring positions (top-left, center, bottom-right)
    place the stamped image correctly on the page relative to the target boundaries.
    """
    img = str(test_stamp_png)
    result = pdftl.api.cat(
        opened_pdfs=[
            # Top-Left anchor placement
            _stamp_and_label(
                img, f"!{img}!(position=top-left, width=100pt, height=100pt)", two_page_pdf
            ),
            # Perfectly centered layout
            _stamp_and_label(
                img, f"!{img}!(position=center, width=150pt, height=150pt)", two_page_pdf
            ),
            # Bottom-Right layout anchor
            _stamp_and_label(
                img, f"!{img}!(position=bottom-right, width=120pt, height=120pt)", two_page_pdf
            ),
        ]
    )
    assert_pdf_match(result, suffix="positions")


def test_visual_add_images_scale_modes(assert_pdf_match, test_stamp_png, two_page_pdf):
    """
    Verifies aspect ratio preservation strategies (fit, fill, stretch, none)
    when scaling coordinates inside the resolved boundary boxes.
    """
    img = str(test_stamp_png)
    result = pdftl.api.cat(
        opened_pdfs=[
            # 'fit' scale strategy inside smaller bounds
            _stamp_and_label(
                img,
                f"!{img}!(position=center, scale_mode=fit, width=150pt, height=80pt)",
                two_page_pdf,
            ),
            # 'fill' scale strategy bleeding past constraints
            _stamp_and_label(
                img,
                f"!{img}!(position=center, scale_mode=fill, width=200pt, height=100pt)",
                two_page_pdf,
            ),
            # 'stretch' ignoring target aspect ratio limits
            _stamp_and_label(
                img,
                f"!{img}!(position=center, scale_mode=stretch, width=250pt, height=100pt)",
                two_page_pdf,
            ),
        ]
    )
    assert_pdf_match(result, suffix="scale_modes")


def test_visual_add_images_opacities_and_underlays(assert_pdf_match, test_stamp_png, two_page_pdf):
    """
    Verifies Z-index routing properties (overlay vs underlay) and alpha transparency
    state values when stamping watermarks onto pre-existing page contents.
    """
    img = str(test_stamp_png)
    result = pdftl.api.cat(
        opened_pdfs=[
            # High opacity foreground overlay
            _stamp_and_label(
                img,
                f"!{img}!(position=center, opacity=0.8, width=200pt, height=200pt)",
                two_page_pdf,
            ),
            # Extreme soft mask alpha transparency (watermark style)
            _stamp_and_label(
                img,
                f"!{img}!(position=center, opacity=0.25, width=250pt, height=250pt)",
                two_page_pdf,
            ),
            # Background underlay behind page contents
            _stamp_and_label(
                img,
                f"!{img}!(position=center, underlay=true, opacity=0.9, width=300pt, height=300pt)",
                two_page_pdf,
            ),
        ]
    )
    assert_pdf_match(result, suffix="opacities")


def test_visual_add_images_percentage_sizing_and_offsets(
    assert_pdf_match, test_stamp_png, two_page_pdf
):
    """
    Verifies page-relative dimensions using percentage strings (e.g. '50%') and
    relative positional offset displacements (offset-x, offset-y) from anchors.
    """
    img = str(test_stamp_png)
    result = pdftl.api.cat(
        opened_pdfs=[
            # Stamped to fill exactly half the page width and height
            _stamp_and_label(
                img, f"!{img}!(position=center, width=50%, height=50%)", two_page_pdf
            ),
            # Offsets shifting the image relative to top-left anchor position
            _stamp_and_label(
                img,
                f"!{img}!(position=top-left, width=100pt, offset-x=50pt, offset-y=-30pt)",
                two_page_pdf,
            ),
            # Negative percent offset adjustments
            _stamp_and_label(
                img,
                f"!{img}!(position=bottom-right, width=80pt, offset-x=-40pt, offset-y=20pt)",
                two_page_pdf,
            ),
        ]
    )
    assert_pdf_match(result, suffix="percentage_offsets")
