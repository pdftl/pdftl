# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/helpers/test_xobject_helpers.py

"""Tests for structural xobject extraction utility helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pikepdf

from pdftl.operations.helpers.xobject_helpers import read_xobject_stream


def test_read_xobject_stream_raw() -> None:
    """Verify raw bytes can be extracted directly without normalization."""
    mock_xobj = MagicMock()
    mock_xobj.read_bytes.return_value = b"q 1 0 0 1 0 0 cm EMC"

    content = read_xobject_stream(mock_xobj, normalize=False)
    assert content == b"q 1 0 0 1 0 0 cm EMC"
    mock_xobj.read_bytes.assert_called_once()


def test_read_xobject_stream_normalize_success() -> None:
    """Test normalized streams execute unparsing cycles safely."""
    mock_xobj = MagicMock()
    parsed_stream = [(pikepdf.Name("/Span"), {"/MCID": 1}, "BDC")]

    with (
        patch("pikepdf.parse_content_stream", return_value=parsed_stream),
        patch(
            "pikepdf.unparse_content_stream", return_value=b"/Span << /MCID 1 >> BDC"
        ) as mock_unparse,
    ):
        content = read_xobject_stream(mock_xobj, normalize=True)

    assert content == b"/Span << /MCID 1 >> BDC"
    mock_unparse.assert_called_once()


def test_read_xobject_stream_normalize_failure_fallback() -> None:
    """Check normalization errors fall back cleanly to raw stream streams."""
    mock_xobj = MagicMock()
    mock_xobj.read_bytes.return_value = b"raw_fallback_bytes"

    with patch(
        "pikepdf.parse_content_stream",
        side_effect=pikepdf.PdfError("Parse Failure"),
    ):
        content = read_xobject_stream(mock_xobj, normalize=True)

    assert content == b"raw_fallback_bytes"
