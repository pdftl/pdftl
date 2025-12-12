import pikepdf
import pytest

from pdftl.commands.dump_data_fields import dump_data_fields
from pdftl.commands.fill_form import fill_form


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
    dump_data_fields(pdf_with_form, output_file=str(output))

    content = output.read_text(encoding="utf-8")
    assert "FieldName: MyTextField" in content
    assert "FieldValue: OriginalValue" in content


def test_dump_data_fields_stdout(pdf_with_form):
    """Test dumping form fields to stdout."""
    import io
    from unittest.mock import patch

    with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        dump_data_fields(pdf_with_form, output_file=None)
        content = mock_stdout.getvalue()

    assert "FieldName: MyTextField" in content


def test_fill_form_basic(pdf_with_form, fdf_file):
    """Test filling a form with FDF data."""
    args = [fdf_file]
    mock_input = lambda msg, **kwargs: None

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
