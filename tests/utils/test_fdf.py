import io
from unittest.mock import MagicMock

import pikepdf
import pytest

# --- Import the module to test ---
from pdftl.utils.fdf import (
    add_fdf_to_catalog,
    extract_main_fdf_dict,
    wrap_fdf_data_in_pdf_bytes,
)

# --- Mock Data ---

# A realistic FDF file is a PDF object.
# We'll create the header, a simple dictionary, a nested dictionary, and the footer.
FDF_HEADER = b"""%FDF-1.2
%\xe2\xe3\xcf\xd3
1 0 obj
"""

# The dictionary *inside* the 1 0 obj
FDF_DICT_SIMPLE_CORE = b" << /FDF << /Fields [ <</T(name)/V(John)>> ] >> >>"
FDF_DICT_SIMPLE = FDF_HEADER + FDF_DICT_SIMPLE_CORE + b"\nendobj\n"

# A more complex FDF with nested dictionaries
FDF_DICT_NESTED_CORE = (
    b" << /FDF << /Fields [ <</T(name)/V(John)>> /Kids [ << /T(addr) /V(123 Main) >> ] ] >> >>"
)
FDF_DICT_NESTED = FDF_HEADER + FDF_DICT_NESTED_CORE + b"\nendobj\n"

FDF_FOOTER = b"""
trailer
<</Root 1 0 R>>
%%EOF
"""

# Full, valid FDF file content
FDF_SIMPLE_FULL = FDF_DICT_SIMPLE + FDF_FOOTER
FDF_NESTED_FULL = FDF_DICT_NESTED + FDF_FOOTER

# A simple PDF skeleton for testing add_fdf_to_catalog
PDF_SKELETON_BYTES = b"""%PDF-1.7
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>
endobj
xref
...
%%EOF
"""

# --- Tests for extract_main_fdf_dict ---


def test_extract_main_fdf_dict_simple():
    """
    Tests extracting the simple FDF dictionary.
    """
    # The expected slice is the content *after* /FDF and *before* the final >>
    expected_bytes = b" << /Fields [ <</T(name)/V(John)>> ] >>"

    # The function first finds the full dict:
    full_dict_bytes = FDF_DICT_SIMPLE_CORE.strip()  # << /FDF ... >> >>

    # Then it finds "/FDF" and slices from there
    start = full_dict_bytes.find(b"/FDF") + 4

    # The correct slice is from that point to the last two '>>'
    expected_bytes_from_logic = full_dict_bytes[start:-2]

    # This test asserts the correct final behavior
    assert extract_main_fdf_dict(FDF_SIMPLE_FULL) == expected_bytes_from_logic


def test_extract_main_fdf_dict_nested():
    """
    Tests extracting a nested FDF dictionary.
    """
    # The function first finds the full dict:
    full_dict_bytes = FDF_DICT_NESTED_CORE.strip()  # << /FDF ... >> >>

    # Then it finds "/FDF" and slices from there
    start = full_dict_bytes.find(b"/FDF") + 4

    # The correct slice is from that point to the last two '>>'
    expected_bytes_from_logic = full_dict_bytes[start:-2]

    assert extract_main_fdf_dict(FDF_NESTED_FULL) == expected_bytes_from_logic


@pytest.mark.parametrize(
    "bad_data, error_msg",
    [
        (b"no start bracket", "No << found in FDF"),
        (b"<< no end bracket", "Could not find matching >> for main FDF dict"),
    ],
)
def test_extract_main_fdf_dict_broken_data(bad_data, error_msg):
    """Tests that the function raises ValueErrors on malformed data."""
    with pytest.raises(ValueError, match=error_msg):
        extract_main_fdf_dict(bad_data)


# --- Tests for add_fdf_to_catalog ---


def test_add_fdf_to_catalog_happy_path():
    """Tests that the /FDF reference is correctly added to the Catalog."""
    fdf_obj_num = 99
    result_bytes = add_fdf_to_catalog(PDF_SKELETON_BYTES, fdf_obj_num)

    # Check that the new reference is present
    expected_injection = b"/Type /Catalog /Pages 2 0 R /FDF 99 0 R >>"
    assert expected_injection in result_bytes

    # Check that the original bytes were modified
    assert result_bytes != PDF_SKELETON_BYTES


def test_add_fdf_to_catalog_idempotent():
    """Tests that a /FDF reference is not added if one already exists."""
    fdf_obj_num = 99
    # Create a PDF that already has an FDF reference
    pdf_with_fdf = PDF_SKELETON_BYTES.replace(b"/Pages 2 0 R >>", b"/Pages 2 0 R /FDF 1 0 R >>")

    result_bytes = add_fdf_to_catalog(pdf_with_fdf, fdf_obj_num)

    # The bytes should be unchanged
    assert result_bytes == pdf_with_fdf
    # The new reference should NOT be present
    assert b"99 0 R" not in result_bytes


def test_add_fdf_to_catalog_no_catalog():
    """Tests that a ValueError is raised if the /Catalog object can't be found."""
    bad_pdf_bytes = b"%PDF-1.7\n%%EOF"
    with pytest.raises(ValueError, match="Could not find catalog object"):
        add_fdf_to_catalog(bad_pdf_bytes, 99)


# --- Tests for wrap_fdf_data_in_pdf_bytes ---


def test_wrap_fdf_data_in_pdf_bytes_integration():
    """
    Tests the full wrap function as an integration test.
    This test will FAIL until extract_main_fdf_dict is fixed.
    """
    # 1. Arrange
    # (Using the simple FDF data)

    # 2. Act
    result_io = wrap_fdf_data_in_pdf_bytes(FDF_SIMPLE_FULL)

    # 3. Assert: Check if the resulting bytes form a valid PDF
    result_io.seek(0)

    # This will raise a pikepdf.PdfError (like InputError) if the PDF is corrupt
    with pikepdf.Pdf.open(result_io) as pdf:
        # Check that the /FDF key was added to the Root catalog
        assert "/FDF" in pdf.Root

        # Check that the FDF object itself exists
        fdf_dict = pdf.Root.FDF
        assert fdf_dict is not None

        # Check the actual content
        assert "/Fields" in fdf_dict
        fields = fdf_dict.Fields
        assert len(fields) == 1

        field_one = fields[0]
        # pikepdf parses PDF strings into pikepdf.String objects
        assert field_one.T == pikepdf.String("name")
        assert field_one.V == pikepdf.String("John")


def test_wrap_fdf_data_in_pdf_bytes_no_xref(mocker):
    """
    Tests that a ValueError is raised if the skeleton PDF
    bytes do not contain an xref table.
    """
    # 1. Mock the first helper function so it doesn't fail
    mocker.patch("pdftl.utils.fdf.extract_main_fdf_dict", return_value=b"<< /Fields [] >>")

    # 2. Mock the BytesIO object that pdf.save() writes to.
    # We will make its .getvalue() method return corrupted PDF bytes
    # that are missing the "\nxref" string.
    mock_buffer = MagicMock(spec=io.BytesIO)
    mock_buffer.getvalue.return_value = b"%PDF-1.7\n%corrupted file\n%no xref here\n%%EOF"

    # Patch the io.BytesIO constructor to return our mock buffer
    mocker.patch("io.BytesIO", return_value=mock_buffer)

    # We also need to mock pikepdf.Pdf.new() because .save() will be called on it
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mocker.patch("pikepdf.Pdf.new", return_value=mock_pdf)

    # 3. Act and Assert
    # Now, when wrap_fdf_data_in_pdf_bytes runs, it will:
    # - call extract_main_fdf_dict (mocked)
    # - call pikepdf.Pdf.new (mocked)
    # - call io.BytesIO() (mocked)
    # - call pdf.save(mock_buffer) (doesn't matter what it writes)
    # - call pdf_buffer.getvalue() (returns our corrupted bytes)
    # - fail the "b\nxref" check and raise the error

    with pytest.raises(ValueError, match="xref not found in skeleton PDF"):
        wrap_fdf_data_in_pdf_bytes(b"dummy data")
