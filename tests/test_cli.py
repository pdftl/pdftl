# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/test_cli.py

import math

import fitz  # PyMuPDF
import pytest
from pikepdf import Pdf
from PIL import Image, ImageChops

# --- Test Case 1: Functional Comparison with `pdftk` ---


@pytest.mark.parametrize("num_pages", [1, 2, 5])
def test_cat_operation(runner, pdf_factory, temp_dir, num_pages):
    """
    Tests the 'cat' operation by comparing page count and metadata.
    This test is parametrized to run with 1, 2, and 5-page PDFs.
    """
    input_pdf = pdf_factory(num_pages)
    output_py = temp_dir / f"cat_py_{num_pages}p.pdf"
    output_tk = temp_dir / f"cat_tk_{num_pages}p.pdf"

    # Dynamically create arguments to reverse the first two pages (or just cat the first if only one page)
    if num_pages > 1:
        cat_args = ["2", "1"]
        expected_pages = 2
    else:
        cat_args = ["1"]
        expected_pages = 1

    # Command arguments for both tools
    args = [input_pdf, "cat", *cat_args, "output", output_py]

    # Run pdftl
    runner.run("pdftl", args)

    # Run pdftk with its corresponding output file
    args[-1] = output_tk  # Change the output file path in the args
    runner.run("pdftk", args)

    # Print the paths for easy inspection
    print(f"\n  pdftl output: {output_py}")
    print(f"  pdftk output:   {output_tk}")

    # --- Comparison ---
    with Pdf.open(output_py) as pdf_py, Pdf.open(output_tk) as pdf_tk:
        # 1. Compare Page Count
        assert len(pdf_py.pages) == len(pdf_tk.pages), "Page counts should match"
        assert len(pdf_py.pages) == expected_pages, f"Output should have {expected_pages} pages"

        # 2. Compare Metadata
        assert pdf_py.docinfo.get("/Author") == pdf_tk.docinfo.get("/Author")


# --- Test Case 2: Visual Comparison with `pdftk` ---


def get_image_diff(image_1_path, image_2_path):
    """Compares two images and returns a percentage difference."""
    img1 = Image.open(image_1_path).convert("RGB")
    img2 = Image.open(image_2_path).convert("RGB")

    if img1.size != img2.size:
        return 100.0  # Sizes are different, max difference

    diff = ImageChops.difference(img1, img2)

    if diff.getbbox() is None:
        return 0.0

    extrema = diff.getextrema()
    max_diff = sum(ext[1] for ext in extrema)
    scale = len(extrema) * 255
    return (max_diff / scale) * 100


@pytest.mark.parametrize("num_pages", [1, 3])
def test_rotate_operation_visual(runner, pdf_factory, temp_dir, num_pages):
    """
    Tests the 'rotate' operation by visually comparing the output images.
    This version rotates all pages to the right (east).
    """
    input_pdf = pdf_factory(num_pages)
    output_py_pdf = temp_dir / f"rotate_py_{num_pages}p.pdf"
    output_tk_pdf = temp_dir / f"rotate_tk_{num_pages}p.pdf"

    # Use a page range that works for any number of pages
    args = [
        input_pdf,
        "rotate",
        f"1-{min(3,num_pages)}east",
        "endleft",
        "output",
        output_py_pdf,
    ]

    runner.run("pdftl", args)
    args[-1] = output_tk_pdf
    runner.run("pdftk", args)

    # --- Visual Comparison ---
    doc_py = fitz.open(output_py_pdf)
    doc_tk = fitz.open(output_tk_pdf)

    assert len(doc_py) == len(doc_tk), "PDFs should have the same number of pages"
    assert len(doc_py) == num_pages

    for i in range(len(doc_py)):
        page_py = doc_py.load_page(i)
        page_tk = doc_tk.load_page(i)

        img_py_path = temp_dir / f"page_{i}_py_{num_pages}p.png"
        img_tk_path = temp_dir / f"page_{i}_tk_{num_pages}p.png"

        page_py.get_pixmap().save(img_py_path)
        page_tk.get_pixmap().save(img_tk_path)

        diff_percent = get_image_diff(img_py_path, img_tk_path)
        assert diff_percent < 1.0, f"Page {i+1} is visually different by {diff_percent:.2f}%"


# --- Test Case 3: Testing `pdftl`-exclusive Functionality ---


@pytest.mark.parametrize("num_pages", [1, 2, 10])
def test_crop_operation(runner, pdf_factory, temp_dir, num_pages):
    """
    Tests the 'crop' operation, which pdftk does not have.
    Checks that the output is created and has the correct page dimensions.
    """
    input_pdf = pdf_factory(num_pages)
    output_pdf = temp_dir / f"crop_py_{num_pages}p.pdf"

    # Crop 100 points from every side for all pages
    args = [input_pdf, "crop", "1-end(100pt,100pt,100pt,100pt)", "output", output_pdf]

    runner.run("pdftl", args)

    # --- Validation ---
    assert output_pdf.exists(), "Output PDF should be created"

    with Pdf.open(output_pdf) as pdf:
        # This assertion is now dynamic based on the input PDF's page count.
        assert len(pdf.pages) == num_pages, f"Cropped PDF should still have {num_pages} pages"

    with fitz.open(output_pdf) as doc:
        for page in doc.pages():
            # Original A4 size is 595x842. Cropping 100 from each side.
            expected_width = 595 - 200
            expected_height = 842 - 200

            assert math.isclose(
                page.rect.width, expected_width
            ), "Page width is incorrect after crop"
            assert math.isclose(
                page.rect.height, expected_height
            ), "Page height is incorrect after crop"
