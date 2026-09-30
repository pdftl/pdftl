import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import QMenu, QPushButton

from pdftl.gui.menu_style import DimShortcutStyle, blend


def _menu(qtbot, styled):
    menu = QMenu()
    qtbot.addWidget(menu)
    if styled:
        menu.style_ = DimShortcutStyle()
        menu.setStyle(menu.style_)
    for text, key in (("Alpha", "Ctrl+O"), ("Beta", "Ctrl+Q"), ("Gamma", None)):
        action = QAction(text, menu)
        if key:
            action.setShortcut(QKeySequence(key))
        menu.addAction(action)
    menu.popup(menu.pos())
    return menu


def _darkest(image, rect):
    return min(
        QColor(image.pixel(x, y)).lightness()
        for x in range(rect.left(), rect.right())
        for y in range(rect.top(), rect.bottom())
    )


def _halves(menu, action):
    rect = menu.actionGeometry(action)
    left = rect.adjusted(0, 0, -rect.width() // 2, 0)
    right = rect.adjusted(rect.width() * 2 // 3, 0, 0, 0)
    return left, right


def test_shortcut_is_lighter_than_the_label(qtbot):
    plain, styled = _menu(qtbot, False), _menu(qtbot, True)
    for menu in (plain, styled):
        menu.image_ = menu.grab().toImage()
    alpha_plain, alpha_styled = plain.actions()[0], styled.actions()[0]
    left, right = _halves(plain, alpha_plain)
    assert _darkest(plain.image_, right) < 60
    left, right = _halves(styled, alpha_styled)
    assert _darkest(styled.image_, left) < 60
    assert 90 < _darkest(styled.image_, right) < 220


def test_item_without_shortcut_is_unchanged(qtbot):
    plain, styled = _menu(qtbot, False), _menu(qtbot, True)
    rect = plain.actionGeometry(plain.actions()[2])
    assert plain.grab(rect).toImage() == styled.grab(rect).toImage()


def test_selected_shortcut_stays_visible_on_the_highlight(qtbot):
    menu = _menu(qtbot, True)
    menu.setActiveAction(menu.actions()[1])
    image = menu.grab().toImage()
    _, right = _halves(menu, menu.actions()[1])
    highlight = menu.palette().highlight().color().lightness()
    brightest = max(
        QColor(image.pixel(x, y)).lightness()
        for x in range(right.left(), right.right())
        for y in range(right.top(), right.bottom())
    )
    assert brightest > highlight + 60


def test_other_widgets_draw_as_the_base_style(qtbot):
    style = DimShortcutStyle()
    plain, styled = QPushButton("OK"), QPushButton("OK")
    styled.setStyle(style)
    for button in (plain, styled):
        qtbot.addWidget(button)
        button.resize(80, 30)
    assert plain.grab().toImage() == styled.grab().toImage()


def test_blend_weights_the_first_colour():
    mixed = blend(QColor(255, 0, 0), QColor(0, 0, 255), 0.75)
    assert (mixed.red(), mixed.green(), mixed.blue()) == (191, 0, 64)
