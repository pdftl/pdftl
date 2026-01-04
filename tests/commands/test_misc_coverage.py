# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import io
import logging
from unittest.mock import MagicMock, PropertyMock, patch

import pikepdf
import pytest

from pdftl.commands.add_text import _build_static_context, add_text_pdf
from pdftl.commands.crop import _apply_crop_rule_to_page
from pdftl.commands.dump_annots import (
    _data_item_to_string_helper,
    dump_data_annots_cli_hook,
)
from pdftl.commands.modify_annots import modify_annots
from pdftl.core.types import OpResult
from pdftl.utils.dimensions import get_visible_page_dimensions

# --- ADD_TEXT MOPPING ---


def test_add_text_metadata_failure(caplog):
    """Mops lines 171-173: Metadata read failure handling."""
    mock_pdf = MagicMock()
    # Trigger a TypeError when accessing docinfo
    type(mock_pdf).docinfo = PropertyMock(side_effect=TypeError("Corrupt Info"))
    mock_pdf.filename = "test.pdf"
    mock_pdf.pages = [1, 2, 3]

    with caplog.at_level(logging.WARNING):
        ctx = _build_static_context(mock_pdf, 3)
        assert ctx["metadata"] == {}
        assert "Could not read PDF metadata" in caplog.text


def test_add_text_no_rules():
    """Mops line 229: Return early if no rules are parsed."""
    mock_pdf = MagicMock()
    mock_pdf.pages = [1]
    # Passing empty specs or specs that result in no rules
    result = add_text_pdf(mock_pdf, [])
    assert result.success is True
    assert result.pdf == mock_pdf


# --- CROP MOPPING ---


def test_crop_invalid_mediabox(caplog):
    """Mops lines 220-221 and 115: Handling pages with invalid MediaBox."""
    mock_page = MagicMock()
    # Return something that causes a TypeError in float conversion
    mock_page.mediabox = [None, "abc", 100, 100]
    mock_page.cropbox = mock_page.mediabox

    dims = get_visible_page_dimensions(mock_page)
    assert dims is None

    mock_pdf = MagicMock()
    mock_pdf.pages = [mock_page]

    with caplog.at_level(logging.WARNING):
        _apply_crop_rule_to_page("rule", 0, mock_pdf, False, None, {})
        assert "no valid MediaBox" in caplog.text


def test_crop_negative_dimensions_skip(caplog):
    """Mops lines 123-124: Skipping crops that result in zero/negative area."""
    mock_page = MagicMock()
    mock_page.mediabox = [0, 0, 100, 100]
    mock_page.cropbox = mock_page.mediabox
    mock_pdf = MagicMock()
    mock_pdf.pages = [mock_page]

    # Mock calculation to return None (invalid dimensions)
    with patch("pdftl.commands.crop._calculate_new_box", return_value=None):
        with caplog.at_level(logging.WARNING):
            _apply_crop_rule_to_page("rule", 0, mock_pdf, False, None, {})
            assert "zero or negative dimensions" in caplog.text


# --- DUMP_ANNOTS MOPPING ---


def test_dump_data_annots_empty_report(caplog):
    """Mops lines 123-124: Warning when no data is available for report."""
    result = OpResult(success=True, data=None, meta={})
    with caplog.at_level(logging.WARNING):
        dump_data_annots_cli_hook(result, "stage")
        assert "No data available" in caplog.text


def test_dump_annots_string_convert_fallback():
    """Mops lines 256-257: Default identity function if string_convert is None."""
    # Passing None should trigger the internal definition of identity function
    line = _data_item_to_string_helper("Key", "Value", "Prefix", None)
    assert "PrefixKey: Value" in line


# --- MODIFY_ANNOTS MOPPING ---


def test_modify_annots_execution():
    """Mops lines 83-84, 107-110: Logic for applying annotation modifications."""
    # Create a real mini PDF with a link annotation
    out = io.BytesIO()
    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(100, 100))
        # Create a basic link
        link = pikepdf.Dictionary(
            Type=pikepdf.Name.Annot,
            Subtype=pikepdf.Name.Link,
            Rect=[0, 0, 10, 10],
            Border=[1, 1, 1],
        )
        pdf.pages[0].Annots = pdf.make_indirect([link])

        # specs_to_modification_rules will be called inside modify_annots
        # Format: selector(Key=Value)
        specs = ["1/Link(Border=[0 0 0])"]
        result = modify_annots(pdf, specs)

        assert result.success is True
        # Verify the border was changed
        mod_link = pdf.pages[0].Annots[0]
        assert mod_link.Border == [0, 0, 0]


def test_modify_annots_invalid_spec():
    """Mops error handling for annotation specs."""
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        # Missing parenthesis or malformed
        with pytest.raises(Exception):
            modify_annots(pdf, ["1/Link Border=0"])
