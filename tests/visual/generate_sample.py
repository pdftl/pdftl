# tests/visual/generate_sample.py
import io
import os
import zlib
from pathlib import Path
from PIL import Image, ImageDraw
import pikepdf
from pikepdf import Name

# Stable public domain Wikimedia reference asset destination
PHOTO_URL = "https://upload.wikimedia.org/wikipedia/commons/c/c1/Wikipedia-sipi-image-db-mandrill-4.2.03.png"

VISUAL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = VISUAL_DIR.parent.parent
SAMPLE_PDF_PATH = PROJECT_ROOT / "tests" / "files" / "pdfs" / "sample_multiformat.pdf"
MANDRILL_CROP = PROJECT_ROOT / "tests" / "files" / "images" / "mandrill_crop.png"


def _generate_synthetic_photo(size):
    """Generates a high-contrast local synthetic asset profile."""
    fallback = Image.new("RGB", size, color=(128, 128, 128))
    draw = ImageDraw.Draw(fallback)
    for i in range(0, size[0], 20):
        draw.line([i, 0, i, size[1]], fill=(i % 255, 255 - (i % 255), 100), width=2)
        draw.line([0, i, size[0], i], fill=(255 - (i % 255), 100, i % 255), width=2)
    draw.ellipse([50, 50, size[0] - 50, size[1] - 50], outline=(255, 255, 255), width=4)
    return fallback


def create_multiformat_pdf(force=False):
    """Generates a multi-format sample PDF inside tests/files/ directly from memory."""
    if os.path.exists(SAMPLE_PDF_PATH) and not force:
        return SAMPLE_PDF_PATH

    # Reverted to standard 300x300 asset dimensions
    size = (300, 300)

    # Fetch remote image directly into memory stream
    try:
        # req = urllib.request.Request(
        #     PHOTO_URL, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        # )
        # with urllib.request.urlopen(req, timeout=7) as response:
        #     remote_stream = io.BytesIO(response.read())
        # real_photo = (
        #     Image.open(remote_stream).convert("RGB").resize(size, Image.Resampling.LANCZOS)
        # )
        real_photo = Image.open(MANDRILL_CROP)
    except Exception as e:
        if os.path.exists(SAMPLE_PDF_PATH):
            os.remove(SAMPLE_PDF_PATH)
        raise RuntimeError(f"Required remote test asset fetch failed: {e}") from e

    pdf = pikepdf.Pdf.new()

    # Define a shared standard Helvetica font resource for rendering text streams
    font_dict = pdf.make_indirect(
        {"/Type": Name.Font, "/Subtype": Name.Type1, "/BaseFont": Name.Helvetica}
    )

    # --- RGB (Remote Mandrill Asset) ---
    pagenum = 1
    buf_rgb = io.BytesIO()
    real_photo.save(buf_rgb, format="JPEG", quality=80)
    p1 = pdf.add_blank_page(page_size=(400, 400))
    x_rgb = pdf.make_stream(buf_rgb.getvalue())
    x_rgb.update(
        {
            "/Type": Name("/XObject"),
            "/Subtype": Name("/Image"),
            "/Width": 128,
            "/Height": 128,
            "/BitsPerComponent": 8,
            "/Filter": Name("/DCTDecode"),
            "/ColorSpace": Name("/DeviceRGB"),
        }
    )
    p1.Resources = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary({"/ImRGB": x_rgb}),
            "/Font": pikepdf.Dictionary({"/F1": font_dict}),
        }
    )
    p1.Contents = pdf.make_stream(
        (
            "q 300 0 0 300 50 80 cm /ImRGB Do Q "
            f"BT /F1 12 Tf 50 40 Td (Page {pagenum}: Standard Additive RGB Color Space - Mandrill) Tj ET"
        ).encode()
    )

    # --- CMYK (Mandrill Asset Conversion) ---
    pagenum += 1
    # Convert the real photo directly to CMYK subtractive channels
    real_cmyk = real_photo.convert("CMYK")

    p_cmyk_monkey = pdf.add_blank_page(page_size=(400, 400))
    x_cmyk_monkey = pdf.make_stream(zlib.compress(real_cmyk.tobytes(), level=9))
    x_cmyk_monkey.update(
        {
            "/Type": Name("/XObject"),
            "/Subtype": Name("/Image"),
            "/Width": 128,
            "/Height": 128,
            "/BitsPerComponent": 8,
            "/Filter": Name("/FlateDecode"),
            "/ColorSpace": Name("/DeviceCMYK"),
        }
    )
    p_cmyk_monkey.Resources = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary({"/ImCMYKMonkey": x_cmyk_monkey}),
            "/Font": pikepdf.Dictionary({"/F1": font_dict}),
        }
    )
    p_cmyk_monkey.Contents = pdf.make_stream(
        (
            "q 300 0 0 300 50 80 cm /ImCMYKMonkey Do Q "
            f"BT /F1 12 Tf 50 40 Td (Page {pagenum}: Subtractive CMYK Space - Mandrill) Tj ET"
        ).encode()
    )
    # --- 8-Bit Grayscale Continuous Tone (Mandrill Conversion) ---
    pagenum += 1
    real_gray = real_photo.convert("L")
    p5 = pdf.add_blank_page(page_size=(400, 400))
    x_gray = pdf.make_stream(zlib.compress(real_gray.tobytes(), level=9))
    x_gray.update(
        {
            "/Type": Name("/XObject"),
            "/Subtype": Name("/Image"),
            "/Width": 128,
            "/Height": 128,
            "/BitsPerComponent": 8,
            "/Filter": Name("/FlateDecode"),
            "/ColorSpace": Name("/DeviceGray"),
        }
    )
    p5.Resources = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary({"/ImGray8": x_gray}),
            "/Font": pikepdf.Dictionary({"/F1": font_dict}),
        }
    )
    p5.Contents = pdf.make_stream(
        (
            "q 300 0 0 300 50 80 cm /ImGray8 Do Q "
            f"BT /F1 12 Tf 50 40 Td (Page {pagenum}: Continuous 8-Bit Grayscale - Mandrill) Tj ET"
        ).encode()
    )

    # --- CMYK (Bright Local Synthetic Reference) ---
    pagenum += 1
    img_cmyk = Image.new("CMYK", size, color=(0, 0, 0, 0))
    draw_cmyk = ImageDraw.Draw(img_cmyk)
    for i in range(0, size[0], 20):
        draw_cmyk.line([i, 0, i, size[1]], fill=(255, 0, 50, 0), width=2)
        draw_cmyk.line([0, i, size[0], i], fill=(0, 255, 200, 0), width=2)
    draw_cmyk.ellipse([50, 50, size[0] - 50, size[1] - 50], outline=(255, 255, 0, 0), width=4)

    raw_cmyk_bytes = img_cmyk.tobytes()
    compressed_cmyk = zlib.compress(raw_cmyk_bytes, level=9)

    p2 = pdf.add_blank_page(page_size=(400, 400))
    x_cmyk = pdf.make_stream(compressed_cmyk)
    x_cmyk.update(
        {
            "/Type": Name("/XObject"),
            "/Subtype": Name("/Image"),
            "/Width": size[0],
            "/Height": size[1],
            "/BitsPerComponent": 8,
            "/Filter": Name("/FlateDecode"),
            "/ColorSpace": Name("/DeviceCMYK"),
        }
    )
    p2.Resources = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary({"/ImCMYK": x_cmyk}),
            "/Font": pikepdf.Dictionary({"/F1": font_dict}),
        }
    )
    p2.Contents = pdf.make_stream(
        (
            "q 300 0 0 300 50 80 cm /ImCMYK Do Q "
            f"BT /F1 12 Tf 50 40 Td (Page {pagenum}: Native Subtractive CMYK Space Grid) Tj ET"
        ).encode()
    )
    # --- Alpha Transparency Object Linkage (Soft Mask / SMask) ---
    pagenum += 1
    synth_rgb = _generate_synthetic_photo(size)
    smask_img = Image.new("L", size, color=0)
    smask_draw = ImageDraw.Draw(smask_img)
    smask_draw.ellipse([30, 30, size[0] - 30, size[1] - 30], fill=255)

    x_smask = pdf.make_stream(zlib.compress(smask_img.tobytes(), level=9))
    x_smask.update(
        {
            "/Type": Name("/XObject"),
            "/Subtype": Name("/Image"),
            "/Width": size[0],
            "/Height": size[1],
            "/BitsPerComponent": 8,
            "/Filter": Name("/FlateDecode"),
            "/ColorSpace": Name("/DeviceGray"),
        }
    )

    x_base = pdf.make_stream(zlib.compress(synth_rgb.tobytes(), level=9))
    x_base.update(
        {
            "/Type": Name("/XObject"),
            "/Subtype": Name("/Image"),
            "/Width": size[0],
            "/Height": size[1],
            "/BitsPerComponent": 8,
            "/Filter": Name("/FlateDecode"),
            "/ColorSpace": Name("/DeviceRGB"),
            "/SMask": x_smask,
        }
    )

    p7 = pdf.add_blank_page(page_size=(400, 400))
    p7.Resources = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary({"/ImTransparent": x_base}),
            "/Font": pikepdf.Dictionary({"/F1": font_dict}),
        }
    )
    p7.Contents = pdf.make_stream(
        (
            "q 300 0 0 300 50 80 cm /ImTransparent Do Q "
            f"BT /F1 12 Tf 50 40 Td (Page {pagenum}: 8-Bit RGB Base with a Secondary /SMask Object) Tj ET"
        ).encode()
    )

    # --- 1-bit Monochrome Scan (Local Synthetic - Flate Compressed) ---
    pagenum += 1
    img_1bit = Image.new("1", size, color=1)
    draw_1bit = ImageDraw.Draw(img_1bit)
    draw_1bit.rectangle([20, 20, 280, 35], fill=0)
    draw_1bit.line([20, 45, 280, 45], fill=0, width=2)
    for y_offset in range(60, 260, 20):
        draw_1bit.rectangle([25, y_offset, 80, y_offset + 10], fill=0)
        draw_1bit.rectangle([95, y_offset, 160, y_offset + 10], fill=0)

    p3 = pdf.add_blank_page(page_size=(400, 400))
    x_1bit = pdf.make_stream(zlib.compress(img_1bit.tobytes(), level=9))
    x_1bit.update(
        {
            "/Type": Name("/XObject"),
            "/Subtype": Name("/Image"),
            "/Width": size[0],
            "/Height": size[1],
            "/BitsPerComponent": 1,
            "/Filter": Name("/FlateDecode"),
            "/ColorSpace": Name("/DeviceGray"),
        }
    )
    p3.Resources = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary({"/Im1Bit": x_1bit}),
            "/Font": pikepdf.Dictionary({"/F1": font_dict}),
        }
    )
    p3.Contents = pdf.make_stream(
        (
            "q 300 0 0 300 50 80 cm /Im1Bit Do Q "
            f"BT /F1 12 Tf 50 40 Td (Page {pagenum}: Pure 1-Bit Monochrome Line Art / Flate) Tj ET"
        ).encode()
    )

    # --- 1-Bit Verified Pure CCITT Group 4 Stream ---
    pagenum += 1
    ccitt_hex = """fffff22016cffffffffffff1ff9100b67e3fff2ac0a0e807ffffffffffff
fc47fe558141d00ffffffffffffff88ffcab0283a01ffffffffffffff11f
f9560507403fffffffffffffe23ff2ac0a0e807fffffffffffffc47fe558
141d00ffffffffffffff88ffcab0283a01ffffffffffffff11ff95605074
03fffffffffffffe23ff2ac0a0e807fffffffffffffc47fe558141d00fff
fffffffffff88ffffffffffffc004004"""
    # Clean any potential hidden shell/copy-paste space artifacts programmatically
    clean_hex = "".join(ccitt_hex.split())
    ccitt_bytes = bytes.fromhex(clean_hex)

    p6 = pdf.add_blank_page(page_size=(400, 400))
    x_ccitt = pdf.make_stream(ccitt_bytes)
    x_ccitt.update(
        {
            "/Type": Name("/XObject"),
            "/Subtype": Name("/Image"),
            "/Width": size[0],
            "/Height": size[1],
            "/BitsPerComponent": 1,
            "/Filter": Name("/CCITTFaxDecode"),
            "/ColorSpace": Name("/DeviceGray"),
            "/DecodeParms": pikepdf.Dictionary(
                {"/K": -1, "/Columns": size[0], "/Rows": size[1], "/BlackIs1": False}
            ),
        }
    )
    p6.Resources = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary({"/ImCCITT": x_ccitt}),
            "/Font": pikepdf.Dictionary({"/F1": font_dict}),
        }
    )
    p6.Contents = pdf.make_stream(
        (
            "q 300 0 0 300 50 80 cm /ImCCITT Do Q "
            f"BT /F1 12 Tf 50 40 Td (Page {pagenum}: 1-Bit Production CCITT Group 4 Fax Compression) Tj ET"
        ).encode()
    )

    # --- Indexed / Palette (Local Synthetic) ---
    pagenum += 1
    img_p = Image.new("P", size)
    palette = [128, 128, 128, 255, 0, 0, 0, 255, 0, 0, 0, 255, 128, 64, 192] + [0] * 753
    img_p.putpalette(palette)
    draw_p = ImageDraw.Draw(img_p)
    draw_p.rectangle([20, 20, 70, 70], fill=1)
    draw_p.rectangle([70, 70, 140, 140], fill=2)
    draw_p.rectangle([140, 140, 200, 200], fill=3)
    draw_p.rectangle([200, 200, 280, 280], fill=4)

    max_index = 4
    active_color_bytes = bytes(palette[: (max_index + 1) * 3])

    p4 = pdf.add_blank_page(page_size=(400, 400))
    x_p = pdf.make_stream(zlib.compress(img_p.tobytes(), level=9))
    x_p.update(
        {
            "/Type": Name.XObject,
            "/Subtype": Name.Image,
            "/Width": size[0],
            "/Height": size[1],
            "/BitsPerComponent": 8,
            "/Filter": Name("/FlateDecode"),
            "/ColorSpace": [Name.Indexed, Name.DeviceRGB, max_index, active_color_bytes],
        }
    )
    p4.Resources = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary({"/ImP": x_p}),
            "/Font": pikepdf.Dictionary({"/F1": font_dict}),
        }
    )
    p4.Contents = pdf.make_stream(
        (
            "q 300 0 0 300 50 80 cm /ImP Do Q "
            f"BT /F1 12 Tf 50 40 Td (Page {pagenum}: 8-Bit Indexed Color Palette Space) Tj ET"
        ).encode()
    )

    os.makedirs(SAMPLE_PDF_PATH.parent, exist_ok=True)
    pdf.save(SAMPLE_PDF_PATH)
    return SAMPLE_PDF_PATH


if __name__ == "__main__":
    try:
        create_multiformat_pdf(force=True)
        print(f"Successfully generated reference file at: {SAMPLE_PDF_PATH}")
    except RuntimeError as err:
        print(f"Generation aborted: {err}")
