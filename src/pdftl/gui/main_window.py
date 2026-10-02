# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/main_window.py

"""The pdftl GUI main window: inputs, stages, command bar, console and help."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QDockWidget,
    QLabel,
    QMainWindow,
    QMenu,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pdftl.gui import op_policy, viewers, widgets
from pdftl.gui import window_dialogs as dialogs
from pdftl.gui.about import app_icon
from pdftl.gui.command_bar import CommandBar
from pdftl.gui.console import AnsiConsole
from pdftl.gui.engine import SubprocessEngine
from pdftl.gui.history import History, Snapshot
from pdftl.gui.icons import tooltip
from pdftl.gui.interfaces import OpKind, Pipeline
from pdftl.gui.stage_list import StageListGestures
from pdftl.gui.thumbs import PdfiumThumbProvider
from pdftl.gui.viewers import Viewer
from pdftl.gui.preview_dialog import PreviewDialog
from pdftl.gui.widgets import StageBox, StripBox
from pdftl.gui.window_files import FilesMixin
from pdftl.gui.window_help import HelpMixin
from pdftl.gui.window_history import HistoryMixin, UndoKeysToWindow
from pdftl.gui.window_inputs import InputsMixin
from pdftl.gui.window_menus import MenusMixin, exec_menu
from pdftl.gui.window_outputs import OutputsMixin
from pdftl.gui.window_recent import RecentMixin
from pdftl.gui.window_run import RunMixin, Runner, Saver
from pdftl.gui.window_stages import StagesMixin, run_picker
from pdftl.gui.zoom import Zoom, ZoomArea

GEOMETRY_KEY = "window/geometry"
STATE_KEY = "window/state"


class MainWindow(
    MenusMixin,
    InputsMixin,
    StagesMixin,
    HistoryMixin,
    FilesMixin,
    RecentMixin,
    RunMixin,
    OutputsMixin,
    HelpMixin,
    QMainWindow,
):
    """Dialogs and the URL opener are attributes so tests can replace them."""

    ask_save_path: Callable[..., str] = staticmethod(dialogs.ask_save_path)
    ask_open_paths: Callable[..., list[str]] = staticmethod(dialogs.ask_open_paths)
    ask_text: Callable[..., str] = staticmethod(dialogs.ask_text)
    ask_password: Callable[..., str | None] = staticmethod(dialogs.ask_password)
    ask_pipeline_open_path: Callable[..., str] = staticmethod(dialogs.ask_pipeline_open_path)
    ask_pipeline_save_path: Callable[..., str] = staticmethod(dialogs.ask_pipeline_save_path)
    ask_discard_pipeline: Callable[..., str] = staticmethod(dialogs.ask_discard_pipeline)
    discover_viewers: Callable[[], list[Viewer]] = staticmethod(viewers.discover)

    def __init__(self, files: list[str] | tuple[str, ...] = (), engine=None) -> None:
        super().__init__()
        self.setWindowTitle("pdftl")
        infos = op_policy.all_ops()
        self.infos = {o.name: o for o in infos}
        self.ops = [o.name for o in infos if o.kind is not OpKind.BLOCKED]
        self.engine = engine or SubprocessEngine()
        self.pipeline = Pipeline()
        self.save_target: Path | None = None
        self.pipeline_path: Path | None = None
        self._pipeline_saved: Snapshot | None = None
        self.runner: Runner | None = None
        self._closed = False
        self.saver: Saver | None = None
        self._declined: set[Path] = set()
        self.preview: PreviewDialog | None = None
        self.boxes: list[StageBox] = []
        self.actions_by_id: dict[str, QAction] = {}
        self._announced = False
        self._command = ""
        self._help_op: str | None = None
        self._help_return: QWidget | None = None

        self.current: StripBox | None = None
        self.history = History(Snapshot(Pipeline()))
        self._restoring = False
        self._undo_keys = UndoKeysToWindow(self)
        self.setWindowIcon(app_icon())
        self.run_dialog: Callable[[QDialog], object] = QDialog.exec
        self.run_menu: Callable[[QMenu, QPoint], object] = exec_menu
        self.run_picker = run_picker
        self.thumbs = PdfiumThumbProvider(passwords=self._password_for)
        self._build_inputs()
        self._build_output()
        self._build_layout()
        self._build_help()
        self.followed: dict[StageBox, tuple[Path, str | None]] = {}
        self._failure_message = ""
        self._follow_serial = 0
        self.debounce = QTimer(self, singleShot=True, interval=700)
        self.debounce.timeout.connect(self.run_pipeline)
        self._build_busy()
        self._build_menus()
        self.zoom = Zoom(
            [
                ZoomArea("console", "Console", self.console),
                ZoomArea("help", "Help", self.help),
                ZoomArea("stages", "Pipeline", self.centralWidget()),
            ]
        )
        self._restore_layout()
        self._update_actions()
        self.show_help("")
        self.statusBar().messageChanged.connect(self._unwarn)
        self.statusBar().showMessage("Ctrl+O add a PDF, Ctrl+N add a stage, Ctrl+/ all keys")
        for f in files:
            self.add_file(f)
        self.add_stage()
        self.history = History(self._snapshot())
        self._update_undo_actions()
        self._update_title()

    def _build_layout(self) -> None:
        self.stages_widget = QWidget()
        self.stages_layout = QVBoxLayout(self.stages_widget)
        self.stages_layout.addWidget(self.inputs_box)
        self.stages_layout.addWidget(self.output_box)
        self.stages_layout.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.stages_widget)
        self.gestures = StageListGestures(
            self.stages_widget,
            self.scroll,
            lambda: self.boxes,
            self.insert_stage,
            lambda box, at: self.move_box_to(box, at, reveal=False),
        )
        self.cli = CommandBar()
        self.cli.submitted.connect(self.apply_command)
        self.cli.failed.connect(self._command_failed)
        self.cli.discarded.connect(self._command_discarded)
        self.cli.textEdited.connect(
            lambda _text: self.statusBar().showMessage(
                "Press Enter to apply the edited command, Esc to revert", 8000
            )
        )
        self.cli.setToolTip(
            tooltip(
                "The pipeline as a pdftl command line; edit it and press Enter", "focus_command"
            )
        )
        self.console = AnsiConsole()
        self.console.setPlaceholderText("stdout and stderr of every stage appear here")
        self.console.setToolTip("Messages and text output from every stage")
        top = QWidget()
        top_layout = QVBoxLayout(top)
        label = QLabel("Co&mmand (Enter applies, Ctrl+Shift+C copies):")
        label.setBuddy(self.cli)
        top_layout.addWidget(label)
        top_layout.addWidget(self.cli)
        top_layout.addWidget(self.scroll)
        self.setCentralWidget(top)
        self.console_dock = QDockWidget(tooltip("Console", "focus_console"))
        self.console_dock.setObjectName("console")
        self.console_dock.setWidget(self._console_panel())
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.console_dock)
        self.resizeDocks([self.console_dock], [200], Qt.Orientation.Vertical)

    def _restore_layout(self) -> None:
        settings = widgets.settings_factory()
        self.zoom.restore(settings)
        geometry, state = settings.value(GEOMETRY_KEY), settings.value(STATE_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)
        if state is not None:
            self.restoreState(state)

    def _save_layout(self) -> None:
        settings = widgets.settings_factory()
        settings.setValue(GEOMETRY_KEY, self.saveGeometry())
        settings.setValue(STATE_KEY, self.saveState())
        self.zoom.save(settings)

    def closeEvent(self, event) -> None:
        if not self._confirm_discard_pipeline():
            event.ignore()
            return
        # A run started after this would outlive the window, and Qt aborts
        # when a still-running QThread is destroyed.
        self._closed = True
        self._save_layout()
        self.debounce.stop()
        self.stop_runner()
        if self.saver is not None:
            self.saver.stop()
        self.engine.close()
        self.thumbs.close()
        super().closeEvent(event)
