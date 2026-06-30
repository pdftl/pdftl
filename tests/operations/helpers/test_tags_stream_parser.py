# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/helpers/test_tags_stream_parser.py

"""Tests for the logical stream parser helper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pikepdf

from pdftl.operations.helpers.tags_stream_parser import (
    _build_mcid_stream_map,
    parse_stream_bytes_for_mcids,
)


def test_parse_stream_bytes_for_mcids_basic() -> None:
    """Test parsing clean stream bytes containing standard BDC, BMC, and EMC blocks including empty lines."""
    # Added \n\n to trigger line 37, and /Artifact BMC to trigger lines 48-49
    stream_bytes = b"/P << /MCID 0 >> BDC\n\n(Hello World) Tj\nEMC\n/Artifact BMC\nEMC\n/Span << /MCID 1 >> BDC\n(Nested) Tj\nEMC\n"

    mcid_blocks, lines = parse_stream_bytes_for_mcids(stream_bytes)

    assert len(lines) == 9
    assert 0 in mcid_blocks
    assert 1 in mcid_blocks

    block_0 = mcid_blocks[0]
    assert block_0["tag"] == "/P"
    assert block_0["start_line"] == 1
    assert block_0["end_line"] == 4
    assert block_0["lines"] == ["/P << /MCID 0 >> BDC", "", "(Hello World) Tj", "EMC"]

    block_1 = mcid_blocks[1]
    assert block_1["tag"] == "/Span"
    assert block_1["start_line"] == 7
    assert block_1["end_line"] == 9


def test_parse_stream_bytes_for_mcids_malformed_stack() -> None:
    """Verify stream parser handles mismatched BDC/EMC pairs gracefully."""
    # BDC without EMC
    stream_bytes_unclosed = b"/P << /MCID 10 >> BDC\n(Lost text) Tj\n"
    mcid_blocks, lines = parse_stream_bytes_for_mcids(stream_bytes_unclosed)

    assert len(lines) == 2
    assert 10 not in mcid_blocks  # Unclosed blocks are discarded

    # EMC without BDC
    stream_bytes_orphaned_emc = b"(Orphan) Tj\nEMC\n"
    mcid_blocks, lines = parse_stream_bytes_for_mcids(stream_bytes_orphaned_emc)
    assert not mcid_blocks


def test_build_mcid_stream_map_success() -> None:
    """Test builder maps page streams with valid page context."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_page = MagicMock()
    mock_pdf.pages = [mock_page]

    parsed_stream = [
        (pikepdf.Name("/P"), {"/MCID": 42}, "BDC"),
        ("Tj", "Text"),
        ("EMC",),
    ]

    with (
        patch("pikepdf.parse_content_stream", return_value=parsed_stream),
        patch(
            "pikepdf.unparse_content_stream", return_value=b"/P << /MCID 42 >> BDC\n(Text) Tj\nEMC"
        ),
    ):
        mcid_blocks, lines = _build_mcid_stream_map(mock_pdf, page_num=1, annotate=False)

    assert 42 in mcid_blocks
    assert mcid_blocks[42]["page"] == 1
    assert len(lines) == 3


def test_build_mcid_stream_map_parse_error() -> None:
    """Verify builder returns empty mappings on parsing failures."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_page = MagicMock()
    mock_pdf.pages = [mock_page]

    with patch(
        "pikepdf.parse_content_stream",
        side_effect=pikepdf.PdfError("Malformed stream"),
    ):
        mcid_blocks, lines = _build_mcid_stream_map(mock_pdf, page_num=1)

    assert mcid_blocks == {}
    assert lines == []


@patch("pdftl.operations.helpers.stream_annotator.annotate_stream")
def test_build_mcid_stream_map_annotate_success(mock_annotate) -> None:
    """Test builder maps page streams with annotate=True when annotation succeeds."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_page = MagicMock()
    mock_pdf.pages = [mock_page]

    parsed_stream = [
        (pikepdf.Name("/P"), {"/MCID": 42}, "BDC"),
        ("Tj", "Text"),
        ("EMC",),
    ]

    mock_annotate.return_value = b"/P << /MCID 42 >> BDC\n(Annotated Text) Tj\nEMC"

    with (
        patch("pikepdf.parse_content_stream", return_value=parsed_stream),
        patch(
            "pikepdf.unparse_content_stream", return_value=b"/P << /MCID 42 >> BDC\n(Text) Tj\nEMC"
        ),
    ):
        mcid_blocks, lines = _build_mcid_stream_map(mock_pdf, page_num=1, annotate=True)

    assert 42 in mcid_blocks
    assert mcid_blocks[42]["page"] == 1
    assert "(Annotated Text) Tj" in lines
    mock_annotate.assert_called_once()


@patch("pdftl.operations.helpers.stream_annotator.annotate_stream")
def test_build_mcid_stream_map_annotate_failure(mock_annotate) -> None:
    """Test builder handles exceptions inside the stream annotator gracefully."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_page = MagicMock()
    mock_pdf.pages = [mock_page]

    parsed_stream = [
        (pikepdf.Name("/P"), {"/MCID": 42}, "BDC"),
        ("Tj", "Text"),
        ("EMC",),
    ]

    # Force a ValueError to trigger the target catch block at lines 105-106
    mock_annotate.side_effect = ValueError("Annotation error")

    with (
        patch("pikepdf.parse_content_stream", return_value=parsed_stream),
        patch(
            "pikepdf.unparse_content_stream", return_value=b"/P << /MCID 42 >> BDC\n(Text) Tj\nEMC"
        ),
    ):
        mcid_blocks, lines = _build_mcid_stream_map(mock_pdf, page_num=1, annotate=True)

    # Shoud drop back seamlessly to using unannotated stream details
    assert 42 in mcid_blocks
    assert mcid_blocks[42]["page"] == 1
    assert "(Text) Tj" in lines


@patch("pdftl.operations.helpers.stream_annotator.annotate_stream")
def test_build_mcid_stream_map_annotate_line_count_mismatch(mock_annotate) -> None:
    """Test builder safely ignores annotation output if its line count doesn't match
    the unannotated stream (would otherwise misalign MCID block slicing)."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_page = MagicMock()
    mock_pdf.pages = [mock_page]

    parsed_stream = [
        (pikepdf.Name("/P"), {"/MCID": 42}, "BDC"),
        ("Tj", "Text"),
        ("EMC",),
    ]

    # Annotated output has fewer lines than the original — simulates
    # annotate_stream() collapsing or merging lines, which would otherwise
    # corrupt start_line/end_line-based slicing if used.
    mock_annotate.return_value = b"/P << /MCID 42 >> BDC\nEMC"

    with (
        patch("pikepdf.parse_content_stream", return_value=parsed_stream),
        patch(
            "pikepdf.unparse_content_stream", return_value=b"/P << /MCID 42 >> BDC\n(Text) Tj\nEMC"
        ),
    ):
        mcid_blocks, lines = _build_mcid_stream_map(mock_pdf, page_num=1, annotate=True)

    # Falls back to the unannotated lines/blocks rather than using the
    # mismatched annotated output.
    assert 42 in mcid_blocks
    assert "(Text) Tj" in lines
    assert lines == ["/P << /MCID 42 >> BDC", "(Text) Tj", "EMC"]
