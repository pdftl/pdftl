import json
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from pdftl.utils.arg_helpers import (
    _load_spec_from_file,
    resolve_operation_spec,
    expand_shorthand_args,
)

# --- Fixtures & Mocks ---


@dataclass
class MockSpec:
    source: str
    target: str

    # Used to test Line 80 (custom factory)
    @classmethod
    def from_dict(cls, data):
        return cls(source=data["source"] + "_from_dict", target=data["target"])


@dataclass
class SimpleSpec:
    """Used to test Line 83 (standard instantiation)."""

    source: str
    target: str


def mock_manual_parser(args):
    if len(args) >= 2:
        return MockSpec(source=args[0], target=args[1])
    return MockSpec(source="manual", target="manual")


# --- Tests ---


def test_resolve_uses_manual_parser_for_normal_args():
    """Ensure standard arguments are passed through to the manual parser."""
    args = ["old", "style"]
    result = resolve_operation_spec(args, mock_manual_parser, MockSpec)
    assert result == MockSpec(source="old", target="style")


def test_resolve_detects_json_file():
    """Ensure arguments starting with @ are treated as file paths."""
    file_content = json.dumps({"source": "json_src", "target": "json_tgt"})

    with patch("builtins.open", mock_open(read_data=file_content)) as mock_file:
        with patch("pathlib.Path.exists", return_value=True):
            args = ["@config.json"]
            _result = resolve_operation_spec(args, mock_manual_parser, MockSpec)

            f_args, f_kwargs = mock_file.call_args
            assert f_args[0] == Path("config.json")
            assert f_kwargs["encoding"] == "utf-8"


def test_resolve_direct_api_object():
    """Line 35: Test passing the object directly (API usage)."""
    spec = MockSpec(source="direct", target="api")
    result = resolve_operation_spec(spec, mock_manual_parser, MockSpec)
    assert result == spec


def test_resolve_invalid_input_type():
    """Line 50: Test passing something that isn't a list or the model."""
    with pytest.raises(TypeError) as exc:
        resolve_operation_spec(12345, mock_manual_parser, MockSpec)
    assert "Expected list of strings" in str(exc.value)


def test_resolve_file_not_found():
    """Line 61: Ensure error if file missing."""
    with patch("pathlib.Path.exists", return_value=False):
        with pytest.raises(FileNotFoundError):
            resolve_operation_spec(["@ghost.json"], mock_manual_parser)


def test_load_no_model_class():
    """Line 75: Test loading file when no model_class is provided (returns dict)."""
    file_content = json.dumps({"foo": "bar"})

    with patch("builtins.open", mock_open(read_data=file_content)):
        with patch("pathlib.Path.exists", return_value=True):
            result = resolve_operation_spec(["@data.json"], mock_manual_parser, model_class=None)
            assert result == {"foo": "bar"}


def test_load_using_from_dict():
    """Line 80: Explicit test for the from_dict logic."""
    file_content = json.dumps({"source": "A", "target": "B"})

    with patch("builtins.open", mock_open(read_data=file_content)):
        with patch("pathlib.Path.exists", return_value=True):
            result = resolve_operation_spec(["@data.json"], mock_manual_parser, MockSpec)
            assert result.source == "A_from_dict"


def test_load_simple_dataclass_no_factory():
    """Line 83: Test loading when class has NO from_dict method."""
    file_content = json.dumps({"source": "s", "target": "t"})

    with patch("builtins.open", mock_open(read_data=file_content)):
        with patch("pathlib.Path.exists", return_value=True):
            # Use SimpleSpec, which lacks from_dict
            result = resolve_operation_spec(["@data.json"], mock_manual_parser, SimpleSpec)

            assert isinstance(result, SimpleSpec)
            assert result.source == "s"  # No "_from_dict" appended


# A dummy class to test object conversion
class MockModel:
    def __init__(self, **kwargs):
        self.data = kwargs


class MockModelFactory:
    def __init__(self, **kwargs):
        # Store arguments so we can assert against them later
        self.data = kwargs

    @classmethod
    def from_dict(cls, data):
        # This passes arguments to __init__, so __init__ must exist!
        return cls(via_factory=True, **data)


def test_load_json_success():
    """Test loading a standard JSON file."""
    mock_data = '{"key": "value"}'

    with patch("builtins.open", mock_open(read_data=mock_data)):
        # We also mock pathlib.Path.exists to always return True
        with patch("pathlib.Path.exists", return_value=True):
            result = _load_spec_from_file("test.json")

    assert result == {"key": "value"}


def test_load_yaml_success():
    """Test loading a YAML file when PyYAML is present."""
    # Mock yaml module
    mock_yaml = MagicMock()
    mock_yaml.safe_load.return_value = {"yaml_key": "yaml_val"}

    # Simulate yaml being installed
    with patch.dict(sys.modules, {"yaml": mock_yaml}):
        with patch("builtins.open", mock_open(read_data="yaml_content")):
            with patch("pathlib.Path.exists", return_value=True):
                # Use a .yaml extension to trigger the yaml branch
                path = "config.yaml"
                result = _load_spec_from_file(path)

    assert result == {"yaml_key": "yaml_val"}
    mock_yaml.safe_load.assert_called_once()


def test_load_yaml_missing_dependency():
    """Test error raised when loading .yaml but PyYAML is not installed."""
    # Simulate yaml NOT being installed (ImportError)
    # We patch the specific import inside the function by patching sys.modules with None
    with patch.dict(sys.modules, {"yaml": None}):
        with patch("builtins.open", mock_open(read_data="...")):
            with patch("pathlib.Path.exists", return_value=True):
                # Verify it raises ImportError
                with pytest.raises(ImportError) as exc:
                    _load_spec_from_file("config.yaml")

                assert "pip install pyyaml" in str(exc.value)


def test_file_not_found():
    """Test that it raises FileNotFoundError if path doesn't exist."""
    with patch("pathlib.Path.exists", return_value=False):
        with pytest.raises(FileNotFoundError):
            _load_spec_from_file("ghost.json")


def test_convert_to_model_init():
    """Test converting dict to a class via standard __init__."""
    mock_data = '{"name": "tom"}'

    with patch("builtins.open", mock_open(read_data=mock_data)):
        with patch("pathlib.Path.exists", return_value=True):
            result = _load_spec_from_file("person.json", model_class=MockModel)

    assert isinstance(result, MockModel)
    assert result.data == {"name": "tom"}


def test_convert_to_model_factory():
    """Test converting dict to a class via from_dict factory."""
    mock_data = '{"name": "mickey"}'

    with patch("builtins.open", mock_open(read_data=mock_data)):
        with patch("pathlib.Path.exists", return_value=True):
            result = _load_spec_from_file("person.json", model_class=MockModelFactory)

    assert isinstance(result, MockModelFactory)
    assert result.data["via_factory"] is True


def test_resolve_operation_spec_passes_data():
    """Line 67: Verify parser receives data dict if the function signature accepts 2 args."""

    # 1. Define a dummy parser that requires two arguments
    #    (inspect.signature will see 'args' and 'data')
    def two_arg_parser(args, data):
        return {"args": args, "received_data": data}

    input_args = ["--some-flag"]
    input_data = {"context": "value"}

    # 2. Call resolve_operation_spec
    result = resolve_operation_spec(input_args, two_arg_parser, data=input_data)

    # 3. Assert correct data was passed through
    assert result["args"] == input_args
    assert result["received_data"] == input_data


def test_load_spec_bad_factory(tmp_path, caplog):
    """
    Covers line 114: Attribute 'from_dict' ... is not callable.
    """

    # Define a class with a non-callable from_dict
    class BadModel:
        from_dict = "I am a string, not a function"

        def __init__(self, x):
            self.x = x

    # Create a dummy JSON file
    f = tmp_path / "test.json"
    f.write_text('{"x": 1}', encoding="utf-8")

    # This should trigger the warning and fall back to __init__
    result = _load_spec_from_file(str(f), BadModel)

    assert isinstance(result, BadModel)
    assert result.x == 1
    assert "is not callable" in caplog.text


def test_load_spec_type_error(tmp_path):
    """
    Covers lines 122-123: except TypeError as e: raise TypeError(...)
    """

    # Define a strict class
    class StrictModel:
        def __init__(self, x):
            self.x = x

    # Create JSON with an unknown field 'y'
    f = tmp_path / "test.json"
    f.write_text('{"x": 1, "y": 2}', encoding="utf-8")

    # Expect the wrapper TypeError
    with pytest.raises(TypeError, match="Failed to instantiate StrictModel"):
        _load_spec_from_file(str(f), StrictModel)


######################################################################
## Unit tests for expand_shorthand_args in pdftl.utils.arg_helpers
######################################################################

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A stub is_selector_func that treats tokens starting with a digit or known
# keywords as valid page selectors, mirroring what is_valid_page_spec does
# for the cases we care about.
_PAGE_SELECTORS = {"odd", "even", "end", "1", "2", "1-5", "1-end", "2-8even"}


def is_sel(token: str) -> bool:
    return token in _PAGE_SELECTORS


# ---------------------------------------------------------------------------
# Guardrail: already-parenthesized args are returned unchanged
# ---------------------------------------------------------------------------


def test_passthrough_when_single_arg_has_parens():
    args = ["1-5(fit,10pt)"]
    assert expand_shorthand_args(args, is_sel) == args


def test_passthrough_when_one_of_many_args_has_parens():
    args = ["1-3(fit)", "4-end(1cm)"]
    assert expand_shorthand_args(args, is_sel) == args


def test_passthrough_when_non_selector_arg_has_parens():
    # Even if the first token looks like a selector, a later paren means hands off
    args = ["1-5", "fit(something)"]
    assert expand_shorthand_args(args, is_sel) == args


# ---------------------------------------------------------------------------
# Guardrail: empty input
# ---------------------------------------------------------------------------


def test_empty_args_returned_unchanged():
    assert expand_shorthand_args([], is_sel) == []


# ---------------------------------------------------------------------------
# Options-only (no selector): wraps everything in bare parens
# ---------------------------------------------------------------------------


def test_options_only_single():
    assert expand_shorthand_args(["fit"], is_sel) == ["(fit)"]


def test_options_only_multiple():
    assert expand_shorthand_args(["fit", "10pt"], is_sel) == ["(fit,10pt)"]


def test_options_only_key_value():
    assert expand_shorthand_args(["threshold=0.01", "dpi=72"], is_sel) == [
        "(threshold=0.01,dpi=72)"
    ]


def test_options_only_single_key_value():
    assert expand_shorthand_args(["threshold=0"], is_sel) == ["(threshold=0)"]


# ---------------------------------------------------------------------------
# Selector + options: wraps options inside selector(...)
# ---------------------------------------------------------------------------


def test_selector_with_single_option():
    assert expand_shorthand_args(["1-5", "fit"], is_sel) == ["1-5(fit)"]


def test_selector_with_multiple_options():
    assert expand_shorthand_args(["2-8even", "a5", "preview"], is_sel) == ["2-8even(a5,preview)"]


def test_selector_with_key_value_options():
    assert expand_shorthand_args(["even", "threshold=0.01", "dpi=72"], is_sel) == [
        "even(threshold=0.01,dpi=72)"
    ]


def test_selector_only_no_options():
    # Selector with nothing after it produces selector()
    # This is the edge case flagged in review — tests document current behaviour.
    assert expand_shorthand_args(["1-5"], is_sel) == ["1-5()"]


# ---------------------------------------------------------------------------
# is_selector_func injection
# ---------------------------------------------------------------------------


def test_custom_selector_func_never_matches():
    """With a selector func that always returns False, first token becomes an option."""
    args = ["1-5", "fit"]
    result = expand_shorthand_args(args, is_selector_func=lambda _: False)
    assert result == ["(1-5,fit)"]


def test_custom_selector_func_always_matches():
    """With a selector func that always returns True, first token is always the selector."""
    args = ["anything", "opt1", "opt2"]
    result = expand_shorthand_args(args, is_selector_func=lambda _: True)
    assert result == ["anything(opt1,opt2)"]


# ---------------------------------------------------------------------------
# Output is always a list of exactly one string
# ---------------------------------------------------------------------------


def test_output_is_single_element_list_options_only():
    result = expand_shorthand_args(["a4"], is_sel)
    assert isinstance(result, list)
    assert len(result) == 1


def test_output_is_single_element_list_with_selector():
    result = expand_shorthand_args(["1", "a4"], is_sel)
    assert isinstance(result, list)
    assert len(result) == 1


def test_resolve_spec_from_json_file_with_factory(monkeypatch):
    """
    Covers Line 80: Verifies that resolve_operation_spec calls the custom
    `from_dict` factory method on the spec class if it exists.
    """
    mock_json_data = {"source": "json_file_source", "target": "json_file_target"}

    # Mock file reading and existence checks
    mock_file = mock_open(read_data=json.dumps(mock_json_data))
    monkeypatch.setattr("builtins.open", mock_file)
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)

    # Passing an argument starting with '@' triggers file loading logic
    result = resolve_operation_spec(["@config.json"], mock_manual_parser, MockSpec)

    # MockSpec.from_dict appends "_from_dict" to the source attribute
    assert result == MockSpec(source="json_file_source_from_dict", target="json_file_target")


def test_resolve_spec_from_json_file_standard_init(monkeypatch):
    """
    Covers Line 83: Verifies that resolve_operation_spec falls back to standard
    instantiation if the target class lacks a `from_dict` method.
    """
    mock_json_data = {"source": "json_file_source", "target": "json_file_target"}

    mock_file = mock_open(read_data=json.dumps(mock_json_data))
    monkeypatch.setattr("builtins.open", mock_file)
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)

    # SimpleSpec does NOT have a from_dict classmethod
    result = resolve_operation_spec(["@config.json"], mock_manual_parser, SimpleSpec)

    assert result == SimpleSpec(source="json_file_source", target="json_file_target")


# Create dummy classes to mock the expected type names.
# This avoids needing to import the actual pipeline classes if they are in a different module.
class InlineSubPipeline:
    pass


class EachSubPipeline:
    pass


def test_expand_shorthand_args_invalid_types():
    """Covers lines 128-137: Type checking and custom error hints for pipeline objects."""

    # 1. Test InlineSubPipeline hint (Lines 130-134)
    with pytest.raises(TypeError) as exc_info:
        expand_shorthand_args(["valid_string", InlineSubPipeline()])

    error_msg = str(exc_info.value)
    assert "Unexpected object of type 'InlineSubPipeline'" in error_msg
    assert "Maybe you forgot to assign your inline pipeline to an input handle?" in error_msg

    # 2. Test EachSubPipeline hint (Lines 135-136)
    with pytest.raises(TypeError) as exc_info:
        expand_shorthand_args([EachSubPipeline()])

    error_msg = str(exc_info.value)
    assert "Unexpected object of type 'EachSubPipeline'" in error_msg
    assert "Using EACH in that position does not seem to make sense." in error_msg

    # 3. Test generic fallback for other non-string types (Line 128, 137-139)
    with pytest.raises(TypeError) as exc_info:
        expand_shorthand_args([123, 456])

    error_msg = str(exc_info.value)
    assert "Unexpected object of type 'int'" in error_msg
    assert "Maybe you forgot" not in error_msg
    assert "Using EACH" not in error_msg
