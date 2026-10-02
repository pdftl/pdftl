# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/gui_op.py

"""The `gui` operation: open the desktop GUI."""

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import OperationError
from pdftl.utils.dependencies import ensure_dependencies

_GUI_LONG_DESC = """
Opens a desktop window for building pipelines interactively. Each stage
shows thumbnails of the pages it produces; select pages in any strip and
press Ctrl+E to turn the selection into a page spec for the next stage.
The command bar at the top shows the equivalent pdftl command and accepts
a typed or pasted one. Everything is keyboard operable; press Ctrl+/ in
the window for the full list of keys.

Any `<file>` arguments are opened as input files, except a single `.yaml`
or `.yml` argument, which opens as a saved pipeline (see Save Pipeline in
the GUI's File menu). Requires the `gui` extra (`pip install pdftl[gui]`).
"""


@register_operation(
    "gui",
    tags=["utility"],
    type="source operation",
    desc="Open the desktop GUI",
    long_desc=_GUI_LONG_DESC,
    usage="gui [<file>...]",
    examples=[
        {"cmd": "gui", "desc": "Open an empty window.", "test_example": False},
        {
            "cmd": "gui in.pdf other.pdf",
            "desc": "Open a window with two input files.",
            "test_example": False,
        },
    ],
    args=([c.OPERATION_ARGS], {}),
)
def gui_op(args: list[str]) -> OpResult:
    """Run the GUI until its window closes."""
    ensure_dependencies("gui", {"PySide6": "PySide6", "pypdfium2": "pypdfium2"}, "gui")
    from pdftl.gui.app import run

    code = run(args)
    if code != 0:
        raise OperationError(f"The GUI exited with status {code}")
    return OpResult(success=True)
