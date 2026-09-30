# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/engine.py

"""Stage-by-stage pipeline runs through the real CLI, with a result cache. Qt-free.

Stage 0 runs as `<inputs> op args output s0.pdf`. Stage i runs as
`s{i-1}.pdf --- <its inputs> op args output si.pdf`: the implicit filter
stage loads the previous output, which the CLI then passes on as `_`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import pikepdf
import yaml

import pdftl
from pdftl.gui import stage_model
from pdftl.gui.op_policy import is_source
from pdftl.gui.interfaces import (
    InputFile,
    OpKind,
    Pipeline,
    ResultKind,
    Stage,
    StageResult,
    declare_inputs,
)

try:
    import fcntl
except ImportError:  # pragma: no cover -- Windows
    fcntl = None

try:
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None

POLL_SECONDS = 0.05
_POSIX = os.name == "posix"
PREVIEW_COLUMNS = 100
KILL_GRACE_SECONDS = 2.0
MAX_CACHE_ENTRIES = 500
LOCK_FILENAME = ".lock"
STALE_SECONDS = 24 * 60 * 60
SWEEP_GRACE_SECONDS = 60
_ERROR_LINE = re.compile(r"^\[[^\]]+\] Error: (.*)$")


def _default_kind(op: str) -> OpKind | None:
    from pdftl.gui import op_policy

    try:
        return op_policy.classify(op).kind
    except KeyError:
        return None


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str


def _file_stamp(path: Path) -> list:
    try:
        st = Path(path).stat()
    except OSError:
        return [str(path), None, None]
    return [str(path), st.st_size, st.st_mtime_ns]


def _kill(proc: subprocess.Popen) -> None:
    """Kill the stage and anything it spawned, which may hold its pipes open."""
    if _POSIX:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass  # Already exited; macOS says EPERM when the group is all zombies.
    else:
        _kill_tree_windows(proc)


def _kill_tree_windows(proc: subprocess.Popen) -> None:
    """Kill `proc` and its descendants; helper processes don't share its pipes."""
    try:
        subprocess.run(  # nosec B603 B607 # nosemgrep -- a system tool, no shell
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        proc.kill()


def _lock_acquire(path: Path):
    """Open `path` and take an exclusive, non-blocking lock. None if already held."""
    try:
        fd = open(path, "a+")
    except OSError:
        return None
    try:
        if _POSIX:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            fd.write("l")
            fd.flush()
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        fd.close()
        return None
    return fd


def _lock_release(fd) -> None:
    if _POSIX:
        fcntl.flock(fd, fcntl.LOCK_UN)
    else:
        fd.seek(0)
        msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
    fd.close()


def _is_stale(path: Path, seconds: float = STALE_SECONDS) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) > seconds
    except OSError:
        return False


def _reap(proc: subprocess.Popen) -> None:
    """Wait briefly for a killed stage; a surviving grandchild may hold its pipes open."""
    try:
        proc.communicate(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        for pipe in (proc.stdout, proc.stderr):
            pipe.close()


def _page_count(path: Path) -> int:
    with pikepdf.open(path) as pdf:
        return len(pdf.pages)


def _live_output_tokens(pipeline: Pipeline) -> list[str]:
    """Output-box tokens that also apply to a live run.

    Excludes encryption/signing options: those only make sense once, at
    Save, so a live preview never needs a password or real key files.
    """
    options = stage_model.parse_output_options(pipeline.output_options)
    live = {k: v for k, v in options.items() if not stage_model.is_save_only_option(k)}
    return stage_model.option_tokens(live)


def password_kind(pdf: pikepdf.Pdf) -> str | None:
    """Which credential opened `pdf`: `"owner"`, `"user"`, or None if unencrypted.

    An owner password that also happens to work as the user password (a file
    encrypted with one password for both roles) reports as `"owner"`.
    """
    if pdf.owner_password_matched:
        return "owner"
    if pdf.user_password_matched:
        return "user"
    return None


class SubprocessEngine:
    """Engine that runs each stage in a fresh `python -m pdftl` process."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        kind_of: Callable[[str], OpKind | None] = _default_kind,
        python: str | Sequence[str] = sys.executable,
        cwd: Path | None = None,
    ):
        """Stages run in `cwd` (default: the current directory) so relative file
        arguments resolve as on the command line; file-writing stages run in a
        scratch directory instead. `python` is the interpreter, or a command
        that stands in for one."""
        self.cwd = Path(cwd or Path.cwd())
        self._password_key = secrets.token_bytes(32)
        self._own_dir = cache_dir is None
        self.cache_dir = Path(cache_dir or tempfile.mkdtemp(prefix="pdftl-gui-"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock_fd = None
        if self._own_dir:
            self._sweep_stale_cache_dirs()
            self._lock_fd = _lock_acquire(self.cache_dir / LOCK_FILENAME)
        self.kind_of = kind_of
        self.python = [python] if isinstance(python, str) else list(python)
        src = str(Path(pdftl.__file__).resolve().parents[1])
        self.env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(filter(None, [src, os.environ.get("PYTHONPATH")])),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        self.preview_env = {"FORCE_COLOR": "1", "COLUMNS": str(PREVIEW_COLUMNS), **self.env}

    def _sweep_stale_cache_dirs(self) -> None:
        """Remove other pdftl-gui-* dirs left behind by engines that are no longer running."""
        for other in Path(tempfile.gettempdir()).glob("pdftl-gui-*"):
            if other == self.cache_dir or not other.is_dir():
                continue
            # A starting engine creates its lock file just before locking it.
            if not _is_stale(other, SWEEP_GRACE_SECONDS):
                continue
            lock_path = other / LOCK_FILENAME
            if not lock_path.exists():
                if _is_stale(other):
                    shutil.rmtree(other, ignore_errors=True)
                continue
            fd = _lock_acquire(lock_path)
            if fd is None:
                continue
            _lock_release(fd)
            shutil.rmtree(other, ignore_errors=True)

    def stage_argv(
        self, pipeline: Pipeline, index: int, previous: Path | None, output: Path | None
    ) -> list[str]:
        """CLI arguments running stage `index` alone on `previous`, the prior stage's PDF.

        The last stage's live-safe output options (see `_live_output_tokens`)
        are appended too, so its preview matches what Save would produce.
        """
        stage = pipeline.stages[index]
        is_last = index == len(pipeline.stages) - 1
        extra = _live_output_tokens(pipeline) if is_last else []
        if index == 0:
            return [*Pipeline(pipeline.inputs, (stage,)).to_argv(output), *extra]
        files = {f.handle: f for f in pipeline.inputs}
        missing = [h for h in stage.inputs if h not in files]
        if missing:
            raise ValueError(f"stage {index + 1} uses undefined handles {missing}")
        used = [files[h] for h in stage.inputs]
        argv = [str(previous), "---", *declare_inputs(used), stage.op, *stage.tokens()]
        if output is not None:
            argv = [*argv, "output", str(output)]
        return [*argv, *extra]

    def _files_for_stage(self, pipeline: Pipeline, index: int) -> list[InputFile]:
        """Input files stage `index` reads: every input on stage 0 (none for a source
        operation), else its declared ones."""
        stage = pipeline.stages[index]
        if index == 0:
            return [] if is_source(stage.op) else list(pipeline.inputs)
        return [f for f in pipeline.inputs if f.handle in stage.inputs]

    def _password_digest(self, password: str | None) -> str | None:
        """HMAC of `password` under a random per-engine key; None stays None.

        Different passwords (or engines) hash differently, so the cache key still
        invalidates correctly, but nothing brute-forceable reaches disk.
        """
        if password is None:
            return None
        return hmac.new(self._password_key, password.encode("utf-8"), hashlib.sha256).hexdigest()

    def _key(self, pipeline: Pipeline, index: int, previous_key: str) -> str:
        stage = pipeline.stages[index]
        is_last = index == len(pipeline.stages) - 1
        files = self._files_for_stage(pipeline, index)
        material = {
            "previous": previous_key,
            "op": stage.op,
            "tokens": stage.tokens(),
            "output_options": _live_output_tokens(pipeline) if is_last else [],
            "inputs": list(stage.inputs) if index else [],
            "files": [
                [f.handle, *_file_stamp(f.path), self._password_digest(f.password)] for f in files
            ],
            "arg_files": [_file_stamp(p) for p in self._file_args(stage)],
            "version": pdftl.__version__,
        }
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:32]

    def _file_args(self, stage: Stage) -> list[Path]:
        """Existing files named by an argument (`f.pdf` or `key=f.pdf`), whose content
        the operation may read."""
        found = []
        for token in stage.tokens():
            for candidate in {token, token.partition("=")[2]} - {""}:
                path = self.cwd / candidate
                if path.is_file():
                    found.append(path)
        return sorted(found)

    def _exec(
        self, argv: list[str], cwd: Path, cancel: threading.Event, env: dict[str, str]
    ) -> Completed | None:
        proc = subprocess.Popen(  # nosec B603 # nosemgrep -- argv list, no shell
            [*self.python, "-m", "pdftl", *argv],
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            start_new_session=_POSIX,
        )
        while True:
            try:
                out, err = proc.communicate(timeout=POLL_SECONDS)
            except subprocess.TimeoutExpired:
                if cancel.is_set():
                    _kill(proc)
                    _reap(proc)
                    return None
                continue
            return Completed(proc.returncode, out, err)

    def _write_args_file(self, argv: list[str]) -> Path:
        """Write `argv` as a private, owner-only-readable `--args` YAML file."""
        fd, name = tempfile.mkstemp(dir=self.cache_dir, prefix=".pw-", suffix=".yaml")
        os.close(fd)
        path = Path(name)
        os.chmod(path, 0o600)
        path.write_text(yaml.safe_dump(argv, allow_unicode=True), encoding="utf-8")
        return path

    def _run_argv(
        self,
        argv: list[str],
        cwd: Path,
        cancel: threading.Event,
        env: dict[str, str],
        secret: bool,
    ) -> Completed | None:
        """Run `argv`, or, if `secret`, its content via a `--args` file so no password
        reaches the child's own command line (visible to other processes via `ps`)."""
        if not secret:
            return self._exec(argv, cwd, cancel, env)
        path = self._write_args_file(argv)
        try:
            return self._exec(["--args", str(path)], cwd, cancel, env)
        finally:
            path.unlink(missing_ok=True)

    def _first_password_failure(self, files: list[InputFile]) -> InputFile | None:
        """The first file among `files` whose configured password doesn't open it."""
        for f in files:
            try:
                with pikepdf.open(f.path, password=f.password or ""):
                    pass
            except pikepdf.PasswordError:
                return f
            except (pikepdf.PdfError, OSError):
                continue
        return None

    def _cached(self, index: int, key: str) -> StageResult | None:
        meta = self.cache_dir / f"{key}.json"
        if not meta.exists():
            return None
        data = json.loads(meta.read_text(encoding="utf-8"))
        pdf = Path(data["pdf_path"]) if data["pdf_path"] else None
        if pdf is not None and not pdf.exists():
            return None
        return StageResult(
            index=index,
            kind=ResultKind[data["kind"]],
            key=key,
            pdf_path=pdf,
            page_count=data["page_count"],
            text=data["text"],
            stderr=data["stderr"],
            cached=True,
        )

    def _store(self, result: StageResult) -> None:
        data = {
            "kind": result.kind.name,
            "pdf_path": str(result.pdf_path) if result.pdf_path else None,
            "page_count": result.page_count,
            "text": result.text,
            "stderr": result.stderr,
        }
        (self.cache_dir / f"{result.key}.json").write_text(json.dumps(data), encoding="utf-8")
        self._prune()

    def _prune(self) -> None:
        """Keep the MAX_CACHE_ENTRIES most recent results.

        An entry still open elsewhere (e.g. a viewer holding its PDF) is left for a
        later prune rather than raising.
        """
        metas = sorted(self.cache_dir.glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
        for meta in metas[: max(0, len(metas) - MAX_CACHE_ENTRIES)]:
            try:
                meta.unlink()
                meta.with_suffix(".pdf").unlink(missing_ok=True)
                shutil.rmtree(meta.with_suffix(".d"), ignore_errors=True)
            except OSError:
                pass

    def _error(
        self,
        index: int,
        key: str,
        message: str,
        done: Completed | None = None,
        needs_password: bool = False,
        password_path: Path | None = None,
    ):
        return StageResult(
            index=index,
            kind=ResultKind.ERROR,
            key=key,
            text=done.stdout if done else "",
            stderr=done.stderr if done else "",
            error=message,
            needs_password=needs_password,
            password_path=password_path,
        )

    def failure_reason(self, done: Completed) -> str:
        """pdftl's own error text from `done`, with cache paths named for what they are."""
        lines = done.stderr.splitlines()
        starts = [i for i, line in enumerate(lines) if _ERROR_LINE.match(line)]
        if not starts:
            return f"pdftl exited with status {done.returncode}"
        first = starts[-1]
        rest = []
        for line in lines[first + 1 :]:
            if not line.startswith((" ", "\t")):
                break
            rest.append(line.strip())
        reason = " ".join([_ERROR_LINE.match(lines[first]).group(1), *rest])
        cache = re.escape(str(self.cache_dir))
        return re.sub(cache + r"\S*?\.pdf", "the previous stage's output", reason)

    def _run_stage(
        self,
        pipeline: Pipeline,
        index: int,
        key: str,
        previous: StageResult | None,
        cancel: threading.Event,
    ) -> StageResult | None:
        stage = pipeline.stages[index]
        kind = self.kind_of(stage.op)
        if kind is None:
            return self._error(index, key, f"Unknown operation {stage.op!r}")
        if kind is OpKind.BLOCKED:
            return self._error(index, key, f"{stage.op} cannot run as a GUI stage")
        work = self.cache_dir / f"{key}.d"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir()
        cwd = work if kind is OpKind.SIDE_EFFECT else self.cwd
        out = self.cache_dir / f"{key}.pdf" if kind is OpKind.PDF else None
        try:
            argv = self.stage_argv(pipeline, index, previous and previous.pdf_path, out)
        except ValueError as exc:
            return self._error(index, key, str(exc))
        files = self._files_for_stage(pipeline, index)
        secret = any(f.password for f in files)
        done = self._run_argv(argv, cwd, cancel, self.preview_env, secret)
        if done is None:
            return None
        if done.returncode != 0:
            bad = self._first_password_failure(files)
            if bad is not None:
                return self._error(
                    index,
                    key,
                    f"{bad.path.name} needs a password",
                    done,
                    needs_password=True,
                    password_path=bad.path,
                )
            return self._error(index, key, self.failure_reason(done), done)
        if out is not None and not out.exists():
            return self._error(index, key, "The stage produced no PDF", done)
        return self._finish(index, key, kind, out, previous, done, work)

    def _finish(self, index, key, kind, out, previous, done, work) -> StageResult:
        stderr = done.stderr
        if kind is OpKind.SIDE_EFFECT:
            written = sum(1 for p in work.rglob("*") if p.is_file())
            stderr = f"{stderr}wrote {written} file(s) in {work}\n"
        if out is not None:
            pdf, pages, rkind = out, _page_count(out), ResultKind.PDF
        elif previous is not None and previous.pdf_path is not None:
            pdf, pages, rkind = previous.pdf_path, previous.page_count, ResultKind.TEXT
        else:
            pdf, pages, rkind = None, 0, ResultKind.TEXT
        result = StageResult(index, rkind, key, pdf, pages, done.stdout, stderr)
        self._store(result)
        return result

    def run(self, pipeline: Pipeline, cancel: threading.Event) -> Iterator[StageResult]:
        """Yield one result per stage; stop after an ERROR or once `cancel` is set."""
        previous = self.preview_input(pipeline.inputs[0]) if pipeline.inputs else None
        key = "root"
        for index in range(len(pipeline.stages)):
            if cancel.is_set():
                return
            try:
                key = self._key(pipeline, index, key)
            except ValueError as exc:
                yield self._error(index, key, f"Cannot parse arguments: {exc}")
                return
            result = self._cached(index, key) or self._run_stage(
                pipeline, index, key, previous, cancel
            )
            if result is None:
                return
            yield result
            if result.kind is ResultKind.ERROR:
                return
            previous = result

    def preview_input(self, item: InputFile) -> StageResult:
        key = hashlib.sha256(json.dumps(_file_stamp(item.path)).encode()).hexdigest()[:32]
        try:
            with pikepdf.open(item.path, password=item.password or "") as pdf:
                pages = len(pdf.pages)
                kind = password_kind(pdf)
        except pikepdf.PasswordError as exc:
            return StageResult(-1, ResultKind.ERROR, key, error=str(exc), needs_password=True)
        except (pikepdf.PdfError, OSError) as exc:
            return StageResult(-1, ResultKind.ERROR, key, error=str(exc))
        return StageResult(
            -1, ResultKind.PDF, key, pdf_path=Path(item.path), page_count=pages, password_kind=kind
        )

    @staticmethod
    def _output_options_are_secret(pipeline: Pipeline) -> bool:
        """Whether a password option appears in the output options, so its
        cleartext value must go through the `--args` file, not the child's argv."""
        try:
            options = stage_model.parse_output_options(pipeline.output_options)
        except ValueError:
            return False
        return bool(stage_model.output_password_options() & options.keys())

    def save(self, pipeline: Pipeline, output: Path, cancel: threading.Event) -> StageResult:
        """Run the whole pipeline once, exactly as `to_argv(output)`."""
        index = len(pipeline.stages) - 1
        secret = any(f.password for f in pipeline.inputs) or self._output_options_are_secret(
            pipeline
        )
        done = self._run_argv(pipeline.to_argv(Path(output)), self.cwd, cancel, self.env, secret)
        if done is None:
            return self._error(index, "save", "Cancelled")
        if done.returncode != 0:
            return self._error(index, "save", f"pdftl exited with status {done.returncode}", done)
        kind = ResultKind.PDF if Path(output).exists() else ResultKind.TEXT
        return StageResult(index, kind, "save", Path(output), 0, done.stdout, done.stderr)

    def close(self) -> None:
        if self._own_dir:
            if self._lock_fd is not None:
                _lock_release(self._lock_fd)
            shutil.rmtree(self.cache_dir, ignore_errors=True)
