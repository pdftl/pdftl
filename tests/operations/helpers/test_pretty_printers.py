# tests/operations/helpers/test_pretty_printers.py

import pikepdf
import pytest

from pdftl.operations.helpers.pretty_printers import (
    _format_stream_ref,
    _format_array,
    _format_dict_value,
    pretty_format_pdf_obj,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pdf():
    p = pikepdf.new()
    p.add_blank_page()
    return p


# ---------------------------------------------------------------------------
# _format_stream_ref
# ---------------------------------------------------------------------------


class TestFormatStreamRef:
    def test_bare_stream_no_objgen(self, pdf):
        stream = pdf.make_stream(b"")
        # objgen is (0, 0) for unattached streams — falsy tuple check
        result = _format_stream_ref(stream, "")
        assert result.startswith("[Stream")

    def test_stream_with_subtype(self, pdf):
        stream = pdf.make_stream(b"")
        stream.Subtype = pikepdf.Name("/Form")
        result = _format_stream_ref(stream, "  ")
        assert result.startswith("  [Stream")
        assert "Subtype: /Form" in result

    def test_stream_without_subtype(self, pdf):
        stream = pdf.make_stream(b"")
        result = _format_stream_ref(stream, "")
        assert "Subtype" not in result

    def test_spaces_prefix(self, pdf):
        stream = pdf.make_stream(b"")
        result = _format_stream_ref(stream, "    ")
        assert result.startswith("    [")

    def test_attached_stream_shows_objgen(self, pdf):
        # Attach the stream so objgen is non-zero
        stream = pdf.make_stream(b"")
        stream.Subtype = pikepdf.Name("/Image")
        pdf.make_indirect(stream)
        result = _format_stream_ref(stream, "")
        # objgen will now be non-zero; should include the (N:0) part
        assert "(" in result and ":" in result


# ---------------------------------------------------------------------------
# _format_array
# ---------------------------------------------------------------------------


class TestFormatArray:
    def test_flat_primitive_array(self):
        arr = pikepdf.Array([pikepdf.Name("/PDF"), pikepdf.Name("/Text")])
        result = _format_array(arr, 0)
        assert result == ["[/PDF, /Text]"]

    def test_flat_array_with_indent(self):
        arr = pikepdf.Array([1, 2, 3])
        result = _format_array(arr, 4)
        assert result == ["    [1, 2, 3]"]

    def test_complex_array_with_stream(self, pdf):
        stream = pdf.make_stream(b"")
        arr = pikepdf.Array([stream])
        result = _format_array(arr, 0)
        assert len(result) == 1
        assert result[0].startswith("- [Stream")

    def test_complex_array_with_dict(self):
        d = pikepdf.Dictionary(Key=pikepdf.Name("/Val"))
        arr = pikepdf.Array([d])
        result = _format_array(arr, 0)
        assert any("Item 0" in line for line in result)

    def test_complex_array_with_nested_array(self):
        inner = pikepdf.Array([pikepdf.Name("/A"), pikepdf.Name("/B")])
        outer = pikepdf.Array([inner])
        result = _format_array(outer, 0)
        assert any("Item 0" in line for line in result)

    def test_complex_array_scalar_item(self, pdf):
        stream = pdf.make_stream(b"")
        arr = pikepdf.Array([stream, "scalar"])
        result = _format_array(arr, 0)
        assert any("- scalar" in line for line in result)


# ---------------------------------------------------------------------------
# _format_dict_value
# ---------------------------------------------------------------------------


class TestFormatDictValue:
    def test_stream_value(self, pdf):
        stream = pdf.make_stream(b"")
        result = _format_dict_value("/Key", stream, 0, "")
        assert len(result) == 1
        assert "/Key: [Stream" in result[0]

    def test_dict_value(self):
        inner = pikepdf.Dictionary(A=pikepdf.Name("/B"))
        result = _format_dict_value("/Key", inner, 0, "")
        assert result[0] == "/Key:"
        assert len(result) > 1

    def test_flat_array_value(self):
        arr = pikepdf.Array([1, 2, 3])
        result = _format_dict_value("/Key", arr, 0, "")
        assert result == ["/Key: [1, 2, 3]"]

    def test_complex_array_value(self, pdf):
        stream = pdf.make_stream(b"")
        arr = pikepdf.Array([stream])
        result = _format_dict_value("/Key", arr, 0, "")
        assert result[0] == "/Key:"
        assert len(result) > 1

    def test_scalar_value(self):
        result = _format_dict_value("/Key", pikepdf.Name("/Val"), 0, "")
        assert result == ["/Key: /Val"]

    def test_spaces_prefix_applied(self):
        result = _format_dict_value("/Key", pikepdf.Name("/Val"), 0, "    ")
        assert result[0].startswith("    /Key:")


# ---------------------------------------------------------------------------
# pretty_format_pdf_obj
# ---------------------------------------------------------------------------


class TestPrettyFormatPdfObj:
    def test_stream_top_level(self, pdf):
        stream = pdf.make_stream(b"")
        stream.Subtype = pikepdf.Name("/Form")
        result = pretty_format_pdf_obj(stream)
        assert len(result) == 1
        assert "[Stream" in result[0]

    def test_dictionary(self):
        d = pikepdf.Dictionary(
            Name=pikepdf.Name("/Helvetica"),
            Size=12,
        )
        result = pretty_format_pdf_obj(d)
        joined = "\n".join(result)
        assert "/Name" in joined or "Name" in joined

    def test_flat_array(self):
        arr = pikepdf.Array([pikepdf.Name("/A"), pikepdf.Name("/B")])
        result = pretty_format_pdf_obj(arr)
        assert result == ["[/A, /B]"]

    def test_complex_array(self, pdf):
        stream = pdf.make_stream(b"")
        arr = pikepdf.Array([stream])
        result = pretty_format_pdf_obj(arr)
        assert any("[Stream" in line for line in result)

    def test_scalar_fallback(self):
        result = pretty_format_pdf_obj(pikepdf.Name("/Test"))
        assert result == ["/Test"]

    def test_indent_applied(self):
        result = pretty_format_pdf_obj(pikepdf.Name("/Test"), indent=4)
        assert result == ["    /Test"]

    def test_nested_dict(self):
        inner = pikepdf.Dictionary(X=pikepdf.Name("/Y"))
        outer = pikepdf.Dictionary(Inner=inner)
        result = pretty_format_pdf_obj(outer)
        joined = "\n".join(result)
        assert "Inner" in joined
        assert "/X" in joined or "X" in joined

    def test_dict_with_stream_value(self, pdf):
        stream = pdf.make_stream(b"")
        stream.Subtype = pikepdf.Name("/Image")
        d = pikepdf.Dictionary(Img=stream)
        result = pretty_format_pdf_obj(d)
        assert any("[Stream" in line for line in result)
