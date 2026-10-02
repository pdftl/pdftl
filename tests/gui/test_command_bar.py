# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/gui/test_command_bar.py

"""Tests for command_bar: pdftl command-line text <-> GUI Pipeline."""

import shlex
from pathlib import Path

import pikepdf
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent

from pdftl.cli.parser import _get_flag_keywords, _get_value_keywords
from pdftl.gui import shell_style
from pdftl.gui.command_bar import CommandBar, ParsedCommand, parse_command, parse_tokens
from pdftl.gui.interfaces import InputFile, Pipeline, Stage
from pdftl.gui.stage_model import to_cli
from pdftl.registry_init import initialize_registry

initialize_registry()

# Tokens known not to collide with any registered CLI keyword, safe as
# arbitrary operation arguments in the round-trip test.
_RESERVED = _get_flag_keywords() | _get_value_keywords() | {"allow", "input_pw"}
_SAFE_ARGS = [t for t in ("1", "1-2", "2-end", "right", "left", "east", "west", "odd", "even")]
assert not (set(_SAFE_ARGS) & _RESERVED)


def _widths_and_rotations(path):
    with pikepdf.open(path) as pdf:
        return [(int(p.mediabox[2]), int(p.get("/Rotate", 0))) for p in pdf.pages]


@pytest.fixture
def marked(tmp_path):
    """Directory with a space holding A (8 pages, 101..108), B (5 pages,
    201..205), and encrypted C (3 pages, 301..303, password 's3 cret')."""
    d = tmp_path / "dir with space"
    d.mkdir()
    paths = {}
    for name, base, n, password in (
        ("a.pdf", 100, 8, None),
        ("b.pdf", 200, 5, None),
        ("c.pdf", 300, 3, "s3 cret"),
    ):
        pdf = pikepdf.new()
        for i in range(1, n + 1):
            pdf.add_blank_page(page_size=(base + i, 100))
        encryption = pikepdf.Encryption(owner=password, user=password) if password else False
        pdf.save(d / name, encryption=encryption)
        paths[name[0].upper()] = d / name
    return paths


def _run_and_widths(run_pdftl, argv, out_path):
    run_pdftl([str(a) for a in argv])
    assert out_path.exists(), f"expected output at {out_path} from {argv}"
    return _widths_and_rotations(out_path)


def _check_equivalent(run_pdftl, tmp_path, tokens, tag):
    """Run `tokens` (a list, possibly with non-str Path items) two ways: as
    given, and round-tripped through parse_command + Pipeline.to_argv."""
    text = shlex.join(str(t) for t in tokens)
    out1 = tmp_path / f"{tag}_direct.pdf"
    out2 = tmp_path / f"{tag}_parsed.pdf"
    direct_argv = [*tokens, "output", out1]
    widths1 = _run_and_widths(run_pdftl, direct_argv, out1)

    parsed = parse_command(text)
    reconstructed = parsed.pipeline.to_argv(out2)
    widths2 = _run_and_widths(run_pdftl, reconstructed, out2)
    assert widths1 == widths2, (text, reconstructed)


def test_single_stage_cat_with_page_spec(run_pdftl, marked, tmp_path):
    a = marked["A"]
    _check_equivalent(run_pdftl, tmp_path, [a, "cat", "1-3,5"], "single")


def test_multiple_named_inputs(run_pdftl, marked, tmp_path):
    a, b = marked["A"], marked["B"]
    tokens = [f"A={a}", f"B={b}", "cat", "A1-2", "B3-end"]
    _check_equivalent(run_pdftl, tmp_path, tokens, "multi")


def test_encrypted_input_with_password(run_pdftl, marked, tmp_path):
    a, c = marked["A"], marked["C"]
    tokens = [f"A={a}", f"C={c}", "input_pw", "C=s3 cret", "cat", "A1", "C2-3"]
    _check_equivalent(run_pdftl, tmp_path, tokens, "encrypted")


def test_rotate_direction_stage(run_pdftl, marked, tmp_path):
    a = marked["A"]
    tokens = [a, "cat", "1-4,6", "---", "rotate", "east"]
    _check_equivalent(run_pdftl, tmp_path, tokens, "rotate")


def test_later_stage_reuses_earlier_handle(run_pdftl, marked, tmp_path):
    a, b = marked["A"], marked["B"]
    tokens = [
        f"A={a}",
        f"B={b}",
        "cat",
        "A1-2",
        "---",
        f"B={b}",
        "cat",
        "_1-end",
        "B3",
    ]
    _check_equivalent(run_pdftl, tmp_path, tokens, "reuse")


def test_later_stage_introduces_new_input(run_pdftl, marked, tmp_path):
    a, b = marked["A"], marked["B"]
    tokens = [a, "cat", "1-2", "---", f"B={b}", "cat", "_", "B1-2"]
    _check_equivalent(run_pdftl, tmp_path, tokens, "newinput")


def test_three_stage_pipeline(run_pdftl, marked, tmp_path):
    a, b = marked["A"], marked["B"]
    tokens = [
        f"A={a}",
        f"B={b}",
        "cat",
        "A1-2",
        "B1",
        "---",
        "rotate",
        "east",
        "---",
        f"A={a}",
        "cat",
        "_",
        "A3",
    ]
    _check_equivalent(run_pdftl, tmp_path, tokens, "threestage")


def test_last_stage_output_options_move_to_pipeline_output_options(marked):
    a = marked["A"]
    text = shlex.join([str(a), "cat", "1-2", "uncompress", "owner_pw", "secret1"])
    parsed = parse_command(f"{text} output whatever.pdf")
    assert parsed.pipeline.stages[-1].args_text == "1-2"
    assert parsed.pipeline.output_options == "uncompress owner_pw secret1"
    assert parsed.output == Path("whatever.pdf")


def test_last_stage_output_option_survives_the_real_cli(run_pdftl, marked, tmp_path):
    a = marked["A"]
    tokens = [a, "cat", "1-2", "uncompress"]
    _check_equivalent(run_pdftl, tmp_path, tokens, "uncompress")


def test_multiple_output_options_move_to_pipeline_output_options(marked):
    a = marked["A"]
    parsed = parse_command(
        f"{shlex.quote(str(a))} cat 1-2 --- rotate east linearize drop_xmp output out.pdf"
    )
    assert parsed.pipeline.stages[-1].args_text == "east"
    assert parsed.pipeline.output_options == "linearize drop_xmp"


@pytest.mark.parametrize(
    ("cmd", "match"),
    [
        ("", "empty command"),
        ("a.pdf", "no operation"),
        ("a.pdf output out.pdf", "no operation"),
        ("PROMPT cat", "PROMPT"),
        ("- cat", "stdin"),
        ("- cat --- rotate right", "stdin"),
        ("a.pdf cat 1 output x.pdf --- rotate right output y.pdf", "only the last stage"),
        (
            "JOB a.pdf rotate right DONE main.pdf cat output out.pdf",
            "sub-pipelines",
        ),
        ("a.pdf b.pdf EACH rotate right DONE cat output out.pdf", "sub-pipelines"),
        ("a.pdf cat --- A=other.pdf rotate right", "reassign"),
    ],
)
def test_value_errors(cmd, match):
    with pytest.raises(ValueError, match=match):
        parse_command(cmd)


def test_underscore_in_first_stage_is_stdin_like_and_unsupported():
    with pytest.raises(ValueError, match="stdin"):
        parse_command("_ cat")


def test_unknown_style():
    with pytest.raises(ValueError, match="style"):
        parse_command("a.pdf cat", style="nonsense")


def test_windows_style_splits_quoted_paths_with_spaces():
    cmd = '"C:\\Program Files\\a.pdf" cat 1-2'
    parsed = parse_command(cmd, style="windows")
    assert parsed.pipeline.inputs[0].path == Path("C:\\Program Files\\a.pdf")
    assert parsed.pipeline.stages[0].args_text == "1-2"


def test_windows_style_drops_quoted_program_path():
    cmd = '"C:\\Program Files\\pdftl.exe" A=a.pdf cat 1-2 output out.pdf'
    parsed = parse_command(cmd, style="windows")
    assert [f.handle for f in parsed.pipeline.inputs] == ["A"]
    assert parsed.output == Path("out.pdf")


def test_parse_command_default_style_is_the_platform_style(monkeypatch):
    monkeypatch.setattr(shell_style, "_WINDOWS", True)
    cmd = '"C:\\Program Files\\a.pdf" cat 1-2'
    parsed = parse_command(cmd)
    assert parsed.pipeline.inputs[0].path == Path("C:\\Program Files\\a.pdf")


def test_args_text_rebuild_uses_windows_quoting_for_backslash_paths():
    parsed = parse_command("A=a.pdf stamp C:\\logo.pdf", style="windows")
    assert parsed.pipeline.stages[0].args_text == "C:\\logo.pdf"


@pytest.mark.parametrize(("style", "windows"), [("posix", False), ("windows", True)])
def test_to_cli_then_parse_command_round_trips_odd_paths(monkeypatch, style, windows):
    """A single argument with a space, a backslash and a quote survives
    to_cli -> parse_command on each style, with the platform default
    (`shell_style._WINDOWS`) matching, as `Stage.tokens` uses it too."""
    monkeypatch.setattr(shell_style, "_WINDOWS", windows)
    weird = 'a b\\c"d.pdf'
    args_text = shell_style.quote(weird, style)
    p = Pipeline(
        inputs=(InputFile("A", Path("in.pdf")),),
        stages=(Stage("stamp", args_text),),
    )
    text = to_cli(p, style=style, mask_passwords=False)
    parsed = parse_command(text, style=style)
    assert parsed.pipeline.stages[0].tokens() == [weird]


@pytest.mark.parametrize(
    ("prefix", "style"),
    [
        ("pdftl", "posix"),
        ("pdftl.exe", "posix"),
        ("/usr/bin/pdftl", "posix"),
        ("PDFTL.EXE", "posix"),
        ("C:\\tools\\pdftl.exe", "windows"),
    ],
)
def test_program_token_is_dropped(prefix, style):
    parsed = parse_command(f"{prefix} a.pdf cat 1-2", style=style)
    assert [f.handle for f in parsed.pipeline.inputs] == ["A"]
    assert parsed.pipeline.inputs[0].path == Path("a.pdf")


def test_no_leading_program_token_still_parses():
    parsed = parse_command("a.pdf cat 1-2")
    assert parsed.pipeline.inputs[0].path == Path("a.pdf")


# --- parse_tokens: the token-level entry point parse_command now delegates to ---


def test_parse_tokens_matches_parse_command_on_the_same_input():
    text = "A=a.pdf cat 1-2 output out.pdf"
    assert parse_tokens(shlex.split(text), style="posix") == parse_command(text, style="posix")


def test_parse_tokens_does_not_shell_split_or_unquote():
    """A token containing literal quote and backslash characters (as loaded
    verbatim from YAML) is kept exactly as given, unlike parse_command's text."""
    weird = 'a "quoted" b\\c.pdf'
    parsed = parse_tokens(["A=" + weird, "cat"], style="posix")
    assert parsed.pipeline.inputs[0].path == Path(weird)


def test_parse_tokens_does_not_drop_a_leading_program_token():
    """Unlike parse_command, tokens never carry a program name to strip: a
    first token that happens to read "pdftl" is an ordinary input file."""
    parsed = parse_tokens(["pdftl", "cat", "1-2"])
    assert parsed.pipeline.inputs[0].path == Path("pdftl")


def test_parse_tokens_empty_is_a_value_error():
    with pytest.raises(ValueError, match="empty command"):
        parse_tokens([])


def test_parse_tokens_unknown_style():
    with pytest.raises(ValueError, match="style"):
        parse_tokens(["a.pdf", "cat"], style="nonsense")


def test_parse_tokens_default_style_is_the_platform_style(monkeypatch):
    monkeypatch.setattr(shell_style, "_WINDOWS", True)
    parsed = parse_tokens(["A=a.pdf", "stamp", "C:\\logo.pdf"])
    assert parsed.pipeline.stages[0].args_text == "C:\\logo.pdf"


def test_parse_tokens_propagates_pipeline_model_errors():
    with pytest.raises(ValueError, match="stdin"):
        parse_tokens(["_", "cat"])


def test_unnamed_first_stage_inputs_get_lowest_free_handles():
    parsed = parse_command("C=a.pdf b.pdf d.pdf cat")
    assert [(f.handle, f.path) for f in parsed.pipeline.inputs] == [
        ("C", Path("a.pdf")),
        ("A", Path("b.pdf")),
        ("B", Path("d.pdf")),
    ]


def test_duplicate_handle_token_reassigns_the_earlier_one():
    """pdftl's own parser keeps only the last 'A=' association for a
    repeated handle; the earlier file is still an input, just unnamed."""
    parsed = parse_command("A=a.pdf A=b.pdf cat")
    assert [(f.handle, f.path) for f in parsed.pipeline.inputs] == [
        ("B", Path("a.pdf")),
        ("A", Path("b.pdf")),
    ]


def test_unnamed_later_stage_input_gets_free_handle():
    parsed = parse_command("a.pdf cat --- other.pdf cat")
    stage2 = parsed.pipeline.stages[1]
    assert stage2.inputs == ("B",)
    assert parsed.pipeline.inputs[1] == InputFile("B", Path("other.pdf"))


def test_later_stage_reusing_same_path_under_same_handle_is_ok():
    parsed = parse_command("A=a.pdf cat --- A=a.pdf cat")
    assert len(parsed.pipeline.inputs) == 1
    assert parsed.pipeline.stages[1].inputs == ("A",)


def test_password_on_later_stage_input():
    parsed = parse_command("a.pdf cat --- B=c.pdf input_pw B=sekrit cat B1")
    b = next(f for f in parsed.pipeline.inputs if f.handle == "B")
    assert b.password == "sekrit"


def test_allow_option_round_trips():
    parsed = parse_command("a.pdf cat allow printing assembly output out.pdf")
    assert parsed.pipeline.stages[0].args_text == ""
    assert parsed.pipeline.output_options == "allow Assembly Printing"


def test_non_final_stage_options_are_unaffected_and_fold_into_its_own_args():
    """Pre-existing behavior for a stage that isn't last: the real CLI ignores its
    options anyway (only the final stage's are honored), so they stay put; see
    stage_model.stage_problems for the hint steering these towards the Output box."""
    parsed = parse_command("a.pdf cat --- rotate east uncompress --- shuffle")
    assert parsed.pipeline.stages[0].args_text == ""
    assert parsed.pipeline.stages[1].args_text == "east uncompress"
    assert parsed.pipeline.stages[2].args_text == ""
    assert parsed.pipeline.output_options == ""


def test_native_parser_error_is_wrapped_as_value_error():
    with pytest.raises(ValueError, match="Missing value for keyword"):
        parse_command("a.pdf cat 1-2 output")


def test_all_handles_exhausted_first_stage():
    letters = " ".join(f"{chr(ord('A') + i)}=f{i}.pdf" for i in range(26))
    with pytest.raises(ValueError, match="26"):
        parse_command(f"{letters} extra.pdf cat")


def test_all_handles_exhausted_later_stage():
    first = " ".join(f"{chr(ord('A') + i)}=f{i}.pdf" for i in range(26))
    with pytest.raises(ValueError, match="26"):
        parse_command(f"{first} cat --- extra.pdf cat")


# --- Hypothesis round trip -------------------------------------------------

_handles = st.permutations(list("ABCD"))
_filenames = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Nd")), min_size=1, max_size=6
).map(lambda s: f"{s}.pdf")
_passwords = st.one_of(
    st.none(),
    st.text(alphabet=st.characters(whitelist_categories=("Ll", "Nd")), min_size=1, max_size=6),
)
_ops = st.sampled_from(["cat", "rotate", "shuffle"])
_args_text = st.lists(st.sampled_from(_SAFE_ARGS), min_size=0, max_size=3).map(shlex.join)


@st.composite
def _pipelines(draw):
    n_inputs = draw(st.integers(1, 4))
    handles = draw(_handles)[:n_inputs]
    inputs = tuple(InputFile(h, Path(draw(_filenames)), draw(_passwords)) for h in sorted(handles))
    all_handles = [f.handle for f in inputs]
    n_stages = draw(st.integers(1, 3))
    stages = []
    for i in range(n_stages):
        op = draw(_ops)
        args_text = draw(_args_text)
        if i == 0:
            stage_inputs: tuple[str, ...] = ()
        else:
            extra = draw(st.lists(st.sampled_from(all_handles), max_size=2, unique=True))
            stage_inputs = tuple(extra)
        stages.append(Stage(op, args_text, stage_inputs))
    output = draw(st.one_of(st.none(), st.just(Path("out.pdf"))))
    return Pipeline(inputs=inputs, stages=tuple(stages)), output


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_pipelines())
def test_round_trip(built):
    p, output = built
    text = to_cli(p, output, mask_passwords=False)
    parsed = parse_command(text)
    assert parsed == ParsedCommand(p, output)


# --- Qt widget --------------------------------------------------------------


def test_placeholder_text(qtbot):
    bar = CommandBar()
    qtbot.addWidget(bar)
    assert bar.placeholderText() == "Type a pdftl command and press Enter"


def test_enter_emits_submitted(qtbot):
    bar = CommandBar()
    qtbot.addWidget(bar)
    bar.setFocus()
    with qtbot.waitSignal(bar.submitted, timeout=1000) as blocker:
        qtbot.keyClicks(bar, "a.pdf cat 1-2")
        qtbot.keyClick(bar, Qt.Key.Key_Return)
    parsed = blocker.args[0]
    assert isinstance(parsed, ParsedCommand)
    assert parsed.pipeline.inputs[0].path == Path("a.pdf")


def test_enter_on_bad_command_emits_failed(qtbot):
    bar = CommandBar()
    qtbot.addWidget(bar)
    bar.setFocus()
    with qtbot.waitSignal(bar.failed, timeout=1000) as blocker:
        qtbot.keyClicks(bar, "a.pdf")
        qtbot.keyClick(bar, Qt.Key.Key_Return)
    assert "no operation" in blocker.args[0]


def test_show_pipeline_sets_text_without_emitting(qtbot):
    bar = CommandBar()
    qtbot.addWidget(bar)
    p = Pipeline(inputs=(InputFile("A", Path("a.pdf")),), stages=(Stage("cat", "1-2"),))
    received = []
    bar.submitted.connect(received.append)
    bar.show_pipeline(p, Path("out.pdf"))
    assert bar.text() == to_cli(p, Path("out.pdf"), mask_passwords=True)
    assert received == []


def test_history_up_down_cycles_and_returns_to_draft(qtbot):
    bar = CommandBar()
    qtbot.addWidget(bar)
    bar.setFocus()
    for cmd in ("a.pdf cat 1", "a.pdf cat 2"):
        qtbot.keyClicks(bar, cmd)
        qtbot.keyClick(bar, Qt.Key.Key_Return)
        bar.clear()
    qtbot.keyClicks(bar, "draft text")
    qtbot.keyClick(bar, Qt.Key.Key_Up)
    assert bar.text() == "a.pdf cat 2"
    qtbot.keyClick(bar, Qt.Key.Key_Up)
    assert bar.text() == "a.pdf cat 1"
    qtbot.keyClick(bar, Qt.Key.Key_Up)
    assert bar.text() == "a.pdf cat 1"
    qtbot.keyClick(bar, Qt.Key.Key_Down)
    assert bar.text() == "a.pdf cat 2"
    qtbot.keyClick(bar, Qt.Key.Key_Down)
    assert bar.text() == "draft text"


def test_history_up_with_no_history_is_a_no_op(qtbot):
    bar = CommandBar()
    qtbot.addWidget(bar)
    bar.setFocus()
    qtbot.keyClicks(bar, "abc")
    qtbot.keyClick(bar, Qt.Key.Key_Up)
    assert bar.text() == "abc"


def test_history_down_without_navigating_is_a_no_op(qtbot):
    bar = CommandBar()
    qtbot.addWidget(bar)
    bar.setFocus()
    qtbot.keyClicks(bar, "abc")
    qtbot.keyClick(bar, Qt.Key.Key_Down)
    assert bar.text() == "abc"


def test_escape_restores_shown_text(qtbot):
    bar = CommandBar()
    qtbot.addWidget(bar)
    bar.setFocus()
    p = Pipeline(inputs=(InputFile("A", Path("a.pdf")),), stages=(Stage("cat", "1-2"),))
    bar.show_pipeline(p, None)
    shown = bar.text()
    qtbot.keyClicks(bar, " extra junk")
    assert bar.text() != shown
    qtbot.keyClick(bar, Qt.Key.Key_Escape)
    assert bar.text() == shown


def test_escape_with_nothing_shown_is_a_no_op(qtbot):
    bar = CommandBar()
    qtbot.addWidget(bar)
    bar.setFocus()
    qtbot.keyClicks(bar, "abc")
    qtbot.keyClick(bar, Qt.Key.Key_Escape)
    assert bar.text() == "abc"


def test_normal_typing_and_editing_keys_still_work(qtbot):
    bar = CommandBar()
    qtbot.addWidget(bar)
    bar.setFocus()
    qtbot.keyClicks(bar, "hello")
    qtbot.keyClick(bar, Qt.Key.Key_Backspace)
    assert bar.text() == "hell"
    qtbot.keyClick(bar, Qt.Key.Key_Home)
    qtbot.keyClicks(bar, "X")
    assert bar.text() == "Xhell"


def _shown_bar(qtbot):
    bar = CommandBar()
    qtbot.addWidget(bar)
    p = Pipeline(inputs=(InputFile("A", Path("a.pdf")),), stages=(Stage("cat", "1-2"),))
    bar.show_pipeline(p, None)
    return bar


def _leave(bar, reason):
    bar.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut, reason))


def test_leaving_with_an_unapplied_edit_reverts_and_keeps_it_in_history(qtbot):
    bar = _shown_bar(qtbot)
    shown = bar.text()
    bar.setText(shown + " --- rotate 1east")
    with qtbot.waitSignal(bar.discarded) as signal:
        _leave(bar, Qt.FocusReason.TabFocusReason)
    assert signal.args == [shown + " --- rotate 1east"]
    assert bar.text() == shown and not bar.is_dirty()
    bar.setFocus()
    qtbot.keyClick(bar, Qt.Key.Key_Up)
    assert bar.text() == shown + " --- rotate 1east"
    bar.setText(shown + " --- rotate 1east")
    _leave(bar, Qt.FocusReason.MouseFocusReason)
    assert bar._history == [shown + " --- rotate 1east"]


def test_menus_and_other_windows_keep_the_edit(qtbot):
    bar = _shown_bar(qtbot)
    edited = bar.text() + " x"
    bar.setText(edited)
    with qtbot.assertNotEmitted(bar.discarded):
        _leave(bar, Qt.FocusReason.PopupFocusReason)
        _leave(bar, Qt.FocusReason.ActiveWindowFocusReason)
    assert bar.text() == edited


def test_leaving_without_an_edit_emits_nothing(qtbot):
    bar = _shown_bar(qtbot)
    with qtbot.assertNotEmitted(bar.discarded):
        _leave(bar, Qt.FocusReason.TabFocusReason)
    fresh = CommandBar()
    qtbot.addWidget(fresh)
    fresh.setText("typed before any pipeline")
    assert not fresh.is_dirty()


def test_an_unapplied_edit_is_tinted(qtbot):
    bar = _shown_bar(qtbot)
    clean = bar.palette().base().color()
    bar.setText(bar.text() + " x")
    assert bar.palette().base().color() != clean
    qtbot.keyClick(bar, Qt.Key.Key_Escape)
    assert bar.palette().base().color() == clean
