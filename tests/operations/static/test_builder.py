import os
from pathlib import Path
import pytest

try:
    from playwright.sync_api import Page, expect

    HAVE_PLAYWRIGHT = True
except ImportError:
    Page, expect = any, None
    HAVE_PLAYWRIGHT = False

pytestmark = pytest.mark.skipif(
    not HAVE_PLAYWRIGHT or os.environ.get("CI") == "true",
    reason="Playwright UI tests run locally only",
)

# Resolve path dynamically relative to this test file
TESTS_STATIC_DIR = Path(__file__).resolve().parent
HTML_FILE_PATH = (
    TESTS_STATIC_DIR.parents[2] / "src" / "pdftl" / "operations" / "static" / "builder.html"
)
HTML_URL = HTML_FILE_PATH.as_uri()


@pytest.fixture(autouse=True)
def mock_api(page: Page):
    """Intercepts and mocks the pdftl API backend."""
    # Mock status endpoint
    page.route(
        "**/v1/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "operations": {
                    "cat": {"description": "Concatenate pages", "usage": "cat [pages]"},
                    "filter": {"description": "No-op filter", "usage": "filter"},
                    "crop": {"description": "Crop pages", "usage": "crop [box]"},
                }
            },
        ),
    )

    # Mock execution endpoint returning a dummy PDF
    page.route(
        "**/v1/execute/pipeline",
        lambda route: route.fulfill(
            status=200, content_type="application/pdf", body=b"%PDF-1.4 dummy pdf bytes"
        ),
    )


def test_url_backfill_behavior(page: Page):
    """Verify that specifying op2 but not op1 backfills op1 with 'filter'."""
    # Load with gaps in the URL parameters (api parameter removed)
    page.goto(f"{HTML_URL}?op2=crop")

    # Locate steps
    steps = page.locator(".step")
    expect(steps).to_have_count(2)

    # Step 1 should have backfilled to 'filter'
    step1_op = steps.nth(0).locator(".stepOpSelect")
    expect(step1_op).to_have_value("filter")

    # Step 2 should be the user's defined 'crop'
    step2_op = steps.nth(1).locator(".stepOpSelect")
    expect(step2_op).to_have_value("crop")


def test_auto_download_on_run(page: Page):
    """Verify that clicking 'Run' triggers both mock API and auto-download."""
    page.goto(HTML_URL)

    # Upload a dummy PDF to satisfy input validation and enable the Run button
    page.set_input_files(
        "#fileInput",
        {"name": "test.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 dummy input"},
    )

    # Ensure the upload registered and the button is active
    expect(page.locator(".file-chip")).to_be_visible()
    expect(page.locator("#runBtn")).to_be_enabled()

    # Run pipeline and listen for the triggered browser download event
    with page.expect_download() as download_info:
        page.click("#runBtn")

    download = download_info.value
    assert download.suggested_filename == "pdftl_output.pdf"


def test_reconstructed_pipeline_link(page: Page, context):
    """Verify the copy link button generates query params of current page state."""
    # Grant clipboard permissions to the headless browser
    context.grant_permissions(["clipboard-read", "clipboard-write"])

    page.goto(HTML_URL)

    # Add a second step and change its settings
    page.click("#addStepBtn")
    steps = page.locator(".step")
    steps.nth(1).locator(".stepOpSelect").select_option("crop")
    steps.nth(1).locator(".argsInput").fill("1-5")

    # Click copy link
    page.click("#shareBtn")

    # Read browser clipboard and assert query parameters are reconstructed
    clipboard_text = page.evaluate("navigator.clipboard.readText()")

    assert "op1=cat" in clipboard_text
    assert "op2=crop" in clipboard_text
    assert "args2=1-5" in clipboard_text
