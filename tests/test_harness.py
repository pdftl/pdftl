# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl-tests.zip/tests/test_harness.py

import shlex
import warnings
from collections.abc import Callable
from pathlib import Path

import pytest

from .conftest import Runner


def format_size(size_bytes: int) -> str:
    """Formats a size in bytes to a human-readable string (B, KB, MB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024**2:
        return f"{size_bytes/1024:.2f} KB"
    else:
        return f"{size_bytes/1024**2:.2f} MB"


def run_test_case(
    runner: Runner,
    temp_dir: Path,
    *,
    input_pdf_generator: Callable[[], Path] | None,
    args_template: str,
    comparison_fns: list[Callable[[Path, Path], None]] = [],
    validation_fn: Callable[[Path], None] = None,
    commands: [str] = ["pdftl", "pdftk"],
    expected_stderrs: [str] = None,  # not implemented
    ignore_pdf_outputs=False,
):
    """
    A meta function to run a configurable test case against pdftl and (optionally) pdftk.
    """
    print(f"\n--- Running Test Case: pdftl {args_template} ---")

    # 1. Prepare inputs and outputs
    if input_pdf_generator is None:
        input_pdf = "/dev/null"
    else:
        input_pdf = input_pdf_generator()

    results = {}
    outputs = {}
    # times={}
    for i, command in enumerate(commands):
        output = temp_dir / f"output_{command}.pdf"

        args = [
            str(arg) for arg in shlex.split(args_template.format(input=input_pdf, output=output))
        ]
        with open(temp_dir / f"params_{command}.txt", "w") as cmd_file:
            cmd_file.write(f"command: {command}\nargs: {' '.join(args)}\n[args]: {args}")

        result = runner.run(command, args, check=False)
        results[command] = result
        outputs[command] = Path(output)
        print(f"   {command} run time: {runner.durations[command]}")

        # --- Report Exit Codes ---
        exit_code = result.returncode
        print(f"  {command} exit code: {result.returncode}")

        if exit_code == 0 and not ignore_pdf_outputs:
            assert output.exists(), f"{command} did not create an output file."
            input_size = input_pdf.stat().st_size
            out_size = output.stat().st_size
            print(
                f"  {command} file size: "
                f"Input: {format_size(input_size)}, Output: {format_size(out_size)}, "
                f"ratio {round(float(out_size)/input_size*100)}%"
            )

        if expected_stderrs:
            expected_stderr = expected_stderrs[min(i, len(expected_stderrs) - 1)]
            assert expected_stderr in result.stderr, f" {command} stderr mismatch.\n"
            f"Expected to see {expected_stderr}, got: {result.stderr}"

    # 3. Analyze results and assert
    return_codes_ok = [x.returncode == 0 for x in results.values()]
    if all(return_codes_ok):
        # --- SUCCESS CASE ---
        print("All tools succeeded. Comparing outputs...")

        for func in comparison_fns:
            for i, command in enumerate(commands):
                if i == 0:
                    continue
                # print(f"{func}({outputs[commands[i-1]]},{outputs[command]})")
                func(outputs[commands[i - 1]], outputs[command])
        print("✅ Outputs are identical.")

    elif all([not x for x in return_codes_ok]):
        # --- EXPECTED FAILURE CASE ---
        print("All tools failed, as expected.")
        print("✅ Test passed.")

    elif all(
        [
            "pdftk" in results,
            results["pdftk"].returncode != 0,
            len([x for x in return_codes_ok if not x]) == 1,
        ]
    ):
        warnings.warn("pdftk failed, others passed.")
    else:
        # --- DIVERGENCE (FAILURE) CASE ---
        result_codes_dict = {k: v.returncode for k, v in results.items()}
        pytest.fail(
            f"Tool behaviors diverged!\n"
            f"Return codes: {result_codes_dict}\n"
            f"--- stderrs for commands {commands} ---\n"
            "\n\n========\n\n".join([x.stderr for x in results.values()])
        )

    return results
