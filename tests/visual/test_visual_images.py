import io

import pikepdf
from PIL import Image, ImageDraw

from pdftl.utils.images import convert_image_dict_to_grayscale


def test_visual_grayscale_image_conversion(assert_pdf_match):
    """
    Verifies that a standard color image is correctly neutralized to grayscale.

    The 'before' baseline shows a vibrant color image, and the 'after' baseline
    shows the successfully converted grayscale version.
    """
    # --- 1. Create a valid color JPEG in memory ---
    # We use a solid bright red color so the grayscale transformation
    # is stark and obvious to a human reviewer.
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
    img_xobj["/ColorSpace"] = pikepdf.Name("/DeviceRGB")  # Valid, renderable color space

    page = pdf.pages[0]
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=img_xobj))
    # Draw the image in the center of the page
    page.Contents = pdf.make_stream(b"q 200 0 0 200 100 100 cm /Im1 Do Q")

    # --- 3. Snapshot the BEFORE state ---
    # This creates a baseline file showing a bright red square.
    assert_pdf_match(pdf, suffix="before")

    # --- 4. Run the target conversion logic ---
    img_meta = {"xobj": img_xobj, "format": "dctdecode"}
    success = convert_image_dict_to_grayscale(img_meta, 75)
    assert success is True, "Image conversion failed unexpectedly."

    # --- 5. Snapshot the AFTER state ---
    # This creates a baseline file showing the neutralized dark gray square.
    assert_pdf_match(pdf, suffix="after")


def test_visual_grayscale_multi_color_grid(assert_pdf_match):
    """
    Verifies grayscale conversion across a variety of primary and secondary
    colors to ensure correct luminosity mapping in the visual baseline.
    """
    # --- 1. Create a 2x2 color quadrant image ---
    img = Image.new("RGB", (200, 200))
    draw = ImageDraw.Draw(img)

    # Paint 4 distinct color zones (100x100 pixels each)
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
    # Center the 200x200 grid on the 400x400 canvas
    page.Contents = pdf.make_stream(b"q 200 0 0 200 100 100 cm /Im1 Do Q")

    # --- 3. Snapshot the BEFORE state (Beautiful 4-color grid) ---
    assert_pdf_match(pdf, suffix="before")

    # --- 4. Run the target conversion logic ---
    img_meta = {"xobj": img_xobj, "format": "dctdecode"}
    success = convert_image_dict_to_grayscale(img_meta, 75)
    assert success is True, "Image conversion failed unexpectedly."

    # --- 5. Snapshot the AFTER state (4 distinct shades of gray) ---
    assert_pdf_match(pdf, suffix="after")
