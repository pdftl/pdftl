# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/test_examples.py

import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

# Import the CLI_DATA dictionary that contains all the examples
from pdftl.core.registry import registry
from pdftl.registry_init import initialize_registry

initialize_registry()

# --- Test Setup: Create Dummy PDF Files ---


# --- Test Generation: Discover and Parameterize Examples ---


def discover_examples():
    all_examples = []

    def _add_or_omit(example, test_id):
        cmd = example.get("cmd", None)
        if not cmd or "PROMPT" in cmd or not example.get("test_example", True):
            return
        all_examples.append(pytest.param(cmd, id=test_id))

    sources = [
        registry.operations,
        registry.help_topics,
        registry.options,
    ]
    for collection in sources:
        for name, data in collection.items():
            for i, example in enumerate(data.get("examples", [])):
                _add_or_omit(example, f"{name}-example{i + 1}")

    return all_examples


# --- The Main Test Function ---


@pytest.mark.slow
@pytest.mark.serial
@pytest.mark.parametrize("command_str", discover_examples())
def test_example_command(command_str, dummy_pdfs, tmp_path, assets_dir):
    """
    Tests all example commands discovered from CLI_DATA in a fully isolated environment.
    """
    # --- Step 0: Setup Isolation ---
    # Create a completely fresh workspace for this specific test run

    examples_files_dir = tmp_path / ".." / "example_filescurrent"

    work_dir = tmp_path / "work_area"
    work_dir.mkdir()

    # Copy all fixture PDFs into the clean workspace
    # This prevents tests from corrupting shared files (the "Form.pdf" crash)
    for filename, source_path in dummy_pdfs.items():
        shutil.copy(source_path, work_dir / filename)

    # Copy extra assets if they exist
    for filename in [
        "meta.txt",
        "bookmarks.yaml",
        "bookmarks.json",
        "Form.pdf",
        "streams.txt",
        "patched_streams.txt",
    ]:
        if (assets_dir / filename).exists():
            shutil.copy(assets_dir / filename, work_dir / filename)

    for filename in ["background.jpg", "watermark.png", "logo.png"]:
        if (examples_files_dir / filename).exists():
            shutil.copy(examples_files_dir / filename, work_dir / filename)
        else:
            breakpoint()

    # --- Step 1: Prepare Arguments ---
    args = shlex.split(command_str)
    processed_args = []

    # Track output location for assertions later
    output_target = None
    is_template = False

    # Iterate through args to fix file paths
    i = 0
    while i < len(args):
        arg = args[i]

        # Handle 'output' keyword specially
        if arg.lower() == "output":
            processed_args.append(arg)
            # The next argument is the output path
            if i + 1 < len(args):
                out_arg = args[i + 1]

                if "%" in out_arg:
                    # It's a template (e.g., 'page_%d.pdf')
                    # We want the output in work_dir, but we pass just the filename
                    # because we will run subprocess with cwd=work_dir
                    processed_args.append(out_arg)
                    output_target = work_dir / Path(out_arg).name  # For assertion only
                    is_template = True
                else:
                    # It's a standard file path
                    # We pass just the filename to let the tool write to CWD
                    processed_args.append(Path(out_arg).name)
                    output_target = work_dir / Path(out_arg).name

                i += 2  # Skip the next arg since we handled it
                continue

        # Handle Input Files
        # If the argument matches a known dummy file, pass ONLY the filename.
        # Since we run in cwd=work_dir, the tool will find the local copy.
        if arg in dummy_pdfs:
            processed_args.append(arg)  # Just "a.pdf", not "/tmp/.../a.pdf"

        elif "=" in arg and arg.split("=")[1] in dummy_pdfs:
            # Handle "source=a.pdf" format
            handle, filename = arg.split("=", 1)
            processed_args.append(f"{handle}={filename}")

        else:
            # Pass through other flags/args unchanged
            processed_args.append(arg)

        i += 1

    # Some commands (like unpack_files) expect a tmp/ subdirectory
    (work_dir / "tmp").mkdir(parents=True, exist_ok=True)

    # --- Step 2: Locate Executable ---
    pdftl_executable = shutil.which("pdftl")
    if not pdftl_executable:
        pytest.fail("Could not find 'pdftl'. Ensure you ran 'pip install -e .'")

    command_to_run = [pdftl_executable] + processed_args

    # Debug helper: Save what we are about to run
    with open(tmp_path / "debug_command.txt", "w") as f:
        f.write(f"CWD: {work_dir}\nCMD: {command_to_run}")

    # --- Step 3: Run the Command ---
    # CRITICAL: We run inside work_dir. The tool sees "a.pdf" and finds it locally.
    result = subprocess.run(command_to_run, capture_output=True, text=False, cwd=work_dir)

    if result.returncode != 0:
        stderr_str = result.stderr.decode("utf-8", errors="replace")

        # Intercept your custom framework message from ensure_dependencies/shutil.which
        if "was not found on your PATH" in stderr_str:
            pytest.skip(
                "Skipping test because a required system binary is missing: " + stderr_str.strip()
            )

    # --- Step 4: Assert Success ---
    assert result.returncode == 0, (
        f"Command failed with exit code {result.returncode}.\n"
        f"Command: {' '.join(command_to_run)}\n"
        f"CWD: {work_dir}\n"
        f"Stderr: {result.stderr}\n"
        f"Stdout: {result.stdout}"
    )

    # --- Step 5: Verify Output ---
    if output_target and output_target.parts[-1] != "-":
        if is_template:
            # Check for the first file of the sequence (e.g., page_1.pdf)
            # We reconstruct the likely path using the python string format
            expected_first = work_dir / (Path(output_target).name % 1)
            assert expected_first.exists(), f"Burst output missing: {expected_first}"
            assert expected_first.stat().st_size > 0, "Burst output file is empty"
        else:
            assert output_target.exists(), f"Output missing: {output_target}"
            if output_target.is_file():
                assert output_target.stat().st_size > 0, f"Output file is empty: {output_target}"
