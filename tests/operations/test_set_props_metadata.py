import pytest
import pikepdf

from pdftl.operations.set_props import (
    _parse_to_datetime,
    _format_date_for_docinfo,
    _format_date_for_xmp,
    _apply_cli_to_docinfo,
    _backfill_xmp,
    _apply_cli_to_xmp,
    _apply_metadata,
)
from pdftl.exceptions import OperationError


def test_parse_to_datetime():
    """Test parsing both legacy PDF dates and modern ISO dates."""
    # Legacy string
    dt_legacy = _parse_to_datetime("D:20220518020122Z")
    assert dt_legacy.year == 2022
    assert dt_legacy.month == 5

    # ISO string
    dt_iso = _parse_to_datetime("2026-05-03T12:00:00")
    assert dt_iso.year == 2026
    assert dt_iso.month == 5


def test_format_date_for_docinfo():
    """Test date conversion for the legacy /Info dict."""
    res = _format_date_for_docinfo("2026-05-03T12:00:00")
    assert res.startswith("D:20260503")


def test_format_date_for_xmp():
    """Test date conversion for the XMP stream."""
    res = _format_date_for_xmp("D:20260503120000Z")
    assert res.startswith("2026-05-03")


def test_apply_cli_to_docinfo():
    """Test that explicit CLI kwargs go into the /Info dict."""
    pdf = pikepdf.Pdf.new()
    kwargs = {"title": "Test Title", "author": "Jane Doe"}

    _apply_cli_to_docinfo(pdf, kwargs)

    assert str(pdf.docinfo["/Title"]) == "Test Title"
    assert str(pdf.docinfo["/Author"]) == "Jane Doe"


def test_apply_cli_to_xmp():
    """Test that explicit CLI kwargs go into the XMP dict."""
    meta = {}
    kwargs = {"title": "Test Title", "author": "Jane Doe"}

    _apply_cli_to_xmp(meta, kwargs)

    assert meta["dc:title"] == "Test Title"
    assert meta["dc:creator"] == ["Jane Doe"]


def test_backfill_xmp_success_and_skip():
    """Test that backfill populates missing XMP data but skips existing."""
    meta = {"dc:title": "Already Here"}  # Title already exists in XMP
    preserved_info = {
        "/Title": "Legacy Title",  # Should be skipped (already in XMP)
        "/Subject": "Legacy Subject",  # Should be backfilled
    }

    _backfill_xmp(meta, preserved_info, pikepdf)

    assert meta["dc:title"] == "Already Here"  # Not overwritten
    assert meta["dc:description"] == "Legacy Subject"  # Successfully backfilled


def test_backfill_xmp_exception_handling(caplog):
    """Test that broken legacy data logs a debug message and doesn't crash."""
    meta = {}
    # Provide a garbage string for a date field to trigger a ValueError/TypeError
    preserved_info = {"/CreationDate": "not_a_valid_date"}

    _backfill_xmp(meta, preserved_info, pikepdf)

    # The dictionary should be empty because the bad date was skipped
    assert "xmp:CreateDate" not in meta
    # Ensure it was caught silently
    assert "Failed to backfill xmp:CreateDate to XMP" in caplog.text


def test_apply_metadata_full_orchestration():
    """Test the entire metadata function, ensuring everything syncs correctly."""
    pdf = pikepdf.Pdf.new()
    # Pre-populate legacy info to test backfill
    pdf.docinfo["/Subject"] = "Old Subject"

    kwargs = {"title": "New CLI Title", "creationdate": "2026-01-01T12:00:00"}

    _apply_metadata(pdf, kwargs, pikepdf)

    # Check legacy /Info
    assert str(pdf.docinfo["/Title"]) == "New CLI Title"
    assert str(pdf.docinfo["/Subject"]) == "Old Subject"
    assert str(pdf.docinfo["/CreationDate"]).startswith("D:20260101")

    # Check XMP (requires opening the stream to verify)
    with pdf.open_metadata() as meta:
        assert meta["dc:title"] == "New CLI Title"
        assert meta["dc:description"] == "Old Subject"  # Proves backfill worked!
        assert "2026-01-01" in meta["xmp:CreateDate"]


def test_apply_metadata_empty_kwargs():
    """Test early return if no metadata kwargs are provided."""
    pdf = pikepdf.Pdf.new()
    # Provide an unrelated kwarg
    _apply_metadata(pdf, {"layout": "SinglePage"}, pikepdf)

    assert len(pdf.docinfo) == 0


def test_apply_metadata_open_metadata_failure(monkeypatch):
    """Test that catastrophic failures in pikepdf raise our OperationError."""
    pdf = pikepdf.Pdf.new()
    kwargs = {"title": "Will Fail"}

    # Force pikepdf to throw an error when trying to open the metadata stream
    def mock_open(*args, **kwargs):
        raise pikepdf.PdfError("Mocked catastrophic failure")

    monkeypatch.setattr(pdf, "open_metadata", mock_open)

    with pytest.raises(OperationError, match="Failed to set document metadata"):
        _apply_metadata(pdf, kwargs, pikepdf)
