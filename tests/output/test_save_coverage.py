import logging
from unittest.mock import MagicMock, patch

import pytest

from pdftl.output.save import _get_passwords_from_options, save_pdf


def test_save_to_stdout_success(minimal_pdf):
    """
    Covers save.py logic: Save to buffer, write buffer to sys.stdout.buffer
    """
    # Fix 1: Patch sys.stdout and attach a mock buffer
    with patch("sys.stdout") as mock_stdout:
        mock_buffer = MagicMock()
        mock_stdout.buffer = mock_buffer

        # Fix 2: Create a mock for the required 'input_context' argument
        mock_ctx = MagicMock()

        # Fix 3: Use correct argument names: 'output_filename' instead of 'output_path'
        save_pdf(minimal_pdf, output_filename="-", input_context=mock_ctx)

        # Verify write was called
        assert mock_buffer.write.called


def test_save_encryption_metadata_aes_flag(minimal_pdf):
    """
    Covers lines 267-268:
    If 'no_encrypt_metadata' is set and we use AES, metadata encryption should be disabled.
    """
    mock_ctx = MagicMock()
    # Use AES-128 (default if just passwords, but explicit flag helps clarity)
    options = {"encrypt_aes128": True, "owner_pw": "secret", "no_encrypt_metadata": True}

    # Patch pikepdf.Encryption to inspect the kwargs passed to it
    with (
        patch("pikepdf.Encryption") as MockEncryption,
        patch.object(minimal_pdf, "save"),
    ):
        save_pdf(minimal_pdf, output_filename="dummy.pdf", input_context=mock_ctx, options=options)

        # Verify the Encryption constructor was called with metadata=False
        call_kwargs = MockEncryption.call_args.kwargs
        assert call_kwargs.get("metadata") is False
        assert call_kwargs.get("aes") is True


def test_save_encryption_metadata_rc4_warning(minimal_pdf, caplog):
    """
    Covers lines 269-270:
    If 'no_encrypt_metadata' is set but we use RC4 (non-AES),
    it should log a warning because RC4 cannot selectively encrypt metadata.
    """
    mock_ctx = MagicMock()
    # encrypt_128bit corresponds to RC4 (aes=False in the map)
    options = {"encrypt_128bit": True, "owner_pw": "secret", "no_encrypt_metadata": True}

    # We expect a logger warning
    with caplog.at_level(logging.WARNING):
        # We don't need to mock Encryption perfectly, just ensure the code runs to the logging point
        with patch("pikepdf.Encryption"), patch.object(minimal_pdf, "save"):
            save_pdf(
                minimal_pdf, output_filename="dummy.pdf", input_context=mock_ctx, options=options
            )

    # Check for the specific warning message
    assert "Ignoring 'no_encrypt_metadata'" in caplog.text
    assert "encrypt_128bit" in caplog.text


def test_save_sign_to_stdout_error(minimal_pdf):
    """
    Covers line 424:
    If options include signing flags (sign_*) and output is stdout ('-'),
    it should raise NotImplementedError.
    """
    mock_ctx = MagicMock()
    # Any option starting with 'sign_' triggers the is_signing flag
    options = {"sign_page": 1, "sign_cert": "me.p12"}

    with pytest.raises(
        NotImplementedError, match="Signing and saving to stdout is not yet implemented"
    ):
        save_pdf(minimal_pdf, output_filename="-", input_context=mock_ctx, options=options)


def test_password_prompt_truncation():
    """
    Covers lines 192-195: Interactive password prompt handling.
    Verifies that passwords > 32 chars are truncated and a warning is logged.
    """
    # Setup
    options = {"user_pw": "PROMPT"}
    mock_input_context = MagicMock()

    # Return a 33-character string
    long_password = "x" * 33
    mock_input_context.get_pass.return_value = long_password

    # Mock the logger to verify the warning
    with patch("pdftl.output.save.logger") as mock_logger:
        passwords = _get_passwords_from_options(options, mock_input_context)

    # Assertions
    # 1. Password should be truncated to 32 chars
    expected_password = "x" * 32
    assert passwords["user"] == expected_password
    assert len(passwords["user"]) == 32

    # 2. Warning should be logged
    mock_logger.warning.assert_called_once()
    assert "truncated" in mock_logger.warning.call_args[0][0]


def test_save_pdf_triggers_font_replacement():
    """
    Covers line 415: save_pdf calling replace_form_fonts.
    """
    # Setup
    mock_pdf = MagicMock()
    options = {"replacement_font": "/path/to/custom_font.ttf"}
    output_filename = "output.pdf"
    mock_context = MagicMock()

    # We need to patch the external dependency functions so save_pdf can run
    with (
        patch("pdftl.output.save.replace_form_fonts") as mock_replace,
        patch("pdftl.output.save._build_save_options", return_value={}),
        patch("pdftl.output.save._remove_source_info"),
        patch("pdftl.output.save._action_drop_flags"),
    ):
        # Execute
        save_pdf(mock_pdf, output_filename, mock_context, options=options)

        # Assertion
        mock_replace.assert_called_once_with(mock_pdf, "/path/to/custom_font.ttf")
