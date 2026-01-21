import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from pdftl.utils.arg_helpers import resolve_operation_spec

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
            result = resolve_operation_spec(args, mock_manual_parser, MockSpec)

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


# def test_load_valid_yaml():
#     """Line 68: Test successfully loading a YAML file."""
#     # Mock HAS_YAML to True
#     with patch("pdftl.utils.arg_helpers.HAS_YAML", True):
#         # We also need to mock the 'yaml' module usage inside the helper
#         mock_yaml = MagicMock()
#         mock_yaml.safe_load.return_value = {"source": "y_src", "target": "y_tgt"}

#         with patch("pdftl.utils.arg_helpers.yaml", mock_yaml):
#             with patch("pathlib.Path.exists", return_value=True):
#                 with patch("builtins.open", mock_open(read_data="source: y_src")):

#                     result = resolve_operation_spec(
#                         ["@config.yaml"], mock_manual_parser, model_class=None
#                     )

#                     assert result == {"source": "y_src", "target": "y_tgt"}
#                     mock_yaml.safe_load.assert_called_once()


# def test_yaml_import_error():
#     """Line 66-67: Test error when loading YAML without PyYAML."""
#     with patch("pdftl.utils.arg_helpers.HAS_YAML", False):
#         with patch("pathlib.Path.exists", return_value=True):
#             with patch("builtins.open", mock_open(read_data="")):
#                 with pytest.raises(ImportError) as exc:
#                     resolve_operation_spec(["@config.yaml"], mock_manual_parser, MockSpec)
#                 assert "PyYAML is required" in str(exc.value)


# def test_import_error_block():
#     """Line 10-11: Verify the top-level ImportError block logic."""
#     import importlib
#     import sys

#     original_yaml = sys.modules.get("yaml")

#     try:
#         sys.modules["yaml"] = None
#         import pdftl.utils.arg_helpers

#         importlib.reload(pdftl.utils.arg_helpers)
#         assert pdftl.utils.arg_helpers.HAS_YAML is False
#     finally:
#         if original_yaml:
#             sys.modules["yaml"] = original_yaml
#         importlib.reload(pdftl.utils.arg_helpers)


import json
import sys
from unittest.mock import MagicMock, mock_open, patch

import pytest

from pdftl.utils.arg_helpers import _load_spec_from_file


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


from pdftl.utils.arg_helpers import resolve_operation_spec


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
