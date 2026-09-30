"""Helpers shared by the main window tests."""

import threading

import pikepdf
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication

from pdftl.gui import keymap, window_stages
from pdftl.gui.interfaces import InputFile, Pipeline, ResultKind, StageResult


def _make_pdf(path, widths, password=None):
    pdf = pikepdf.new()
    for w in widths:
        pdf.add_blank_page(page_size=(w, 100))
    enc = pikepdf.Encryption(owner=password, user=password) if password else False
    pdf.save(path, encryption=enc)
    return path


def _pages(path):
    with pikepdf.open(path) as pdf:
        return [(int(p.mediabox[2]), int(p.obj.get("/Rotate", 0))) for p in pdf.pages]


def _keep_active(qtbot, widget):
    """A headless macOS runner can leave the window inactive (a closed completer
    popup does not hand activation back); a desktop would, so the tests do too.
    Qt moves focus only within the active window."""
    window = widget.window()
    if not window.isActiveWindow():
        window.activateWindow()
        qtbot.waitUntil(window.isActiveWindow, timeout=2000)


def press(qtbot, seq: str):
    """Send `seq` (portable QKeySequence text) to the current focus widget."""
    widget = QApplication.focusWidget()
    assert widget is not None, f"no focus widget to receive {seq!r}"
    _keep_active(qtbot, widget)
    qtbot.keySequence(widget, QKeySequence(seq))
    return widget


def type_text(qtbot, text: str):
    widget = QApplication.focusWidget()
    assert widget is not None, f"no focus widget to receive {text!r}"
    _keep_active(qtbot, widget)
    qtbot.keyClicks(widget, text)


def fire(qtbot, action_id: str):
    """Press the first key of a `keymap` action on the focus widget."""
    press(qtbot, keymap.BY_ID[action_id].keys[0])


def replace_selection(qtbot, text: str):
    """Select all in the focused editable widget, then type `text` over it."""
    press(qtbot, "Ctrl+A")
    type_text(qtbot, text)


def _focus_widget_is(window, widget) -> bool:
    focused = QApplication.focusWidget()
    assert focused is widget, (
        f"focus is on {focused!r}; current box {window.current!r}; "
        f"window active: {window.isActiveWindow()}"
    )
    return True


def _with_second_input(window, path):
    window.pipeline = Pipeline(
        inputs=(*window.pipeline.inputs, InputFile("B", path)), stages=window.pipeline.stages
    )


def _use_runner(window, runner):
    """Make `runner` the window's current one. The window's own runner is stopped
    first: a QThread whose last reference goes while it runs aborts the process."""
    window.stop_runner()
    window.runner = runner


class _SlowEngine:
    """Blocks until cancelled, to exercise Runner.stop()'s real cancellation path."""

    def run(self, pipeline, cancel):
        cancel.wait(5)
        return
        yield  # pragma: no cover - makes this a generator function

    def preview_input(self, item):  # pragma: no cover - unused here
        raise NotImplementedError

    def save(self, pipeline, output, cancel):  # pragma: no cover - unused here
        raise NotImplementedError

    def close(self):  # pragma: no cover - unused here
        pass


def _run_args(qtbot, window, box, args):
    box.args.setText(args)
    window.run_pipeline()
    qtbot.waitUntil(lambda: "page" in box.status.text(), timeout=15000)


def _enabled(window, action_id):
    return window.actions_by_id[action_id].isEnabled()


def _top_in_viewport(window, box):
    return box.mapTo(window.scroll.viewport(), QPoint(0, 0)).y()


def _expected_top(window, box):
    bar = window.scroll.verticalScrollBar()
    top = box.mapTo(window.stages_widget, QPoint(0, 0)).y() - window_stages.REVEAL_MARGIN
    return (
        window_stages.REVEAL_MARGIN
        if top <= bar.maximum()
        else window_stages.REVEAL_MARGIN + top - bar.maximum()
    )


def _focus_stage(qtbot, window, box):
    box.args.setFocus()
    qtbot.waitUntil(lambda: window.current is box)


def _mnemonic(text: str) -> str | None:
    i = text.replace("&&", "").find("&")
    return text.replace("&&", "")[i + 1].upper() if i >= 0 else None


class _GatedEngine:
    """Finishes stage 1 when `first` is set and the rest when `rest` is set."""

    def __init__(self):
        self.first, self.rest = threading.Event(), threading.Event()

    def run(self, pipeline, cancel):
        self.first.wait(5)
        yield StageResult(index=0, kind=ResultKind.TEXT, key="k0")
        self.rest.wait(5)
        for i in range(1, len(pipeline.stages)):
            yield StageResult(index=i, kind=ResultKind.TEXT, key=f"k{i}")

    def close(self):
        pass


def _gap_between(upper, lower) -> int:
    return (upper.geometry().bottom() + lower.geometry().top()) // 2


def _settle(qtbot, window):
    qtbot.waitUntil(lambda: all(b.isVisible() for b in window.boxes))
    QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)


def _menu_texts(window, box):
    shown = []
    window.run_menu = lambda menu, _pos: shown.append(
        [(a.text(), a.isEnabled(), a.isCheckable() and a.isChecked()) for a in menu.actions()]
    )
    window._show_box_menu(box, QPoint(0, 0))
    return shown[0]
