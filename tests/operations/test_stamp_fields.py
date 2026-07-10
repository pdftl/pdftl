# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/test_stamp_fields.py

import pytest
from unittest.mock import patch

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.stamp_fields import (
    _get_field_widgets,
    _parse_args,
    stamp_fields,
    _parse_align,
)


def test_parse_args():
    """Verify flat token parsing logic for stamp_fields arguments."""
    src, fields, opts = _parse_args(["sig.pdf", "Signature"])
    assert src == "sig.pdf"
    assert fields == ["Signature"]
    assert opts == {}

    src, fields, opts = _parse_args(
        ["sig.pdf", "Signature", "scale=0.8", "opacity=0.5", "align=top-left"]
    )
    assert src == "sig.pdf"
    assert fields == ["Signature"]
    assert opts == {"scale": "0.8", "opacity": "0.5", "align": "top-left"}

    src, fields, opts = _parse_args(["sig.pdf", "Signature", "scale=FIT"])
    assert src == "sig.pdf"
    assert fields == ["Signature"]
    assert opts == {"scale": "fit"}

    with pytest.raises(InvalidArgumentError, match="Expected format"):
        _parse_args([])

    with pytest.raises(InvalidArgumentError, match="Expected format"):
        _parse_args(["scale=fit"])

    with pytest.raises(InvalidArgumentError, match="Missing target field names"):
        _parse_args(["sig.pdf"])


def test_parse_align_variations():
    """Test string-based alignment parsed correctly into vertical and horizontal anchors."""
    assert _parse_align("center") == ("mid", "center")
    assert _parse_align("top-left") == ("top", "left")
    assert _parse_align("right-bottom") == ("bottom", "right")
    assert _parse_align("mid") == ("mid", "center")
    assert _parse_align("left") == ("mid", "left")

    with pytest.raises(InvalidArgumentError, match="Invalid align values"):
        _parse_align("top-bottom")  # contrarily vertical values

    with pytest.raises(InvalidArgumentError, match="Invalid align values"):
        _parse_align("unknown-left")

    with pytest.raises(InvalidArgumentError, match="Invalid align format"):
        _parse_align("top-mid-left")

    with pytest.raises(InvalidArgumentError, match="Invalid align values"):
        _parse_align("magic")  # length 1, completely invalid


@pytest.fixture
def form_pdf():
    """Provides a basic valid PDF containing a nested AcroForm structure."""
    import pikepdf

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))

    parent = pikepdf.Dictionary(
        T=pikepdf.String("Parent"), Kids=pikepdf.Array(), FT=pikepdf.Name.Tx
    )
    parent_obj = pdf.make_indirect(parent)

    child = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        T=pikepdf.String("Child"),
        Rect=pikepdf.Array([10, 10, 110, 60]),
    )
    child_obj = pdf.make_indirect(child)
    parent_obj.Kids.append(child_obj)

    pdf.pages[0].Annots = pdf.make_indirect(pikepdf.Array([child_obj]))
    pdf.Root.AcroForm = pdf.make_indirect(pikepdf.Dictionary(Fields=pikepdf.Array([parent_obj])))
    return pdf


@pytest.fixture
def stamp_pdf_path(tmp_path):
    """Provides the absolute path to a minimal temporary valid PDF to be used as a stamp."""
    import pikepdf

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(50, 100))  # 1:2 aspect ratio
    path = tmp_path / "stamp.pdf"
    pdf.save(path)
    return str(path)


def test_stamp_fields_success(form_pdf, stamp_pdf_path):
    """Test standard placement into a valid widget, fitting by default."""
    result = stamp_fields(form_pdf, [stamp_pdf_path, "Parent.Child"])
    assert result.success

    child_obj = form_pdf.Root.AcroForm.Fields[0].Kids[0]
    assert "/AP" in child_obj
    assert "/N" in child_obj.AP

    ap_stream = child_obj.AP.N
    assert "/BBox" in ap_stream
    assert "/Resources" in ap_stream
    assert "/SrcPageForm" in ap_stream.Resources.XObject


def test_stamp_fields_multiple_fields(form_pdf, tmp_path):
    """Verify multistamp logic where multiple fields sequentially draw from multi-page stamps."""
    import pikepdf

    # 1. Create a multi-page stamp PDF
    stamp_pdf = pikepdf.new()
    stamp_pdf.add_blank_page(page_size=(50, 100))
    stamp_pdf.add_blank_page(page_size=(100, 50))
    stamp_path = tmp_path / "multi_stamp.pdf"
    stamp_pdf.save(stamp_path)

    # 2. Inject a second field into the form document
    child2 = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        T=pikepdf.String("Child2"),
        Rect=pikepdf.Array([10, 70, 110, 120]),
    )
    child2_obj = form_pdf.make_indirect(child2)
    form_pdf.Root.AcroForm.Fields[0].Kids.append(child2_obj)
    form_pdf.pages[0].Annots.append(child2_obj)

    # 3. Stamp both fields
    result = stamp_fields(form_pdf, [str(stamp_path), "Parent.Child", "Parent.Child2"])
    assert result.success

    child1_obj = form_pdf.Root.AcroForm.Fields[0].Kids[0]
    child2_obj = form_pdf.Root.AcroForm.Fields[0].Kids[1]

    assert "/AP" in child1_obj
    assert "/AP" in child2_obj


def test_stamp_fields_options(form_pdf, stamp_pdf_path):
    """Test placement with scaling overrides, alignment, and explicit opacity options."""
    result = stamp_fields(
        form_pdf, [stamp_pdf_path, "Parent.Child", "scale=0.5", "align=top-left", "opacity=0.5"]
    )
    assert result.success

    ap_stream = form_pdf.Root.AcroForm.Fields[0].Kids[0].AP.N
    assert "/ExtGState" in ap_stream.Resources
    assert "/GS0" in ap_stream.Resources.ExtGState
    assert ap_stream.Resources.ExtGState.GS0.ca == 0.5


def test_stamp_fields_align_bottom_right(form_pdf, stamp_pdf_path):
    """Test placement with bottom-right alignment to cover offset calculations."""
    result = stamp_fields(form_pdf, [stamp_pdf_path, "Parent.Child", "align=bottom-right"])
    assert result.success


def test_stamp_fields_scale_modes(form_pdf, stamp_pdf_path):
    """Verify various scale mode mappings (fit, width, height, stretch)."""
    # Test stretch
    result = stamp_fields(form_pdf, [stamp_pdf_path, "Parent.Child", "scale=stretch"])
    assert result.success
    ap_stream = form_pdf.Root.AcroForm.Fields[0].Kids[0].AP.N
    assert b"cm" in ap_stream.read_bytes()

    # Test width
    result = stamp_fields(form_pdf, [stamp_pdf_path, "Parent.Child", "scale=width"])
    assert result.success

    # Test height
    result = stamp_fields(form_pdf, [stamp_pdf_path, "Parent.Child", "scale=height"])
    assert result.success


def test_stamp_fields_non_zero_origin(form_pdf, tmp_path):
    """Test stamping with a source PDF that has a non-zero BBox origin (cropped signature)."""
    import pikepdf

    # Create a stamp with a non-zero MediaBox (cropped page with offset origin)
    # BBox bounds match the origin shift of tc.pdf: [73, 719, 161, 738]
    stamp_pdf = pikepdf.new()
    page = stamp_pdf.add_blank_page()
    page.MediaBox = [73, 719, 161, 738]

    stamp_path = tmp_path / "cropped_stamp.pdf"
    stamp_pdf.save(stamp_path)

    # widget_w = 110 - 10 = 100
    # widget_h = 60 - 10 = 50
    result = stamp_fields(form_pdf, [str(stamp_path), "Parent.Child", "scale=stretch"])
    assert result.success

    child_obj = form_pdf.Root.AcroForm.Fields[0].Kids[0]
    ap_stream = child_obj.AP.N
    stream_content = bytes(ap_stream.read_bytes())

    # Find the matrix operator line in the generated Appearance Stream
    assert b"cm" in stream_content
    lines = stream_content.split(b"\n")
    matrix_line = next(line for line in lines if b"cm" in line)
    matrix_line_vals = matrix_line.split()[:6]
    matrix_vals = [float(x) for x in matrix_line_vals]

    # Expected values calculation:
    # src_w = 161 - 73 = 88
    # src_h = 738 - 719 = 19
    # scale_x = 100 / 88 = 1.13636...
    # scale_y = 50 / 19 = 2.63157...
    # trans_x = 0 - 73 * scale_x = -82.9545
    # trans_y = 0 - 719 * scale_y = -1892.1052
    expected_scale_x = 100.0 / 88.0
    expected_scale_y = 50.0 / 19.0
    expected_trans_x = -73.0 * expected_scale_x
    expected_trans_y = -719.0 * expected_scale_y

    assert abs(matrix_vals[0] - expected_scale_x) < 1e-4
    assert abs(matrix_vals[3] - expected_scale_y) < 1e-4
    assert abs(matrix_vals[4] - expected_trans_x) < 1e-4
    assert abs(matrix_vals[5] - expected_trans_y) < 1e-4


def test_stamp_fields_missing_args(form_pdf):
    with pytest.raises(InvalidArgumentError, match="Missing arguments"):
        stamp_fields(form_pdf, [])


def test_stamp_fields_no_acroform(stamp_pdf_path):
    import pikepdf

    pdf = pikepdf.new()
    pdf.add_blank_page()
    with pytest.raises(InvalidArgumentError, match="does not contain any form fields"):
        stamp_fields(pdf, [stamp_pdf_path, "AnyField"])


def test_stamp_fields_form_iteration_error(stamp_pdf_path):
    """Test AcroForm failure handling when pikepdf Form parsing throws an exception."""
    import pikepdf

    pdf = pikepdf.new()
    pdf.add_blank_page()

    with patch("pikepdf.form.Form", side_effect=pikepdf.PdfError("Simulated AcroForm error")):
        with pytest.raises(InvalidArgumentError, match="does not contain any form fields"):
            stamp_fields(pdf, [stamp_pdf_path, "AnyField"])


def test_stamp_fields_field_not_found(form_pdf, stamp_pdf_path):
    with pytest.raises(InvalidArgumentError, match="not found in the document"):
        stamp_fields(form_pdf, [stamp_pdf_path, "MissingField"])


def test_stamp_fields_no_widgets(form_pdf, stamp_pdf_path):
    # Remove the /Rect from the child widget to simulate a headless field
    child_field = form_pdf.Root.AcroForm.Fields[0].Kids[0]
    if "/Rect" in child_field:
        del child_field["/Rect"]
    with pytest.raises(InvalidArgumentError, match="has no visual widgets"):
        stamp_fields(form_pdf, [stamp_pdf_path, "Parent.Child"])


def test_stamp_fields_invalid_scale_mode(form_pdf, stamp_pdf_path):
    with pytest.raises(InvalidArgumentError, match="Invalid scale"):
        stamp_fields(form_pdf, [stamp_pdf_path, "Parent.Child", "scale=magic"])


def test_stamp_fields_invalid_scale_negative(form_pdf, stamp_pdf_path):
    with pytest.raises(InvalidArgumentError, match="scale"):
        stamp_fields(form_pdf, [stamp_pdf_path, "Parent.Child", "scale=-1"])


def test_stamp_fields_invalid_scale_str(form_pdf, stamp_pdf_path):
    with pytest.raises(InvalidArgumentError, match="scale"):
        stamp_fields(form_pdf, [stamp_pdf_path, "Parent.Child", "scale=one"])


def test_stamp_fields_invalid_opacity_high(form_pdf, stamp_pdf_path):
    with pytest.raises(InvalidArgumentError, match="between 0.0 and 1.0"):
        stamp_fields(form_pdf, [stamp_pdf_path, "Parent.Child", "opacity=5.0"])


def test_stamp_fields_invalid_opacity_str(form_pdf, stamp_pdf_path):
    with pytest.raises(InvalidArgumentError, match="between 0.0 and 1.0"):
        stamp_fields(form_pdf, [stamp_pdf_path, "Parent.Child", "opacity=opaque"])


def test_stamp_fields_missing_source(form_pdf):
    with pytest.raises(InvalidArgumentError, match="Could not open source PDF"):
        stamp_fields(form_pdf, ["does_not_exist_at_all.pdf", "Parent.Child"])


def test_stamp_fields_empty_source(form_pdf, tmp_path):
    import pikepdf

    empty_path = tmp_path / "empty.pdf"
    pikepdf.new().save(empty_path)
    with pytest.raises(InvalidArgumentError, match="has no pages"):
        stamp_fields(form_pdf, [str(empty_path), "Parent.Child"])


def test_get_field_widgets_cycle():
    """Verifies that widget tree traversal correctly blocks infinite cyclical recursion."""
    import pikepdf

    pdf = pikepdf.new()
    pdf.add_blank_page()

    node1 = pikepdf.Dictionary(Kids=pikepdf.Array())
    node1_obj = pdf.make_indirect(node1)

    # Node 2 must NOT have /Rect, otherwise traversal stops before the cycle is hit.
    node2 = pikepdf.Dictionary(Kids=pikepdf.Array([node1_obj]))
    node2_obj = pdf.make_indirect(node2)

    node1_obj.Kids.append(node2_obj)

    # Walk the tree (node1 -> node2 -> node1(blocked))
    widgets = _get_field_widgets(node1_obj, pikepdf)

    assert widgets == []
