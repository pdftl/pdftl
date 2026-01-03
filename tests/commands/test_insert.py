# tests/commands/test_insert.py

import pikepdf
import pytest

from pdftl.commands.insert import insert_pages
from pdftl.commands.parsers.insert_parser import parse_insert_args
from pdftl.exceptions import UserCommandLineError

# --- PART 1: Parser Logic Tests ---


@pytest.mark.parametrize(
    "args, expected",
    [
        # 1. Defaults
        ([], (1, None, "after", "1-end")),
        # 2. Pure Geometry
        (["(A4)"], (1, "A4", "after", "1-end")),
        (["(20cm,10cm)"], (1, "20cm,10cm", "after", "1-end")),
        # 3. Count + Geometry
        (["2(A4)"], (2, "A4", "after", "1-end")),
        (["5(A4)"], (5, "A4", "after", "1-end")),
        # 4. Ambiguity Resolution: "N" vs "Page Range"
        # "insert 5" -> Insert 1 page at range 5 (Default behavior)
        (["5"], (1, None, "after", "5")),
        # "insert 5 after 1" -> Insert 5 pages after range 1 (Explicit keyword trigger)
        (["5", "after", "1"], (5, None, "after", "1")),
        # "insert 5 before 2" -> Insert 5 pages before range 2
        (["5", "before", "2"], (5, None, "before", "2")),
        # 5. Full Complexity
        # "insert 2(A4) before 1-5"
        (["2(A4)", "before", "1-5"], (2, "A4", "before", "1-5")),
        # "insert (model=1) after end"
        (["(model=1)", "after", "end"], (1, "model=1", "after", "end")),
    ],
)
def test_insert_parser(args, expected):
    """Verify that CLI arguments are parsed into the correct InsertSpec."""
    spec = parse_insert_args(args)
    assert spec.count == expected[0]
    assert spec.geometry_spec == expected[1]
    assert spec.mode == expected[2]
    assert spec.target_page_spec == expected[3]


# --- PART 2: Command Execution Tests ---


@pytest.fixture
def simple_pdf(tmp_path):
    """Creates a simple 1-page PDF (100x100) for testing."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(100, 100))
    return pdf


def test_insert_default_copy(simple_pdf):
    """Test default: copy target page geometry."""
    # Insert 1 page after page 1
    insert_pages(simple_pdf, ["after", "1"])

    assert len(simple_pdf.pages) == 2
    # The new page (index 1) should match page 1's size (100x100)
    assert simple_pdf.pages[1].MediaBox == [0, 0, 100, 100]


def test_insert_absolute_geometry(simple_pdf):
    """Test inserting a specific paper size (A4)."""
    # A4 is approx 595.28 x 841.89
    insert_pages(simple_pdf, ["(A4)"])

    assert len(simple_pdf.pages) == 2
    mb = simple_pdf.pages[1].MediaBox
    assert float(mb[2]) == pytest.approx(595.28, rel=1e-2)
    assert float(mb[3]) == pytest.approx(841.89, rel=1e-2)


def test_insert_custom_units(simple_pdf):
    """Test inserting custom absolute units (72pt = 1inch)."""
    # Insert a 1x1 inch page
    insert_pages(simple_pdf, ["(72pt, 1in)"])

    assert len(simple_pdf.pages) == 2
    mb = simple_pdf.pages[1].MediaBox
    assert float(mb[2]) == 72.0
    assert float(mb[3]) == 72.0


def test_insert_relative_units(simple_pdf):
    """Test inserting relative units (%) based on target page."""
    # Target is 100x100. We insert (50%, 200%).
    # New page should be 50x200.
    insert_pages(simple_pdf, ["(50%, 200%)"])

    assert len(simple_pdf.pages) == 2
    mb = simple_pdf.pages[1].MediaBox
    assert float(mb[2]) == 50.0
    assert float(mb[3]) == 200.0


def test_insert_model_mode(simple_pdf):
    """Test copying geometry from a specific model page."""
    # 1. Add a second page with distinct size (200x200) to act as the model
    page2 = simple_pdf.add_blank_page(page_size=(200, 200))

    # 2. Insert AFTER page 1, but using PAGE 2 (model=2) as the geometry source
    # The new page will be at index 1 (between page 1 and 2)
    insert_pages(simple_pdf, ["(model=2)", "after", "1"])

    assert len(simple_pdf.pages) == 3
    # Check the new page (index 1)
    new_page_mb = simple_pdf.pages[1].MediaBox
    assert new_page_mb == [0, 0, 200, 200]


def test_insert_count(simple_pdf):
    """Test inserting multiple pages."""
    # Insert 3 pages after page 1
    insert_pages(simple_pdf, ["3", "after", "1"])
    assert len(simple_pdf.pages) == 1 + 3


def test_insert_unknown_geometry_raises(simple_pdf):
    """Test that invalid geometry specs raise UserCommandLineError."""
    with pytest.raises(UserCommandLineError):
        insert_pages(simple_pdf, ["(INVALID_SIZE)"])


def test_insert_before_start(simple_pdf):
    """Test inserting before the first page."""
    # Insert a 10x10 page before page 1
    insert_pages(simple_pdf, ["(10pt,10pt)", "before", "1"])

    assert len(simple_pdf.pages) == 2
    # The NEW page is now at index 0
    assert simple_pdf.pages[0].MediaBox == [0, 0, 10, 10]
    # The original page is at index 1
    assert simple_pdf.pages[1].MediaBox == [0, 0, 100, 100]


# ... (append to tests/commands/test_insert.py)


def test_insert_no_matching_target(simple_pdf, caplog):
    """
    Cover lines 99-100: Warning when target range matches nothing.
    """
    # Try to insert after page 10 (only 1 page exists)
    insert_pages(simple_pdf, ["after", "10"])

    assert "matched no pages" in caplog.text
    # No pages should be added
    assert len(simple_pdf.pages) == 1


def test_insert_copy_boxes(simple_pdf):
    """
    Cover lines 127, 129: Verify CropBox and TrimBox are copied if present.
    """
    # Setup source page with CropBox and TrimBox
    page = simple_pdf.pages[0]
    page.CropBox = [10, 10, 90, 90]
    page.TrimBox = [20, 20, 80, 80]

    # Insert copy
    insert_pages(simple_pdf, ["after", "1"])

    assert len(simple_pdf.pages) == 2
    new_page = simple_pdf.pages[1]

    # Check if boxes carried over
    assert new_page.CropBox == [10, 10, 90, 90]
    assert new_page.TrimBox == [20, 20, 80, 80]


def test_insert_invalid_model_index(simple_pdf):
    """
    Cover line 147: Raise error when 'model=N' matches no pages.
    """
    with pytest.raises(UserCommandLineError) as exc:
        insert_pages(simple_pdf, ["(model=99)"])

    assert "matched no pages" in str(exc.value)


def test_insert_malformed_custom_geometry(simple_pdf):
    """
    Cover lines 183-185: ValueError exception swallowing.

    Logic:
      1. "(bad,data)" contains a comma, so it enters the custom dims block.
      2. `dim_str_to_pts` raises ValueError on "bad".
      3. The except block catches it and `pass`.
      4. Code falls through to 'Unknown geometry spec' error.
    """
    with pytest.raises(UserCommandLineError) as exc:
        insert_pages(simple_pdf, ["(bad,data)"])

    assert "Unknown geometry spec" in str(exc.value)
