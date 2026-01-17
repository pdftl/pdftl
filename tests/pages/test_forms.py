# tests/pages/test_forms.py

import logging

import pikepdf
import pytest

from pdftl.pages.forms import handle_page_widgets, rebuild_acroform_index

# --- Fixtures ---


@pytest.fixture
def clean_pdf():
    """Returns a fresh in-memory PDF."""
    return pikepdf.Pdf.new()


@pytest.fixture
def simple_widget_page(clean_pdf):
    """Returns a page with a single simple text widget."""
    pdf = clean_pdf
    pdf.add_blank_page()
    page = pdf.pages[0]

    # Create a simple text widget
    widget = pikepdf.Dictionary(
        Type=pikepdf.Name.Widget,
        Subtype=pikepdf.Name.Widget,
        T="MyTextField",
        Rect=[0, 0, 100, 100],
    )

    # Annots must be an array
    page.Annots = pdf.make_indirect(pikepdf.Array([widget]))
    return pdf, page, widget


@pytest.fixture
def complex_radio_page(clean_pdf):
    """
    Returns a page with two radio buttons sharing a common Parent.
    This simulates a Radio Group.
    """
    pdf = clean_pdf
    pdf.add_blank_page()
    page = pdf.pages[0]

    # 1. Create the Parent Field (Indirect Object)
    parent = pikepdf.Dictionary(T="MyRadioGroup", FT=pikepdf.Name.Btn, V=pikepdf.Name.Yes)
    parent_obj = pdf.make_indirect(parent)

    # 2. Create Child Widgets
    # Child 1
    w1 = pikepdf.Dictionary(Type=pikepdf.Name.Widget, Parent=parent_obj, AS=pikepdf.Name.Yes)
    # Child 2
    w2 = pikepdf.Dictionary(Type=pikepdf.Name.Widget, Parent=parent_obj, AS=pikepdf.Name.Off)

    # 3. Add to page
    page.Annots = pdf.make_indirect(pikepdf.Array([w1, w2]))

    return pdf, page, parent_obj


# --- Tests for handle_page_widgets ---


def test_handle_widgets_no_annots(clean_pdf):
    """Test graceful exit if page has no annotations."""
    clean_pdf.add_blank_page()
    page = clean_pdf.pages[0]

    # Should not raise error
    handle_page_widgets(clean_pdf, page, None, 1)
    assert "/Annots" not in page


def test_handle_widgets_ignore_non_widgets(clean_pdf):
    """Test that non-widget annotations (e.g. Links) are ignored."""
    clean_pdf.add_blank_page()
    page = clean_pdf.pages[0]

    link_annot = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link, Rect=[0, 0, 10, 10]
    )
    page.Annots = clean_pdf.make_indirect(pikepdf.Array([link_annot]))

    # Run with instance > 0
    handle_page_widgets(clean_pdf, page, None, 1)

    # Should remain unchanged
    assert page.Annots[0].Subtype == "/Link"
    assert "/T" not in page.Annots[0]


def test_handle_widgets_instance_zero(simple_widget_page):
    """Test that instance_num=0 (original page) does NOT rename widgets."""
    pdf, page, widget = simple_widget_page
    original_name = str(widget.T)

    handle_page_widgets(pdf, page, None, 0)

    assert str(page.Annots[0].T) == original_name


def test_handle_widgets_rename_simple(simple_widget_page):
    """Test renaming of a simple standalone widget."""
    pdf, page, widget = simple_widget_page

    # Simulate duplication (instance_num=1)
    handle_page_widgets(pdf, page, None, 1)

    expected_name = "MyTextField_1"
    assert str(page.Annots[0].T) == expected_name


def test_handle_widgets_clone_parent_radio_group(complex_radio_page):
    """
    Test deep cloning of Parent objects for radio groups.
    Ensures siblings on the same duplicate page share the SAME new parent.
    """
    pdf, page, old_parent = complex_radio_page

    # Sanity check before
    assert page.Annots[0].Parent.objgen == old_parent.objgen
    assert page.Annots[1].Parent.objgen == old_parent.objgen

    # Run duplication logic
    handle_page_widgets(pdf, page, None, 1)

    w1 = page.Annots[0]
    w2 = page.Annots[1]

    # 1. Parents should be renamed
    assert str(w1.Parent.T) == "MyRadioGroup_1"

    # 2. Parents should NOT be the old object ID
    assert w1.Parent.objgen != old_parent.objgen

    # 3. Both siblings should point to the SAME new parent object
    assert w1.Parent.objgen == w2.Parent.objgen

    # 4. The new parent should be an Indirect Object (not inline)
    # objgen returns (obj_id, gen_id), verify obj_id != 0
    assert w1.Parent.objgen[0] != 0


def test_handle_widgets_parent_no_name(clean_pdf, caplog):
    """Test handling of a parent object that has no /T (Name)."""
    pdf = clean_pdf
    pdf.add_blank_page()
    page = pdf.pages[0]

    parent = pdf.make_indirect(pikepdf.Dictionary(FT=pikepdf.Name.Btn))
    widget = pikepdf.Dictionary(Type=pikepdf.Name.Widget, Parent=parent)
    page.Annots = pdf.make_indirect(pikepdf.Array([widget]))

    with caplog.at_level(logging.DEBUG):
        handle_page_widgets(pdf, page, None, 1)

    # Should not crash, parent should remain (logic skips renaming if no /T)
    assert page.Annots[0].Parent.objgen == parent.objgen


# --- Tests for rebuild_acroform_index ---


def test_rebuild_acroform_creates_new(clean_pdf):
    """Test creation of /AcroForm from scratch."""
    pdf = clean_pdf
    pdf.add_blank_page()

    # Add a widget directly
    w = pikepdf.Dictionary(Type=pikepdf.Name.Widget, T="NewField", Rect=[0, 0, 10, 10])
    pdf.pages[0].Annots = pdf.make_indirect(pikepdf.Array([w]))

    rebuild_acroform_index(pdf)

    assert "/AcroForm" in pdf.Root
    assert len(pdf.Root.AcroForm.Fields) == 1
    assert str(pdf.Root.AcroForm.Fields[0].T) == "NewField"


def test_rebuild_acroform_updates_existing(clean_pdf):
    """Test updating an existing /AcroForm dictionary."""
    pdf = clean_pdf
    pdf.add_blank_page()

    # Create existing (empty) AcroForm
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([]), NeedAppearances=False)

    # Add a widget to the page
    w = pikepdf.Dictionary(Type=pikepdf.Name.Widget, T="FieldA")
    pdf.pages[0].Annots = pdf.make_indirect(pikepdf.Array([w]))

    rebuild_acroform_index(pdf)

    assert len(pdf.Root.AcroForm.Fields) == 1
    assert pdf.Root.AcroForm.NeedAppearances == True


def test_rebuild_acroform_deduplicates_parents(complex_radio_page):
    """
    Test that if multiple widgets share a parent, only the PARENT is added
    to the Fields array, and only ONCE.
    """
    pdf, page, parent = complex_radio_page

    rebuild_acroform_index(pdf)

    fields = pdf.Root.AcroForm.Fields
    assert len(fields) == 1
    assert fields[0].objgen == parent.objgen
    assert str(fields[0].T) == "MyRadioGroup"


def test_rebuild_acroform_removes_empty(clean_pdf):
    """Test that /AcroForm is removed if no widgets are found."""
    pdf = clean_pdf
    pdf.add_blank_page()

    # Create a stale AcroForm
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([]))

    rebuild_acroform_index(pdf)

    assert "/AcroForm" not in pdf.Root
