from unittest.mock import MagicMock, patch
import pikepdf
import pytest

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.replace import RegexReplaceContentStream, replace_in_content_streams


@pytest.fixture
def pdf_with_content():
    """Creates a PDF where page 1 has specific content stream text."""
    pdf = pikepdf.new()
    pdf.add_blank_page()

    # Set content stream to something predictable: "BT ... (Hello World) Tj ... ET"
    stream_data = b"BT /F1 12 Tf 100 100 Td (Hello World) Tj ET"
    pdf.pages[0].Contents = pdf.make_stream(stream_data)

    return pdf


def test_replace_basic(pdf_with_content):
    """Test simple string replacement on a specific page."""
    specs = ["1/Hello/Hola/"]
    replace_in_content_streams(pdf_with_content, specs)
    content = pdf_with_content.pages[0].Contents.read_bytes()
    assert b"(Hola World) Tj" in content


def test_replace_global_implicit(pdf_with_content):
    """Test replacement without page range (implies all pages)."""
    specs = ["/World/Earth/"]
    replace_in_content_streams(pdf_with_content, specs)
    content = pdf_with_content.pages[0].Contents.read_bytes()
    assert b"(Hello Earth) Tj" in content


def test_replace_with_count(pdf_with_content):
    """Test the count limit (trailing integer in spec)."""
    stream_data = b"(Apple) Tj (Apple) Tj"
    pdf_with_content.pages[0].Contents = pdf_with_content.make_stream(stream_data)

    specs = ["/Apple/Banana/1"]
    replace_in_content_streams(pdf_with_content, specs)

    content = pdf_with_content.pages[0].Contents.read_bytes()
    assert content.count(b"Banana") == 1
    assert content.count(b"Apple") == 1


def test_replace_alternate_delimiter(pdf_with_content):
    """Test using a non-standard delimiter (determined by last char)."""
    specs = ["1#Hello#Hola#"]
    replace_in_content_streams(pdf_with_content, specs)
    content = pdf_with_content.pages[0].Contents.read_bytes()
    assert b"(Hola World)" in content


def test_replace_regex_behavior(pdf_with_content):
    """Test that regex patterns work."""
    specs = ["/H...o/Hi/"]
    replace_in_content_streams(pdf_with_content, specs)
    content = pdf_with_content.pages[0].Contents.read_bytes()
    assert b"(Hi World)" in content


def test_replace_invalid_spec(pdf_with_content):
    """Test that malformed specs raise InvalidArgumentError."""
    specs = ["/foo/bar"]
    with pytest.raises(InvalidArgumentError, match="expected 4 parts"):
        replace_in_content_streams(pdf_with_content, specs)


def test_replace_no_normalization(pdf_with_content):
    """Test disabling normalization."""
    specs = ["/Hello/Hola/"]
    replace_in_content_streams(
        pdf_with_content, specs, normalize_input=False, normalize_output=False
    )
    content = pdf_with_content.pages[0].Contents.read_bytes()
    assert b"(Hola World)" in content


def test_replace_empty_spec_element():
    """Covers empty specification early exit path."""
    mock_pdf = MagicMock()
    replace_in_content_streams(mock_pdf, [""])
    mock_pdf.pages.assert_not_called()


def test_replacer_fallback_no_regex():
    """Covers fallback path when self.from_re is empty."""
    mock_pdf = MagicMock()
    mock_page = MagicMock()
    mock_page.Contents.read_bytes.return_value = b"Original Content"
    mock_pdf.pages = [mock_page]

    replacer = RegexReplaceContentStream(
        pdf=mock_pdf,
        from_re=b"",
        to_re=b"anything",
        count=0,
        normalize_input=False,
        normalize_output=False,
    )

    replacer.apply(1)
    mock_pdf.make_stream.assert_called_with(b"Original Content")


def test_replace_parse_error_coverage():
    """Covers parsing specification syntax validation mismatch errors."""
    mock_pdf = MagicMock()
    with patch("re.match", return_value=None):
        with pytest.raises(InvalidArgumentError, match="Could not parse count suffix"):
            from pdftl.operations.replace import _parse_replace_spec

            _parse_replace_spec(mock_pdf, "any_spec", True, True)


# ---------------------------------------------------------------------------
# Targeted Coverage Tests for Uncovered Branches
# ---------------------------------------------------------------------------


def test_coverage_line_171_no_contents_graceful_exit():
    """
    Covers Line 171: Early exit in _extract_bytes returning None
    and Line 213: Early exit in _apply_to_stream.
    """
    pdf = pikepdf.new()
    pdf.add_blank_page()
    page = pdf.pages[0]

    # Explicitly remove the /Contents key from the page dictionary
    if "/Contents" in page:
        del page["/Contents"]

    specs = ["/Hello/World/"]
    # Should complete without throwing KeyErrors/AttributeErrors
    replace_in_content_streams(pdf, specs, normalize_input=False)
    assert "/Contents" not in page


def test_coverage_line_178_contents_is_pikepdf_array():
    """Covers Line 178: Extracting bytes from a pikepdf.Array with normalize_input=False."""
    pdf = pikepdf.new()
    pdf.add_blank_page()
    page = pdf.pages[0]

    # Assign an Array of multiple content streams
    stream1 = pdf.make_stream(b"BT (Part1) Tj ET ")
    stream2 = pdf.make_stream(b"BT (Part2) Tj ET")
    page.Contents = pikepdf.Array([stream1, stream2])

    specs = ["/Part1/Clean/"]
    replace_in_content_streams(pdf, specs, normalize_input=False, normalize_output=False)

    # Since it is normalized back into a single stream on write:
    combined_content = page.Contents.read_bytes()
    assert b"Clean" in combined_content
    assert b"Part2" in combined_content


def test_coverage_line_184_form_xobject_read_bytes_normalize_input_false():
    """Covers Line 184: Extracting raw Form XObject stream bytes with normalize_input=False."""
    pdf = pikepdf.new()
    pdf.add_blank_page()

    # Form XObject stream
    xobj = pdf.make_stream(b"BT(HelloXObject)Tj ET")
    xobj.Type = pikepdf.Name("/XObject")
    xobj.Subtype = pikepdf.Name("/Form")
    xobj.BBox = [0, 0, 100, 100]

    pdf.pages[0].Resources = {"/XObject": {"/Fm1": xobj}}
    pdf.pages[0].Contents = pdf.make_stream(b"/Fm1 Do")

    # Match raw unspaced syntax because normalize_input is disabled
    specs = ["/HelloXObject/CleanXObject/"]
    replace_in_content_streams(pdf, specs, normalize_input=False, normalize_output=False)

    assert b"CleanXObject" in xobj.read_bytes()


def test_coverage_line_202_203_normalize_output_exception_fallback():
    """Covers Lines 202-203: Catching normalization parsing errors in XObject writeback."""
    pdf = pikepdf.new()
    pdf.add_blank_page()

    # Form XObject stream
    xobj = pdf.make_stream(b"BT (Target) Tj ET")
    xobj.Type = pikepdf.Name("/XObject")
    xobj.Subtype = pikepdf.Name("/Form")
    xobj.BBox = [0, 0, 100, 100]

    pdf.pages[0].Resources = {"/XObject": {"/Fm1": xobj}}
    pdf.pages[0].Contents = pdf.make_stream(b"/Fm1 Do")

    # Target standard replacement format
    specs = ["/Target/Clean/"]

    orig_parse = pikepdf.parse_content_stream

    def side_effect(obj):
        # Only simulate parsing failure during the writeback phase on the XObject stream.
        # This is reached after container.write(new_content_stream), so we check if
        # "Clean" has been written to the stream yet.
        if isinstance(obj, pikepdf.Stream) and b"Clean" in obj.read_bytes():
            raise pikepdf.PdfError("Simulated parse error")
        return orig_parse(obj)

    # Force pikepdf's parser to raise an exception ONLY on writeback normalization for the stream
    # (Form XObject)
    with patch("pikepdf.parse_content_stream", side_effect=side_effect):
        replace_in_content_streams(pdf, specs, normalize_input=True, normalize_output=True)

    assert b"Clean" in xobj.read_bytes()


def test_coverage_line_230_processed_objgens_skipped():
    """Covers Line 230: Skipping an XObject already registered in _processed_objgens."""
    pdf = pikepdf.new()
    pdf.add_blank_page()

    xobj = pdf.make_stream(b"BT (Target) Tj ET")
    xobj.Type = pikepdf.Name("/XObject")
    xobj.Subtype = pikepdf.Name("/Form")
    xobj.BBox = [0, 0, 100, 100]

    # Reference the same XObject stream twice under different resource keys on the page
    pdf.pages[0].Resources = {"/XObject": {"/Fm1": xobj, "/Fm2": xobj}}
    pdf.pages[0].Contents = pdf.make_stream(b"/Fm1 Do /Fm2 Do")

    specs = ["/Target/Clean/"]

    # Run replacement; Fm2 should be skipped automatically on the second lookup iteration
    replace_in_content_streams(pdf, specs)
    assert b"Clean" in xobj.read_bytes()


def test_coverage_line_232_skip_non_form_xobjects():
    """Covers Line 232: Skipping XObjects whose /Subtype is not /Form (e.g. /Image)."""
    pdf = pikepdf.new()
    pdf.add_blank_page()

    # Create an Image XObject instead of a Form XObject
    img_xobj = pdf.make_stream(b"image_raw_data_goes_here")
    img_xobj.Type = pikepdf.Name("/XObject")
    img_xobj.Subtype = pikepdf.Name("/Image")
    img_xobj.Width = 1
    img_xobj.Height = 1
    img_xobj.ColorSpace = pikepdf.Name("/DeviceRGB")
    img_xobj.BitsPerComponent = 8

    pdf.pages[0].Resources = {"/XObject": {"/Im1": img_xobj}}
    pdf.pages[0].Contents = pdf.make_stream(b"/Im1 Do")

    # Try replacement; image should be skipped cleanly without attempts to parse it as content
    specs = ["/image_raw/skipped/"]
    replace_in_content_streams(pdf, specs)
    assert b"skipped" not in img_xobj.read_bytes()


def test_coverage_line_237_nested_xobject_resources_recursion():
    """Covers Line 237: Nested resource recursion inside sub-XObjects."""
    pdf = pikepdf.new()
    pdf.add_blank_page()

    # Nested child XObject (Grandchild)
    child_xobj = pdf.make_stream(b"BT (NestedTarget) Tj ET")
    child_xobj.Type = pikepdf.Name("/XObject")
    child_xobj.Subtype = pikepdf.Name("/Form")
    child_xobj.BBox = [0, 0, 100, 100]

    # Parent XObject containing the Grandchild reference in its own Resources
    parent_xobj = pdf.make_stream(b"/FmChild Do")
    parent_xobj.Type = pikepdf.Name("/XObject")
    parent_xobj.Subtype = pikepdf.Name("/Form")
    parent_xobj.BBox = [0, 0, 100, 100]
    parent_xobj.Resources = {"/XObject": {"/FmChild": child_xobj}}

    # Attach Parent to main page
    pdf.pages[0].Resources = {"/XObject": {"/FmParent": parent_xobj}}
    pdf.pages[0].Contents = pdf.make_stream(b"/FmParent Do")

    # Execute replace recursively
    specs = ["/NestedTarget/CleanNested/"]
    replace_in_content_streams(pdf, specs)

    # Validate that recursion correctly traversed into the child's resources
    assert b"CleanNested" in child_xobj.read_bytes()
