# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/gui/test_zoom.py

"""Tests for zoom: per-part font steps, clamping and persistence."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from pdftl.gui.zoom import MAX_FACTOR, MIN_POINTS, ZOOM_KEY, Zoom, ZoomArea


def _widget(qtbot, points=10.0):
    w = QWidget()
    font = QFont(w.font())
    font.setPointSizeF(points)
    w.setFont(font)
    QVBoxLayout(w).addWidget(QLabel("child"))
    qtbot.addWidget(w)
    return w


def test_steps_change_the_font_by_whole_points_and_zero_resets(qtbot):
    area = ZoomArea("a", "A", _widget(qtbot))
    area.step(2)
    assert area.widget.font().pointSizeF() == 12.0
    assert area.percent() == 120
    area.step(-3)
    assert area.widget.font().pointSizeF() == 9.0
    area.step(0)
    assert area.widget.font().pointSizeF() == 10.0


def test_children_follow_the_zoomed_font(qtbot):
    area = ZoomArea("a", "A", _widget(qtbot))
    area.step(4)
    assert area.widget.findChild(QLabel).font().pointSizeF() == 14.0


def test_sizes_are_clamped(qtbot):
    area = ZoomArea("a", "A", _widget(qtbot))
    area.step(-100)
    assert area.size() == MIN_POINTS
    area.step(1000)
    assert area.size() == 10.0 * MAX_FACTOR


def test_a_pixel_sized_font_starts_from_its_actual_point_size(qtbot):
    w = _widget(qtbot)
    font = QFont(w.font())
    font.setPixelSize(20)
    w.setFont(font)
    area = ZoomArea("a", "A", w)
    assert area.base == pytest.approx(20 * 72 / w.logicalDpiY())
    assert area.size() == area.base
    area.step(1)
    assert area.widget.font().pointSizeF() == area.base + 1


def test_a_size_set_elsewhere_is_read_back(qtbot):
    w = _widget(qtbot)
    area = ZoomArea("a", "A", w)
    font = QFont(w.font())
    font.setPointSizeF(13.0)
    w.setFont(font)
    area.step(1)
    assert area.size() == 14.0


def test_the_focused_part_zooms_and_anything_else_zooms_the_last(qtbot):
    first, last = _widget(qtbot), _widget(qtbot)
    zoom = Zoom([ZoomArea("f", "F", first), ZoomArea("l", "L", last)])
    child = first.findChild(QLabel)
    assert zoom.step(child, 1).name == "f"
    assert zoom.step(first, 1).name == "f"
    assert zoom.step(None, 1).name == "l"
    assert zoom.step(QLabel(), 1).name == "l"
    assert first.font().pointSizeF() == 12.0
    assert last.font().pointSizeF() == 12.0


def test_zoom_survives_save_and_restore(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "z.ini"), QSettings.Format.IniFormat)
    zoom = Zoom([ZoomArea("f", "F", _widget(qtbot)), ZoomArea("l", "L", _widget(qtbot))])
    zoom.areas[0].step(3)
    zoom.save(settings)
    settings.sync()
    reread = QSettings(str(tmp_path / "z.ini"), QSettings.Format.IniFormat)
    assert reread.value(ZOOM_KEY.format("f"), type=float) == 3.0
    again = Zoom([ZoomArea("f", "F", _widget(qtbot)), ZoomArea("l", "L", _widget(qtbot))])
    again.restore(reread)
    assert again.areas[0].size() == 13.0
    assert again.areas[1].size() == 10.0
