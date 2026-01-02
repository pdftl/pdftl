import pikepdf
import pytest

from pdftl.commands.dump_data_fields import _get_field_type_strings, dump_data_fields

# --- Fixture for Complex Form (Multiple Types) ---


@pytest.fixture
def complex_form_pdf():
    """
    Creates a PDF with:
    1. Text Field with Justification (Q=1)
    2. Checkbox (Btn) with /V as Name and /AS
    3. Choice (Ch) with simple string Options
    4. Choice (Ch) with Export/Display pair Options
    5. Pushbutton (Btn with Flag) to trigger 'Button' type detection
    """
    pdf = pikepdf.new()
    pdf.add_blank_page()

    pdf.Root.AcroForm = pikepdf.Dictionary(
        Fields=pikepdf.Array(),
        DA=pikepdf.String("/Helv 0 Tf 0 g"),
        NeedAppearances=True,
    )

    # 1. Centered Text Field (Tests /Q and Separator)
    f1 = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        FT=pikepdf.Name.Tx,
        T=pikepdf.String("TextCentered"),
        V=pikepdf.String("Value1"),
        Rect=[0, 0, 100, 20],
        Q=1,  # Center Justification
    )

    # 2. Checkbox (Tests /Btn, /V as Name, /AS)
    f2 = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        FT=pikepdf.Name.Btn,
        T=pikepdf.String("MyCheckbox"),
        V=pikepdf.Name.Yes,
        AS=pikepdf.Name.Yes,
        Rect=[0, 50, 20, 70],
    )

    # 3. Simple Choice (Tests simple /Opt)
    f3 = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        FT=pikepdf.Name.Ch,
        T=pikepdf.String("SimpleChoice"),
        V=pikepdf.String("Option1"),
        Opt=[pikepdf.String("Option1"), pikepdf.String("Option2")],
        Rect=[0, 100, 100, 120],
    )

    # 4. Complex Choice (Tests Export/Display /Opt)
    # Opt is [[Export, Display], [Export, Display]]
    f4 = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        FT=pikepdf.Name.Ch,
        T=pikepdf.String("ComplexChoice"),
        Opt=[
            [pikepdf.String("exp1"), pikepdf.String("Display One")],
            [pikepdf.String("exp2"), pikepdf.String("Display Two")],
        ],
        Rect=[0, 150, 100, 170],
    )

    # 5. Pushbutton (Tests explicit "Button" type string detection)
    # Requires Flag 65536 (bit 17) to be identified as PushbuttonField by pikepdf
    f5 = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        FT=pikepdf.Name.Btn,
        T=pikepdf.String("MyPushbutton"),
        Ff=65536,
        Rect=[0, 200, 50, 250],
    )

    # Register all fields (Indirect Objects)
    for f in [f1, f2, f3, f4, f5]:
        ind = pdf.make_indirect(f)
        pdf.Root.AcroForm.Fields.append(ind)
        if "/Annots" not in pdf.pages[0]:
            pdf.pages[0].Annots = pdf.make_indirect([])
        pdf.pages[0].Annots.append(ind)

    return pdf


# --- Tests ---


def test_dump_complex_attributes(complex_form_pdf, assert_dump_output):
    """
    Tests:
    - Multiple fields separator ('---')
    - Button type detection
    - /Q Justification
    - /V as Name object (Checkbox)
    - /Opt (simple and array)
    """
    expected = [
        "---",
        "FieldType: Button",
        "FieldName: MyCheckbox",
        "FieldJustification: Center",
        "FieldValue: Yes",
        "FieldStateOption: exp1",
    ]
    assert_dump_output(dump_data_fields, complex_form_pdf, expected)


def test_dump_no_escape_xml(complex_form_pdf, assert_dump_output):
    """Tests escape_xml=False branch."""
    assert_dump_output(
        dump_data_fields, complex_form_pdf, "FieldName: TextCentered", escape_xml=False
    )


def test_dump_extra_info(complex_form_pdf, assert_dump_output):
    """Tests extra_info=True branch."""
    assert_dump_output(dump_data_fields, complex_form_pdf, "FieldSubType:", extra_info=True)


def test_dump_fallback_values(complex_form_pdf, assert_dump_output):
    """
    Tests fallback to /AS if /V is missing (common in some checkboxes).
    """
    # Modify the checkbox to have AS but no V
    # complex_form_pdf.Root.AcroForm.Fields[1] is the checkbox
    checkbox = complex_form_pdf.Root.AcroForm.Fields[1]
    del checkbox["/V"]
    expected = ["FieldName: MyCheckbox", "FieldValue: Yes"]
    assert_dump_output(dump_data_fields, complex_form_pdf, expected)


def test_unknown_field_type():
    """
    Tests that unknown field types are handled gracefully (fallback to class name).
    """

    class WeirdThing:
        pass

    weird_field = WeirdThing()

    # New behavior: No error raised. It returns the class name as the default.
    type_in, type_out = _get_field_type_strings(weird_field)

    assert type_in == "WeirdThing"
    assert type_out == "WeirdThing"
