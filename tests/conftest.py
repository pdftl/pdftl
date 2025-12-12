# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/conftest.py

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

# need
# pip install pytest PyMuPDF Pillow
# or
# apt-get install python3-pytest python3-pymupdf python3-pillow
from .create_pdf import create_custom_pdf

TESTS_DIR = Path(__file__).parent
SCRIPT_PATH = TESTS_DIR / "scripts" / "generate_form.py"
ASSETS_DIR = TESTS_DIR / "assets"
FORM_PDF = ASSETS_DIR / "Form.pdf"


@pytest.fixture(scope="session", autouse=True)
def ensure_form_pdf():
    """
    Automatically generates tests/assets/Form.pdf before the test session starts
    if it doesn't already exist (or always, if you prefer).
    """
    # Option A: Generate it every time to be safe (Recommended for fast scripts)
    # Option B: Check if exists first: if not FORM_PDF.exists(): ...

    logging.info(f"\n[Fixture] Generating {FORM_PDF}...")

    # Ensure the directory exists
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    # Run the generation script
    try:
        subprocess.check_call([sys.executable, str(SCRIPT_PATH)])
    except subprocess.CalledProcessError as e:
        pytest.fail(f"Failed to generate test PDF: {e}")

    yield

    # Optional: Clean up after tests are done
    # os.remove(FORM_PDF)


@pytest.fixture
def get_pdf_path():
    """
    Returns the absolute path to a PDF if it exists (checking public first,
    then private). Skips the test if the file is missing.
    """

    def _resolver(filename):
        # 1. Check Public Folder (Standard Git files)
        base = Path(__file__).parent
        public_path = base / "files" / "pdfs" / filename
        if not filename.endswith(".pdf"):
            public_path = base / "files" / "pdfs" / (filename + ".pdf")

        if public_path.exists():
            return public_path

        # 2. Check Private Folder (Local Dev only)
        private_path = base / "files" / "private" / filename
        if not filename.endswith(".pdf"):
            private_path = base / "files" / "private" / (filename + ".pdf")

        if private_path.exists():
            return private_path

        # 3. File not found? Skip!
        pytest.skip(f"Test file '{filename}' not found. Skipping.")

    return _resolver


@pytest.fixture
def temp_dir(tmp_path):
    """
    A pytest fixture that creates a temporary directory for test files.
    It yields a Path object to the directory.
    """
    # tmp_path is a built-in pytest fixture that provides a temporary directory
    return tmp_path


@pytest.fixture(scope="session")
def assets_dir():
    """Provides the path to the static assets directory."""
    return Path(__file__).parent / "assets"


@pytest.fixture(scope="session")
def pdf_factory(assets_dir):
    """
    A session-scoped factory fixture that creates and caches test PDFs.

    This fixture returns a function. When you call that function with a
    number of pages, it will return the path to a PDF with that many pages,
    creating it if it doesn't already exist for the test session.
    """
    created_files = {}  # Cache to store paths of generated PDFs

    def _get_or_create_pdf(num_pages: int):
        """The actual function that will be returned by the fixture."""
        if num_pages in created_files:
            return created_files[num_pages]

        assets_dir.mkdir(exist_ok=True)
        pdf_path = assets_dir / f"{num_pages}_page.pdf"

        if not pdf_path.exists():
            logging.info(f"Creating test asset: {pdf_path}")
            create_custom_pdf(str(pdf_path), pages=num_pages)

        created_files[num_pages] = pdf_path
        return pdf_path

    return _get_or_create_pdf


@pytest.fixture(scope="session")
def two_page_pdf(pdf_factory):
    """
    Ensures a standard two-page PDF exists for testing and returns its path.
    This now uses the pdf_factory for consistency.
    """
    return pdf_factory(2)


@pytest.fixture(scope="session")
def six_page_pdf(pdf_factory):
    """
    Ensures a standard two-page PDF exists for testing and returns its path.
    This now uses the pdf_factory for consistency.
    """
    return pdf_factory(6)


class Runner:
    """A helper class to run CLI commands and manage test files."""

    def __init__(self, temp_dir: Path):
        self.temp_dir = temp_dir
        self.pdftk_path = (
            os.environ["PDFTK"] if "PDFTK" in os.environ else shutil.which("pdftk")
        )
        self.durations = {}
        self.stdout = None
        self.stderr = None
        # self.pdftk_path = shutil.which("pdftk") # Find pdftk in the system's PATH

    def run(self, tool: str, args: list[str], check=True):
        """
        Runs a command for either 'pdftk' or 'pdftl'.

        Args:
            tool: The tool to run ('pdftk' or 'pdftl').
            args: A list of command-line arguments.
            check: If True, raises an exception if the command fails.
        """
        # py_command_head = [sys.executable, "-m", "coverage", "run", "-m", "pdftl", "-v"]
        py_command_head = [sys.executable, "-m", "pdftl"]
        if tool == "pdftl":
            command = py_command_head + args
        elif tool == "pdftl-experimental":
            command = py_command_head + ["--experimental"] + args
        elif tool == "pdftk":
            if not self.pdftk_path:
                pytest.skip("pdftk executable not found in PATH")
            command = [self.pdftk_path] + args
        else:
            raise ValueError(f"Unknown tool: {tool}")

        command_str = [str(item) for item in command]
        env = os.environ.copy()
        src_path = str(Path(__file__).parent.parent / "src")
        env["PYTHONPATH"] = f"{src_path}{os.pathsep}{env.get('PYTHONPATH', '')}"
        time_start = time.time()
        # Pass the modified environment to the subprocess
        result = subprocess.run(
            command_str, capture_output=True, text=True, check=False, env=env
        )
        self.durations[tool] = round(time.time() - time_start, 2)
        self.stdout = result.stdout
        self.stderr = result.stderr

        if check and result.returncode != 0:
            logging.warning("STDOUT:", result.stdout)
            logging.warning("STDERR:", result.stderr)
            raise subprocess.CalledProcessError(
                result.returncode, command_str, result.stdout, result.stderr
            )

        return result


@pytest.fixture
def runner(temp_dir):
    """Provides a configured Runner instance for each test."""
    return Runner(temp_dir)


def pytest_addoption(parser):
    parser.addoption("--pdftk", action="store", default=None)
