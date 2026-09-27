# tests/utils/test_memory_profiling.py

import io
import logging
import sys
import types
from pathlib import Path

import numpy as np
import pytest

import pdftl.utils.memory_profiling as mp
from pdftl.utils.memory_profiling import MB, StageMemoryWatch
from pdftl.utils.profiling import CliStageProfiler

LINUX_PEAK_RESET = sys.platform.startswith("linux") and mp.reset_peak_rss()


@pytest.fixture(autouse=True)
def no_profiling_env(monkeypatch):
    for key in ("PDFTL_MEMORY_THRESHOLD", "PDFTL_PROFILE_MEMORY", "PDFTL_PROFILE_STAGES"):
        monkeypatch.delenv(key, raising=False)


# --- reading and resetting the peak ---


def test_peak_is_read_from_vmhwm(tmp_path, monkeypatch):
    status = tmp_path / "status"
    status.write_text("Name:\tpython\nVmPeak:\t 999 kB\nVmHWM:\t   12345 kB\nVmRSS:\t 1 kB\n")
    monkeypatch.setattr(mp, "_STATUS", str(status))
    assert mp.peak_rss_bytes() == 12345 * 1024


@pytest.mark.parametrize("platform,expected", [("linux", 2048 * 1024), ("darwin", 2048)])
def test_peak_falls_back_to_getrusage(monkeypatch, platform, expected):
    monkeypatch.setattr(mp, "_STATUS", "/nonexistent/status")
    fake = types.SimpleNamespace(
        RUSAGE_SELF=0, getrusage=lambda who: types.SimpleNamespace(ru_maxrss=2048)
    )
    monkeypatch.setitem(sys.modules, "resource", fake)
    monkeypatch.setattr(mp.sys, "platform", platform)
    assert mp.peak_rss_bytes() == expected


def test_peak_unknown_without_proc_or_resource(tmp_path, monkeypatch):
    status = tmp_path / "status"
    status.write_text("Name:\tpython\n")  # no VmHWM line
    monkeypatch.setattr(mp, "_STATUS", str(status))
    monkeypatch.setitem(sys.modules, "resource", None)  # import fails, as on Windows
    assert mp.peak_rss_bytes() is None


def test_reset_writes_5_to_clear_refs(tmp_path, monkeypatch):
    clear_refs = tmp_path / "clear_refs"
    monkeypatch.setattr(mp, "_CLEAR_REFS", str(clear_refs))
    assert mp.reset_peak_rss() is True
    assert clear_refs.read_text() == "5"
    monkeypatch.setattr(mp, "_CLEAR_REFS", str(tmp_path / "missing" / "clear_refs"))
    assert mp.reset_peak_rss() is False


@pytest.mark.skipif(not LINUX_PEAK_RESET, reason="needs Linux peak-RSS reset")
def test_stage_peak_includes_a_real_allocation(monkeypatch):
    monkeypatch.setenv("PDFTL_MEMORY_THRESHOLD", "100000")  # opt in to the per-stage reset
    with StageMemoryWatch("alloc", []) as watch:
        before = mp.peak_rss_bytes()
        block = np.ones(300 * MB // 8)  # 300 MB, touched
        block[::512] = 2.0
    assert watch.peak - before >= 290 * MB
    del block


# --- attributing a peak to a stage ---


@pytest.mark.parametrize(
    "reset,before,after,expected",
    [
        (True, 900, 300, 300),  # reset: the stage's own peak
        (False, 300, 900, 900),  # a new process high: this stage set it
        (False, 900, 900, None),  # at or below an earlier high: unknown
        (False, None, 500, 500),
        (True, 100, None, None),  # no peak available at all
    ],
)
def test_stage_peak_attribution(monkeypatch, reset, before, after, expected):
    watch = StageMemoryWatch("s", [])
    watch._reset, watch._before = reset, before
    monkeypatch.setattr(mp, "peak_rss_bytes", lambda: after)
    assert watch._stage_peak() == expected


def _run_watch(monkeypatch, peak, threshold_mb):
    monkeypatch.setenv("PDFTL_MEMORY_THRESHOLD", str(threshold_mb))
    monkeypatch.setattr(mp, "reset_peak_rss", lambda: True)
    monkeypatch.setattr(mp, "peak_rss_bytes", lambda: peak)
    with StageMemoryWatch("shrink", ["strong"]):
        pass


def test_breach_logs_a_hint(monkeypatch, caplog):
    with caplog.at_level(logging.INFO):
        _run_watch(monkeypatch, 1536 * MB, 1000)
    assert (
        "Stage 'shrink' peaked at 1536 MB of memory, above 1000 MB. "
        "To see where: PDFTL_PROFILE_MEMORY=shrink" in caplog.text
    )


@pytest.mark.parametrize("peak", [999 * MB, None])
def test_no_hint_under_threshold_or_unknown(monkeypatch, caplog, peak):
    with caplog.at_level(logging.INFO):
        _run_watch(monkeypatch, peak, 1000)
    assert "peaked" not in caplog.text


@pytest.mark.parametrize(
    "targets,expected",
    [
        ("all", True),
        ("TRUE", True),
        ("1", True),
        ("shrink, save", True),
        ("save", False),
        ("", False),
    ],
)
def test_targeting(targets, expected):
    assert mp.is_targeted("shrink", targets) is expected


# --- active profiling ---


def _allocate_and_hold():
    import time

    held = np.ones(40 * MB // 8)  # the line the report must name
    time.sleep(0.15)
    return float(held.sum())


def test_profiled_stage_writes_a_report_naming_the_allocation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PDFTL_PROFILE_MEMORY", "render")
    with StageMemoryWatch("render", ["1-2"]):
        _allocate_and_hold()
    reports = list(Path("pdftl_profiles").glob("render_*_memory.txt"))
    assert len(reports) == 1
    assert reports[0].with_suffix(".tracemalloc").exists()
    text = reports[0].read_text()
    assert "MEMORY REPORT | Stage: 'render'" in text
    assert "Stage Args   : 1-2" in text
    this_file = Path(__file__).name
    line = next(
        i
        for i, src in enumerate(Path(__file__).read_text().splitlines(), 1)
        if "the line the report must name" in src
    )
    assert f'{this_file}", line {line}' in text  # in the largest call stack
    stacks = text.split("Largest 8 call stacks:")[1]
    assert float(stacks.split()[0]) >= 39.0  # MB in the largest stack


def test_pdftl_sites_charge_the_innermost_pdftl_frame():
    package = str(Path(mp.__file__).resolve().parents[1])

    def stat(size, count, *frames):
        tb = [types.SimpleNamespace(filename=f, lineno=n) for f, n in frames]
        return types.SimpleNamespace(size=size, count=count, traceback=tb)

    ours, lib = f"{package}/utils/a.py", "/site-packages/numpy/core.py"
    snapshot = types.SimpleNamespace(
        statistics=lambda key: [
            # oldest frame first; the pdftl frame nearest the allocation wins
            stat(100, 1, (f"{package}/cli/main.py", 5), (ours, 10), (lib, 99)),
            stat(40, 2, (ours, 10), (lib, 7)),
            stat(70, 1, (ours, 20)),
            stat(999, 9, ("/usr/lib/python3/json.py", 1)),  # no pdftl frame: left out
        ]
    )
    assert mp.pdftl_sites(snapshot) == [(140, 3, f"{ours}:10"), (70, 1, f"{ours}:20")]


def test_a_stage_too_quick_to_sample_still_gets_a_full_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PDFTL_PROFILE_MEMORY", "all")
    with StageMemoryWatch("tiny", []):
        pass
    text = next(Path("pdftl_profiles").glob("tiny_*_memory.txt")).read_text()
    assert "Peak traced (Python/numpy)" in text
    assert "--- NEAR THE PEAK:" in text


def test_report_says_when_the_peak_was_missed():
    import tracemalloc

    tracemalloc.start()
    try:
        snapshot = tracemalloc.take_snapshot()
    finally:
        tracemalloc.stop()
    out = io.StringIO()
    mp.write_snapshot(out, snapshot, traced_peak=500 * MB)
    assert "of 500.0 MB traced" in out.getvalue()
    assert "too brief to catch" in out.getvalue()


def test_unknown_rss_is_reported_as_unknown(tmp_path, monkeypatch):
    watch = StageMemoryWatch("s", [])
    out = io.StringIO()
    watch._write_summary(out, 10 * MB)
    assert "Peak resident memory (RSS) : unknown" in out.getvalue()


# --- the CLI stage profiler runs the memory watch ---


def test_peak_is_not_reset_unless_asked(monkeypatch):
    calls = []
    monkeypatch.setattr(mp, "reset_peak_rss", lambda: calls.append(1) or True)
    with StageMemoryWatch("s", []):
        pass
    assert calls == []  # getrusage and /usr/bin/time keep the process's true peak
    monkeypatch.setenv("PDFTL_MEMORY_THRESHOLD", "500")
    with StageMemoryWatch("s", []):
        pass
    assert calls == [1]


def test_cli_stage_profiler_measures_memory(monkeypatch):
    monkeypatch.setenv("PDFTL_MEMORY_THRESHOLD", "500")
    monkeypatch.setattr(mp, "reset_peak_rss", lambda: True)
    monkeypatch.setattr(mp, "peak_rss_bytes", lambda: 321 * MB)
    with CliStageProfiler("cat", []) as profiler:
        pass
    assert profiler.memory.peak == 321 * MB


def test_cli_monitors_the_operation_and_the_save(tmp_path):
    import os
    import subprocess

    import pikepdf

    src = tmp_path / "in.pdf"
    doc = pikepdf.new()
    doc.add_blank_page()
    doc.save(src)
    env = dict(os.environ, PDFTL_MEMORY_THRESHOLD="0")
    env["PYTHONPATH"] = str(Path(mp.__file__).resolve().parents[2])
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pdftl",
            str(src),
            "cat",
            "output",
            str(tmp_path / "out.pdf"),
            "verbose",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert "Stage 'cat' peaked at" in proc.stderr
    assert "Stage 'save' peaked at" in proc.stderr
    assert (tmp_path / "out.pdf").exists()
