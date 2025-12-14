from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.commands.generate_fdf import generate_fdf


@pytest.fixture
def fdf_source_pdf():
    """Creates a PDF with various form fields."""
    pdf = pikepdf.new()
    pdf.add_blank_page()

    pdf.Root.AcroForm = pikepdf.Dictionary(
        Fields=pikepdf.Array(),
        DA=pikepdf.String("/Helv 0 Tf 0 g"),
        NeedAppearances=True,
    )

    # 1. Text Field
    f1 = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        FT=pikepdf.Name.Tx,
        T=pikepdf.String("MyText"),
        V=pikepdf.String("Hello World"),
        Rect=[0, 0, 100, 20],
    )

    # 2. Radio Button Group
    f2 = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        FT=pikepdf.Name.Btn,
        T=pikepdf.String("MyRadio"),
        Ff=32768,  # Radio
        V=pikepdf.Name("/1"),
        Opt=[pikepdf.String("OptionA"), pikepdf.String("OptionB")],
        Rect=[0, 50, 100, 70],
    )

    # 3. Choice Field (No Value)
    f3 = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        FT=pikepdf.Name.Ch,
        T=pikepdf.String("MyChoice"),
        Opt=[pikepdf.String("Red"), pikepdf.String("Blue")],
        Rect=[0, 100, 100, 120],
    )

    # Add Indirect Objects
    for f in [f1, f2, f3]:
        ind = pdf.make_indirect(f)
        pdf.Root.AcroForm.Fields.append(ind)
        if "/Annots" not in pdf.pages[0]:
            pdf.pages[0].Annots = pdf.make_indirect([])
        pdf.pages[0].Annots.append(ind)

    return pdf


def test_generate_fdf_structure(fdf_source_pdf, tmp_path):
    """Test that generated FDF contains correct keys and values."""
    output = tmp_path / "out.fdf"

    generate_fdf(fdf_source_pdf, lambda x: x, str(output))

    # Read as bytes because FDF headers are binary
    content = output.read_bytes()

    assert b"%FDF-1.2" in content
    assert b"/T (MyText)" in content
    assert b"/V (Hello World)" in content
    assert b"/T (MyRadio)" in content
    assert b"/V (OptionB)" in content
    # Check for presence of MyChoice
    assert b"/T (MyChoice)" in content


def test_generate_fdf_prompt(fdf_source_pdf, tmp_path):
    """Test the PROMPT logic."""
    output = tmp_path / "prompted.fdf"

    def mock_input(msg, **kwargs):
        return str(output)

    generate_fdf(fdf_source_pdf, mock_input, "PROMPT")

    assert output.exists()


def test_generate_fdf_binary_string(fdf_source_pdf, tmp_path):
    """Test handling of binary strings that fail str() conversion (Lines 99-102)."""

    # Define a class that behaves like a String but fails conversion
    class FailingString:
        def __str__(self):
            raise ValueError("Binary data")

        def unparse(self):
            return "<BINARY>"

    # 1. Patch 'String' in the module so `isinstance(val, String)` returns True
    # 2. Patch 'Form' to return our FailingString object as a field value
    with patch("pikepdf.String", FailingString):

        mock_field = MagicMock()
        mock_field.value = FailingString()

        with patch("pikepdf.form.Form") as MockForm:
            # Mock form iteration to yield our problematic field
            MockForm.return_value.items.return_value = [("BinaryField", mock_field)]

            output = tmp_path / "binary.fdf"

            # Pass None as input_pdf because we mocked Form(pdf)
            generate_fdf(None, None, str(output))

            content = output.read_bytes()
            # Verify it fell back to unparse()
            assert b"/V <BINARY>" in content
