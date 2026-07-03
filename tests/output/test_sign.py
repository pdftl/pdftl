# tests/output/test_sign.py

from unittest.mock import MagicMock, mock_open, patch

import pytest

from pdftl.exceptions import UserCommandLineError
from pdftl.output.sign import parse_sign_options, save_and_sign

from pathlib import Path

from cryptography.x509.oid import NameOID
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.validation import validate_pdf_signature

from pdftl.cli.main import main


@pytest.fixture
def test_pki(tmp_path):
    from datetime import datetime, timedelta, timezone  # 3.10 compatible

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PDTFL Test")])

    # Use timezone.utc for compatibility with Python 3.10
    now = datetime.now(timezone.utc)

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.ExtendedKeyUsage([x509.ObjectIdentifier("1.2.840.113583.1.1.5")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    key_path = tmp_path / "test_key.pem"
    cert_path = tmp_path / "test_cert.pem"

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    return key_path, cert_path


def test_sign_pipeline_integrity(tmp_path, test_pki):
    key_path, cert_path = test_pki
    input_pdf = Path("tests/assets/2_page.pdf")
    output_pdf = tmp_path / "signed.pdf"

    # NOTE: this test used to reload pdftl.cli.parser, pdftl.cli.main, and
    # pdftl.output.sign here, in the belief that the sign_key/sign_cert
    # options needed a module reload to be "seen" by the parser. That's not
    # how this codebase works: _get_value_keywords()/_get_flag_keywords() in
    # parser.py read registry.options live, on every call, with no caching -
    # there is no module-level VALUE_KEYWORDS constant anywhere to go stale.
    # The sign_key/sign_cert options are registered once at process startup
    # via initialize_registry() and are already visible here with no reload.

    # Mock sys.argv so main() thinks it was called from the CLI
    test_args = [
        "pdftl",
        str(input_pdf),
        "output",
        str(output_pdf),
        "sign_key",
        str(key_path),
        "sign_cert",
        str(cert_path),
    ]

    with patch("sys.argv", test_args):
        # main() usually returns None or 0 on success
        main()

    # Verify Cryptographic Integrity
    with open(output_pdf, "rb") as f:
        reader = PdfFileReader(f)
        # Typically, we want to validate the last signature added
        # If there's only one, it's at index 0
        if not reader.embedded_signatures:
            pytest.fail("No signatures found in the output PDF")

        sig = reader.embedded_signatures[0]
        status = validate_pdf_signature(sig)

        assert status.intact, "Signature digest mismatch - file likely corrupted"
        assert status.valid, "Signature failed cryptographic validation"

        # Verify the algorithm used
        # This ensures pyHanko didn't default to an older algorithm like SHA1
        assert status.md_algorithm == "sha256", f"Expected SHA256, got {status.md_algorithm}"

        # Verify the signer identity using asn1crypto's native dictionary access
        signer_info = status.signing_cert.subject.native
        # signer_info will look like: {'common_name': 'PDTFL Test'}
        assert signer_info["common_name"] == "PDTFL Test"

        # Verify the file coverage
        # ENTIRE_FILE means the signature covers everything except the signature itself
        from pyhanko.sign.validation import SignatureCoverageLevel

        assert status.coverage == SignatureCoverageLevel.ENTIRE_FILE

        # Verify it was an incremental update
        # ModificationLevel.NONE confirms no changes were made to the document
        # structure after the signature was applied.
        from pyhanko.sign.diff_analysis import ModificationLevel

        assert status.diff_result.modification_level == ModificationLevel.NONE


# --- Tests for parse_sign_options ---


def test_parse_sign_options_missing_args():
    with pytest.raises(UserCommandLineError, match="requires both 'sign_key' and 'sign_cert'"):
        parse_sign_options({"sign_key": "key.pem"}, None)


def test_parse_sign_options_env_passphrase(monkeypatch):
    monkeypatch.setenv("MY_PASS_VAR", "secret123")
    options = {
        "sign_key": "k.pem",
        "sign_cert": "c.pem",
        "sign_pass_env": "MY_PASS_VAR",
    }
    cfg = parse_sign_options(options, None)
    assert cfg["passphrase"] == "secret123"


def test_parse_sign_options_env_missing(monkeypatch):
    options = {
        "sign_key": "k.pem",
        "sign_cert": "c.pem",
        "sign_pass_env": "MISSING_VAR",
    }
    monkeypatch.delenv("MISSING_VAR", raising=False)
    with pytest.raises(UserCommandLineError, match="Environment variable MISSING_VAR not found"):
        parse_sign_options(options, None)


def test_parse_sign_options_prompt():
    mock_context = MagicMock()
    mock_context.get_pass.return_value = "prompt_pass"
    options = {"sign_key": "k.pem", "sign_cert": "c.pem", "sign_pass_prompt": True}
    cfg = parse_sign_options(options, mock_context)
    assert cfg["passphrase"] == "prompt_pass"


# --- Tests for save_and_sign ---


@patch("pyhanko.sign.signers.sign_pdf")
@patch("pyhanko.sign.signers.SimpleSigner.load")
@patch("pyhanko.pdf_utils.incremental_writer.IncrementalPdfFileWriter")
def test_save_and_sign_with_encryption(mock_writer_cls, mock_signer_load, mock_sign_pdf):
    # Use a Mock object that simulates pikepdf.Pdf
    mock_pdf = MagicMock()
    mock_enc = MagicMock()
    mock_enc.user = "userpw"
    mock_enc.owner = "ownerpw"

    save_opts = {"encryption": mock_enc}
    sign_cfg = {
        "key": "key.pem",
        "cert": "cert.pem",
        "passphrase": "pass",
        "field": "CustomSignature",
    }

    mock_writer = MagicMock()
    mock_writer_cls.return_value = mock_writer
    mock_signer = MagicMock()
    mock_signer_load.return_value = mock_signer

    with patch("pdftl.output.sign.open", mock_open()):
        save_and_sign(mock_pdf, sign_cfg, save_opts, "out.pdf")

    # Verify pikepdf save used encryption
    mock_pdf.save.assert_called_once()

    # Verify pyHanko writer encryption
    mock_writer.encrypt.assert_called_with(user_pwd=b"userpw")

    # Verify field name in metadata
    args, kwargs = mock_sign_pdf.call_args
    assert args[1].field_name == "CustomSignature"


@patch("pyhanko.sign.signers.sign_pdf")
@patch("pyhanko.sign.signers.SimpleSigner.load")
@patch("pyhanko.pdf_utils.incremental_writer.IncrementalPdfFileWriter")
def test_save_and_sign_default_field(mock_writer_cls, mock_signer_load, mock_sign_pdf):
    """Tests line 83: Default signature field name fallback."""
    # Mocking the PDF object instead of passing a Path
    mock_pdf = MagicMock()

    sign_cfg = {"key": "k.pem", "cert": "c.pem", "passphrase": None, "field": None}

    with patch("pdftl.output.sign.open", mock_open()):
        save_and_sign(mock_pdf, sign_cfg, {}, "out.pdf")

    args, _ = mock_sign_pdf.call_args
    # Verify fallback to "Signature1"
    assert args[1].field_name == "Signature1"


@patch("pyhanko.sign.signers.SimpleSigner.load")
@patch("pyhanko.sign.signers.sign_pdf")
@patch("pyhanko.pdf_utils.incremental_writer.IncrementalPdfFileWriter")
def test_save_and_sign_full_flow(mock_writer, mock_sign, mock_load):
    mock_pdf = MagicMock()
    mock_save_opts = {"encryption": None}
    sign_cfg = {"key": "key.pem", "cert": "cert.pem", "passphrase": "password", "field": "MySig"}

    # This ensures line 60-61 in sign.py is executed
    save_and_sign(mock_pdf, sign_cfg, mock_save_opts, "out.pdf")

    mock_load.assert_called_once_with("key.pem", "cert.pem", key_passphrase=b"password")
    assert mock_sign.called


def test_save_and_sign_encryption_success_path():
    """Hits line 42 by simulating a buffer that contains /Encrypt."""
    mock_pdf = MagicMock()

    # 1. Create a mock encryption object
    mock_enc = MagicMock()
    mock_enc.user = "user_pw"
    mock_enc.owner = "owner_pw"

    save_opts = {"encryption": mock_enc}
    sign_cfg = {"key": "dummy.pem", "cert": "dummy.pem", "passphrase": "pass", "field": "Sig1"}

    # 2. Define what happens when pdf.save(buffer, ...) is called
    def side_effect_save(buffer, **kwargs):
        # Write the specific string required to trigger line 42
        buffer.write(b"some pdf data /Encrypt more data")
        return None

    mock_pdf.save.side_effect = side_effect_save

    # 3. Patch the external dependencies and the logger
    with (
        patch("pyhanko.pdf_utils.incremental_writer.IncrementalPdfFileWriter"),
        patch("pyhanko.sign.signers.SimpleSigner.load"),
        patch("pyhanko.sign.signers.sign_pdf"),
        patch("pdftl.output.sign.logger") as mock_logger,
    ):
        save_and_sign(mock_pdf, sign_cfg, save_opts, "out.pdf")

        # Verify line 42 was hit by checking the logger call
        mock_logger.debug.assert_any_call("SUCCESS: Buffer is now encrypted.")


def test_sign_load_failure_coverage():
    """Hit sign.py:67 - Failed to load signing key and certificate."""
    from unittest.mock import MagicMock, patch

    from pdftl.exceptions import UserCommandLineError
    from pdftl.output.sign import save_and_sign

    mock_pdf = MagicMock()
    sign_cfg = {"key": "k", "cert": "c", "passphrase": "p", "field": "f"}
    save_opts = {"encryption": None}

    # Intercept pyhanko's load method to return None
    with (
        patch("pyhanko.sign.signers.SimpleSigner.load", return_value=None),
        patch("pyhanko.pdf_utils.incremental_writer.IncrementalPdfFileWriter"),
        patch("pdftl.output.sign.io.BytesIO"),
    ):
        with pytest.raises(UserCommandLineError, match="Failed to load signing key"):
            save_and_sign(mock_pdf, sign_cfg, save_opts, "out.pdf")
