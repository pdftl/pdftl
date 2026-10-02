# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/test_gui_op.py

"""Tests for pdftl.operations.gui_op: the `gui` operation."""

import importlib.util

import pytest

pytest.importorskip("PySide6")

# Importing the real submodule here binds it on `pdftl.gui`'s parent package
# dict; without this, `pdftl.gui` resolves through pdftl's dynamic operation
# `__getattr__` instead (since "gui" is also a registered operation name),
# and any later attribute access on `pdftl.gui.app` breaks.
import pdftl.gui.app as gui_app_module
from pdftl.core.core_types import OpResult
from pdftl.exceptions import InvalidArgumentError, OperationError
from pdftl.operations.gui_op import gui_op


def test_missing_pyside6_raises_with_install_hint(monkeypatch):
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a: None if name == "PySide6" else real_find_spec(name, *a),
    )

    with pytest.raises(InvalidArgumentError, match=r"pdftl\[gui\]"):
        gui_op(["a.pdf"])


def test_runs_app_run_with_given_args_and_succeeds(monkeypatch):
    seen = {}

    def fake_run(files, exec_app=True):
        seen["files"] = list(files)
        return 0

    monkeypatch.setattr(gui_app_module, "run", fake_run)

    result = gui_op(["a.pdf", "b.pdf"])

    assert seen["files"] == ["a.pdf", "b.pdf"]
    assert result == OpResult(success=True)


def test_nonzero_exit_code_raises_operation_error(monkeypatch):
    monkeypatch.setattr(gui_app_module, "run", lambda files, exec_app=True: 3)

    with pytest.raises(OperationError, match="exited with status 3"):
        gui_op(["a.pdf"])


def test_cli_invokes_app_run_with_positional_files(monkeypatch):
    from pdftl.cli.main import main

    seen = {}

    def fake_run(files, exec_app=True):
        seen["files"] = list(files)
        return 0

    monkeypatch.setattr(gui_app_module, "run", fake_run)

    code = main(["pdftl", "gui", "a.pdf"])

    assert code == 0
    assert seen["files"] == ["a.pdf"]


def test_cli_nonzero_run_code_yields_nonzero_exit(monkeypatch):
    from pdftl.cli.main import main

    monkeypatch.setattr(gui_app_module, "run", lambda files, exec_app=True: 3)

    code = main(["pdftl", "gui", "a.pdf"])

    assert code != 0


def test_help_gui_mentions_file_args_usage(monkeypatch):
    import io
    import sys

    from rich.console import Console

    from pdftl.cli import console as console_mod
    from pdftl.cli.main import main

    # With `-s`, stdout can be a terminal (it is on Windows CI workers): help then
    # pages, and a console cached there emits colour codes.
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    plain = Console(file=out, width=100, color_system=None, legacy_windows=False)
    monkeypatch.setattr(console_mod, "_CONSOLE", plain)

    main(["pdftl", "help", "gui"])

    assert "gui [<file>...]" in out.getvalue()
