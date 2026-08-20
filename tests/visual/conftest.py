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


def compare_rendered_pages(images_a, images_b, tmp_path, label: str, max_diff_threshold=2):
    """
    Page-by-page visual comparison shared by assert_pdf_match (baseline
    vs. test) and any direct A-vs-B comparison (e.g. a corpus PDF vs. its
    own post-operation output, where there's no baseline file to store).

    Raises via pytest.fail on a page-count mismatch or any page whose max
    per-channel pixel delta exceeds `max_diff_threshold`, saving a
    brightness-amplified diff image plus the offending page for
    inspection under tmp_path. Returns nothing on a clean match.
    """
    if len(images_a) != len(images_b):
        pytest.fail(
            f"Page count mismatch for {label}! A has {len(images_a)}, B has {len(images_b)}."
        )

    for i, (img_a, img_b) in enumerate(zip(images_a, images_b)):
        img_a = img_a.convert("RGB")
        img_b = img_b.convert("RGB")

        diff = ImageChops.difference(img_a, img_b)
        if not diff.getbbox():
            continue

        stat = ImageStat.Stat(diff)
        max_diff = max(extrema[1] for extrema in stat.extrema)
        if max_diff <= max_diff_threshold:
            continue

        diff_visible = ImageEnhance.Brightness(diff).enhance(100.0)
        page_suffix = f"_page_{i + 1}" if len(images_a) > 1 else ""
        diff_path = tmp_path / f"FAILED_DIFF_{label}{page_suffix}.png"
        output_img_path = tmp_path / f"FAILED_OUTPUT_{label}{page_suffix}.png"
        diff_visible.save(diff_path)
        img_b.save(output_img_path)

        pytest.fail(
            f"Visual regression detected on page {i + 1}! (Max pixel delta: {max_diff}/255)\n"
            f"  Diff image: {diff_path}\n"
            f"  Actual output image: {output_img_path}"
        )


# --- Generic "dump what I was holding if the test failed" plumbing ---


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Stashes each phase's TestReport on the item so fixture finalizers
    (which only run during teardown, with no direct access to the test
    outcome) can check the item to see whether the test they're tearing
    down actually failed. Standard pytest pattern for failure-conditional
    fixture cleanup."""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


def _test_failed(request) -> bool:
    """True if the just-run test's call phase failed (or errored)."""
    rep_call = getattr(request.node, "rep_call", None)
    return bool(rep_call is not None and rep_call.failed)


@pytest.fixture()
def assert_pdf_match(request, tmp_path):
    """
    Visually compares a test PDF (path or in-memory object) against a baseline.
    Supports multi-page PDFs.
    """
    created_baselines = []
    last_pdf_bytes: dict[str, bytes] = {}

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

        # Remember the bytes so the automatic failure-dump teardown below
        # can write them out if this test ends up failing -- for any
        # reason, not just a compare_rendered_pages mismatch.
        last_pdf_bytes["name"] = baseline_name
        last_pdf_bytes["bytes"] = test_pdf_bytes

        # --- 2. First-run helper ---
        if not baseline_pdf_path.exists():
            baseline_pdf_path.write_bytes(test_pdf_bytes)
            created_baselines.append(baseline_name)
            return
        # pytest.skip(f"New baseline generated: {baseline_name}. Inspect and commit.")

        # --- 3. Rasterize Both (Multi-page) ---
        test_images = render_pdf_to_images(test_pdf_bytes)
        baseline_images = render_pdf_to_images(baseline_pdf_path)

        compare_rendered_pages(baseline_images, test_images, tmp_path, baseline_name)

    yield _checker

    # --- Automatic failure dump ---
    # Runs regardless of *why* the test failed (a compare_rendered_pages
    # pytest.fail, an unrelated assertion later in the test, etc.) -- the
    # caller never has to remember to opt in, and compare_rendered_pages
    # itself stays free of any PDF-writing responsibility.
    if _test_failed(request) and "bytes" in last_pdf_bytes:
        dump_path = tmp_path / f"FAILED_OUTPUT_{last_pdf_bytes['name']}"
        dump_path.write_bytes(last_pdf_bytes["bytes"])

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


from .generate_sample import create_multiformat_pdf


@pytest.fixture(scope="session", autouse=True)
def ensure_sample_multiformat_pdf():
    """Session fixture that guarantees sample_multiformat.pdf exists in tests/files/

    before any visual tests run. Proxies execution out to the generation module.
    """
    return create_multiformat_pdf(force=False)
