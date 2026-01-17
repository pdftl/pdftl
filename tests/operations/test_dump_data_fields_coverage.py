import pikepdf

from pdftl.operations.dump_data_fields import dump_data_fields


def test_dump_fields_checkbox_options():
    """
    Covers lines 231-234, 244-251, 317.
    Tests extraction of checkbox options from the Appearance (/AP) dictionary.
    """
    with pikepdf.new() as pdf:
        pdf.add_blank_page()

        # 1. Define Appearance Dictionary
        ap_n_dict = pikepdf.Dictionary(
            {"/Yes": pikepdf.Dictionary(), "/Off": pikepdf.Dictionary()}
        )
        ap_dict = pikepdf.Dictionary({"/N": ap_n_dict})

        # 2. Define the Field (MUST be indirect)
        field_dict = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Annot"),
                    "/Subtype": pikepdf.Name("/Widget"),
                    "/FT": pikepdf.Name("/Btn"),
                    "/T": "MyCheckbox",
                    "/V": pikepdf.Name("/Yes"),
                    "/AP": ap_dict,
                    "/P": pdf.pages[0].obj,
                }
            )
        )

        # 3. Add to AcroForm
        pdf.Root.AcroForm = pikepdf.Dictionary({"/Fields": pikepdf.Array([field_dict])})

        # 4. Link page to annotation
        pdf.pages[0].Annots = pikepdf.Array([field_dict])

        # Run operation
        result = dump_data_fields(pdf)

        assert result.success
        data = result.data
        assert len(data) == 1
        field = data[0]

        # Verify Options Extraction
        assert "FieldStateOption" in field
        options = field["FieldStateOption"]
        # "Off" is standard; "Yes" comes from keys in AP/N
        assert "Yes" in options
        assert "Off" in options
        assert field["FieldValue"] == "Yes"


def test_dump_fields_radio_group_kids():
    """
    Covers lines 256-257.
    Tests extraction of options from a Radio Group where options are defined in Kids.
    """
    with pikepdf.new() as pdf:
        pdf.add_blank_page()

        # Parent Radio Group (Indirect)
        parent_dict = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/FT": pikepdf.Name("/Btn"),
                    "/T": "RadioGroup",
                    "/Ff": 49152,  # Radio button flag
                    "/V": pikepdf.Name("/Choice1"),
                    # Kids will be added later
                }
            )
        )

        # Kid 1 (Indirect)
        ap1 = pikepdf.Dictionary({"/N": pikepdf.Dictionary({"/Choice1": pikepdf.Dictionary()})})
        kid1 = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Parent": parent_dict,
                    "/Subtype": pikepdf.Name("/Widget"),
                    "/AP": ap1,
                    "/P": pdf.pages[0].obj,
                }
            )
        )

        # Kid 2 (Indirect)
        ap2 = pikepdf.Dictionary({"/N": pikepdf.Dictionary({"/Choice2": pikepdf.Dictionary()})})
        kid2 = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Parent": parent_dict,
                    "/Subtype": pikepdf.Name("/Widget"),
                    "/AP": ap2,
                    "/P": pdf.pages[0].obj,
                }
            )
        )

        # Link Kids to Parent
        parent_dict.Kids = pikepdf.Array([kid1, kid2])

        # Link to Root and Page
        pdf.Root.AcroForm = pikepdf.Dictionary({"/Fields": pikepdf.Array([parent_dict])})
        pdf.pages[0].Annots = pikepdf.Array([kid1, kid2])

        result = dump_data_fields(pdf)

        assert result.success
        assert len(result.data) == 1
        field = result.data[0]

        # Check that we recursed into Kids to find options
        assert "FieldStateOption" in field
        # Note: Depending on sorting/implementation "Off" might appear,
        # but Choice1/Choice2 definitely should.
        assert "Choice1" in field["FieldStateOption"]
        assert "Choice2" in field["FieldStateOption"]


def test_dump_fields_recursive_naming():
    """
    Covers lines 334, 353-355.
    Tests recursive traversal of intermediate nodes and dot-notation naming.
    Structure: Parent (Node) -> Child (Node) -> Grandchild (Field)
    """
    with pikepdf.new() as pdf:
        pdf.add_blank_page()

        # 1. Root Parent (Indirect)
        parent_node = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/T": "Parent",
                    # Kids added later
                }
            )
        )

        # 2. Intermediate Child (Indirect)
        child_node = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/T": "Child",
                    "/Parent": parent_node,
                    # Kids added later
                }
            )
        )

        # 3. Leaf Field (Grandchild) (Indirect)
        leaf_field = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Annot"),
                    "/Subtype": pikepdf.Name("/Widget"),
                    "/FT": pikepdf.Name("/Tx"),  # Text field
                    "/T": "GrandChild",
                    "/V": "I am deep",
                    "/P": pdf.pages[0].obj,
                    "/Parent": child_node,
                }
            )
        )

        # Link Downwards
        parent_node.Kids = pikepdf.Array([child_node])
        child_node.Kids = pikepdf.Array([leaf_field])

        # Setup AcroForm
        pdf.Root.AcroForm = pikepdf.Dictionary({"/Fields": pikepdf.Array([parent_node])})
        pdf.pages[0].Annots = pikepdf.Array([leaf_field])

        result = dump_data_fields(pdf)

        assert result.success
        assert len(result.data) == 1
        field = result.data[0]

        # Verify full path name construction
        assert field["FieldName"] == "Parent.Child.GrandChild"
        assert field["FieldValue"] == "I am deep"


def test_dump_fields_no_acroform():
    """
    Covers lines 412-414.
    Tests behavior when PDF has no AcroForm dictionary.
    """
    with pikepdf.new() as pdf:
        # Do not add AcroForm
        result = dump_data_fields(pdf)

        assert result.success
        assert result.data == []


##################################################

from unittest.mock import MagicMock, mock_open, patch

import pytest

from pdftl.core.types import OpResult
from pdftl.operations.dump_data_fields import _extract_field_data_high_level, dump_fields_cli_hook

# --- Mocks for Test 2 ---


class MockPdfObj:
    """Simulates the underlying pikepdf object (.obj)"""

    def __init__(self):
        # Line 325: 'TU' attribute triggers FieldNameAlt
        self.TU = "My Tooltip"

        # Line 346: 'DV' attribute triggers FieldValueDefault
        self.DV = "Yes"

        # Ensure 'V' and 'AS' are missing to trigger Line 341 (val is None)
        # We do not set self.V or self.AS here.

        # Justification check uses /Q or Q
        self.Q = 1

    def get(self, key):
        """Simulate pikepdf.Object.get()"""
        if key == "/DV":
            return "Yes"
        if key == "/Q":
            return 1
        return None

    def __contains__(self, key):
        return key in ["/Q"]


class Checkbox:
    """
    Simulates a pikepdf.form.Checkbox object.
    The class name 'Checkbox' is crucial for Line 341 logic.
    """

    def __init__(self):
        self.obj = MockPdfObj()
        self.fully_qualified_name = "my.checkbox"


def test_dump_fields_cli_hook_meta_none():
    """
    Covers Line 110: `escape_xml = True` (else block).
    Ensures that if result.meta is None, the hook still proceeds with default escaping.
    """
    # Create an OpResult with meta=None.
    # We use "Text" here because the extraction layer normally produces "Text"
    # for Tx fields, and the CLI hook's mapping function passes it through.
    result = OpResult(success=True, data=[{"FieldType": "Text"}], meta=None)

    # Mock stage options
    mock_stage = MagicMock()
    mock_stage.options = {"output_file": "dummy.txt"}

    # Mock smart_open to capture file operations
    m_open = mock_open()

    with patch("pdftl.operations.dump_data_fields.smart_open", m_open):
        dump_fields_cli_hook(result, mock_stage)

    # Assert file was opened (proving execution reached past line 110)
    m_open.assert_called_once_with("dummy.txt")

    # Verify content was written (proving default escape_xml=True worked)
    handle = m_open()

    # Collect all write calls to handle split writes (content vs newline)
    written_content = "".join(call.args[0] for call in handle.write.call_args_list)

    # Ensure our output contains the type string
    assert "FieldType: Text" in written_content


def test_extract_field_data_high_level_coverage():
    """
    Covers:
      - Line 325: data["FieldNameAlt"] = field.obj.TU
      - Line 341: val = "Off" (Fallback for unset checkbox)
      - Line 346: data["FieldValueDefault"] = ...
    """
    # Instantiate our specific Mock class named 'Checkbox'
    mock_field = Checkbox()

    # Execute
    data = _extract_field_data_high_level(mock_field)

    # Assertions

    # 1. Line 325 Check: FieldNameAlt should correspond to .TU
    assert "FieldNameAlt" in data
    assert data["FieldNameAlt"] == "My Tooltip"

    # 2. Line 341 Check: FieldValue should default to "Off"
    # because the mock has no /V and is a Checkbox
    assert data["FieldValue"] == "Off"

    # 3. Line 346 Check: FieldValueDefault should correspond to .DV
    assert "FieldValueDefault" in data
    assert data["FieldValueDefault"] == "Yes"

    # 4. Verify Justification (sanity check based on Q=1)
    assert data["FieldJustification"] == "Center"
