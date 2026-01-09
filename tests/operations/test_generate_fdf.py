from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.operations.generate_fdf import generate_fdf, generate_fdf_cli_hook


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

from pdftl.core.types import OpResult
from pdftl.operations.generate_fdf import _write_field_as_fdf_to_file


def test_generate_fdf_hook_failure():
    """
    Covers line 40: if not result.success: return
    """
    result = OpResult(success=False)

    # Mock smart_open to ensure it is NOT called
    with patch("pdftl.operations.generate_fdf.smart_open") as mock_open:
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


def test_generate_fdf_choice_field_null_value():
    """
    Covers lines 107-108: val = "" if val is None and isinstance(field, ChoiceField)
    """
    mock_pdf = MagicMock()

    class MockChoiceField:
        pass

    with (
        patch("pikepdf.form.Form") as MockForm,
        patch("pikepdf.form.ChoiceField", MockChoiceField),
    ):

        mock_form_instance = MockForm.return_value

        mock_field = MockChoiceField()
        mock_field.value = None
        mock_field.default_value = None

        mock_form_instance.items.return_value = [("MyDropdown", mock_field)]

        # Execute the operation
        result = generate_fdf(mock_pdf, lambda x: "y", "out.fdf")

        assert result.success

        # Verify the raw bytes instead of decoding as utf-8
        # This avoids the UnicodeDecodeError from the FDF header
        output_bytes = result.data.getvalue()

        # Check for the expected FDF field structure in the byte stream
        assert b"/T (MyDropdown)" in output_bytes
        assert b"/V ()" in output_bytes


from unittest.mock import PropertyMock

from pikepdf import Name


def test_generate_fdf_radio_button_exception_handling():
    """Triggers lines 124-128 by forcing an AttributeError on Opt."""
    mock_pdf = MagicMock()

    class MockRB:
        pass  # Dummy for isinstance

    with patch("pikepdf.form.Form") as MockForm, patch("pikepdf.form.RadioButtonGroup", MockRB):

        mock_field = MockRB()
        mock_field.value = Name("/1")
        mock_field.obj = MagicMock()
        # Trigger AttributeError when accessing Opt
        type(mock_field.obj).Opt = PropertyMock(side_effect=AttributeError)

        MockForm.return_value.items.return_value = [("Radio1", mock_field)]

        # This will hit the 'pass' in the except block
        result = generate_fdf(mock_pdf, lambda x: "y", "out.fdf")
        assert result.success


import unittest


class TestFDFFieldEdgeCases(unittest.TestCase):
    def test_radio_button_index_error_handling(self):
        """
        Covers lines 124-128.
        Simulates a RadioButtonGroup where the value is a Name (e.g., /1)
        but the underlying object lacks an 'Opt' array, triggering AttributeError.
        """
        # 1. Setup mocks
        # We need to mock specific classes so isinstance checks pass
        from pikepdf.form import RadioButtonGroup

        mock_field = MagicMock(spec=RadioButtonGroup)
        mock_field.value = Name("/1")

        # This triggers line 124-128:
        # When the code tries to access field.obj.Opt, it raises AttributeError
        mock_field.obj = MagicMock()
        del mock_field.obj.Opt

        buffer = io.BytesIO()

        # 2. Execute the function
        # The try/except block on line 124 will catch the AttributeError and 'pass'
        try:
            _write_field_as_fdf_to_file("RadioTest", mock_field, buffer)
        except AttributeError:
            self.fail("_write_field_as_fdf_to_file failed to catch AttributeError at line 124")

        # 3. Verify output
        # Because it 'passed', it should fall back to using str(val) which is "/1"
        output = buffer.getvalue().decode("utf-8")
        self.assertIn("/V /1", output)
        self.assertIn("/T (RadioTest)", output)


if __name__ == "__main__":
    unittest.main()
