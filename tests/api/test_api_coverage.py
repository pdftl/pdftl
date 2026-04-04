# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl import api
from pdftl.core.types import OpResult
from pdftl.exceptions import OperationError

# ==================================================================
# === Input Normalization Tests (Lines 32-83)
# ==================================================================


class TestInputNormalization:
    def test_normalize_only_opened_pdfs(self):
        """Hit lines 44-46: No string inputs, only pre-opened PDFs."""
        mock_pdf = MagicMock(spec=pikepdf.Pdf)
        user_opened = {0: mock_pdf, 2: mock_pdf}  # Gaps in index

        inputs, opened = api._normalize_inputs(None, user_opened, None)

        # Should generate placeholders up to max index
        assert len(inputs) == 3
        assert inputs[0] == "<obj-0>"
        assert inputs[1] == "<obj-1>"
        assert inputs[2] == "<obj-2>"
        assert opened == user_opened

    def test_normalize_explicit_overlap(self):
        """Hit line 63: Input list has index explicitly in opened dict."""
        mock_pdf = MagicMock(spec=pikepdf.Pdf)
        user_inputs = ["file1.pdf", "file2.pdf"]
        user_opened = {1: mock_pdf}

        with patch("pikepdf.open") as mock_open:
            inputs, opened = api._normalize_inputs(user_inputs, user_opened, None)
            assert inputs[1] == "<explicit-obj-1>"
            assert opened[1] is mock_pdf

    def test_normalize_open_failure(self):
        """Hit lines 71-72: File open failure."""
        with patch("pikepdf.open", side_effect=FileNotFoundError("Mock fail")):
            with pytest.raises(ValueError, match="Failed to open input"):
                api._normalize_inputs(["bad.pdf"], {}, None)

    def test_normalize_invalid_type(self):
        """Hit line 79: Invalid input type."""
        with pytest.raises(TypeError, match="must be a file path or pikepdf.Pdf"):
            api._normalize_inputs([12345], {}, None)


# ==================================================================
# === Call & Return Logic Tests (Lines 86-142)
# ==================================================================


class TestApiCall:
    @patch("pdftl.core.executor.run_operation")
    def test_call_password_propagation(self, mock_run):
        """Hit line 112: Password passed to context."""
        api.call("noop", password="secret")
        call_args = mock_run.call_args[0][1]
        assert call_args["input_password"] == "secret"

    @patch("pdftl.core.executor.run_operation")
    def test_call_failure(self, mock_run):
        """Hit lines 130-131: Operation failure raises exception."""
        mock_run.return_value = OpResult(success=False, summary="Broken")
        with pytest.raises(OperationError, match="Operation 'noop' failed: Broken"):
            api.call("noop")

    @patch("pdftl.core.executor.run_operation")
    def test_call_logging(self, mock_run, caplog):
        """Hit line 133-134: Summary logging. Fixes random race conditions in xdist."""
        mock_run.return_value = OpResult(success=True, summary="Did things")

        # We explicitly target the 'pdftl' logger which api.call uses
        with caplog.at_level(logging.INFO, logger="pdftl"):
            api.call("noop")

            # Check records directly for better reliability across environments
            log_messages = [rec.message for rec in caplog.records]
            assert any("[noop] Did things" in msg for msg in log_messages)

    @patch("pdftl.core.executor.run_operation")
    def test_call_returns(self, mock_run):
        """Hit lines 136-142: Various return types."""
        res_full = OpResult(success=True, data="D", pdf="P")
        mock_run.return_value = res_full

        assert api.call("noop", full_result=True) == res_full
        assert api.call("noop") == "D"

        mock_run.return_value = OpResult(success=True, data="DataOnly")
        assert api.call("noop") == "DataOnly"

        mock_run.return_value = OpResult(success=True, pdf="PdfOnly")
        assert api.call("noop") == "PdfOnly"


# ==================================================================
# === Dynamic Attributes & Introspection (Lines 180-215)
# ==================================================================


class TestIntrospection:
    def test_dir_exposure(self):
        """Hit line 209: __dir__ exposes operations."""
        keys = dir(api)
        assert "call" in keys
        # Ensure it contains dynamic operations from the registry
        from pdftl.core.executor import registry

        for op_name in registry.operations.keys():
            assert op_name in keys

    def test_docstring_fallback(self):
        """Hit lines 196-197: Docstring from 'desc' vs 'long_desc'."""
        fake_op = MagicMock()
        fake_op.desc = "Short desc"
        # Ensure long_desc doesn't exist to trigger fallback
        if hasattr(fake_op, "long_desc"):
            del fake_op.long_desc
        fake_op.function = None

        with patch.dict("pdftl.core.executor.registry.operations", {"fake_op": fake_op}):
            func = api.fake_op
            assert func.__doc__ == "Short desc"

    def test_getattr_missing(self):
        """Hit line 204: Attribute error."""
        with pytest.raises(AttributeError, match="has no attribute 'invalid_op'"):
            _ = api.invalid_op


import io


def test_process_user_input_with_bytes(temp_pdf):
    """
    Hits api.py:71-72 by passing raw PDF bytes.
    """
    # 1. Create a minimal PDF in memory and get its bytes
    out_buf = io.BytesIO()
    temp_pdf.add_blank_page()
    temp_pdf.save(out_buf)
    pdf_bytes = out_buf.getvalue()

    # 2. Call the API using these bytes as the input
    # We use a simple operation name like 'info' or any registered op
    # This triggers _normalize_inputs -> _process_user_input
    # which sees the 'bytes' type and hits line 71.

    # Using 'call' is the cleanest way to hit the whole flow
    # We'll mock the internal executor so we don't actually need
    # the operation to do anything.
    from unittest.mock import patch

    from pdftl.core.types import OpResult

    with patch("pdftl.core.executor.run_operation") as mock_run:
        mock_run.return_value = OpResult(success=True, summary="Success", pdf=temp_pdf)

        # Passing bytes here hits line 71
        api.call("info", pdf_bytes)

    # 3. Verification
    # No assertion needed for coverage, but let's ensure it didn't crash
    assert mock_run.called


import pdftl.api


def test_api_metadata():
    """
    Ensures the module docstring and metadata are loaded.
    This clears line 7 (docstring) coverage.
    """
    assert pdftl.api.__doc__ is not None
    assert "API layer" in pdftl.api.__doc__
