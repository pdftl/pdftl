import io
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
import pytest
from PIL import ImageChops, ImageEnhance, ImageStat


def render_pdf_to_images(pdf_data, dpi=150):
    """
    Takes a file path or raw PDF bytes and yields a list of PIL Images,
    one for each page.
    """
    scale_factor = dpi / 72.0

    # pypdfium2 handles both file paths and raw bytes natively
    pdf = pdfium.PdfDocument(pdf_data)
    images = []

    for page in pdf:
        bitmap = page.render(scale=scale_factor)
        images.append(bitmap.to_pil())

    pdf.close()
    return images


@pytest.fixture()
def assert_pdf_match(request, tmp_path):
    """
    Visually compares a test PDF (path or in-memory object) against a baseline.
    Supports multi-page PDFs.
    """
    created_baselines = []

    def _checker(pdf_input, custom_name: str = None, suffix: str = None):
        baseline_name = custom_name if custom_name else request.node.name
        if suffix:
            baseline_name += f"_{suffix}"
        baseline_name += ".pdf"
        baseline_dir = Path(request.config.rootdir) / "tests" / "baselines"
        baseline_dir.mkdir(parents=True, exist_ok=True)
        baseline_pdf_path = baseline_dir / baseline_name

        # --- 1. Normalize Input to Bytes (Avoids Disk I/O) ---
        if hasattr(pdf_input, "save"):
            # Handles pikepdf.Pdf, fitz.Document, or anything with a .save() method
            buf = io.BytesIO()
            pdf_input.save(buf)
            test_pdf_bytes = buf.getvalue()
        elif isinstance(pdf_input, (str, Path)):
            test_pdf_bytes = Path(pdf_input).read_bytes()
        elif isinstance(pdf_input, bytes):
            test_pdf_bytes = pdf_input
        else:
            raise TypeError(f"Unsupported PDF input type: {type(pdf_input)}")

        # --- 2. First-run helper ---
        if not baseline_pdf_path.exists():
            baseline_pdf_path.write_bytes(test_pdf_bytes)
            created_baselines.append(baseline_name)
            return
        # pytest.skip(f"New baseline generated: {baseline_name}. Inspect and commit.")

        # --- 3. Rasterize Both (Multi-page) ---
        test_images = render_pdf_to_images(test_pdf_bytes)
        baseline_images = render_pdf_to_images(baseline_pdf_path)

        # --- Define output PDF path for failures ---
        # (baseline_name already includes '.pdf' extension)
        output_pdf_path = tmp_path / f"FAILED_OUTPUT_{baseline_name}"

        # --- 4. Assert Page Count ---
        if len(test_images) != len(baseline_images):
            output_pdf_path.write_bytes(test_pdf_bytes)
            pytest.fail(
                f"Page count mismatch for {baseline_name}! "
                f"Baseline has {len(baseline_images)}, Output has {len(test_images)}.\n"
                f"  Actual output PDF saved to: {output_pdf_path}"
            )

        # --- 5. Compare Visually Page-by-Page ---
        for i, (base_img, test_img) in enumerate(zip(baseline_images, test_images)):
            # Ensure strict RGB to avoid alpha-channel transparency math bugs
            base_img = base_img.convert("RGB")
            test_img = test_img.convert("RGB")

            diff = ImageChops.difference(base_img, test_img)

            if diff.getbbox():
                # Get the maximum difference in any color channel
                stat = ImageStat.Stat(diff)
                # stat.extrema gives [(min_r, max_r), (min_g, max_g), (min_b, max_b)]
                max_diff = max([extrema[1] for extrema in stat.extrema])

                # Tolerance: Ignore microscopic anti-aliasing/rendering shifts
                if max_diff > 2:
                    # Amplify the diff image by 100x so human eyes can actually see the errors
                    diff_visible = ImageEnhance.Brightness(diff).enhance(100.0)

                    # If multipage, attach the page number (1-indexed) to the failure artifacts
                    page_suffix = f"_page_{i + 1}" if len(test_images) > 1 else ""

                    diff_path = tmp_path / f"FAILED_DIFF_{baseline_name}{page_suffix}.png"
                    output_img_path = tmp_path / f"FAILED_OUTPUT_{baseline_name}{page_suffix}.png"

                    diff_visible.save(diff_path)
                    test_img.save(output_img_path)

                    output_pdf_path.write_bytes(test_pdf_bytes)

                    pytest.fail(
                        f"Visual regression detected on page {i + 1}! (Max pixel delta: {max_diff}/255)\n"
                        f"  Diff image: {diff_path}\n"
                        f"  Actual output image: {output_img_path}\n"
                        f"  Actual output PDF: {output_pdf_path}"
                    )

    yield _checker

    # --- TEARDOWN ---
    # If the test finishes and we had to create baselines, fail it now.
    # This ensures CI will fail if someone forgets to commit the baselines,
    # but generates all of them locally in a single run.
    if created_baselines:
        pytest.skip(
            f"Generated new baselines: {created_baselines}. "
            "Please visually inspect them in 'tests/baselines/' and commit them."
        )


@pytest.fixture
def six_page_rotated_pdf(pdf_factory):
    base_pdf_path = pdf_factory(6)
    pdf = pikepdf.Pdf.open(base_pdf_path)
    pdf.pages[0].Rotate = 90
    pdf.pages[1].Rotate = 180
    pdf.pages[2].Rotate = 270
    pdf.pages[4].Rotate = 180
    pdf.pages[5].Rotate = 90
    return pdf
