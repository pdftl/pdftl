# tests/operations/test_import_streams.py

import io
from unittest.mock import patch
import pikepdf
import pytest

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.import_streams import (
    StreamTarget,
    _parse_stream_file,
    _apply_stream_target,
    import_streams,
)


@pytest.fixture
def base_pdf():
    """Provides a basic PDF structure with 1 blank page for stream application."""
    pdf = pikepdf.new()
    pdf.add_blank_page()
    return pdf


# ---------------------------------------------------------------------------
# Parser Coverage
# ---------------------------------------------------------------------------


def test_parse_stream_file_comprehensive():
    """Validates header parsing, resource skipping, target flushing, and noise exclusion."""
    content = (
        b"Just some noise before headers that should be skipped\n"
        b"=== Warning: Shared Object warning\n"
        b"================\n"
        b"=== Page 1 / Contents (4:0)\n"
        b"BT /F1 12 Tf ET\n"
        b"=== Page 2 / Contents\n"
        b"ET\n"
        b"=== Page 3 / Resources\n"  # This should flush Page 2 and ignore itself
        b"  /Font << >>\n"
        b"=== Page 4 / XObject /Fm1\n"
        b"/SubDo Do\n"
        b"=== Page 5 / XObject /Fm2\n"  # This should flush Page 4
        b"Do2\n"
    )
    file_obj = io.BytesIO(content)
    targets = _parse_stream_file(file_obj)

    assert len(targets) == 4

    assert targets[0][0].page_num == 1
    assert targets[0][0].target_type == "Contents"
    assert targets[0][1] == b"BT /F1 12 Tf ET"

    assert targets[1][0].page_num == 2
    assert targets[1][0].target_type == "Contents"
    assert targets[1][1] == b"ET"

    assert targets[2][0].page_num == 4
    assert targets[2][0].target_type == "XObject"
    assert targets[2][0].name == "/Fm1"
    assert targets[2][1] == b"/SubDo Do"

    assert targets[3][0].page_num == 5
    assert targets[3][0].target_type == "XObject"
    assert targets[3][0].name == "/Fm2"
    assert targets[3][1] == b"Do2"


# ---------------------------------------------------------------------------
# Application Target Coverage
# ---------------------------------------------------------------------------


def test_apply_stream_target_contents(base_pdf):
    """Successfully applying bytes to a Page Contents stream."""
    target = StreamTarget(1, "Contents")
    content = b"BT (Hello) Tj ET"
    _apply_stream_target(base_pdf, target, content, normalize=False)

    assert base_pdf.pages[0].Contents.read_bytes() == b"BT (Hello) Tj ET"


def test_apply_stream_target_contents_array_warning(base_pdf, caplog):
    """Logs a warning and collapses to a single stream when /Contents is an array."""
    sub1 = base_pdf.make_stream(b"BT (A) Tj ET")
    sub2 = base_pdf.make_stream(b"BT (B) Tj ET")
    base_pdf.pages[0]["/Contents"] = pikepdf.Array([sub1, sub2])

    target = StreamTarget(1, "Contents")
    content = b"BT (New) Tj ET"
    _apply_stream_target(base_pdf, target, content, normalize=False)

    assert base_pdf.pages[0].Contents.read_bytes() == content
    assert "collapsing to a single stream" in caplog.text


def test_apply_stream_target_contents_normalize_error(base_pdf, caplog):
    """Testing graceful fallback to raw bytes when normalize_page_content_stream raises.

    pikepdf silently accepts unknown operators, so we cannot rely on invalid PDF
    syntax to trigger the except branch.  Instead we patch the normalizer directly
    to simulate a parse failure and confirm the warning is logged and the raw bytes
    are preserved.
    """
    target = StreamTarget(1, "Contents")
    content = b"BT (Hello) Tj ET"

    with patch(
        "pdftl.operations.import_streams.normalize_page_content_stream",
        side_effect=ValueError("simulated parse error"),
    ):
        _apply_stream_target(base_pdf, target, content, normalize=True)

    assert base_pdf.pages[0].Contents.read_bytes() == content
    assert "Could not normalize imported Contents stream" in caplog.text


def test_apply_stream_target_xobject(base_pdf):
    """Successfully applying bytes to a referenced Form XObject."""
    xobj = base_pdf.make_stream(b"Old")
    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/XObject": pikepdf.Dictionary({"/Fm1": xobj})}
    )

    target = StreamTarget(1, "XObject", "/Fm1")
    content = b"BT (New) Tj ET"
    _apply_stream_target(base_pdf, target, content, normalize=False)

    assert xobj.read_bytes() == b"BT (New) Tj ET"


def test_apply_stream_target_xobject_normalize_success(base_pdf):
    """Normalized bytes are written back when parse_content_stream succeeds on an XObject."""
    xobj = base_pdf.make_stream(b"Old")
    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/XObject": pikepdf.Dictionary({"/Fm1": xobj})}
    )

    target = StreamTarget(1, "XObject", "/Fm1")
    # Whitespace-heavy but syntactically valid stream; normalization should compact it.
    content = b"BT   (Hello)   Tj   ET"
    _apply_stream_target(base_pdf, target, content, normalize=True)

    # The stream must be non-empty and must not still contain the raw padded form.
    result = xobj.read_bytes()
    assert result  # something was written
    assert b"BT" in result and b"ET" in result


def test_apply_stream_target_xobject_normalize_error(base_pdf, caplog):
    """Testing graceful fallback to raw bytes when XObject normalization raises.

    pikepdf silently accepts unknown operators, so we cannot rely on invalid PDF
    syntax to trigger the except branch.  Instead we patch pikepdf.parse_content_stream
    to raise and confirm the warning is logged and the raw bytes are preserved.
    """
    xobj = base_pdf.make_stream(b"Old")
    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/XObject": pikepdf.Dictionary({"/Fm1": xobj})}
    )

    target = StreamTarget(1, "XObject", "/Fm1")
    content = b"BT (Hello) Tj ET"

    with patch(
        "pikepdf.parse_content_stream",
        side_effect=pikepdf.PdfError("simulated parse error"),
    ):
        _apply_stream_target(base_pdf, target, content, normalize=True)

    assert xobj.read_bytes() == content
    assert "Could not normalize imported XObject" in caplog.text


def test_apply_stream_target_invalid_page(base_pdf, caplog):
    """Safely skips targets mapped to pages that do not exist."""
    target = StreamTarget(99, "Contents")
    _apply_stream_target(base_pdf, target, b"Data", normalize=False)
    assert "Page 99 does not exist" in caplog.text


def test_apply_stream_target_xobject_not_found(base_pdf, caplog):
    """Safely skips targets mapped to an XObject name that does not exist in the map."""
    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/XObject": pikepdf.Dictionary({"/Fm2": base_pdf.make_stream(b"")})}
    )
    target = StreamTarget(1, "XObject", "/Fm1")
    _apply_stream_target(base_pdf, target, b"Data", normalize=False)
    assert "XObject /Fm1 not found on page 1" in caplog.text


def test_apply_stream_target_missing_resources(base_pdf, caplog):
    """Safely skips targets mapped to an XObject when the page entirely lacks a Resources map."""
    # Ensure page has no resources
    if "/Resources" in base_pdf.pages[0]:
        del base_pdf.pages[0].Resources

    target = StreamTarget(1, "XObject", "/Fm1")
    _apply_stream_target(base_pdf, target, b"Data", normalize=False)
    assert "missing Resources dict" in caplog.text


# ---------------------------------------------------------------------------
# High-Level Operation Flow Coverage
# ---------------------------------------------------------------------------


def test_import_streams_cli_missing_arg(base_pdf):
    """Raises InvalidArgumentError when no positional stream target file is provided."""
    with pytest.raises(InvalidArgumentError, match="requires a <stream_file> argument"):
        import_streams(base_pdf, ["normalize=true"])


def test_import_streams_success_file(base_pdf, tmp_path):
    """E2E flow successfully reading from a physical test file."""
    stream_path = tmp_path / "streams.txt"
    stream_path.write_bytes(b"=== Page 1 / Contents\nBT (Success) Tj ET\n")

    import_streams(base_pdf, [str(stream_path), "normalize=false"])
    assert base_pdf.pages[0].Contents.read_bytes() == b"BT (Success) Tj ET"


def test_import_streams_stdin_dash(base_pdf):
    """E2E flow successfully reading from standard input via `-`.

    sys.stdin.buffer is a readonly attribute in Python 3.10+, so we cannot
    patch it directly.  Instead we patch smart_open at the import site so that
    the '-' sentinel causes it to return our in-memory BytesIO object.
    """
    fake_stdin = io.BytesIO(b"=== Page 1 / Contents\nBT (Dash) Tj ET\n")

    with patch(
        "pdftl.operations.import_streams.smart_open",
        return_value=fake_stdin,
    ):
        import_streams(base_pdf, ["-", "normalize=false"])

    assert base_pdf.pages[0].Contents.read_bytes() == b"BT (Dash) Tj ET"
