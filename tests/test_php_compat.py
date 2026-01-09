import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Config: Where is the PHP repo relative to this test file?
PHP_REPO_PATH = Path(__file__).parent.parent / "vendor_tests" / "php-pdftk"
PHP_UNIT_PATH = PHP_REPO_PATH / "vendor" / "bin" / "phpunit"

DOCKER_PATH = Path("/opt/php-pdftk")
LOCAL_PATH = Path(__file__).parent.parent / "vendor_tests" / "php-pdftk"

if DOCKER_PATH.exists():
    PHP_REPO_PATH = DOCKER_PATH
else:
    PHP_REPO_PATH = LOCAL_PATH


def prerequisites_met():
    """Checks if PHP, Composer deps, and the repo exist."""
    if not PHP_REPO_PATH.exists():
        return False
    if not PHP_UNIT_PATH.exists():
        return False
    if not shutil.which("php"):
        return False
    return True


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.skipif(not prerequisites_met(), reason="php-pdftk repo or dependencies missing")
def test_php_pdftk_compatibility(tmp_path):
    """
    Runs the php-pdftk test suite using YOUR python tool as 'pdftk'.
    Intercepts calls using a bash shim to log arguments.
    """
    # 1. Locate YOUR real tool
    real_pdftl = shutil.which("pdftl")
    if not real_pdftl:
        pytest.fail("Could not find 'pdftl'. Is your venv active?")

    # 2. Define a log file
    arg_log_file = tmp_path / "pdftl_args.log"

    # 3. Create the Shim script
    shim_content = f"""#!/bin/bash
    echo "--- START BLOCK ---" >> "{arg_log_file}"
    echo "ARGS: $@" >> "{arg_log_file}"
    
    # Run the real tool
    "{real_pdftl}" "$@"
    EXIT_CODE=$?
    
    echo "EXIT: $EXIT_CODE" >> "{arg_log_file}"
    echo "--- END BLOCK ---" >> "{arg_log_file}"
    
    exit $EXIT_CODE
    """

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_pdftk = fake_bin / "pdftk"
    fake_pdftk.write_text(shim_content)
    fake_pdftk.chmod(0o755)

    # 4. Prepare Environment
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]

    # 5. Run PHPUnit
    result = subprocess.run(
        [str(PHP_UNIT_PATH)], cwd=str(PHP_REPO_PATH), env=env, capture_output=True, text=True
    )

    if "No such file or directory" in result.stderr:
        pytest.fail(f"stderr indicates phpunit could not find a file: {result.stderr}")

    # 6. Reporting
    # PHPUnit 10 returns non-zero for Warnings (like missing coverage driver).
    # We must distinguish between "Tests Failed" and "Tests Passed with Warnings".

    # "OK (49 tests...)" -> Perfect pass
    # "OK, but there were issues!" -> Pass with warnings (e.g. no coverage)
    is_pass = "OK (" in result.stdout or "OK, but there were issues!" in result.stdout

    if result.returncode != 0 and not is_pass:
        filtered_log = []
        raw_log = ""

        if arg_log_file.exists():
            raw_log = arg_log_file.read_text()
            # Split log into individual command blocks
            blocks = raw_log.split("--- END BLOCK ---")

            for i, block in enumerate(blocks):
                if "--- START BLOCK ---" not in block:
                    continue

                clean_block = block.replace("--- START BLOCK ---", "").strip()

                # FILTER LOGIC:
                # 1. Show Failures: Any command with non-zero exit code.
                # 2. Show Context: The very last command executed.
                is_failure = "EXIT: 0" not in clean_block
                is_last = i == len(blocks) - 2  # -2 because split leaves an empty string at end

                if is_failure or is_last:
                    prefix = "[CRASH] " if is_failure else "[LAST RUN] "
                    filtered_log.append(prefix + clean_block)

        log_display = "\n\n".join(filtered_log) if filtered_log else "No commands captured."

        error_msg = (
            "\n" + ("=" * 60) + "\n"
            "PHP UNIT SUITE FAILED\n" + ("=" * 60) + "\n\n"
            "--- [ SUSPICIOUS COMMANDS ] ---\n"
            f"{log_display}\n"
            "\n"
            "--- [ PHPUNIT OUTPUT ] ---\n"
            f"{result.stdout}\n"
            f"{result.stderr}\n" + ("=" * 60)
        )

        pytest.fail(error_msg, pytrace=False)
