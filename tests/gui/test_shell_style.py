import subprocess

import pytest

from pdftl.gui import shell_style


def test_default_style_posix(monkeypatch):
    monkeypatch.setattr(shell_style, "_WINDOWS", False)
    assert shell_style.default_style() == "posix"


def test_default_style_windows(monkeypatch):
    monkeypatch.setattr(shell_style, "_WINDOWS", True)
    assert shell_style.default_style() == "windows"


def test_split_posix():
    assert shell_style.split("'a b' c", "posix") == ["a b", "c"]


def test_split_unknown_style():
    with pytest.raises(ValueError, match="style"):
        shell_style.split("a", "fish")


def test_quote_unknown_style():
    with pytest.raises(ValueError, match="style"):
        shell_style.quote("a", "fish")


def test_join_posix_quotes_spaces():
    assert shell_style.join(["a b", "c"], "posix") == "'a b' c"


def test_quote_posix_round_trips_through_split():
    quoted = shell_style.quote("a b", "posix")
    assert shell_style.split(quoted, "posix") == ["a b"]


def test_quote_windows_matches_list2cmdline():
    assert shell_style.quote(r"C:\logo.pdf", "windows") == subprocess.list2cmdline(
        [r"C:\logo.pdf"]
    )
    assert shell_style.quote("a b", "windows") == subprocess.list2cmdline(["a b"])


def test_join_windows_matches_list2cmdline():
    args = [r"C:\Program Files\a.pdf", "cat", "1-2"]
    assert shell_style.join(args, "windows") == subprocess.list2cmdline(args)


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (["a b.pdf", "cat"], ["a b.pdf", "cat"]),
        (['weird""quote', "cat"], ['weird""quote', "cat"]),
        (["", "cat"], ["", "cat"]),
        (["back\\\\slash.pdf"], ["back\\\\slash.pdf"]),
        (["a b\\"], ["a b\\"]),
        ([], []),
        (["  a   b  "], ["a", "b"]),
    ],
)
def test_split_windows_matches_list2cmdline(tokens, expected):
    encoded = subprocess.list2cmdline(tokens) if tokens else ""
    if tokens == ["  a   b  "]:
        encoded = "  a   b  "
    assert shell_style.split_windows(encoded) == expected


def test_split_windows_backslash_run_not_before_quote_is_literal():
    assert shell_style.split_windows(r"a\b\c") == ["a\\b\\c"]


def test_split_windows_tab_separates_arguments():
    assert shell_style.split_windows("a\tb") == ["a", "b"]
