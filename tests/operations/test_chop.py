from unittest.mock import MagicMock, patch

from pdftl.operations.chop import chop_pages


def test_chop_unmatched_page_pass_through():
    """
    Covers line 183: final_pages.append(source_page)
    Occurs when a page index is not found in page_rules.
    """
    # Setup PDF with 2 pages
    mock_pdf = MagicMock()
    p1 = MagicMock(name="Page1")
    p2 = MagicMock(name="Page2")
    # Determine length for range
    mock_pdf.pages = [p1, p2]

    # Mock the rules parser to only return a rule for index 0 (Page 1)
    # Page 2 (index 1) will have no rule, triggering line 183
    mock_rules = {0: "cols2"}

    with patch("pdftl.operations.chop.parse_chop_specs_to_rules", return_value=mock_rules):
        # We also mock the internal apply function to avoid complex logic there
        with patch("pdftl.operations.chop._apply_chop_to_page", return_value=[MagicMock()]):
            chop_pages(mock_pdf, ["irrelevant_spec"])

    # Verify behavior:
    # The function deletes all pages and extends with final_pages.
    # We expect p2 (the one without a rule) to be in the final list unmodified.
    assert p2 in mock_pdf.pages
