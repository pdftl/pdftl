import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from pdftl.utils.profiling import CliStageProfiler


@pytest.fixture(autouse=True)
def clean_env():
    """Ensures environmental overrides don't bleed between test cycles."""
    initial_env = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(initial_env)


## Passive Monitoring & Fast-Path Tests
## ------------------------------------


def test_passive_monitoring_fast_path_under_threshold():
    """Verifies that no report output happens when the runtime stays below the threshold."""
    with (
        patch("pdftl.utils.profiling.os.environ.get", return_value="0.5"),
        patch("pdftl.utils.profiling.time.perf_counter", side_effect=[10.0, 10.2]),
        patch.object(CliStageProfiler, "_process_breach") as mock_breach,
    ):
        with CliStageProfiler("parse_pages", ["--compact"]) as profiler:
            pass

        assert profiler.profiler is None
        mock_breach.assert_not_called()


def test_passive_monitoring_breached_threshold(tmp_path, caplog):
    """Verifies that exceeding the execution threshold triggers diagnostic warnings."""
    import logging

    os.environ["PDFTL_SLOW_THRESHOLD"] = "0.1"
    os.environ["PDFTL_PROFILE_STAGES"] = ""

    # Force a breach via sequential time increments
    with (
        patch("pdftl.utils.profiling.time.perf_counter", side_effect=[10.0, 10.3]),
        patch.object(
            CliStageProfiler, "_prepare_output_dir", return_value=tmp_path / "test_stage_1234"
        ),
        patch("sys.argv", ["pdftl", "input.pdf"]),
    ):
        # Capture INFO level logs or lower
        with caplog.at_level(logging.INFO):
            with CliStageProfiler("merge_layers", []):
                pass

    # Ensure warning notice was captured in the logs
    log_messages = [record.message for record in caplog.records]

    assert any(
        "Stage 'merge_layers' took 0.30s, which exceeds 0.10s" in msg for msg in log_messages
    )


## Target Matching Verification
## ----------------------------


@pytest.mark.parametrize(
    "env_value, expected_targeted",
    [
        ("all", True),
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("render,merge_layers,export", True),
        ("render, export", True),  # tests explicit splitting and trailing spacing
        ("other_stage", False),
        ("", False),
    ],
)
def test_profiler_targeting_logic(env_value, expected_targeted):
    """Validates global wildcards, fuzzy case evaluations, and exact target matching matches."""
    profiler = CliStageProfiler("render", [])
    assert profiler._is_targeted(env_value) is expected_targeted


def test_active_profiling_enabled_via_env(tmp_path):
    """Ensures cProfile hooks activate and export binary .prof frames if targeted."""
    os.environ["PDFTL_PROFILE_STAGES"] = "render"

    base_file = tmp_path / "render_run"

    with (
        patch.object(CliStageProfiler, "_prepare_output_dir", return_value=base_file),
        patch("sys.argv", ["pdftl"]),
    ):
        with CliStageProfiler("render", []) as profiler:
            assert profiler.profiler is not None
            # Do some arbitrary operations to record instructions
            _ = sum(i * i for i in range(10))

    # Verify that both the textual ledger and binary trace were committed to the disk
    assert Path(f"{base_file}.txt").exists()
    assert Path(f"{base_file}.prof").exists()


## Input Argument Fingerprinting & Edge Cases
## ------------------------------------------


def test_is_valid_pdf_arg_filtering(tmp_path):
    """Validates the directory inspection filters for CLI input tracking."""
    real_pdf = tmp_path / "sample.pdf"
    real_pdf.touch()

    profiler = CliStageProfiler("test", [])

    assert profiler._is_valid_pdf_arg(str(real_pdf)) is True
    assert profiler._is_valid_pdf_arg("missing.pdf") is False  # File does not exist
    assert profiler._is_valid_pdf_arg("--option=file.pdf") is False  # Contains assignments
    assert profiler._is_valid_pdf_arg("document.txt") is False  # Wrong extension


def test_fingerprint_generation_no_pdfs():
    """Asserts that report writes indicate an empty set if no PDFs are in sys.argv."""
    with patch("sys.argv", ["pdftl", "--verbose", "output.json"]):
        profiler = CliStageProfiler("test", [])
        fingerprints = profiler._fingerprint_inputs()
        assert fingerprints == {}


def test_file_hashing_io_error(tmp_path):
    """Validates fallback mechanisms when standard IO blocks chunk-reading loops."""
    bad_pdf = tmp_path / "corrupt.pdf"
    bad_pdf.touch()

    profiler = CliStageProfiler("test", [])

    # Simulate a sudden file access error mid-stream
    with patch("builtins.open", side_effect=OSError("Permission Denied")):
        metadata = profiler._hash_file(bad_pdf)

    assert metadata["size"] == "unknown"
    assert "error_reading_file" in metadata["hash"]


def test_prepare_output_dir_creates_directory():
    """Confirms target directories auto-create safely on initialization metrics."""
    profiler = CliStageProfiler("test_stage", [])

    with patch.object(Path, "mkdir") as mock_mkdir:
        base_path = profiler._prepare_output_dir()
        mock_mkdir.assert_called_once_with(exist_ok=True)
        assert base_path.parent == Path("pdftl_profiles")
        assert base_path.name.startswith("test_stage_")


def test_fingerprint_inputs_and_hash_chunks(tmp_path):
    """Hits Lines 180, 198-200: Executes the hashing chunk loop and saves fingerprint."""
    valid_pdf = tmp_path / "target_document.pdf"
    # Write exactly enough bytes to verify chunk loops (larger than 64KB chunk space)
    valid_pdf.write_bytes(b"A" * 70000)

    with patch("sys.argv", ["pdftl", str(valid_pdf)]):
        profiler = CliStageProfiler("test_stage", [])
        fingerprints = profiler._fingerprint_inputs()

        # Confirms Line 180 evaluated, and Lines 198-200 processed multiple read chunks
        assert str(valid_pdf) in fingerprints
        assert fingerprints[str(valid_pdf)]["size"] == 70000
        assert len(fingerprints[str(valid_pdf)]["hash"]) == 16


def test_write_fingerprints_with_data():
    """Hits Lines 126-128: Exercises reporting iteration for identified files."""
    profiler = CliStageProfiler("test_stage", [])
    mock_file = MagicMock()

    fake_fingerprints = {"/path/to/doc.pdf": {"size": 1024, "hash": "abc123xyz"}}

    # Run the internal writer framework
    profiler._write_fingerprints(mock_file, fake_fingerprints)

    # Assert strings from lines 127 are formatted and piped out to the text file stream
    mock_file.write.assert_any_call(
        "File: /path/to/doc.pdf\n  Size: 1024 bytes\n  SHA256: abc123xyz\n"
    )
