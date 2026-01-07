import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pikepdf
import pytest
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import signers

from pdftl.operations.dump_signatures import dump_signatures, dump_signatures_cli_hook

# --- Fixtures ---


@pytest.fixture
def cert_and_key():
    """Returns paths to test certificate and key assets."""
    key_path = Path("tests/assets/signing/test_key.pem")
    cert_path = Path("tests/assets/signing/test_cert.pem")
    return str(key_path), str(cert_path)


@pytest.fixture
def out_pdf_with_no_sigs():
    """Fixture providing a blank pikepdf object."""
    pdf = pikepdf.new()
    pdf.add_blank_page()
    return pdf


@pytest.fixture
def signed_pdf_path(tmp_path, cert_and_key):
    """Creates a physically signed PDF and returns the path."""
    pdf_path = tmp_path / "test_signed.pdf"
    key, cert = cert_and_key

    pdf = pikepdf.new()
    pdf.add_blank_page()

    buf = io.BytesIO()
    pdf.save(buf)
    buf.seek(0)

    w = IncrementalPdfFileWriter(buf)
    signer = signers.SimpleSigner.load(key, cert)
    with open(pdf_path, "wb") as out:
        signers.sign_pdf(
            w,
            signers.PdfSignatureMetadata(field_name="Signature1"),
            signer=signer,
            output=out,
        )
    return str(pdf_path)


@pytest.fixture
def encrypted_signed_pdf_path(tmp_path, cert_and_key):
    """Creates an encrypted signed PDF (user password 'bar') and returns the path."""
    pdf_path = tmp_path / "test_encrypted.pdf"
    key, cert = cert_and_key

    pdf = pikepdf.new()
    pdf.add_blank_page()
    enc = pikepdf.Encryption(user="bar", owner="foo", R=6)

    buf = io.BytesIO()
    pdf.save(buf, encryption=enc)
    buf.seek(0)

    w = IncrementalPdfFileWriter(buf)
    w.prev.decrypt(b"bar")
    w.encrypt(user_pwd=b"bar")

    signer = signers.SimpleSigner.load(key, cert)
    with open(pdf_path, "wb") as out:
        signers.sign_pdf(
            w,
            signers.PdfSignatureMetadata(field_name="Signature1"),
            signer=signer,
            output=out,
        )
    return str(pdf_path)


@pytest.fixture
def dump_sigs_helper(tmp_path):
    """
    Returns a function that runs dump_signatures and returns the output text.
    Handles temp file creation, cleanup, and string reading automatically.
    """

    def _runner(pdf_path_or_obj, password=None):
        # 1. Setup temp file
        output_file = tmp_path / "sigs_output.txt"

        # 2. Determine args based on input type
        pdf_path = pdf_path_or_obj if isinstance(pdf_path_or_obj, str) else "_"
        pdf_obj = pdf_path_or_obj if not isinstance(pdf_path_or_obj, str) else None

        # 3. Run Command
        result = dump_signatures(pdf_path, pdf_obj, password, output_file=str(output_file))
        dump_signatures_cli_hook(result, None)

        # 4. Return content
        return output_file.read_text(encoding="utf-8")

    return _runner


# --- The Refactored Tests ---


def test_dump_signatures_from_file(signed_pdf_path, dump_sigs_helper):
    """Tests reading from a physical file path."""
    results = dump_sigs_helper(signed_pdf_path)

    assert "SignatureBegin" in results
    assert "SignatureFieldName: Signature1" in results
    assert "SignatureIntegrity: VALID" in results


def test_dump_signatures_from_memory(signed_pdf_path, dump_sigs_helper):
    """Tests reading from a pikepdf object."""
    with pikepdf.open(signed_pdf_path) as pdf:
        results = dump_sigs_helper(pdf)
        assert "SignatureBegin" in results


def test_dump_signatures_encrypted(encrypted_signed_pdf_path, dump_sigs_helper):
    """Tests decryption logic with provided password."""
    results = dump_sigs_helper(encrypted_signed_pdf_path, password="bar")
    assert "SignatureBegin" in results


# --- Tests ---


def test_dump_signatures_no_signatures(tmp_path, out_pdf_with_no_sigs):
    """Tests logic for documents without signatures."""
    output_file = tmp_path / "sig_dump.txt"
    result = dump_signatures("_", out_pdf_with_no_sigs, None, output_file=str(output_file))
    dump_signatures_cli_hook(result, None)
    assert "No signatures found." in output_file.read_text()


def test_dump_signatures_suspicious_mod(signed_pdf_path):
    """Tests handling of non-DiffResult modification results (Lines 113-117)."""
    output = io.StringIO()
    mock_status = MagicMock()
    mock_status.intact = True
    mock_status.md_algorithm = "sha256"
    mock_status.coverage.name = "PARTIAL"
    mock_status.signing_cert.subject.native = {"common_name": "Test Signer"}
    mock_status.diff_result = Exception()

    # FIX: Since validate_pdf_signature is imported LOCALLY inside the function,
    # we must patch it in the place it is IMPORTED FROM (pyhanko.sign.validation)
    # rather than where it is used.
    target = "pyhanko.sign.validation.validate_pdf_signature"

    with patch(target, return_value=mock_status):
        with patch("pdftl.operations.dump_signatures.smart_open_output") as mock_open:
            mock_open.return_value.__enter__.return_value = output
            result = dump_signatures(signed_pdf_path, None, None)
            dump_signatures_cli_hook(result, None)
            assert "SignatureModificationLevel: SUSPICIOUS (Exception)" in output.getvalue()


import sys

import pytest

import pdftl.core.constants as c
from pdftl.core.types import OpResult
from pdftl.operations.dump_signatures import _validate_signatures_worker


def test_dump_signatures_hook_multiple_sigs():
    """
    Covers line 64: print("---", file=out)
    Verifies that the separator is printed when multiple signatures exist.
    """
    # 1. Mock result data with TWO signatures
    fake_sigs = [
        {
            "field_name": "Sig1",
            "signer": "Alice",
            "hash_algorithm": "sha256",
            "is_valid": True,
            "coverage": "ENTIRE_FILE",
            "modification_level": "NONE",
        },
        {
            "field_name": "Sig2",
            "signer": "Bob",
            "hash_algorithm": "sha256",
            "is_valid": False,
            "coverage": "PARTIAL",
            "modification_level": "FORM_FILLING",
        },
    ]

    op_result = OpResult(
        success=True, data=fake_sigs, meta={c.META_OUTPUT_FILE: None}  # None -> Stdout
    )

    # 2. Capture stdout
    # We patch smart_open_output or just capture stdout if output_file is None.
    # The hook uses smart_open_output(None) which usually defaults to sys.stdout.
    # We will assume smart_open_output handles None by yielding sys.stdout,
    # so we can use capsys.

    # Actually, let's mock smart_open_output to be safe and independent of IO implementation
    with patch("pdftl.operations.dump_signatures.smart_open_output") as mock_open:
        # Create a StringIO to capture output
        mock_buffer = io.StringIO()
        mock_open.return_value.__enter__.return_value = mock_buffer

        dump_signatures_cli_hook(op_result, "post_run")

        output = mock_buffer.getvalue()

    # 3. Assert separator exists
    assert "---" in output
    assert "SignatureFieldName: Sig1" in output
    assert "SignatureFieldName: Sig2" in output


def test_validate_signatures_missing_pyhanko():
    """
    Covers lines 103-104: except ImportError: raise RuntimeError(...)
    """
    # 1. Simulate pyhanko being missing by setting it to None in sys.modules
    with patch.dict(sys.modules, {"pyhanko": None, "pyhanko.pdf_utils.reader": None}):

        with pytest.raises(RuntimeError, match="pyhanko' library is required"):
            # We call the worker directly or the main command; worker is direct access to the import block
            _validate_signatures_worker("dummy.pdf", None, None)
