from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.commands.generate_fdf import generate_fdf, generate_fdf_cli_hook


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

    result = generate_fdf(fdf_source_pdf, lambda x: x, str(output))
    generate_fdf_cli_hook(result, None)

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

    result = generate_fdf(fdf_source_pdf, mock_input, "PROMPT")
    generate_fdf_cli_hook(result, None)

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
            result = generate_fdf(None, None, str(output))
            generate_fdf_cli_hook(result, None)

            content = output.read_bytes()
            # Verify it fell back to unparse()
            assert b"/V <BINARY>" in content


import io
from types import SimpleNamespace

from pdftl.commands.generate_fdf import _write_field_as_fdf_to_file
from pdftl.core.types import OpResult


def test_generate_fdf_hook_failure():
    """
    Covers line 40: if not result.success: return
    """
    result = OpResult(success=False)

    # Mock smart_open to ensure it is NOT called
    with patch("pdftl.commands.generate_fdf.smart_open_output") as mock_open:
        generate_fdf_cli_hook(result, "post")
        mock_open.assert_not_called()


def test_write_field_non_string_value():
    """
    Covers lines 125-126: elif val is not None: val_as_string = str(val)
    """
    # 1. Mock a field object with an integer value
    mock_field = SimpleNamespace(value=999, default_value=None)

    buffer = io.BytesIO()

    # 2. Call the helper directly
    # Note: The function writes bytes to the file, so we expect bytes in buffer
    # It imports pikepdf types locally, but we don't need to mock them
    # unless 'val' matches them. Here 'val' is int, so it falls through.
    _write_field_as_fdf_to_file("AgeField", mock_field, buffer)

    # 3. Verify output contains the integer converted to string
    content = buffer.getvalue().decode("utf-8")
    assert "/V 999" in content
    assert "/T (AgeField)" in content
