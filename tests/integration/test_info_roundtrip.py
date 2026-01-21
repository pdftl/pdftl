import sys
from unittest.mock import patch

import pikepdf
import pytest

from pdftl.cli.main import main


def run_pdftl(args):
    """
    Helper to run the CLI in-process.
    This is faster than subprocess and allows coverage tracking.
    """
    # We patch sys.argv to simulate running from the command line
    with patch.object(sys, "argv", ["pdftl"] + args):
        try:
            main()
        except SystemExit as e:
            # Catch exit(0) (success) but let errors fail the test
            if e.code != 0:
                raise RuntimeError(f"pdftl failed with exit code {e.code}")


@pytest.fixture
def source_pdf_path(tmp_path):
    p = tmp_path / "source.pdf"
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        with pdf.open_metadata() as meta:
            meta["dc:title"] = "CLI Round Trip"
            meta["dc:creator"] = ["Integration Bot"]

        # specific ID
        test_id = b"12345678901234567890123456789012"
        pdf.trailer.ID = pikepdf.Array([test_id, test_id])
        pdf.save(p)
    return p


@pytest.fixture
def blank_dest_pdf_path(tmp_path):
    p = tmp_path / "dest.pdf"
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        pdf.save(p)
    return p


def test_round_trip_stanza_cli(source_pdf_path, blank_dest_pdf_path, tmp_path):
    """
    Tests: pdftl source.pdf dump_data output meta.txt
           pdftl dest.pdf update_info meta.txt output out.pdf
    """
    meta_file = tmp_path / "meta.txt"
    out_pdf_file = tmp_path / "out.pdf"

    # 1. Run DUMP
    run_pdftl([str(source_pdf_path), "dump_data", "output", str(meta_file)])

    assert meta_file.exists()

    # 2. Run UPDATE
    run_pdftl(
        [
            str(blank_dest_pdf_path),
            "update_info",
            str(meta_file),
            "output",
            str(out_pdf_file),
        ]
    )

    # 3. Verify
    with pikepdf.open(out_pdf_file) as pdf:
        assert pdf.docinfo["/Title"] == "CLI Round Trip"
        assert bytes(pdf.trailer.ID[0]) == b"12345678901234567890123456789012"


def test_round_trip_json_cli(source_pdf_path, blank_dest_pdf_path, tmp_path):
    """
    Tests: pdftl source.pdf dump_data json output meta.json
           pdftl dest.pdf update_info @meta.json output out.pdf
    """
    meta_file = tmp_path / "meta.json"
    out_pdf_file = tmp_path / "out_json.pdf"

    # 1. Run DUMP (with 'json' flag)
    json_args = [
        str(source_pdf_path),
        "dump_data",
        "json",
        "output",
        str(meta_file),
    ]

    run_pdftl(json_args)

    assert meta_file.exists()

    # 2. Run UPDATE
    update_json_args = [
        str(blank_dest_pdf_path),
        "update_info",
        "@" + str(meta_file),
        "output",
        str(out_pdf_file),
    ]
    run_pdftl(update_json_args)

    # 3. Verify
    with pikepdf.open(out_pdf_file) as pdf:
        assert pdf.docinfo["/Title"] == "CLI Round Trip"
        assert bytes(pdf.trailer.ID[0]) == b"12345678901234567890123456789012"
