"""Tests for the global Rich console."""

from unittest.mock import MagicMock, patch

import pytest

import pdftl.cli.console as console_module


@pytest.fixture(autouse=True)
def reset_console():
    """Ensure each test starts with no global console."""
    console_module._CONSOLE = None
    yield
    console_module._CONSOLE = None


def test_get_console_returns_none_without_creating():
    """create_if_none=False returns None when no console exists."""
    assert console_module.get_console(create_if_none=False) is None


def test_get_console_creates_default_console(monkeypatch):
    """A normal environment creates a default Rich Console."""
    monkeypatch.delenv("FORCE_COLOR", raising=False)

    mock_console = MagicMock()

    with patch("rich.console.Console", return_value=mock_console) as console_class:
        result = console_module.get_console()

    assert result is mock_console
    console_class.assert_called_once_with()
    assert console_module._CONSOLE is mock_console


def test_get_console_creates_forced_color_console(monkeypatch):
    """FORCE_COLOR creates a terminal-forced standard-color console."""
    monkeypatch.setenv("FORCE_COLOR", "1")

    mock_console = MagicMock()

    with patch("rich.console.Console", return_value=mock_console) as console_class:
        result = console_module.get_console()

    assert result is mock_console
    console_class.assert_called_once_with(
        color_system="standard",
        force_terminal=True,
        legacy_windows=False,
    )
    assert console_module._CONSOLE is mock_console


def test_get_console_reuses_existing_console(monkeypatch):
    """An existing global console is returned without creating another."""
    existing_console = MagicMock()
    console_module._CONSOLE = existing_console

    monkeypatch.setenv("FORCE_COLOR", "1")

    with patch("rich.console.Console") as console_class:
        result = console_module.get_console()

    assert result is existing_console
    console_class.assert_not_called()


def test_get_console_does_not_create_when_existing_console_and_create_false():
    """create_if_none=False also preserves an existing console."""
    existing_console = MagicMock()
    console_module._CONSOLE = existing_console

    result = console_module.get_console(create_if_none=False)

    assert result is existing_console
