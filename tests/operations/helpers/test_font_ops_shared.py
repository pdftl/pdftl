# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/helpers/test_font_ops_shared.py

"""
Unit tests for pdftl.operations.helpers.font_ops_shared.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from pdftl.operations.helpers.font_ops_shared import get_target_pages


def test_get_target_pages_empty_specs():
    """Verify that an empty page_specs list resolves to all valid pages in the PDF."""
    mock_pdf = MagicMock()
    mock_pdf.pages = [1, 2, 3]  # Simulating a 3-page document

    assert get_target_pages(mock_pdf, []) == [1, 2, 3]


def test_get_target_pages_with_specific_specs():
    """Line 45: non-empty page_specs resolves to the matching sorted page numbers."""
    from pdftl.operations.helpers.font_ops_shared import get_target_pages
    from unittest.mock import MagicMock

    mock_pdf = MagicMock()
    mock_pdf.pages = [1, 2, 3, 4, 5]  # 5-page document

    result = get_target_pages(mock_pdf, ["3", "1"])
    assert result == [1, 3]
