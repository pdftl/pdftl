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
from types import SimpleNamespace

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

import copy

from pdftl.core.registry import registry


@pytest.fixture
def mock_missing_dependency():
    """Simulates a missing dependency and ensures cleanup."""

    def _simulate(dependency_name, module_to_reload):
        with mock.patch.dict(sys.modules, {dependency_name: None}):
            importlib.reload(module_to_reload)
            yield
        # Teardown: Restore the module to working state
        importlib.reload(module_to_reload)

    return _simulate


@pytest.fixture(autouse=True)
def isolated_registry():
    """
    Global Registry Armor.

    Runs before EVERY test. It creates a deep copy of the registry's internal state,
    lets the test run, and then forcibly restores the original state into the
    EXISTING registry object.
    """
    # 1. SNAPSHOT: Deep copy ensures we capture nested objects (Operations, Options)
    #    This protects against tests that mutate existing operations (e.g. changing an arg).
    backup_state = copy.deepcopy(registry.__dict__)

    yield  # The test runs here

    # 2. WIPE: Clear the dirty state from the Real Registry
    registry.__dict__.clear()

    # 3. RESTORE: Pour the clean backup state back into the Real Registry
    #    We use 'update' so we modify the object in-place.
    registry.__dict__.update(backup_state)


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
        self.pdftk_path = os.environ["PDFTK"] if "PDFTK" in os.environ else shutil.which("pdftk")
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
        result = subprocess.run(command_str, capture_output=True, text=True, check=False, env=env)
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


def pytest_addoption(parser):
    """Add command-line options to pytest."""
    parser.addoption("--skip-slow", action="store_true", default=False, help="skip slow tests")


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "slow: mark test as slow to run")


def pytest_collection_modifyitems(config, items):
    """
    Skip tests marked as slow if --skip-slow is provided.
    """
    if not config.getoption("--skip-slow"):
        # --skip-slow not provided: run all tests by default
        return

    skip_slow = pytest.mark.skip(reason="skipped due to --skip-slow option")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


@pytest.fixture(scope="module")
def dummy_pdfs(tmp_path_factory, assets_dir):
    """
    A pytest fixture that creates a set of dummy PDF files with enough pages
    to satisfy all example commands.
    """
    import pikepdf

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


@pytest.fixture
def assert_dump_output(capsys):
    """
    Fixture that returns a helper function to run an operation,
    trigger its CLI hook, and assert text is present in stdout.
    """

    def _check(op_func, pdf, expected_text_or_list, **kwargs):
        # 1. Lookup the operation metadata in the central registry
        # We use the function name (e.g., 'dump_data_fields') as the key
        op_name = op_func.__name__
        op_meta = registry.operations.get(op_name)

        if not op_meta:
            pytest.fail(f"Operation '{op_name}' is not registered. (Is the module imported?)")

        # 2. Get the hook from the metadata object
        hook = getattr(op_meta, "cli_hook", None)
        if not hook:
            pytest.fail(
                f"Operation '{op_name}' has no cli_hook registered! (Check your @register_operation arguments)"
            )

        # 3. Run the operation (getting the OpResult)
        # We pass output_file=None to ensure it returns data, not writes to file
        result = op_func(pdf, output_file=None, **kwargs)

        # 4. Manually trigger the CLI hook
        # We strip 'output_file' from kwargs to avoid duplicates in options
        opts = {"output_file": None, **kwargs}
        hook(result, SimpleNamespace(options=opts))

        # 5. Assert
        out = capsys.readouterr().out

        if isinstance(expected_text_or_list, list):
            for text in expected_text_or_list:
                assert text in out, f"Expected '{text}' in output.\nGot:\n{out[:200]}..."
        else:
            assert (
                expected_text_or_list in out
            ), f"Expected '{expected_text_or_list}' in output.\nGot:\n{out[:200]}..."

        return out

    return _check


import importlib
import pkgutil
import sys

import pytest

import pdftl.commands
from pdftl.core.registry import registry


@pytest.fixture
def clean_registry():
    """
    Fixture that forces a complete reset of the operation registry.
    """
    # 1. Clear the existing registry entries IN PLACE.
    # We do NOT replace the 'registry' object, just clear its dicts.
    # This ensures that any module holding a reference to 'registry'
    # (like api.py) sees the empty state and then the refill.
    if hasattr(registry, "operations"):
        registry.operations.clear()
    if hasattr(registry, "options"):
        registry.options.clear()
    if hasattr(registry, "help_topics"):
        registry.help_topics.clear()

    # 2. Force-reload the command modules.
    # Instead of walking packages manually, we trust the logic in
    # 'initialize_registry' but we must ensure it actually runs.
    # Since 'initialize_registry' usually has a "has_run" check,
    # we might need to bypass it or force re-execution.

    # Check if initialize_registry is idempotent or flagged.
    # If it has a flag like '_initialized = True', reset it here.
    import pdftl.registry_init

    if hasattr(pdftl.registry_init, "_initialized"):
        pdftl.registry_init._initialized = False

    # 3. Re-run the standard initialization logic.
    # This mimics exactly what 'main()' does at startup.
    # Note: If initialize_registry imports modules, and they are already
    # in sys.modules, Python won't re-run the file (and thus won't re-run decorators).
    # SO we DO need to force reload, but we can do it smarter.

    # Helper to force-reload all 'pdftl.commands' submodules
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("pdftl.commands."):
            try:
                importlib.reload(sys.modules[mod_name])
            except ImportError:
                # If reload fails (e.g. module was deleted), just ignore
                pass

    from pdftl.registry_init import initialize_registry

    # Now run the official init to catch anything else
    initialize_registry()

    return registry


import os
import pprint
import sys

import pytest


# --- PART 1: Hookwrapper to capture test status ---
@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """
    This hook runs after every test phase (setup, call, teardown).
    We use it to attach the test report (pass/fail status) to the test item
    so our fixture can see it.
    """
    # Execute all other hooks to obtain the report object
    outcome = yield
    rep = outcome.get_result()

    # Attach the report to the test item (node)
    # We create attributes like 'rep_call', 'rep_setup', etc.
    setattr(item, "rep_" + rep.when, rep)


# --- PART 2: Autouse Fixture to check status and Dump ---
@pytest.fixture(autouse=True)
def forensic_dump_on_fail(request):
    """
    Runs automatically for every test.
    In the teardown phase (yield), it checks if the test failed.
    If yes, it prints the forensic dump.
    """
    yield  # Run the test

    # Check if the 'call' phase (the actual test) failed
    node = request.node
    report = getattr(node, "rep_call", None)

    if report and report.failed:
        print("\n\n" + "=" * 80, file=sys.stderr)
        print("🛑  FORENSIC FAILURE DUMP", file=sys.stderr)
        print("=" * 80 + "\n", file=sys.stderr)

        # 1. Inspect the Registry State
        try:
            from pdftl.core.registry import registry

            # Use a list of keys to keep output clean, but show distinct namespaces
            for x in ["operations", "options"]:
                print(f"\n  Examining registry.{x}", file=sys.stderr)
                reg = getattr(registry, x, None)
                if reg:
                    keys = sorted(list(reg.keys()))
                    pprint.pprint(keys, stream=sys.stderr, width=120, compact=True)

                    # Specific check for signing keys if relevant
                    if any("sign" in str(k) for k in keys):
                        print(
                            f"\n   ✅ Found 'sign' related keys in registry.{x}", file=sys.stderr
                        )
                    else:
                        print(f"\n   ⚠️  NO 'sign' keys found in registry.{x}", file=sys.stderr)

        except Exception as e:
            print(f"❌ Error inspecting registry: {e}", file=sys.stderr)

        print("-" * 40, file=sys.stderr)

        # 2. Inspect sys.modules (loaded packages)
        print("📦 LOADED PDFTL MODULES:", file=sys.stderr)
        pdftl_modules = sorted([m for m in sys.modules.keys() if m.startswith("pdftl")])

        # Highlight crucial modules
        crucial = ["pdftl.output.sign", "pdftl.registry_init"]
        for c in crucial:
            status = "✅ LOADED" if c in pdftl_modules else "❌ MISSING"
            loc = getattr(sys.modules.get(c), "__file__", "N/A") if c in pdftl_modules else ""
            print(f"   {status} : {c} ({loc})", file=sys.stderr)

        print("-" * 40, file=sys.stderr)
        print("\n" + "=" * 80 + "\n", file=sys.stderr)
