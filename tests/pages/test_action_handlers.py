from unittest.mock import MagicMock

import pytest
from pikepdf import Name

# --- Import module and functions to test ---
from pdftl.pages.action_handlers import (
    ACTION_HANDLERS,
    DEFAULT_ACTION_HANDLER,
    _handle_goto_action,
    _handle_self_contained_action,
    _handle_unsupported_action,
)

# --- Import the class we need to mock ---
from pdftl.pages.link_remapper import LinkRemapper

# --- Fixtures ---


@pytest.fixture
def mock_remapper():
    """
    Creates a mock LinkRemapper instance with a spec.
    Using a spec ensures that the tests will fail if the underlying
    LinkRemapper class changes its method names.
    """
    return MagicMock(spec=LinkRemapper)


# --- Test Cases ---


def test_handle_goto_action(mock_remapper):
    """
    Tests that _handle_goto_action correctly delegates
    to remapper.remap_goto_action.
    """
    mock_action = MagicMock()
    _handle_goto_action(mock_remapper, mock_action)

    # Assert the correct method was called
    mock_remapper.remap_goto_action.assert_called_once_with(mock_action)

    # Assert other methods were not called
    mock_remapper.copy_self_contained_action.assert_not_called()
    mock_remapper.copy_unsupported_action.assert_not_called()


def test_handle_self_contained_action(mock_remapper):
    """
    Tests that _handle_self_contained_action correctly delegates
    to remapper.copy_self_contained_action.
    """
    mock_action = MagicMock()
    _handle_self_contained_action(mock_remapper, mock_action)

    # Assert the correct method was called
    mock_remapper.copy_self_contained_action.assert_called_once_with(mock_action)

    # Assert other methods were not called
    mock_remapper.remap_goto_action.assert_not_called()
    mock_remapper.copy_unsupported_action.assert_not_called()


def test_handle_unsupported_action(mock_remapper):
    """
    Tests that _handle_unsupported_action correctly delegates
    to remapper.copy_unsupported_action.
    """
    mock_action = MagicMock()
    _handle_unsupported_action(mock_remapper, mock_action)

    # Assert the correct method was called
    mock_remapper.copy_unsupported_action.assert_called_once_with(mock_action)

    # Assert other methods were not called
    mock_remapper.remap_goto_action.assert_not_called()
    mock_remapper.copy_self_contained_action.assert_not_called()


def test_default_action_handler_alias():
    """
    Tests that DEFAULT_ACTION_HANDLER is an alias for
    _handle_unsupported_action.
    """
    assert DEFAULT_ACTION_HANDLER is _handle_unsupported_action


@pytest.mark.parametrize(
    "action_name, expected_handler",
    [
        (Name.GoTo, _handle_goto_action),
        (Name.GoToR, _handle_self_contained_action),
        (Name.Launch, _handle_self_contained_action),
        (Name.URI, _handle_self_contained_action),
        (Name.Sound, _handle_self_contained_action),
        (Name.JavaScript, _handle_unsupported_action),
        (Name.SubmitForm, _handle_unsupported_action),
        (Name.ResetForm, _handle_unsupported_action),
        (Name.ImportData, _handle_unsupported_action),
    ],
)
def test_action_handlers_mapping(action_name, expected_handler):
    """
    Tests that the ACTION_HANDLERS dictionary maps the correct
    pikepdf.Name to the correct handler function.
    """
    assert ACTION_HANDLERS[action_name] is expected_handler


def test_action_handlers_total_count():
    """
    Tests that the ACTION_HANDLERS dictionary has the expected
    number of entries. This is a sanity check to ensure no
    new handlers were added without being added to the
    parametrized test.
    """
    expected_count = 9
    assert len(ACTION_HANDLERS) == expected_count
