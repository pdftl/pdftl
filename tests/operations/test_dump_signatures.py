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
        pdf_path = pdf_path_or_obj if isinstance(pdf_path_or_obj, (str, Path)) else "_"
        pdf_obj = pdf_path_or_obj if not isinstance(pdf_path_or_obj, (str, Path)) else None

        # 3. Run Command
        result = dump_signatures(pdf_path, pdf_obj, password, output_file=str(output_file))
        dump_signatures_cli_hook(result, None, None)

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


def test_dump_signatures_from_etsi_file(get_pdf_path, dump_sigs_helper):
    """Tests reading from a physical file path."""
    signed_pdf_path = get_pdf_path("sig_etsi.pdf")
    results = dump_sigs_helper(signed_pdf_path)

    assert "SignatureBegin" in results
    assert "SignatureFieldName: 2A1EB9ED1E3766E2FB585A476CD57FEF" in results
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
    dump_signatures_cli_hook(result, None, None)
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
        with patch("pdftl.operations.dump_signatures.smart_open") as mock_open:
            mock_open.return_value.__enter__.return_value = output
            result = dump_signatures(signed_pdf_path, None, None)
            dump_signatures_cli_hook(result, None, None)
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
        success=True,
        data=fake_sigs,
        meta={c.META_OUTPUT_FILE: None},  # None -> Stdout
    )

    # 2. Capture stdout
    # We patch smart_open or just capture stdout if output_file is None.
    # The hook uses smart_open(None) which usually defaults to sys.stdout.
    # We will assume smart_open handles None by yielding sys.stdout,
    # so we can use capsys.

    # Actually, let's mock smart_open to be safe and independent of IO implementation
    with patch("pdftl.operations.dump_signatures.smart_open") as mock_open:
        # Create a StringIO to capture output
        mock_buffer = io.StringIO()
        mock_open.return_value.__enter__.return_value = mock_buffer

        dump_signatures_cli_hook(op_result, "post_run", None)

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


import pytest


def test_dump_signatures_raises_operation_error_on_validation_failure(signed_pdf_path, tmp_path):
    """Tests that SignatureValidationError is wrapped in OperationError."""
    from pyhanko.sign.validation.errors import SignatureValidationError

    from pdftl.exceptions import OperationError

    with patch(
        "pyhanko.sign.validation.validate_pdf_signature",
        side_effect=SignatureValidationError("bad sig"),
    ):
        with pytest.raises(OperationError, match="bad sig"):
            from pdftl.operations.dump_signatures import dump_signatures

            dump_signatures(signed_pdf_path, None, None)


def test_dump_signatures_raises_operation_error_on_value_error(signed_pdf_path):
    """Tests that ValueError is also wrapped in OperationError."""
    from pdftl.exceptions import OperationError

    with patch(
        "pyhanko.sign.validation.validate_pdf_signature",
        side_effect=ValueError("unexpected value"),
    ):
        with pytest.raises(OperationError, match="unexpected value"):
            from pdftl.operations.dump_signatures import dump_signatures

            dump_signatures(signed_pdf_path, None, None)


def test_patch_pyhanko_bytes_handling():
    """Directly tests the patched function body."""
    from pyhanko.pdf_utils import generic

    from pdftl.operations.dump_signatures import _patch_pyhanko

    # Ensure patch is applied
    _patch_pyhanko()

    # Call with bytes — this exercises the isinstance branch
    generic.parse_pdf_date(b"D:20211230134641+11'00'")

    # Call with trailing nulls
    generic.parse_pdf_date("D:20211230134641+11'00'\x00   ")


# --- Additional Coverage Tests ---


def test_dump_signatures_isolated_cert_fallback(signed_pdf_path):
    """
    Covers lines 283-286.
    Tests the fallback when validation_path is None, but a signing_cert exists.
    """
    from pdftl.operations.dump_signatures import dump_signatures

    mock_status = MagicMock()
    mock_status.intact = True
    mock_status.diff_result = "Basic Modification"

    # Empty validation path, but provide the isolated signing cert
    mock_status.validation_path = None
    mock_cert = MagicMock()
    mock_cert.subject.native = {"common_name": "Isolated Test Signer"}
    mock_status.signing_cert = mock_cert

    with patch("pyhanko.sign.validation.validate_pdf_signature", return_value=mock_status):
        result = dump_signatures(signed_pdf_path, None, None)

    assert result.success
    sig_data = result.data[0]
    # Verify the fallback tag was applied
    assert "Isolated Test Signer (Untrusted/Isolated)" in sig_data["chain_of_trust"]


def test_parse_suspicious_details_all_branches():
    """
    Covers lines 98-130 in dump_signatures.py.
    Tests all regex replacements and formatting branches of the text parser.
    """
    from pdftl.operations.dump_signatures import _parse_suspicious_details

    # A crafted string that triggers every branch:
    # - Exception wrapper regex
    # - Reference & Context compression
    # - Orphan bullets and text
    # - Header colons vs Inline colons
    # - End-of-loop flushing with and without data
    raw_diff = (
        'Exception("\n'
        "- Orphan Bullet\n"
        "Orphan Plain Text\n"
        "Header 1:\n"
        "- Bullet under header\n"
        "Plain text under header\n"
        "InlineType: Inline Data\n"
        "Header 2:\n"
        "Reference(idnum=112, generation=0)\n"
        "AbsoluteContext(path=PathInRevision('.Root.Pages'))\n"
        "Header 3:\n"
        "Trailing data to flush\n"
        '")'
    )

    blocks = _parse_suspicious_details(raw_diff)

    # We expect 6 blocks because `112:0` triggers the inline colon split branch
    assert len(blocks) == 6
    assert blocks[0] == {"type": "General Modification", "data": "Orphan Plain Text"}
    assert blocks[1] == {
        "type": "Header 1",
        "data": "Bullet under header | Plain text under header",
    }
    assert blocks[2] == {"type": "InlineType", "data": "Inline Data"}
    assert blocks[3] == {"type": "112", "data": "0"}
    assert blocks[4] == {"type": "Header 2", "data": ".Root.Pages"}
    assert blocks[5] == {"type": "Header 3", "data": "Trailing data to flush"}


def test_dump_signatures_diffresult_suspicious(signed_pdf_path):
    """
    Covers lines 261 and 281-282.
    Tests the branch where diff_result is exactly an instance of DiffResult
    and its modification level is SUSPICIOUS, plus the validation_path loop.
    """
    from pyhanko.sign.diff_analysis import DiffResult
    from pdftl.operations.dump_signatures import dump_signatures

    mock_status = MagicMock()
    mock_status.intact = True

    # Fake a proper pyHanko DiffResult without strict spec causing AttributeError
    mock_diff = MagicMock()
    mock_diff.__class__ = DiffResult
    mock_diff.modification_level.name = "SUSPICIOUS"
    mock_diff.__str__.return_value = "FakeHeader:\n- Fake modification"
    mock_status.diff_result = mock_diff

    # Trigger coverage for lines 281-282 (validation path iteration)
    mock_cert = MagicMock()
    mock_cert.subject.native = {"common_name": "Chain Cert"}
    mock_cert.subject.human_friendly = "Chain Cert Friendly"
    mock_status.validation_path = [mock_cert]
    mock_status.signing_cert = None

    with patch("pyhanko.sign.validation.validate_pdf_signature", return_value=mock_status):
        result = dump_signatures(signed_pdf_path, None, None)

    assert result.success
    sig_data = result.data[0]
    assert sig_data["modification_level"] == "SUSPICIOUS"
    assert len(sig_data["suspicious_details"]) > 0
    assert sig_data["suspicious_details"][0]["type"] == "FakeHeader"
    assert "Chain Cert" in sig_data["chain_of_trust"]


def test_parse_suspicious_details_empty_flush():
    """
    Covers lines 129-130 in dump_signatures.py.
    Ensures that a trailing header with no data is properly flushed.
    """
    from pdftl.operations.dump_signatures import _parse_suspicious_details

    # A string that ends exactly on a header, leaving current_data empty
    raw_diff = "Trailing Empty Header:\n"

    blocks = _parse_suspicious_details(raw_diff)

    assert len(blocks) == 1
    assert blocks[0] == {"type": "Trailing Empty Header", "data": ""}


def test_signature_no_timestamp_handled_correctly():
    """
    Covers line 237 and ensures that a missing timestamp safely
    propagates as None and is omitted from the CLI output.
    """
    import io
    from pdftl.operations.dump_signatures import _extract_signature_info, _print_signature_stanza

    # 1. Mock the necessary pyHanko objects to survive extraction
    class MockSig:
        field_name = "TestSigNoTime"

    class MockStatus:
        md_algorithm = "sha256"
        intact = True
        valid = True
        trust_problem_indic = None
        docmdp_ok = True
        pkcs7_signature_mechanism = "rsa"
        validation_path = []
        signing_cert = None

        # Mocking the coverage and diff_result enums/objects
        coverage = type("MockCoverage", (), {"name": "ENTIRE_FILE"})
        diff_result = type(
            "MockDiff", (), {"modification_level": type("MockMod", (), {"name": "NONE"})}
        )

        # Explicitly lack timestamp data to trigger the fallback branch
        timestamp_validity = None
        signer_reported_dt = None

    # 2. Run the extraction (this hits line 237 and returns None)
    sig_data = _extract_signature_info(MockSig(), MockStatus())
    assert sig_data.get("timestamp") is None

    # 3. Pass the data to the formatter and capture the stdout
    out_buffer = io.StringIO()
    _print_signature_stanza(sig_data, out_buffer)
    output_text = out_buffer.getvalue()

    # 4. Verify the caller correctly ignored the None value
    assert "SignatureBegin" in output_text  # Ensure it actually printed the stanza
    assert "SignatureTimestamp" not in output_text  # Ensure the timestamp line was safely skipped
