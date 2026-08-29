# tests/operations/test_dump_streams.py

import logging
from unittest.mock import patch
import pikepdf
import pytest

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.operations.dump_streams import (
    dump_streams,
    dump_streams_cli_hook,
    _write_stream_block,
)
from pdftl.operations.helpers.stream_annotator import annotate_stream
from pdftl.operations.helpers.pretty_printers import pretty_format_pdf_obj


@pytest.fixture
def base_pdf():
    """Creates a basic PDF structure with 1 blank page for stream operations."""
    pdf = pikepdf.new()
    pdf.add_blank_page()
    return pdf


# ---------------------------------------------------------------------------
# 1. Annotation Lookups & Failures Coverage
# ---------------------------------------------------------------------------


def test_annotate_stream_tf_lookup(base_pdf):
    """Verifies that Tf font lookups find the correct BaseFont mapping."""
    font = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type1"),
            "/BaseFont": pikepdf.Name("/Helvetica"),
        }
    )
    resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})

    stream = b"/F1 12 Tf"
    annotated = annotate_stream(stream, resources)
    assert b"/Helvetica" in annotated


def test_annotate_stream_do_lookup(base_pdf):
    """Verifies that Do lookups correctly identify XObject Subtypes."""
    xobj = base_pdf.make_stream(b"")
    xobj.Type = pikepdf.Name("/XObject")
    xobj.Subtype = pikepdf.Name("/Form")

    resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Fm1": xobj})})

    stream = b"/Fm1 Do"
    annotated = annotate_stream(stream, resources)
    assert b"[Subtype: /Form]" in annotated


def test_annotate_stream_tr_lookup():
    """Verifies that Tr lookup translates rendering mode integers to human-readable strings."""
    # Test valid modes (0-7)
    for i in range(8):
        stream = f"{i} Tr".encode()
        annotated = annotate_stream(stream)
        assert b"[" in annotated

    # Test an invalid rendering mode
    stream = b"8 Tr"
    annotated = annotate_stream(stream)
    assert (
        b"% state/text: Set text rendering mode" in annotated
    )  # generic descriptor remains, but extra translation is omitted

    # Test empty lines, comment lines (lines 47, 57-58), long line overflow (line 125), and unknown operator (line 128)
    long_line = b"1 0 0 1 100 100 100 100 100 100 100 100 100 100 100 100 100 100 100 100 100 100 100 100 cm"
    stream_complex = b"  \n% This is a comment\n" + long_line + b"\nUNKNOWN_OP"
    annotated_complex = annotate_stream(stream_complex)
    assert b"This is a comment" in annotated_complex
    assert b"UNKNOWN_OP" in annotated_complex


def test_annotate_stream_gs_lookup(base_pdf):
    """Verifies ExtGState parameters (/ca, /CA, /BM) are correctly read and appended."""
    gs = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/ExtGState"),
            "/ca": 0.5,
            "/CA": 0.8,
            "/BM": pikepdf.Name("/Multiply"),
        }
    )
    resources = pikepdf.Dictionary({"/ExtGState": pikepdf.Dictionary({"/GS1": gs})})

    stream = b"/GS1 gs"
    annotated = annotate_stream(stream, resources)
    assert b"fill-alpha: 0.5" in annotated
    assert b"stroke-alpha: 0.8" in annotated
    assert b"blend: Multiply" in annotated


def test_annotate_stream_graceful_lookup_failures():
    """Ensures AttributeError, KeyError, TypeError, ValueError are caught and ignored inside lookups."""

    # Passing a resources object that raises custom exceptions on lookup access
    class BrokenResources:
        @property
        def Font(self):
            raise KeyError("Simulated key error")

    stream = b"/F1 12 Tf"
    annotated = annotate_stream(stream, BrokenResources())
    assert b"Tf" in annotated  # Annotation should succeed without throwing exceptions


# ---------------------------------------------------------------------------
# 2. Pretty Formatting Engine Coverage
# ---------------------------------------------------------------------------


def test_pretty_format_pdf_obj_direct_stream(base_pdf):
    """Directly pretty formatting a Stream helper."""
    stream = base_pdf.make_stream(b"")
    stream.Type = pikepdf.Name("/XObject")
    stream.Subtype = pikepdf.Name("/Form")

    formatted = pretty_format_pdf_obj(stream)
    assert "[Stream" in formatted[0]
    assert "Subtype: /Form" in formatted[0]


def test_pretty_format_pdf_obj_complex_dictionary(base_pdf):
    """Formatting a complex nested Dictionary containing streams, dicts, flat, and complex arrays."""
    nested_dict = pikepdf.Dictionary(NestedKey="Value")
    flat_array = pikepdf.Array([1, 2, 3])

    stream = base_pdf.make_stream(b"")
    stream.Subtype = pikepdf.Name("/Image")

    complex_array = pikepdf.Array([nested_dict, stream])

    resources = pikepdf.Dictionary(
        SubDict=nested_dict,
        SubStream=stream,
        FlatArr=flat_array,
        ComplexArr=complex_array,
        SimpleVal="PlainString",
    )

    formatted = "\n".join(pretty_format_pdf_obj(resources))
    assert "SubStream: [Stream" in formatted
    assert "SubDict:" in formatted
    assert "FlatArr: [1, 2, 3]" in formatted
    assert "ComplexArr:" in formatted
    assert "SimpleVal: PlainString" in formatted


def test_pretty_format_pdf_obj_array_variants(base_pdf):
    """Standalone Flat Arrays vs complex standalone arrays."""
    flat = pikepdf.Array([pikepdf.Name("/PDF"), pikepdf.Name("/Text")])
    formatted_flat = pretty_format_pdf_obj(flat)
    assert "[/PDF, /Text]" in formatted_flat[0]

    stream = base_pdf.make_stream(b"")
    nested_dict = pikepdf.Dictionary(Key="Val")
    complex_arr = pikepdf.Array([stream, nested_dict, "Scalar"])

    formatted_complex = "\n".join(pretty_format_pdf_obj(complex_arr))
    assert "- [Stream" in formatted_complex
    assert "- [Item 1]:" in formatted_complex
    assert "- Scalar" in formatted_complex


def test_pretty_format_pdf_obj_scalar():
    """Covers line 210 fallback path for simple scalar objects in the pretty formatting engine."""
    formatted = pretty_format_pdf_obj("PlainScalarString")
    assert formatted == ["PlainScalarString"]


# ---------------------------------------------------------------------------
# 3. Stream Collection Configurations
# ---------------------------------------------------------------------------


def test_collect_page_stream_normalization_disabled(base_pdf):
    """Page collection scenarios where normalize=False is configured."""
    # 1. Page with no Contents key
    page_empty = base_pdf.pages[0]
    if "/Contents" in page_empty:
        del page_empty["/Contents"]
    from pdftl.operations.dump_streams import _collect_page_stream

    assert _collect_page_stream(page_empty, normalize=False) == (None, [])

    # 2. Page with multi-stream array (normalize=False)
    pdf_multi = pikepdf.new()
    pdf_multi.add_blank_page()
    page_multi = pdf_multi.pages[0]
    stream1 = pdf_multi.make_stream(b"BT (One) Tj ET ")
    stream2 = pdf_multi.make_stream(b"BT (Two) Tj ET")
    page_multi.Contents = pikepdf.Array([stream1, stream2])

    result, warnings = _collect_page_stream(page_multi, normalize=False)
    assert result == b"BT (One) Tj ET BT (Two) Tj ET"
    assert len(warnings) == 1
    assert "Page /Contents is an array of 2 streams" in warnings[0]

    # 3. Page with multi-stream array (normalize=True)
    result_norm, warnings_norm = _collect_page_stream(page_multi, normalize=True)
    assert len(warnings_norm) == 1
    assert "Page /Contents is an array of 2 streams" in warnings_norm[0]

    # 4. Page with single content stream
    page_single = base_pdf.pages[0]
    page_single.Contents = base_pdf.make_stream(b"BT (Single) Tj ET")
    assert _collect_page_stream(page_single, normalize=False) == (b"BT (Single) Tj ET", [])


# ---------------------------------------------------------------------------
# 4. Walked-stream Normalization Exception Fallback
# ---------------------------------------------------------------------------


def test_collect_walked_streams_normalization_exception_fallback(base_pdf):
    """Triggers the try-except fallback in read_xobject_stream (via
    _collect_walked_streams) when normalization fails: raw bytes are kept."""
    if "/Contents" in base_pdf.pages[0]:
        del base_pdf.pages[0].Contents

    xobj = base_pdf.make_stream(b"BT (Stream) Tj ET")
    xobj.Type = pikepdf.Name("/XObject")
    xobj.Subtype = pikepdf.Name("/Form")
    xobj.BBox = [0, 0, 100, 100]

    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/XObject": pikepdf.Dictionary({"/Fm1": xobj})}
    )

    # Force unparse_content_stream to throw a PdfError during collection
    with patch(
        "pikepdf.unparse_content_stream", side_effect=pikepdf.PdfError("Mock syntax error")
    ):
        res = dump_streams(base_pdf, ["1"])

    # It must gracefully catch the exception, print warning, and fall back to the raw content stream
    fm1_entry = [entry for entry in res.data if "XObject /Fm1" in entry[0]][0]
    assert fm1_entry[1] == b"BT (Stream) Tj ET"


def test_collect_walked_streams_none_content_warns():
    """A Form XObject whose read_xobject_stream returns None (e.g. a
    malformed/uninitialized stream) produces a 'No stream content!'
    warning rather than erroring."""
    pdf = pikepdf.new()
    pdf.add_blank_page()

    with patch("pdftl.operations.dump_streams.read_xobject_stream", return_value=None):
        xobj = pdf.make_stream(b"")
        xobj.Type = pikepdf.Name("/XObject")
        xobj.Subtype = pikepdf.Name("/Form")
        xobj.BBox = [0, 0, 100, 100]
        pdf.pages[0].Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/FmNone": xobj})}
        )

        res = dump_streams(pdf, ["1"])

    fmnone_entry = [entry for entry in res.data if "FmNone" in entry[0]][0]
    assert "No stream content!" in fmnone_entry[2]
    assert fmnone_entry[1] == b""


# ---------------------------------------------------------------------------
# 5. Shared Resource Warning Triggers
# ---------------------------------------------------------------------------


def test_shared_resource_warning_output(base_pdf):
    """Ensures resources shared across multiple pages properly list those pages in warnings."""
    xobj = base_pdf.make_stream(b"")
    xobj.Type = pikepdf.Name("/XObject")
    xobj.Subtype = pikepdf.Name("/Form")
    xobj.BBox = [0, 0, 100, 100]

    # Create 3 pages, referencing the same Form XObject
    page_resources = {"/XObject": {"/Fm1": xobj}}
    base_pdf.add_blank_page()
    base_pdf.add_blank_page()

    base_pdf.pages[0].Resources = page_resources
    base_pdf.pages[1].Resources = page_resources
    base_pdf.pages[2].Resources = page_resources

    res = dump_streams(base_pdf, ["1-3"])

    # Locate collected data for Fm1 under Page 1
    warnings = [w for header, _, w in res.data if "Page 1 / XObject /Fm1" == header][0]
    assert any("Shared resource: also appears on page(s): 2, 3" in warn for warn in warnings)

    # Ensure page 2 yielded an alias
    page2_content = [c for h, c, w in res.data if "Page 2 / XObject /Fm1" == h][0]
    assert b"% ALIAS OF: Page 1 / XObject /Fm1" in page2_content


def test_build_stream_page_map_complex(base_pdf):
    """Covers the multi-kind sharing map: a nested Form chain plus a
    non-Form Image sibling (which the walker never yields at all)."""
    img_xobj = base_pdf.make_stream(b"")
    img_xobj.Type = pikepdf.Name("/XObject")
    img_xobj.Subtype = pikepdf.Name("/Image")

    grandchild = base_pdf.make_stream(b"")
    grandchild.Type = pikepdf.Name("/XObject")
    grandchild.Subtype = pikepdf.Name("/Form")
    grandchild.BBox = [0, 0, 100, 100]

    child = base_pdf.make_stream(b"")
    child.Type = pikepdf.Name("/XObject")
    child.Subtype = pikepdf.Name("/Form")
    child.BBox = [0, 0, 100, 100]
    child.Resources = pikepdf.Dictionary(
        {"/XObject": pikepdf.Dictionary({"/FmGrandchild": grandchild})}
    )

    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary(
                {
                    "/FmChild": child,
                    "/FmGrandchild": grandchild,  # referenced again to trigger seen skip
                    "/Img": img_xobj,  # triggers non-Form skip
                }
            )
        }
    )

    from pdftl.operations.dump_streams import _build_stream_page_map

    page_map = _build_stream_page_map(base_pdf, [1])
    assert grandchild.objgen in page_map
    assert child.objgen in page_map
    assert img_xobj.objgen not in page_map


def test_build_stream_page_map_covers_pattern_smask_annotation(base_pdf):
    """The sharing map now also tracks Patterns, SMask groups, and
    annotation appearance streams, not just Forms."""
    pat = base_pdf.make_stream(b"pattern content")
    pat.PatternType = 1

    smask_group = base_pdf.make_stream(b"mask content")
    smask_group.Subtype = pikepdf.Name("/Form")
    smask_group.BBox = [0, 0, 100, 100]
    smask = pikepdf.Dictionary({"/Type": pikepdf.Name("/Mask"), "/G": smask_group})
    gs = pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState"), "/SMask": smask})

    ap_form = base_pdf.make_stream(b"annot content")
    ap_form.Subtype = pikepdf.Name("/Form")
    ap_form.BBox = [0, 0, 100, 100]
    annot = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Annot"),
            "/Subtype": pikepdf.Name("/FreeText"),
            "/AP": pikepdf.Dictionary({"/N": ap_form}),
        }
    )

    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {
            "/Pattern": pikepdf.Dictionary({"/P1": pat}),
            "/ExtGState": pikepdf.Dictionary({"/GS1": gs}),
        }
    )
    base_pdf.pages[0].Annots = pikepdf.Array([annot])

    from pdftl.operations.dump_streams import _build_stream_page_map

    page_map = _build_stream_page_map(base_pdf, [1])
    assert pat.objgen in page_map
    assert smask_group.objgen in page_map
    assert ap_form.objgen in page_map


# ---------------------------------------------------------------------------
# 6. Comprehensive Walked-Stream Collection Coverage
# ---------------------------------------------------------------------------


def test_dump_streams_comprehensive_nesting_and_alias(base_pdf):
    """A Form nested two levels deep, plus a sibling reference to the
    inner Form under a second name -- exercising nesting, alias-stub
    dedup, dump_resources, and normalize=False in one pass."""
    img_xobj = base_pdf.make_stream(b"")
    img_xobj.Type = pikepdf.Name("/XObject")
    img_xobj.Subtype = pikepdf.Name("/Image")

    empty_xobj = base_pdf.make_stream(b"")
    empty_xobj.Type = pikepdf.Name("/XObject")
    empty_xobj.Subtype = pikepdf.Name("/Form")
    empty_xobj.BBox = [0, 0, 100, 100]

    nested_xobj = base_pdf.make_stream(b"BT (Nested) Tj ET")
    nested_xobj.Type = pikepdf.Name("/XObject")
    nested_xobj.Subtype = pikepdf.Name("/Form")
    nested_xobj.BBox = [0, 0, 100, 100]
    nested_xobj.Resources = pikepdf.Dictionary(
        {"/XObject": pikepdf.Dictionary({"/FmEmpty": empty_xobj})}
    )

    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {
            "/XObject": pikepdf.Dictionary(
                {
                    "/FmNested": nested_xobj,
                    "/FmEmpty": empty_xobj,  # triggers seen_objgens skip
                    "/Img": img_xobj,  # triggers non-Form skip
                }
            )
        }
    )

    res = dump_streams(base_pdf, ["annotate", "resources=true", "normalize=false", "1"])
    # entries: [Page 1 Contents, Page 1 Resources,
    #           FmNested, FmNested/Resources, FmNested/XObject FmEmpty,
    #           FmEmpty (alias, since seen via FmNested first or vice versa
    #           depending on dict iteration order)]
    collected = res.data

    empty_warnings = [w for h, c, w in collected if "FmEmpty" in h and b"ALIAS" not in c][0]
    assert "Empty stream content!" in empty_warnings
    alias_entries = [c for _, c, _ in collected if b"% ALIAS OF:" in c]
    assert len(alias_entries) == 1


# ---------------------------------------------------------------------------
# 7. Integration Command Run & Page Order Sequence Retention
# ---------------------------------------------------------------------------


def test_dump_streams_integration_options(base_pdf):
    """Verifies output specs, non-standard order repeats, and resources/annotation blocks."""
    # Write page content
    base_pdf.pages[0].Contents = base_pdf.make_stream(b"BT /F1 12 Tf ET")
    font = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type1"),
            "/BaseFont": pikepdf.Name("/Times-Roman"),
        }
    )
    base_pdf.pages[0].Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})

    # Request pages in duplicate, mixed order: page 1 twice, with resources and annotations
    res = dump_streams(base_pdf, ["annotate", "resources=true", "1", "1"])

    assert res.success is True
    assert len(res.data) == 4  # [Page 1, Page 1 resources, Page 1, Page 1 resources]

    # Check that annotations found the BaseFont lookup
    assert b"/Times-Roman" in res.data[0][1]


def test_dump_streams_empty_page_handling(base_pdf):
    """Verifies that blank pages containing no content stream map show warnings."""
    page = base_pdf.pages[0]
    if "/Contents" in page:
        del page["/Contents"]

    # Must pass normalize=false to trigger the warning payload check path safely
    res = dump_streams(base_pdf, ["normalize=false", "1"])
    assert res.success is True
    assert res.data[0][1] == b""
    assert "No content stream found" in res.data[0][2][0]


def test_dump_streams_default_page_specs(base_pdf):
    """Covers line 505 by falling back to '1-end' when empty specifications are passed."""
    res = dump_streams(base_pdf, [])
    assert res.success is True
    assert len(res.data) == 1
    assert "Page 1" in res.data[0][0]


def test_dump_streams_annotate_without_normalize_warning(base_pdf, caplog):
    """Covers line 538 by triggering a warning when annotations are requested without normalization."""
    with caplog.at_level(logging.WARNING):
        res = dump_streams(base_pdf, ["annotate", "normalize=false"])
    assert res.success is True
    assert any(
        "annotation is only reliable on normalized streams" in record.message
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# 8. CLI Hook Output Formatting Coverage
# ---------------------------------------------------------------------------


def test_dump_streams_cli_hook(tmp_path):
    """Executes the CLI hook pipeline to write collected logs into standard outputs."""
    out_file = tmp_path / "output.txt"

    # Mock OpResult data
    mock_data = [
        ("Page 1 / Contents", b"BT ET", []),
        ("Page 2 / Contents", b"", ["Empty stream content!"]),
    ]
    result = OpResult(success=True, data=mock_data, meta={c.META_OUTPUT_FILE: str(out_file)})

    # Execute Hook
    dump_streams_cli_hook(result, stage=None, _pipeline=None)

    output_content = out_file.read_text()
    assert "=== Page 1 / Contents" in output_content
    assert "BT ET" in output_content
    assert "=== Empty stream content!" in output_content


# ---------------------------------------------------------------------------
# 9. Content-Line Escaping (collision with structural '===' framing)
# ---------------------------------------------------------------------------


def test_escape_content_line():
    """Directly verifies the escaping helper."""
    from pdftl.operations.dump_streams import _escape_content_line

    assert _escape_content_line("=== Fake Header Tj") == "\\=== Fake Header Tj"
    assert _escape_content_line("\\Already backslashed Tj") == "\\\\Already backslashed Tj"
    assert _escape_content_line("BT (Normal) Tj ET") == "BT (Normal) Tj ET"
    assert _escape_content_line("") == ""


def test_write_stream_block_escapes_equals_collision():
    """A content line starting with '===' must be escaped so import_streams
    doesn't misread it as a structural header on round-trip."""
    import io

    out = io.StringIO()
    # Deliberately includes a line that looks like a header and a line that
    # starts with the escape character itself.
    content = b"BT (Normal) Tj ET\n=== Fake Header Tj\n\\Already backslashed Tj"
    _write_stream_block(out, "Page 1 / Contents", content)
    text = out.getvalue()

    # The real header line is unescaped.
    assert "=== Page 1 / Contents" in text
    # The colliding content line must be escaped with a leading backslash.
    assert "\\=== Fake Header Tj" in text
    # A content line already starting with backslash must also be escaped
    # (double-escaped), so it can be unambiguously reversed.
    assert "\\\\Already backslashed Tj" in text
    # A normal line is untouched.
    assert "BT (Normal) Tj ET" in text


def test_dump_streams_escapes_real_content_with_equals(base_pdf):
    """End-to-end: dump_streams escapes a page content stream that happens
    to contain a line starting with '==='."""
    base_pdf.pages[0].Contents = base_pdf.make_stream(b"=== not a header ===")

    res = dump_streams(base_pdf, ["normalize=false", "1"])
    content = res.data[0][1]

    # Stored content itself is untouched in memory...
    assert content == b"=== not a header ==="

    # ...but when written out via the CLI hook (or _write_stream_block
    # directly), the line is escaped.
    import io as _io

    out = _io.StringIO()
    _write_stream_block(out, "Page 1 / Contents", content)
    text = out.getvalue()
    assert "\\=== not a header ===" in text


def test_api_serialize_stream_blocks_renders_text():
    """_api_serialize_stream_blocks must format collected (header, content,
    warnings) tuples into the same text block format the CLI hook writes,
    wrapped in the {"kind": "text", ...} shape the server expects."""
    from pdftl.operations.dump_streams import _api_serialize_stream_blocks

    collected = [
        ("Page 1 / Contents", b"BT ET", []),
        ("Page 2 / Contents", b"", ["Empty stream content!"]),
    ]

    result_bytes, meta = _api_serialize_stream_blocks(collected, {})

    assert result_bytes is None
    assert meta["kind"] == "text"
    assert "=== Page 1 / Contents" in meta["text"]
    assert "BT ET" in meta["text"]
    assert "=== Empty stream content!" in meta["text"]


def test_api_serialize_stream_blocks_empty_collected():
    """An empty collected list still produces a valid (empty-ish) text
    payload rather than erroring."""
    from pdftl.operations.dump_streams import _api_serialize_stream_blocks

    result_bytes, meta = _api_serialize_stream_blocks([], {})

    assert result_bytes is None
    assert meta == {"kind": "text", "text": ""}


def test_api_serialize_stream_blocks_ignores_meta_argument():
    """The _meta parameter is accepted but unused -- confirms passing an
    arbitrary/irrelevant meta dict doesn't affect output."""
    from pdftl.operations.dump_streams import _api_serialize_stream_blocks

    collected = [("Page 1 / Contents", b"content", [])]
    result_bytes, meta = _api_serialize_stream_blocks(collected, {"json_output": True, "extra": 1})

    assert meta["kind"] == "text"
    assert "content" in meta["text"]


# ---------------------------------------------------------------------------
# 10. Inherited /Resources (page-tree inheritance, not own-dict)
# ---------------------------------------------------------------------------


def test_dump_streams_recurses_into_inherited_page_resources():
    """Fails on current code: a page whose /Resources is only inherited
    from a /Pages ancestor is treated as having no resources at all, so
    dump_streams (recurse=true, the default) never descends into its
    Form XObjects, and resources=true never dumps its resource block."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    parent = page.obj["/Parent"]

    form = pdf.make_stream(b"1 0 0 RG")
    form.Type = pikepdf.Name("/XObject")
    form.Subtype = pikepdf.Name("/Form")
    form.BBox = pikepdf.Array([0, 0, 100, 100])

    page.Contents = pdf.make_stream(b"/Fm1 Do")
    parent[pikepdf.Name.Resources] = pikepdf.Dictionary(
        {"/XObject": pikepdf.Dictionary({"/Fm1": form})}
    )
    if pikepdf.Name.Resources in page.obj:
        del page.obj[pikepdf.Name.Resources]

    result = dump_streams(pdf, ["resources=true"])
    headers = [entry[0] for entry in result.data]

    assert "Page 1 / Resources" in headers
    assert any("XObject /Fm1" in h for h in headers)


def test_build_stream_page_map_page_with_no_resources_anywhere():
    """Covers the loop-continues-without-scanning branch: a page with no
    /Resources in its own dict and none inherited must be skipped
    without error, contributing nothing to the map."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    if pikepdf.Name.Resources in page.obj:
        del page.obj[pikepdf.Name.Resources]
    if pikepdf.Name.Resources in pdf.Root.Pages:
        del pdf.Root.Pages[pikepdf.Name.Resources]

    from pdftl.operations.dump_streams import _build_stream_page_map

    result = _build_stream_page_map(pdf, [1])
    assert result == {}


def test_dump_streams_recurse_false_skips_xobject_recursion(base_pdf):
    """Covers the recurse=False fall-through: entries returned without
    ever walking resource streams, even though the page has Form
    XObjects in its resources."""
    xobj = base_pdf.make_stream(b"")
    xobj.Type = pikepdf.Name("/XObject")
    xobj.Subtype = pikepdf.Name("/Form")
    xobj.BBox = pikepdf.Array([0, 0, 100, 100])
    base_pdf.pages[0].Contents = base_pdf.make_stream(b"/Fm1 Do")
    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/XObject": pikepdf.Dictionary({"/Fm1": xobj})}
    )

    res = dump_streams(base_pdf, ["recurse=false"])
    headers = [h for h, _, _ in res.data]

    assert headers == ["Page 1 / Contents"]


# ---------------------------------------------------------------------------
# 11. New breadcrumb segments: Pattern, SMask, Annotation
# ---------------------------------------------------------------------------


def test_dump_streams_pattern_breadcrumb(base_pdf):
    """A tiling Pattern gets a 'Pattern <name>' breadcrumb segment."""
    pat = base_pdf.make_stream(b"1 0 0 rg")
    pat.PatternType = 1
    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/Pattern": pikepdf.Dictionary({"/P1": pat})}
    )

    res = dump_streams(base_pdf, ["1"])
    headers = [h for h, _, _ in res.data]
    assert "Page 1 / Pattern /P1" in headers


def test_dump_streams_smask_breadcrumb(base_pdf):
    """An ExtGState /SMask /G group gets an 'ExtGState <name> / SMask'
    breadcrumb segment."""
    group = base_pdf.make_stream(b"mask content")
    group.Subtype = pikepdf.Name("/Form")
    group.BBox = [0, 0, 100, 100]
    smask = pikepdf.Dictionary({"/Type": pikepdf.Name("/Mask"), "/G": group})
    gs = pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState"), "/SMask": smask})
    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/ExtGState": pikepdf.Dictionary({"/GS1": gs})}
    )

    res = dump_streams(base_pdf, ["1"])
    headers = [h for h, _, _ in res.data]
    assert "Page 1 / ExtGState /GS1 / SMask" in headers


def test_dump_streams_annotation_direct_stream_breadcrumb(base_pdf):
    """An annotation's direct-stream /AP /N entry gets an
    'Annot <1-based-index> / AP /N' breadcrumb, with no /State suffix."""
    ap_form = base_pdf.make_stream(b"annot content")
    ap_form.Subtype = pikepdf.Name("/Form")
    ap_form.BBox = [0, 0, 100, 100]
    annot = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Annot"),
            "/Subtype": pikepdf.Name("/FreeText"),
            "/AP": pikepdf.Dictionary({"/N": ap_form}),
        }
    )
    base_pdf.pages[0].Annots = pikepdf.Array([annot])

    res = dump_streams(base_pdf, ["1"])
    headers = [h for h, _, _ in res.data]
    assert "Page 1 / Annot 1 / AP /N" in headers


def test_dump_streams_annotation_state_dict_breadcrumb(base_pdf):
    """An annotation's /AP /N as a state sub-dictionary gets a
    '/ State <state>' suffix appended to the breadcrumb."""
    on_form = base_pdf.make_stream(b"on content")
    on_form.Subtype = pikepdf.Name("/Form")
    on_form.BBox = [0, 0, 100, 100]
    annot = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Annot"),
            "/Subtype": pikepdf.Name("/Widget"),
            "/AP": pikepdf.Dictionary({"/N": pikepdf.Dictionary({"/On": on_form})}),
        }
    )
    base_pdf.pages[0].Annots = pikepdf.Array([annot])

    res = dump_streams(base_pdf, ["1"])
    headers = [h for h, _, _ in res.data]
    assert "Page 1 / Annot 1 / AP /N / State /On" in headers


def test_dump_streams_annotation_index_is_second_position(base_pdf):
    """The second annotation in /Annots gets 'Annot 2', confirming the
    1-based conversion from the walker's 0-based annot_index."""
    no_ap_annot = pikepdf.Dictionary(
        {"/Type": pikepdf.Name("/Annot"), "/Subtype": pikepdf.Name("/FreeText")}
    )
    ap_form = base_pdf.make_stream(b"second content")
    ap_form.Subtype = pikepdf.Name("/Form")
    ap_form.BBox = [0, 0, 100, 100]
    second_annot = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Annot"),
            "/Subtype": pikepdf.Name("/FreeText"),
            "/AP": pikepdf.Dictionary({"/N": ap_form}),
        }
    )
    base_pdf.pages[0].Annots = pikepdf.Array([no_ap_annot, second_annot])

    res = dump_streams(base_pdf, ["1"])
    headers = [h for h, _, _ in res.data]
    assert "Page 1 / Annot 2 / AP /N" in headers


def test_dump_streams_nested_form_inside_pattern_breadcrumb(base_pdf):
    """A Form nested inside a Pattern's own /Resources produces a
    3-segment breadcrumb, confirming the depth-stack correctly threads
    through a non-Form parent kind."""
    nested_form = base_pdf.make_stream(b"nested in pattern")
    nested_form.Subtype = pikepdf.Name("/Form")
    nested_form.BBox = [0, 0, 100, 100]

    pat = base_pdf.make_stream(b"pattern content")
    pat.PatternType = 1
    pat.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Fm1": nested_form})})

    base_pdf.pages[0].Resources = pikepdf.Dictionary(
        {"/Pattern": pikepdf.Dictionary({"/P1": pat})}
    )

    res = dump_streams(base_pdf, ["1"])
    headers = [h for h, _, _ in res.data]
    assert "Page 1 / Pattern /P1 / XObject /Fm1" in headers


def test_breadcrumb_segment_page_kind_returns_empty():
    """Direct unit test of the kind=='page' fallback branch in
    _breadcrumb_segment (line 125), which the normal walk never exercises
    since _collect_walked_streams explicitly skips page-kind entries
    before calling this helper."""
    from pdftl.operations.dump_streams import _breadcrumb_segment
    from pdftl.utils.pdf_resources import StreamContext

    ctx = StreamContext(page_num=1, depth=0, kind="page", resources=None)
    assert _breadcrumb_segment(ctx) == ""
