# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/app.py

"""Start the GUI: `pdftl gui [file.pdf ...]` or `python -m pdftl.gui`.

A single `.yaml`/`.yml` argument opens as a saved pipeline (see `pipeline_file`)
instead of as an input file. `--args file.yaml` does not do this: pdftl's own
`main()` expands `--args` before this operation ever sees argv, splicing the
file's flattened content in as this operation's own arguments (so `output` in
the file is even consumed as this operation's `output`, not passed through).
There is no way to intercept that from here; pass the bare filename instead.
"""

from __future__ import annotations

import signal
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from pdftl.gui.about import app_icon
from pdftl.gui.main_window import MainWindow

_PIPELINE_SUFFIXES = (".yaml", ".yml")


def _pipeline_arg(files: Sequence[str]) -> str | None:
    """The single file to open as a pipeline, or None to open `files` as inputs."""
    if len(files) == 1 and Path(files[0]).suffix.lower() in _PIPELINE_SUFFIXES:
        return files[0]
    return None


SIGNAL_POLL_MS = 200


def close_on_sigint(window: MainWindow) -> None:
    """Ctrl+C in the terminal closes `window` (asking to save as usual); a second
    Ctrl+C kills the process. The timer lets Python run the handler: signals are
    only seen between bytecodes, never inside Qt's event loop."""

    def handler(_signum, _frame) -> None:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        window.close()

    signal.signal(signal.SIGINT, handler)
    poll = QTimer(window, interval=SIGNAL_POLL_MS)
    poll.timeout.connect(lambda: None)
    poll.start()


def run(files: Sequence[str] = (), exec_app: bool = True) -> int:
    """Open a window on `files` and run the event loop; returns its exit code."""
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName("pdftl")
    app.setWindowIcon(app_icon())
    pipeline_path = _pipeline_arg(files)
    window = MainWindow([] if pipeline_path else list(files))
    if pipeline_path is not None:
        window._load_pipeline_file(Path(pipeline_path))
    window.resize(1100, 900)
    window.show()
    if not exec_app:
        window.close()
        return 0
    close_on_sigint(window)
    return app.exec()


def main() -> None:
    sys.exit(run(sys.argv[1:]))
