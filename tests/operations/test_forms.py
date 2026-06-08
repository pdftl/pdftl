from types import SimpleNamespace

import pikepdf
import pytest

from pdftl.operations.dump_data_fields import (
    _get_field_type_strings,
    dump_data_fields,
    dump_fields_cli_hook,
)
from pdftl.operations.fill_form import fill_form


@pytest.fixture
def pdf_with_form():
    """Create a PDF with a valid AcroForm structure using Indirect Objects."""
    pdf = pikepdf.new()
    pdf.add_blank_page()

    # 1. Initialize AcroForm
    pdf.Root.AcroForm = pikepdf.Dictionary(
        Fields=pikepdf.Array(),
        DA=pikepdf.String("/Helv 0 Tf 0 g"),
        NeedAppearances=True,
    )

    # 2. Create a Text Field
    text_field_dict = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        FT=pikepdf.Name.Tx,
        T=pikepdf.String("MyTextField"),
        V=pikepdf.String("OriginalValue"),
        Rect=[0, 0, 100, 20],
        Ff=0,
    )

    # Make the field an INDIRECT object.
    # pikepdf requires fields to have an Object ID (e.g. "10 0 R")
    indirect_field = pdf.make_indirect(text_field_dict)

    # 3. Add the Indirect Object to Fields and Page Annots
    pdf.Root.AcroForm.Fields.append(indirect_field)

    # Page Annots also usually expect indirect references
    pdf.pages[0].Annots = pdf.make_indirect([indirect_field])

    return pdf


@pytest.fixture
def fdf_file(tmp_path):
    """Creates a valid FDF file to fill 'MyTextField'."""
    fdf = pikepdf.new()
    fdf.Root.FDF = pikepdf.Dictionary(Fields=pikepdf.Array())

    field_data = pikepdf.Dictionary(
        T=pikepdf.String("MyTextField"), V=pikepdf.String("FilledValue")
    )
    fdf.Root.FDF.Fields.append(field_data)

    out_path = tmp_path / "data.fdf"
    fdf.save(out_path)
    return str(out_path)


def test_dump_data_fields(pdf_with_form, tmp_path):
    """Test dumping form fields to a file."""
    output = tmp_path / "fields.txt"
    result = dump_data_fields(pdf_with_form, output_file=str(output))
    mock_stage = SimpleNamespace(options={"output": str(output), "escape_xml": True})
    dump_fields_cli_hook(result, mock_stage, None)

    content = output.read_text(encoding="utf-8")
    assert "FieldName: MyTextField" in content
    assert "FieldValue: OriginalValue" in content


def test_dump_data_fields_stdout(pdf_with_form):
    """Test dumping form fields to stdout."""
    import io
    from unittest.mock import patch

    with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        result = dump_data_fields(pdf_with_form, output_file=None)
        mock_stage = SimpleNamespace(options={"output_file": None, "escape_xml": True})
        dump_fields_cli_hook(result, mock_stage, None)
        content = mock_stdout.getvalue()

    assert "FieldName: MyTextField" in content


def test_fill_form_basic(pdf_with_form, fdf_file):
    """Test filling a form with FDF data."""
    args = [fdf_file]

    def mock_input(msg, **kwargs):
        return None

    fill_form(pdf_with_form, args, mock_input)

    # Verify update by checking the raw object (simplest/safest way)
    # The field is the first (and only) one in our fixture
    field = pdf_with_form.Root.AcroForm.Fields[0]
    assert str(field.V) == "FilledValue"


def test_fill_form_missing_args(pdf_with_form):
    from pdftl.exceptions import UserCommandLineError

    def mock_get_input(msg, **kwargs):
        return "dummy.fdf"

    with pytest.raises(UserCommandLineError):
        fill_form(pdf_with_form, [], mock_get_input)


# --- merged from test_forms_coverage.py ---

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
