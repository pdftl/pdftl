# tests/operations/test_server_burst_zip.py

"""Tests for zip-bundling of generator-shaped OpResult.data (e.g. burst)
when crossing the subprocess/HTTP boundary."""

import io
import zipfile
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.core.core_types import OpResult
from pdftl.server.subprocess_workers import (
    _serialize_generator_as_zip,
    _serialize_operation_result,
    _run_single_operation_in_subprocess,
)
from pdftl.server.handler import PdftlServerRequestHandlerMixIn

from tests.server.server_fixtures import make_pdf_bytes, post_multipart


# ==============================================================================
# Unit tests: _serialize_generator_as_zip
# ==============================================================================


def _make_pdf(n_pages=1):
    pdf = pikepdf.new()
    for _ in range(n_pages):
        pdf.add_blank_page()
    return pdf


class TestSerializeGeneratorAsZip:
    def test_bundles_each_yielded_pdf_under_its_filename(self):
        def gen():
            yield "pg_0001.pdf", _make_pdf(1)
            yield "pg_0002.pdf", _make_pdf(1)

        zip_bytes, meta = _serialize_generator_as_zip(gen())

        assert meta == {"kind": "zip", "count": 2}
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            assert sorted(zf.namelist()) == ["pg_0001.pdf", "pg_0002.pdf"]
            for name in zf.namelist():
                with pikepdf.open(io.BytesIO(zf.read(name))) as pdf:
                    assert len(pdf.pages) == 1

    def test_empty_generator_yields_empty_zip_with_zero_count(self):
        def gen():
            return
            yield  # pragma: no cover - unreachable, makes this a generator

        zip_bytes, meta = _serialize_generator_as_zip(gen())

        assert meta == {"kind": "zip", "count": 0}
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            assert zf.namelist() == []

    def test_closes_each_pdf_after_writing_it(self):
        pdf1 = _make_pdf(1)
        pdf1.close = MagicMock(wraps=pdf1.close)

        def gen():
            yield "a.pdf", pdf1

        _serialize_generator_as_zip(gen())

        pdf1.close.assert_called_once()

    def test_closes_pdf_even_if_save_raises(self):
        """A save() failure on one chunk must not leak that pdf's file
        handle -- close() still runs via the inner try/finally."""
        pdf1 = _make_pdf(1)
        pdf1.save = MagicMock(side_effect=OSError("disk full"))
        pdf1.close = MagicMock(wraps=pdf1.close)

        def gen():
            yield "a.pdf", pdf1

        with pytest.raises(OSError, match="disk full"):
            _serialize_generator_as_zip(gen())

        pdf1.close.assert_called_once()

    def test_preserves_multipage_chunk_content(self):
        def gen():
            yield "chunk_1.pdf", _make_pdf(3)

        zip_bytes, meta = _serialize_generator_as_zip(gen())

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            with pikepdf.open(io.BytesIO(zf.read("chunk_1.pdf"))) as pdf:
                assert len(pdf.pages) == 3


# ==============================================================================
# Unit tests: _serialize_operation_result generator detection
# ==============================================================================


class TestSerializeOperationResultGeneratorDetection:
    def test_generator_data_routes_to_zip_serialization(self):
        def gen():
            yield "pg_0001.pdf", _make_pdf(1)

        result = OpResult(success=True, data=gen())
        result_bytes, meta = _serialize_operation_result(result)

        assert meta["kind"] == "zip"
        assert meta["count"] == 1
        with zipfile.ZipFile(io.BytesIO(result_bytes)) as zf:
            assert zf.namelist() == ["pg_0001.pdf"]

    def test_non_generator_data_is_unaffected(self):
        """Regression guard: ordinary dict/list data must not be
        accidentally routed through the new generator branch."""
        result = OpResult(success=True, data={"pages": 3}, meta={"json_output": True})

        class FakeData:
            def to_dict(self):
                return {"pages": 3}

        result = OpResult(success=True, data=FakeData(), meta={"json_output": True})
        result_bytes, meta = _serialize_operation_result(result)

        assert result_bytes is None
        assert meta == {"kind": "data", "data": {"pages": 3}}

    def test_pdf_result_is_unaffected(self):
        result = OpResult(success=True, pdf=_make_pdf(2))
        result_bytes, meta = _serialize_operation_result(result)

        assert meta == {"kind": "pdf"}
        with pikepdf.open(io.BytesIO(result_bytes)) as pdf:
            assert len(pdf.pages) == 2


# ==============================================================================
# Unit tests: handler.py zip response plumbing
# ==============================================================================


class TestHandlerZipResponse:
    def test_send_zip_bytes_sets_expected_headers(self):
        handler = PdftlServerRequestHandlerMixIn()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = MagicMock()

        handler._send_zip_bytes(b"PK\x03\x04fakezipbytes")

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "application/zip")
        handler.send_header.assert_any_call(
            "Content-Disposition", 'attachment; filename="burst_output.zip"'
        )
        handler.send_header.assert_any_call("Content-Length", str(len(b"PK\x03\x04fakezipbytes")))
        handler.end_headers.assert_called_once()
        handler.wfile.write.assert_called_once_with(b"PK\x03\x04fakezipbytes")

    def test_serialize_subprocess_result_dispatches_zip_kind(self):
        handler = PdftlServerRequestHandlerMixIn()
        handler._send_zip_bytes = MagicMock()
        handler._send_pdf_bytes = MagicMock()

        handler._serialize_subprocess_result(b"zipbytes", {"kind": "zip", "count": 3})

        handler._send_zip_bytes.assert_called_once_with(b"zipbytes")
        handler._send_pdf_bytes.assert_not_called()

    def test_serialize_subprocess_result_zip_kind_with_none_bytes_falls_through(self):
        """If kind=='zip' but bytes are somehow None (shouldn't happen in
        practice, but must not crash), it must not call _send_zip_bytes
        with an invalid payload -- falls through to the generic success ack."""
        handler = PdftlServerRequestHandlerMixIn()
        handler._send_zip_bytes = MagicMock()
        handler._send_json = MagicMock()

        handler._serialize_subprocess_result(None, {"kind": "zip", "count": 0})

        handler._send_zip_bytes.assert_not_called()
        handler._send_json.assert_called_once_with({"success": True})


# ==============================================================================
# In-process integration: _run_single_operation_in_subprocess with a real
# generator-returning operation (mocked run_operation, real zip pipeline)
# ==============================================================================


def test_run_single_operation_in_subprocess_zips_generator_result():
    def fake_generator():
        yield "pg_0001.pdf", _make_pdf(1)
        yield "pg_0002.pdf", _make_pdf(1)

    with patch(
        "pdftl.core.executor.run_operation",
        return_value=OpResult(success=True, data=fake_generator()),
    ):
        result_bytes, meta = _run_single_operation_in_subprocess("burst", [], [])

    assert meta == {"kind": "zip", "count": 2}
    with zipfile.ZipFile(io.BytesIO(result_bytes)) as zf:
        assert sorted(zf.namelist()) == ["pg_0001.pdf", "pg_0002.pdf"]


# ==============================================================================
# End-to-end: real burst request over HTTP returns a valid zip
# ==============================================================================


@pytest.fixture(autouse=True)
def _no_shutdown_token(monkeypatch):
    monkeypatch.delenv("PDFTL_SERVER_SHUTDOWN_TOKEN", raising=False)


def test_server_burst_returns_zip_of_all_pages(server) -> None:
    """A real burst request (default: one file per page) round-trips
    through the actual subprocess and returns a zip containing one valid
    single-page PDF per input page."""
    ms = server()
    base_url = ms.base_url

    pdf_bytes = make_pdf_bytes(3)

    with post_multipart(f"{base_url}/v1/execute/burst", {"file": pdf_bytes}, "[]") as response:
        assert response.status == 200
        assert response.headers["Content-Type"] == "application/zip"
        assert "burst_output.zip" in response.headers.get("Content-Disposition", "")
        zip_bytes = response.read()

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = sorted(zf.namelist())
        assert len(names) == 3
        for name in names:
            with pikepdf.open(io.BytesIO(zf.read(name))) as pdf:
                assert len(pdf.pages) == 1


def test_server_burst_with_split_spec_returns_multi_page_chunks(server) -> None:
    """burst step2 on a 4-page doc should produce 2 chunks of 2 pages each,
    bundled in the same zip response."""
    ms = server()
    base_url = ms.base_url

    pdf_bytes = make_pdf_bytes(4)

    with post_multipart(
        f"{base_url}/v1/execute/burst", {"file": pdf_bytes}, '["step2"]'
    ) as response:
        assert response.status == 200
        zip_bytes = response.read()

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = sorted(zf.namelist())
        assert len(names) == 2
        for name in names:
            with pikepdf.open(io.BytesIO(zf.read(name))) as pdf:
                assert len(pdf.pages) == 2
