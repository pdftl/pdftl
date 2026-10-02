# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/gui/test_widgets.py

"""Tests for widgets module: page strips and stage editors."""

from pathlib import Path

import pikepdf
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QItemSelectionModel, QPoint, QPointF, QSettings, QSize, Qt
from PySide6.QtGui import QColor, QImage, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QScrollArea, QToolButton, QWidget

from pdftl.gui import widgets
from pdftl.gui.interfaces import Stage
from pdftl.gui.keymap import keys_for
from pdftl.gui.thumbs import PDFIUM_LOCK, PdfiumThumbProvider
from pdftl.gui.widgets import (
    THUMB,
    PageStrip,
    StageBox,
    StripBox,
    WheelSafeCombo,
)

PLACEHOLDER_RGB = (210, 210, 210)


def _make_pdf(path: Path, pages: int = 6) -> Path:
    """Pages of distinct sizes, each with a dark left half, light right half."""
    pdf = pikepdf.new()
    for i in range(pages):
        pdf.add_blank_page(page_size=(200, 100 + i * 10))
    for page in pdf.pages:
        w, h = float(page.MediaBox[2]), float(page.MediaBox[3])
        content = f"0 0 0 rg 0 0 {w / 2} {h} re f 1 1 1 rg {w / 2} 0 {w / 2} {h} re f".encode()
        page.Contents = pdf.make_stream(content)
    pdf.save(path)
    return path


def _icon_rgb(item) -> tuple[int, int, int]:
    image = item.icon().pixmap(THUMB, int(THUMB * 1.3)).toImage()
    color = image.pixelColor(5, 5)
    return (color.red(), color.green(), color.blue())


def _wheel_event(dy: int, dx: int = 0, modifiers=Qt.KeyboardModifier.NoModifier) -> QWheelEvent:
    return QWheelEvent(
        QPointF(5, 5),
        QPointF(5, 5),
        QPoint(dx, 0),
        QPoint(dx, dy),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


@pytest.fixture
def provider(qtbot):
    p = PdfiumThumbProvider()
    yield p
    p.close()


@pytest.fixture
def box(qtbot, provider):
    b = StripBox("Title", provider)
    qtbot.addWidget(b)
    b.show()
    return b


# --- StripBox: rendering ---


def test_show_pdf_creates_placeholders_then_real_thumbnails(qtbot, box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=3)
    box.show_pdf(pdf_path, 3, key="k1")

    assert len(box.items) == 3
    assert _icon_rgb(box.items[1]) == PLACEHOLDER_RGB

    qtbot.waitUntil(lambda: _icon_rgb(box.items[1]) != PLACEHOLDER_RGB, timeout=3000)
    assert _icon_rgb(box.items[1]) == (0, 0, 0)


def test_show_pdf_default_status_is_page_count(box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=4)
    box.show_pdf(pdf_path, 4, key="k1")
    assert box.status.text() == "4 pages"


def test_show_pdf_default_status_singular_for_one_page(box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=1)
    box.show_pdf(pdf_path, 1, key="k1")
    assert box.status.text() == "1 page"


def test_show_pdf_default_status_plural_for_zero_pages(box):
    box.show_pdf(None, 0, key="k1")
    assert box.status.text() == "0 pages"


def test_show_pdf_custom_status_overrides_default(box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=2)
    box.show_pdf(pdf_path, 2, status="custom", key="k1")
    assert box.status.text() == "custom"


def test_show_failure_clears_the_pages_and_shows_the_error_in_red(box, tmp_path):
    box.show_pdf(_make_pdf(tmp_path / "a.pdf", pages=2), 2, key="k1")
    box.show_failure("✗ boom")
    assert box.failed and box.path is None and box.strip.count() == 0
    assert box.status.text() == "✗ boom"
    assert "color: #d0312d" in box.status.styleSheet()
    box.show_pdf(None, status="waiting")
    assert not box.failed
    assert box.status.styleSheet() == ""


def test_show_pdf_enables_view_export_only_with_a_pdf(box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=1)
    box.show_pdf(pdf_path, 1, key="k1")
    assert box.view.isEnabled()
    assert box.export.isEnabled()

    box.show_pdf(None)
    assert not box.view.isEnabled()
    assert not box.export.isEnabled()


def test_same_key_keeps_items_and_sends_no_new_requests(qtbot, box, tmp_path, monkeypatch):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=3)
    box.show_pdf(pdf_path, 3, key="k1")
    qtbot.waitUntil(lambda: _icon_rgb(box.items[1]) != PLACEHOLDER_RGB, timeout=3000)
    items_before = dict(box.items)

    requests = []
    monkeypatch.setattr(box.provider, "request", lambda *a: requests.append(a))
    box.show_pdf(pdf_path, 3, key="k1")

    assert requests == []
    assert all(items_before[p] is box.items[p] for p in items_before)


def test_new_key_reloads_and_cancels_old_key(box, tmp_path, monkeypatch):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=3)
    box.show_pdf(pdf_path, 3, key="k1")

    cancelled = []
    requested = []
    monkeypatch.setattr(box.provider, "cancel", lambda key: cancelled.append(key))
    monkeypatch.setattr(box.provider, "request", lambda *a: requested.append(a))

    box.show_pdf(pdf_path, 3, key="k2")

    assert cancelled == ["k1"]
    assert len(requested) == 3
    assert box.key == "k2"


def test_path_none_clears_strip(box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=3)
    box.show_pdf(pdf_path, 3, key="k1")
    assert box.items

    box.show_pdf(None)
    assert box.items == {}
    assert box.total == 0
    assert box.strip.count() == 0


def test_key_none_reloads_every_call_and_skips_requests(box, tmp_path, monkeypatch):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=3)
    box.show_pdf(pdf_path, 3, key="k1")

    requested = []
    monkeypatch.setattr(box.provider, "request", lambda *a: requested.append(a))
    box.show_pdf(pdf_path, 3)  # key defaults to None

    assert box.key is None
    assert box.total == 3
    assert box.items == {}
    assert requested == []


def test_every_page_gets_an_item_but_far_pages_do_not_render_at_once(box, tmp_path, monkeypatch):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=200)
    requested = []
    monkeypatch.setattr(box.provider, "request", lambda *a: requested.append(a[2]))

    box.show_pdf(pdf_path, 200, key="k1")

    assert len(box.items) == 200
    assert 1 in requested
    assert 200 not in requested
    assert len(requested) < 200
    box.strip.selectAll()
    assert box.selection_spec() == "1-end"


def test_request_around_wants_only_pages_within_the_lazy_margin(box, tmp_path, monkeypatch):
    monkeypatch.setattr(widgets, "LAZY_MARGIN", 3)
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=50)
    box._reload(pdf_path, 50, "k1")
    requested = []
    monkeypatch.setattr(box.provider, "request", lambda *a: requested.append(a[2]))
    box.requested = set()

    box._request_around(20, 22)

    assert sorted(requested) == list(range(17, 26))


def test_request_around_drops_out_of_range_queued_work(box, tmp_path, monkeypatch):
    """narrow() on the provider drops queued renders outside the new wanted set."""
    monkeypatch.setattr(widgets, "LAZY_MARGIN", 0)
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=50)
    box._reload(pdf_path, 50, "k1")
    narrowed = []
    monkeypatch.setattr(box.provider, "narrow", lambda key, pages: narrowed.append((key, pages)))
    monkeypatch.setattr(box.provider, "request", lambda *a: None)

    box._request_around(10, 10)

    assert narrowed == [("k1", {10})]


def test_thumb_ready_ignored_for_stale_key(box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=2)
    box.show_pdf(pdf_path, 2, key="k1")
    before = _icon_rgb(box.items[1])

    stale_image = QImage(10, 10, QImage.Format.Format_RGB32)
    stale_image.fill(Qt.GlobalColor.red)
    box._thumb_ready("stale-key", 1, stale_image)

    assert _icon_rgb(box.items[1]) == before


def test_thumb_ready_ignores_null_image(box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=2)
    box.show_pdf(pdf_path, 2, key="k1")
    before = _icon_rgb(box.items[1])

    box._thumb_ready("k1", 1, QImage())

    assert _icon_rgb(box.items[1]) == before


# --- StripBox: keyboard selection ---


def test_selection_spec_via_keyboard_no_handle(qtbot, box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=6)
    box.show_pdf(pdf_path, 6, key="k1")
    box.strip.setFocus()

    assert box.selection_spec() == ""

    QTest.keyClick(box.strip, Qt.Key.Key_Right)
    assert box.selection_spec() == "1"

    QTest.keyClick(box.strip, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert box.selection_spec() == "1-2"

    QTest.keyClick(box.strip, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert box.selection_spec() == "1-end"


def test_plain_space_toggles_the_current_page(qtbot, box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=6)
    box.show_pdf(pdf_path, 6, key="k1")
    box.strip.setFocus()
    box.strip.setCurrentRow(2)

    # setCurrentRow already selects row 2 (page 3); Space toggles it off, then on.
    assert box.selection_spec() == "3"
    QTest.keyClick(box.strip, Qt.Key.Key_Space)
    assert box.selection_spec() == ""
    QTest.keyClick(box.strip, Qt.Key.Key_Space)
    assert box.selection_spec() == "3"


def test_plain_space_ignored_with_no_current_item(qtbot, box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=6)
    box.show_pdf(pdf_path, 6, key="k1")
    box.strip.setFocus()
    box.strip.setCurrentItem(None)
    QTest.keyClick(box.strip, Qt.Key.Key_Space)
    assert box.selection_spec() == ""


def test_selection_spec_via_keyboard_with_handle(qtbot, box, tmp_path):
    box.handle = "B"
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=6)
    box.show_pdf(pdf_path, 6, key="k1")
    box.strip.setFocus()

    QTest.keyClick(box.strip, Qt.Key.Key_Right)
    QTest.keyClick(box.strip, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert box.selection_spec() == "B1-2"


def test_spec_label_shows_none_with_no_selection(box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=2)
    box.show_pdf(pdf_path, 2, key="k1")
    assert box.spec.text().startswith("Selection: (none)")


def test_spec_label_updates_with_selection(qtbot, box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=2)
    box.show_pdf(pdf_path, 2, key="k1")
    box.strip.setFocus()
    QTest.keyClick(box.strip, Qt.Key.Key_Right)
    assert box.spec.text().startswith("Selection: 1 ")


# --- StripBox: save_text and signals ---


def test_save_text_button_visibility(box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=1)
    assert not box.save_text.isVisible()

    box.show_pdf(pdf_path, 1, key="k1", text="some output")
    assert box.save_text.isVisible()
    assert box.save_text.isEnabled()

    box.show_pdf(pdf_path, 1, key="k1", text="   ")
    assert not box.save_text.isVisible()


def test_view_requested_via_keyboard(qtbot, box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=1)
    box.show_pdf(pdf_path, 1, key="k1")
    seen = []
    box.view_requested.connect(lambda b: seen.append(b))

    box.view.setFocus()
    qtbot.waitUntil(lambda: box.view.hasFocus())
    QTest.keyClick(box.view, Qt.Key.Key_Space)

    assert seen == [box]


def test_export_requested_via_keyboard(qtbot, box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=1)
    box.show_pdf(pdf_path, 1, key="k1")
    seen = []
    box.export_requested.connect(lambda b: seen.append(b))

    box.export.setFocus()
    qtbot.waitUntil(lambda: box.export.hasFocus())
    QTest.keyClick(box.export, Qt.Key.Key_Space)

    assert seen == [box]


def test_save_text_requested_via_keyboard(qtbot, box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=1)
    box.show_pdf(pdf_path, 1, key="k1", text="output")
    seen = []
    box.save_text_requested.connect(lambda b: seen.append(b))

    box.save_text.setFocus()
    qtbot.waitUntil(lambda: box.save_text.hasFocus())
    QTest.keyClick(box.save_text, Qt.Key.Key_Space)

    assert seen == [box]


def test_preview_requested_via_return_on_strip_item(qtbot, box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=3)
    box.show_pdf(pdf_path, 3, key="k1")
    seen = []
    box.preview_requested.connect(lambda b, p: seen.append((b, p)))

    box.strip.setCurrentRow(1)
    box.strip.setFocus()
    QTest.keyClick(box.strip, Qt.Key.Key_Return)

    assert seen == [(box, 2)]


def test_return_on_a_selected_run_previews_its_first_page(qtbot, box, tmp_path):
    box.show_pdf(_make_pdf(tmp_path / "a.pdf", pages=6), 6, key="k1")
    seen = []
    box.preview_requested.connect(lambda b, p: seen.append(p))
    box.strip.setFocus()
    box.strip.setCurrentRow(1)
    QTest.keyClick(box.strip, Qt.Key.Key_Space)
    QTest.keyClick(box.strip, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClick(box.strip, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert box.strip.currentRow() == 3
    assert [i.data(Qt.ItemDataRole.UserRole) for i in box.strip.selectedItems()] == [2, 3, 4]
    box.strip.item(5).setSelected(True)
    QTest.keyClick(box.strip, Qt.Key.Key_Return)
    box.strip.setCurrentRow(5, QItemSelectionModel.SelectionFlag.NoUpdate)
    QTest.keyClick(box.strip, Qt.Key.Key_Return)
    box.strip.setCurrentRow(0, QItemSelectionModel.SelectionFlag.NoUpdate)
    QTest.keyClick(box.strip, Qt.Key.Key_Return)
    assert seen == [2, 6, 1]


def test_keypad_enter_previews_and_with_no_current_page_does_nothing(qtbot, box, tmp_path):
    box.show_pdf(_make_pdf(tmp_path / "a.pdf", pages=3), 3, key="k1")
    seen = []
    box.preview_requested.connect(lambda b, p: seen.append(p))
    box.strip.setFocus()
    box.strip.setCurrentRow(-1)
    QTest.keyClick(box.strip, Qt.Key.Key_Enter, Qt.KeyboardModifier.KeypadModifier)
    assert seen == []
    box.strip.setCurrentRow(2)
    QTest.keyClick(box.strip, Qt.Key.Key_Enter, Qt.KeyboardModifier.KeypadModifier)
    assert seen == [3]


# --- StageBox ---


def test_stagebox_set_stage_then_stage_round_trip(qtbot, provider):
    sbox = StageBox(["cat", "rotate"], provider)
    qtbot.addWidget(sbox)
    sbox.set_stage(Stage("rotate", "left", ("b", "c")))

    assert sbox.op.currentText() == "rotate"
    assert sbox.args.text() == "left"
    assert sbox.extra.text() == "b c"

    stage = sbox.stage()
    assert stage == Stage("rotate", "left", ("B", "C"))


def test_stagebox_stage_defaults_to_no_extra_inputs(qtbot, provider):
    sbox = StageBox(["cat"], provider)
    qtbot.addWidget(sbox)
    stage = sbox.stage()
    assert stage.inputs == ()


def test_stagebox_op_change_emits_edited_and_op_changed(qtbot, provider):
    sbox = StageBox(["cat", "rotate"], provider)
    qtbot.addWidget(sbox)
    edited_calls = []
    changed_calls = []
    sbox.edited.connect(lambda: edited_calls.append(True))
    sbox.op_changed.connect(lambda op: changed_calls.append(op))

    sbox.op.setCurrentText("rotate")

    assert edited_calls
    assert changed_calls == ["rotate"]


def test_stagebox_args_edit_emits_edited(qtbot, provider):
    sbox = StageBox(["cat"], provider)
    qtbot.addWidget(sbox)
    sbox.show()
    edited_calls = []
    sbox.edited.connect(lambda: edited_calls.append(True))

    sbox.args.setFocus()
    QTest.keyClicks(sbox.args, "1-3")

    assert edited_calls


# --- WheelSafeCombo ---


def test_wheelsafecombo_focused_still_ignores_wheel(qtbot):
    """Scrolling the window past a focused op field must not change the op either."""
    combo = WheelSafeCombo()
    qtbot.addWidget(combo)
    combo.addItems(["a", "b", "c"])
    combo.setCurrentIndex(0)
    combo.show()
    combo.setFocus()
    qtbot.waitUntil(lambda: combo.hasFocus())

    event = _wheel_event(dy=-120)
    combo.wheelEvent(event)

    assert combo.currentIndex() == 0
    assert not event.isAccepted()


def test_wheelsafecombo_unfocused_ignores_wheel(qtbot):
    combo = WheelSafeCombo()
    qtbot.addWidget(combo)
    combo.addItems(["a", "b", "c"])
    combo.setCurrentIndex(0)

    event = _wheel_event(dy=-120)
    combo.wheelEvent(event)

    assert combo.currentIndex() == 0
    assert not event.isAccepted()


# --- PageStrip ---


def test_pagestrip_plain_vertical_wheel_is_ignored(qtbot):
    strip = PageStrip()
    qtbot.addWidget(strip)
    strip.show()
    bar = strip.horizontalScrollBar()
    bar.setRange(0, 1000)
    bar.setValue(500)

    event = _wheel_event(dy=-120)
    strip.wheelEvent(event)

    assert bar.value() == 500
    assert not event.isAccepted()


def test_pagestrip_shift_wheel_scrolls_horizontally(qtbot):
    strip = PageStrip()
    qtbot.addWidget(strip)
    strip.show()
    bar = strip.horizontalScrollBar()
    bar.setRange(0, 1000)
    bar.setValue(500)

    event = _wheel_event(dy=-120, modifiers=Qt.KeyboardModifier.ShiftModifier)
    strip.wheelEvent(event)

    assert bar.value() == 620
    assert event.isAccepted()


def test_pagestrip_horizontal_wheel_scrolls(qtbot):
    strip = PageStrip()
    qtbot.addWidget(strip)
    strip.show()
    bar = strip.horizontalScrollBar()
    bar.setRange(0, 1000)
    bar.setValue(500)

    event = _wheel_event(dy=0, dx=-120)
    strip.wheelEvent(event)

    assert bar.value() == 620
    assert event.isAccepted()


def _placeholder_rgb_reference() -> tuple[int, int, int]:
    color = QColor(210, 210, 210)
    return (color.red(), color.green(), color.blue())


def test_placeholder_color_matches_reference():
    assert PLACEHOLDER_RGB == _placeholder_rgb_reference()


def test_pages_past_the_lazy_margin_render_when_scrolled_or_focused(
    qtbot, box, tmp_path, monkeypatch
):
    monkeypatch.setattr(widgets, "LAZY_MARGIN", 1)
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=40)
    requested = []
    monkeypatch.setattr(box.provider, "request", lambda *a: requested.append(a[2]))
    box.show()
    qtbot.waitExposed(box)
    box.show_pdf(pdf_path, 40, key="k1")
    assert 1 in requested
    assert 40 not in requested
    box.strip.setCurrentRow(29)
    assert {29, 30, 31} <= set(requested)
    box.strip.scrollToItem(box.items[40])
    box._request_visible()
    assert 40 in requested
    assert len(requested) == len(set(requested))


def test_request_helpers_are_noops_without_a_document(box):
    box._request_around(1, 3)
    box._request_near_current(None)
    box._request_visible()
    assert box.requested == set()


def test_landscape_thumbnail_repaints_the_taller_placeholder_area(qtbot, box, tmp_path):
    pdf = pikepdf.new()
    for _ in range(2):
        pdf.add_blank_page(page_size=(400, 250))
    path = tmp_path / "landscape.pdf"
    pdf.save(path)
    box.show()
    qtbot.waitExposed(box)
    repainted = []
    viewport = box.strip.viewport()
    real_update = viewport.update
    viewport.update = lambda *rect: repainted.append(rect) or real_update(*rect)
    box.show_pdf(path, 2, key="land")
    qtbot.waitUntil(lambda: _icon_rgb(box.items[1]) != PLACEHOLDER_RGB, timeout=3000)

    final_rect = box.strip.visualItemRect(box.items[1])
    assert final_rect.height() < 100
    assert repainted
    # a fixed grid: item 1's true height does not move item 2
    assert (
        box.strip.visualItemRect(box.items[2]).x() - final_rect.x() == box.strip.gridSize().width()
    )


def test_set_current_tints_and_marks_the_title(box):
    base = box.palette().window().color()
    box.set_title("<b>Stage 2</b>")
    box.set_current(True)
    assert box.title.text() == "▶ <b>Stage 2</b> <i>(current)</i>"
    assert box.autoFillBackground()
    tint = box.palette().window().color()
    highlight = box.palette().highlight().color()
    assert tint != base
    assert abs(tint.red() - (0.2 * highlight.red() + 0.8 * base.red())) <= 1
    box.set_current(False)
    assert box.title.text() == "<b>Stage 2</b>"
    assert not box.autoFillBackground()
    assert box.palette().window().color() == base


def test_clicking_a_box_focuses_its_strip(qtbot, box):
    box.show()
    qtbot.waitExposed(box)
    box.activateWindow()
    qtbot.mouseClick(box.title, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(box.strip.hasFocus)


def test_show_pdf_announces_output_changes(qtbot, box):
    with qtbot.waitSignal(box.output_changed, timeout=1000):
        box.show_pdf(None, status="nothing")


def test_stage_buttons_sit_on_the_title_row_and_emit(qtbot, provider):
    box = StageBox(["cat"], provider)
    qtbot.addWidget(box)
    row = [box.title_row.itemAt(i).widget() for i in range(box.title_row.count())]
    assert row == [box.title, box.insert_before, box.insert_after, box.delete]
    with qtbot.waitSignal(box.insert_requested) as before:
        box.insert_before.click()
    with qtbot.waitSignal(box.insert_requested) as after:
        box.insert_after.click()
    with qtbot.waitSignal(box.delete_requested) as deleted:
        box.delete.click()
    assert before.args == [box, 0] and after.args == [box, 1] and deleted.args == [box]


# --- WheelSafeCombo: no stray items, select-all on focus ---


def test_op_combo_does_not_insert_typed_text_as_a_new_item(qtbot):
    combo = WheelSafeCombo()
    combo.setEditable(True)
    combo.addItems(["cat", "shrink"])
    qtbot.addWidget(combo)
    combo.show()
    before_count = combo.count()

    combo.setCurrentText("shrink target=500kb")
    QTest.keyClick(combo.lineEdit(), Qt.Key.Key_Return)

    assert combo.count() == before_count


def test_op_combo_selects_all_text_on_focus_when_editable(qtbot):
    combo = WheelSafeCombo()
    combo.setEditable(True)
    combo.setCurrentText("cat")
    qtbot.addWidget(combo)
    combo.show()
    qtbot.waitExposed(combo)

    combo.setFocus()
    qtbot.waitUntil(lambda: combo.lineEdit().selectedText() == "cat", timeout=1000)


def test_op_combo_focus_is_safe_when_not_editable(qtbot):
    combo = WheelSafeCombo()
    combo.addItems(["cat"])
    qtbot.addWidget(combo)
    combo.show()
    qtbot.waitExposed(combo)

    combo.setFocus()  # must not raise: lineEdit() is None when not editable
    qtbot.wait(10)


# --- StripBox: hide/show the page strip ---


def test_hide_pages_tooltip_advertises_no_shortcut(box):
    tip = box.hide_pages.toolTip()
    assert "Alt+H" not in tip
    assert not any(mod in tip for mod in ("Ctrl+", "Alt+", "Shift+"))


def test_hide_pages_toggle_hides_and_restores_the_strip(box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=3)
    box.show_pdf(pdf_path, 3, key="k1")
    assert box.hide_pages.text() == "Hide pages"
    assert not box.pages_hidden

    box.hide_pages.setChecked(True)
    assert box.pages_hidden
    assert box.strip.isHidden()
    assert box.hide_pages.text() == "Show pages"

    box.hide_pages.setChecked(False)
    assert not box.pages_hidden
    assert not box.strip.isHidden()
    assert box.hide_pages.text() == "Hide pages"


def test_hidden_strip_requests_no_thumbnails(box, tmp_path, monkeypatch):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=5)
    box.show_pdf(pdf_path, 5, key="k1")
    box.hide_pages.setChecked(True)
    requested = []
    monkeypatch.setattr(box.provider, "request", lambda *a: requested.append(a[2]))

    box.strip.setCurrentRow(2)
    box._request_visible()

    assert requested == []


# --- Global default: hide new stages' page strips ---


def test_default_settings_factory_uses_the_pdftl_gui_scope():
    settings = widgets._default_settings()
    assert isinstance(settings, QSettings)
    assert settings.organizationName() == "pdftl"
    assert settings.applicationName() == "gui"


def test_default_pages_hidden_is_false_with_no_stored_value():
    assert widgets.default_pages_hidden() is False


def test_set_default_pages_hidden_persists_to_the_settings_store():
    ini_path = widgets.settings_factory().fileName()

    widgets.set_default_pages_hidden(True)

    fresh = QSettings(ini_path, QSettings.Format.IniFormat)
    assert fresh.value("stage_strip/hide_new_by_default", type=bool) is True
    assert widgets.default_pages_hidden() is True


def test_new_strip_starts_hidden_when_the_default_is_set(provider, qtbot):
    widgets.set_default_pages_hidden(True)

    box = StripBox("Title", provider)
    qtbot.addWidget(box)

    assert box.pages_hidden is True
    assert box.hide_pages.isChecked() is True
    assert box.strip.isHidden()


# --- StripBox: scroll-area wiring for the lazy viewport ---


@pytest.fixture
def boxed_scroll_area(qtbot, provider):
    """A StripBox hosted inside a real QScrollArea, positioned freely (no layout)."""
    scroll = QScrollArea()
    container = QWidget()
    container.resize(400, 4000)
    scroll.setWidget(container)
    scroll.resize(300, 300)
    box = StripBox("Title", provider)
    box.setParent(container)
    box.resize(250, 200)
    box.move(0, 0)
    qtbot.addWidget(scroll)
    scroll.show()
    qtbot.waitExposed(scroll)
    box.show()
    return scroll, box


def test_scroll_area_is_found_and_wired_on_show(boxed_scroll_area):
    scroll, box = boxed_scroll_area
    assert box._scroll_area is scroll
    assert box._in_viewport()

    box._wire_scroll_area()  # same area again: a no-op, not a double-connect
    assert box._scroll_area is scroll


def test_moving_far_from_the_viewport_stops_counting_as_visible(boxed_scroll_area):
    scroll, box = boxed_scroll_area
    assert box._in_viewport()

    box.move(0, 3000)

    assert not box._in_viewport()


def test_event_filter_reacts_only_to_resize_and_show_events(
    boxed_scroll_area, tmp_path, monkeypatch
):
    scroll, box = boxed_scroll_area
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=3)
    box.show_pdf(pdf_path, 3, key="k1")
    requested = []
    monkeypatch.setattr(box.provider, "request", lambda *a: requested.append(a[2]))
    box.requested = set()

    box.eventFilter(scroll.viewport(), QEvent(QEvent.Type.Paint))
    assert requested == []

    box.eventFilter(scroll.viewport(), QEvent(QEvent.Type.Resize))
    assert requested != []


def test_request_visible_is_a_noop_when_out_of_the_viewport(
    boxed_scroll_area, tmp_path, monkeypatch
):
    scroll, box = boxed_scroll_area
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=3)
    box.show_pdf(pdf_path, 3, key="k1")
    box.move(0, 3000)
    requested = []
    monkeypatch.setattr(box.provider, "request", lambda *a: requested.append(a[2]))
    box.requested = set()

    box._request_visible()

    assert requested == []


def test_hiding_the_box_unwires_the_scroll_area(boxed_scroll_area):
    scroll, box = boxed_scroll_area
    assert box._scroll_area is scroll

    box.hide()
    assert box._scroll_area is None

    box.hide()  # already unwired: a no-op, not a double-disconnect
    assert box._scroll_area is None


def test_unwire_scroll_area_is_a_noop_with_nothing_wired(box):
    assert box._scroll_area is None
    box._unwire_scroll_area()
    assert box._scroll_area is None


# --- Defensive guards: skip work on an already-deleted C++ object ---


def test_event_filter_short_circuits_when_self_is_invalid(monkeypatch, box):
    monkeypatch.setattr(widgets.shiboken6, "isValid", lambda _obj: False)
    assert box.eventFilter(box, QEvent(QEvent.Type.Resize)) is False


def test_request_visible_short_circuits_when_self_is_invalid(monkeypatch, box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=3)
    box.show_pdf(pdf_path, 3, key="k1")
    requested = []
    monkeypatch.setattr(box.provider, "request", lambda *a: requested.append(a[2]))
    monkeypatch.setattr(widgets.shiboken6, "isValid", lambda _obj: False)

    box._request_visible()

    assert requested == []


def test_unwire_scroll_area_short_circuits_when_the_area_is_invalid(monkeypatch, box):
    monkeypatch.setattr(widgets.shiboken6, "isValid", lambda _obj: False)
    box._scroll_area = box  # placeholder; isValid() is forced False regardless of target

    box._unwire_scroll_area()

    assert box._scroll_area is None


# --- Icon buttons: compact, tooltip = label + bound shortcut, accessibleName = old text ---


def test_stripbox_status_buttons_are_icon_only_and_keyboard_reachable(box):
    for button in (box.view, box.follow, box.export, box.save_text, box.hide_pages):
        assert isinstance(button, QToolButton)
        assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        assert not button.icon().isNull()
        assert button.focusPolicy() == Qt.FocusPolicy.StrongFocus


def test_view_tooltip_and_accessible_name_match_its_shortcut(box):
    assert box.view.accessibleName() == "View in PDF viewer"
    assert box.view.toolTip() == f"View in PDF viewer ({keys_for('view_stage')[0]})"


def test_follow_tooltip_matches_its_shortcut_and_stays_checkable(box):
    assert box.follow.isCheckable()
    assert box.follow.accessibleName() == "Follow in PDF viewer"
    assert box.follow.toolTip() == f"Follow in PDF viewer ({keys_for('follow_stage')[0]})"


def test_export_tooltip_matches_its_shortcut(box):
    assert box.export.accessibleName() == "Save as..."
    assert box.export.toolTip() == f"Save as... ({keys_for('export_stage')[0]})"


def test_save_text_has_no_shortcut_so_the_tooltip_is_just_its_label(box):
    assert box.save_text.accessibleName() == "Save text..."
    assert box.save_text.toolTip() == "Save text..."


def test_hide_pages_toggle_updates_icon_and_accessible_name(box):
    def image(button):
        return button.icon().pixmap(24, 24).toImage()

    before = image(box.hide_pages)
    assert box.hide_pages.accessibleName() == "Hide pages"

    box.hide_pages.setChecked(True)

    assert box.hide_pages.accessibleName() == "Show pages"
    assert not box.hide_pages.icon().isNull()
    assert image(box.hide_pages) != before

    box.hide_pages.setChecked(False)

    assert box.hide_pages.accessibleName() == "Hide pages"
    assert image(box.hide_pages) == before


def test_stagebox_action_buttons_are_icon_only_with_shortcuts(qtbot, provider):
    sbox = StageBox(["cat"], provider)
    qtbot.addWidget(sbox)

    before = keys_for("insert_stage_before")[0]
    assert sbox.insert_before.toolTip() == f"New stage before ({before})"
    assert sbox.insert_after.toolTip() == f"New stage after ({keys_for('add_stage')[0]})"
    assert sbox.delete.toolTip() == f"Delete stage ({keys_for('delete_stage')[0]})"
    for button, name in (
        (sbox.insert_before, "New stage before"),
        (sbox.insert_after, "New stage after"),
        (sbox.delete, "Delete stage"),
    ):
        assert isinstance(button, QToolButton)
        assert button.accessibleName() == name
        assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        assert not button.icon().isNull()
        assert button.focusPolicy() == Qt.FocusPolicy.StrongFocus


# --- Click anywhere on a stage box selects it ---


def _click(widget, pos=None) -> None:
    widget.window().activateWindow()
    QApplication.processEvents()
    QTest.mouseClick(
        widget,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        pos or widget.rect().center(),
    )


def _defocus(qtbot, other: QWidget) -> None:
    """Park focus outside the box under test so a click's effect is observable."""
    other.window().activateWindow()
    QApplication.processEvents()
    other.setFocus()
    qtbot.waitUntil(other.hasFocus)


def _expose(qtbot, widget: QWidget) -> None:
    widget.show()
    qtbot.waitExposed(widget)
    widget.activateWindow()


def test_click_on_disabled_view_button_before_output_selects_the_box(qtbot, box):
    _expose(qtbot, box)
    other = QLineEdit()
    qtbot.addWidget(other)
    other.show()
    assert not box.view.isEnabled()
    _defocus(qtbot, other)

    _click(box.view)

    assert box.isAncestorOf(QApplication.focusWidget())


def test_click_on_the_empty_status_label_selects_the_box(qtbot, box):
    _expose(qtbot, box)
    other = QLineEdit()
    qtbot.addWidget(other)
    other.show()
    _defocus(qtbot, other)

    _click(box.status)

    assert box.isAncestorOf(QApplication.focusWidget())


def test_click_on_the_spec_label_selects_the_box(qtbot, box):
    _expose(qtbot, box)
    other = QLineEdit()
    qtbot.addWidget(other)
    other.show()
    _defocus(qtbot, other)

    _click(box.spec)

    assert box.isAncestorOf(QApplication.focusWidget())


def test_click_on_the_box_margin_selects_the_box(qtbot, box):
    _expose(qtbot, box)
    other = QLineEdit()
    qtbot.addWidget(other)
    other.show()
    _defocus(qtbot, other)

    _click(box, QPoint(2, 2))

    assert box.isAncestorOf(QApplication.focusWidget())


def test_click_on_an_enabled_button_focuses_itself_not_the_strip(qtbot, box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=1)
    box.show_pdf(pdf_path, 1, key="k1")  # enables view/export
    _expose(qtbot, box)
    other = QLineEdit()
    qtbot.addWidget(other)
    other.show()
    _defocus(qtbot, other)

    _click(box.view)

    assert QApplication.focusWidget() is box.view


def test_click_on_stagebox_args_focuses_the_field_itself(qtbot, provider):
    sbox = StageBox(["cat"], provider)
    qtbot.addWidget(sbox)
    _expose(qtbot, sbox)
    other = QLineEdit()
    qtbot.addWidget(other)
    other.show()
    _defocus(qtbot, other)

    _click(sbox.args)

    assert QApplication.focusWidget() is sbox.args


def test_every_visible_child_and_the_margin_selects_a_stagebox(qtbot, provider):
    sbox = StageBox(["cat"], provider)
    qtbot.addWidget(sbox)
    _expose(qtbot, sbox)
    other = QLineEdit()
    qtbot.addWidget(other)
    other.show()

    children = [
        sbox.title,
        sbox.status,
        sbox.spec,
        sbox.strip,
        sbox.op,
        sbox.args,
        sbox.extra,
        sbox.view,
        sbox.follow,
        sbox.export,
        sbox.hide_pages,
        sbox.insert_before,
        sbox.insert_after,
        sbox.delete,
    ]
    targets = [(sbox, QPoint(2, 2))] + [(c, None) for c in children if c.isVisible()]
    assert len(targets) > len(children)  # sanity: save_text (hidden) was excluded, margin added

    for target, pos in targets:
        _defocus(qtbot, other)
        assert not sbox.isAncestorOf(QApplication.focusWidget())

        _click(target, pos)

        assert sbox.isAncestorOf(QApplication.focusWidget()), target


# --- Constant per-strip thumbnail scale: true relative page sizes ---


def _make_sized_pdf(path: Path, sizes: list[tuple[float, float]]) -> Path:
    pdf = pikepdf.new()
    for w, h in sizes:
        pdf.add_blank_page(page_size=(w, h))
    pdf.save(path)
    return path


def test_a4_only_strip_renders_at_the_original_thumbnail_width(qtbot, box, tmp_path):
    pdf_path = _make_sized_pdf(tmp_path / "a4.pdf", [(595, 842), (595, 842)])
    box.show_pdf(pdf_path, 2, key="k1")
    qtbot.waitUntil(lambda: _icon_rgb(box.items[1]) != PLACEHOLDER_RGB, timeout=3000)

    assert box.strip.iconSize().width() == THUMB


def test_a5_page_renders_smaller_than_a4_by_the_true_paper_ratio(qtbot, box, tmp_path):
    pdf_path = _make_sized_pdf(tmp_path / "mixed.pdf", [(595, 842), (420, 595)])  # A4, A5
    received = {}
    box.provider.thumb_ready.connect(lambda _k, p, img: received.__setitem__(p, img))
    box.show_pdf(pdf_path, 2, key="k1")
    qtbot.waitUntil(lambda: 1 in received and 2 in received, timeout=3000)

    # ground truth is the real ISO paper ratio, independent of the code's own scale
    # formula; pages first drawn before their sizes are known get redrawn true to size
    qtbot.waitUntil(
        lambda: received[2].width() / received[1].width() == pytest.approx(420 / 595, rel=0.05),
        timeout=3000,
    )


def test_sizes_arriving_behind_queued_fallback_renders_still_end_true_to_size(
    qtbot, box, tmp_path
):
    """The worker serves newest first: stale fallback renders must not land last."""
    pdf_path = _make_sized_pdf(tmp_path / "mixed.pdf", [(595, 842), (420, 595)])  # A4, A5
    received = {}
    box.provider.thumb_ready.connect(lambda _k, p, img: received.__setitem__(p, img))
    with PDFIUM_LOCK:  # the worker stalls, leaving the fallback-width renders queued
        box.show_pdf(pdf_path, 2, key="k1")
        box._sizes_ready("k1", {1: (595.0, 842.0), 2: (420.0, 595.0)})
    qtbot.waitUntil(lambda: 1 in received and 2 in received, timeout=3000)
    qtbot.wait(300)  # any stale render would arrive after the true-size ones
    assert received[2].width() / received[1].width() == pytest.approx(420 / 595, rel=0.05)


def test_landscape_page_renders_wider_than_tall_by_the_true_ratio(qtbot, box, tmp_path):
    pdf_path = _make_sized_pdf(tmp_path / "landscape.pdf", [(842, 595)])  # A4 landscape
    received = {}
    box.provider.thumb_ready.connect(lambda _k, p, img: received.__setitem__(p, img))
    box.show_pdf(pdf_path, 1, key="k1")
    qtbot.waitUntil(lambda: 1 in received, timeout=3000)

    image = received[1]
    assert image.width() > image.height()
    assert image.width() / image.height() == pytest.approx(842 / 595, rel=0.05)


def test_grid_accommodates_every_pages_true_scaled_size(qtbot, box, tmp_path):
    pdf_path = _make_sized_pdf(tmp_path / "mixed2.pdf", [(400, 800), (900, 300)])
    received = {}
    box.provider.thumb_ready.connect(lambda _k, p, img: received.__setitem__(p, img))
    box.show_pdf(pdf_path, 2, key="k1")

    def fit() -> bool:
        icon = box.strip.iconSize()
        return len(received) == 2 and all(
            image.width() <= icon.width() and image.height() <= icon.height()
            for image in received.values()
        )

    # pages first drawn before their sizes are known get redrawn true to size
    qtbot.waitUntil(fit, timeout=3000)
    icon = box.strip.iconSize()
    # no wasted headroom: at least one page exactly fills the box on one axis
    assert any(
        image.width() == icon.width() or image.height() == icon.height()
        for image in received.values()
    )


def test_page_tooltip_names_a_known_paper_size(qtbot, box, tmp_path):
    pdf_path = _make_sized_pdf(tmp_path / "a4.pdf", [(595, 842)])
    box.show_pdf(pdf_path, 1, key="k1")
    qtbot.waitUntil(lambda: box.items[1].toolTip() != "")

    assert box.items[1].toolTip() == "Page 1 — 210 × 297 mm (A4)"


def test_page_tooltip_omits_a_name_for_a_nonstandard_size(qtbot, box, tmp_path):
    pdf_path = _make_sized_pdf(tmp_path / "odd.pdf", [(321, 654)])
    box.show_pdf(pdf_path, 1, key="k1")
    qtbot.waitUntil(lambda: box.items[1].toolTip() != "")

    tip = box.items[1].toolTip()
    assert tip.startswith("Page 1 — ")
    assert "mm" in tip
    assert "(" not in tip


def test_sizes_ready_skips_the_tooltip_for_a_page_missing_from_the_result(box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=2)
    box._reload(pdf_path, 2, "k1")

    box._sizes_ready("k1", {1: (595.0, 842.0)})  # page 2 missing from the result

    assert box.items[1].toolTip() != ""
    assert box.items[2].toolTip() == ""


def test_sizes_ready_ignores_a_degenerate_zero_size(box, tmp_path):
    pdf_path = _make_pdf(tmp_path / "a.pdf", pages=1)
    box._reload(pdf_path, 1, "k1")
    icon_before = box.strip.iconSize()

    box._sizes_ready("k1", {1: (0.0, 0.0)})

    assert box.strip.iconSize() == icon_before

    assert box._scroll_area is None


def test_click_on_a_box_whose_strip_is_hidden_focuses_its_fallback(qtbot, box, provider):
    _expose(qtbot, box)
    other = QLineEdit()
    qtbot.addWidget(other)
    other.show()
    box.hide_pages.setChecked(True)
    _defocus(qtbot, other)
    _click(box, QPoint(2, 2))
    assert box.hide_pages.hasFocus()

    sbox = StageBox(["cat"], provider)
    _expose(qtbot, sbox)
    sbox.hide_pages.setChecked(True)
    _defocus(qtbot, other)
    _click(sbox.status)
    assert sbox.op.hasFocus()


def test_stage_delete_icon_is_the_painted_red_cross(qtbot, provider):
    from pdftl.gui.icons import DELETE_RED

    sbox = StageBox(["cat"], provider)
    qtbot.addWidget(sbox)
    image = sbox.delete.icon().pixmap(QSize(32, 32)).toImage()
    centre = image.pixelColor(16, 16)
    assert (centre.red(), centre.green(), centre.blue()) == (
        DELETE_RED.red(),
        DELETE_RED.green(),
        DELETE_RED.blue(),
    )


def test_a_stage_box_is_draggable_but_a_plain_strip_box_is_not(qtbot, box, provider):
    sbox = StageBox(["cat"], provider)
    qtbot.addWidget(sbox)
    assert sbox.draggable and not box.draggable
