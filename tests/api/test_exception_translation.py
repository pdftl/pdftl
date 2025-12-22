from unittest.mock import MagicMock, patch

import pytest

from pdftl import api
from pdftl.exceptions import MissingArgumentError, UserCommandLineError


@patch("pdftl.core.executor.registry")
def test_api_wraps_cli_errors(mock_registry):
    """Ensure CLI-specific errors are translated to standard Python errors."""

    def side_effect(*args, **kwargs):
        raise MissingArgumentError("Missing input file")

    # Mock an operation that fails with a CLI error
    mock_op = MagicMock()
    mock_op.function = side_effect
    mock_op.args = ([], {}, {})
    mock_registry.operations = {"fail_op": mock_op}

    # API user should see a TypeError, not a MissingArgumentError
    with pytest.raises(TypeError) as excinfo:
        api.fail_op()

    assert "missing required argument" in str(excinfo.value)


@patch("pdftl.core.executor.registry")
def test_api_wraps_user_errors(mock_registry):
    """Ensure UserCommandLineError becomes ValueError."""

    def side_effect(*args, **kwargs):
        raise UserCommandLineError("Bad range")

    mock_op = MagicMock()
    mock_op.function = side_effect
    mock_op.args = ([], {}, {})
    mock_registry.operations = {"bad_op": mock_op}

    with pytest.raises(ValueError) as excinfo:
        api.bad_op()

    assert "Invalid operation parameters" in str(excinfo.value)
