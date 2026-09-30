"""Tests for window_run: the command bar, runs and stage results."""

import shlex

import pytest

pytest.importorskip("PySide6")


from pdftl.gui import window_run
from pdftl.gui.command_bar import ParsedCommand, parse_command
from pdftl.gui.engine import SubprocessEngine
from pdftl.gui.interfaces import InputFile, Pipeline, ResultKind, Stage, StageResult
from pdftl.gui.window_run import Runner
from tests.gui.window_support import (
    _GatedEngine,
    _make_pdf,
    _pages,
    _run_args,
    _SlowEngine,
    _use_runner,
    _with_second_input,
    fire,
    press,
    replace_selection,
    type_text,
)


def test_apply_command_retains_known_password_for_masked_prompt(make_window, tmp_path):
    window = make_window([])
    enc = _make_pdf(tmp_path / "enc.pdf", [10], password="secret")
    window.pipeline = Pipeline(inputs=(InputFile("A", enc, "secret"),))
    parsed = ParsedCommand(
        pipeline=Pipeline(inputs=(InputFile("A", enc, "PROMPT"),), stages=(Stage("cat", "1"),))
    )
    window.apply_command(parsed)
    assert window.pipeline.inputs[0].password == "secret"


def test_apply_command_leaves_unmasked_password_alone(make_window, tmp_path):
    window = make_window([])
    plain = _make_pdf(tmp_path / "plain.pdf", [10])
    parsed = ParsedCommand(
        pipeline=Pipeline(inputs=(InputFile("A", plain, None),), stages=(Stage("cat", "1"),))
    )
    window.apply_command(parsed)
    assert window.pipeline.inputs[0].password is None


def test_run_pipeline_with_no_inputs_is_a_noop(make_window):
    window = make_window([])
    assert window.runner is None
    window.run_pipeline()
    assert window.runner is None


def test_run_pipeline_with_no_stages_is_a_noop(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.delete_box(window.boxes[0])
    window.run_pipeline()
    assert window.runner is None
    assert window.inputs_box.strip.count() == 2


def test_removing_every_input_blanks_the_stages(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.add_stage()
    last = window.boxes[1]
    qtbot.waitUntil(lambda: last.strip.count() == 2, timeout=30000)
    window.input_list.setCurrentRow(0)
    window.remove_current_input()
    window.run_pipeline()
    for box in window.boxes:
        assert box.strip.count() == 0
        assert box.status.text() == "No input files"


def test_stage_problems_shown_as_warning_before_run(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.add_stage()
    window.boxes[1].extra.setText("Z")
    window.run_pipeline()
    assert window.boxes[1].status.text() == "⚠ Undefined input handle(s): Z"


def test_stage_error_shows_cross_and_marks_later_stages_not_run(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.add_stage()
    engine = window.engine
    pipeline = window.current_pipeline()
    runner = Runner(engine, pipeline)
    _use_runner(window, runner)
    runner.stage_done.connect(window._stage_done)
    runner.stage_done.emit(StageResult(index=0, kind=ResultKind.ERROR, key="k", error="boom"))
    assert "✗ boom" in window.boxes[0].status.text()
    assert window.boxes[1].status.text() == "not run"
    assert window.boxes[0].failed and not window.boxes[1].failed
    bar = window.statusBar()
    assert bar.currentMessage() == "Stage 1 (cat) failed: boom; see console"
    assert bar.styleSheet() == window_run.WARN_STYLE
    assert not window.output_box.stale.isVisibleTo(window.output_box)


def test_a_stage_failure_stays_in_the_status_bar_until_the_next_run(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.stop_runner()
    window._show_failure(0, "Stage 1 (cat)", "boom")
    assert window.statusBar().currentMessage() == "Stage 1 (cat) failed: boom; see console"
    window.run_pipeline()
    assert window.statusBar().currentMessage() == ""
    window.stop_runner()


def test_the_next_run_keeps_a_later_unrelated_message(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.stop_runner()
    window._show_failure(0, "Stage 1 (cat)", "boom")
    window.statusBar().showMessage("Saved x.pdf")
    window.run_pipeline()
    assert window.statusBar().currentMessage() == "Saved x.pdf"
    window.stop_runner()


def test_stage_done_prompts_for_a_later_input_password_and_reruns(
    make_window, two_pages, tmp_path
):
    """A password-bearing extra input declared by a later stage, resolved via `_stage_done`."""
    window = make_window([str(two_pages)])
    window.add_stage()
    enc = _make_pdf(tmp_path / "enc.pdf", [10], password="secret")
    _with_second_input(window, enc)
    prompts = []
    window.ask_password = lambda *args: prompts.append(args[2]) or "secret"
    runner = Runner(window.engine, window.current_pipeline())
    _use_runner(window, runner)
    runner.stage_done.connect(window._stage_done)
    runner.stage_done.emit(
        StageResult(
            index=0,
            kind=ResultKind.ERROR,
            key="k",
            error="enc.pdf needs a password",
            needs_password=True,
            password_path=enc,
        )
    )
    assert prompts
    assert window.pipeline.inputs[1].password == "secret"
    assert "✗" not in window.boxes[0].status.text()


def test_stage_done_shows_error_when_a_later_input_password_is_declined(
    make_window, two_pages, tmp_path
):
    window = make_window([str(two_pages)])
    window.add_stage()
    enc = _make_pdf(tmp_path / "enc.pdf", [10], password="secret")
    _with_second_input(window, enc)
    window.ask_password = lambda *_a: ""
    runner = Runner(window.engine, window.current_pipeline())
    _use_runner(window, runner)
    runner.stage_done.connect(window._stage_done)
    runner.stage_done.emit(
        StageResult(
            index=0,
            kind=ResultKind.ERROR,
            key="k",
            error="enc.pdf needs a password",
            needs_password=True,
            password_path=enc,
        )
    )
    assert "✗ enc.pdf needs a password" in window.boxes[0].status.text()


def test_stage_done_ignores_result_from_a_stale_runner(make_window, two_pages):
    window = make_window([str(two_pages)])
    pipeline = window.current_pipeline()
    stale = Runner(window.engine, pipeline)
    current = Runner(window.engine, pipeline)
    _use_runner(window, current)
    stale.stage_done.connect(window._stage_done)
    before = window.boxes[0].status.text()
    stale.stage_done.emit(StageResult(index=0, kind=ResultKind.PDF, key="k", page_count=3))
    assert window.boxes[0].status.text() == before


def test_stage_done_ignores_out_of_range_index(make_window, two_pages):
    window = make_window([str(two_pages)])
    pipeline = window.current_pipeline()
    runner = Runner(window.engine, pipeline)
    _use_runner(window, runner)
    runner.stage_done.connect(window._stage_done)
    before = window.boxes[0].status.text()
    runner.stage_done.emit(StageResult(index=5, kind=ResultKind.PDF, key="k", page_count=1))
    assert window.boxes[0].status.text() == before


def test_stage_done_text_result_notes_console_passthrough(make_window, two_pages):
    window = make_window([str(two_pages)])
    pipeline = window.current_pipeline()
    runner = Runner(window.engine, pipeline)
    _use_runner(window, runner)
    runner.stage_done.connect(window._stage_done)
    runner.stage_done.emit(
        StageResult(
            index=0, kind=ResultKind.TEXT, key="k", pdf_path=None, page_count=2, text="hello"
        )
    )
    assert "text output in console" in window.boxes[0].status.text()


@pytest.mark.parametrize(
    ("page_count", "expected"), [(0, "0 pages"), (1, "1 page"), (2, "2 pages")]
)
def test_stage_done_note_pluralises_page_count(make_window, two_pages, page_count, expected):
    window = make_window([str(two_pages)])
    pipeline = window.current_pipeline()
    runner = Runner(window.engine, pipeline)
    _use_runner(window, runner)
    runner.stage_done.connect(window._stage_done)
    runner.stage_done.emit(
        StageResult(index=0, kind=ResultKind.PDF, key="k", page_count=page_count)
    )
    assert window.boxes[0].status.text() == expected


def test_stage_done_replays_cached_output(make_window, two_pages):
    window = make_window([str(two_pages)])
    pipeline = window.current_pipeline()
    runner = Runner(window.engine, pipeline)
    _use_runner(window, runner)
    runner.stage_done.connect(window._stage_done)
    runner.stage_done.emit(
        StageResult(
            index=0, kind=ResultKind.TEXT, key="k", page_count=2, text="report", cached=True
        )
    )
    assert "--- Stage 1 (cat) ---\nreport" in window.console.toPlainText()
    assert "(cached)" in window.boxes[0].status.text()


def test_rerun_of_cached_pipeline_prints_the_same_transcript(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.apply_command(
        parse_command(f"{shlex.quote(str(two_pages))} usage --- delete 1 --- usage")
    )
    last = window.boxes[2]
    qtbot.waitUntil(lambda: "page" in last.status.text(), timeout=30000)
    window.run_pipeline()
    qtbot.waitUntil(lambda: "(cached)" in last.status.text(), timeout=30000)
    runs = window.console.toPlainText().split("$ ")[1:]
    assert len(runs) == 2
    assert runs[0] == runs[1]
    assert "--- Stage 1 (usage) ---" in runs[0]
    assert "--- Stage 3 (usage) ---" in runs[0]


def test_log_result_appends_stdout_stderr_and_error(make_window, two_pages):
    window = make_window([str(two_pages)])
    window._log_result(
        StageResult(
            index=0,
            kind=ResultKind.ERROR,
            key="k",
            text="out text",
            stderr="err text",
            error="boom",
        ),
        "Stage 1 (cat)",
    )
    text = window.console.toPlainText()
    assert "out text" in text
    assert "err text" in text
    assert "boom" in text


def test_apply_command_with_no_inputs(make_window):
    window = make_window([])
    parsed = ParsedCommand(pipeline=Pipeline(inputs=(), stages=(Stage("cat", "1"),)))
    window.apply_command(parsed)
    assert window.pipeline.inputs == ()
    assert window.input_list.count() == 0


def test_schedule_shows_not_runnable_when_args_do_not_parse(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    assert not window.cli.hasFocus()
    fire(qtbot, "focus_args")
    type_text(qtbot, '"unterminated')
    assert window.cli.text().startswith("(not runnable:")


def test_runner_run_emits_a_result_per_stage(qtbot, tmp_path, two_pages):
    engine = SubprocessEngine(cache_dir=tmp_path / "cache")
    pipeline = Pipeline(inputs=(InputFile("A", two_pages),), stages=(Stage("cat", "1"),))
    runner = Runner(engine, pipeline)
    seen = []
    runner.stage_done.connect(seen.append)
    runner.run()
    assert len(seen) == 1
    assert seen[0].kind is ResultKind.PDF
    engine.close()


def test_runner_stop_cancels_a_running_thread(qtbot):
    runner = Runner(_SlowEngine(), Pipeline())
    runner.start()
    qtbot.waitUntil(runner.isRunning, timeout=2000)
    runner.stop()
    assert not runner.isRunning()


def test_runner_stop_without_starting_is_a_noop():
    runner = Runner(SubprocessEngine.__new__(SubprocessEngine), Pipeline())
    runner.stop()
    assert not runner.isRunning()


def test_saver_run_emits_the_engine_result(qtbot, tmp_path):
    result = StageResult(index=0, kind=ResultKind.PDF, key="save", pdf_path=tmp_path / "o.pdf")

    class FakeEngine:
        def save(self, pipeline, path, cancel):
            return result

    saver = window_run.Saver(FakeEngine(), Pipeline(), tmp_path / "o.pdf")
    got = []
    saver.saved.connect(got.append)
    saver.run()
    assert got == [result]


def test_editing_the_command_bar_hints_how_to_apply(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    fire(qtbot, "focus_command")
    type_text(qtbot, "x")
    assert "Press Enter to apply" in window.statusBar().currentMessage()


def test_failed_command_is_shown_in_red_until_the_next_message(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window._command_failed("stage 3 has no operation")
    bar = window.statusBar()
    assert "Command not applied" in bar.currentMessage()
    assert bar.styleSheet() == window_run.WARN_STYLE
    block = window.console.document().lastBlock().previous()
    fmt = block.begin().fragment().charFormat()
    assert block.text() == "Command not applied: stage 3 has no operation"
    assert fmt.fontWeight() >= 700
    assert fmt.foreground().color().red() > fmt.foreground().color().green() + 60
    bar.showMessage("Command applied", 3000)
    assert bar.styleSheet() == ""


def test_failed_save_is_a_red_warning(make_window, two_pages):
    window = make_window([str(two_pages)])
    window._save_done(StageResult(index=0, kind=ResultKind.ERROR, key="k", error="boom"))
    assert window.statusBar().currentMessage() == "Save failed; see console"
    assert window.statusBar().styleSheet() == window_run.WARN_STYLE


def test_ctrl_n_while_editing_the_bar_discards_the_edit_visibly(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    fire(qtbot, "focus_command")
    replace_selection(qtbot, f"{two_pages} rotate 1east")
    fire(qtbot, "add_stage")
    assert len(window.boxes) == 2
    assert window.cli.text() == window._cli_text(window.current_pipeline())
    assert "discarded" in window.statusBar().currentMessage()
    fire(qtbot, "focus_command")
    press(qtbot, "Up")
    assert window.cli.text() == f"{two_pages} rotate 1east"


def test_applied_command_is_what_escape_reverts_to(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    fire(qtbot, "focus_command")
    replace_selection(qtbot, f"pdftl A={shlex.quote(str(two_pages))} rotate 1east")
    press(qtbot, "Return")
    assert window.cli.hasFocus()
    assert not window.cli.is_dirty()
    press(qtbot, "Escape")
    press(qtbot, "Return")
    assert [(s.op, s.args_text) for s in window.current_pipeline().stages] == [("rotate", "1east")]


def test_create_runs_as_the_first_stage_with_no_inputs(qtbot, make_window):
    window = make_window([])
    window.apply_command(ParsedCommand(pipeline=Pipeline(stages=(Stage("create", "3"),))))
    assert window.runner is not None
    qtbot.waitUntil(lambda: "3 page" in window.boxes[0].status.text(), timeout=15000)


def test_running_stage_spins_and_the_status_bar_shows_progress(qtbot, make_window):
    engine = _GatedEngine()
    window = make_window([], engine=engine)
    window.boxes[0].op.setCurrentText("create")
    window.add_stage()
    first, second = window.boxes
    window.run_pipeline()
    assert window.busy.isVisible()
    assert window.busy_label.text() == "Running stage 1 of 2"
    assert second.status.text() == "waiting..."
    frames = set()
    qtbot.waitUntil(lambda: frames.add(first.status.text()) or len(frames) > 1, timeout=3000)
    assert all(t[0] in window_run.SPINNER and t.endswith("running...") for t in frames)
    engine.first.set()
    qtbot.waitUntil(lambda: window.busy_label.text() == "Running stage 2 of 2")
    assert second.status.text().endswith("running...")
    engine.rest.set()
    qtbot.waitUntil(lambda: not window.busy.isVisible())
    assert window.busy_label.text() == ""
    assert not window.spinner.isActive()


def test_a_failed_stage_stops_the_spinner(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.boxes[0].op.setCurrentText("no_such_op")
    window.run_pipeline()
    qtbot.waitUntil(lambda: "✗" in window.boxes[0].status.text(), timeout=15000)
    assert not window.spinner.isActive()
    assert not window.busy.isVisible()


def test_spinner_tick_after_the_running_stage_is_deleted(make_window):
    window = make_window([], engine=_SlowEngine())
    window.boxes[0].op.setCurrentText("create")
    window.add_stage()
    window.run_pipeline()
    window._running = 1
    window.delete_stage()
    before = window.boxes[0].status.text()
    window._spin()
    assert window.boxes[0].status.text() == before
    window.stop_runner()


def test_applied_prompt_output_password_keeps_the_known_one(make_window):
    window = make_window([])
    window.output_box.set_options_text("owner_pw s3cret")
    window._show_command()
    shown = window.cli.text()
    assert "s3cret" not in shown and "owner_pw PROMPT" in shown
    window.apply_command(parse_command(shown))
    assert window.current_pipeline().output_options == "owner_pw s3cret"


def test_a_closed_window_starts_no_more_runs(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.schedule()
    assert window.debounce.isActive()
    window.close()
    assert not window.debounce.isActive()
    before = window.runner
    window.run_pipeline()
    assert window.runner is before
    assert before is None or not before.isRunning()


def test_a_followed_stage_that_fails_says_the_viewer_is_stale(qtbot, make_window, tmp_path):
    src = _make_pdf(tmp_path / "three.pdf", [100, 200, 300])
    window = make_window([str(src)])
    window.add_stage()
    first, last = window.boxes
    _run_args(qtbot, window, last, "")
    window.open_url = lambda p: True
    window.toggle_follow(first)
    window.toggle_output_follow()
    live = window.followed[last][0]
    assert _pages(live) == [(100, 0), (200, 0), (300, 0)]

    first.args.setText("9")
    window.run_pipeline()
    qtbot.waitUntil(lambda: first.failed, timeout=15000)
    assert window_run.STALE_NOTE in first.status.text()
    assert last.status.text() == f"not run; {window_run.STALE_NOTE}"
    message = (
        "Stage 1 (cat) failed: Invalid page. Page spec '9' includes page 9"
        f" but there are only 3 pages in {src}; see console"
    )
    assert window.statusBar().currentMessage() == message
    assert window.output_box.stale.text() == f"Stage 1 (cat) failed, so {window_run.STALE_NOTE}"
    assert window.output_box.stale.isVisibleTo(window.output_box)
    assert _pages(live) == [(100, 0), (200, 0), (300, 0)]

    first.args.setText("2")
    window.run_pipeline()
    qtbot.waitUntil(lambda: "page" in last.status.text(), timeout=15000)
    assert not first.failed
    assert not window.output_box.stale.isVisibleTo(window.output_box)
    assert _pages(live) == [(200, 0)]

    first.args.setText("9")
    window.run_pipeline()
    qtbot.waitUntil(lambda: first.failed, timeout=15000)
    window.toggle_output_follow()
    assert not window.output_box.stale.isVisibleTo(window.output_box)


def test_saver_stop_without_starting_is_a_noop(tmp_path):
    saver = window_run.Saver(SubprocessEngine.__new__(SubprocessEngine), Pipeline(), tmp_path)
    saver.stop()
    assert saver.cancel.is_set()
    assert not saver.isRunning()


def test_current_pipeline_leaves_out_output_options_that_do_not_parse(make_window):
    window = make_window([])
    window.output_box.set_options_text("banana")
    assert window.current_pipeline().output_options == ""
    window.output_box.set_options_text("compress")
    assert window.current_pipeline().output_options == "compress"


def test_applying_a_command_with_an_output_sets_the_save_target(make_window, two_pages, tmp_path):
    window = make_window([str(two_pages)])
    target = tmp_path / "out.pdf"
    pipeline = Pipeline(inputs=window.pipeline.inputs, stages=(Stage("cat"),))
    window.apply_command(ParsedCommand(pipeline=pipeline, output=target))
    assert window.save_target == target
    assert f"output target set to {target}" in window.statusBar().currentMessage()
