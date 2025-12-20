import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pikepdf
import pytest
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import signers

from pdftl.commands.dump_signatures import dump_signatures

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


# --- Tests ---


def test_dump_signatures_no_signatures(tmp_path, out_pdf_with_no_sigs):
    """Tests logic for documents without signatures."""
    output_file = tmp_path / "sig_dump.txt"
    dump_signatures("_", out_pdf_with_no_sigs, None, output_file=str(output_file))
    assert "No signatures found." in output_file.read_text()


def test_dump_signatures_from_file(signed_pdf_path):
    """Tests reading from a physical file path (Lines 64-66)."""
    output = io.StringIO()
    with patch("pdftl.commands.dump_signatures.smart_open_output") as mock_open:
        mock_open.return_value.__enter__.return_value = output
        dump_signatures(signed_pdf_path, None, None, output_file="dummy.txt")

        results = output.getvalue()
        assert "SignatureBegin" in results
        assert "SignatureFieldName: Signature1" in results
        assert "SignatureIntegrity: VALID" in results


def test_dump_signatures_from_memory(signed_pdf_path):
    """Tests reading from a pikepdf object via pdf.save (Lines 67-70)."""
    output = io.StringIO()
    with pikepdf.open(signed_pdf_path) as pdf:
        with patch("pdftl.commands.dump_signatures.smart_open_output") as mock_open:
            mock_open.return_value.__enter__.return_value = output
            dump_signatures("_", pdf, None, output_file="dummy.txt")
            assert "SignatureBegin" in output.getvalue()


def test_dump_signatures_encrypted(encrypted_signed_pdf_path):
    """Tests decryption logic with provided password (Lines 76-79)."""
    output = io.StringIO()
    with patch("pdftl.commands.dump_signatures.smart_open_output") as mock_open:
        mock_open.return_value.__enter__.return_value = output
        dump_signatures(encrypted_signed_pdf_path, None, "bar", output_file="dummy.txt")
        assert "SignatureBegin" in output.getvalue()


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
        with patch("pdftl.commands.dump_signatures.smart_open_output") as mock_open:
            mock_open.return_value.__enter__.return_value = output
            dump_signatures(signed_pdf_path, None, None)
            assert "SignatureModificationLevel: SUSPICIOUS (Exception)" in output.getvalue()
