# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/memory_profiling.py

"""Per-stage peak memory: a cheap passive check, and tracemalloc on request.

Passive: each stage's peak resident memory, from the kernel's high-water
mark. By default a stage is measured only when it sets a new high for the
process. When the user sets PDFTL_MEMORY_THRESHOLD or PDFTL_PROFILE_MEMORY
on Linux, the mark is reset at each stage so every stage gets its own peak;
the reset is opt-in because it also resets the peak that getrusage and
/usr/bin/time report for the process.

Active (PDFTL_PROFILE_MEMORY=<stage>|all): tracemalloc records Python and
numpy allocations, and a background thread snapshots them near their peak.
Native allocations (qpdf, Pillow's C buffers) are not traced; the report
shows them only as the gap between peak RSS and the traced peak.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

MB = 1024 * 1024
DEFAULT_THRESHOLD_MB = 500
_STATUS = "/proc/self/status"
_CLEAR_REFS = "/proc/self/clear_refs"
_SNAPSHOT_INTERVAL = 0.01  # seconds; polling is cheap, snapshots happen only on growth
_SNAPSHOT_GROWTH = 1.05  # re-snapshot once traced memory grows 5% past the last one
_TRACE_FRAMES = 12


def reset_peak_rss() -> bool:
    """Reset the kernel's peak-RSS counter (Linux 4.0+); False where unsupported."""
    try:
        with open(_CLEAR_REFS, "w", encoding="ascii") as f:
            f.write("5")
    except OSError:
        return False
    return True


def peak_rss_bytes() -> int | None:
    """Peak resident memory since process start, or since the last reset; None if unknown."""
    try:
        with open(_STATUS, encoding="ascii") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    try:
        import resource
    except ImportError:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024  # macOS reports bytes, others KiB


def is_targeted(stage_name: str, targets: str) -> bool:
    """Whether `targets` (a comma list, or all/1/true) names this stage."""
    if not targets:
        return False
    if targets.lower() in ("all", "1", "true"):
        return True
    return stage_name in {t.strip() for t in targets.split(",")}


class _PeakSnapshots(threading.Thread):
    """Takes a tracemalloc snapshot whenever traced memory reaches a new high."""

    def __init__(self) -> None:
        super().__init__(name="pdftl-memory-snapshots", daemon=True)
        self._stop_event = threading.Event()
        self.best = 0
        self.snapshot = None

    def run(self) -> None:
        while not self._stop_event.wait(_SNAPSHOT_INTERVAL):
            self.sample()

    def sample(self) -> None:
        import tracemalloc

        current, _ = tracemalloc.get_traced_memory()
        if current > self.best * _SNAPSHOT_GROWTH:
            self.best = current
            self.snapshot = tracemalloc.take_snapshot()

    def finish(self) -> None:
        self._stop_event.set()
        self.join()
        self.sample()


class StageMemoryWatch:
    """Context manager measuring one pipeline stage's memory; see the module docstring."""

    def __init__(self, stage_name: str, stage_args: list[str]) -> None:
        self.stage_name = stage_name
        self.stage_args = stage_args
        self.threshold = float(os.environ.get("PDFTL_MEMORY_THRESHOLD", DEFAULT_THRESHOLD_MB)) * MB
        self.targeted = is_targeted(stage_name, os.environ.get("PDFTL_PROFILE_MEMORY", ""))
        self._opted_in = "PDFTL_MEMORY_THRESHOLD" in os.environ or bool(
            os.environ.get("PDFTL_PROFILE_MEMORY")
        )
        self.peak: int | None = None
        self._before: int | None = None
        self._reset = False
        self._snapshots: _PeakSnapshots | None = None

    def __enter__(self) -> StageMemoryWatch:
        self._before = peak_rss_bytes()
        self._reset = self._opted_in and reset_peak_rss()
        if self.targeted:
            import tracemalloc

            tracemalloc.start(_TRACE_FRAMES)
            self._snapshots = _PeakSnapshots()
            self._snapshots.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.peak = self._stage_peak()
        if self.peak is not None and self.peak > self.threshold:
            logger.info(
                "Stage '%s' peaked at %.0f MB of memory, above %.0f MB. "
                "To see where: PDFTL_PROFILE_MEMORY=%s",
                self.stage_name,
                self.peak / MB,
                self.threshold / MB,
                self.stage_name,
            )
        if self._snapshots is not None:
            self._write_report()

    def _stage_peak(self) -> int | None:
        after = peak_rss_bytes()
        if after is None:
            return None
        if self._reset or self._before is None or after > self._before:
            return after
        return None  # below an earlier stage's peak: this stage's own peak is unknown

    def _write_report(self) -> None:
        import tracemalloc

        self._snapshots.finish()
        _, traced_peak = tracemalloc.get_traced_memory()
        snapshot = self._snapshots.snapshot
        tracemalloc.stop()
        from pdftl.utils.profiling import report_base

        base = report_base(self.stage_name, suffix="memory")
        with open(f"{base}.txt", "w", encoding="utf-8") as f:
            self._write_summary(f, traced_peak)
            write_snapshot(f, snapshot, traced_peak)  # finish() always leaves one
        snapshot.dump(f"{base}.tracemalloc")
        logger.warning(
            "[MEMORY] Stage '%s' profiled. Report saved to: %s.txt", self.stage_name, base
        )

    def _write_summary(self, f, traced_peak: int) -> None:
        import shlex

        rss = "unknown" if self.peak is None else f"{self.peak / MB:.1f} MB"
        f.write(f"MEMORY REPORT | Stage: '{self.stage_name}'\n")
        f.write(f"Full Command : {shlex.join(sys.argv)}\n")
        f.write(f"Stage Args   : {shlex.join(self.stage_args)}\n\n")
        f.write(f"Peak resident memory (RSS) : {rss}\n")
        f.write(f"Peak traced (Python/numpy) : {traced_peak / MB:.1f} MB\n")
        f.write(
            "RSS also counts memory held before the stage, and native allocations\n"
            "tracemalloc cannot see (qpdf, Pillow's C buffers).\n\n"
        )


def pdftl_sites(snapshot) -> list[tuple[int, int, str]]:
    """(size, blocks, "file:line") per innermost pdftl frame, largest first.

    Allocations made inside numpy or Pillow are charged to the pdftl line
    that called into them; allocations with no pdftl frame are left out.
    """
    package = str(Path(__file__).resolve().parents[1])
    totals: dict[str, list[int]] = {}
    for stat in snapshot.statistics("traceback"):
        frame = next(
            (fr for fr in reversed(stat.traceback) if fr.filename.startswith(package)), None
        )
        if frame is None:
            continue
        entry = totals.setdefault(f"{frame.filename}:{frame.lineno}", [0, 0])
        entry[0] += stat.size
        entry[1] += stat.count
    return sorted(((s, c, w) for w, (s, c) in totals.items()), reverse=True)


def write_snapshot(f, snapshot, traced_peak: int, top: int = 25, traces: int = 8) -> None:
    """Write a snapshot's largest allocation sites, by line and by call stack."""
    import tracemalloc

    snapshot = snapshot.filter_traces(
        (
            tracemalloc.Filter(False, tracemalloc.__file__),
            tracemalloc.Filter(False, "<frozen importlib._bootstrap*>"),
        )
    )
    total = sum(stat.size for stat in snapshot.statistics("filename"))
    f.write(f"--- NEAR THE PEAK: {total / MB:.1f} of {traced_peak / MB:.1f} MB traced ---\n")
    if total < traced_peak / 2:
        f.write("The peak was too brief to catch; this shows a smaller moment.\n")
    f.write("\n")
    f.write(f"Largest {top} pdftl lines responsible (innermost pdftl frame):\n")
    for size, count, where in pdftl_sites(snapshot)[:top]:
        f.write(f"  {size / MB:9.1f} MB  {count:8d} blocks  {where}\n")
    f.write(f"\nLargest {top} allocation sites (line):\n")
    for stat in snapshot.statistics("lineno")[:top]:
        frame = stat.traceback[0]
        where = f"{frame.filename}:{frame.lineno}"
        f.write(f"  {stat.size / MB:9.1f} MB  {stat.count:8d} blocks  {where}\n")
    f.write(f"\nLargest {traces} call stacks:\n")
    for stat in snapshot.statistics("traceback")[:traces]:
        f.write(f"\n  {stat.size / MB:.1f} MB in {stat.count} blocks\n")
        for line in stat.traceback.format(most_recent_first=True)[:16]:
            f.write(f"    {line}\n")
