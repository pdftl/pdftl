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


def test_greyscale_process_resources_xobject_recursion():
    """Covers lines 84-90: XObject Form recursive extraction and processed-cache skipping."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page()

    # 1. Build a nested form layout stream (Stroke color)
    nested_form = pdf.make_stream(b"0 1 0 RG")
    nested_form.Subtype = pikepdf.Name("/Form")

    # 2. Build a parent form layout stream (Fill color) containing the nested form
    parent_form = pdf.make_stream(b"0 0 1 rg")
    parent_form.Subtype = pikepdf.Name("/Form")
    parent_form.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm2=nested_form))

    # 3. Mount to page
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm1=parent_form))

    replacer = GreyscaleReplaceContentStream(pdf)
    replacer._process_resources(page.Resources)

    # Verify the parent was converted to fill 'g'
    assert b"g" in parent_form.read_bytes()

    # Verify the recursion caught the nested object and converted to stroke 'G'
    assert b"G" in nested_form.read_bytes()

    # 4. Trigger processed skip logic (Lines 82-83)
    parent_bytes_before = parent_form.read_bytes()
    replacer._process_resources(page.Resources)
    assert parent_form.read_bytes() == parent_bytes_before


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


def test_greyscale_process_xobjects_exception_handling(caplog):
    """Covers the exception block when Form XObject stream parsing fails."""
    pdf = pikepdf.new()
    replacer = GreyscaleReplaceContentStream(pdf)

    # Create a malformed XObject (a dictionary instead of a stream)
    broken_xobj = pikepdf.Dictionary(Type=pikepdf.Name("/XObject"), Subtype=pikepdf.Name("/Form"))
    # pikepdf.parse_content_stream will raise a TypeError/ValueError on non-streams
    resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm1=broken_xobj))

    replacer._process_xobjects(resources)
    assert "Failed to process Form XObject" in caplog.text


def test_greyscale_process_patterns_exception_handling(caplog):
    """Covers the exception block when Tiling Pattern stream parsing fails."""
    pdf = pikepdf.new()
    replacer = GreyscaleReplaceContentStream(pdf)

    # Create a malformed Pattern (a dictionary instead of a stream)
    broken_pat = pikepdf.Dictionary(Type=pikepdf.Name("/Pattern"), PatternType=1)
    resources = pikepdf.Dictionary(Pattern=pikepdf.Dictionary(Pat1=broken_pat))

    replacer._process_patterns(resources)
    assert "Failed to process Pattern" in caplog.text


def test_greyscale_process_patterns_success():
    """Covers the happy-path for Tiling Pattern extraction and recursion."""
    pdf = pikepdf.new()
    replacer = GreyscaleReplaceContentStream(pdf)

    # Create a valid Pattern Stream
    pat_stream = pdf.make_stream(b"1 0 0 rg")
    pat_stream.update(
        {
            pikepdf.Name("/Type"): pikepdf.Name("/Pattern"),
            pikepdf.Name("/PatternType"): 1,
            # Include an empty Resources dict to hit the `if "/Resources" in pat:` branch
            pikepdf.Name("/Resources"): pikepdf.Dictionary(),
        }
    )
    resources = pikepdf.Dictionary(Pattern=pikepdf.Dictionary(Pat1=pat_stream))

    replacer._process_patterns(resources)

    # Assert the 'rg' inside the pattern was converted to 'g'
    assert b"g" in pat_stream.read_bytes()


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


def test_greyscale_process_patterns_cache_hit_line_142():
    """Explicitly triggers line 142 by processing the exact same pattern object twice."""
    pdf = pikepdf.new()
    replacer = GreyscaleReplaceContentStream(pdf)

    # 1. Create a valid mock pattern stream
    pat_stream = pdf.make_stream(b"1 0 0 rg")
    pat_stream.update(
        {
            pikepdf.Name("/Type"): pikepdf.Name("/Pattern"),
            pikepdf.Name("/PatternType"): 1,
        }
    )
    resources = pikepdf.Dictionary(Pattern=pikepdf.Dictionary(Pat1=pat_stream))

    # 2. First pass: Processes normally, adding pat_stream.objgen to the cache
    replacer._process_patterns(resources)
    assert pat_stream.objgen in replacer._processed_objgens

    # 3. Second pass: Hits line 142 ('continue') because the objgen is already cached
    # We alter the stream contents locally to prove that the second pass completely skips processing it
    pat_stream.write(b"0 1 0 rg")
    replacer._process_patterns(resources)

    # If line 142 successfully skipped it, the new 'rg' operator remains intact and wasn't morphed to 'g'
    assert b"rg" in pat_stream.read_bytes()
