import json
import subprocess
import sys

# Adjust this import to match where your PdfInfo/pikepdf helpers are,
# or use pikepdf directly for verification.
import pikepdf
import pytest


def run_pdftl(args):
    """Helper to run pdftl via subprocess to simulate real CLI usage."""
    cmd = [sys.executable, "-m", "pdftl"] + args
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
    result.check_returncode()
    return result


@pytest.fixture
def input_pdf(tmp_path):
    """Creates a minimal blank PDF for testing."""
    pdf_path = tmp_path / "input.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(pdf_path)
    return pdf_path


def test_update_info_legacy_at_file(tmp_path, input_pdf):
    """
    Verifies 'update_info' using @file.json.
    Checks that it accepts XML-encoded entities in the JSON values.
    """
    output_pdf = tmp_path / "output_legacy.pdf"
    json_path = tmp_path / "metadata_legacy.json"

    # 1. Create JSON with XML entities (e.g. &amp;, &#169;)
    # 'update_info' (standard) usually maps these to PDF DocInfo
    meta_data = {
        "Info": {
            "Title": "Tom &amp; Jerry",  # & -> &amp;
            "Author": "Copyright &#169; 2024",  # © -> &#169;
        }
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(meta_data, f)

    # 2. Run: pdftl input.pdf update_info @metadata.json output.pdf
    run_pdftl([str(input_pdf), "update_info", f"@{str(json_path)}", "output", str(output_pdf)])

    # 3. Verify Result
    with pikepdf.open(output_pdf) as pdf:
        info = pdf.docinfo
        # Note: pdftk/update_info usually decodes the XML entities when writing to the PDF
        assert info["/Title"] == "Tom & Jerry"
        assert info["/Author"] == "Copyright © 2024"


def test_update_info_utf8_at_file(tmp_path, input_pdf):
    """
    Verifies 'update_info_utf8' using @file.json.
    Checks that it accepts raw Unicode characters.
    """
    output_pdf = tmp_path / "output_utf8.pdf"
    json_path = tmp_path / "metadata_utf8.json"

    # 1. Create JSON with raw UTF-8 characters (Emoji, Accents)
    meta_data = {"Info": {"Title": "Café ☕ Life", "Author": "Jürgen Üser"}}

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(meta_data, f)

    # 2. Run: pdftl input.pdf update_info_utf8 @metadata.json output.pdf
    run_pdftl(
        [str(input_pdf), "update_info_utf8", f"@{str(json_path)}", "output", str(output_pdf)]
    )

    # 3. Verify Result
    with pikepdf.open(output_pdf) as pdf:
        info = pdf.docinfo
        assert info["/Title"] == "Café ☕ Life"
        assert info["/Author"] == "Jürgen Üser"


def test_at_file_missing_fails(tmp_path, input_pdf):
    """Ensure we get a clear error if the @file doesn't exist."""
    missing_file = tmp_path / "does_not_exist.json"

    cmd = [
        sys.executable,
        "-m",
        "pdftl",
        str(input_pdf),
        "update_info",
        f"@{str(missing_file)}",
        "output",
        str(tmp_path / "out.pdf"),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode != 0
    # Ensure the error message mentions the file problem
    assert "No such file" in result.stderr or "does_not_exist.json" in result.stderr
