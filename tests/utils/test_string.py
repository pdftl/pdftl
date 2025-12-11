import pytest

from pdftl.utils.string import split_escaped


def test_split_escaped():
    def run_test(name, actual, expected):
        try:
            assert actual == expected
            print(f"  [PASS] {name}")
        except AssertionError:
            print(f"  [FAIL] {name}")
            print(f"    Expected: {expected}")
            print(f"    Got:      {actual}")
            return False
        return True

    tests = [
        ("Simple split", split_escaped("a,b,c", ","), ["a", "b", "c"]),
        ("Escaped delimiter", split_escaped("a,b\\,c,d", ","), ["a", "b,c", "d"]),
        ("Escaped backslash", split_escaped("a,b\\\\,c", ","), ["a", r"b\,c"]),
        ("Trailing delimiter", split_escaped("a,b,", ","), ["a", "b", ""]),
        ("Escaped trailing", split_escaped("a,b\\,", ","), ["a", "b,"]),
        ("Double escape", split_escaped("a\\\\,b\\,c", ","), [r"a\,b,c"]),
        ("Complex sequence", split_escaped("a\\\\\\.b,c", "."), ["a\\.b,c"]),
        ("Empty string", split_escaped("", ","), [""]),
        ("Only delimiter", split_escaped(",", ","), ["", ""]),
    ]

    if all(tests):
        print("All tests passed!")
    else:
        print("\nSome tests failed.")


def test_split_escaped_value_error():
    with pytest.raises(ValueError):
        split_escaped("", "ab")
