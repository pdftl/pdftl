import sys
from unittest.mock import MagicMock, patch

import pytest

from pdftl.output.save import save_pdf


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


def test_save_to_stdout_broken_pipe(minimal_pdf):
    """
    Covers save.py logic: Handle BrokenPipeError gracefully
    """
    # Fix 4: Patch sys.stderr as well, because the code calls sys.stderr.close() on error
    with patch("sys.stdout") as mock_stdout, patch("sys.stderr") as mock_stderr:

        # Configure the buffer.write to raise BrokenPipeError
        mock_buffer = MagicMock()
        mock_buffer.write.side_effect = BrokenPipeError
        mock_stdout.buffer = mock_buffer

        mock_ctx = MagicMock()

        # Call with correct signature
        save_pdf(minimal_pdf, output_filename="-", input_context=mock_ctx)

        # Verify that the code handled the error by closing stderr (as per source code)
        assert mock_stderr.close.called


import logging
from unittest.mock import MagicMock, patch

import pytest

from pdftl.output.save import save_pdf


def test_save_encryption_metadata_aes_flag(minimal_pdf):
    """
    Covers lines 267-268:
    If 'no_encrypt_metadata' is set and we use AES, metadata encryption should be disabled.
    """
    mock_ctx = MagicMock()
    # Use AES-128 (default if just passwords, but explicit flag helps clarity)
    options = {"encrypt_aes128": True, "owner_pw": "secret", "no_encrypt_metadata": True}

    # Patch pikepdf.Encryption to inspect the kwargs passed to it
    with patch("pikepdf.Encryption") as MockEncryption:
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
        with patch("pikepdf.Encryption"):
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
