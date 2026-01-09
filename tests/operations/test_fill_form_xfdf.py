import io
from unittest.mock import MagicMock, patch

import pikepdf
import pytest
from pikepdf import Name

from pdftl.operations.fill_form import fill_form

# --- Helpers to construct in-memory PDF forms ---


def create_mock_form_pdf():
    """
    Creates a minimal valid PDF with an AcroForm.
    We must include an /AP (Appearance) dictionary for buttons (Checkbox/Radio)
    because pikepdf checks this to validate allowed states.
    """
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(100, 100))
    page = pdf.pages[0]

    # Create a dummy appearance stream (empty XObject)
    # We just need the keys to exist in the /AP/N dict.
    dummy_ap = pdf.make_indirect(
        {"/Type": Name("/XObject"), "/Subtype": Name("/Form"), "/BBox": [0, 0, 10, 10]}
    )

    # 1. Text Field
    text_field = pdf.make_indirect(
        {
            "/Type": Name("/Annot"),
            "/Subtype": Name("/Widget"),
            "/FT": Name("/Tx"),
            "/T": "MyTextField",
            "/Rect": [10, 10, 50, 20],
        }
    )

    # 2. Checkbox
    # Needs /AP dictionary defining "Yes" and "Off" states
    checkbox = pdf.make_indirect(
        {
            "/Type": Name("/Annot"),
            "/Subtype": Name("/Widget"),
            "/FT": Name("/Btn"),
            "/T": "MyCheckbox",
            "/V": Name("/Off"),
            "/Rect": [10, 30, 30, 50],
            "/AP": {"/N": {"/Yes": dummy_ap, "/Off": dummy_ap}},
        }
    )

    # 3. Radio Button Group
    # Bit 16 (32768) = Radio, Bit 15 (16384) = NoToggleToOff -> 49152
    radio_group = pdf.make_indirect(
        {
            "/Type": Name("/Annot"),
            "/Subtype": Name("/Widget"),
            "/FT": Name("/Btn"),
            "/Ff": 49152,
            "/T": "MyRadioGroup",
            "/V": Name("/Off"),
            "/Rect": [10, 60, 60, 80],
            "/AP": {"/N": {"/Choice1": dummy_ap, "/Off": dummy_ap}},
        }
    )

    # Add fields to Page and AcroForm
    page.Annots = pdf.make_indirect([text_field, checkbox, radio_group])

    pdf.Root.AcroForm = pdf.make_indirect({"/Fields": [text_field, checkbox, radio_group]})

    # Cycle through memory to finalize structure and refs
    out_stream = io.BytesIO()
    pdf.save(out_stream)
    out_stream.seek(0)
    return pikepdf.open(out_stream)


# --- Tests ---


def test_fill_form_xfdf_parsing_and_types():
    """
    Targets:
      - Lines 165-200: _fill_form_from_xfdf_data (XML parsing)
      - Lines 215-216: Checkbox handling (Yes/True/1 mapping)
      - Lines 223-227: Radio Button handling (Name generation)
    """
    pdf = create_mock_form_pdf()

    # Valid XFDF data
    xfdf_content = b"""<?xml version="1.0" encoding="UTF-8"?>
    <xfdf xmlns="http://ns.adobe.com/xfdf/" xml:space="preserve">
      <fields>
        <field name="MyTextField">
          <value>Hello World</value>
        </field>
        <field name="MyCheckbox">
          <value>Yes</value>
        </field>
        <field name="MyRadioGroup">
          <value>Choice1</value>
        </field>
      </fields>
    </xfdf>
    """

    with patch("pdftl.operations.fill_form.smart_open") as mock_open:
        mock_file = MagicMock()
        mock_file.__enter__.return_value.read.return_value = xfdf_content
        mock_open.return_value = mock_file

        # Run operation
        fill_form(pdf, ["dummy.xfdf"])

    # Verify Text Field (Raw PDF access)
    assert str(pdf.Root.AcroForm.Fields[0].V) == "Hello World"

    # Verify Checkbox and Radio using high-level wrapper
    from pikepdf.form import Form

    form = Form(pdf)

    field_cb = next(f for f in form if str(f.obj.get("/T")) == "MyCheckbox")
    assert field_cb.checked is True

    field_radio = next(f for f in form if str(f.obj.get("/T")) == "MyRadioGroup")
    # Verify logic: Stripped slash and converted to Name
    assert str(field_radio.value) == "/Choice1"


def test_fill_form_checkbox_variations():
    """
    Targets line 215 specifically to ensure 'On', 'True', '1' work.
    """
    pdf = create_mock_form_pdf()

    # "On" is mapped to True, which maps to the first non-Off state in /AP (which is /Yes in our mock)
    xfdf_content = b"""<?xml version="1.0"?>
    <xfdf>
      <fields>
        <field name="MyCheckbox"><value>On</value></field>
      </fields>
    </xfdf>
    """

    with patch("pdftl.operations.fill_form.smart_open") as mock_open:
        mock_file = MagicMock()
        mock_file.__enter__.return_value.read.return_value = xfdf_content
        mock_open.return_value = mock_file
        fill_form(pdf, ["dummy.xfdf"])

    from pikepdf.form import Form

    form = Form(pdf)
    field_cb = next(f for f in form if str(f.obj.get("/T")) == "MyCheckbox")

    assert field_cb.checked is True


def test_fill_form_bad_xfdf():
    """
    Ensures that if XFDF parsing fails, we bubble up the error appropriately.
    """
    pdf = create_mock_form_pdf()

    # Malformed XML
    bad_data = b"<xfdf><fields><field name='oops'>Missing closing tags"

    with patch("pdftl.operations.fill_form.smart_open") as mock_open:
        mock_file = MagicMock()
        mock_file.__enter__.return_value.read.return_value = bad_data
        mock_open.return_value = mock_file

        from pdftl.exceptions import UserCommandLineError

        with pytest.raises(UserCommandLineError) as excinfo:
            fill_form(pdf, ["bad.xfdf"])

        # Verify the composite error message
        assert "Invalid XFDF XML" in str(excinfo.value)


def create_simple_mock_pdf():
    """
    A minimal PDF without /AP dictionaries.
    This tests if our code is robust enough to handle "broken" forms.
    """
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(100, 100))
    page = pdf.pages[0]

    # 1. Text Field
    text_field = pdf.make_indirect(
        {
            "/Type": Name("/Annot"),
            "/Subtype": Name("/Widget"),
            "/FT": Name("/Tx"),
            "/T": "MyTextField",
            "/Rect": [10, 10, 50, 20],
        }
    )

    # 2. Checkbox (Missing /AP - will trigger the fallback logic)
    checkbox = pdf.make_indirect(
        {
            "/Type": Name("/Annot"),
            "/Subtype": Name("/Widget"),
            "/FT": Name("/Btn"),
            "/T": "MyCheckbox",
            "/V": Name("/Off"),
            "/Rect": [10, 30, 30, 50],
        }
    )

    # 3. Radio Group
    radio_group = pdf.make_indirect(
        {
            "/Type": Name("/Annot"),
            "/Subtype": Name("/Widget"),
            "/FT": Name("/Btn"),
            "/Ff": 49152,
            "/T": "MyRadioGroup",
            "/V": Name("/Off"),
            "/Rect": [10, 60, 60, 80],
        }
    )

    page.Annots = pdf.make_indirect([text_field, checkbox, radio_group])
    pdf.Root.AcroForm = pdf.make_indirect({"/Fields": [text_field, checkbox, radio_group]})

    out_stream = io.BytesIO()
    pdf.save(out_stream)
    out_stream.seek(0)
    return pikepdf.open(out_stream)


def test_fill_form_robustness_missing_ap():
    """
    Tests that we can fill a checkbox even if the PDF lacks
    the /AP dictionary (which causes pikepdf to crash by default).
    """
    pdf = create_simple_mock_pdf()

    xfdf_content = b"""<?xml version="1.0"?>
    <xfdf>
      <fields>
        <field name="MyCheckbox"><value>On</value></field>
      </fields>
    </xfdf>
    """

    with patch("pdftl.operations.fill_form.smart_open") as mock_open:
        mock_file = MagicMock()
        mock_file.__enter__.return_value.read.return_value = xfdf_content
        mock_open.return_value = mock_file
        fill_form(pdf, ["dummy.xfdf"])

    # Verify we fell back to manual setting
    assert pdf.Root.AcroForm.Fields[1].V == "/Yes"


from unittest.mock import PropertyMock

from pdftl.exceptions import UserCommandLineError
from pdftl.operations.fill_form import (
    _fill_form_from_xfdf_data,
    _set_form_field_value,
    fully_qualified_name,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_field():
    """Returns a generic mock field for form filling tests."""
    field = MagicMock()
    # Default to not being any specific type
    field.is_text = False
    field.is_checkbox = False
    field.is_radio_button = False
    # Mock the underlying pikepdf object
    field.obj = MagicMock()
    # Default fully qualified name
    field.fully_qualified_name = "test.field"
    return field


@pytest.fixture
def mock_form():
    """Returns a list-like mock for the pikepdf Form wrapper."""
    return []


# ---------------------------------------------------------------------------
# Test: XFDF XML Parsing (Recursion & Edge Cases)
# ---------------------------------------------------------------------------


def test_xfdf_field_missing_name(mock_form):
    """
    Covers line 173.
    Tests that a <field> tag without a 'name' attribute is skipped (continue).
    """
    # XML with a nameless field
    xml_data = """
    <xfdf xmlns="http://ns.adobe.com/xfdf/">
        <fields>
            <field>
                <value>Should Be Ignored</value>
            </field>
            <field name="ValidField">
                <value>KeepMe</value>
            </field>
        </fields>
    </xfdf>
    """

    # Mock the form iteration to capture what it tries to set
    mock_field_obj = MagicMock()
    mock_field_obj.fully_qualified_name = "ValidField"
    mock_form = [mock_field_obj]

    with patch("pdftl.operations.fill_form._set_form_field_value") as mock_set:
        _fill_form_from_xfdf_data(mock_form, xml_data)

        # Should only be called once for "ValidField"
        mock_set.assert_called_once()
        args, _ = mock_set.call_args
        assert args[0] == mock_field_obj
        assert args[1] == "KeepMe"


def test_xfdf_invalid_xml():
    """Test handling of broken XML."""
    with pytest.raises(ValueError, match="Invalid XFDF XML"):
        _fill_form_from_xfdf_data([], "<broken><xml")


# ---------------------------------------------------------------------------
# Test: Field Setting Logic (Text, Checkbox, Radio)
# ---------------------------------------------------------------------------


def test_set_field_text(mock_field):
    """
    Covers lines 206-207.
    """
    mock_field.is_text = True
    _set_form_field_value(mock_field, "Hello World")
    assert mock_field.value == "Hello World"


def test_set_field_checkbox_standard(mock_field):
    """
    Covers lines 209-216.
    """
    mock_field.is_checkbox = True

    # Test "Yes" -> True
    _set_form_field_value(mock_field, "Yes")
    assert mock_field.checked is True

    # Test "Off" -> False
    _set_form_field_value(mock_field, "Off")
    assert mock_field.checked is False


def test_set_field_checkbox_fallback(mock_field):
    """
    Covers lines 217-227.
    Trigger AttributeError when setting .checked to test the fallback logic
    (manual /V and /AS setting).
    """
    mock_field.is_checkbox = True

    # Make accessing 'checked' raise AttributeError
    # We use PropertyMock to simulate a property setter failure
    type(mock_field).checked = PropertyMock(side_effect=AttributeError("No AP dict"))

    # Setup the underlying object to receive the raw update
    mock_field.obj = MagicMock()

    # Test setting to True
    _set_form_field_value(mock_field, "Yes")

    # Check that we manually set the pikepdf Name objects
    assert mock_field.obj.V == pikepdf.Name("/Yes")
    assert mock_field.obj.AS == pikepdf.Name("/Yes")

    # Test setting to False
    _set_form_field_value(mock_field, "No")
    assert mock_field.obj.V == pikepdf.Name("/Off")


def test_set_field_radio_standard(mock_field):
    """
    Covers lines 234, 240.
    Standard radio button setting (with prepending slash).
    """
    mock_field.is_radio_button = True
    # Ensure it thinks it has Kids so it takes the standard path
    mock_field.obj = {"/Kids": []}

    _set_form_field_value(mock_field, "Choice1")

    # Should be converted to Name("/Choice1")
    assert mock_field.value == pikepdf.Name("/Choice1")


def test_set_field_radio_no_slash_needed(mock_field):
    """
    Covers line 233 check.
    If value already starts with /, don't add another.
    """
    mock_field.is_radio_button = True
    mock_field.obj = {"/Kids": []}

    _set_form_field_value(mock_field, "/Choice1")
    assert mock_field.value == pikepdf.Name("/Choice1")


def test_set_field_radio_no_kids_workaround(mock_field):
    """
    Covers lines 237-238.
    Workaround for radio groups without /Kids (direct obj.V setting).
    """
    mock_field.is_radio_button = True
    # Empty dictionary (no /Kids)
    mock_field.obj = MagicMock()
    # Ensure 'in' operator fails for /Kids
    mock_field.obj.__contains__.return_value = False

    _set_form_field_value(mock_field, "OptionA")

    # Should set obj.V directly instead of field.value
    assert mock_field.obj.V == pikepdf.Name("/OptionA")


def test_set_field_unknown_type(mock_field):
    """
    Covers lines 242-244.
    Fallback for generic fields.
    """
    # All flags false by default in fixture
    _set_form_field_value(mock_field, "SomeValue")
    assert mock_field.value == "SomeValue"


# ---------------------------------------------------------------------------
# Test: Helper Functions
# ---------------------------------------------------------------------------


def test_fully_qualified_name_helper():
    """
    Covers lines 247-251.
    """
    mock_fdf_field = MagicMock()
    mock_fdf_field.T = "FieldChild"
    ancestors = ["Parent", "SubGroup"]

    result = fully_qualified_name(mock_fdf_field, ancestors)
    assert result == "Parent.SubGroup.FieldChild"


# ---------------------------------------------------------------------------
# Test: Main Entry Point (fill_form)
# ---------------------------------------------------------------------------


def test_fill_form_prompt_args(mock_field):
    """
    Covers lines 62-69.
    Test argument prompting logic.
    """
    mock_pdf = MagicMock()
    mock_get_input = MagicMock(return_value="data.fdf")

    with (
        patch("pdftl.operations.fill_form.smart_open") as mock_open,
        patch("pdftl.operations.fill_form._fill_form_from_data") as mock_fill,
    ):

        mock_open.return_value.__enter__.return_value.read.return_value = b"DATA"

        # Case 1: No args -> prompt
        fill_form(mock_pdf, [], get_input=mock_get_input)
        mock_get_input.assert_called()

        # Case 2: Explicit "PROMPT" -> prompt
        mock_get_input.reset_mock()
        fill_form(mock_pdf, ["PROMPT"], get_input=mock_get_input)
        mock_get_input.assert_called()


def test_fill_form_io_error():
    """
    Covers lines 74-75.
    Test OSError handling during file open.
    """
    with patch("pdftl.operations.fill_form.smart_open", side_effect=OSError("File not found")):
        with pytest.raises(UserCommandLineError):
            fill_form(MagicMock(), ["missing.fdf"])


import pytest

# ---------------------------------------------------------------------------
# Test: XFDF Missing Name (Line 173)
# ---------------------------------------------------------------------------


def test_xfdf_field_missing_name_attribute():
    """
    Covers line 173: 'if not name: continue' inside _recurse_xfdf.
    We pass XML with a field tag that has no 'name' attribute.
    """
    xml_data = """
    <xfdf xmlns="http://ns.adobe.com/xfdf/">
        <fields>
            <field>
                <value>IgnoreMe</value>
            </field>
            <field name="Valid">
                <value>ProcessMe</value>
            </field>
        </fields>
    </xfdf>
    """

    mock_field = MagicMock()
    mock_field.fully_qualified_name = "Valid"

    # We mock the form to act as a list containing our one valid field
    mock_form = [mock_field]

    with patch("pdftl.operations.fill_form._set_form_field_value") as mock_set:
        _fill_form_from_xfdf_data(mock_form, xml_data)

        # Verify we only tried to set the valid field, ignoring the nameless one
        mock_set.assert_called_once()
        args, _ = mock_set.call_args
        assert args[1] == "ProcessMe"


# ---------------------------------------------------------------------------
# Test: Radio Button with Kids (Lines 240)
# ---------------------------------------------------------------------------


def test_set_field_radio_with_kids():
    """
    Covers lines 239-240: else: field.value = Name(val_str).
    Condition: Field is radio button AND it has '/Kids'.
    """
    field = MagicMock()
    field.is_text = False
    field.is_checkbox = False
    field.is_radio_button = True

    # Ensure '/Kids' is in the object to hit the 'else' block
    field.obj = {"/Kids": ["kid1", "kid2"]}

    # Value to set
    val = "ChoiceA"

    _set_form_field_value(field, val)

    # Should use the high-level .value setter, not direct obj.V access
    assert field.value == pikepdf.Name("/ChoiceA")


# ---------------------------------------------------------------------------
# Test: Unknown Field Type (Lines 242-244)
# ---------------------------------------------------------------------------


def test_set_field_unknown_type():
    """
    Covers lines 242-244: Fallback for other types.
    Condition: is_text, is_checkbox, is_radio_button are all False.
    """
    field = MagicMock()
    field.is_text = False
    field.is_checkbox = False
    field.is_radio_button = False

    _set_form_field_value(field, "SomeRandomValue")

    # Should just stringify the value and set it
    assert field.value == "SomeRandomValue"
