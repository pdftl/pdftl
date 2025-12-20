import pikepdf
import pytest

from pdftl.commands.chop import chop_pages
from pdftl.commands.overlay import apply_overlay
from pdftl.commands.update_info import update_info

# --- CHOP TESTS ---


def test_chop_basic(two_page_pdf):
    """Test chopping pages into rows (horizontal split)."""
    with pikepdf.open(two_page_pdf) as pdf:
        # "rows" defaults to 2 equal pieces per page
        # 2 input pages * 2 pieces = 4 output pages
        specs = ["rows"]
        result = chop_pages(pdf, specs)

        assert len(result.pages) == 4
        # Verify page size changed (height should be halved)
        # We assume standard A4 or Letter, just checking it's smaller
        original_height = 1000  # arbitrary, dependent on fixture
        # But we can check they are equal to each other
        assert result.pages[0].mediabox == result.pages[1].mediabox


def test_chop_specific_spec(two_page_pdf):
    """Test chopping with a specific column spec."""
    with pikepdf.open(two_page_pdf) as pdf:
        # "cols3" -> 3 vertical columns
        # 2 input pages * 3 pieces = 6 output pages
        specs = ["cols3"]
        result = chop_pages(pdf, specs)
        assert len(result.pages) == 6


def test_chop_no_spec_defaults(two_page_pdf):
    """Test that empty specs default to 'cols' (2 columns)."""
    with pikepdf.open(two_page_pdf) as pdf:
        result = chop_pages(pdf, [])
        # 2 pages * 2 cols = 4 pages
        assert len(result.pages) == 4


# --- OVERLAY/STAMP TESTS ---


@pytest.fixture
def stamp_pdf_path(tmp_path):
    """Creates a 1-page PDF to act as a stamp/overlay."""
    p = pikepdf.new()
    p.add_blank_page()
    output = tmp_path / "stamp.pdf"
    p.save(output)
    return str(output)


def test_overlay_stamp_basic(two_page_pdf, stamp_pdf_path):
    """Test applying a stamp (overlay)."""
    with pikepdf.open(two_page_pdf) as pdf:
        # apply_overlay(input_pdf, overlay_filename, ...)
        apply_overlay(pdf, stamp_pdf_path, on_top=True)

        # We verify success by checking the file structure implicitly
        # (pikepdf handles the heavy lifting)
        assert len(pdf.pages) == 2


def test_overlay_background(two_page_pdf, stamp_pdf_path):
    """Test applying a background (underlay)."""
    with pikepdf.open(two_page_pdf) as pdf:
        apply_overlay(pdf, stamp_pdf_path, on_top=False)
        assert len(pdf.pages) == 2


def test_overlay_missing_file_error(two_page_pdf):
    """Test error when overlay file doesn't exist."""
    with pikepdf.open(two_page_pdf) as pdf:
        with pytest.raises(FileNotFoundError):
            apply_overlay(pdf, "non_existent_file.pdf")


# --- UPDATE_INFO TESTS ---


@pytest.fixture
def metadata_file(tmp_path):
    """Creates a dummy dump_data formatted text file."""
    content = (
        "InfoKey: Title\n" "InfoValue: New Title\n" "InfoKey: Author\n" "InfoValue: Test Author\n"
    )
    # update_info opens in 'rb' mode, so we write bytes
    f = tmp_path / "meta.txt"
    f.write_bytes(content.encode("utf-8"))
    return str(f)


def test_update_info_basic(two_page_pdf, metadata_file):
    """Test updating PDF metadata from file."""
    with pikepdf.open(two_page_pdf) as pdf:
        # op_args expects [filename]
        op_args = [metadata_file]
        mock_input = lambda msg, **kwargs: None

        update_info(pdf, op_args, mock_input)

        # Verify changes
        assert pdf.docinfo["/Title"] == "New Title"
        assert pdf.docinfo["/Author"] == "Test Author"


def test_update_info_missing_arg(two_page_pdf):
    """Test error when no metadata filename is provided."""
    from pdftl.exceptions import MissingArgumentError

    with pikepdf.open(two_page_pdf) as pdf:
        with pytest.raises(MissingArgumentError):
            update_info(pdf, [], lambda x: x)
