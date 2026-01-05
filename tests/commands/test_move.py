# tests/commands/test_move.py

import pikepdf
import pytest

from pdftl.commands.move import move_pages
from pdftl.commands.parsers.move_parser import parse_move_args
from pdftl.exceptions import UserCommandLineError

# --- Parser Tests ---


def test_move_parser_valid():
    spec = parse_move_args(["1-5", "after", "10"])
    assert spec.source_spec == "1-5"
    assert spec.mode == "after"
    assert spec.target_spec == "10"


def test_move_parser_spaces():
    spec = parse_move_args(["1", "-", "5", "before", "end"])
    assert spec.source_spec == "1 - 5"
    assert spec.mode == "before"
    assert spec.target_spec == "end"


def test_move_parser_errors():
    # Missing keyword
    with pytest.raises(UserCommandLineError):
        parse_move_args(["1", "2"])
    # Missing source
    with pytest.raises(UserCommandLineError):
        parse_move_args(["before", "2"])
    # Missing target
    with pytest.raises(UserCommandLineError):
        parse_move_args(["1", "after"])


# --- Logic Tests ---


@pytest.fixture
def numbered_pdf():
    """Creates a 10-page PDF where page content/label equals its original index (0-9)."""
    pdf = pikepdf.new()
    for i in range(10):
        # We use a dummy cropbox to identify pages by 'size' or just rely on index tracking
        # Ideally, we track object ID, but pikepdf pages are proxies.
        # We'll attach a custom attribute to the python object for testing if possible,
        # but pikepdf regenerates wrappers.
        # Instead, we'll verify by list manipulation logic or set a specific box size.
        page = pdf.add_blank_page(page_size=(100, 100 + i))  # Height = 100 + index
        # Index 0 -> Height 100
        # Index 5 -> Height 105
    return pdf


def get_heights(pdf):
    return [int(p.MediaBox[3]) for p in pdf.pages]


def test_move_simple_after(numbered_pdf):
    # move 5 after 10 (indices: move 4 after 9).
    # PDF has 10 pages (0..9).
    # move page_index 0 (height 100) after page_index 5 (height 105)
    # Args: source="1", target="6" (1-based)

    # Let's match the spec example: "move 5 after 10"
    # (Assuming 1-based inputs in CLI)
    # Move page 5 (idx 4, h=104) after page 8 (idx 7, h=107)

    move_pages(numbered_pdf, ["5", "after", "8"])

    # Original: 0, 1, 2, 3, 4(moved), 5, 6, 7(anchor), 8, 9
    # Expected: 0, 1, 2, 3, 5, 6, 7, 4, 8, 9
    heights = get_heights(numbered_pdf)
    assert heights == [100, 101, 102, 103, 105, 106, 107, 104, 108, 109]


def test_move_range_before(numbered_pdf):
    # Example: move 1-3 before 5
    # Move indices 0,1,2 before index 4
    # Original: [0, 1, 2], 3, [4], 5...
    # Expected: 3, 0, 1, 2, 4, 5...

    move_pages(numbered_pdf, ["1-3", "before", "5"])
    heights = get_heights(numbered_pdf)
    assert heights == [103, 100, 101, 102, 104, 105, 106, 107, 108, 109]


def test_move_to_front(numbered_pdf):
    # Example: move 9-10 before 1 (Move last 2 to start)
    # Indices 8, 9. Target 0.
    move_pages(numbered_pdf, ["9-10", "before", "1"])
    heights = get_heights(numbered_pdf)
    assert heights == [108, 109, 100, 101, 102, 103, 104, 105, 106, 107]


def test_move_to_end(numbered_pdf):
    # Example: move 1-2 after end
    move_pages(numbered_pdf, ["1-2", "after", "end"])
    heights = get_heights(numbered_pdf)
    assert heights == [102, 103, 104, 105, 106, 107, 108, 109, 100, 101]


def test_move_overlap_source_contains_target(numbered_pdf):
    # Spec: "move 1-5 after 2"
    # Source: 0,1,2,3,4. Target: 1. Anchor: after 1 (orig index 2).
    # Removing 0-4 removes the target.
    # Logic check:
    # Anchor Orig = 2.
    # Adjustment: How many source < 2? (0, 1) -> 2 pages.
    # Anchor Final = 2 - 2 = 0.
    # Remove 0,1,2,3,4. Remainder: 5,6,7,8,9.
    # Insert at 0.
    # Result: 0,1,2,3,4, 5,6,7,8,9. (No change)

    original = get_heights(numbered_pdf)
    move_pages(numbered_pdf, ["1-5", "after", "2"])
    assert get_heights(numbered_pdf) == original


def test_move_overlap_target_contains_source(numbered_pdf):
    # "move 2 after 1-3"
    # Source: 1. Target: 0,1,2.
    # Anchor: after last target (2) -> 3.
    # Adjustment: Source (1) < 3? Yes (1).
    # Anchor Final = 3 - 1 = 2.
    # Remove 1. List: 0, 2, 3...
    # Insert at 2. List: 0, 2, 1, 3...

    move_pages(numbered_pdf, ["2", "after", "1-3"])
    # Orig: 0, 1, 2, 3...
    # Exp:  0, 2, 1, 3...
    heights = get_heights(numbered_pdf)
    assert heights[0:4] == [100, 102, 101, 103]


def test_non_contiguous_source(numbered_pdf):
    # move 1,3,5 before 8
    # Source: 0, 2, 4. Target: 7.
    # Anchor: 7.
    # Adj: 0<7(y), 2<7(y), 4<7(y) -> 3.
    # Anchor Final: 7 - 3 = 4.
    # Remove 0,2,4. List: 1, 3, 5, 6, 7, 8, 9.
    # Insert at 4 (Before 7).
    # Result: 1, 3, 5, 6, [0, 2, 4], 7, 8, 9.

    move_pages(numbered_pdf, ["1,3,5", "before", "8"])
    heights = get_heights(numbered_pdf)
    expected = [101, 103, 105, 106, 100, 102, 104, 107, 108, 109]
    assert heights == expected


import logging

import pytest


def test_move_empty_source_spec(two_page_pdf, caplog):
    """
    Covers lines 46-47:
    logger.warning("Move source '%s' matched no pages...", ...)
    return OpResult(...)
    """
    # 1. Provide a source spec that matches nothing (e.g., "100" in a small doc)
    args = ["100", "before", "1"]

    with caplog.at_level(logging.WARNING):
        result = move_pages(pikepdf.open(two_page_pdf), args)

    # Assert successful return but no changes
    assert result.success
    assert "matched no pages" in caplog.text


def test_move_invalid_target_spec(two_page_pdf):
    """
    Covers line 54:
    if not target_nums: raise UserCommandLineError(...)
    """
    # 1. Provide a valid source but invalid target
    args = ["1", "before", "100"]  # '100' does not exist

    with pytest.raises(UserCommandLineError, match="matched no pages"):
        move_pages(pikepdf.open(two_page_pdf), args)


import json
from unittest.mock import mock_open, patch

import pytest


def test_move_command_loads_json_spec():
    """
    Test that the move_pages command delegates to arg_resolver
    when it sees an @ argument.
    """
    move_json = json.dumps({"source_spec": "10", "mode": "after", "target_spec": "1"})

    # 1. Mock 'open' so we can read the fake file
    with patch("builtins.open", mock_open(read_data=move_json)):
        # 2. CRITICAL FIX: Mock 'exists' so the helper believes the file is there
        with patch("pathlib.Path.exists", return_value=True):
            # 3. Mock execute_move so we don't need a real PDF
            with patch("pdftl.commands.move.execute_move") as mock_exec:

                # We can pass None as the PDF since execute_move is mocked
                move_pages(None, ["@my_plan.json"])

                assert mock_exec.called
                _, called_spec = mock_exec.call_args[0]
                assert called_spec.source_spec == "10"
                assert called_spec.mode == "after"
