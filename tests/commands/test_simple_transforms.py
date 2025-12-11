import pikepdf
import pytest

from pdftl.commands.crop import crop_pages
from pdftl.commands.delete import delete_pages
from pdftl.commands.delete_annots import delete_annots
from pdftl.commands.filter import filter_pdf
from pdftl.commands.modify_annots import modify_annots
from pdftl.commands.rotate import rotate_pdf
from pdftl.commands.spin import spin_pdf

# Define the cases: (Function, Specs List, Expected Page Count Change)
PARAMS = [
    (rotate_pdf, ["1-endeast"], 0),  # Rotation keeps page count
    (spin_pdf, ["1-end:45"], 0),  # Spin keeps page count
    (delete_pages, ["2"], -1),  # Deleting 1 page reduces count by 1
    (crop_pages, ["1-end(0,0,100,100)"], 0),  # Crop keeps page count
    (filter_pdf, [], 0),  # Filter usually just passes through
    (delete_annots, [], 0),  # Removing annots keeps page count
    # modify_annots usually requires specific args, passing empty list usually does nothing safe
    (modify_annots, [], 0),
]


@pytest.mark.parametrize("func, specs, page_diff", PARAMS)
def test_simple_transform(two_page_pdf, func, specs, page_diff):
    """
    Tests commands that take (pdf, specs) and modify the PDF.
    """
    with pikepdf.open(two_page_pdf) as pdf:
        original_count = len(pdf.pages)

        # Some functions allow empty specs, others might handle it differently.
        # Filter doesn't take specs in your signature: def filter_pdf(pdf)
        if func == filter_pdf:
            result = func(pdf)
        else:
            result = func(pdf, specs)

        # Some commands return the PDF, others modify in-place and return None.
        # We check the 'pdf' object itself for validity.
        assert isinstance(pdf, pikepdf.Pdf)
        assert len(pdf.pages) == original_count + page_diff
