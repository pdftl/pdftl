# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/conftest.py

import copy
import importlib
import logging
import os
import pprint
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
import pikepdf

from pdftl.core.registry import registry

from .create_pdf import create_custom_pdf

TESTS_DIR = Path(__file__).parent
SCRIPT_PATH = TESTS_DIR / "scripts" / "generate_form.py"
ASSETS_DIR = TESTS_DIR / "assets"
FORM_PDF = ASSETS_DIR / "Form.pdf"

pytest_plugins = ["tests.server.server_fixtures"]


@pytest.fixture
def empty_pdf():
    with pikepdf.Pdf.new() as pdf:
        yield pdf


@pytest.fixture
def mock_missing_dependency():
    """Simulates a missing dependency and ensures cleanup."""

    @contextmanager
    def _simulate(dependency_name, module_to_reload):
        with mock.patch.dict(sys.modules, {dependency_name: None}):
            importlib.reload(module_to_reload)
            try:
                yield
            finally:
                # Teardown: Restore the module to working state
                importlib.reload(module_to_reload)

    return _simulate


@pytest.fixture(autouse=True)
def isolated_registry():
    """
    Global Registry Armor.
    Runs before EVERY test. Minimizes deepcopy depth where possible.
    """
    backup_state = copy.deepcopy(registry.__dict__)
    yield
    registry.__dict__.clear()
    registry.__dict__.update(backup_state)


@pytest.fixture(scope="session", autouse=True)
def ensure_form_pdf(tmp_path_factory, worker_id):
    """
    Automatically generates tests/assets/Form.pdf before the test session starts.
    Uses file locking to prevent multiple xdist workers from building assets at the same time.
    """
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    # If xdist is running parallel processes, synchronize them
    if worker_id != "master":
        # Get the shared root temp directory across all workers
        root_tmp_dir = tmp_path_factory.getbasetemp().parent
        lock_path = root_tmp_dir / "generate_form_pdf.lock"

        # Try importing filelock dynamically so it remains optional for flat serial environments
        try:
            from filelock import FileLock

            with FileLock(str(lock_path)):
                if not FORM_PDF.exists():
                    _compile_form_pdf()
        except ImportError:
            # Fallback if filelock isn't installed
            if not FORM_PDF.exists():
                _compile_form_pdf()
    else:
        # Serial mode execution
        if not FORM_PDF.exists():
            _compile_form_pdf()

    yield


def _compile_form_pdf():
    """Helper method to handle compilation execution."""
    logging.info(f"\n[Fixture] Generating {FORM_PDF}...")
    try:
        subprocess.check_call([sys.executable, str(SCRIPT_PATH)])
    except subprocess.CalledProcessError as e:
        pytest.fail(f"Failed to generate test PDF via subprocess: {e}")


@pytest.fixture
def get_pdf_path():
    """
    Returns the absolute path to a PDF if it exists. Skips the test if missing.
    """

    # codeql[py/mixed-returns]
    def _resolver(filename):
        base = TESTS_DIR
        if not filename.endswith(".pdf"):
            filename += ".pdf"

        for folder in ["pdfs", "private", Path("private") / "pop"]:
            path = base / "files" / folder / filename
            if path.exists():
                return path

        pdftk_java_files_path = TESTS_DIR.parent / "vendor_tests" / "pdftk-java" / "files"
        pdftk_java_path = pdftk_java_files_path / filename
        if pdftk_java_path.exists():
            return pdftk_java_path

        pytest.skip(f"Test file '{filename}' not found. Skipping.")

    return _resolver


@pytest.fixture
def temp_dir(tmp_path):
    """Provides a temporary directory for test files."""
    return tmp_path


@pytest.fixture(scope="session")
def assets_dir():
    """Provides the path to the static assets directory."""
    return Path(__file__).parent / "assets"


@pytest.fixture(scope="session")
def pdf_factory(assets_dir, tmp_path_factory, worker_id):
    """
    A session-scoped factory fixture that creates and caches test PDFs safely
    across parallel xdist worker nodes.
    """
    created_files = {}

    def _get_or_create_pdf(num_pages: int):
        if num_pages in created_files:
            return created_files[num_pages]

        assets_dir.mkdir(exist_ok=True)
        pdf_path = assets_dir / f"{num_pages}_page.pdf"

        if not pdf_path.exists():
            if worker_id != "master":
                root_tmp_dir = tmp_path_factory.getbasetemp().parent
                lock_path = root_tmp_dir / f"pdf_factory_{num_pages}.lock"
                try:
                    from filelock import FileLock

                    with FileLock(str(lock_path)):
                        if not pdf_path.exists():
                            create_custom_pdf(str(pdf_path), pages=num_pages)
                except ImportError:
                    if not pdf_path.exists():
                        create_custom_pdf(str(pdf_path), pages=num_pages)
            else:
                create_custom_pdf(str(pdf_path), pages=num_pages)

        created_files[num_pages] = pdf_path
        return pdf_path

    yield _get_or_create_pdf
    created_files.clear()


@pytest.fixture(scope="session")
def two_page_pdf(pdf_factory):
    return pdf_factory(2)


@pytest.fixture(scope="session")
def six_page_pdf(pdf_factory):
    return pdf_factory(6)


@pytest.fixture(scope="session")
def twelve_page_pdf(pdf_factory):
    return pdf_factory(12)


class Runner:
    """A helper class to run CLI commands and manage test files."""

    def __init__(self, temp_dir: Path):
        self.temp_dir = temp_dir
        self.pdftk_path = os.environ.get("PDFTK") or shutil.which("pdftk")
        self.durations = {}
        self.stdout = None
        self.stderr = None

    def run(self, tool: str, args: list[str], check=True):
        """
        Runs a command for either 'pdftk' or 'pdftl'.

        Args:
            tool: The tool to run ('pdftk' or 'pdftl').
            args: A list of command-line arguments.
            check: If True, raises an exception if the command fails.
        """
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
            logging.warning("STDOUT: %s", result.stdout)
            logging.warning("STDERR: %s", result.stderr)
            raise subprocess.CalledProcessError(
                result.returncode, command_str, result.stdout, result.stderr
            )

        return result


@pytest.fixture
def runner(temp_dir):
    """Provides a configured Runner instance for each test."""
    return Runner(temp_dir)


def pytest_addoption(parser):
    """Add command-line options to pytest."""
    parser.addoption("--pdftk", action="store", default=None)
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


@pytest.fixture
def temp_pdf():
    import pikepdf

    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        yield pdf


@pytest.fixture(scope="module")
def dummy_pdfs(tmp_path_factory, assets_dir, worker_id):
    """Creates a set of dummy PDF files cross-process safely using single compilation paths."""
    import pikepdf

    tmp_path = tmp_path_factory.mktemp("example_files")
    main_pdf_path = tmp_path / "main_20_page.pdf"
    create_custom_pdf(main_pdf_path, pages=20)

    overlay_pdf = pikepdf.Pdf.new()
    for _ in range(5):
        overlay_pdf.add_blank_page()
    overlay_pdf_path = tmp_path / "overlay_5_page.pdf"
    overlay_pdf.save(overlay_pdf_path)

    placeholder_names = {
        "a.pdf",
        "b.pdf",
        "c.pdf",
        "contract.pdf",
        "content.pdf",
        "doc1.pdf",
        "doc2.pdf",
        "front.pdf",
        "back.pdf",
        "in.pdf",
        "cover.pdf",
        "body.pdf",
        "index.pdf",
        "my.pdf",
        "main.pdf",
        "manga.pdf",
        "logo.pdf",
        "watermark.pdf",
        "overlay.pdf",
        "letterhead.pdf",
        "letter.pdf",
        "mark.pdf",
        "bgs.pdf",
        "slides.pdf",
        "stamps.pdf",
        "signatures.pdf",
        "doc_A.pdf",
        "doc_B.pdf",
        "twopagetest.pdf",
        "A.pdf",
        "B.pdf",
    }

    paths = {}
    for name in placeholder_names:
        is_overlay_type = any(
            kw in name for kw in ["watermark", "overlay", "letterhead", "stamp", "signature", "bg"]
        )
        target_pdf = overlay_pdf_path if is_overlay_type else main_pdf_path

        link_path = tmp_path / name
        if not link_path.exists():
            link_path.symlink_to(target_pdf)
        paths[name] = link_path

    def _copy_to_tmp_path_if_exists(src_item):
        if src_item.exists():
            shutil.copy(src_item, tmp_path / item)
        else:
            pytest.fail()

    for item in ["meta.txt", "bookmarks.json", "bookmarks.yaml", "Form.pdf"]:
        _copy_to_tmp_path_if_exists(assets_dir / item)

    files_dir = Path(__file__).parent / "files"

    for item in ["watermark.png", "logo.png", "background.jpg"]:
        _copy_to_tmp_path_if_exists(files_dir / "images" / item)

    scripts_source = Path(__file__).parent / "files" / "python"
    if scripts_source.exists():
        for script in scripts_source.glob("*.py"):
            target = tmp_path / script.name
            shutil.copy(script, target)
            paths[script.name] = target

    return paths


@pytest.fixture
def assert_dump_output(capsys):
    def _check(op_func, pdf, expected_text_or_list, **kwargs):
        op_name = op_func.__name__
        op_meta = registry.operations.get(op_name)

        if not op_meta:
            pytest.fail(f"Operation '{op_name}' is not registered.")

        hook = getattr(op_meta, "cli_hook", None)
        if not hook:
            pytest.fail(f"Operation '{op_name}' has no cli_hook registered!")

        result = op_func(pdf, output_file=None, **kwargs)
        opts = {"output_file": None, **kwargs}
        hook(result, SimpleNamespace(options=opts), None)

        out = capsys.readouterr().out
        targets = (
            expected_text_or_list
            if isinstance(expected_text_or_list, list)
            else [expected_text_or_list]
        )
        for text in targets:
            assert text in out, f"Expected '{text}' in output.\nGot:\n{out[:200]}..."
        return out

    return _check


@pytest.fixture
def clean_registry():
    """
    Reset the operation/option/help-topic registry and re-run discovery.

    IMPORTANT: This must NOT use importlib.reload() on already-imported
    application modules. Reloading rebuilds every class defined at module
    level as a brand-new object, silently breaking isinstance() checks for
    any test module that imported those classes directly before the reload
    ran (see: SimplifiedPath / Path isinstance failures, 2026-07-02 postmortem).
    Clearing the registry + re-invoking initialize_registry() is sufficient:
    _discover_modules() uses importlib.import_module(), which returns the
    cached module/class objects and simply re-executes registration
    decorators against the existing registry.
    """
    if hasattr(registry, "operations"):
        registry.operations.clear()
    if hasattr(registry, "options"):
        registry.options.clear()
    if hasattr(registry, "help_topics"):
        registry.help_topics.clear()

    import pdftl.registry_init

    if hasattr(pdftl.registry_init.initialize_registry, "initialized"):
        delattr(pdftl.registry_init.initialize_registry, "initialized")

    pdftl.registry_init.initialize_registry()
    return registry


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, "rep_" + rep.when, rep)


@pytest.fixture(autouse=False)
def forensic_dump_on_fail(request):
    yield
    node = request.node
    report = getattr(node, "rep_call", None)

    if report and report.failed:
        print("\n\n" + "=" * 80, file=sys.stderr)
        print("🛑  FORENSIC FAILURE DUMP", file=sys.stderr)
        print("=" * 80 + "\n", file=sys.stderr)
        try:
            from pdftl.core.registry import registry

            for x in ["operations", "options"]:
                print(f"\n  Examining registry.{x}", file=sys.stderr)
                reg = getattr(registry, x, None)
                if reg:
                    keys = sorted(list(reg.keys()))
                    pprint.pprint(keys, stream=sys.stderr, width=120, compact=True)
        except Exception as e:
            print(f"❌ Error inspecting registry: {e}", file=sys.stderr)


@pytest.fixture
def minimal_pdf():
    import pikepdf

    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(100, 100))
        yield pdf


@pytest.fixture
def mock_pdf():
    import pikepdf

    pdf = MagicMock(spec=pikepdf.Pdf)
    pdf.Root = MagicMock()
    pdf.Root.__contains__.return_value = False
    return pdf


@pytest.fixture(autouse=True)
def clean_logging_state():
    """Optimized quick namespace scan to preserve performance."""
    logger_dict = logging.Logger.manager.loggerDict
    for name, logger in logger_dict.items():
        if name.startswith("pdftl") and isinstance(logger, logging.Logger):
            logger.setLevel(logging.NOTSET)
            logger.propagate = True
            logger.disabled = False

    pdftl_logger = logging.getLogger("pdftl")
    pdftl_logger.propagate = True
    pdftl_logger.setLevel(logging.NOTSET)
    yield


@contextmanager
def allow_pdftl_reload():
    """
    Explicit, reviewed opt-out for the reload guard.

    Use only when you've confirmed no other already-imported module holds
    a direct reference to a class/function defined in the module being
    reloaded — otherwise you'll reintroduce the isinstance()-splitting bug
    the guard exists to catch. See postmortem 2026-07-02.
    """
    guarded_reload._allowed = True
    try:
        yield
    finally:
        guarded_reload._allowed = False


def guarded_reload(module):
    mod_name = getattr(module, "__name__", "")
    if (mod_name.startswith("pdftl.") or mod_name == "pdftl") and not getattr(
        guarded_reload, "_allowed", False
    ):
        raise RuntimeError(
            f"Blocked importlib.reload({mod_name!r}). Reloading pdftl "
            "modules rebuilds their classes as new objects and silently "
            "breaks isinstance() checks for any test module holding a "
            "pre-reload reference (see 2026-07-02 postmortem). Wrap this "
            "call in `with allow_pdftl_reload():` after confirming it's safe."
        )
    return _real_reload(module)


@pytest.fixture(scope="session", autouse=True)
def _forbid_module_reload_of_pdftl():
    global _real_reload
    _real_reload = importlib.reload
    importlib.reload = guarded_reload
    yield
    importlib.reload = _real_reload


@pytest.fixture
def run_pdftl():
    from pdftl.cli.main import main

    def _run(args):
        with patch.object(sys, "argv", ["pdftl"] + args):
            try:
                main()
            except SystemExit as e:
                if e.code != 0:
                    raise RuntimeError(f"pdftl failed with exit code {e.code}")

    return _run


@pytest.fixture
def mock_tty(monkeypatch):
    """Simulates an interactive terminal environment (triggers paging)."""
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)


@pytest.fixture
def mock_notty(monkeypatch):
    """Simulates a piped/non-interactive environment (bypasses paging)."""
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
