# tests/utils/images/test_quality_search.py
#
# Ground truth: settings 0..n-1 with a known first passing index, and a count
# of the attempts made.

import math

import pytest

from pdftl.utils.images.quality_search import lowest_passing


def _search(n, first_pass):
    tried = []

    def attempt(setting):
        tried.append(setting)
        return f"result {setting}" if setting >= first_pass else None

    return lowest_passing(list(range(n)), attempt), tried


@pytest.mark.parametrize("n", [1, 2, 3, 5, 8, 17])
def test_finds_the_first_passing_setting_within_the_attempt_bound(n):
    for first_pass in range(n):
        result, tried = _search(n, first_pass)
        assert result == f"result {first_pass}"
        assert len(tried) <= 2 + math.ceil(math.log2(n)) if n > 1 else len(tried) == 1


def test_a_passing_first_setting_is_the_only_attempt():
    result, tried = _search(10, 0)
    assert (result, tried) == ("result 0", [0])


def test_first_then_last_before_bisecting():
    _, tried = _search(10, 6)
    assert tried[:2] == [0, 9]


@pytest.mark.parametrize("n", [1, 2, 7])
def test_nothing_passing_gives_none_after_first_and_last(n):
    result, tried = _search(n, n)
    assert result is None
    assert tried == ([0] if n == 1 else [0, n - 1])


def test_no_settings_gives_none_without_an_attempt():
    tried = []
    assert lowest_passing([], tried.append) is None
    assert tried == []
