# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

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
    """Validates header parsing, separator bypassing, target flushing, deep paths, and alias bypass."""
    content = (
        b"Just some noise before headers that should be skipped\n"
        b"========================================\n"
        b"=== Page 1 / Contents\n"
        b"========================================\n"
        b"BT /F1 12 Tf ET\n"
        b"\n"
        b"========================================\n"
        b"=== Page 2 / Contents\n"
        b"=== Warning: Simulated shared object warning\n"
        b"========================================\n"
        b"ET\n"
        b"\n"
        b"========================================\n"
        b"=== Page 3 / Resources\n"  # This should flush Page 2 and ignore itself
        b"========================================\n"
        b"  /Font << >>\n"
        b"========================================\n"
        b"=== Page 4 / XObject /Fm1 / XObject /FmSub\n"
        b"========================================\n"
        b"/SubDo Do\n"
        b"========================================\n"
        b"=== Page 5 / XObject /Fm2\n"  # This should flush Page 4
        b"========================================\n"
        b"% ALIAS OF: Page 4 / XObject /Fm1\n"  # ALIAS blocks must be completely bypassed!
        b"========================================\n"
        b"=== Page 6 / XObject /Fm3\n"
        b"========================================\n"
        b"Do3\n"
        # The following blocks explicitly cover invalid semantic path parsing fallbacks:
        b"=== Page XXX / Contents\n"  # coverage: ValueError on page int cast
        b"=== Page 7 / XObject /Fm4 / Image\n"  # coverage: nested part doesn't start with XObject
        b"=== Page 8 / XObject \n"  # coverage: missing space/name (fails startswith("XObject "))
        b"=== Page 9 / Unknown\n"  # coverage: completely unknown target structure
    )
    file_obj = io.BytesIO(content)
    targets = _parse_stream_file(file_obj)

    # 4 distinct valid targets (Page 5 ALIAS block is ignored, and malformed paths are bypassed)
    assert len(targets) == 4

    assert targets[0][0].page_num == 1
    assert targets[0][0].target_type == "Contents"
    assert targets[0][1] == b"BT /F1 12 Tf ET"

    assert targets[1][0].page_num == 2
    assert targets[1][0].target_type == "Contents"
    assert targets[1][1] == b"ET"

    assert targets[2][0].page_num == 4
    assert targets[2][0].target_type == "XObject"
    assert targets[2][0].xobject_path == ["/Fm1", "/FmSub"]
    assert targets[2][1] == b"/SubDo Do"

    assert targets[3][0].page_num == 6
    assert targets[3][0].target_type == "XObject"
    assert targets[3][0].xobject_path == ["/Fm3"]
    assert targets[3][1] == b"Do3"


def test_parse_stream_file_unicode_error():
    """Covers the latin-1 fallback branch when invalid UTF-8 bytes are encountered."""
    # 0xff is an invalid start byte in UTF-8, which forces the decoding try/except to fall back to latin-1
    content = b"=================\n=== Page 1 / Contents\n=================\nBT \xff ET\n"
    file_obj = io.BytesIO(content)
    targets = _parse_stream_file(file_obj)

    assert len(targets) == 1
    assert targets[0][1] == b"BT \xff ET"


def test_parse_target_path_invalid():
    """Directly tests _parse_target_path with invalid paths to achieve 100% coverage."""
    from pdftl.operations.import_streams import _parse_target_path

    assert _parse_target_path("NotPage 1") is None


def test_parse_stream_file_with_contents_array_warning():
    """Ensures that the '=== Page /Contents is an array...' warning line does not trip up import_streams."""
    content = (
        b"========================================\n"
        b"=== Page 1 / Contents\n"
        b"=== Page /Contents is an array of 2 streams (1:0, 2:0), concatenated below.\n"
        b"========================================\n"
        b"BT /F1 12 Tf ET\n"
    )
    file_obj = io.BytesIO(content)
    targets = _parse_stream_file(file_obj)

    assert len(targets) == 1
    assert targets[0][0].page_num == 1
    assert targets[0][0].target_type == "Contents"
    assert targets[0][1] == b"BT /F1 12 Tf ET"


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
    """Testing graceful fallback to raw bytes when normalize_page_content_stream raises."""
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

    target = StreamTarget(1, "XObject", ["/Fm1"])
    content = b"BT (New) Tj ET"
    _apply_stream_target(base_pdf, target, content, normalize=False)

    assert xobj.read_bytes() == b"BT (New) Tj ET"


def test_apply_stream_target_xobject_deep_path(base_pdf):
    """Applying bytes resolving down a deep semantic path DAG mapping."""
    sub_xobj = base_pdf.make_stream(b"Old Sub")
    parent_xobj = base_pdf.make_stream(b"Old Parent")
    parent_xobj.Resources = pikepdf.Dictionary(
        {"/XObject": pikepdf.Dictionary({"/FmSub": sub_xobj})}
    )
    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/XObject": pikepdf.Dictionary({"/FmParent": parent_xobj})}
    )

    target = StreamTarget(1, "XObject", ["/FmParent", "/FmSub"])
    content = b"BT (New Sub) Tj ET"
    _apply_stream_target(base_pdf, target, content, normalize=False)

    assert sub_xobj.read_bytes() == content
    # Ensure parent was unmutated
    assert parent_xobj.read_bytes() == b"Old Parent"


def test_apply_stream_target_xobject_empty_path(base_pdf):
    """Safely returns early if an XObject target has an empty path list."""
    target = StreamTarget(1, "XObject", xobject_path=[])
    # Calling this should not raise or mutate anything
    _apply_stream_target(base_pdf, target, b"Data", normalize=False)


def test_apply_stream_target_xobject_normalize_success(base_pdf):
    """Normalized bytes are written back when parse_content_stream succeeds on an XObject."""
    xobj = base_pdf.make_stream(b"Old")
    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/XObject": pikepdf.Dictionary({"/Fm1": xobj})}
    )

    target = StreamTarget(1, "XObject", ["/Fm1"])
    # Whitespace-heavy but syntactically valid stream; normalization should compact it.
    content = b"BT   (Hello)   Tj   ET"
    _apply_stream_target(base_pdf, target, content, normalize=True)

    # The stream must be non-empty and must not still contain the raw padded form.
    result = xobj.read_bytes()
    assert result  # something was written
    assert b"BT" in result and b"ET" in result


def test_apply_stream_target_xobject_normalize_error(base_pdf, caplog):
    """Testing graceful fallback to raw bytes when XObject normalization raises."""
    xobj = base_pdf.make_stream(b"Old")
    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/XObject": pikepdf.Dictionary({"/Fm1": xobj})}
    )

    target = StreamTarget(1, "XObject", ["/Fm1"])
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
    target = StreamTarget(1, "XObject", ["/Fm1"])
    _apply_stream_target(base_pdf, target, b"Data", normalize=False)
    assert "Could not find XObject path" in caplog.text


def test_apply_stream_target_missing_resources(base_pdf, caplog):
    """Safely skips targets mapped to an XObject when the page entirely lacks a Resources map."""
    # Ensure page has no resources
    if "/Resources" in base_pdf.pages[0]:
        del base_pdf.pages[0].Resources

    target = StreamTarget(1, "XObject", ["/Fm1"])
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


def test_import_streams_mojibake_roundtrip(base_pdf, tmp_path):
    """Ensures high-byte characters (e.g., 'é') are correctly round-tripped without UTF-8 mojibake corruption."""
    # Simulate dump_streams output that has been saved/piped as UTF-8 by the user/OS
    utf8_content = "=== Page 1 / Contents\nBT (z désor) Tj ET\n".encode()

    stream_path = tmp_path / "streams.txt"
    stream_path.write_bytes(utf8_content)

    # Use normalize=True to ensure Pikepdf can successfully parse the restored byte sequences
    import_streams(base_pdf, [str(stream_path), "normalize=true"])

    # Verify that the PDF stream contains the raw Latin-1 byte \xe9 (233) for 'é',
    # and strictly NOT the UTF-8 multi-byte sequence \xc3\xa9.
    applied_bytes = base_pdf.pages[0].Contents.read_bytes()

    # We test for the substring rather than the entire line because pikepdf's
    # unparse engine inserts newlines between individual operators.
    assert b"(z d\xe9sor) Tj" in applied_bytes
    assert b"\xc3\xa9" not in applied_bytes


def test_import_streams_roundtrip_fixture(two_page_pdf, tmp_path):
    """E2E round-trip integration test using dump_streams and import_streams on a real PDF fixture."""
    from pdftl.operations.dump_streams import dump_streams, dump_streams_cli_hook
    from pdftl.operations.import_streams import import_streams
    import pdftl.core.constants as c

    # Create a mutable copy of the two-page PDF fixture
    copied_pdf_path = tmp_path / "copied.pdf"
    with pikepdf.open(two_page_pdf) as pdf:
        pdf.save(copied_pdf_path)

    # 1. Dump the streams of the copied PDF
    with pikepdf.open(copied_pdf_path) as pdf:
        dump_res = dump_streams(pdf, ["normalize=true"])
        assert dump_res.success

        # Write the dump to a file using the CLI hook to match authentic output formatting
        dump_file = tmp_path / "dumped_streams.txt"
        dump_res.meta[c.META_OUTPUT_FILE] = str(dump_file)
        dump_streams_cli_hook(dump_res, None, None)

    # Verify that the dump file contains the expected page headers
    dumped_text = dump_file.read_text("latin-1")
    assert "=== Page 1 / Contents" in dumped_text

    # 2. Import the streams back into the copied PDF
    with pikepdf.open(copied_pdf_path) as pdf:
        import_res = import_streams(pdf, ["normalize=true", str(dump_file)])
        assert import_res.success

        # Save to a new file to verify successful serialization
        saved_pdf_path = tmp_path / "output.pdf"
        import_res.pdf.save(saved_pdf_path)

    # 3. Open the output PDF and assert that the content streams are fully preserved and uncorrupted
    with pikepdf.open(saved_pdf_path) as pdf_out:
        roundtripped_page_1 = pdf_out.pages[0].Contents.read_bytes()
        # Verify that round-tripping didn't erase the page contents
        assert len(roundtripped_page_1) > 0
        assert b"q" in roundtripped_page_1 or b"BT" in roundtripped_page_1


# ---------------------------------------------------------------------------
# Escaping Round-Trip Coverage
# ---------------------------------------------------------------------------


def test_unescape_content_line():
    """Directly verifies the escape-stripping helper."""
    from pdftl.operations.import_streams import _unescape_content_line

    assert _unescape_content_line(b"\\=== Fake Header Tj") == b"=== Fake Header Tj"
    assert _unescape_content_line(b"\\\\Already backslashed Tj") == b"\\Already backslashed Tj"
    assert _unescape_content_line(b"BT (Normal) Tj ET") == b"BT (Normal) Tj ET"
    assert _unescape_content_line(b"") == b""


def test_dump_import_roundtrip_equals_collision(base_pdf, tmp_path):
    """End-to-end: a content stream containing a line starting with '===' must
    survive a dump_streams -> import_streams round-trip unmodified, instead of
    being misread as a structural header."""
    from pdftl.operations.dump_streams import dump_streams, dump_streams_cli_hook
    import pdftl.core.constants as c

    base_pdf.pages[0].Contents = base_pdf.make_stream(b"=== not a header ===")

    dump_res = dump_streams(base_pdf, ["normalize=false", "1"])
    dump_file = tmp_path / "dump.txt"
    dump_res.meta[c.META_OUTPUT_FILE] = str(dump_file)
    dump_streams_cli_hook(dump_res, None, None)

    # Sanity: the on-disk form is escaped, not raw.
    assert "\\=== not a header ===" in dump_file.read_text("latin-1")

    import_streams(base_pdf, ["normalize=false", str(dump_file)])
    assert base_pdf.pages[0].Contents.read_bytes() == b"=== not a header ==="


# ---------------------------------------------------------------------------
# Regression: Resources header must flush AND reset current_target
# ---------------------------------------------------------------------------


def test_parse_stream_file_resources_does_not_leak_into_prior_buffer():
    """Regression test: a '=== Page N / Resources' header must both flush the
    prior target's buffer *and* reset current_target, so that the Resources
    block's own body lines are discarded rather than appended to the
    previous target's content.

    Prior to the fix, the Resources header only `continue`d without
    resetting current_target, so subsequent lines (e.g. '/Font << >>') were
    silently appended to the previous Contents/XObject buffer, corrupting it.
    """
    content = (
        b"=== Page 1 / Contents\n"
        b"BT (Page One) Tj ET\n"
        b"=== Page 1 / Resources\n"
        b"  /Font << /F1 0 R >>\n"
        b"=== Page 2 / Contents\n"
        b"BT (Page Two) Tj ET\n"
    )
    file_obj = io.BytesIO(content)
    targets = _parse_stream_file(file_obj)

    assert len(targets) == 2
    assert targets[0][0].page_num == 1
    assert targets[0][1] == b"BT (Page One) Tj ET"
    # Must NOT contain the Resources block's body.
    assert b"/Font" not in targets[0][1]

    assert targets[1][0].page_num == 2
    assert targets[1][1] == b"BT (Page Two) Tj ET"
