# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Tests for pdftl/utils/keyval_parser.py — 100% coverage target."""

import pytest

from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.keyval_parser import (
    parse_keyval_list,
    parse_keyval_string,
    parse_keyval_token,
)

# ---------------------------------------------------------------------------
# parse_keyval_token
# ---------------------------------------------------------------------------


class TestParseKeyvalToken:
    def test_basic_pair(self):
        assert parse_keyval_token("threshold=0.01") == ("threshold", "0.01")

    def test_value_with_equals_splits_on_first(self):
        assert parse_keyval_token("key=val=ue") == ("key", "val=ue")

    def test_whitespace_stripped(self):
        assert parse_keyval_token("  key = val  ") == ("key", "val")

    def test_keys_lowercased_by_default(self):
        k, _ = parse_keyval_token("Threshold=0.01")
        assert k == "threshold"

    def test_values_not_lowercased_by_default(self):
        _, v = parse_keyval_token("mode=RGB")
        assert v == "RGB"

    def test_lowercase_keys_false(self):
        k, _ = parse_keyval_token("Threshold=0.01", lowercase_keys=False)
        assert k == "Threshold"

    def test_lowercase_values_true(self):
        _, v = parse_keyval_token("mode=RGB", lowercase_values=True)
        assert v == "rgb"

    def test_missing_equals_raises(self):
        with pytest.raises(InvalidArgumentError, match="missing '='"):
            parse_keyval_token("noequalssign")

    def test_allowed_keys_valid(self):
        assert parse_keyval_token("dpi=72", allowed_keys=["dpi", "mode"]) == ("dpi", "72")

    def test_allowed_keys_invalid_raises(self):
        with pytest.raises(InvalidArgumentError, match="unknown parameter 'bad'"):
            parse_keyval_token("bad=1", allowed_keys=["dpi"])

    def test_allowed_keys_error_lists_expected(self):
        with pytest.raises(InvalidArgumentError, match="dpi"):
            parse_keyval_token("bad=1", allowed_keys=["dpi"])

    def test_allowed_keys_none_is_permissive(self):
        assert parse_keyval_token("anything=goes") == ("anything", "goes")

    def test_context_in_missing_equals_error(self):
        with pytest.raises(InvalidArgumentError, match="delete_images: missing"):
            parse_keyval_token("noequalssign", context="delete_images")

    def test_context_in_unknown_key_error(self):
        with pytest.raises(InvalidArgumentError, match="delete_blank: unknown"):
            parse_keyval_token("bad=1", allowed_keys=["good"], context="delete_blank")

    def test_no_context_no_prefix(self):
        with pytest.raises(InvalidArgumentError) as exc_info:
            parse_keyval_token("noequalssign")
        assert not str(exc_info.value).startswith(":")


# ---------------------------------------------------------------------------
# parse_keyval_list
# ---------------------------------------------------------------------------


class TestParseKeyvalList:
    def test_empty_list(self):
        assert parse_keyval_list([]) == {}

    def test_single_token(self):
        assert parse_keyval_list(["threshold=0.01"]) == {"threshold": "0.01"}

    def test_multiple_tokens(self):
        result = parse_keyval_list(["threshold=0.01", "dpi=72"])
        assert result == {"threshold": "0.01", "dpi": "72"}

    def test_bare_token_raises_when_none(self):
        with pytest.raises(InvalidArgumentError, match="missing '='"):
            parse_keyval_list(["bare"])

    def test_bare_token_skipped_when_true(self):
        result = parse_keyval_list(["bare", "dpi=72"], bare_tokens=True)
        assert result == {"dpi": "72"}

    def test_bare_token_collected_when_list(self):
        bare = []
        result = parse_keyval_list(["bare", "dpi=72"], bare_tokens=bare)
        assert result == {"dpi": "72"}
        assert bare == ["bare"]

    def test_bare_tokens_list_empty_when_no_bare(self):
        bare = []
        result = parse_keyval_list(["dpi=72"], bare_tokens=bare)
        assert result == {"dpi": "72"}
        assert bare == []

    def test_all_bare_collected(self):
        bare = []
        result = parse_keyval_list(["a", "b", "c"], bare_tokens=bare)
        assert result == {}
        assert bare == ["a", "b", "c"]

    def test_all_bare_skipped_when_true(self):
        result = parse_keyval_list(["a", "b", "c"], bare_tokens=True)
        assert result == {}

    def test_kwargs_forwarded_to_token(self):
        result = parse_keyval_list(["Mode=RGB"], lowercase_values=True)
        assert result == {"mode": "rgb"}

    def test_allowed_keys_enforced(self):
        with pytest.raises(InvalidArgumentError, match="unknown parameter"):
            parse_keyval_list(["bad=1"], allowed_keys=["good"])


# ---------------------------------------------------------------------------
# parse_keyval_string
# ---------------------------------------------------------------------------


class TestParseKeyvalString:
    def test_empty_string_returns_empty(self):
        assert parse_keyval_string("") == {}

    def test_single_pair(self):
        assert parse_keyval_string("threshold=0.01") == {"threshold": "0.01"}

    def test_multiple_pairs(self):
        result = parse_keyval_string("threshold=0.01,dpi=72")
        assert result == {"threshold": "0.01", "dpi": "72"}

    def test_empty_tokens_ignored(self):
        result = parse_keyval_string(",threshold=0.01,")
        assert result == {"threshold": "0.01"}

    def test_kwargs_forwarded(self):
        result = parse_keyval_string("mode=RGB", lowercase_values=True)
        assert result == {"mode": "rgb"}

    def test_bare_token_raises_by_default(self):
        with pytest.raises(InvalidArgumentError):
            parse_keyval_string("noequalssign")

    def test_bare_token_skipped_when_true(self):
        result = parse_keyval_string("bare,dpi=72", bare_tokens=True)
        assert result == {"dpi": "72"}

    def test_bare_tokens_collected_when_list(self):
        bare = []
        result = parse_keyval_string("bare,dpi=72", bare_tokens=bare)
        assert result == {"dpi": "72"}
        assert bare == ["bare"]

    def test_allowed_keys_enforced(self):
        with pytest.raises(InvalidArgumentError, match="unknown parameter"):
            parse_keyval_string("bad=1", allowed_keys=["good"])
