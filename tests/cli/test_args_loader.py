# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/cli/test_args_loader.py

import pytest

from pdftl.cli.args_loader import expand_args, flatten_and_stringify, load_yaml_args
from pdftl.exceptions import InvalidArgumentError, UserCommandLineError


def test_flatten_and_stringify_basic():
    assert flatten_and_stringify(["a", "b"]) == ["a", "b"]


def test_flatten_and_stringify_nested():
    assert flatten_and_stringify(["a", ["b", "c"]]) == ["a", "b", "c"]


def test_flatten_and_stringify_scalars():
    assert flatten_and_stringify([1, True, 3.14]) == ["1", "True", "3.14"]


def test_flatten_and_stringify_none():
    assert flatten_and_stringify([None, "a"]) == ["a"]


def test_flatten_and_stringify_rejects_dict():
    with pytest.raises(UserCommandLineError, match="YAML dictionaries are not supported"):
        flatten_and_stringify([{"a": "b"}])


def test_flatten_and_stringify_dict_at_root():
    with pytest.raises(UserCommandLineError, match="YAML dictionaries are not supported"):
        flatten_and_stringify({"a": "b"})


def test_load_yaml_args_basic(tmp_path):
    f = tmp_path / "args.yml"
    f.write_text("- in.pdf\n- cat\n- output\n- out.pdf", encoding="utf-8")
    assert load_yaml_args(str(f)) == ["in.pdf", "cat", "output", "out.pdf"]


def test_load_yaml_args_multiple_docs(tmp_path):
    f = tmp_path / "args.yml"
    f.write_text("- in.pdf\n- cat\n---\n- rotate\n- right", encoding="utf-8")
    assert load_yaml_args(str(f)) == ["in.pdf", "cat", "---", "rotate", "right"]


def test_load_yaml_args_forgot_hyphens(tmp_path):
    f = tmp_path / "args.yml"
    f.write_text("in.pdf\ncat\noutput\nout.pdf", encoding="utf-8")
    with pytest.raises(UserCommandLineError, match="parsed as a single string"):
        load_yaml_args(str(f))


def test_load_yaml_args_dict_error_wrapper(tmp_path):
    f = tmp_path / "args.yml"
    f.write_text("- valid\n- key: value", encoding="utf-8")
    with pytest.raises(UserCommandLineError, match="Error in pipeline segment 1"):
        load_yaml_args(str(f))


def test_load_yaml_args_file_not_found():
    with pytest.raises(UserCommandLineError, match="Arguments file not found"):
        load_yaml_args("nonexistent_file.yml")


def test_load_yaml_args_directory(tmp_path):
    with pytest.raises(UserCommandLineError, match="Arguments file not found"):
        load_yaml_args(str(tmp_path))


def test_load_yaml_args_os_error(tmp_path, monkeypatch):
    f = tmp_path / "args.yml"
    f.write_text("- in.pdf", encoding="utf-8")

    def fake_open(*args, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr("builtins.open", fake_open)
    with pytest.raises(UserCommandLineError, match="Error reading args file"):
        load_yaml_args(str(f))


def test_load_yaml_args_yaml_error(tmp_path):
    f = tmp_path / "args.yml"
    f.write_text("- [unclosed list", encoding="utf-8")
    with pytest.raises(UserCommandLineError, match="Error parsing YAML args file"):
        load_yaml_args(str(f))


def test_missing_pyyaml_import(monkeypatch):
    import importlib.util

    real_find_spec = importlib.util.find_spec
    calls = []

    def fake_find_spec(name, package=None):
        calls.append(name)
        if name == "yaml":
            return None
        return real_find_spec(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    with pytest.raises(InvalidArgumentError, match="PyYAML"):
        load_yaml_args("dummy.yml")

    # Guard against this test silently no-op'ing if ensure_dependencies changes
    # its detection mechanism (e.g. to try/except ImportError instead of find_spec).
    assert "yaml" in calls, "ensure_dependencies didn't call find_spec('yaml') — test is a no-op"


def test_expand_args_basic(tmp_path):
    f = tmp_path / "args.yml"
    f.write_text("- in.pdf\n- cat", encoding="utf-8")
    args = ["--args", str(f), "output", "out.pdf"]
    assert expand_args(args) == ["in.pdf", "cat", "output", "out.pdf"]


def test_expand_args_recursive(tmp_path):
    f1 = tmp_path / "args1.yml"
    f2 = tmp_path / "args2.yml"
    # as_posix() gives a forward-slash path safe for unquoted YAML on all platforms
    f2_path_str = f2.as_posix()
    f1_content = f"- in.pdf\n- --args\n- {f2_path_str}"
    f1.write_text(f1_content, encoding="utf-8")
    f2.write_text("- cat", encoding="utf-8")

    args = ["--args", str(f1)]
    assert expand_args(args) == ["in.pdf", "cat"]


def test_expand_args_collects_expansions(tmp_path):
    f1 = tmp_path / "args1.yml"
    f2 = tmp_path / "args2.yml"
    f2_path_str = f2.as_posix()
    f1_content = f"- in.pdf\n- --args\n- {f2_path_str}"
    f1.write_text(f1_content, encoding="utf-8")
    f2.write_text("- cat", encoding="utf-8")

    expansions = []
    args = ["--args", str(f1)]
    expanded = expand_args(args, expansions=expansions)
    assert expanded == ["in.pdf", "cat"]
    # expand_args records exactly the string it read out of the args list/YAML file,
    # which is f2_path_str (posix form) here, not str(f2) (native form on Windows).
    assert expansions == [str(f1), f2_path_str]


def test_expand_args_max_depth(tmp_path):
    f = tmp_path / "args.yml"
    f.write_text(f"- --args\n- {f.as_posix()}", encoding="utf-8")
    args = ["--args", str(f)]
    with pytest.raises(UserCommandLineError, match="Maximum argument file inclusion depth"):
        expand_args(args)


def test_expand_args_missing_file_path():
    with pytest.raises(UserCommandLineError, match="without a target file path"):
        expand_args(["--args"])


def test_expand_args_nested_error_context(tmp_path):
    """Verifies that depth-level trace descriptions are raised when recursion fails."""
    f1 = tmp_path / "args1.yml"
    f2 = tmp_path / "args2.yml"
    f1_content = f"- in.pdf\n- --args\n- {f2.as_posix()}"
    f1.write_text(f1_content, encoding="utf-8")

    args = ["--args", str(f1)]
    with pytest.raises(
        UserCommandLineError, match="Error expanding.*depth 0.*Arguments file not found"
    ):
        expand_args(args)
