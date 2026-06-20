# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/visual/test_visual_images.py

import io
import pikepdf
from PIL import Image, ImageDraw

import pdftl.utils.images.grayscale as gray_mod
from pdftl.utils.images.stamper import stamp_images_on_pdf

JPEG_QUALITY = 75


def test_visual_grayscale_image_conversion(assert_pdf_match):
    """
    Verifies that a standard color image is correctly neutralized to grayscale.

    The 'before' baseline shows a vibrant color image, and the 'after' baseline
    shows the successfully converted grayscale version.
    """
    # --- 1. Create a valid color JPEG in memory ---
    img = Image.new("RGB", (200, 200), color=(255, 50, 50))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    jpeg_bytes = buf.getvalue()

    # --- 2. Build a valid, renderable PDF and embed the image ---
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(400, 400))

    img_xobj = pdf.make_stream(jpeg_bytes)
    img_xobj["/Type"] = pikepdf.Name("/XObject")
    img_xobj["/Subtype"] = pikepdf.Name("/Image")
    img_xobj["/Width"] = 200
    img_xobj["/Height"] = 200
    img_xobj["/BitsPerComponent"] = 8
    img_xobj["/Filter"] = pikepdf.Name("/DCTDecode")
    img_xobj["/ColorSpace"] = pikepdf.Name("/DeviceRGB")

    page = pdf.pages[0]
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=img_xobj))
    page.Contents = pdf.make_stream(b"q 200 0 0 200 100 100 cm /Im1 Do Q")

    # --- 3. Snapshot the BEFORE state ---
    assert_pdf_match(pdf, suffix="before")

    # --- 4. Run the target conversion logic (Phased Pipeline) ---
    img_meta = {"xobj": img_xobj, "format": "dctdecode", "page": 1}
    seen_objgens = set()

    prepared = gray_mod.prepare_recolor_payload(img_meta, JPEG_QUALITY, seen_objgens)
    if prepared:
        payload, ctx = prepared
        result = gray_mod.worker_recolor_pixels(payload)
        success = gray_mod.commit_recolored_stream(ctx, result, payload)
    else:
        success = False

    assert success is True, "Image conversion failed unexpectedly."

    # --- 5. Snapshot the AFTER state ---
    assert_pdf_match(pdf, suffix="after")


def test_visual_grayscale_multi_color_grid(assert_pdf_match):
    """
    Verifies grayscale conversion across a variety of primary and secondary
    colors to ensure correct luminosity mapping in the visual baseline.
    """
    # --- 1. Create a 2x2 color quadrant image ---
    img = Image.new("RGB", (200, 200))
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, 100, 100], fill=(255, 50, 50))  # Top-Left: Bright Red
    draw.rectangle([100, 0, 200, 100], fill=(50, 255, 50))  # Top-Right: Bright Green
    draw.rectangle([0, 100, 100, 200], fill=(50, 50, 255))  # Bottom-Left: Deep Blue
    draw.rectangle([100, 100, 200, 200], fill=(255, 255, 50))  # Bottom-Right: Vibrant Yellow

    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    jpeg_bytes = buf.getvalue()

    # --- 2. Build a valid, renderable PDF and embed the image ---
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(400, 400))

    img_xobj = pdf.make_stream(jpeg_bytes)
    img_xobj["/Type"] = pikepdf.Name("/XObject")
    img_xobj["/Subtype"] = pikepdf.Name("/Image")
    img_xobj["/Width"] = 200
    img_xobj["/Height"] = 200
    img_xobj["/BitsPerComponent"] = 8
    img_xobj["/Filter"] = pikepdf.Name("/DCTDecode")
    img_xobj["/ColorSpace"] = pikepdf.Name("/DeviceRGB")

    page = pdf.pages[0]
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=img_xobj))
    page.Contents = pdf.make_stream(b"q 200 0 0 200 100 100 cm /Im1 Do Q")

    # --- 3. Snapshot the BEFORE state ---
    assert_pdf_match(pdf, suffix="before")

    # --- 4. Run the target conversion logic (Phased Pipeline) ---
    img_meta = {"xobj": img_xobj, "format": "dctdecode", "page": 1}
    seen_objgens = set()

    prepared = gray_mod.prepare_recolor_payload(img_meta, JPEG_QUALITY, seen_objgens)
    if prepared:
        payload, ctx = prepared
        result = gray_mod.worker_recolor_pixels(payload)
        success = gray_mod.commit_recolored_stream(ctx, result, payload)
    else:
        success = False

    assert success is True, "Image conversion failed unexpectedly."

    # --- 5. Snapshot the AFTER state ---
    assert_pdf_match(pdf, suffix="after")


def test_visual_stamp_images_variations(assert_pdf_match, tmp_path):
    """
    Generates a single PDF document testing a wide variety of image stamping
    configurations (overlays, underlays, rotations, opacities, and percentage sizing),
    and verifies it against the visual baseline.
    """
    # 1. Create a test image (vibrant checkerboard pattern)
    stamp_img_path = tmp_path / "stamp_pattern.png"
    img = Image.new("RGB", (100, 100), color="white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 50, 50], fill=(255, 100, 100))
    draw.rectangle([50, 50, 100, 100], fill=(100, 100, 255))
    img.save(stamp_img_path, format="PNG")

    # 2. Initialize a blank 5-page PDF with size 400x400
    pdf = pikepdf.Pdf.new()
    for _ in range(5):
        pdf.add_blank_page(page_size=(400, 400))

    # 3. Apply Stamp Variations page-by-page

    # Page 1: Standard top-right overlay with partial opacity
    stamp_images_on_pdf(
        pdf=pdf,
        images=[stamp_img_path],
        pages="1",
        underlay=False,
        position="top-right",
        width="120pt",
        height="80pt",
        opacity=0.7,
    )

    # Page 2: Center underlay (behind text)
    # First, write some text on Page 2
    font_dict = pdf.make_indirect(
        {
            "/Type": pikepdf.Name.Font,
            "/Subtype": pikepdf.Name.Type1,
            "/BaseFont": pikepdf.Name.Helvetica,
        }
    )
    pdf.pages[1].Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font_dict))
    pdf.pages[1].Contents = pdf.make_stream(b"BT /F1 24 Tf 80 200 Td (FOREGROUND TEXT) Tj ET\n")

    stamp_images_on_pdf(
        pdf=pdf,
        images=[stamp_img_path],
        pages="2",
        underlay=True,
        position="center",
        width="250pt",
        height="250pt",
        scale_mode="fit",
    )

    # Page 3: Relative percentage sizing and center-left alignment with custom offsets
    stamp_images_on_pdf(
        pdf=pdf,
        images=[stamp_img_path],
        pages="3",
        underlay=False,
        position="center-left",
        width="50%",
        height="50%",
        offset_x="20pt",
        offset_y="-10pt",
    )

    # Page 4: Visual page rotation (90 degrees) with top-left anchor stamping
    pdf.pages[3].Rotate = 90
    stamp_images_on_pdf(
        pdf=pdf,
        images=[stamp_img_path],
        pages="4",
        underlay=False,
        position="top-left",
        width="100pt",
        height="100pt",
    )

    # Page 5: Watermark style scaling (fill) with extreme transparency
    stamp_images_on_pdf(
        pdf=pdf,
        images=[stamp_img_path],
        pages="5",
        underlay=False,
        position="center",
        width="100%",
        height="100%",
        scale_mode="fill",
        opacity=0.25,
    )

    # 4. Perform visual matching
    assert_pdf_match(pdf)
