# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/profiling.py

"""
Performance profiling utilities for pdftl CLI stages.
Provides zero-overhead threshold monitoring and targeted cProfile execution.
"""

import os
import sys
import time
import datetime
from pathlib import Path

import logging

logger = logging.getLogger(__name__)


class CliStageProfiler:
    """
    A context manager that profiles a specific pipeline stage.

    Operates in two modes:
    1. Passive Monitoring: Tracks execution time with zero overhead.
    2. Active Profiling: Targeted via PDFTL_PROFILE_STAGES.
    """

    def __init__(self, stage_name: str, stage_args: list[str]):
        self.stage_name = stage_name
        self.stage_args = stage_args
        self.threshold = float(os.environ.get("PDFTL_SLOW_THRESHOLD", "0.5"))
        self.profiler = None
        self.start_time = None

        self._initialize_profiler(os.environ.get("PDFTL_PROFILE_STAGES", ""))

    def __enter__(self):
        self.start_time = time.perf_counter()
        if self.profiler:
            logger.debug("Enabling profiler")
            self.profiler.enable()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed = time.perf_counter() - self.start_time
        logger.debug("Profiler: elapsed=%s, threshold=%s", elapsed, self.threshold)
        # fast path exit: zero-overhead return if no breach and not profiling
        if not self.profiler and elapsed <= self.threshold:
            return
        if elapsed > self.threshold:
            logger.info(
                "Stage '%s' took %.2fs, which exceeds %.2fs. To profile: PDFTL_PROFILE_STAGES=1",
                self.stage_name,
                elapsed,
                self.threshold,
            )
        if not self.profiler:
            return

        self._process_breach(elapsed)

    def _initialize_profiler(self, profile_targets: str) -> None:
        """Evaluates targets and lazy loads cProfile if required."""
        if self._is_targeted(profile_targets):
            import cProfile

            self.profiler = cProfile.Profile()

    def _is_targeted(self, profile_targets: str) -> bool:
        """Checks if the current stage is flagged for active profiling."""
        if not profile_targets:
            return False
        if profile_targets.lower() in ("all", "1", "true"):
            return True

        targets = {t.strip() for t in profile_targets.split(",")}
        return self.stage_name in targets

    def _process_breach(self, elapsed: float) -> None:
        """Handles diagnostic dumps when a stage breaches thresholds or is profiled."""
        if self.profiler:
            self.profiler.disable()

        base_filename = self._prepare_output_dir()
        self._generate_reports(base_filename, elapsed)

        print(
            f"\n[PERF WARNING] Stage '{self.stage_name}' breached threshold ({elapsed:.2f}s).\n"
            f"Diagnostics saved to: {base_filename}.txt",
            file=sys.stderr,
        )

    def _prepare_output_dir(self) -> Path:
        """Creates the diagnostic output directory and returns the base filename."""
        profile_dir = Path("pdftl_profiles")
        profile_dir.mkdir(exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y_%m_%d_%H%M%S")
        return profile_dir / f"{self.stage_name}_{timestamp}"

    def _generate_reports(self, base_filename: Path, elapsed: float) -> None:
        """Coordinates writing the text reports and binary profile dumps."""
        import shlex

        fingerprints = self._fingerprint_inputs()

        with open(f"{base_filename}.txt", "w", encoding="utf-8") as f:
            self._write_report_header(f, elapsed, shlex)
            self._write_fingerprints(f, fingerprints)

            if self.profiler:
                self._write_profile_stats(f, base_filename)

    def _write_report_header(self, f, elapsed: float, shlex_mod) -> None:
        """Writes the basic CLI context to the open report file."""
        f.write(f"PERFORMANCE REPORT | Stage: '{self.stage_name}'\n")
        f.write(f"Duration: {elapsed:.4f}s (Threshold: {self.threshold}s)\n")
        f.write("=" * 70 + "\n\n")

        f.write("--- CLI CONTEXT ---\n")
        f.write(f"Full Command : {shlex_mod.join(sys.argv)}\n")
        f.write(f"Stage Args   : {shlex_mod.join(self.stage_args)}\n\n")

    def _write_fingerprints(self, f, fingerprints: dict) -> None:
        """Writes the lazily gathered file fingerprints to the report."""
        f.write("--- INPUT FILE FINGERPRINTS ---\n")
        if not fingerprints:
            f.write("No explicit input .pdf files identified in CLI arguments.\n")
            f.write("\n" + "=" * 70 + "\n\n")
            return

        for path, info in fingerprints.items():
            f.write(f"File: {path}\n  Size: {info['size']} bytes\n  SHA256: {info['hash']}\n")
        f.write("\n" + "=" * 70 + "\n\n")

    def _write_profile_stats(self, f, base_filename: Path) -> None:
        """Writes the human-readable profile table and dumps the binary stats."""
        import pstats

        # The strict filter to prevent venv path leaks
        internal_filter = "/src/pdftl/"

        # 1. OVERALL CUMULATIVE VIEW
        f.write("--- 1. OVERALL TOP CALLS (Sorted by Cumulative Time) ---\n")
        f.write("Provides a broad layout of the path overhead including dependencies.\n\n")
        ps_overall = pstats.Stats(self.profiler, stream=f).sort_stats(pstats.SortKey.CUMULATIVE)
        ps_overall.print_stats(40)
        f.write("\n" + "=" * 78 + "\n\n")

        # 2. INTERNAL LOGIC: HOTSPOTS (tottime)
        f.write("--- 2. PROJECT LOGIC: HIGHEST INTERNAL TIME (tottime) ---\n")
        f.write("Target these functions for local loop optimizations or type-checking blocks.\n\n")
        ps_tot = pstats.Stats(self.profiler, stream=f).sort_stats(pstats.SortKey.TIME)
        ps_tot.print_stats(internal_filter, 25)
        f.write("\n" + "=" * 78 + "\n\n")

        # 3. INTERNAL LOGIC: TRACE CALLERS
        f.write("--- 3. HOTSPOT TRACE: WHO IS CALLING THE HEAVIEST INTERNAL FUNCTIONS? ---\n")
        f.write("Maps the top 5 internal time-burners back to their source triggers.\n\n")
        # Re-uses the TIME sorted stats, taking the top 5 matches for our internal code
        ps_tot.print_callers(internal_filter, 5)
        f.write("\n" + "=" * 78 + "\n\n")

        # 4. GLOBAL SPAM TRACE (Catches built-ins like isinstance)
        f.write("--- 4. BUILT-IN & OVERALL SPAM TRACE (Callers of Top 5 by Time) ---\n")
        f.write("Identifies which functions are spamming built-ins or C-extensions.\n\n")
        ps_tot_overall = pstats.Stats(self.profiler, stream=f).sort_stats(pstats.SortKey.TIME)
        ps_tot_overall.print_callers(5)
        f.write("\n" + "=" * 78 + "\n\n")

        # 5. INTERNAL LOGIC: ARCHITECTURE (cumtime)
        f.write("--- 5. PROJECT LOGIC: HIGHEST CUMULATIVE TIME (cumtime) ---\n")
        f.write("Target these methods for architectural adjustments or logic bypass paths.\n\n")
        ps_cum = pstats.Stats(self.profiler, stream=f).sort_stats(pstats.SortKey.CUMULATIVE)
        ps_cum.print_stats(internal_filter, 25)
        f.write("\n" + "=" * 78 + "\n")

        # Dump the binary stats payload for external visualizers
        self.profiler.dump_stats(f"{base_filename}.prof")

    def _fingerprint_inputs(self) -> dict:
        """Scans arguments and computes hashes for valid PDF inputs."""
        fingerprints = {}
        for arg in sys.argv:
            if self._is_valid_pdf_arg(arg):
                fingerprints[arg] = self._hash_file(Path(arg))
        return fingerprints

    def _is_valid_pdf_arg(self, arg: str) -> bool:
        """Filters sys.argv to find legitimate PDF file paths."""
        if not arg.lower().endswith(".pdf"):
            return False
        if "=" in arg:
            return False
        return Path(arg).is_file()

    def _hash_file(self, path: Path) -> dict:
        """Safely hashes a file using chunking, returning size and hash metadata."""
        import hashlib

        try:
            hasher = hashlib.sha256()
            with open(path, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            return {"size": path.stat().st_size, "hash": hasher.hexdigest()[:16]}
        except OSError as e:
            return {"size": "unknown", "hash": f"error_reading_file: {str(e)}"}
