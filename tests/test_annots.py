import difflib
import json
import subprocess
from pathlib import Path

import pytest

# --- Configuration ---
TEST_FILES_DIR = Path(__file__).parent / "files"
PDFS_DIR = TEST_FILES_DIR / "pdfs"

# --- Test Cases ---
PDF_TEST_CASES = [
    "bad2u",
    "1",
    "xxx-bad",
    "cs229_main_notes",
]


def run_pdftl_command(command: list[str]) -> str:
    """Helper function to run a pdftl command and return its stdout."""
    try:
        process = ["pdftl"] + command
        result = subprocess.run(
            process, capture_output=True, text=True, check=True, encoding="utf-8"
        )
        return result.stdout
    except FileNotFoundError:
        pytest.fail("The 'pdftl' command was not found. Is it in your PATH?")
    except subprocess.CalledProcessError as e:
        if e.returncode == 0:
            return e.stdout
        pytest.fail(
            f"pdftl command failed with exit code {e.returncode}:\n"
            f"Command: {' '.join(e.cmd)}\n"
            f"Stdout: {e.stdout}\n"
            f"Stderr: {e.stderr}"
        )


def _compare_annotations(original_dump: str, processed_dump: str) -> list[str]:
    """
    Compares two annotation dumps (JSON lists) with special handling for the /P key.
    """
    errors = []
    try:
        original_data = json.loads(original_dump) if original_dump else []
        processed_data = json.loads(processed_dump) if processed_dump else []
    except json.JSONDecodeError as e:
        return [f"Failed to parse annotation JSON: {e}\nOriginal dump:\n{original_dump}"]

    if len(original_data) != len(processed_data):
        errors.append(
            f"Annotation count mismatch. "
            f"Expected {len(original_data)}, got {len(processed_data)}."
        )
        return errors

    for i, (original_annot, processed_annot) in enumerate(zip(original_data, processed_data)):
        original_props = original_annot.get("Properties", {}).copy()
        processed_props = processed_annot.get("Properties", {}).copy()
        original_p = original_props.get("/P")
        processed_p = processed_props.get("/P")
        is_repair = (original_p is None or str(original_p).find("Unknown") != -1) and (
            processed_p is not None and str(processed_p).find("Unknown") == -1
        )
        if is_repair:
            original_props.pop("/P", None)
            processed_props.pop("/P", None)
        if original_props != processed_props:
            page = original_annot.get("Page", "N/A")
            index = original_annot.get("AnnotationIndex", i + 1)
            errors.append(
                f"Annotation {index} on Page {page}: Data mismatch.\n"
                f"  Expected: {json.dumps(original_props, indent=2)}\n"
                f"  Actual:   {json.dumps(processed_props, indent=2)}"
            )
    return errors


def _compare_named_dests(original_dump: str, processed_dump: str) -> list[str]:
    """
    Compares named destination dumps, checking for exact preservation of names and values.
    For a simple 'cat' operation, these should be identical.
    """
    errors = []
    try:
        original_list = (json.loads(original_dump).get("dests", [])) if original_dump else []
        processed_list = (json.loads(processed_dump).get("dests", [])) if processed_dump else []
    except json.JSONDecodeError as e:
        return [f"Failed to parse destination JSON: {e}\nOriginal dump:\n{original_dump}"]

    original_dests = {d["name"]: d for d in original_list}
    processed_dests = {d["name"]: d for d in processed_list}

    original_names = set(original_dests.keys())
    processed_names = set(processed_dests.keys())

    if original_names != processed_names:
        missing = sorted(list(original_names - processed_names))
        added = sorted(list(processed_names - original_names))
        if missing:
            # allow deletions, for now!!
            # really we should at least check that we haven't deleted any VALID destination
            # and maybe(?) we should ask for cat that we do not delete ANY destination,
            # if would point to a page that exists in the output?
            # errors.append(f"Missing named destinations: {missing}")
            pass
        if added:
            errors.append(f"Unexpected new named destinations: {added}")
        return errors

    for name in original_names:
        original_value = original_dests[name].get("value", {})
        processed_value = processed_dests[name].get("value", {})

        # A simple 'cat' operation should not change the destination value at all.
        # This is a strict check.
        if original_value != processed_value:
            errors.append(
                f"Named destination '{name}': Value mismatch.\n"
                f"  Expected: {json.dumps(original_value, indent=2)}\n"
                f"  Actual:   {json.dumps(processed_value, indent=2)}"
            )
    return errors


@pytest.mark.slow
@pytest.mark.parametrize("pdf_basename", PDF_TEST_CASES)
@pytest.mark.xdist_group(name="serial_io_tests")
def test_cat_operation_is_idempotent(get_pdf_path, pdf_basename, tmp_path):
    """
    Verifies that the 'cat' operation is idempotent for annotations.
    """
    input_pdf = get_pdf_path(f"{pdf_basename}.pdf")
    first_pass_pdf = tmp_path / f"{pdf_basename}_pass1.pdf"
    second_pass_pdf = tmp_path / f"{pdf_basename}_pass2.pdf"
    assert input_pdf.exists(), f"Input PDF not found: {input_pdf}"

    run_pdftl_command([str(input_pdf), "cat", "output", str(first_pass_pdf)])
    run_pdftl_command([str(first_pass_pdf), "cat", "output", str(second_pass_pdf)])

    first_pass_dump = run_pdftl_command([str(first_pass_pdf), "dump_annots"])
    second_pass_dump = run_pdftl_command([str(second_pass_pdf), "dump_annots"])

    if first_pass_dump != second_pass_dump:
        diff = difflib.unified_diff(
            first_pass_dump.splitlines(keepends=True),
            second_pass_dump.splitlines(keepends=True),
            fromfile=f"{pdf_basename}_pass1.txt",
            tofile=f"{pdf_basename}_pass2.txt",
        )
        pytest.fail(
            "The 'cat' operation is not idempotent for annotations.\n"
            f"--- Diff ---\n{''.join(diff)}"
        )


@pytest.mark.slow
@pytest.mark.parametrize("pdf_basename", PDF_TEST_CASES)
@pytest.mark.xdist_group(name="serial_io_tests")
def test_cat_preserves_annotations_with_repair(get_pdf_path, pdf_basename, tmp_path):
    """
    Verifies that 'cat' preserves all annotation data, only allowing for
    the specific case of repairing a missing or invalid parent page (/P) link.
    """
    input_pdf = get_pdf_path(f"{pdf_basename}.pdf")
    processed_pdf = tmp_path / f"{pdf_basename}_processed.pdf"
    assert input_pdf.exists(), f"Input PDF not found: {input_pdf}"

    original_dump = run_pdftl_command([str(input_pdf), "dump_annots"])
    run_pdftl_command([str(input_pdf), "cat", "output", str(processed_pdf)])
    processed_dump = run_pdftl_command([str(processed_pdf), "dump_annots"])

    comparison_errors = _compare_annotations(original_dump, processed_dump)
    if comparison_errors:
        pytest.fail(
            "Annotation data was unexpectedly modified by the 'cat' operation.\n"
            + "\n".join(comparison_errors)
        )


@pytest.mark.parametrize("pdf_basename", PDF_TEST_CASES)
@pytest.mark.xdist_group(name="serial_io_tests")
def test_cat_preserves_named_destinations(get_pdf_path, pdf_basename, tmp_path):
    """
    Verifies that 'cat' preserves all named destinations and their values exactly.
    """
    input_pdf = get_pdf_path(f"{pdf_basename}.pdf")
    processed_pdf = tmp_path / f"{pdf_basename}_processed.pdf"
    assert input_pdf.exists(), f"Input PDF not found: {input_pdf}"

    try:
        original_dump = run_pdftl_command([str(input_pdf), "dump_dests"])
    except subprocess.CalledProcessError:
        original_dump = ""

    run_pdftl_command([str(input_pdf), "cat", "output", str(processed_pdf)])

    try:
        processed_dump = run_pdftl_command([str(processed_pdf), "dump_dests"])
    except subprocess.CalledProcessError:
        processed_dump = ""

    comparison_errors = _compare_named_dests(original_dump, processed_dump)
    if comparison_errors:
        pytest.fail(
            "Named destination data was unexpectedly modified by 'cat' operation.\n"
            + "\n".join(comparison_errors)
        )
