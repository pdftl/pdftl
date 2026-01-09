from unittest.mock import MagicMock, mock_open, patch

import pytest

from pdftl.exceptions import UserCommandLineError
from pdftl.output.sign import parse_sign_options, save_and_sign

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


from unittest.mock import patch


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


from unittest.mock import patch


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
