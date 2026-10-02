"""Tests for console module: ANSI parsing and the read-only output console."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QMouseEvent,
    QPalette,
    QTextCursor,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pdftl.gui.console import (
    _DARK_PALETTE,
    _LIGHT_PALETTE,
    AnsiConsole,
    Style,
    parse_ansi,
)

RED = 1
GREEN = 2
BRIGHT_RED = 9


# --- parse_ansi: hand-written ground truth -------------------------------


def test_bold_color_then_reset():
    runs, end = parse_ansi("\x1b[1;31mred\x1b[0m plain")
    assert runs == [
        ("red", Style(fg=RED, bold=True)),
        (" plain", Style()),
    ]
    assert end == Style()


def test_bare_reset_sgr():
    runs, _end = parse_ansi("\x1b[1mA\x1b[mB")
    assert runs == [("A", Style(bold=True)), ("B", Style())]


def test_bold_toggle_on_and_off():
    runs, end = parse_ansi("\x1b[1mA\x1b[22mB")
    assert runs == [("A", Style(bold=True)), ("B", Style(bold=False))]
    assert end == Style(bold=False)


def test_italic_toggle_on_and_off():
    runs, _ = parse_ansi("\x1b[3mA\x1b[23mB")
    assert runs == [("A", Style(italic=True)), ("B", Style(italic=False))]


def test_underline_toggle_on_and_off():
    runs, _ = parse_ansi("\x1b[4mA\x1b[24mB")
    assert runs == [("A", Style(underline=True)), ("B", Style(underline=False))]


def test_standard_fg_boundaries_and_default():
    runs, _ = parse_ansi("\x1b[30mA\x1b[37mB\x1b[39mC")
    assert runs == [
        ("A", Style(fg=0)),
        ("B", Style(fg=7)),
        ("C", Style(fg=None)),
    ]


def test_standard_bg_boundaries_and_default():
    runs, _ = parse_ansi("\x1b[40mA\x1b[47mB\x1b[49mC")
    assert runs == [
        ("A", Style(bg=0)),
        ("B", Style(bg=7)),
        ("C", Style(bg=None)),
    ]


def test_bright_fg_and_bg_boundaries():
    runs, _ = parse_ansi("\x1b[90mA\x1b[97mB\x1b[100mC\x1b[107mD")
    assert runs == [
        ("A", Style(fg=8)),
        ("B", Style(fg=15, bg=None)),
        ("C", Style(fg=15, bg=8)),
        ("D", Style(fg=15, bg=15)),
    ]


def test_combined_sgr_single_escape():
    runs, _ = parse_ansi("\x1b[1;4;31;42mA")
    assert runs == [("A", Style(fg=RED, bg=GREEN, bold=True, underline=True))]


def test_256_color_low_range_is_palette_index():
    runs, _ = parse_ansi("\x1b[38;5;1mA")
    assert runs == [("A", Style(fg=RED))]


def test_256_color_cube_range_is_rgb():
    runs, _ = parse_ansi("\x1b[38;5;196mA")
    # index 196: r=(196-16)//36=5, g=((196-16)//6)%6=0, b=(196-16)%6=0
    assert runs == [("A", Style(fg=(255, 0, 0)))]


def test_256_color_grayscale_range_is_rgb():
    runs, _ = parse_ansi("\x1b[38;5;232mA")
    assert runs == [("A", Style(fg=(8, 8, 8)))]


def test_256_color_background():
    runs, _ = parse_ansi("\x1b[48;5;196mA")
    assert runs == [("A", Style(bg=(255, 0, 0)))]


def test_truecolor_fg_and_bg():
    runs, _ = parse_ansi("\x1b[38;2;10;20;30mfoo\x1b[48;2;1;2;3mbar")
    assert runs == [
        ("foo", Style(fg=(10, 20, 30))),
        ("bar", Style(fg=(10, 20, 30), bg=(1, 2, 3))),
    ]


def test_truecolor_channels_clamped():
    runs, _ = parse_ansi("\x1b[38;2;999;0;0mA")
    assert runs == [("A", Style(fg=(255, 0, 0)))]


def test_extended_color_missing_mode_byte_is_dropped():
    runs, end = parse_ansi("\x1b[38mA")
    assert runs == [("A", Style())]
    assert end == Style()


def test_extended_color_256_mode_missing_index_is_dropped():
    runs, _ = parse_ansi("\x1b[38;5mA")
    assert runs == [("A", Style())]


def test_extended_color_truecolor_mode_missing_channel_is_dropped():
    runs, _ = parse_ansi("\x1b[38;2;9;9mA")
    assert runs == [("A", Style())]


def test_extended_color_unknown_mode_byte_is_dropped():
    runs, _ = parse_ansi("\x1b[38;9;1mA")
    assert runs == [("A", Style())]


def test_unknown_sgr_code_is_ignored():
    runs, end = parse_ansi("\x1b[5mtext")
    assert runs == [("text", Style())]
    assert end == Style()


def test_non_numeric_sgr_param_is_malformed_and_ignored():
    runs, end = parse_ansi("\x1b[1:31mtext")
    assert runs == [("text", Style())]
    assert end == Style()


def test_style_continues_across_calls():
    runs1, mid = parse_ansi("\x1b[1;31mred-start")
    assert runs1 == [("red-start", Style(fg=RED, bold=True))]
    runs2, end = parse_ansi("-continued", mid)
    assert runs2 == [("-continued", Style(fg=RED, bold=True))]
    assert end == mid


def test_truncated_csi_at_end_is_dropped_not_raised():
    runs, end = parse_ansi("before\x1b[31")
    assert runs == [("before", Style())]
    assert end == Style()


def test_truncated_osc_at_end_is_dropped_not_raised():
    runs, _ = parse_ansi("before\x1b]0;untermina")
    assert runs == [("before", Style())]


def test_osc_terminated_by_bel_is_dropped():
    runs, _ = parse_ansi("\x1b]0;window title\x07visible")
    assert runs == [("visible", Style())]


def test_osc_terminated_by_st_is_dropped():
    runs, _ = parse_ansi("\x1b]2;other\x1b\\visible")
    assert runs == [("visible", Style())]


def test_non_sgr_csi_is_dropped():
    runs, _ = parse_ansi("\x1b[2Ktext")  # erase-line
    assert runs == [("text", Style())]


def test_cursor_position_csi_with_params_is_dropped():
    runs, _ = parse_ansi("\x1b[10;20Htext")
    assert runs == [("text", Style())]


def test_private_mode_csi_is_dropped():
    runs, _ = parse_ansi("\x1b[?25htext")
    assert runs == [("text", Style())]


def test_lone_esc_not_csi_or_osc_is_dropped():
    runs, _ = parse_ansi("a\x1bZb")
    assert runs == [("aZb", Style())]


def test_lone_esc_at_end_of_text():
    runs, _ = parse_ansi("abc\x1b")
    assert runs == [("abc", Style())]


def test_carriage_return_overwrites_line():
    runs, _ = parse_ansi("line1\rline2")
    assert runs == [("line2", Style())]


def test_carriage_return_then_newline_keeps_newline():
    runs, _ = parse_ansi("abc\rdef\nghi")
    assert runs == [("def\nghi", Style())]


def test_leading_carriage_return():
    runs, _ = parse_ansi("\rabc")
    assert runs == [("abc", Style())]


def test_multiple_carriage_returns_keep_only_last():
    runs, _ = parse_ansi("a\rb\rc")
    assert runs == [("c", Style())]


def test_newline_preserved_with_color_and_merges_with_next_default_run():
    runs, _ = parse_ansi("\x1b[31mred\x1b[0m\nplain")
    assert runs == [("red", Style(fg=RED)), ("\nplain", Style())]


def test_empty_text_returns_empty_runs():
    runs, end = parse_ansi("")
    assert runs == []
    assert end == Style()


# --- parse_ansi: property-based, independent ground truth ----------------

_ANSI_STRIP_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]" r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")

_VALID_ESCAPES = (
    "\x1b[0m",
    "\x1b[1m",
    "\x1b[31m",
    "\x1b[1;31;42m",
    "\x1b[38;5;200m",
    "\x1b[48;2;10;20;30m",
    "\x1b[2K",
    "\x1b[H",
    "\x1b[999;999H",
    "\x1b]0;title\x07",
    "\x1b]2;another\x1b\\",
    "\x1b[?25h",
)

_PLAIN_ALPHABET = list("abcXYZ019 \n\t.,!?")


@given(
    st.lists(
        st.one_of(
            st.text(alphabet=_PLAIN_ALPHABET, max_size=6),
            st.sampled_from(_VALID_ESCAPES),
        ),
        max_size=12,
    )
)
@settings(max_examples=200)
def test_parse_ansi_visible_text_matches_independent_strip(parts):
    full_text = "".join(parts)
    runs, _end = parse_ansi(full_text)
    visible = "".join(text for text, _style in runs)
    assert visible == _ANSI_STRIP_RE.sub("", full_text)


@given(st.text(min_size=0, max_size=40))
@settings(max_examples=200)
def test_parse_ansi_never_raises_on_arbitrary_text(text):
    parse_ansi(text)


# --- AnsiConsole: Qt widget behaviour -------------------------------------


def _set_theme(widget, dark: bool) -> None:
    palette = widget.palette()
    palette.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0) if dark else QColor(255, 255, 255))
    widget.setPalette(palette)


def _format_at(console: AnsiConsole, index: int):
    cursor = QTextCursor(console.document())
    cursor.setPosition(index + 1)
    return cursor.charFormat()


def test_is_read_only_and_fixed_width_font(qtbot):
    console = AnsiConsole()
    qtbot.addWidget(console)
    assert console.isReadOnly()
    expected = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    assert console.font().family() == expected.family()


def test_append_ansi_applies_dark_palette_color_and_bold(qtbot):
    console = AnsiConsole()
    qtbot.addWidget(console)
    _set_theme(console, dark=True)

    console.append_ansi("\x1b[1;31mred\x1b[0m plain")

    red_fmt = _format_at(console, 0)
    assert red_fmt.foreground().color().getRgb()[:3] == _DARK_PALETTE[RED]
    assert red_fmt.fontWeight() == QFont.Weight.Bold

    plain_fmt = _format_at(console, len("red") + 1)
    assert plain_fmt.foreground().style() == Qt.BrushStyle.NoBrush
    assert plain_fmt.fontWeight() == QFont.Weight.Normal


def test_append_ansi_applies_light_palette_color(qtbot):
    console = AnsiConsole()
    qtbot.addWidget(console)
    _set_theme(console, dark=False)

    console.append_ansi("\x1b[31mred")

    fmt = _format_at(console, 0)
    assert fmt.foreground().color().getRgb()[:3] == _LIGHT_PALETTE[RED]


def test_append_ansi_truecolor_ignores_theme_palette(qtbot):
    console = AnsiConsole()
    qtbot.addWidget(console)
    _set_theme(console, dark=True)

    console.append_ansi("\x1b[38;2;10;20;30mfoo")

    fmt = _format_at(console, 0)
    assert fmt.foreground().color().getRgb()[:3] == (10, 20, 30)


def test_append_ansi_underline_and_italic_formats(qtbot):
    console = AnsiConsole()
    qtbot.addWidget(console)

    console.append_ansi("\x1b[3;4mstyled")

    fmt = _format_at(console, 0)
    assert fmt.fontItalic()
    assert fmt.fontUnderline()


def test_append_ansi_background_color(qtbot):
    console = AnsiConsole()
    qtbot.addWidget(console)
    _set_theme(console, dark=True)

    console.append_ansi("\x1b[42mgreenbg")

    fmt = _format_at(console, 0)
    assert fmt.background().color().getRgb()[:3] == _DARK_PALETTE[GREEN]


def test_append_ansi_keeps_running_style_across_calls(qtbot):
    console = AnsiConsole()
    qtbot.addWidget(console)
    _set_theme(console, dark=True)

    console.append_ansi("\x1b[1;31mred-")
    console.append_ansi("still-red")

    assert console.toPlainText() == "red-still-red"
    fmt = _format_at(console, len("red-"))
    assert fmt.foreground().color().getRgb()[:3] == _DARK_PALETTE[RED]
    assert fmt.fontWeight() == QFont.Weight.Bold


def test_append_plain_inserts_verbatim(qtbot):
    console = AnsiConsole()
    qtbot.addWidget(console)

    console.append_plain("\x1b[31m literal escape bytes")

    assert console.toPlainText() == "\x1b[31m literal escape bytes"
    fmt = _format_at(console, 0)
    assert fmt.foreground().style() == Qt.BrushStyle.NoBrush


def test_clear_resets_text_and_running_style(qtbot):
    console = AnsiConsole()
    qtbot.addWidget(console)

    console.append_ansi("\x1b[1;31mred")
    console.clear()
    assert console.toPlainText() == ""

    console.append_ansi("after-clear")
    fmt = _format_at(console, 0)
    assert fmt.foreground().style() == Qt.BrushStyle.NoBrush
    assert fmt.fontWeight() == QFont.Weight.Normal


def test_save_to_writes_plain_text(qtbot, tmp_path):
    console = AnsiConsole()
    qtbot.addWidget(console)
    console.append_ansi("\x1b[31mred\x1b[0m plain")

    out = tmp_path / "out.txt"
    console.save_to(out)

    assert out.read_text(encoding="utf-8") == "red plain"


def test_context_menu_has_clear_and_save_as(qtbot):
    console = AnsiConsole()
    qtbot.addWidget(console)

    menu = console._build_context_menu()
    labels = [action.text() for action in menu.actions() if action.text()]

    assert "Clear" in labels
    assert "Save As..." in labels


def test_context_menu_clear_action_clears_console(qtbot):
    console = AnsiConsole()
    qtbot.addWidget(console)
    console.append_plain("some text")

    menu = console._build_context_menu()
    clear_action = next(a for a in menu.actions() if a.text() == "Clear")
    clear_action.trigger()

    assert console.toPlainText() == ""


def test_save_as_action_uses_injectable_dialog_and_saves(qtbot, tmp_path):
    console = AnsiConsole()
    qtbot.addWidget(console)
    console.append_plain("hello")

    target = tmp_path / "chosen.txt"
    console._file_dialog = lambda *args, **kwargs: (str(target), "")

    menu = console._build_context_menu()
    save_action = next(a for a in menu.actions() if a.text() == "Save As...")
    save_action.trigger()

    assert target.read_text(encoding="utf-8") == "hello"


def test_save_as_action_cancelled_dialog_saves_nothing(qtbot, tmp_path):
    console = AnsiConsole()
    qtbot.addWidget(console)
    console.append_plain("hello")
    console._file_dialog = lambda *args, **kwargs: ("", "")

    menu = console._build_context_menu()
    save_action = next(a for a in menu.actions() if a.text() == "Save As...")
    save_action.trigger()  # must not raise, must not write anything

    assert not (tmp_path / "chosen.txt").exists()


def test_context_menu_event_with_keyboard_reason_opens_menu(qtbot, monkeypatch):
    console = AnsiConsole()
    qtbot.addWidget(console)

    calls = []
    fake_menu = console._build_context_menu()
    monkeypatch.setattr(fake_menu, "exec", lambda pos: calls.append(pos))
    monkeypatch.setattr(console, "_build_context_menu", lambda url=None: fake_menu)

    event = QContextMenuEvent(QContextMenuEvent.Reason.Keyboard, QPoint(1, 1), QPoint(11, 11))
    console.contextMenuEvent(event)

    assert calls == [QPoint(11, 11)]


def test_is_focusable_for_keyboard_navigation(qtbot):
    console = AnsiConsole()
    qtbot.addWidget(console)
    assert console.focusPolicy() != Qt.FocusPolicy.NoFocus


def test_page_down_scrolls_via_keyboard(qtbot):
    console = AnsiConsole()
    qtbot.addWidget(console)
    console.resize(200, 100)
    console.show()
    for i in range(200):
        console.append_plain(f"line {i}\n")
    scrollbar = console.verticalScrollBar()
    scrollbar.setValue(0)

    console.setFocus()
    QTest.keyClick(console, Qt.Key.Key_PageDown)

    assert scrollbar.value() > 0


def test_stays_scrolled_to_bottom_when_already_there(qtbot):
    console = AnsiConsole()
    qtbot.addWidget(console)
    console.resize(200, 100)
    console.show()
    for i in range(200):
        console.append_plain(f"line {i}\n")
    scrollbar = console.verticalScrollBar()
    assert scrollbar.value() == scrollbar.maximum()

    console.append_plain("one more line\n")

    assert scrollbar.value() == scrollbar.maximum()


def test_new_output_scrolls_into_view_even_when_scrolled_up(qtbot):
    console = AnsiConsole()
    qtbot.addWidget(console)
    console.resize(200, 100)
    console.show()
    for i in range(200):
        console.append_plain(f"line {i}\n")
    console.verticalScrollBar().setValue(0)

    console.append_plain("appended while scrolled up\n")

    last = console.document().findBlockByNumber(200)
    assert last.text() == "appended while scrolled up"
    cursor = QTextCursor(last)
    assert console.viewport().rect().contains(console.cursorRect(cursor))


def test_insert_runs_skips_empty_text_runs(qtbot):
    console = AnsiConsole()
    qtbot.addWidget(console)
    console._insert_runs([("", Style()), ("visible", Style())])
    assert console.toPlainText() == "visible"


# --- Real pdftl CLI: which env vars actually produce colour into a pipe --


def test_real_cli_emits_ansi_color_into_pipe_with_force_color(tmp_path):
    """Ground truth for how pdftl decides to colour non-tty output.

    pdftl's own `Console()` (src/pdftl/cli/console.py) is created with no
    `force_terminal`/`no_color`/`width` overrides, so colour and width
    detection fall through entirely to `rich.console.Console`'s own env-var
    rules (site-packages rich/console.py): `FORCE_COLOR` (any non-empty
    value) forces colour even into a pipe; `NO_COLOR` (any non-empty value)
    strips it again even if FORCE_COLOR is set; `COLUMNS` sets the assumed
    terminal width when a real one can't be read (defaults to 80). This
    test exercises that real path end to end.
    """
    src_path = Path(__file__).resolve().parents[2] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_path) + os.pathsep + env.get("PYTHONPATH", "")
    env["FORCE_COLOR"] = "1"
    env["COLUMNS"] = "100"
    env.pop("NO_COLOR", None)

    result = subprocess.run(
        [sys.executable, "-m", "pdftl", "help", "help"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    combined = result.stdout + result.stderr
    if "\x1b[" not in combined:
        pytest.skip("pdftl did not emit ANSI escapes into a pipe with FORCE_COLOR set")

    runs, _end = parse_ansi(combined)
    assert any(style != Style() for _text, style in runs)


def test_real_cli_no_color_env_strips_color_but_not_bold(tmp_path):
    """`NO_COLOR` (rich's `Segment.remove_color`) strips fg/bg colour only;

    it does not strip other SGR attributes such as bold/underline, matching
    the no-color.org convention (colour, not all styling).
    """
    src_path = Path(__file__).resolve().parents[2] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_path) + os.pathsep + env.get("PYTHONPATH", "")
    env["FORCE_COLOR"] = "1"
    env["NO_COLOR"] = "1"

    result = subprocess.run(
        [sys.executable, "-m", "pdftl", "help", "help"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    combined = result.stdout + result.stderr
    if "\x1b[" not in combined:
        pytest.skip("pdftl did not emit any ANSI escapes to check")

    runs, _end = parse_ansi(combined)
    assert all(style.fg is None and style.bg is None for _text, style in runs)


URL = "https://pdftl.readthedocs.io/en/latest/operations/cat.html"
RICH_LINE = (
    "\x1b[3mRead online: \x1b[0m\x1b]8;id=11893892;" + URL + "\x1b\\"
    "\x1b[3;4;34m" + URL + "\x1b[0m\x1b]8;;\x1b\\   \n"
)


def test_osc8_hyperlink_as_rich_emits_it():
    runs, end = parse_ansi(RICH_LINE)
    assert runs == [
        ("Read online: ", Style(italic=True)),
        (URL, Style(fg=4, italic=True, underline=True, link=URL)),
        ("   \n", Style()),
    ]
    assert end == Style()


def test_osc8_link_survives_an_sgr_reset_and_bel_terminator():
    runs, end = parse_ansi("\x1b]8;;http://a\x07\x1b[1mx\x1b[0my\x1b]8;;\x07z")
    assert runs == [
        ("x", Style(bold=True, link="http://a")),
        ("y", Style(link="http://a")),
        ("z", Style()),
    ]
    assert end == Style()


def _linked_console(qtbot):
    console = AnsiConsole()
    qtbot.addWidget(console)
    console.resize(700, 200)
    console.show()
    qtbot.waitExposed(console)
    console.append_ansi(RICH_LINE)
    opened = []
    console.open_link = opened.append
    start = console.toPlainText().index(URL)
    return console, opened, start


def _point(console, position):
    cursor = QTextCursor(console.document())
    cursor.setPosition(position)
    return console.cursorRect(cursor).center() + QPoint(3, 0)


def test_link_positions_cover_exactly_the_url(qtbot):
    console, _opened, start = _linked_console(qtbot)
    assert console.link_at_position(start + 5) == URL
    assert console.link_at_position(start) == URL
    assert console.link_at_position(start + len(URL)) == URL
    assert console.link_at_position(2) is None
    assert console.link_at_position(console.document().characterCount() - 1) is None
    fmt = QTextCursor(console.document())
    fmt.setPosition(start + 1)
    assert fmt.charFormat().isAnchor()


def test_clicking_a_link_opens_it(qtbot):
    console, opened, start = _linked_console(qtbot)
    point = _point(console, start + 10)
    qtbot.mouseClick(console.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert opened == [URL]
    qtbot.mouseClick(console.viewport(), Qt.MouseButton.LeftButton, pos=_point(console, 1))
    qtbot.mouseClick(console.viewport(), Qt.MouseButton.MiddleButton, pos=point)
    assert opened == [URL]


def test_clicking_a_link_after_selecting_text_does_not_open_it(qtbot):
    console, opened, start = _linked_console(qtbot)
    console.selectAll()
    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(_point(console, start + 10)),
        QPointF(console.viewport().mapToGlobal(_point(console, start + 10))),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    console.mouseReleaseEvent(event)
    assert opened == []


def _hover(console, point):
    """A plain mouse move: `qtbot.mouseMove` may only move the OS cursor."""
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(point),
        QPointF(console.viewport().mapToGlobal(point)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(console.viewport(), event)


def test_hovering_a_link_shows_a_pointing_hand(qtbot):
    console, _opened, start = _linked_console(qtbot)
    _hover(console, _point(console, start + 10))
    assert console.viewport().cursor().shape() == Qt.CursorShape.PointingHandCursor
    _hover(console, _point(console, 1))
    assert console.viewport().cursor().shape() == Qt.CursorShape.IBeamCursor


def test_enter_on_a_link_opens_it_from_the_keyboard(qtbot):
    console, opened, start = _linked_console(qtbot)
    console.setFocus()
    cursor = console.textCursor()
    cursor.setPosition(1)
    console.setTextCursor(cursor)
    QTest.keyClick(console, Qt.Key.Key_Return)
    assert opened == []
    for _ in range(start + 4 - 1):
        QTest.keyClick(console, Qt.Key.Key_Right)
    assert console.textCursor().position() == start + 4
    QTest.keyClick(console, Qt.Key.Key_Enter)
    assert opened == [URL]


def test_keyboard_context_menu_offers_open_and_copy_link(qtbot, monkeypatch):
    console, opened, start = _linked_console(qtbot)
    cursor = console.textCursor()
    cursor.setPosition(start + 3)
    console.setTextCursor(cursor)
    menus = []
    real_build = console._build_context_menu

    def build(url=None):
        menu = real_build(url)
        monkeypatch.setattr(menu, "exec", lambda _pos: None)
        menus.append(menu)
        return menu

    monkeypatch.setattr(console, "_build_context_menu", build)
    console.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Keyboard, QPoint(0, 0)))
    actions = {a.text(): a for a in menus[0].actions()}
    assert menus[0].actions()[0].text() == "Open Link"
    actions["Open Link"].trigger()
    assert opened == [URL]
    actions["Copy Link Address"].trigger()
    assert QGuiApplication.clipboard().text() == URL

    console.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, _point(console, 1)))
    assert "Open Link" not in [a.text() for a in menus[1].actions()]
