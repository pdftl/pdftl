# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/widgets.py

"""Page strips and stage editors."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import shiboken6
from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    QRect,
    QSettings,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QIcon,
    QImage,
    QKeyEvent,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QComboBox,
    QCompleter,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from pdftl.core.constants import PAPER_SIZES
from pdftl.gui.interfaces import Stage, plural
from pdftl.gui.box_frame import FAILED_STATUS_STYLE, BoxFrame
from pdftl.gui.icons import delete_icon, themed, tool_button, tooltip
from pdftl.gui.pagesel import pages_to_spec
from pdftl.gui.thumbs import PdfiumThumbProvider

THUMB = 110
LAZY_MARGIN = 20
VERTICAL_MARGIN = 200
"""Pixels of slack around the enclosing scroll viewport that still count as visible."""

_HIDE_PAGES_KEY = "stage_strip/hide_new_by_default"


def _default_settings() -> QSettings:
    return QSettings("pdftl", "gui")


settings_factory: Callable[[], QSettings] = _default_settings
"""Injectable QSettings source; tests replace this to keep off the user's real config."""


def default_pages_hidden() -> bool:
    """Whether a newly created stage/strip starts with its page strip hidden."""
    return bool(settings_factory().value(_HIDE_PAGES_KEY, False, type=bool))


def set_default_pages_hidden(hidden: bool) -> None:
    """Persist the default from `default_pages_hidden` for future stages."""
    settings_factory().setValue(_HIDE_PAGES_KEY, hidden)


_PAGES_ICONS = {
    False: ("view-hidden", QStyle.StandardPixmap.SP_TitleBarShadeButton),
    True: ("view-visible", QStyle.StandardPixmap.SP_TitleBarUnshadeButton),
}
"""The Hide/Show pages button's icon, keyed by whether pages are hidden."""


def _placeholder() -> QIcon:
    pixmap = QPixmap(THUMB, int(THUMB * 1.3))
    pixmap.fill(QColor(210, 210, 210))
    return QIcon(pixmap)


_MM_PER_POINT = 25.4 / 72


def _paper_name(width_pt: float, height_pt: float) -> str | None:
    """Name of the first `PAPER_SIZES` entry matching this size, within 1pt."""
    for name, (w, h) in PAPER_SIZES.items():
        if abs(w - width_pt) < 1 and abs(h - height_pt) < 1:
            return name.upper()
    return None


def _size_label(width_pt: float, height_pt: float) -> str:
    mm_w = round(width_pt * _MM_PER_POINT)
    mm_h = round(height_pt * _MM_PER_POINT)
    label = f"{mm_w} × {mm_h} mm"
    name = _paper_name(width_pt, height_pt)
    return f"{label} ({name})" if name else label


class WheelSafeCombo(QComboBox):
    """Ignores the wheel, so scrolling the window past it never changes the op.

    The open dropdown list is a separate view and still scrolls.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        if self.isEditable():
            QTimer.singleShot(0, self.lineEdit().selectAll)


class PageStrip(QListWidget):
    """Horizontal page list. Plain wheel scrolls the page; Shift+wheel scrolls the strip.

    Plain Space toggles the current page's selection (Ctrl+Space does the
    same natively; Qt's own binding for plain Space just reselects the
    current item alone, which arrow-key navigation already does). Return
    and Enter activate the current page on every platform.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False)
        self.setMovement(QListView.Movement.Static)
        self.setIconSize(QSize(THUMB, int(THUMB * 1.5)))
        self.setFixedHeight(int(THUMB * 1.5) + 48)
        self.setGridSize(QSize(THUMB + 12, int(THUMB * 1.5) + 24))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

    def focusInEvent(self, event) -> None:
        """Keep "no current page" on plain focus-in; Qt otherwise auto-picks row 0."""
        had_current = self.currentIndex().isValid()
        super().focusInEvent(event)
        if not had_current:
            self.setCurrentIndex(QModelIndex())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        plain = event.modifiers() in (
            Qt.KeyboardModifier.NoModifier,
            Qt.KeyboardModifier.KeypadModifier,
        )
        item = self.currentItem()
        if plain and event.key() == Qt.Key.Key_Space:
            if item is not None:
                item.setSelected(not item.isSelected())
            return
        if plain and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # macOS item views treat Return as "edit", not "activate".
            if item is not None:
                self.itemActivated.emit(self._run_start(item))
            return
        super().keyPressEvent(event)

    def _run_start(self, item: QListWidgetItem) -> QListWidgetItem:
        """The first page of the selected run holding `item`, or `item` if unselected."""
        row = self.row(item)
        while item.isSelected() and row > 0 and self.item(row - 1).isSelected():
            row -= 1
        return self.item(row)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta()
        bar = self.horizontalScrollBar()
        if delta.x():
            bar.setValue(bar.value() - delta.x())
        elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            bar.setValue(bar.value() - delta.y())
        else:
            event.ignore()


class StripBox(BoxFrame):
    """A titled page strip; its selection is offered as a page spec."""

    edited = Signal()
    view_requested = Signal(object)
    export_requested = Signal(object)
    save_text_requested = Signal(object)
    follow_requested = Signal(object)
    output_changed = Signal()
    preview_requested = Signal(object, int)

    def __init__(
        self,
        title: str,
        provider: PdfiumThumbProvider,
        handle: str = "",
        draggable: bool = False,
    ) -> None:
        super().__init__(title, draggable)
        self.provider = provider
        self.handle = handle
        self.total = 0
        self.key: str | None = None
        self.path: Path | None = None
        self.text = ""
        self.items: dict[int, QListWidgetItem] = {}
        self.requested: set[int] = set()
        self._page_pt_sizes: dict[int, tuple[float, float]] = {}
        self._scale = 1.0
        """Pixels per point for this strip's thumbnails; set once page sizes are known."""
        self._scroll_area: QAbstractScrollArea | None = None
        self.placeholder = _placeholder()
        self.layout_ = QVBoxLayout(self)
        self.status = QLabel("")
        style = self.style()
        self.view = tool_button(
            themed(style, "document-open", QStyle.StandardPixmap.SP_FileDialogContentsView),
            "View in PDF viewer",
            "view_stage",
        )
        self.export = tool_button(
            themed(style, "document-save-as", QStyle.StandardPixmap.SP_DialogSaveButton),
            "Save as...",
            "export_stage",
        )
        self.save_text = tool_button(
            themed(style, "text-x-generic", QStyle.StandardPixmap.SP_FileIcon), "Save text..."
        )
        self.follow = tool_button(
            themed(style, "view-refresh", QStyle.StandardPixmap.SP_BrowserReload),
            "Follow in PDF viewer",
            "follow_stage",
            checkable=True,
        )
        self.pages_hidden = False
        """UI-only: not part of undo snapshots or saved pipeline files."""
        self.focus_fallback: QWidget | None = None
        """Focused by a click on the box while its page strip is hidden."""
        self.hide_pages = tool_button(
            themed(style, *_PAGES_ICONS[False]), "Hide pages", None, True
        )
        self.hide_pages.toggled.connect(self._set_pages_hidden)
        self.view.clicked.connect(lambda: self.view_requested.emit(self))
        self.export.clicked.connect(lambda: self.export_requested.emit(self))
        self.save_text.clicked.connect(lambda: self.save_text_requested.emit(self))
        self.follow.clicked.connect(lambda: self.follow_requested.emit(self))
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        for button in (self.view, self.follow, self.export, self.save_text):
            button.setEnabled(False)
            status_row.addWidget(button)
        status_row.addWidget(self.hide_pages)
        self.save_text.hide()
        self.follow.hide()
        self.strip = PageStrip()
        self.strip.setToolTip(
            "Click, Shift+click or Ctrl+click pages to select them; Enter opens a large preview"
        )
        self.spec = QLabel("Selection: (none)")
        self.spec.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.spec.setToolTip("The selected pages as a page spec (Ctrl+E inserts it)")
        self.title_row = QHBoxLayout()
        self.title_row.addWidget(self.title, 1)
        self.layout_.addLayout(self.title_row)
        self.layout_.addLayout(status_row)
        self.layout_.addWidget(self.strip)
        self.layout_.addWidget(self.spec)
        self.strip.itemSelectionChanged.connect(self._update_spec)
        self.strip.horizontalScrollBar().valueChanged.connect(self._request_visible)
        self.strip.currentItemChanged.connect(self._request_near_current)
        self.strip.itemActivated.connect(
            lambda item: self.preview_requested.emit(self, item.data(Qt.ItemDataRole.UserRole))
        )
        if default_pages_hidden():
            self.hide_pages.setChecked(True)
        provider.thumb_ready.connect(self._thumb_ready)
        provider.sizes_ready.connect(self._sizes_ready)
        self.install_click_filters()

    def show_pdf(
        self,
        path: Path | None,
        count: int = 0,
        status: str = "",
        key: str | None = None,
        text: str = "",
    ) -> None:
        """Show `count` pages of `path`; unchanged `key` keeps the rendered thumbnails."""
        if key is None or key != self.key:
            self._reload(path, count, key)
        self.key, self.path, self.text = key, path, text
        self.set_failed(False)
        self.status.setText(status or plural(self.total, "page"))
        has_pdf = path is not None
        self.view.setEnabled(has_pdf)
        self.export.setEnabled(has_pdf)
        self.follow.setEnabled(has_pdf or self.follow.isChecked())
        self.save_text.setVisible(bool(text.strip()))
        self.save_text.setEnabled(bool(text.strip()))
        self._update_spec()
        self.output_changed.emit()

    def show_failure(self, status: str) -> None:
        """Clear the pages and show `status` as this box's error."""
        self.show_pdf(None, status=status)
        self.set_failed(True)

    def set_failed(self, failed: bool) -> None:
        super().set_failed(failed)
        self.status.setStyleSheet(FAILED_STATUS_STYLE if failed else "")

    def focus_target(self) -> QWidget:
        if self.pages_hidden:
            return self.focus_fallback or self.hide_pages
        return self.strip

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._wire_scroll_area()
        self._request_visible()

    def hideEvent(self, event) -> None:
        """Drop the scroll-area wiring: `hide()` precedes deletion in the pipeline."""
        super().hideEvent(event)
        self._unwire_scroll_area()

    def eventFilter(self, watched: QObject, event) -> bool:
        if not shiboken6.isValid(self):
            return False
        if watched not in self._click_filtered and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
        ):
            self._request_visible()
        return super().eventFilter(watched, event)

    def filters_child(self, child: QWidget) -> bool:
        """The strip handles its own clicks."""
        return child is not self.strip and not self.strip.isAncestorOf(child)

    def _wire_scroll_area(self) -> None:
        """Watch the enclosing scroll area (if any) so its scroll/resize re-requests."""
        area = self._find_scroll_area()
        if area is None or area is self._scroll_area:
            return
        self._scroll_area = area
        area.verticalScrollBar().valueChanged.connect(self._request_visible)
        area.viewport().installEventFilter(self)

    def _unwire_scroll_area(self) -> None:
        area, self._scroll_area = self._scroll_area, None
        if area is None or not shiboken6.isValid(area):
            return
        area.verticalScrollBar().valueChanged.disconnect(self._request_visible)
        area.viewport().removeEventFilter(self)

    def _find_scroll_area(self) -> QAbstractScrollArea | None:
        parent = self.parentWidget()
        while parent is not None and not isinstance(parent, QAbstractScrollArea):
            parent = parent.parentWidget()
        return parent

    def _in_viewport(self) -> bool:
        """Whether this box is (near) visible in its enclosing scroll area, if any."""
        area = self._find_scroll_area()
        if area is None:
            return True
        viewport = area.viewport()
        top_left = self.mapTo(viewport, QPoint(0, 0))
        box_rect = QRect(top_left, self.size())
        visible_rect = viewport.rect().adjusted(0, -VERTICAL_MARGIN, 0, VERTICAL_MARGIN)
        return box_rect.intersects(visible_rect)

    def _set_pages_hidden(self, hidden: bool) -> None:
        self.pages_hidden = hidden
        self.strip.setVisible(not hidden)
        label = "Show pages" if hidden else "Hide pages"
        self.hide_pages.setText(label)
        self.hide_pages.setAccessibleName(label)
        self.hide_pages.setToolTip(label)
        self.hide_pages.setIcon(themed(self.style(), *_PAGES_ICONS[hidden]))
        if not hidden:
            self._request_visible()

    def _reload(self, path: Path | None, count: int, key: str | None) -> None:
        if self.key is not None:
            self.provider.cancel(self.key)
        self.strip.clear()
        self.items = {}
        self.requested = set()
        self.total = count if path is not None else 0
        self.key, self.path = key, path
        self._page_pt_sizes = {}
        self._scale = 1.0
        if path is None or key is None:
            return
        for page in range(1, count + 1):
            item = QListWidgetItem(self.placeholder, str(page))
            item.setData(Qt.ItemDataRole.UserRole, page)
            self.strip.addItem(item)
            self.items[page] = item
        self.provider.request_sizes(key, path, count)
        self._request_visible()

    def _sizes_ready(self, key: str, sizes: dict[int, tuple[float, float]]) -> None:
        """One scale for the whole strip: the largest page fills the usual thumbnail box.

        Pages already rendered under the pre-scale fallback width are
        re-requested so they redraw true to size.
        """
        if key != self.key or not sizes:
            return
        self._page_pt_sizes = sizes
        for page, item in self.items.items():
            size = sizes.get(page)
            if size is not None:
                item.setToolTip(f"Page {page} — {_size_label(*size)}")
        max_w = max(w for w, _h in sizes.values())
        max_h = max(h for _w, h in sizes.values())
        if max_w <= 0 or max_h <= 0:
            return
        self._scale = min(THUMB / max_w, (THUMB * 1.5) / max_h)
        icon_w = max(1, round(max_w * self._scale))
        icon_h = max(1, round(max_h * self._scale))
        self.strip.setIconSize(QSize(icon_w, icon_h))
        self.strip.setGridSize(QSize(icon_w + 12, icon_h + 24))
        self.strip.setFixedHeight(icon_h + 48)
        # Renders are served newest first, so a queued fallback-width one would
        # land after, and overwrite, its true-size replacement.
        self.provider.narrow(key, set())
        self.requested = set()
        self._request_visible()

    def _request(self, key: str, path: Path, pages) -> None:
        for page in pages:
            if page in self.items and page not in self.requested:
                self.requested.add(page)
                size = self._page_pt_sizes.get(page)
                width_px = max(1, round(size[0] * self._scale)) if size else THUMB
                self.provider.request(key, path, page, width_px)

    def _request_around(self, first: int, last: int) -> None:
        if self.pages_hidden or self.key is None or self.path is None or not self._in_viewport():
            return
        wanted = {
            page
            for page in range(first - LAZY_MARGIN, last + LAZY_MARGIN + 1)
            if page in self.items
        }
        self.provider.narrow(self.key, wanted)
        self.requested &= wanted
        self._request(self.key, self.path, sorted(wanted, reverse=True))

    def _request_visible(self, *_args) -> None:
        """Only pages of a visible strip, intersecting the strip's own viewport, render.

        Uses the horizontal scrollbar and the strip's fixed grid cell width
        rather than `itemAt`, whose hit-testing needs a layout pass that has
        not necessarily run yet (e.g. right after a fresh reload).
        """
        if not shiboken6.isValid(self) or self.pages_hidden or not self.items:
            return
        if not self._in_viewport():
            return
        bar = self.strip.horizontalScrollBar()
        cell = self.strip.gridSize().width() or 1
        first = bar.value() // cell + 1
        last = (bar.value() + self.strip.viewport().width()) // cell + 1
        self._request_around(first, last)

    def _request_near_current(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current is not None:
            page = current.data(Qt.ItemDataRole.UserRole)
            self._request_around(page, page)

    def _thumb_ready(self, key: str, page: int, image: QImage) -> None:
        item = self.items.get(page) if key == self.key else None
        if item is not None and not image.isNull():
            placeholder_rect = self.strip.visualItemRect(item)
            item.setIcon(QIcon(QPixmap.fromImage(image)))
            self.strip.viewport().update(placeholder_rect)

    def selected_pages(self) -> list[int]:
        return sorted(i.data(Qt.ItemDataRole.UserRole) for i in self.strip.selectedItems())

    def selection_spec(self) -> str:
        pages = self.selected_pages()
        return pages_to_spec(pages, self.total, self.handle) if pages else ""

    def _update_spec(self) -> None:
        spec = self.selection_spec() or "(none)"
        self.spec.setText(f"Selection: {spec}   (Ctrl+E inserts it into the next stage)")


class StageBox(StripBox):
    """One pipeline stage: operation, arguments, extra inputs and its output pages."""

    op_changed = Signal(str)
    insert_requested = Signal(object, int)
    delete_requested = Signal(object)

    def __init__(self, ops: list[str], provider: PdfiumThumbProvider) -> None:
        super().__init__("", provider, draggable=True)
        self.title.setToolTip("Drag to move this stage; right-click for more")
        self.op = WheelSafeCombo()
        self.op.setEditable(True)
        self.op.addItems(ops)
        self.op.setCurrentText("cat")
        self.op.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.op.setToolTip(tooltip("Operation: type a name or pick one from the list", "focus_op"))
        self.args = QLineEdit()
        self.args.setPlaceholderText("arguments, e.g. 1-3 east (Alt+A)")
        self.args.setToolTip(
            tooltip("Arguments, as on the command line; F1 shows the help", "focus_args")
        )
        self.extra = QLineEdit()
        self.extra.setPlaceholderText(
            "extra inputs: handles such as B C, read after the previous output"
        )
        self.extra.setToolTip(
            "Input file handles this stage reads after the previous stage's output"
        )
        self.focus_fallback = self.op
        self.layout_.insertWidget(1, self.op)
        self.layout_.insertWidget(2, self.args)
        self.layout_.insertWidget(3, self.extra)
        self.follow.show()
        style = self.style()
        self.insert_before = tool_button(
            themed(style, "go-up", QStyle.StandardPixmap.SP_ArrowUp),
            "New stage before",
            "insert_stage_before",
        )
        self.insert_after = tool_button(
            themed(style, "go-down", QStyle.StandardPixmap.SP_ArrowDown),
            "New stage after",
            "add_stage",
        )
        self.delete = tool_button(delete_icon(), "Delete stage", "delete_stage")
        self.insert_before.clicked.connect(lambda: self.insert_requested.emit(self, 0))
        self.insert_after.clicked.connect(lambda: self.insert_requested.emit(self, 1))
        self.delete.clicked.connect(lambda: self.delete_requested.emit(self))
        for button in (self.insert_before, self.insert_after, self.delete):
            self.title_row.addWidget(button)
        self.op.currentTextChanged.connect(self.edited)
        self.op.currentTextChanged.connect(self.op_changed)
        self.args.textEdited.connect(self.edited)
        self.extra.textEdited.connect(self.edited)
        self.install_click_filters()

    def set_stage(self, stage: Stage) -> None:
        self.op.setCurrentText(stage.op)
        self.args.setText(stage.args_text)
        self.extra.setText(" ".join(stage.inputs))

    def stage(self) -> Stage:
        handles = tuple(self.extra.text().upper().replace(",", " ").split())
        return Stage(self.op.currentText().strip(), self.args.text(), handles)
