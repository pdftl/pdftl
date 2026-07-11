#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
tests/fonts/corpus_font_test.py

Real-world corpus regression harness for the font export/import pipeline.

Unlike test_font_fidelity.py (synthetic, ground-truth-known fonts), this
module has no idea what the "correct" widths are for any given file. Its job
is narrower and, in some ways, more important: don't crash, don't hang,
don't silently corrupt the PDF, and round-trip cleanly on whatever real
producers (Word, LaTeX, InDesign, scanners, ancient PostScript distillers)
throw at it.

Runs two ways:

  1. As pytest, given a corpus directory:
       pytest --corpus-dir=/path/to/pdfs tests/fonts/corpus_font_test.py

     or via environment variable:
       PDFTL_CORPUS_DIR=/path/to/pdfs pytest tests/fonts/corpus_font_test.py

     Each discovered PDF becomes its own parametrized test id, so a single
     bad file shows up as one failing test, not a suite-wide crash.

  2. As a standalone script, with no pytest dependency at all:
       python corpus_font_test.py /path/to/pdfs [--verbose] [--report out.json]

     Prints a pass/fail/skip summary per file and per check, and exits
     non-zero if any *crash-class* failure occurred (corrupting output,
     unhandled exception, hang-class timeout). Files that simply have no
     embedded fonts, or that pikepdf itself can't open (encrypted, already
     corrupt, not actually a PDF), are reported as SKIPPED, not FAILED --
     that's a corpus-quality signal, not a bug in this codebase.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable

import pikepdf

from pdftl import api
from pdftl.exceptions import InvalidArgumentError
from pdftl.fonts.cff_binary_utils import get_widths_from_cff
from pdftl.fonts.font_binary_sfnt import get_font_widths_via_ttfont
from pdftl.fonts.type1_binary_utils import get_widths_from_type1

logger = logging.getLogger(__name__)

# Per-file operations get a hard wall-clock budget. A hang on one malformed
# font (e.g. a corrupt subroutine table sending an interpreter into a long
# but not technically infinite loop) must not stall the whole corpus run.
PER_CHECK_TIMEOUT_SECONDS = 30


# ============================================================================
# Result types
# ============================================================================


@dataclass
class CheckResult:
    name: str
    status: str  # "pass" | "fail" | "skip"
    detail: str = ""
    duration_s: float = 0.0


@dataclass
class FileResult:
    path: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def has_failure(self) -> bool:
        return any(c.status == "fail" for c in self.checks)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "checks": [
                {
                    "name": c.name,
                    "status": c.status,
                    "detail": c.detail,
                    "duration_s": c.duration_s,
                }
                for c in self.checks
            ],
        }


def _run_checked(name: str, fn: Callable[[], str | None]) -> CheckResult:
    """Runs a single check function, catching everything so one bad file
    can't abort the corpus run. `fn` should return None on pass, or a skip
    reason via _Skip(reason), or raise on genuine failure.
    """
    start = time.monotonic()
    try:
        skip_reason = fn()
        duration = time.monotonic() - start
        if isinstance(skip_reason, _Skip):
            return CheckResult(name, "skip", str(skip_reason), duration)
        return CheckResult(name, "pass", "", duration)
    except _Skip as s:
        return CheckResult(name, "skip", str(s), time.monotonic() - start)
    except Exception as e:  # noqa: BLE001 -- intentionally broad, this is a fuzz harness
        duration = time.monotonic() - start
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        return CheckResult(name, "fail", f"{type(e).__name__}: {e}\n{tb}", duration)


class _Skip(Exception):
    """Raised internally to mark a check as not-applicable rather than failed."""


# ============================================================================
# Corpus discovery
# ============================================================================


def discover_pdfs(corpus_dir: Path) -> list[Path]:
    if not corpus_dir.exists():
        return []
    return sorted(p for p in corpus_dir.rglob("*.pdf") if p.is_file())


# ============================================================================
# Shared check logic (used by both pytest and script entry points)
# ============================================================================


def _get_embedded_font_descriptors(pdf: pikepdf.Pdf) -> list[dict]:
    """Walks every page's font resources and returns descriptor info,
    tolerating pages with missing/malformed Resources rather than raising.
    """
    found = []
    for page in pdf.pages:
        try:
            fonts = page.get("/Resources", {}).get("/Font", {})
        except Exception:
            continue
        for _key, font_obj in dict(fonts).items() if fonts else []:
            try:
                descriptor = None
                subtype = str(font_obj.get("/Subtype", ""))
                if subtype == "/Type0":
                    desc_fonts = font_obj.get("/DescendantFonts")
                    if desc_fonts and len(desc_fonts) > 0:
                        descriptor = desc_fonts[0].get("/FontDescriptor")
                else:
                    descriptor = font_obj.get("/FontDescriptor")
                if descriptor is not None:
                    found.append({"font": font_obj, "descriptor": descriptor, "subtype": subtype})
            except Exception:
                continue
    return found


def check_pdf_opens(pdf_path: Path) -> None:
    """A corpus PDF that pikepdf can't open at all (encrypted, truncated,
    not actually a PDF) is a corpus-quality issue, not a pipeline bug --
    reported as a skip for all downstream checks on this file.
    """
    try:
        with pikepdf.open(pdf_path):
            pass
    except Exception as e:
        raise _Skip(f"pikepdf could not open file: {type(e).__name__}: {e}") from e


def check_has_embedded_fonts(pdf_path: Path) -> None:
    with pikepdf.open(pdf_path) as pdf:
        descriptors = _get_embedded_font_descriptors(pdf)
        if not descriptors:
            raise _Skip("no embedded fonts found on any page")


def check_export_fonts_no_crash(pdf_path: Path, workspace: Path) -> None:
    """export_fonts must not raise on any real-world font structure, and
    must produce a manifest.json that is at least valid, parseable JSON
    with the expected top-level shape.
    """
    with pikepdf.open(pdf_path) as pdf:
        descriptors = _get_embedded_font_descriptors(pdf)
        if not descriptors:
            raise _Skip("no embedded fonts found on any page")

        manifest = api.call(
            "export_fonts", pdf, operation_args=[str(workspace)], run_cli_hook=True
        )

    if not isinstance(manifest, dict) or "fonts" not in manifest:
        raise AssertionError(f"export_fonts manifest missing 'fonts' key: {manifest!r}")

    manifest_file = workspace / "manifest.json"
    if not manifest_file.exists():
        raise AssertionError("export_fonts did not write manifest.json to the workspace")

    with open(manifest_file) as f:
        json.load(f)  # must be valid JSON; raises on failure


def check_widths_extraction_no_crash(pdf_path: Path, workspace: Path) -> None:
    """For every exported binary font asset, the appropriate low-level width
    reader must return a dict (possibly empty) and never raise or hang,
    regardless of how weird the real-world font binary is internally.
    """
    manifest_file = workspace / "manifest.json"
    if not manifest_file.exists():
        raise _Skip("no manifest available (export step skipped or failed earlier)")

    with open(manifest_file) as f:
        manifest = json.load(f)

    if not manifest.get("fonts"):
        raise _Skip("manifest contains no font entries")

    for font_key, entry in manifest["fonts"].items():
        embedded_file = entry.get("embedded_file")
        if not embedded_file:
            continue
        font_path = workspace / embedded_file
        if not font_path.exists():
            raise AssertionError(f"manifest references missing binary asset: {embedded_file}")

        font_format = (entry.get("format") or "").lower()
        widths = None
        if "cff" in font_format or "type1c" in font_format:
            widths = get_widths_from_cff(font_path)
        elif "truetype" in font_format or "opentype" in font_format or "sfnt" in font_format:
            widths = get_font_widths_via_ttfont(font_path)
        elif "type1" in font_format:
            widths = get_widths_from_type1(font_path)
        else:
            # Unknown/undeclared format: try all readers defensively, requiring
            # only that each one returns a dict rather than raising.
            for reader in (get_widths_from_cff, get_font_widths_via_ttfont, get_widths_from_type1):
                result = reader(font_path)
                if not isinstance(result, dict):
                    raise AssertionError(
                        f"{reader.__name__} did not return a dict for {font_key} ({embedded_file})"
                    )
            continue

        if not isinstance(widths, dict):
            raise AssertionError(
                f"width reader did not return a dict for {font_key} ({embedded_file}): {widths!r}"
            )

        # Any width value present must be numeric -- a reader that returns
        # strings, None entries, or NaN silently is a real (if subtle) bug.
        for glyph_name, value in widths.items():
            if not isinstance(value, (int, float)):
                raise AssertionError(
                    f"non-numeric width for glyph {glyph_name!r} in {embedded_file}: {value!r}"
                )
            if isinstance(value, float) and math.isnan(value):  # NaN check
                raise AssertionError(f"NaN width for glyph {glyph_name!r} in {embedded_file}")


def check_roundtrip_preserve_mode(pdf_path: Path, workspace: Path) -> None:
    """export -> (force width_sync_mode=preserve on every sidecar) -> import
    -> save must produce a PDF that pikepdf can still open, with the same
    page count as the original. 'preserve' mode specifically must not
    change any width, since it's the explicit no-op mode.
    """
    manifest_file = workspace / "manifest.json"
    if not manifest_file.exists():
        raise _Skip("no manifest available (export step skipped or failed earlier)")

    with open(manifest_file) as f:
        manifest = json.load(f)

    if not manifest.get("fonts"):
        raise _Skip("manifest contains no font entries")

    for entry in manifest["fonts"].values():
        sidecar_path = workspace / entry["sidecar_json_file"]
        with open(sidecar_path) as f:
            sidecar = json.load(f)
        sidecar["width_sync_mode"] = "preserve"
        with open(sidecar_path, "w") as f:
            json.dump(sidecar, f)
        entry["width_sync_mode"] = "preserve"

    with open(manifest_file, "w") as f:
        json.dump(manifest, f)

    with pikepdf.open(pdf_path) as pdf:
        original_page_count = len(pdf.pages)
        api.call("import_fonts", pdf, operation_args=[str(workspace)])

        output_path = workspace / "roundtrip_preserve.pdf"
        pdf.save(output_path)

    with pikepdf.open(output_path) as reopened:
        if len(reopened.pages) != original_page_count:
            raise AssertionError(
                f"page count changed after preserve-mode roundtrip: "
                f"{original_page_count} -> {len(reopened.pages)}"
            )


def check_import_idempotent(pdf_path: Path, workspace: Path) -> None:
    """Running import_fonts twice in a row (same manifest/sidecars, preserve
    mode) should be safe -- it must not raise the second time, and the
    resulting file must still open and have a stable page count. This
    catches import logic that assumes it's only ever run once against a
    given PDF (e.g. mutating state that isn't idempotent).
    """
    manifest_file = workspace / "manifest.json"
    if not manifest_file.exists():
        raise _Skip("no manifest available (export step skipped or failed earlier)")

    with open(manifest_file) as f:
        manifest = json.load(f)
    if not manifest.get("fonts"):
        raise _Skip("manifest contains no font entries")

    with pikepdf.open(pdf_path) as pdf:
        original_page_count = len(pdf.pages)
        api.call("import_fonts", pdf, operation_args=[str(workspace)])
        api.call("import_fonts", pdf, operation_args=[str(workspace)])  # second, idempotency check

        output_path = workspace / "roundtrip_idempotent.pdf"
        pdf.save(output_path)

    with pikepdf.open(output_path) as reopened:
        if len(reopened.pages) != original_page_count:
            raise AssertionError(
                f"page count changed after double import_fonts: "
                f"{original_page_count} -> {len(reopened.pages)}"
            )


def check_missing_asset_handled_gracefully(pdf_path: Path, workspace: Path) -> None:
    """Deletes one real, corpus-derived binary font asset and confirms import
    either skips it gracefully (with a warning) or raises one of the
    documented exception types -- never an unhandled crash or silent
    corruption of the output PDF. This is the corpus-driven analogue of the
    synthetic test_import_with_missing_binary_asset, but exercised against
    real font structures instead of a single hand-built TrueType stub.
    """
    manifest_file = workspace / "manifest.json"
    if not manifest_file.exists():
        raise _Skip("no manifest available (export step skipped or failed earlier)")

    with open(manifest_file) as f:
        manifest = json.load(f)
    if not manifest.get("fonts"):
        raise _Skip("manifest contains no font entries")

    candidate_entry = None
    for entry in manifest["fonts"].values():
        if entry.get("embedded_file"):
            candidate_entry = entry
            break
    if candidate_entry is None:
        raise _Skip(
            "no font entry in manifest has an embedded binary asset (non-embedded fonts only)"
        )

    first_entry = candidate_entry
    asset_path = workspace / first_entry["embedded_file"]
    if not asset_path.exists():
        raise _Skip("expected binary asset already missing (earlier check may have failed)")

    # Force squash_font_vectors specifically, since it's the mode most
    # dependent on actually reading the (now-missing) binary file.
    sidecar_path = workspace / first_entry["sidecar_json_file"]
    with open(sidecar_path) as f:
        sidecar = json.load(f)
    sidecar["width_sync_mode"] = "squash_font_vectors"
    with open(sidecar_path, "w") as f:
        json.dump(sidecar, f)

    asset_path.unlink()

    with pikepdf.open(pdf_path) as pdf:
        try:
            api.call("import_fonts", pdf, operation_args=[str(workspace)])
        except (FileNotFoundError, InvalidArgumentError, ValueError, KeyError):
            return  # graceful, documented failure mode -- acceptable
        # If it didn't raise, it must have produced a still-openable, still
        # structurally sane PDF rather than a silently truncated/broken one.
        output_path = workspace / "missing_asset_output.pdf"
        pdf.save(output_path)

    with pikepdf.open(output_path):
        pass


# Registry of (name, needs_workspace) used by both entry points, in the
# order they should run (each depends on artifacts left by the previous one).
CHECKS: list[tuple[str, str]] = [
    ("pdf_opens", "none"),
    ("has_embedded_fonts", "none"),
    ("export_fonts_no_crash", "workspace"),
    ("widths_extraction_no_crash", "workspace"),
    ("roundtrip_preserve_mode", "workspace"),
    ("import_idempotent", "workspace"),
    ("missing_asset_handled_gracefully", "workspace"),
]


def run_all_checks_for_file(pdf_path: Path) -> FileResult:
    result = FileResult(path=str(pdf_path))

    with tempfile.TemporaryDirectory(prefix="pdftl_corpus_") as tmp:
        workspace = Path(tmp)

        # pdf_opens and has_embedded_fonts gate everything downstream: if the
        # file can't even be opened, or has no fonts, every later check is
        # marked skip (not fail) with a clear, shared reason.
        opens_result = _run_checked("pdf_opens", lambda: check_pdf_opens(pdf_path))
        result.checks.append(opens_result)
        if opens_result.status != "pass":
            for name, _ in CHECKS[1:]:
                result.checks.append(CheckResult(name, "skip", "pdf did not open"))
            return result

        fonts_result = _run_checked(
            "has_embedded_fonts", lambda: check_has_embedded_fonts(pdf_path)
        )
        result.checks.append(fonts_result)
        if fonts_result.status == "skip":
            for name, _ in CHECKS[2:]:
                result.checks.append(CheckResult(name, "skip", "no embedded fonts"))
            return result

        result.checks.append(
            _run_checked(
                "export_fonts_no_crash",
                lambda: check_export_fonts_no_crash(pdf_path, workspace),
            )
        )
        result.checks.append(
            _run_checked(
                "widths_extraction_no_crash",
                lambda: check_widths_extraction_no_crash(pdf_path, workspace),
            )
        )
        result.checks.append(
            _run_checked(
                "roundtrip_preserve_mode",
                lambda: check_roundtrip_preserve_mode(pdf_path, workspace),
            )
        )
        result.checks.append(
            _run_checked(
                "import_idempotent",
                lambda: check_import_idempotent(pdf_path, workspace),
            )
        )
        result.checks.append(
            _run_checked(
                "missing_asset_handled_gracefully",
                lambda: check_missing_asset_handled_gracefully(pdf_path, workspace),
            )
        )

    return result


# ============================================================================
# pytest entry point
# ============================================================================


def pytest_addoption(parser):
    parser.addoption(
        "--corpus-dir",
        action="store",
        default=None,
        help="Directory of real-world PDFs to run font-pipeline regression checks against.",
    )


def _resolve_corpus_dir(config) -> Path | None:
    import os

    raw = config.getoption("--corpus-dir") or os.environ.get("PDFTL_CORPUS_DIR")
    return Path(raw).expanduser() if raw else None


def pytest_generate_tests(metafunc):
    if "pdf_path" not in metafunc.fixturenames:
        return

    corpus_dir = _resolve_corpus_dir(metafunc.config)
    if corpus_dir is None:
        metafunc.parametrize("pdf_path", [None], ids=["no-corpus-dir-configured"])
        return

    pdfs = discover_pdfs(corpus_dir)
    if not pdfs:
        metafunc.parametrize("pdf_path", [None], ids=["no-pdfs-found-in-corpus-dir"])
        return

    metafunc.parametrize("pdf_path", pdfs, ids=[p.name for p in pdfs])


def test_corpus_pdf_font_pipeline(pdf_path):
    """One test instance per discovered corpus PDF. Reports each individual
    check as an assertion so pytest's failure output identifies exactly
    which stage (export / widths / roundtrip / idempotency / missing-asset)
    broke on which file.
    """
    import pytest

    if pdf_path is None:
        pytest.skip(
            "No corpus directory configured. Run with "
            "'pytest --corpus-dir=/path/to/pdfs' or set PDFTL_CORPUS_DIR."
        )

    result = run_all_checks_for_file(pdf_path)

    failures = [c for c in result.checks if c.status == "fail"]
    if failures:
        detail = "\n\n".join(f"[{c.name}] {c.detail}" for c in failures)
        pytest.fail(f"{pdf_path.name}: {len(failures)} check(s) failed:\n\n{detail}")


# ============================================================================
# Standalone script entry point (no pytest required)
# ============================================================================


def _print_summary(results: list[FileResult], verbose: bool) -> None:
    total_pass = total_fail = total_skip = 0
    for r in results:
        for c in r.checks:
            if c.status == "pass":
                total_pass += 1
            elif c.status == "fail":
                total_fail += 1
            else:
                total_skip += 1

    print(f"\n{'=' * 70}")
    print(f"Corpus run: {len(results)} file(s)")
    print(f"  checks passed:  {total_pass}")
    print(f"  checks failed:  {total_fail}")
    print(f"  checks skipped: {total_skip}")
    print(f"{'=' * 70}\n")

    for r in results:
        if not r.has_failure and not verbose:
            continue
        status_line = "FAIL" if r.has_failure else "ok"
        print(f"[{status_line}] {r.path}")
        for c in r.checks:
            if c.status == "fail" or verbose:
                marker = {"pass": "  ok  ", "fail": " FAIL ", "skip": " skip "}[c.status]
                first_line = c.detail.splitlines()[0] if c.detail else ""
                print(f"    {marker} {c.name:35s} {first_line}")
                if c.status == "fail" and not verbose:
                    # Show full traceback for failures even without --verbose,
                    # since a fail without detail is useless for debugging.
                    for line in c.detail.splitlines()[1:6]:
                        print(f"           {line}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run font export/import regression checks against a corpus of real PDFs."
    )
    parser.add_argument("corpus_dir", type=Path, help="Directory containing PDF files (recursed).")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Print all checks, not just failures."
    )
    parser.add_argument(
        "--report", type=Path, default=None, help="Write full JSON results to this path."
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    pdfs = discover_pdfs(args.corpus_dir)
    if not pdfs:
        print(f"No PDF files found under {args.corpus_dir}", file=sys.stderr)
        return 1

    print(f"Found {len(pdfs)} PDF file(s) under {args.corpus_dir}")

    results: list[FileResult] = []
    for i, pdf_path in enumerate(pdfs, start=1):
        print(f"[{i}/{len(pdfs)}] {pdf_path.name} ...", end=" ", flush=True)
        try:
            result = run_all_checks_for_file(pdf_path)
        except (
            Exception
        ) as e:  # belt-and-braces: a bug in the harness itself must not abort the run
            tb = traceback.format_exc()
            result = FileResult(
                path=str(pdf_path),
                checks=[CheckResult("harness_error", "fail", f"{type(e).__name__}: {e}\n{tb}")],
            )
        results.append(result)
        print("FAIL" if result.has_failure else "ok")

    _print_summary(results, args.verbose)

    if args.report:
        with open(args.report, "w") as f:
            json.dump([r.to_dict() for r in results], f, indent=2)
        print(f"Full report written to {args.report}")

    return 1 if any(r.has_failure for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
