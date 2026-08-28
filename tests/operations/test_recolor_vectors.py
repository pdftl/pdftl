from unittest.mock import MagicMock, patch
import pikepdf
from pdftl.operations.recolor_vectors import (
    recolor_vectors_in_content_streams,
    _apply_recolor_vectors_spec_in_content_streams,
    GreyscaleReplaceContentStream,
)


def test_recolor_vectors_orchestrator_default():
    """Covers lines 49-53: Fallback to default '-' spec and standard looping."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(100, 100))

    # Passing an empty spec triggers the default '-' behavior, executing the module end-to-end
    res = recolor_vectors_in_content_streams(pdf, [])
    assert res.success is True
    assert res.pdf == pdf


def test_apply_recolor_vectors_empty_spec():
    """Covers lines 57-58: Early exit if spec evaluation turns up completely empty."""
    # Should safely return and do nothing
    _apply_recolor_vectors_spec_in_content_streams(None, "", set())


def test_apply_recolor_vectors_spec_skips_already_processed():
    """[Hits Line 67] Verify that already processed pages trigger the continue statement."""
    # 1. Setup a mock PDF with dummy pages to satisfy len(pdf.pages)
    mock_pdf = MagicMock()
    mock_pdf.pages = [MagicMock(), MagicMock()]

    # 2. Pre-seed the set with page '1'
    pages_processed = {1}

    with (
        patch("pdftl.operations.recolor_vectors.page_numbers_matching_page_spec") as mock_matcher,
        patch(
            "pdftl.operations.recolor_vectors.GreyscaleReplaceContentStream"
        ) as mock_replacer_cls,
    ):
        mock_matcher.return_value = [1]
        mock_replacer = MagicMock()
        mock_replacer_cls.return_value = mock_replacer
        result = _apply_recolor_vectors_spec_in_content_streams(mock_pdf, "1", pages_processed)
        mock_replacer.apply.assert_not_called()
        assert result == {1}


def test_greyscale_replace_content_stream_apply():
    """Covers lines 74-75, 79-90: apply() reading page content and correctly modifying it."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    # Write a pure red RGB color instruction into the page layout
    page.Contents = pdf.make_stream(b"1.0 0.0 0.0 rg")

    replacer = GreyscaleReplaceContentStream(pdf)
    replacer.apply(1)

    modified_content = page.Contents.read_bytes().decode()

    # Ensure the red color token 'rg' was wiped and replaced with grayscale 'g'
    assert "g" in modified_content
    assert "rg" not in modified_content


def test_process_instructions_edge_cases(caplog):
    """Covers lines 94-109: warning branches on bad operations."""
    replacer = GreyscaleReplaceContentStream(None)

    # Simulate a malformed 'rg' operator with only 2 arguments instead of 3
    instructions = [
        ([1, 0], pikepdf.Operator("rg")),
        ([], pikepdf.Operator("q")),  # standard irrelevant operator
    ]

    new_stream = replacer._process_instructions(instructions)

    # Verify the code bails out gracefully, leaving the bad instruction intact
    assert "Conversions: 0 success, 1 fail" in caplog.text
    assert b"1 0 rg" in new_stream
    assert b"q" in new_stream


def test_to_gray_cmyk_and_invalid_counts():
    """Covers lines 113-131: CMYK conversions, RGB handling, and length guards."""
    replacer = GreyscaleReplaceContentStream(None)

    # 1. Valid CMYK (Fill)
    val, op = replacer.to_gray([0.5, 0.5, 0.5, 0.1], "k")
    assert op == "g"
    assert len(val) == 1

    # 2. Invalid CMYK arg count (3 instead of 4)
    val, op = replacer.to_gray([1, 2, 3], "k")
    assert op is None

    # 3. Invalid RGB arg count (2 instead of 3)
    val, op = replacer.to_gray([1, 2], "rg")
    assert op is None

    # 4. Unrecognized operator fallback loop block
    val, op = replacer.to_gray([1, 2, 3], "UNKNOWN")
    assert op is None


def test_to_gray_exception_handling(caplog):
    """Covers lines 135-164: Exception trapping on bad token types."""
    replacer = GreyscaleReplaceContentStream(None)

    # Trigger a severe TypeError/ValueError via bad casting inside map() function
    val, op = replacer.to_gray(["BAD", "DATA", "STR"], "rg")

    assert op is None
    assert "Exception running raster math logic transformation" in caplog.text


def test_to_gray_uppercase_operators():
    """Covers uppercase 'RG' and 'K' stroke operators in NTSC luminance mapping."""
    replacer = GreyscaleReplaceContentStream(None)

    # Valid RGB Stroke (100% Red)
    val, op = replacer.to_gray([1.0, 0.0, 0.0], "RG")
    assert op == "G"
    assert len(val) == 1
    assert val[0] == 0.3  # 0.3 * 1.0

    # Valid CMYK Stroke (100% Magenta)
    val, op = replacer.to_gray([0.0, 1.0, 0.0, 0.0], "K")
    assert op == "G"
    assert len(val) == 1


def test_recolor_vectors_non_empty_specs():
    """Covers the path when non-empty page specifications are provided."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(100, 100))
    res = recolor_vectors_in_content_streams(pdf, ["1"])
    assert res.success is True


def test_greyscale_post_init_custom_processed_set():
    """Covers __post_init__ when an existing _processed_objgens set is explicitly provided."""
    custom_set = {(1, 0)}
    replacer = GreyscaleReplaceContentStream(pdf=None, _processed_objgens=custom_set)
    assert replacer._processed_objgens is custom_set


def test_greyscale_apply_with_page_stream_in_walk():
    """Covers skipping resource stream handling when the context kind is 'page' during walking."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    page.Contents = pdf.make_stream(b"1.0 0.0 0.0 rg")

    # Mock walk_content_streams_deduped to yield a stream with ctx.kind == "page"
    mock_ctx = MagicMock()
    mock_ctx.kind = "page"
    mock_stream = page.Contents

    with patch(
        "pdftl.operations.recolor_vectors.walk_content_streams_deduped",
        return_value=[(mock_stream, mock_ctx)],
    ):
        replacer = GreyscaleReplaceContentStream(pdf)
        replacer.apply(1)

    assert b"g" in page.Contents.read_bytes()


def test_process_resource_stream_success_and_exception(caplog):
    """Covers direct calls to _process_resource_stream for both success and exception paths."""
    pdf = pikepdf.new()
    replacer = GreyscaleReplaceContentStream(pdf)

    # 1. Happy path: valid resource stream (e.g., Form, SMask, or Annotation appearance stream)
    stream_obj = pdf.make_stream(b"0 0 1 rg")
    replacer._process_resource_stream(stream_obj)
    assert b"g" in stream_obj.read_bytes()

    # 2. Exception path: passing an invalid object causes parse_content_stream to fail
    broken_obj = pikepdf.Dictionary(Type=pikepdf.Name("/XObject"))
    replacer._process_resource_stream(broken_obj)
    assert "Failed to process resource stream" in caplog.text


def test_greyscale_apply_skips_page_kind_in_walk():
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    page.Contents = pdf.make_stream(b"1.0 0.0 0.0 rg")

    mock_ctx = MagicMock()
    mock_ctx.kind = "page"
    mock_stream = page.Contents

    with patch(
        "pdftl.operations.recolor_vectors.walk_content_streams_deduped",
        return_value=[(mock_stream, mock_ctx)],
    ):
        replacer = GreyscaleReplaceContentStream(pdf)
        replacer.apply(1)

    assert b"g" in page.Contents.read_bytes()


def test_greyscale_apply_page_stream_continue_integration():
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    # Ensure page.Contents is a stream object registered in the PDF
    page.Contents = pdf.make_stream(b"1.0 0.0 0.0 rg")

    replacer = GreyscaleReplaceContentStream(pdf)
    # Clear processed set prior to apply if it was pre-filled
    replacer._processed_objgens.clear()
    replacer.apply(1)

    assert b"g" in page.Contents.read_bytes()


def test_greyscale_apply_processes_nested_resource_stream():
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    page.Contents = pdf.make_stream(b"1 0 0 rg /X1 Do")

    # Create a Form XObject (resource stream with ctx.kind != "page")
    form_stream = pdf.make_stream(b"0 1 0 rg")
    form_stream.Type = pikepdf.Name("/XObject")
    form_stream.Subtype = pikepdf.Name("/Form")
    form_stream.BBox = [0, 0, 100, 100]

    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(X1=form_stream))

    replacer = GreyscaleReplaceContentStream(pdf)
    replacer.apply(1)

    # Verifies both main content stream and nested resource stream were processed
    assert b"g" in page.Contents.read_bytes()
    assert b"g" in form_stream.read_bytes()
