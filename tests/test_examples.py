# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/test_examples.py

import shlex
import shutil
import subprocess
from pathlib import Path

import pikepdf
import pikepdf.form
import pytest

# Import the CLI_DATA dictionary that contains all the examples
from pdftl.core.cli_data import CLI_DATA
from pdftl.core.registry import registry
from pdftl.registry_init import initialize_registry
from tests.create_pdf import create_custom_pdf

initialize_registry()

# --- Test Setup: Create Dummy PDF Files ---


@pytest.fixture(scope="module")
def dummy_pdfs(tmp_path_factory, assets_dir):
    """
    A pytest fixture that creates a set of dummy PDF files with enough pages
    to satisfy all example commands.
    """
    tmp_path = tmp_path_factory.mktemp("example_files")

    # Create a main PDF with plenty of pages (e.g., 20)
    main_pdf_path = tmp_path / "main_20_page.pdf"
    main_pdf = create_custom_pdf(main_pdf_path, pages=20)
    # pikepdf.Pdf.new()
    # for _ in range(20):
    #     main_pdf.add_blank_page()
    # main_pdf.save(main_pdf_path)

    # Create a smaller PDF for overlays, stamps, etc.
    overlay_pdf = pikepdf.Pdf.new()
    for _ in range(5):
        overlay_pdf.add_blank_page()
    overlay_pdf_path = tmp_path / "overlay_5_page.pdf"
    overlay_pdf.save(overlay_pdf_path)

    # --- Create symlinks for all placeholder names used in examples ---
    placeholder_names = {
        "a.pdf",
        "b.pdf",
        "c.pdf",
        "doc1.pdf",
        "doc2.pdf",
        "in.pdf",
        "cover.pdf",
        "body.pdf",
        "index.pdf",
        "my.pdf",
        "main.pdf",
        "watermark.pdf",
        "overlay.pdf",
        "letterhead.pdf",
        "bgs.pdf",
        "stamps.pdf",
        "signatures.pdf",
        "contract.pdf",
        "doc_A.pdf",
        "doc_B.pdf",
        "twopagetest.pdf",
    }

    paths = {}
    for name in placeholder_names:
        # Point overlay-like files to the smaller PDF, everything else to the main one.
        is_overlay_type = any(
            keyword in name
            for keyword in [
                "watermark",
                "overlay",
                "letterhead",
                "stamp",
                "signature",
                "bg",
            ]
        )

        target_pdf = overlay_pdf_path if is_overlay_type else main_pdf_path

        link_path = tmp_path / name
        if not link_path.exists():
            link_path.symlink_to(target_pdf)
        paths[name] = link_path

    # 1. Ensure meta.txt is copied to the test working directory
    shutil.copy(assets_dir / "meta.txt", tmp_path / "meta.txt")

    # 2. Ensure Form.pdf is copied to the test working directory
    shutil.copy(assets_dir / "Form.pdf", tmp_path / "Form.pdf")
    return paths


# --- Test Generation: Discover and Parameterize Examples ---


def discover_examples():
    """
    Finds all example commands in CLI_DATA, including operation examples
    and pipeline examples, and yields them for pytest.
    Omit any with PROMPT, for now.
    """
    all_examples = []

    # 1. Discover examples from each operation
    for op_name, op_data in registry.operations.items():
        examples = op_data.get("examples", [])
        if not examples and "example" in op_data:  # Fallback for single example
            examples = [{"cmd": op_data["example"], "desc": ""}]

        for i, example in enumerate(examples):
            if (cmd := example.get("cmd")) and "PROMPT" not in cmd:
                test_id = f"{op_name}-example{i+1}"
                all_examples.append(pytest.param(cmd, id=test_id))

    # 2. Discover examples from the dedicated pipeline help section
    # if "pipeline_help" in CLI_DATA and "examples" in CLI_DATA["pipeline_help"]:
    for topic_name, topic in CLI_DATA["extra help topics"].items():
        if "examples" in topic:
            for i, example in enumerate(topic["examples"]):
                if example.get("cmd"):
                    test_id = f"{topic_name}-example{i+1}"
                    all_examples.append(pytest.param(example["cmd"], id=test_id))

    for topic_name, topic in registry["options"].items():
        if "examples" in topic:
            for i, example in enumerate(topic["examples"]):
                if example.get("cmd"):
                    test_id = f"{topic_name}-example{i+1}"
                    all_examples.append(pytest.param(example["cmd"], id=test_id))

    return all_examples


# --- The Main Test Function ---


@pytest.mark.serial
@pytest.mark.parametrize("command_str", discover_examples())
def test_example_command(command_str, dummy_pdfs, tmp_path):
    """
    This single function tests all example commands discovered from CLI_DATA.
    """
    args = shlex.split(command_str)
    with open(tmp_path / "args.txt", "w") as f:
        f.write("\n".join(args))

    output_file = None
    output_template = None

    # --- Step 1: Prepare arguments ---
    processed_args = []
    # Find the 'output' keyword to determine where to redirect output files
    try:
        # Find last occurrence of 'output' in case it's in specs
        output_indices = [i for i, arg in enumerate(args) if arg.lower() == "output"]
        output_index = output_indices[-1] if output_indices else -1

        if output_index != -1:
            output_arg = args[output_index + 1]

            if "%" in output_arg:
                output_template = str(tmp_path / Path(output_arg).name)
                args[output_index + 1] = output_template
            else:
                output_file = tmp_path / Path(output_arg).name
                args[output_index + 1] = str(output_file)

    except IndexError:
        # This command might have 'output' as the very last word with no file
        pytest.fail(f"Malformed 'output' argument in command: {command_str}")

    # Substitute placeholder input filenames with real paths to dummy PDFs
    for arg in args:
        if arg in dummy_pdfs:
            processed_args.append(str(dummy_pdfs[arg]))
        elif "=" in arg and arg.split("=")[1] in dummy_pdfs:
            handle, filename = arg.split("=", 1)
            processed_args.append(f"{handle}={dummy_pdfs[filename]}")
        else:
            processed_args.append(arg)

    pdftl_executable = shutil.which("pdftl")
    if not pdftl_executable:
        pytest.fail(
            "Could not find the 'pdftl' executable in the environment's PATH. "
            "Ensure you have run 'pip install -e .'"
        )

    command_to_run = [pdftl_executable] + processed_args

    # --- Step 2: Run the command ---
    # Run from the directory where dummy files are, so burst works without an output path
    cwd_child = next(iter(dummy_pdfs.values()))
    cwd = cwd_child.parent
    # hack in ./tmp for some tests to pass
    try:
        Path.mkdir(tmp_path / "tmp")
    except FileExistsError:
        pass
    # print(command_to_run)
    result = subprocess.run(command_to_run, capture_output=True, text=True, cwd=cwd)

    # --- Step 3: Assert success ---
    assert result.returncode == 0, (
        f"Command failed with exit code {result.returncode}.\n"
        f"Command: {' '.join(command_to_run)}\n"
        f"Stderr: {result.stderr}\n"
        f"Stdout: {result.stdout}"
    )

    # --- Step 4: Assert output file(s) exist ---
    if output_file:
        assert output_file.exists(), f"Output file was not created: {output_file}"
        assert output_file.stat().st_size > 0, f"Output file is empty: {output_file}"

    if output_template:
        # Check that at least the first burst file was created
        expected_first_file = Path(output_template % 1)
        assert (
            expected_first_file.exists()
        ), f"Burst output file was not created: {expected_first_file}"
        assert (
            expected_first_file.stat().st_size > 0
        ), f"Burst output file is empty: {expected_first_file}"
