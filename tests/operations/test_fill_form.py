# tests/operations/test_fill_form.py

import io
from unittest.mock import MagicMock, patch

import pikepdf
import pytest
from pikepdf.form import Form

from pdftl.exceptions import UserCommandLineError
from pdftl.operations.fill_form import _fill_form_from_fdf_data, _set_form_field_value, fill_form


@pytest.fixture
def pdf():
    """Create a basic PDF with an initialized AcroForm."""
    p = pikepdf.new()
    p.add_blank_page()
    p.Root.AcroForm = pikepdf.Dictionary(
        Fields=pikepdf.Array(),
        DA=pikepdf.String("/Helv 0 Tf 0 g"),
        NeedAppearances=True,
    )
    return p


def test_fill_form_prompt(pdf, tmp_path):
    """Hits lines 61 & 66: Testing fallback to PROMPT when args are empty."""
    fdf_path = tmp_path / "data.fdf"
    fdf = pikepdf.new()
    fdf.Root.FDF = pikepdf.Dictionary(Fields=[])
    fdf.save(fdf_path)

    with patch("pdftl.operations.fill_form.smart_open", return_value=open(fdf_path, "rb")):
        # Lambda mimics user typing the correct path during get_input prompt
        fill_form(pdf, [], lambda msg, **kw: str(fdf_path))


def test_fill_form_os_error(pdf):
    """Hits line 74: Validates OSError fallback wrapping."""
    with patch("pdftl.operations.fill_form.smart_open", side_effect=OSError("Permission denied")):
        with pytest.raises(UserCommandLineError, match="Permission denied"):
            fill_form(pdf, ["dummy.fdf"], None)


def test_fill_form_xfdf_success(pdf, tmp_path):
    """Hits lines 158-207: Test filling a form from valid XFDF data structure."""
    xfdf_data = b"""<?xml version="1.0" encoding="UTF-8"?>
    <xfdf xmlns="http://ns.adobe.com/xfdf/" xml:space="preserve">
        <fields>
            <field name="MyText">
                <value>XFDF Hello</value>
            </field>
            <field name="MyCheckbox">
                <value>Yes</value>
            </field>
            <field name="Parent">
                <field name="Child">
                    <value>Nested Value</value>
                </field>
            </field>
        </fields>
    </xfdf>"""

    xfdf_path = tmp_path / "data.xfdf"
    xfdf_path.write_bytes(xfdf_data)

    # Add fields to PDF
    f1 = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        FT=pikepdf.Name.Tx,
        T=pikepdf.String("MyText"),
        V=pikepdf.String(""),
        Rect=[0, 0, 100, 20],
    )
    f2 = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        FT=pikepdf.Name.Btn,
        T=pikepdf.String("MyCheckbox"),
        V=pikepdf.Name.Off,
        Rect=[0, 30, 100, 50],
    )
    f3_child = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        FT=pikepdf.Name.Tx,
        T=pikepdf.String("Child"),
        V=pikepdf.String(""),
        Rect=[0, 60, 100, 80],
    )
    f3_child_obj = pdf.make_indirect(f3_child)
    f3_parent = pikepdf.Dictionary(
        T=pikepdf.String("Parent"), Kids=[f3_child_obj], FT=pikepdf.Name.Tx
    )
    f3_parent_obj = pdf.make_indirect(f3_parent)
    f3_child_obj.Parent = f3_parent_obj

    pdf.Root.AcroForm.Fields.extend([pdf.make_indirect(f1), pdf.make_indirect(f2), f3_parent_obj])

    # We need to force FDF to fail so XFDF is tried. `_fill_form_from_fdf_data` fails natively if data is XML.
    fill_form(pdf, [str(xfdf_path)], None)

    from pikepdf.form import Form

    form = Form(pdf)
    assert form["MyText"].value == "XFDF Hello"
    assert (
        str(form["MyCheckbox"].obj.V) == "/Yes"
    )  # _set_checkbox_value fallback sets /Yes via AttributeError
    assert form["Parent.Child"].value == "Nested Value"


def test_fill_form_xfdf_fallback(pdf):
    """
    Test that if FDF parsing fails, it tries XFDF
    and eventually raises UserCommandLineError.
    """
    with patch("pdftl.operations.fill_form._fill_form_from_fdf_data") as mock_fdf:
        mock_fdf.side_effect = ValueError("Not FDF")

        with patch("builtins.open", new_callable=MagicMock) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = b"JUNK"

            with pytest.raises(UserCommandLineError, match="Errors encountered"):
                fill_form(pdf, ["dummy.xfdf"], lambda x: x)


def test_fill_form_recursion_kids(pdf, tmp_path):
    """Test FDF with nested 'Kids' (recursion logic)."""
    child = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        FT=pikepdf.Name.Tx,
        T=pikepdf.String("Child"),
        V=pikepdf.String("OldValue"),
        Rect=[0, 0, 50, 50],
    )
    indirect_child = pdf.make_indirect(child)

    parent = pikepdf.Dictionary(
        T=pikepdf.String("Parent"), Kids=[indirect_child], FT=pikepdf.Name.Tx
    )
    indirect_parent = pdf.make_indirect(parent)
    indirect_child.Parent = indirect_parent

    pdf.Root.AcroForm.Fields.append(indirect_parent)
    pdf.pages[0].Annots = pdf.make_indirect([indirect_child])

    fdf = pikepdf.new()
    fdf_parent = pikepdf.Dictionary(
        T=pikepdf.String("Parent"),
        Kids=[pikepdf.Dictionary(T=pikepdf.String("Child"), V=pikepdf.String("NewValue"))],
    )
    fdf.Root.FDF = pikepdf.Dictionary(Fields=[fdf_parent])
    fdf_path = tmp_path / "kids.fdf"
    fdf.save(fdf_path)

    fill_form(pdf, [str(fdf_path)], None)

    updated_child = pdf.Root.AcroForm.Fields[0].Kids[0]
    assert str(updated_child.V) == "NewValue"


def test_fill_form_radio_button_index(pdf, tmp_path):
    """Test setting RadioButton value by index using a valid Group structure."""

    # Use pdf.make_stream() instead of pikepdf.Stream()
    opt1 = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        AS=pikepdf.Name.Off,
        Rect=[10, 10, 20, 20],
        AP=pikepdf.Dictionary(
            N=pikepdf.Dictionary(ChoiceA=pdf.make_stream(b""), Off=pdf.make_stream(b""))
        ),
    )
    opt2 = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        AS=pikepdf.Name.Off,
        Rect=[30, 10, 40, 20],
        AP=pikepdf.Dictionary(
            N=pikepdf.Dictionary(ChoiceB=pdf.make_stream(b""), Off=pdf.make_stream(b""))
        ),
    )
    ind_opt1 = pdf.make_indirect(opt1)
    ind_opt2 = pdf.make_indirect(opt2)

    radio_group = pikepdf.Dictionary(
        FT=pikepdf.Name.Btn,
        Ff=32768,
        T=pikepdf.String("MyRadio"),
        V=pikepdf.Name.Off,
        Opt=[pikepdf.String("ChoiceA"), pikepdf.String("ChoiceB")],
        Kids=[ind_opt1, ind_opt2],
    )
    ind_group = pdf.make_indirect(radio_group)

    ind_opt1.Parent = ind_group
    ind_opt2.Parent = ind_group

    pdf.Root.AcroForm.Fields.append(ind_group)
    pdf.pages[0].Annots = pdf.make_indirect([ind_opt1, ind_opt2])

    fdf = pikepdf.new()
    fdf.Root.FDF = pikepdf.Dictionary(
        Fields=[pikepdf.Dictionary(T=pikepdf.String("MyRadio"), V=pikepdf.String("ChoiceB"))]
    )
    fdf_path = tmp_path / "radio.fdf"
    fdf.save(fdf_path)

    fill_form(pdf, [str(fdf_path)], None)

    assert str(pdf.Root.AcroForm.Fields[0].V) == "/1"


# Bit 16 (Radio) + Bit 15 (NoToggleToOff)
# We need NoToggleToOff to force pikepdf to raise ValueError when we try to set "/Off",
# which ensures our "except ValueError" block is actually tested.
RADIO_FLAG = 32768 | 16384


@pytest.fixture
def radio_no_kids(tmp_path):
    """
    Creates a standalone Radio Button field (No /Kids array).
    """
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()

    obj = pdf.make_indirect(
        pikepdf.Dictionary(
            FT=pikepdf.Name.Btn,
            Ff=RADIO_FLAG,
            T="RadioNoKids",
            V=pikepdf.Name.Yes,
            AS=pikepdf.Name.Yes,
            AP=pikepdf.Dictionary(N=pikepdf.Dictionary(Yes=pikepdf.Stream(pdf, b""))),
        )
    )

    pdf.pages[0].Annots = pdf.make_indirect(pikepdf.Array([obj]))
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([obj]))

    form_field = Form(pdf)["RadioNoKids"]
    return form_field, obj


@pytest.fixture
def radio_with_kids(tmp_path):
    """
    Creates a standard Radio Button Group with Kids using Indirect Objects.
    """
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()

    parent = pdf.make_indirect(
        pikepdf.Dictionary(
            FT=pikepdf.Name.Btn, Ff=RADIO_FLAG, T="RadioGroup", V=pikepdf.Name.Choice1, Kids=[]
        )
    )

    c1 = pdf.make_indirect(
        pikepdf.Dictionary(
            Parent=parent,
            AS=pikepdf.Name.Choice1,
            AP=pikepdf.Dictionary(N=pikepdf.Dictionary(Choice1=pikepdf.Stream(pdf, b""))),
        )
    )
    c2 = pdf.make_indirect(
        pikepdf.Dictionary(
            Parent=parent,
            AS=pikepdf.Name.Off,
            AP=pikepdf.Dictionary(N=pikepdf.Dictionary(Choice2=pikepdf.Stream(pdf, b""))),
        )
    )

    parent.Kids = pikepdf.Array([c1, c2])

    pdf.pages[0].Annots = pdf.make_indirect(pikepdf.Array([c1, c2]))
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([parent]))

    form_field = Form(pdf)["RadioGroup"]
    return form_field, parent


def test_radio_no_kids_clear_off(radio_no_kids):
    field, obj = radio_no_kids
    _set_form_field_value(field, "/Off")
    assert "/V" not in obj
    assert "/AS" not in obj


def test_radio_no_kids_set_value(radio_no_kids):
    field, obj = radio_no_kids
    _set_form_field_value(field, "/NewVal")
    assert str(obj.V) == "/NewVal"


def test_radio_with_kids_exception_handling(radio_with_kids):
    """
    Tests that when pikepdf raises ValueError for unchecking (due to NoToggleToOff),
    we catch it and manually clear the field.
    """
    field, obj = radio_with_kids
    _set_form_field_value(field, "/Off")
    assert "/V" not in obj


def test_radio_exception_reraise_real_error():
    """
    Coverage for Line 258: ensure generic ValueErrors are re-raised.
    Uses a Dummy class to force a ValueError reliably without relying on
    pikepdf internal validation logic.
    """

    class DummyField:
        is_text = False
        is_checkbox = False
        is_radio_button = True
        # Emulate having kids so we hit the try/except block
        obj = {"/Kids": ["fake_kid"]}
        fully_qualified_name = "DummyField"

        @property
        def value(self):
            return None

        @value.setter
        def value(self, val):
            # This simulates a "real" error (e.g. corruption, weird state)
            # that is NOT the "uncheck a radio button" error.
            raise ValueError("Some catastrophic failure")

    field = DummyField()

    # We expect the function to try setting value, catch the error,
    # realize it's not the "uncheck" error, and re-raise it.
    with pytest.raises(ValueError, match="Some catastrophic failure"):
        _set_form_field_value(field, "/AnyValue")


def test_fill_form_fdf_fields_not_array():
    """Hits line 113: FDF /Fields is a Dictionary instead of an Array."""
    # Create a dummy FDF-structured PDF
    with pikepdf.new() as fdf:
        # Create the /FDF entry in the Root
        fdf.Root.FDF = pikepdf.Dictionary(
            Fields=pikepdf.Dictionary(T="NotAnArray")  # Malformed: should be [ ... ]
        )

        # Get the raw bytes
        out = io.BytesIO()
        fdf.save(out)
        malformed_data = out.getvalue()

    # We need a dummy form object to satisfy the first argument
    with pikepdf.new() as target_pdf:
        from pikepdf.form import Form

        form_proxy = Form(target_pdf)

        with pytest.raises(UserCommandLineError, match="FDF fields is not an array"):
            _fill_form_from_fdf_data(form_proxy, malformed_data)


# --- EDGE CASE FAILURES ---


def test_fill_form_skip_signature_field(pdf):
    """Ensures filling is skipped entirely for cryptographically sensitive Signature fields."""
    sig_dict = pikepdf.Dictionary(
        FT=pikepdf.Name.Sig, T=pikepdf.String("MySignature"), V=pikepdf.Dictionary()
    )
    sig_obj = pdf.make_indirect(sig_dict)
    pdf.Root.AcroForm.Fields.append(sig_obj)

    form_field = Form(pdf)["MySignature"]

    # Attempting to assign string value to Signature field should skip (log warning) and leave unchanged
    _set_form_field_value(form_field, "HackedSignatureValue")
    assert isinstance(form_field.obj.V, pikepdf.Dictionary)


def test_fill_form_multi_select_choice_array(pdf):
    """Ensures choice fields accept lists/arrays for multi-selection."""
    choice_dict = pikepdf.Dictionary(
        FT=pikepdf.Name.Ch,
        Ff=1 << 21,  # MultiSelect flag (bit 22)
        T=pikepdf.String("Languages"),
        Opt=pikepdf.Array(
            [pikepdf.String("English"), pikepdf.String("Spanish"), pikepdf.String("German")]
        ),
    )
    choice_obj = pdf.make_indirect(choice_dict)
    pdf.Root.AcroForm.Fields.append(choice_obj)

    form_field = Form(pdf)["Languages"]

    # Set value using raw list
    _set_form_field_value(form_field, ["Spanish", "German"])

    # Check field.obj.V directly since field.value getter might fail
    assert len(form_field.obj.V) == 2
    assert str(form_field.obj.V[0]) == "Spanish"
    assert str(form_field.obj.V[1]) == "German"


def test_fill_form_single_choice_fallback(pdf):
    """Hits line 240: Standard fallback for Choice fields with single selection."""
    choice_dict = pikepdf.Dictionary(
        FT=pikepdf.Name.Ch,
        T=pikepdf.String("SingleChoice"),
        Opt=pikepdf.Array([pikepdf.String("Option1"), pikepdf.String("Option2")]),
    )
    choice_obj = pdf.make_indirect(choice_dict)
    pdf.Root.AcroForm.Fields.append(choice_obj)

    form_field = Form(pdf)["SingleChoice"]

    # Pass a single string, which falls through to the `else` block
    _set_form_field_value(form_field, "Option2")

    assert str(form_field.value) == "Option2"


def test_radio_no_kids_missing_slash(radio_no_kids):
    """Hits line 267: Adds missing slash to radio button value."""
    field, obj = radio_no_kids

    # Pass value without slash
    _set_form_field_value(field, "NewValNoSlash")

    # It should prepend the slash automatically
    assert str(obj.V) == "/NewValNoSlash"
