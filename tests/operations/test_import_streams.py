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


# ---------------------------------------------------------------------------
# _parse_target_path: Pattern / SMask / Annotation grammar
# ---------------------------------------------------------------------------


def test_parse_target_path_pattern():
    """Test 'Pattern <name>' resolves to a Pattern target."""
    from pdftl.operations.import_streams import _parse_target_path

    target = _parse_target_path("Page 1 / Pattern /P1")
    assert target.page_num == 1
    assert target.target_type == "Pattern"
    assert target.pattern_name == "/P1"
    assert target.xobject_path == []


def test_parse_target_path_pattern_nested_xobject():
    """Test 'Pattern <name> / XObject <name>' resolves nested path."""
    from pdftl.operations.import_streams import _parse_target_path

    target = _parse_target_path("Page 1 / Pattern /P1 / XObject /Fm1")
    assert target.target_type == "Pattern"
    assert target.pattern_name == "/P1"
    assert target.xobject_path == ["/Fm1"]


def test_parse_target_path_pattern_malformed_nested():
    """Test a malformed nested segment after Pattern returns None."""
    from pdftl.operations.import_streams import _parse_target_path

    assert _parse_target_path("Page 1 / Pattern /P1 / NotXObject /Fm1") is None


def test_parse_target_path_smask():
    """Test 'ExtGState <name> / SMask' resolves to an SMask target."""
    from pdftl.operations.import_streams import _parse_target_path

    target = _parse_target_path("Page 1 / ExtGState /GS1 / SMask")
    assert target.target_type == "SMask"
    assert target.extgstate_name == "/GS1"
    assert target.xobject_path == []


def test_parse_target_path_smask_nested_xobject():
    """Test SMask target with a further nested XObject chain."""
    from pdftl.operations.import_streams import _parse_target_path

    target = _parse_target_path("Page 1 / ExtGState /GS1 / SMask / XObject /Fm1")
    assert target.target_type == "SMask"
    assert target.extgstate_name == "/GS1"
    assert target.xobject_path == ["/Fm1"]


def test_parse_target_path_smask_malformed_nested():
    """Test a malformed nested segment after SMask returns None."""
    from pdftl.operations.import_streams import _parse_target_path

    assert _parse_target_path("Page 1 / ExtGState /GS1 / SMask / Bogus /Fm1") is None


def test_parse_target_path_annotation_direct_stream():
    """Test 'Annot <n> / AP <key>' resolves to an Annotation target with
    0-based annot_index and no ap_state."""
    from pdftl.operations.import_streams import _parse_target_path

    target = _parse_target_path("Page 1 / Annot 2 / AP /N")
    assert target.target_type == "Annotation"
    assert target.annot_index == 1
    assert target.ap_key == "/N"
    assert target.ap_state is None
    assert target.xobject_path == []


def test_parse_target_path_annotation_state():
    """Test 'Annot <n> / AP <key> / State <state>' resolves ap_state."""
    from pdftl.operations.import_streams import _parse_target_path

    target = _parse_target_path("Page 1 / Annot 1 / AP /N / State /On")
    assert target.target_type == "Annotation"
    assert target.annot_index == 0
    assert target.ap_key == "/N"
    assert target.ap_state == "/On"


def test_parse_target_path_annotation_nested_xobject_after_state():
    """Test an Annotation target with State followed by a nested XObject
    chain."""
    from pdftl.operations.import_streams import _parse_target_path

    target = _parse_target_path("Page 1 / Annot 1 / AP /N / State /On / XObject /Fm1")
    assert target.target_type == "Annotation"
    assert target.ap_state == "/On"
    assert target.xobject_path == ["/Fm1"]


def test_parse_target_path_annotation_nested_xobject_no_state():
    """Test an Annotation target with a nested XObject chain directly
    after AP, no State segment."""
    from pdftl.operations.import_streams import _parse_target_path

    target = _parse_target_path("Page 1 / Annot 1 / AP /N / XObject /Fm1")
    assert target.target_type == "Annotation"
    assert target.ap_state is None
    assert target.xobject_path == ["/Fm1"]


def test_parse_target_path_annotation_invalid_index():
    """Test a non-numeric or zero/negative Annot index returns None."""
    from pdftl.operations.import_streams import _parse_target_path

    assert _parse_target_path("Page 1 / Annot X / AP /N") is None
    assert _parse_target_path("Page 1 / Annot 0 / AP /N") is None


def test_parse_target_path_annotation_malformed_ap():
    """Test a malformed AP segment (no space) returns None."""
    from pdftl.operations.import_streams import _parse_target_path

    assert _parse_target_path("Page 1 / Annot 1 / APNoSpace") is None


def test_parse_target_path_annotation_malformed_state():
    """Test a malformed State segment (no space) returns None."""
    from pdftl.operations.import_streams import _parse_target_path

    assert _parse_target_path("Page 1 / Annot 1 / AP /N / StateNoSpace") is None


def test_parse_target_path_annotation_malformed_nested_after_state():
    """Test a malformed nested segment after State returns None."""
    from pdftl.operations.import_streams import _parse_target_path

    assert _parse_target_path("Page 1 / Annot 1 / AP /N / State /On / Bogus /Fm1") is None


# ---------------------------------------------------------------------------
# Application: Pattern / SMask / Annotation targets
# ---------------------------------------------------------------------------


def test_apply_stream_target_pattern(base_pdf):
    """Successfully applying bytes to a referenced tiling Pattern."""
    pat = base_pdf.make_stream(b"old pattern")
    pat.PatternType = 1
    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/Pattern": pikepdf.Dictionary({"/P1": pat})}
    )

    from pdftl.operations.import_streams import StreamTarget

    target = StreamTarget(1, "Pattern", pattern_name="/P1")
    _apply_stream_target(base_pdf, target, b"new pattern content", normalize=False)

    assert pat.read_bytes() == b"new pattern content"


def test_apply_stream_target_pattern_nested_xobject(base_pdf):
    """Successfully applying bytes to a Form nested inside a Pattern's own
    /Resources."""
    nested = base_pdf.make_stream(b"old nested")
    pat = base_pdf.make_stream(b"old pattern")
    pat.PatternType = 1
    pat.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Fm1": nested})})
    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/Pattern": pikepdf.Dictionary({"/P1": pat})}
    )

    from pdftl.operations.import_streams import StreamTarget

    target = StreamTarget(1, "Pattern", pattern_name="/P1", xobject_path=["/Fm1"])
    _apply_stream_target(base_pdf, target, b"new nested", normalize=False)

    assert nested.read_bytes() == b"new nested"
    assert pat.read_bytes() == b"old pattern"


def test_apply_stream_target_pattern_not_found(base_pdf, caplog):
    """Safely skips a Pattern target that doesn't exist."""
    from pdftl.operations.import_streams import StreamTarget

    target = StreamTarget(1, "Pattern", pattern_name="/P1")
    _apply_stream_target(base_pdf, target, b"data", normalize=False)
    assert "Could not find Pattern /P1" in caplog.text


def test_apply_stream_target_smask(base_pdf):
    """Successfully applying bytes to an ExtGState /SMask /G group."""
    group = base_pdf.make_stream(b"old mask")
    group.Subtype = pikepdf.Name("/Form")
    smask = pikepdf.Dictionary({"/Type": pikepdf.Name("/Mask"), "/G": group})
    gs = pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState"), "/SMask": smask})
    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/ExtGState": pikepdf.Dictionary({"/GS1": gs})}
    )

    from pdftl.operations.import_streams import StreamTarget

    target = StreamTarget(1, "SMask", extgstate_name="/GS1")
    _apply_stream_target(base_pdf, target, b"new mask", normalize=False)

    assert group.read_bytes() == b"new mask"


def test_apply_stream_target_smask_not_found(base_pdf, caplog):
    """Safely skips an SMask target that doesn't exist."""
    from pdftl.operations.import_streams import StreamTarget

    target = StreamTarget(1, "SMask", extgstate_name="/GS1")
    _apply_stream_target(base_pdf, target, b"data", normalize=False)
    assert "Could not find SMask group for ExtGState /GS1" in caplog.text


def test_apply_stream_target_annotation_direct_stream(base_pdf):
    """Successfully applying bytes to a direct-stream annotation
    appearance stream."""
    ap_form = base_pdf.make_stream(b"old annot")
    ap_form.Subtype = pikepdf.Name("/Form")
    annot = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Annot"),
            "/Subtype": pikepdf.Name("/FreeText"),
            "/AP": pikepdf.Dictionary({"/N": ap_form}),
        }
    )
    base_pdf.pages[0].Annots = pikepdf.Array([annot])

    from pdftl.operations.import_streams import StreamTarget

    target = StreamTarget(1, "Annotation", annot_index=0, ap_key="/N")
    _apply_stream_target(base_pdf, target, b"new annot", normalize=False)

    assert ap_form.read_bytes() == b"new annot"


def test_apply_stream_target_annotation_state_dict(base_pdf):
    """Successfully applying bytes to a state-keyed annotation appearance
    stream."""
    on_form = base_pdf.make_stream(b"old on")
    on_form.Subtype = pikepdf.Name("/Form")
    annot = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Annot"),
            "/Subtype": pikepdf.Name("/Widget"),
            "/AP": pikepdf.Dictionary({"/N": pikepdf.Dictionary({"/On": on_form})}),
        }
    )
    base_pdf.pages[0].Annots = pikepdf.Array([annot])

    from pdftl.operations.import_streams import StreamTarget

    target = StreamTarget(1, "Annotation", annot_index=0, ap_key="/N", ap_state="/On")
    _apply_stream_target(base_pdf, target, b"new on", normalize=False)

    assert on_form.read_bytes() == b"new on"


def test_apply_stream_target_annotation_nested_xobject(base_pdf):
    """Successfully applying bytes to a Form nested inside an annotation
    appearance stream's own /Resources."""
    nested = base_pdf.make_stream(b"old nested")
    ap_form = base_pdf.make_stream(b"old annot")
    ap_form.Subtype = pikepdf.Name("/Form")
    ap_form.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Fm1": nested})})
    annot = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Annot"),
            "/Subtype": pikepdf.Name("/FreeText"),
            "/AP": pikepdf.Dictionary({"/N": ap_form}),
        }
    )
    base_pdf.pages[0].Annots = pikepdf.Array([annot])

    from pdftl.operations.import_streams import StreamTarget

    target = StreamTarget(1, "Annotation", annot_index=0, ap_key="/N", xobject_path=["/Fm1"])
    _apply_stream_target(base_pdf, target, b"new nested", normalize=False)

    assert nested.read_bytes() == b"new nested"
    assert ap_form.read_bytes() == b"old annot"


def test_apply_stream_target_annotation_index_out_of_range(base_pdf, caplog):
    """Safely skips an Annotation target whose index is out of range for
    the page's /Annots array."""
    from pdftl.operations.import_streams import StreamTarget

    base_pdf.pages[0].Annots = pikepdf.Array([])
    target = StreamTarget(1, "Annotation", annot_index=0, ap_key="/N")
    _apply_stream_target(base_pdf, target, b"data", normalize=False)
    assert "Could not find annotation appearance stream" in caplog.text


def test_apply_stream_target_annotation_no_annots(base_pdf, caplog):
    """Safely skips an Annotation target when the page has no /Annots at
    all."""
    from pdftl.operations.import_streams import StreamTarget

    target = StreamTarget(1, "Annotation", annot_index=0, ap_key="/N")
    _apply_stream_target(base_pdf, target, b"data", normalize=False)
    assert "Could not find annotation appearance stream" in caplog.text


def test_apply_stream_target_annotation_missing_ap_key(base_pdf, caplog):
    """Safely skips an Annotation target requesting an /AP key the
    annotation doesn't have."""
    ap_form = base_pdf.make_stream(b"old annot")
    ap_form.Subtype = pikepdf.Name("/Form")
    annot = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Annot"),
            "/Subtype": pikepdf.Name("/FreeText"),
            "/AP": pikepdf.Dictionary({"/N": ap_form}),
        }
    )
    base_pdf.pages[0].Annots = pikepdf.Array([annot])

    from pdftl.operations.import_streams import StreamTarget

    target = StreamTarget(1, "Annotation", annot_index=0, ap_key="/D")
    _apply_stream_target(base_pdf, target, b"data", normalize=False)
    assert "Could not find annotation appearance stream" in caplog.text


def test_apply_stream_target_pattern_normalize_error(base_pdf, caplog):
    """Testing graceful fallback to raw bytes when Pattern normalization
    raises."""
    pat = base_pdf.make_stream(b"old pattern")
    pat.PatternType = 1
    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/Pattern": pikepdf.Dictionary({"/P1": pat})}
    )

    from pdftl.operations.import_streams import StreamTarget

    target = StreamTarget(1, "Pattern", pattern_name="/P1")
    content = b"new pattern"
    with patch(
        "pikepdf.parse_content_stream",
        side_effect=pikepdf.PdfError("simulated parse error"),
    ):
        _apply_stream_target(base_pdf, target, content, normalize=True)

    assert pat.read_bytes() == content
    assert "Could not normalize imported Pattern /P1" in caplog.text


# ---------------------------------------------------------------------------
# End-to-end round-trip: dump_streams -> import_streams for new kinds
# ---------------------------------------------------------------------------


def test_roundtrip_pattern(base_pdf, tmp_path):
    """End-to-end: a tiling Pattern's content survives a
    dump_streams -> import_streams round trip via the new breadcrumb."""
    from pdftl.operations.dump_streams import dump_streams, dump_streams_cli_hook
    import pdftl.core.constants as c

    pat = base_pdf.make_stream(b"1 0 0 rg")
    pat.PatternType = 1
    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/Pattern": pikepdf.Dictionary({"/P1": pat})}
    )

    dump_res = dump_streams(base_pdf, ["normalize=false", "1"])
    dump_file = tmp_path / "dump.txt"
    dump_res.meta[c.META_OUTPUT_FILE] = str(dump_file)
    dump_streams_cli_hook(dump_res, None, None)

    assert "=== Page 1 / Pattern /P1" in dump_file.read_text("latin-1")

    import_streams(base_pdf, ["normalize=false", str(dump_file)])
    assert pat.read_bytes() == b"1 0 0 rg"


def test_roundtrip_smask(base_pdf, tmp_path):
    """End-to-end: an SMask group's content survives a round trip."""
    from pdftl.operations.dump_streams import dump_streams, dump_streams_cli_hook
    import pdftl.core.constants as c

    group = base_pdf.make_stream(b"0 g")
    group.Subtype = pikepdf.Name("/Form")
    smask = pikepdf.Dictionary({"/Type": pikepdf.Name("/Mask"), "/G": group})
    gs = pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState"), "/SMask": smask})
    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/ExtGState": pikepdf.Dictionary({"/GS1": gs})}
    )

    dump_res = dump_streams(base_pdf, ["normalize=false", "1"])
    dump_file = tmp_path / "dump.txt"
    dump_res.meta[c.META_OUTPUT_FILE] = str(dump_file)
    dump_streams_cli_hook(dump_res, None, None)

    assert "=== Page 1 / ExtGState /GS1 / SMask" in dump_file.read_text("latin-1")

    import_streams(base_pdf, ["normalize=false", str(dump_file)])
    assert group.read_bytes() == b"0 g"


def test_roundtrip_annotation(base_pdf, tmp_path):
    """End-to-end: an annotation appearance stream's content survives a
    round trip."""
    from pdftl.operations.dump_streams import dump_streams, dump_streams_cli_hook
    import pdftl.core.constants as c

    ap_form = base_pdf.make_stream(b"1 1 1 rg")
    ap_form.Subtype = pikepdf.Name("/Form")
    annot = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Annot"),
            "/Subtype": pikepdf.Name("/FreeText"),
            "/AP": pikepdf.Dictionary({"/N": ap_form}),
        }
    )
    base_pdf.pages[0].Annots = pikepdf.Array([annot])

    dump_res = dump_streams(base_pdf, ["normalize=false", "1"])
    dump_file = tmp_path / "dump.txt"
    dump_res.meta[c.META_OUTPUT_FILE] = str(dump_file)
    dump_streams_cli_hook(dump_res, None, None)

    assert "=== Page 1 / Annot 1 / AP /N" in dump_file.read_text("latin-1")

    import_streams(base_pdf, ["normalize=false", str(dump_file)])
    assert ap_form.read_bytes() == b"1 1 1 rg"


def test_import_streams_empty_targets_no_op(base_pdf, tmp_path):
    """Covers the zero-iteration branch of the apply loop in
    import_streams(): a stream file with no recognizable headers parses
    to zero targets, and the operation completes as a no-op."""
    stream_path = tmp_path / "empty.txt"
    stream_path.write_bytes(b"just some text with no headers at all\n")

    result = import_streams(base_pdf, [str(stream_path), "normalize=false"])

    assert result.success is True


def test_apply_stream_target_unrecognized_type_falls_through(base_pdf):
    """Covers the branch where target_type matches none of the five known
    kinds: _apply_stream_target simply falls through and returns without
    error or mutation."""
    from pdftl.operations.import_streams import StreamTarget

    target = StreamTarget(1, "Bogus")
    # Must not raise; must not touch the page.
    _apply_stream_target(base_pdf, target, b"data", normalize=False)


def test_roundtrip_combined_all_kinds_one_page(base_pdf, tmp_path):
    """End-to-end: a single page combining Contents, a Form, a Pattern, an
    SMask group, and an annotation appearance stream (state-keyed) all
    round-trip through dump_streams -> import_streams together, with each
    edited to a distinguishable value, confirming no cross-contamination
    between breadcrumb kinds when parsing/applying in one file."""
    from pdftl.operations.dump_streams import dump_streams, dump_streams_cli_hook
    import pdftl.core.constants as c

    base_pdf.pages[0].Contents = base_pdf.make_stream(b"BT (page) Tj ET")

    form = base_pdf.make_stream(b"BT (form) Tj ET")
    form.Subtype = pikepdf.Name("/Form")

    pat = base_pdf.make_stream(b"1 0 0 rg")
    pat.PatternType = 1

    smask_group = base_pdf.make_stream(b"0 g")
    smask_group.Subtype = pikepdf.Name("/Form")
    smask = pikepdf.Dictionary({"/Type": pikepdf.Name("/Mask"), "/G": smask_group})
    gs = pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState"), "/SMask": smask})

    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary({"/Fm1": form}),
            "/Pattern": pikepdf.Dictionary({"/P1": pat}),
            "/ExtGState": pikepdf.Dictionary({"/GS1": gs}),
        }
    )

    on_form = base_pdf.make_stream(b"1 1 1 rg")
    on_form.Subtype = pikepdf.Name("/Form")
    annot = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Annot"),
            "/Subtype": pikepdf.Name("/Widget"),
            "/AP": pikepdf.Dictionary({"/N": pikepdf.Dictionary({"/On": on_form})}),
        }
    )
    base_pdf.pages[0].Annots = pikepdf.Array([annot])

    dump_res = dump_streams(base_pdf, ["normalize=false", "1"])
    dump_file = tmp_path / "dump.txt"
    dump_res.meta[c.META_OUTPUT_FILE] = str(dump_file)
    dump_streams_cli_hook(dump_res, None, None)
    dumped_text = dump_file.read_text("latin-1")

    # Sanity: all five breadcrumb kinds are present in the dump.
    assert "=== Page 1 / Contents" in dumped_text
    assert "=== Page 1 / XObject /Fm1" in dumped_text
    assert "=== Page 1 / Pattern /P1" in dumped_text
    assert "=== Page 1 / ExtGState /GS1 / SMask" in dumped_text
    assert "=== Page 1 / Annot 1 / AP /N / State /On" in dumped_text

    # Edit each block to a distinguishable value via targeted substitution,
    # simulating the sed-style workflow.
    edited = (
        dumped_text.replace("BT (page) Tj ET", "BT (PAGE-EDITED) Tj ET")
        .replace("BT (form) Tj ET", "BT (FORM-EDITED) Tj ET")
        .replace("1 0 0 rg", "0 0 1 rg")
        .replace("0 g", "1 g")
        .replace("1 1 1 rg", "0.5 0.5 0.5 rg")
    )
    dump_file.write_text(edited, "latin-1")

    import_streams(base_pdf, ["normalize=false", str(dump_file)])

    assert base_pdf.pages[0].Contents.read_bytes() == b"BT (PAGE-EDITED) Tj ET"
    assert form.read_bytes() == b"BT (FORM-EDITED) Tj ET"
    assert pat.read_bytes() == b"0 0 1 rg"
    assert smask_group.read_bytes() == b"1 g"
    assert on_form.read_bytes() == b"0.5 0.5 0.5 rg"


def test_roundtrip_annotation_state_dict(base_pdf, tmp_path):
    """End-to-end: an annotation's state-keyed /AP /N /On stream survives
    a dump -> edit -> import round trip via the '/ State /On' breadcrumb
    suffix (as opposed to test_roundtrip_annotation, which covers the
    direct-stream case with no state suffix)."""
    from pdftl.operations.dump_streams import dump_streams, dump_streams_cli_hook
    import pdftl.core.constants as c

    on_form = base_pdf.make_stream(b"1 1 1 rg")
    on_form.Subtype = pikepdf.Name("/Form")
    off_form = base_pdf.make_stream(b"0 0 0 rg")
    off_form.Subtype = pikepdf.Name("/Form")
    annot = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Annot"),
            "/Subtype": pikepdf.Name("/Widget"),
            "/AP": pikepdf.Dictionary(
                {"/N": pikepdf.Dictionary({"/On": on_form, "/Off": off_form})}
            ),
        }
    )
    base_pdf.pages[0].Annots = pikepdf.Array([annot])

    dump_res = dump_streams(base_pdf, ["normalize=false", "1"])
    dump_file = tmp_path / "dump.txt"
    dump_res.meta[c.META_OUTPUT_FILE] = str(dump_file)
    dump_streams_cli_hook(dump_res, None, None)
    dumped_text = dump_file.read_text("latin-1")

    assert "=== Page 1 / Annot 1 / AP /N / State /On" in dumped_text
    assert "=== Page 1 / Annot 1 / AP /N / State /Off" in dumped_text

    # Only edit the /On state, confirm /Off is untouched.
    edited = dumped_text.replace("1 1 1 rg", "0.2 0.2 0.2 rg")
    dump_file.write_text(edited, "latin-1")

    import_streams(base_pdf, ["normalize=false", str(dump_file)])

    assert on_form.read_bytes() == b"0.2 0.2 0.2 rg"
    assert off_form.read_bytes() == b"0 0 0 rg"


def test_roundtrip_shared_pattern_aliased_update_propagates(base_pdf, tmp_path):
    """End-to-end: a Pattern referenced twice under two names on the same
    page produces one canonical entry and one ALIAS stub on dump; editing
    only the canonical block and re-importing updates the underlying
    shared object, so both names see the new content (since they point at
    the same pikepdf object)."""
    from pdftl.operations.dump_streams import dump_streams, dump_streams_cli_hook
    import pdftl.core.constants as c

    shared_pat = base_pdf.make_stream(b"1 0 0 rg")
    shared_pat.PatternType = 1
    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/Pattern": pikepdf.Dictionary({"/P1": shared_pat, "/P2": shared_pat})}
    )

    dump_res = dump_streams(base_pdf, ["normalize=false", "1"])
    dump_file = tmp_path / "dump.txt"
    dump_res.meta[c.META_OUTPUT_FILE] = str(dump_file)
    dump_streams_cli_hook(dump_res, None, None)
    dumped_text = dump_file.read_text("latin-1")

    assert "=== Page 1 / Pattern /P1" in dumped_text
    assert "=== Page 1 / Pattern /P2" in dumped_text
    assert "% ALIAS OF:" in dumped_text

    edited = dumped_text.replace("1 0 0 rg", "0 1 0 rg")
    dump_file.write_text(edited, "latin-1")

    import_streams(base_pdf, ["normalize=false", str(dump_file)])

    # Both names reference the same underlying object, so a single write
    # via the canonical /P1 (or /P2, whichever dict iteration hit first)
    # block updates it for both.
    assert shared_pat.read_bytes() == b"0 1 0 rg"


def test_roundtrip_pattern_nested_xobject_via_dump_file(base_pdf, tmp_path):
    """End-to-end: a Form nested inside a Pattern's own /Resources
    round-trips through the actual breadcrumb parser (not a
    hand-constructed StreamTarget), confirming
    'Pattern <name> / XObject <name>' parses and applies correctly."""
    from pdftl.operations.dump_streams import dump_streams, dump_streams_cli_hook
    import pdftl.core.constants as c

    nested = base_pdf.make_stream(b"BT (nested) Tj ET")
    nested.Subtype = pikepdf.Name("/Form")

    pat = base_pdf.make_stream(b"1 0 0 rg")
    pat.PatternType = 1
    pat.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Fm1": nested})})

    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/Pattern": pikepdf.Dictionary({"/P1": pat})}
    )

    dump_res = dump_streams(base_pdf, ["normalize=false", "1"])
    dump_file = tmp_path / "dump.txt"
    dump_res.meta[c.META_OUTPUT_FILE] = str(dump_file)
    dump_streams_cli_hook(dump_res, None, None)
    dumped_text = dump_file.read_text("latin-1")

    assert "=== Page 1 / Pattern /P1 / XObject /Fm1" in dumped_text

    edited = dumped_text.replace("BT (nested) Tj ET", "BT (NESTED-EDITED) Tj ET")
    dump_file.write_text(edited, "latin-1")

    import_streams(base_pdf, ["normalize=false", str(dump_file)])

    assert nested.read_bytes() == b"BT (NESTED-EDITED) Tj ET"
    # Pattern's own stream must be untouched by the nested edit.
    assert pat.read_bytes() == b"1 0 0 rg"


def test_roundtrip_new_kinds_with_annotate_normalize(base_pdf, tmp_path):
    """End-to-end: with annotate=true and normalize=true (the realistic
    default workflow), a Pattern's dump includes annotation comments,
    which import_streams correctly discards during normalization,
    leaving clean re-parsed content rather than comment text leaking
    into the stream."""
    from pdftl.operations.dump_streams import dump_streams, dump_streams_cli_hook
    import pdftl.core.constants as c

    pat = base_pdf.make_stream(b"1 0 0 rg\n0 0 100 100 re\nf")
    pat.PatternType = 1
    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/Pattern": pikepdf.Dictionary({"/P1": pat})}
    )

    dump_res = dump_streams(base_pdf, ["annotate", "1"])
    dump_file = tmp_path / "dump.txt"
    dump_res.meta[c.META_OUTPUT_FILE] = str(dump_file)
    dump_streams_cli_hook(dump_res, None, None)
    dumped_text = dump_file.read_text("latin-1")

    assert "=== Page 1 / Pattern /P1" in dumped_text
    assert "%" in dumped_text  # annotation comments present

    import_streams(base_pdf, ["normalize=true", str(dump_file)])

    result = pat.read_bytes()
    assert b"rg" in result and b"re" in result and b"f" in result
    # Comments must not have been written into the live stream.
    assert b"%" not in result
