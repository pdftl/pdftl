# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/parsers/test_add_images_parser.py

from __future__ import annotations

import pytest
from pdftl.operations.parsers.add_images_parser import parse_add_images_rules, _parse_add_images_op
from pdftl.exceptions import InvalidArgumentError


def test_parse_add_images_rules_defaults():
    """Verifies that parsing a basic image rule with default values works cleanly."""
    rules = parse_add_images_rules(["/logo.png/"], total_pages=5)

    # Rule applies to all pages (0-based indices 0 to 4)
    assert len(rules) == 5
    for page_idx in range(5):
        page_rules = rules[page_idx]
        assert len(page_rules) == 1
        rule = page_rules[0]
        assert rule["images"] == ["logo.png"]
        assert rule["underlay"] is False
        assert rule["scale_mode"] == "none"
        assert rule["position"] == "bottom-left"
        assert rule["width"] is None
        assert rule["height"] is None
        assert rule["offset-x"] == "0"
        assert rule["offset-y"] == "0"
        assert rule["opacity"] == 1.0


def test_parse_add_images_rules_custom_values():
    """Verifies parsing of rule strings with a full set of custom options."""
    raw_rules = [
        "even!stamp.png!(underlay=true, scale_mode=fit, position=center, width=10cm, height=5cm, offset-x=2cm, offset-y=-1cm, opacity=0.75)"
    ]
    rules = parse_add_images_rules(raw_rules, total_pages=4)

    # Matched pages: "even" translates to pages 2 and 4 (0-based indices 1 and 3)
    assert set(rules.keys()) == {1, 3}
    for page_idx in (1, 3):
        page_rules = rules[page_idx]
        assert len(page_rules) == 1
        rule = page_rules[0]
        assert rule["images"] == ["stamp.png"]
        assert rule["underlay"] is True
        assert rule["scale_mode"] == "fit"
        assert rule["position"] == "mid-center"  # Center auto-normalizes to mid-center
        assert rule["width"] == "10cm"
        assert rule["height"] == "5cm"
        assert rule["offset-x"] == "2cm"
        assert rule["offset-y"] == "-1cm"
        assert rule["opacity"] == 0.75


def test_parse_add_images_rules_multiple_images():
    """Verifies that multiple image files separated by spaces or commas are parsed correctly."""
    raw_rules = ["1-2#img1.png, 'img2 space.png' img3.jpg#(underlay=true)"]
    rules = parse_add_images_rules(raw_rules, total_pages=2)

    assert len(rules) == 2
    for page_idx in range(2):
        rule = rules[page_idx][0]
        assert rule["images"] == ["img1.png", "img2 space.png", "img3.jpg"]
        assert rule["underlay"] is True


def test_parse_add_images_rules_invalid_spec():
    """Verifies that parse_add_images_rules raises descriptive ValueErrors on formatting errors."""
    # Empty images list
    with pytest.raises(ValueError, match="At least one image path must be provided"):
        parse_add_images_rules(["1-end//"], total_pages=2)

    # Invalid image path parsing (unclosed quote to trigger lines 80-81)
    with pytest.raises(ValueError, match="Could not parse image paths"):
        parse_add_images_rules(["/logo.png 'unclosed_quote/"], total_pages=2)

    # No valid image paths after cleaning (trigger line 84)
    with pytest.raises(ValueError, match="No valid image file paths found"):
        parse_add_images_rules(["/''/"], total_pages=2)

    # Invalid opacity float
    with pytest.raises(ValueError, match="Invalid opacity value"):
        parse_add_images_rules(["/logo.png/(opacity=invalid)"], total_pages=2)

    # Unknown options (raises InvalidArgumentError)
    with pytest.raises(InvalidArgumentError, match="unknown parameter"):
        parse_add_images_rules(["/logo.png/(unknown_key=val)"], total_pages=2)

    # Unmatched delimiter
    with pytest.raises(ValueError, match="Unmatched images delimiter"):
        parse_add_images_rules(["/logo.png#"], total_pages=2)

    # Options block not enclosed in parentheses (triggers line 96)
    # Direct invocation of the internal parser helper is required because the public-facing
    # split_delimited_rule parser filters options by checking if they end with ')' first.
    with pytest.raises(ValueError, match="Options block must be enclosed in parentheses"):
        _parse_add_images_op("logo.png", "underlay=true")

    # TODO: Refactor split_delimited_rule or raw parse streams to improve this error message.
    # Currently, passing /logo.png/underlay=true fails early inside the delimiter-sniffing
    # block with "Invalid images delimiter 'e'" rather than warning about missing parentheses.
    with pytest.raises(ValueError, match="Invalid images delimiter"):
        parse_add_images_rules(["/logo.png/underlay=true"], total_pages=2)
