import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pikepdf
import pytest
import yaml

from pdftl.gui import engine as engine_module
from pdftl.gui.engine import Completed, SubprocessEngine
from pdftl.gui.interfaces import InputFile, OpKind, Pipeline, ResultKind, Stage


def _pages(path):
    with pikepdf.open(path) as pdf:
        return [(int(p.mediabox[2]), int(p.obj.get("/Rotate", 0))) for p in pdf.pages]


def _make(path, widths, password=None):
    pdf = pikepdf.new()
    for w in widths:
        pdf.add_blank_page(page_size=(w, 100))
    enc = pikepdf.Encryption(owner=password, user=password) if password else False
    pdf.save(path, encryption=enc)
    return path


def _script(path, body):
    """A Python script that stands in for the interpreter, as a portable command."""
    path.write_text(f"import os, subprocess, sys, time\n{body}\n", encoding="utf-8")
    return [sys.executable, str(path)]


def _logging(log):
    """Script body: log the argv, then run the real interpreter on it."""
    return (
        f"with open({str(log)!r}, 'a', encoding='utf-8') as f:\n"
        "    f.write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"sys.exit(subprocess.call([{sys.executable!r}, *sys.argv[1:]]))"
    )


@pytest.fixture
def files(tmp_path):
    return (
        _make(tmp_path / "a.pdf", [101, 102, 103, 104]),
        _make(tmp_path / "b.pdf", [201, 202], password="pw"),
    )


@pytest.fixture
def logged(tmp_path):
    """Engine whose interpreter logs one line per real process launch."""
    log = tmp_path / "launches.log"
    python = _script(tmp_path / "py", _logging(log))
    engine = SubprocessEngine(cache_dir=tmp_path / "cache", python=python, cwd=tmp_path)
    yield engine, lambda: len(log.read_text().splitlines()) if log.exists() else 0
    engine.close()


def _password_capturing(tmp_path):
    """Like `logged`, but also captures a `--args` file's content before pdftl deletes it.

    `"-m pdftl --args <file>"` puts `--args` after the interpreter's own `-m pdftl`,
    so this scans all the arguments for it rather than assuming a fixed position.
    """
    log = tmp_path / "launches.log"
    content_log = tmp_path / "content.log"
    body = (
        "args = sys.argv[1:]\n"
        "for prev, arg in zip(args, args[1:]):\n"
        "    if prev == '--args':\n"
        f"        with open(arg, 'rb') as src, open({str(content_log)!r}, 'ab') as dst:\n"
        "            dst.write(src.read())\n" + _logging(log)
    )
    python = _script(tmp_path / "py", body)
    return python, log, content_log


def _run(engine, pipeline):
    return list(engine.run(pipeline, threading.Event()))


def _pipeline(files, *stages):
    a, b = files
    return Pipeline((InputFile("A", a), InputFile("B", b, "pw")), stages)


def test_stagewise_matches_one_shot_cli(logged, files, run_pdftl, tmp_path):
    engine, _ = logged
    p = _pipeline(
        files,
        Stage("cat", "A1-3 B2"),
        Stage("dump_text"),
        Stage("rotate", "2east"),
        Stage("cat", "_1-end B1", inputs=("B",)),
    )
    results = _run(engine, p)
    one_shot = tmp_path / "one.pdf"
    run_pdftl(p.to_argv(one_shot))
    expected = _pages(one_shot)
    assert expected == [(101, 0), (102, 90), (103, 0), (202, 0), (201, 0)]
    assert [r.kind for r in results] == [ResultKind.PDF, ResultKind.TEXT, *[ResultKind.PDF] * 2]
    assert _pages(results[-1].pdf_path) == expected
    assert results[-1].page_count == 5
    assert results[1].pdf_path == results[0].pdf_path
    assert results[1].page_count == 4


def test_cache_reuses_unchanged_prefix(logged, files):
    engine, launches = logged
    first = _run(engine, _pipeline(files, Stage("cat", "A1-2"), Stage("rotate", "1east")))
    assert launches() == 2
    again = _run(engine, _pipeline(files, Stage("cat", "A1-2"), Stage("rotate", "1east")))
    assert launches() == 2
    assert [r.cached for r in again] == [True, True]
    assert [r.pdf_path for r in again] == [r.pdf_path for r in first]
    edited = _run(engine, _pipeline(files, Stage("cat", "A1-2"), Stage("rotate", "2east")))
    assert launches() == 3
    assert [r.cached for r in edited] == [True, False]
    assert _pages(edited[1].pdf_path) == [(101, 0), (102, 90)]


def test_touching_an_input_invalidates(logged, files):
    engine, launches = logged
    p = _pipeline(files, Stage("cat", "A1"))
    _run(engine, p)
    _make(files[0], [150, 151])
    (result,) = _run(engine, p)
    assert launches() == 2
    assert _pages(result.pdf_path) == [(150, 0)]


def test_missing_cached_pdf_reruns(logged, files):
    engine, launches = logged
    p = _pipeline(files, Stage("cat", "A1"))
    (first,) = _run(engine, p)
    first.pdf_path.unlink()
    (again,) = _run(engine, p)
    assert launches() == 2
    assert not again.cached and again.pdf_path.exists()


def test_first_stage_data_op_passes_the_first_input_through(logged, files):
    engine, _ = logged
    (r,) = _run(engine, Pipeline((InputFile("A", files[0]),), (Stage("dump_data"),)))
    assert r.kind is ResultKind.TEXT
    assert r.pdf_path == files[0] and r.page_count == 4
    assert "NumberOfPages: 4" in r.text


def test_side_effect_op_writes_into_scratch_dir(logged, files):
    engine, _ = logged
    a_only = Pipeline((InputFile("A", files[0]),), (Stage("burst"),))
    (r,) = _run(engine, a_only)
    assert r.kind is ResultKind.TEXT and r.page_count == 4
    scratch = engine.cache_dir / f"{r.key}.d"
    assert sorted(p.name for p in scratch.glob("*.pdf")) == [
        f"pg_{i:04d}.pdf" for i in range(1, 5)
    ]
    assert f"wrote 5 file(s) in {scratch}" in r.stderr


def test_error_stops_the_run(logged, files):
    engine, _ = logged
    results = _run(engine, _pipeline(files, Stage("rotate", "0east"), Stage("cat")))
    assert [r.kind for r in results] == [ResultKind.ERROR]
    assert results[0].error == (
        "The 'rotate' operation requires one input, but received 2 effective input(s)."
    )
    assert results[0].stderr


def test_a_later_stage_error_names_the_previous_output_not_its_cache_path(logged, files):
    engine, _ = logged
    results = _run(engine, _pipeline(files, Stage("cat", "1-2"), Stage("rotate", "9east")))
    assert results[1].kind is ResultKind.ERROR
    assert str(engine.cache_dir) not in results[1].error
    assert results[1].error == (
        "Invalid page. Page spec '9east' includes page 9"
        " but there are only 2 pages in the previous stage's output"
    )


def _reason(engine, stderr, returncode=2):
    return engine.failure_reason(Completed(returncode, "", stderr))


def test_failure_reason_without_an_error_line_gives_the_status(logged):
    engine, _ = logged
    assert _reason(engine, "Traceback\n  boom\n") == "pdftl exited with status 2"


def test_failure_reason_takes_the_last_error_and_its_indented_lines(logged):
    engine, _ = logged
    stderr = (
        "[pdftl] Error: first\nnoise\n[pdftl.exe] Error: second\n  more\n\tand more\nunrelated\n"
    )
    assert _reason(engine, stderr) == "second more and more"


@pytest.mark.parametrize(
    ("stage", "message"),
    [
        (Stage("no_such_op"), "Unknown operation 'no_such_op'"),
        (Stage("server"), "server cannot run as a GUI stage"),
        (Stage("cat", "'open"), "Cannot parse arguments: No closing quotation"),
        (Stage("cat", inputs=("Q",)), "stage 2 uses undefined handles ['Q']"),
    ],
)
def test_static_errors(logged, files, stage, message):
    engine, launches = logged
    results = _run(engine, _pipeline(files, Stage("cat", "A1"), stage))
    assert results[-1].kind is ResultKind.ERROR
    assert results[-1].error == message
    assert launches() == 1


def test_missing_input_file(logged, tmp_path):
    engine, _ = logged
    p = Pipeline((InputFile("A", tmp_path / "gone.pdf"),), (Stage("cat"),))
    (r,) = _run(engine, p)
    assert r.kind is ResultKind.ERROR
    assert not r.needs_password


def test_exit_zero_without_output_is_an_error(tmp_path, files):
    engine = SubprocessEngine(
        cache_dir=tmp_path / "c", python=_script(tmp_path / "py", "sys.exit(0)")
    )
    (r,) = _run(engine, _pipeline(files, Stage("cat")))
    assert r.error == "The stage produced no PDF"


def test_cancel_kills_a_running_stage(tmp_path, files):
    engine = SubprocessEngine(
        cache_dir=tmp_path / "c", python=_script(tmp_path / "py", "time.sleep(30)")
    )
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    start = time.monotonic()
    assert list(engine.run(_pipeline(files, Stage("cat")), cancel)) == []
    assert time.monotonic() - start < 5


def test_cancel_before_start(logged, files):
    engine, launches = logged
    cancel = threading.Event()
    cancel.set()
    assert list(engine.run(_pipeline(files, Stage("cat")), cancel)) == []
    assert launches() == 0


def test_preview_input(logged, files, tmp_path):
    engine, _ = logged
    ok = engine.preview_input(InputFile("B", files[1], "pw"))
    assert (ok.kind, ok.index, ok.page_count, ok.pdf_path) == (ResultKind.PDF, -1, 2, files[1])
    assert ok.password_kind == "owner"
    bad = engine.preview_input(InputFile("B", files[1]))
    assert bad.kind is ResultKind.ERROR and bad.error and bad.needs_password
    missing = engine.preview_input(InputFile("C", tmp_path / "password-missing.pdf"))
    assert missing.kind is ResultKind.ERROR and not missing.needs_password


def test_preview_input_reports_owner_and_user_password_kind(tmp_path):
    """An independent ground truth: owner and user are distinct passwords on one file."""
    engine = SubprocessEngine(cache_dir=tmp_path / "c")
    path = tmp_path / "enc.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(10, 10))
    pdf.save(path, encryption=pikepdf.Encryption(owner="ownerpw", user="userpw"))
    plain = tmp_path / "plain.pdf"
    pdf.save(plain)
    owner = engine.preview_input(InputFile("A", path, "ownerpw"))
    user = engine.preview_input(InputFile("A", path, "userpw"))
    none_ = engine.preview_input(InputFile("A", plain))
    assert (owner.password_kind, user.password_kind, none_.password_kind) == (
        "owner",
        "user",
        None,
    )
    engine.close()


def test_password_kind_prioritizes_owner_when_both_match(tmp_path):
    path = tmp_path / "enc.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(10, 10))
    pdf.save(path, encryption=pikepdf.Encryption(owner="samepw", user="samepw"))
    with pikepdf.open(path, password="samepw") as opened:
        assert engine_module.password_kind(opened) == "owner"


def test_save_runs_the_one_shot_command(logged, files, tmp_path):
    engine, launches = logged
    p = _pipeline(files, Stage("cat", "B1-2"), Stage("rotate", "1south"))
    out = tmp_path / "saved.pdf"
    r = engine.save(p, out, threading.Event())
    assert (r.kind, r.pdf_path, launches()) == (ResultKind.PDF, out, 1)
    assert _pages(out) == [(201, 180), (202, 0)]
    a_only = Pipeline((InputFile("A", files[0]),), (Stage("dump_data"),))
    text = engine.save(a_only, tmp_path / "d.txt", threading.Event())
    assert text.kind is ResultKind.PDF
    assert "NumberOfPages" in (tmp_path / "d.txt").read_text()


def test_save_failure_and_cancel(tmp_path, files):
    failing = SubprocessEngine(
        cache_dir=tmp_path / "f", python=_script(tmp_path / "f.py", "sys.exit(3)")
    )
    r = failing.save(_pipeline(files, Stage("cat")), tmp_path / "o.pdf", threading.Event())
    assert (r.kind, r.error) == (ResultKind.ERROR, "pdftl exited with status 3")
    quiet = SubprocessEngine(
        cache_dir=tmp_path / "q", python=_script(tmp_path / "q.py", "sys.exit(0)")
    )
    r = quiet.save(_pipeline(files, Stage("cat")), tmp_path / "none.pdf", threading.Event())
    assert r.kind is ResultKind.TEXT
    slow = SubprocessEngine(
        cache_dir=tmp_path / "s", python=_script(tmp_path / "s.py", "time.sleep(30)")
    )
    cancel = threading.Event()
    cancel.set()
    r = slow.save(_pipeline(files, Stage("cat")), tmp_path / "o.pdf", cancel)
    assert (r.kind, r.error) == (ResultKind.ERROR, "Cancelled")


def test_close_removes_only_its_own_dir(tmp_path):
    own = SubprocessEngine()
    own.close()
    assert not own.cache_dir.exists()
    given = SubprocessEngine(cache_dir=tmp_path / "keep")
    given.close()
    assert given.cache_dir.exists()


def test_injected_kinds(tmp_path, files):
    engine = SubprocessEngine(cache_dir=tmp_path / "c", kind_of=lambda op: OpKind.BLOCKED)
    (r,) = _run(engine, _pipeline(files, Stage("cat")))
    assert r.error == "cat cannot run as a GUI stage"


def test_cancel_without_process_groups(tmp_path, files, monkeypatch):
    import pdftl.gui.engine as engine_module

    monkeypatch.setattr(engine_module, "_POSIX", False)
    python = _script(tmp_path / "py", "time.sleep(30)")
    engine = SubprocessEngine(cache_dir=tmp_path / "c", python=python)
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    start = time.monotonic()
    assert list(engine.run(_pipeline(files, Stage("cat")), cancel)) == []
    assert time.monotonic() - start < 5


def test_data_stage_without_inputs(tmp_path):
    python = _script(tmp_path / "py", "print('hello')")
    engine = SubprocessEngine(
        cache_dir=tmp_path / "c", kind_of=lambda op: OpKind.DATA, python=python
    )
    (r,) = _run(engine, Pipeline((), (Stage("usage"),)))
    assert (r.kind, r.pdf_path, r.page_count, r.text) == (ResultKind.TEXT, None, 0, "hello\n")


def test_previews_force_colour_but_save_does_not(tmp_path, files, monkeypatch):
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("COLUMNS", raising=False)
    python = _script(
        tmp_path / "py",
        "e = os.environ.get\nprint(f\"colour={e('FORCE_COLOR', '')} columns={e('COLUMNS', '')}\")",
    )
    engine = SubprocessEngine(
        cache_dir=tmp_path / "c", kind_of=lambda op: OpKind.DATA, python=python
    )
    (r,) = _run(engine, _pipeline(files, Stage("dump_data")))
    assert r.text == "colour=1 columns=100\n"
    saved = engine.save(
        _pipeline(files, Stage("dump_data")), tmp_path / "o.txt", threading.Event()
    )
    assert saved.text == "colour= columns=\n"


def _stamp_pdf(path, width):
    return _make(path, [width])


def test_relative_file_argument_resolves_and_invalidates_on_edit(logged, files, tmp_path):
    engine, launches = logged
    _stamp_pdf(tmp_path / "wm.pdf", 50)
    p = Pipeline((InputFile("A", files[0]),), (Stage("stamp", "wm.pdf"),))
    (first,) = _run(engine, p)
    assert first.kind is ResultKind.PDF, first.stderr
    (again,) = _run(engine, p)
    assert again.cached and launches() == 1
    _stamp_pdf(tmp_path / "wm.pdf", 60)
    (edited,) = _run(engine, p)
    assert not edited.cached and launches() == 2


def test_file_args_include_key_value_paths(tmp_path):
    engine = SubprocessEngine(cache_dir=tmp_path / "c", cwd=tmp_path)
    (tmp_path / "data.json").write_text("{}")
    stage = Stage("cat", "1-2 data.json font=data.json missing.pdf =")
    assert engine._file_args(stage) == [tmp_path / "data.json", tmp_path / "data.json"]


def test_cancel_is_bounded_when_a_grandchild_holds_the_pipes(tmp_path, files, monkeypatch):
    import pdftl.gui.engine as engine_module

    monkeypatch.setattr(engine_module, "_POSIX", False)
    monkeypatch.setattr(engine_module, "KILL_GRACE_SECONDS", 0.3)
    python = _script(
        tmp_path / "py",
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']).wait()",
    )
    engine = SubprocessEngine(cache_dir=tmp_path / "c", python=python)
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    start = time.monotonic()
    assert list(engine.run(_pipeline(files, Stage("cat")), cancel)) == []
    assert time.monotonic() - start < 5


def test_cache_keeps_only_the_newest_entries(logged, files, monkeypatch):
    import pdftl.gui.engine as engine_module

    engine, _ = logged
    monkeypatch.setattr(engine_module, "MAX_CACHE_ENTRIES", 2)
    results = [_run(engine, _pipeline(files, Stage("cat", f"A{i}")))[0] for i in (1, 2, 3)]
    kept = sorted(p.stem for p in engine.cache_dir.glob("*.json"))
    assert kept == sorted(r.key for r in results[1:])
    assert not results[0].pdf_path.exists()
    assert not (engine.cache_dir / f"{results[0].key}.d").exists()


# --- A: UTF-8 everywhere, independent of the host locale ------------------


def test_non_ascii_stage_output_round_trips_as_utf8(tmp_path):
    """A real `python -m pdftl` child; the docinfo string is the ground truth."""
    title = "café ━ résumé"
    pdf_path = tmp_path / "meta.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(100, 100))
    pdf.docinfo["/Title"] = title
    pdf.save(pdf_path)
    engine = SubprocessEngine(cache_dir=tmp_path / "cache", cwd=tmp_path)
    (r,) = _run(engine, Pipeline((InputFile("A", pdf_path),), (Stage("dump_data_utf8"),)))
    assert r.kind is ResultKind.TEXT, r.stderr
    assert title in r.text
    engine.close()


def test_engine_env_forces_utf8_regardless_of_locale(tmp_path, monkeypatch):
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LANG", "C")
    engine = SubprocessEngine(cache_dir=tmp_path / "c")
    assert engine.env["PYTHONUTF8"] == "1"
    assert engine.env["PYTHONIOENCODING"] == "utf-8"
    assert engine.preview_env["PYTHONUTF8"] == "1"
    assert engine.preview_env["PYTHONIOENCODING"] == "utf-8"
    engine.close()


def test_exec_decodes_invalid_utf8_bytes_with_replacement(tmp_path):
    python = _script(
        tmp_path / "py",
        "sys.stdout.buffer.write(b'caf\\xc3\\xa9 \\xe2\\x94\\x81 broken:\\xffend\\n')",
    )
    engine = SubprocessEngine(
        cache_dir=tmp_path / "c", kind_of=lambda op: OpKind.DATA, python=python
    )
    (r,) = _run(engine, Pipeline((), (Stage("usage"),)))
    assert r.kind is ResultKind.TEXT
    assert "café ━ broken:" in r.text
    assert "end" in r.text
    assert "�" in r.text


def test_cache_json_round_trips_utf8(tmp_path):
    """Both `_store`'s write and `_cached`'s read go through the real cache file."""
    engine = SubprocessEngine(cache_dir=tmp_path / "c")
    engine._store(
        SimpleNamespace(
            key="k",
            kind=ResultKind.TEXT,
            pdf_path=None,
            page_count=0,
            text="café ━",
            stderr="",
        )
    )
    cached = engine._cached(0, "k")
    assert cached.text == "café ━"
    engine.close()


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
def test_kill_tolerates_a_stage_that_has_already_exited():
    import pdftl.gui.engine as engine_module

    proc = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    proc.wait()
    with pytest.raises(ProcessLookupError):
        os.killpg(proc.pid, 0)
    engine_module._kill(proc)


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
def test_kill_tolerates_eperm_from_a_group_of_zombies(monkeypatch):
    import errno

    import pdftl.gui.engine as engine_module

    monkeypatch.setattr(engine_module, "_POSIX", True)

    def zombie_group(pid, sig):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "killpg", zombie_group, raising=False)
    engine_module._kill(SimpleNamespace(pid=4321))


# --- C: killing the whole process tree on Windows --------------------------


def test_kill_uses_taskkill_on_windows(monkeypatch):
    import pdftl.gui.engine as engine_module

    monkeypatch.setattr(engine_module, "_POSIX", False)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    fake_proc = SimpleNamespace(pid=4321, kill=lambda: calls.append("fallback"))
    engine_module._kill(fake_proc)
    assert calls == [
        (
            ["taskkill", "/F", "/T", "/PID", "4321"],
            {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
            },
        )
    ]


def test_kill_falls_back_to_proc_kill_when_taskkill_is_unavailable(monkeypatch):
    import pdftl.gui.engine as engine_module

    monkeypatch.setattr(engine_module, "_POSIX", False)

    def fake_run(argv, **kwargs):
        raise FileNotFoundError("no taskkill")

    monkeypatch.setattr(subprocess, "run", fake_run)
    killed = []
    fake_proc = SimpleNamespace(pid=1, kill=lambda: killed.append(True))
    engine_module._kill(fake_proc)
    assert killed == [True]


# --- B: cache pruning tolerates a file another process still holds open ----


def test_prune_skips_an_entry_whose_unlink_fails(tmp_path, monkeypatch):
    import pdftl.gui.engine as engine_module

    engine = SubprocessEngine(cache_dir=tmp_path / "c")
    monkeypatch.setattr(engine_module, "MAX_CACHE_ENTRIES", 0)
    for i, name in enumerate(("old1", "old2", "locked")):
        p = engine.cache_dir / f"{name}.json"
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (i, i))
    locked = engine.cache_dir / "locked.json"
    real_unlink = Path.unlink

    def fake_unlink(self, *a, **kw):
        if self == locked:
            raise PermissionError("busy")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", fake_unlink)
    engine._prune()
    assert not (engine.cache_dir / "old1.json").exists()
    assert not (engine.cache_dir / "old2.json").exists()
    assert locked.exists()
    engine.close()


# --- D: temp-dir lifecycle: lock file, sweep, close -------------------------


class _FakeMsvcrt:
    LK_NBLCK = 1
    LK_UNLCK = 2

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def locking(self, fd, mode, nbytes):
        self.calls.append((fd, mode, nbytes))
        if mode == self.LK_NBLCK and self.fail:
            raise OSError("locked")


def test_lock_acquire_and_release_on_windows(tmp_path, monkeypatch):
    import pdftl.gui.engine as engine_module

    fake = _FakeMsvcrt()
    monkeypatch.setattr(engine_module, "_POSIX", False)
    monkeypatch.setattr(engine_module, "msvcrt", fake)
    path = tmp_path / ".lock"
    fd = engine_module._lock_acquire(path)
    assert fd is not None
    engine_module._lock_release(fd)
    assert fake.calls[0][1] == fake.LK_NBLCK
    assert fake.calls[-1][1] == fake.LK_UNLCK


def test_lock_acquire_on_windows_when_already_locked(tmp_path, monkeypatch):
    import pdftl.gui.engine as engine_module

    fake = _FakeMsvcrt(fail=True)
    monkeypatch.setattr(engine_module, "_POSIX", False)
    monkeypatch.setattr(engine_module, "msvcrt", fake)
    path = tmp_path / ".lock"
    assert engine_module._lock_acquire(path) is None


def _age(path, seconds):
    then = time.time() - seconds
    os.utime(path, (then, then))


def test_sweep_does_not_remove_a_live_locked_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    first = SubprocessEngine()
    _age(first.cache_dir, 120)
    second = SubprocessEngine()
    assert first.cache_dir.exists()
    second.close()
    assert first.cache_dir.exists()
    first.close()


def _released_lock_dir(tmp_path):
    import pdftl.gui.engine as engine_module

    path = Path(tempfile.mkdtemp(prefix="pdftl-gui-", dir=str(tmp_path)))
    fd = engine_module._lock_acquire(path / engine_module.LOCK_FILENAME)
    fd.close()  # the owning process exited: the OS drops the lock
    return path


def test_sweep_removes_a_dir_whose_lock_was_released(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    stale = _released_lock_dir(tmp_path)
    _age(stale, 120)
    engine = SubprocessEngine()
    assert not stale.exists()
    engine.close()


def test_sweep_spares_a_dir_touched_within_the_grace_period(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    starting = _released_lock_dir(tmp_path)
    engine = SubprocessEngine()
    assert starting.exists()
    engine.close()


def test_lock_acquire_returns_none_when_the_file_cannot_be_opened(tmp_path):
    import pdftl.gui.engine as engine_module

    assert engine_module._lock_acquire(tmp_path) is None


def test_sweep_removes_a_lockless_old_dir(tmp_path, monkeypatch):
    import pdftl.gui.engine as engine_module

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    old = Path(tempfile.mkdtemp(prefix="pdftl-gui-", dir=str(tmp_path)))
    old_time = time.time() - engine_module.STALE_SECONDS - 10
    os.utime(old, (old_time, old_time))
    engine = SubprocessEngine()
    assert not old.exists()
    engine.close()


def test_sweep_keeps_a_lockless_new_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    fresh = Path(tempfile.mkdtemp(prefix="pdftl-gui-", dir=str(tmp_path)))
    _age(fresh, 120)  # past the grace period, well short of stale
    engine = SubprocessEngine()
    assert fresh.exists()
    engine.close()


def test_close_releases_the_lock_and_removes_the_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    engine = SubprocessEngine()
    assert engine._lock_fd is not None
    engine.close()
    assert not engine.cache_dir.exists()


def test_close_tolerates_a_never_acquired_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    engine = SubprocessEngine()
    engine._lock_fd = None
    engine.close()
    assert not engine.cache_dir.exists()


def test_is_stale_returns_false_when_stat_fails(tmp_path):
    import pdftl.gui.engine as engine_module

    assert engine_module._is_stale(tmp_path / "does-not-exist") is False


# --- E: passwords stay off disk and off the child's argv --------------------


def test_password_digest_is_stable_per_engine_and_distinct_across_engines(tmp_path):
    engine = SubprocessEngine(cache_dir=tmp_path / "c")
    assert engine._password_digest(None) is None
    d1 = engine._password_digest("secret")
    d2 = engine._password_digest("secret")
    d3 = engine._password_digest("other")
    assert d1 == d2 != d3
    assert d1 != "secret"
    assert len(d1) == 64
    other = SubprocessEngine(cache_dir=tmp_path / "c2")
    assert other._password_digest("secret") != d1
    engine.close()
    other.close()


def test_cache_files_never_contain_the_raw_password(logged, tmp_path):
    engine, _ = logged
    secret_pw = "S3cr3t-XyZ-unique-9f8"
    enc = _make(tmp_path / "enc.pdf", [10], password=secret_pw)
    _run(engine, Pipeline((InputFile("A", enc, secret_pw),), (Stage("cat", "1"),)))
    for meta in engine.cache_dir.glob("*.json"):
        assert secret_pw not in meta.read_text()
    assert not any(secret_pw in p.name for p in engine.cache_dir.rglob("*"))


@pytest.mark.parametrize(
    "password",
    [
        "with space",
        "has#hash",
        'quote\'d "both"',
        "-leading-dash",
        "café-emoji-🔥",
        "yes",
        "no",
        "null",
        "NULL",
        "true",
    ],
)
def test_stage_password_never_reaches_child_argv_and_yaml_round_trips(tmp_path, password):
    """Real subprocess: proves the child never sees the password, and that it still works."""
    python, log, content_log = _password_capturing(tmp_path)
    engine = SubprocessEngine(cache_dir=tmp_path / "cache", python=python, cwd=tmp_path)
    enc = _make(tmp_path / "enc.pdf", [42], password=password)
    p = Pipeline((InputFile("A", enc, password),), (Stage("cat", "1"),))
    (result,) = _run(engine, p)
    assert result.kind is ResultKind.PDF, result.stderr
    assert _pages(result.pdf_path) == [(42, 0)]
    argv_seen = log.read_text()
    assert "--args" in argv_seen
    assert password not in argv_seen
    assert f"A={password}" in content_log.read_text()
    assert not list(engine.cache_dir.glob("*.yaml"))
    engine.close()


def test_save_routes_a_password_through_args_file_not_argv(tmp_path):
    python, log, content_log = _password_capturing(tmp_path)
    engine = SubprocessEngine(cache_dir=tmp_path / "cache", python=python, cwd=tmp_path)
    password = "sav3-p@ss w/space"
    enc = _make(tmp_path / "enc.pdf", [7], password=password)
    p = Pipeline((InputFile("A", enc, password),), (Stage("cat", "1"),))
    out = tmp_path / "out.pdf"
    r = engine.save(p, out, threading.Event())
    assert r.kind is ResultKind.PDF, r.stderr
    assert _pages(out) == [(7, 0)]
    assert password not in log.read_text()
    assert f"A={password}" in content_log.read_text()
    assert not list(engine.cache_dir.glob("*.yaml"))
    engine.close()


def test_passwordless_stage_still_runs_argv_directly(logged, files):
    """No password anywhere: the old, simpler direct-argv path is unchanged."""
    engine, _ = logged
    p = Pipeline((InputFile("A", files[0]),), (Stage("cat", "1"),))
    (result,) = _run(engine, p)
    assert result.kind is ResultKind.PDF
    assert "--args" not in (engine.cache_dir.parent / "launches.log").read_text()


def test_secret_run_cleans_up_args_file_on_cancel(tmp_path):
    python = _script(tmp_path / "py", "time.sleep(30)")
    engine = SubprocessEngine(cache_dir=tmp_path / "c", python=python)
    enc = _make(tmp_path / "enc.pdf", [5], password="pw123")
    p = Pipeline((InputFile("A", enc, "pw123"),), (Stage("cat", "1"),))
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    assert list(engine.run(p, cancel)) == []
    assert not list(engine.cache_dir.glob("*.yaml"))
    engine.close()


@pytest.mark.parametrize(
    "password",
    [
        "plain",
        "with space",
        "trailing space ",
        " leading space",
        "has#hash",
        "quote'd",
        'has "double"',
        "-leading-dash",
        "--flag-like",
        "café-🔥-unicode",
        "yes",
        "no",
        "null",
        "NULL",
        "true",
        "False",
        "~",
        "1.5",
        "",
    ],
)
def test_args_file_round_trips_tricky_passwords(tmp_path, password):
    engine = SubprocessEngine(cache_dir=tmp_path / "c")
    argv = ["A=in.pdf", "input_pw", f"A={password}", "cat", "output", "out.pdf"]
    path = engine._write_args_file(argv)
    try:
        if os.name == "posix":  # Windows keeps it private by the per-user temp dir
            assert (path.stat().st_mode & 0o777) == 0o600
        assert yaml.safe_load(path.read_text(encoding="utf-8")) == argv
    finally:
        path.unlink()
    engine.close()


def test_stage_failure_from_a_later_stage_input_is_flagged_needs_password(logged, files, tmp_path):
    """Later-stage `Stage.inputs` encrypted files were never checked for needs_password."""
    engine, _ = logged
    enc = _make(tmp_path / "c.pdf", [30], password="topsecret")
    p = Pipeline(
        (InputFile("A", files[0]), InputFile("C", enc)),
        (Stage("cat", "A1"), Stage("cat", "_1 C1", inputs=("C",))),
    )
    results = _run(engine, p)
    assert results[-1].kind is ResultKind.ERROR
    assert results[-1].needs_password
    assert results[-1].password_path == enc
    assert not list(engine.cache_dir.glob("*.yaml"))


def test_first_password_failure_skips_unreadable_files(tmp_path):
    engine = SubprocessEngine(cache_dir=tmp_path / "c")
    missing = InputFile("A", tmp_path / "gone.pdf")
    assert engine._first_password_failure([missing]) is None
    engine.close()


# --- output options: live-safe subset applies live, all of it applies at Save ---


def _first_id(path):
    with pikepdf.open(path) as pdf:
        return bytes(pdf.trailer.ID[0])


def test_live_run_applies_live_safe_output_options(logged, files):
    """Ground truth: pikepdf's own /ID trailer, not a re-derivation of the tokens.

    A1 B1 merges two distinct files, which always gets a fresh /ID on its own;
    keep_first_id must then make it match A's /ID exactly.
    """
    engine, _ = logged
    p = _pipeline(files, Stage("cat", "A1 B1"))
    p = Pipeline(p.inputs, p.stages, "keep_first_id")
    (r,) = _run(engine, p)
    assert r.kind is ResultKind.PDF
    assert _first_id(r.pdf_path) == _first_id(files[0])


def test_live_run_skips_save_only_output_options(logged, files):
    """owner_pw would need a password prompt; a live run must never need one."""
    engine, _ = logged
    p = Pipeline(
        (InputFile("A", files[0]),), (Stage("cat", "1"),), output_options="owner_pw s3cret"
    )
    (r,) = _run(engine, p)
    assert r.kind is ResultKind.PDF
    with pikepdf.open(r.pdf_path) as pdf:
        assert not pdf.is_encrypted


def test_only_the_last_stage_gets_output_options(logged, files):
    """A mid-pipeline stage's own run is untouched by the pipeline's output options:
    stage 0 gets a fresh /ID, unrelated to the input file's; keep_first_id on the
    last stage then copies stage 0's own output /ID (its "first input"), not the
    original input file's."""
    engine, _ = logged
    marked = files[0].with_name("marked.pdf")
    with pikepdf.open(files[0]) as pdf:
        # qpdf seeds a fresh /ID from the clock; a fixed one can't collide with it.
        pdf.trailer.ID = pikepdf.Array([b"A" * 16, b"A" * 16])
        pdf.save(marked)
    p = Pipeline(
        (InputFile("A", marked),),
        (Stage("cat", "1-2"), Stage("rotate", "1east")),
        output_options="keep_first_id",
    )
    results = _run(engine, p)
    assert _first_id(marked) == b"A" * 16
    assert _first_id(results[0].pdf_path) != b"A" * 16
    assert _first_id(results[1].pdf_path) == _first_id(results[0].pdf_path)


def _two_stage(files, output_options):
    return Pipeline(
        (InputFile("A", files[0]),),
        (Stage("cat", "1-2"), Stage("rotate", "1east")),
        output_options,
    )


def test_output_options_change_invalidates_only_the_last_stage(logged, files):
    engine, launches = logged
    first = _run(engine, _two_stage(files, ""))
    assert launches() == 2
    again = _run(engine, _two_stage(files, "keep_first_id"))
    assert launches() == 3
    assert (again[0].cached, again[1].cached) == (True, False)
    assert first[0].pdf_path == again[0].pdf_path


def test_save_applies_every_output_option_including_save_only(files, tmp_path):
    engine = SubprocessEngine(cache_dir=tmp_path / "c")
    p = Pipeline(
        (InputFile("A", files[0]),), (Stage("cat", "1"),), output_options="owner_pw s3cret"
    )
    out = tmp_path / "out.pdf"
    r = engine.save(p, out, threading.Event())
    assert r.kind is ResultKind.PDF, r.stderr
    with pikepdf.open(out, password="s3cret") as pdf:
        assert pdf.is_encrypted
    engine.close()


def test_output_options_are_secret_detects_owner_and_user_pw():
    engine = SubprocessEngine.__new__(SubprocessEngine)
    secret = Pipeline(output_options="owner_pw x")
    other_secret = Pipeline(output_options="user_pw x")
    plain = Pipeline(output_options="uncompress")
    assert engine._output_options_are_secret(secret)
    assert engine._output_options_are_secret(other_secret)
    assert not engine._output_options_are_secret(plain)


def test_output_options_are_secret_false_for_unparseable_text():
    engine = SubprocessEngine.__new__(SubprocessEngine)
    assert not engine._output_options_are_secret(Pipeline(output_options="banana"))


def test_save_routes_an_output_option_password_through_args_file(tmp_path):
    python, log, content_log = _password_capturing(tmp_path)
    engine = SubprocessEngine(cache_dir=tmp_path / "cache", python=python, cwd=tmp_path)
    a = _make(tmp_path / "a.pdf", [10])
    password = "out-secr3t-unique-9f8"
    p = Pipeline((InputFile("A", a),), (Stage("cat", "1"),), output_options=f"owner_pw {password}")
    out = tmp_path / "out.pdf"
    r = engine.save(p, out, threading.Event())
    assert r.kind is ResultKind.PDF, r.stderr
    assert "--args" in log.read_text()
    assert password not in log.read_text()
    assert password in content_log.read_text()
    engine.close()


def test_source_first_stage_reads_no_inputs(logged, files, tmp_path):
    engine, launches = logged
    p = _pipeline(files, Stage("create", "3"))
    first = _run(engine, p)
    assert first[0].kind is ResultKind.PDF
    assert first[0].page_count == 3
    _make(tmp_path / "a.pdf", [500])
    again = _run(engine, p)
    assert again[0].cached
    assert launches() == 1


def test_render_stage_hands_on_one_rasterized_pdf(logged, files):
    engine, _ = logged
    results = _run(engine, _pipeline(files, Stage("cat", "A"), Stage("render", "dpi=20")))
    assert results[1].kind is ResultKind.PDF
    with pikepdf.open(results[1].pdf_path) as pdf:
        assert len(pdf.pages) == 4
        assert all(len(page.get_images()) == 1 for page in pdf.pages)
        assert all(b"Tj" not in page.Contents.read_bytes() for page in pdf.pages)
