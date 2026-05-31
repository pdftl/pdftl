import io
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

import pikepdf
import pytest
from pikepdf import Name
from pikepdf.form import RadioButtonGroup

from pdftl.core.constants import META_OUTPUT_FILE
from pdftl.core.core_types import OpResult
from pdftl.operations.generate_fdf import (
    _write_field_as_fdf_to_file,
    _write_string_to_binary_file,
    generate_fdf,
    generate_fdf_cli_hook,
)


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
    generate_fdf_cli_hook(result, None, None)

    # Read as bytes because FDF headers are binary
    content = output.read_bytes()

    assert b"%FDF-1.2" in content
    assert b"/T (MyText)" in content
    assert b"/V (Hello World)" in content
    assert b"/T (MyRadio)" in content
    assert b"/V /1" in content
    # Check for presence of MyChoice
    assert b"/T (MyChoice)" in content


def test_generate_fdf_prompt(fdf_source_pdf, tmp_path):
    """Test the PROMPT logic."""
    output = tmp_path / "prompted.fdf"

    def mock_input(msg, **kwargs):
        return str(output)

    result = generate_fdf(fdf_source_pdf, mock_input, "PROMPT")
    generate_fdf_cli_hook(result, None, None)

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
            generate_fdf_cli_hook(result, None, None)

            content = output.read_bytes()
            # Verify it fell back to unparse()
            assert b"/V <BINARY>" in content


def test_generate_fdf_hook_failure():
    """
    Covers line 40: if not result.success: return
    """
    result = OpResult(success=False)

    # Mock smart_open to ensure it is NOT called
    with patch("pdftl.operations.generate_fdf.smart_open") as mock_open:
        generate_fdf_cli_hook(result, "post", None)
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
    assert "/V (999)" in content
    assert "/T (AgeField)" in content


def test_generate_fdf_choice_field_null_value():
    mock_pdf = MagicMock()

    class MockChoiceField:
        def __init__(self):
            self.obj = MagicMock()  # Added to fix AttributeError

    with (
        patch("pikepdf.form.Form") as MockForm,
        patch("pikepdf.form.ChoiceField", MockChoiceField),
    ):
        mock_form_instance = MockForm.return_value
        mock_field = MockChoiceField()
        mock_field.value = None
        mock_field.default_value = None

        # Ensure obj.get("/Subtype") doesn't crash
        mock_field.obj.get.return_value = None

        mock_form_instance.items.return_value = [("MyDropdown", mock_field)]
        _result = generate_fdf(mock_pdf, lambda x: "y", "out.fdf")


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


def test_write_string_utf8_fallback():
    """Covers lines 102-105: Fallback to UTF-8 for non-Latin-1 chars."""
    buffer = io.BytesIO()
    # 'π' (Pi) cannot be encoded in Latin-1
    test_str = "Value: π"
    _write_string_to_binary_file(test_str, buffer)

    # Verify it was written as UTF-8
    assert buffer.getvalue() == test_str.encode("utf-8")


def test_radio_group_off_state():
    """Covers line 130: RadioButtonGroup with no value and no merged widget."""
    buffer = io.BytesIO()

    # Create a mock that mimics a RadioButtonGroup
    field = MagicMock(spec=RadioButtonGroup)
    field.value = None
    field.default_value = None
    # FIX: Use string keys for pikepdf.Dictionary
    field.obj = pikepdf.Dictionary({"/Subtype": "/Form"})

    _write_field_as_fdf_to_file("Radio1", field, buffer)

    output = buffer.getvalue().decode("utf-8")
    assert "/V /Off" in output


def test_pikepdf_name_vs_string_formatting():
    """Covers line 144-145: Ensures single slash for Names."""
    # Test Name (should result in /Yes, not //Yes)
    name_buffer = io.BytesIO()
    name_field = MagicMock()
    name_field.value = pikepdf.Name("/Yes")

    _write_field_as_fdf_to_file("Toggle", name_field, name_buffer)
    assert b"/V /Yes" in name_buffer.getvalue()
    assert b"//Yes" not in name_buffer.getvalue()


def test_pikepdf_string_happy_path():
    """Covers line 146: Standard pikepdf.String formatting."""
    buffer = io.BytesIO()

    # Use a real pikepdf.String to ensure isinstance(val, String) is True naturally
    str_field = MagicMock()
    str_field.value = pikepdf.String("Hello World")

    _write_field_as_fdf_to_file("TextField", str_field, buffer)

    output = buffer.getvalue()
    # Expect: /T (TextField) /V (Hello World)
    assert b"/V (Hello World)" in output


# --- CLI Hook Tests ---


def test_generate_fdf_cli_hook_stdout():
    """Covers line 48: When output_file is '-', treat it as None (stdout)."""
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.meta = {META_OUTPUT_FILE: "-"}
    mock_result.data = io.BytesIO(b"FDF_DATA")

    with patch("pdftl.operations.generate_fdf.smart_open") as mock_open:
        mock_file_handle = MagicMock()
        mock_open.return_value.__enter__.return_value = mock_file_handle

        generate_fdf_cli_hook(mock_result, None, None)

        # smart_open(None, ...) means stdout in this codebase
        mock_open.assert_called_with(None, mode="wb")
        # Ensure write happened
        assert mock_file_handle.write.call_count > 0


# --- Helper Classes for Type Mocking ---


class FakeString:
    """A minimal class to stand in for pikepdf.String"""

    def __str__(self):
        return "fake"

    def unparse(self):
        return "unparsed"


class FickleMeta(type):
    """
    A metaclass that allows us to script the return value of isinstance().
    Used to reach 'impossible' else blocks.
    """

    answers = []

    def __instancecheck__(cls, instance):
        if cls.answers:
            return cls.answers.pop(0)
        return False


class FickleString(metaclass=FickleMeta):
    pass


class FickleName(metaclass=FickleMeta):
    pass


# --- Complex Logic Tests ---


def test_unparseable_value_fallback():
    """Covers line 146-148: Fallback to unparse() when str() fails."""

    # 1. Define a fake class that mimics the behavior we want.
    # It acts as the "Type" we check against and the "Instance" we use.
    class FakeString:
        def __str__(self):
            # Raise the specific error to trigger the except block
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "fail")

        def unparse(self):
            # Return the fallback value
            return "<FEFF0041>"

    # 2. Patch 'pikepdf.String' with our FakeString class.
    # When the function under test does 'from pikepdf import String',
    # it receives FakeString.
    with patch("pikepdf.String", new=FakeString):
        buffer = io.BytesIO()

        # 3. Create an instance of our fake
        bad_val = FakeString()

        field = MagicMock()
        field.value = bad_val

        # 4. Run the function
        # isinstance(bad_val, FakeString) will be True
        _write_field_as_fdf_to_file("BadField", field, buffer)

    # 5. Verify the fallback output
    assert b"/V <FEFF0041>" in buffer.getvalue()


def test_impossible_else_branch():
    """
    Covers line 151: The defensive 'else' block.
    We trick isinstance() to return True then False for the same object.
    """
    buffer = io.BytesIO()

    # 1. isinstance(val, (String, Name)) -> Checks String first. We want True to ENTER.
    # 2. if isinstance(val, String) -> We want False to skip first block.
    # 3. elif isinstance(val, Name) -> We want False to skip second block.
    # 4. else -> We land here.

    FickleString.answers = [True, False]
    FickleName.answers = [False]

    # Patch the types used in the function with our Fickle types
    with patch("pikepdf.String", new=FickleString), patch("pikepdf.Name", new=FickleName):
        # val can be anything, the metaclass controls the check result
        val = MagicMock()
        val.__str__.return_value = "DefensiveFallback"

        field = MagicMock()
        field.value = val

        _write_field_as_fdf_to_file("GhostField", field, buffer)

        assert b"/V (DefensiveFallback)" in buffer.getvalue()


def test_pikepdf_name_formatting_bug_fix():
    """Verifies the fix for the double-slash bug."""
    buffer = io.BytesIO()
    name_field = MagicMock()

    # Use real pikepdf.Name to ensure standard behavior
    # (Assuming pikepdf is installed in env, otherwise mock similarly to above)
    try:
        real_name = pikepdf.Name("/Yes")
    except (ImportError, AttributeError):
        # Fallback if pikepdf not present in test env, though it should be
        real_name = "/Yes"

    name_field.value = real_name
    _write_field_as_fdf_to_file("Radio", name_field, buffer)

    # Should be /V /Yes, NOT /V //Yes
    assert b"/V /Yes" in buffer.getvalue()
    assert b"//Yes" not in buffer.getvalue()
