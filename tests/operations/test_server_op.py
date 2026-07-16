# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/test_server_op.py

"""Integration test suite for stateless server mode operation."""

import io

import os
import tempfile
import errno
import json
import threading
import time
import urllib.error
import urllib.request
import socket
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.exceptions import OperationError, UserCommandLineError
from pdftl.operations.server_op import (
    run_server_op,
    _parse_server_args,
    _attempt_replace_existing,
    _require_shutdown_token_if_non_loopback,
    _sweep_stale_pipeline_temp_files,
)
from pdftl.server import (
    _run_single_operation_in_subprocess,
    PdftlServerRequestHandlerMixIn,
)

from pdftl.server.multipart import (
    parse_multipart_segment,
    parse_multipart_payload,
    split_multipart_segments,
    cleanup_uploaded_files,
    iter_body_chunks,
)
from tests.server.server_fixtures import (
    wait_until_down,
    get_free_port,
    make_pdf_bytes,
    post_multipart,
)


@pytest.fixture(autouse=True)
def _no_shutdown_token(monkeypatch):
    monkeypatch.delenv("PDFTL_SERVER_SHUTDOWN_TOKEN", raising=False)


# The server_test_* throwaway operations formerly registered here don't
# work with the subprocess-based _run_with_timeout: a spawned child
# reimports pdftl fresh and never sees operations registered dynamically
# by a test fixture in the parent process, so an end-to-end HTTP test
# against them would just 404. Instead, the behaviors they used to
# exercise (exception -> HTTP status mapping, result-shape serialization)
# are tested directly against _run_single_operation_in_subprocess below --
# that function runs in-process here, so ordinary mocking works, and it's
# actually the more precise place to test this logic anyway since it
# isolates serialization/error-translation from HTTP and process plumbing.
# No operations are registered anywhere for this purpose anymore -- see
# tests/operations/conftest.py, which no longer defines any server_test_*
# ops either, for the same reason.


def test_server_builder_page_served(server) -> None:
    """Verifies GET /builder serves the static pipeline-builder HTML page."""
    ms = server()
    base_url = ms.base_url

    with urllib.request.urlopen(f"{base_url}/builder") as response:
        assert response.status == 200
        assert "text/html" in response.headers.get("Content-Type", "")
        body = response.read()
        assert b"<html" in body.lower() or b"<!doctype" in body.lower()


# ==============================================================================
# Shared helpers
# ==============================================================================

# Ignore errors if the server is already down and unreachable.
# (moved to tests/operations/conftest.py: wait_for_server, wait_until_down,
#  shutdown_server, get_free_port, and the `server` fixture)


# ==============================================================================
# Comprehensive Server Test Suite Case Definitions
# ==============================================================================


def test_server_status_and_execution_flow(server) -> None:
    """Spawns the pdftl server, validates status queries, and tests running operations dynamically."""
    ms = server()
    base_url = ms.base_url

    # 1. Test GET /v1/status
    with urllib.request.urlopen(f"{base_url}/v1/status") as response:
        assert response.status == 200
        data = json.loads(response.read().decode("utf-8"))
        assert data["status"] == "online"
        assert "create" in data["operations"]

    # 1b. Root path "/" is aliased to the same status route
    with urllib.request.urlopen(f"{base_url}/") as response:
        assert response.status == 200
        data = json.loads(response.read().decode("utf-8"))
        assert data["status"] == "online"

    # 2. Test POST /v1/execute/create (Zero-input operation)
    boundary = "===BOUNDARY==="
    body_parts = [
        f"--{boundary}",
        'Content-Disposition: form-data; name="args"',
        "",
        '["1(A4)"]',
        f"--{boundary}--",
        "",
    ]
    body_data = "\r\n".join(body_parts).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/v1/execute/create",
        data=body_data,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    with urllib.request.urlopen(req) as response:
        assert response.status == 200
        assert response.headers["Content-Type"] == "application/pdf"
        pdf_bytes = response.read()
        with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
            assert len(pdf.pages) == 1


def test_server_multi_file_concatenation(server) -> None:
    """Verifies that multiple file uploads with handles can be cleanly concatenated using cat."""
    ms = server()
    base_url = ms.base_url

    pdf1 = pikepdf.new()
    pdf1.add_blank_page()
    pdf1_io = io.BytesIO()
    pdf1.save(pdf1_io)
    pdf1_bytes = pdf1_io.getvalue()

    pdf2 = pikepdf.new()
    pdf2.add_blank_page()
    pdf2_io = io.BytesIO()
    pdf2.save(pdf2_io)
    pdf2_bytes = pdf2_io.getvalue()

    boundary = "===MULTI_BOUNDARY==="
    body_parts = [
        f"--{boundary}",
        'Content-Disposition: form-data; name="A"; filename="first.pdf"',
        "Content-Type: application/pdf",
        "",
        pdf1_bytes.decode("latin1"),
        f"--{boundary}",
        'Content-Disposition: form-data; name="B"; filename="second.pdf"',
        "Content-Type: application/pdf",
        "",
        pdf2_bytes.decode("latin1"),
        f"--{boundary}",
        'Content-Disposition: form-data; name="args"',
        "",
        '["A", "B"]',
        f"--{boundary}--",
        "",
    ]
    body_data = "\r\n".join(body_parts).encode("latin1")

    req = urllib.request.Request(
        f"{base_url}/v1/execute/cat",
        data=body_data,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    with urllib.request.urlopen(req) as response:
        assert response.status == 200
        pdf_bytes = response.read()
        with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
            assert len(pdf.pages) == 2


# def test_server_metadata_serialization_modes(server) -> None:
#     """Verifies that PDF info extraction returns text stanzas and structured JSON correctly."""
#     ms = server()
#     base_url = ms.base_url

#     pdf = pikepdf.new()
#     pdf.add_blank_page()
#     pdf_io = io.BytesIO()
#     pdf.save(pdf_io)
#     pdf_bytes = pdf_io.getvalue()

#     # 1. Test Default (No args) -> Should return plaintext stanzas
#     boundary = "===STANZA_BOUNDARY==="
#     body_parts = [
#         f"--{boundary}",
#         'Content-Disposition: form-data; name="file"; filename="test.pdf"',
#         "Content-Type: application/pdf",
#         "",
#         pdf_bytes.decode("latin1"),
#         f"--{boundary}--",
#         "",
#     ]
#     body_data = "\r\n".join(body_parts).encode("latin1")

#     req = urllib.request.Request(
#         f"{base_url}/v1/execute/dump_data",
#         data=body_data,
#         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
#     )

#     with urllib.request.urlopen(req) as response:
#         assert response.status == 200
#         assert "text/plain" in response.headers.get("Content-Type", "")
#         raw_text = response.read().decode("utf-8")
#         assert "NumberOfPages:" in raw_text

#     # 2. Test JSON Mode -> Should return structured serialization dictionary
#     body_parts_json = [
#         f"--{boundary}",
#         'Content-Disposition: form-data; name="file"; filename="test.pdf"',
#         "Content-Type: application/pdf",
#         "",
#         pdf_bytes.decode("latin1"),
#         f"--{boundary}",
#         'Content-Disposition: form-data; name="args"',
#         "",
#         '["json"]',
#         f"--{boundary}--",
#         "",
#     ]
#     body_data_json = "\r\n".join(body_parts_json).encode("latin1")

#     req_json = urllib.request.Request(
#         f"{base_url}/v1/execute/dump_data",
#         data=body_data_json,
#         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
#     )

#     with urllib.request.urlopen(req_json) as response:
#         assert response.status == 200
#         assert "application/json" in response.headers.get("Content-Type", "")
#         json_data = json.loads(response.read().decode("utf-8"))
#         assert json_data["success"] is True
#         assert "result" in json_data
#         assert json_data["result"]["NumberOfPages"] == 1


def test_attempt_replace_existing_sends_shutdown_request() -> None:
    """Verifies _attempt_replace_existing POSTs to /v1/shutdown and pauses
    briefly afterward to let the OS free the port -- exercised as a pure
    unit test, with no real sockets or server threads involved."""
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__.return_value = mock_response

    with (
        patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen,
        patch("pdftl.operations.server_op.time.sleep") as mock_sleep,
    ):
        _attempt_replace_existing("127.0.0.1", 4080)

    called_req = mock_urlopen.call_args[0][0]
    assert called_req.full_url == "http://127.0.0.1:4080/v1/shutdown"
    assert called_req.method == "POST"
    mock_sleep.assert_called_once_with(1.0)


def test_attempt_replace_existing_ignores_connection_errors() -> None:
    """If nothing is listening on the target port, _attempt_replace_existing
    swallows the connection error silently rather than raising."""
    with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError()):
        _attempt_replace_existing("127.0.0.1", 4080)  # must not raise


def test_server_replace_port_handling(server) -> None:
    """Verifies that launching the server with the 'replace' keyword overrides
    port bindings, end-to-end. The shutdown-request logic itself is covered
    by the faster, socket-free unit tests above; this test only needs to
    confirm the real rebind actually succeeds."""
    server_host = "127.0.0.1"
    ms_a = server(port=get_free_port(), host=server_host)
    server_port = ms_a.port
    base_url = f"http://{server_host}:{server_port}"

    def run_server_b() -> None:
        try:
            run_server_op([f"port={server_port}", f"host={server_host}", "replace"])
        except Exception:
            pass

    server_thread_b = threading.Thread(target=run_server_b, daemon=True)
    server_thread_b.start()

    # There's a real gap between server A going down (after the shutdown
    # POST) and server B actually binding (it sleeps ~1s first, to let the
    # OS free the port). A single connect can land in that gap even after
    # wait_for_server reports "up" -- e.g. if it caught server A still
    # accepting mid-shutdown. Retry the actual status check itself rather
    # than trusting one probe.
    deadline = time.monotonic() + 15.0
    last_error: Exception | None = None
    data = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/v1/status", timeout=1.0) as response:
                assert response.status == 200
                data = json.loads(response.read().decode("utf-8"))
                break
        except (urllib.error.URLError, ConnectionRefusedError, OSError) as exc:
            last_error = exc
            time.sleep(0.1)
    assert data is not None, f"Server B never came up in time: {last_error}"
    assert data["status"] == "online"

    # The `server` fixture teardown will call shutdown on ms_a's thread object,
    # but that thread has already been replaced by server_thread_b's bind;
    # explicitly join the manually-created thread B here since the fixture
    # doesn't track it.
    server_thread_b.join(timeout=5.0)


def test_server_error_paths(server) -> None:
    """Verifies that error status codes are returned for incorrect requests."""
    ms = server()
    base_url = ms.base_url

    # 1. Test GET 404
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}/v1/invalid_get_endpoint")
    assert exc_info.value.code == 404

    # 2. Test POST 404
    req_bad_post = urllib.request.Request(f"{base_url}/v1/invalid_post_endpoint", data=b"")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_bad_post)
    assert exc_info.value.code == 404

    # 3. Test execution of missing operation
    req_missing_op = urllib.request.Request(f"{base_url}/v1/execute/ghost_operation", data=b"")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_missing_op)
    assert exc_info.value.code == 404

    # 4. Test bad request content-type guard
    req_bad_ct = urllib.request.Request(
        f"{base_url}/v1/execute/create",
        data=b"raw-unsupported-data",
        headers={"Content-Type": "application/octet-stream"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_bad_ct)
    assert exc_info.value.code == 400


def test_server_command_argument_parsing() -> None:
    """Verifies that command line parsing correctly interprets server configuration bounds."""
    host, port, replace, max_mb, timeout = _parse_server_args(
        ["port=8080", "host=0.0.0.0", "replace", "max_upload_mb=50", "timeout=60"]
    )
    assert host == "0.0.0.0"
    assert port == 8080
    assert replace is True
    assert max_mb == 50 * 1024 * 1024
    assert timeout == 60

    host, port, replace, max_mb, timeout = _parse_server_args([])
    assert host == "127.0.0.1"
    assert port == 4080
    assert replace is False

    with pytest.raises(UserCommandLineError, match="[Ii]nvalid value.*port"):
        _parse_server_args(["port=invalid_port_number"])

    with pytest.raises(UserCommandLineError, match="Unexpected argument"):
        _parse_server_args(["unsupported_argument"])

    with pytest.raises(UserCommandLineError, match="[Ii]nvalid value.*max_upload_mb"):
        _parse_server_args(["max_upload_mb=abc"])

    with pytest.raises(UserCommandLineError, match="[Ii]nvalid value.*max_upload_mb"):
        _parse_server_args(["max_upload_mb=-1"])

    with pytest.raises(UserCommandLineError, match="[Ii]nvalid value.*timeout"):
        _parse_server_args(["timeout=abc"])

    with pytest.raises(UserCommandLineError, match="[Ii]nvalid value.*timeout"):
        _parse_server_args(["timeout=0"])


def test_server_operational_error_on_collision(server) -> None:
    """Verifies that trying to bind on an occupied port raises a clean OperationError."""
    ms = server(port=get_free_port())

    with pytest.raises(OperationError) as exc_info:
        run_server_op([f"port={ms.port}", f"host={ms.host}"])
    assert "Failed to start server on" in str(exc_info.value)


def test_server_dispatcher_edge_cases(server) -> None:
    """Tests exception handling, nameless parts, split argument fallbacks, and serialization variations."""
    ms = server()
    base_url = ms.base_url

    # Cases 1 & 2 (exception -> HTTP status mapping) moved to
    # test_run_single_operation_in_subprocess_error_mapping below.

    # 3. Test nameless part inside multipart payload parser
    boundary_nameless = "===NAMELESS_BOUNDARY==="
    body_parts_nameless = [
        f"--{boundary_nameless}",
        'Content-Disposition: form-data; filename="no_name.pdf"',
        "Content-Type: application/pdf",
        "",
        "pdf_data",
        f"--{boundary_nameless}--",
        "",
    ]
    body_data_nameless = "\r\n".join(body_parts_nameless).encode("utf-8")
    req_nameless = urllib.request.Request(
        f"{base_url}/v1/execute/create",
        data=body_data_nameless,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary_nameless}"},
    )
    with urllib.request.urlopen(req_nameless) as response:
        assert response.status == 200

    # 4. Test corrupted PDF payload in _initialize_pdfs
    boundary_corrupt = "===CORRUPT_BOUNDARY==="
    body_parts_corrupt = [
        f"--{boundary_corrupt}",
        'Content-Disposition: form-data; name="file"; filename="corrupted.pdf"',
        "Content-Type: application/pdf",
        "",
        "Corrupt plaintext mock payload - not a valid PDF structure",
        f"--{boundary_corrupt}--",
        "",
    ]
    body_data_corrupt = "\r\n".join(body_parts_corrupt).encode("latin1")
    req_corrupt = urllib.request.Request(
        f"{base_url}/v1/execute/cat",
        data=body_data_corrupt,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary_corrupt}"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_corrupt)
    assert exc_info.value.code == 400
    assert "unable to find trailer dictionary" in exc_info.value.read().decode("utf-8")

    # X. Test multipart payload missing a boundary parameter in Content-Type
    req_no_boundary = urllib.request.Request(
        f"{base_url}/v1/execute/create",
        data=b"irrelevant-body",
        headers={"Content-Type": "multipart/form-data"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_no_boundary)
    assert exc_info.value.code == 400
    assert "boundary" in exc_info.value.read().decode("utf-8").lower()

    # 5. Test args as a JSON string/scalar that is NOT a list
    boundary_not_list = "===NOT_LIST_BOUNDARY==="
    body_parts_not_list = [
        f"--{boundary_not_list}",
        'Content-Disposition: form-data; name="args"',
        "",
        '"1(A4)"',
        f"--{boundary_not_list}--",
        "",
    ]
    body_data_not_list = "\r\n".join(body_parts_not_list).encode("latin1")
    req_not_list = urllib.request.Request(
        f"{base_url}/v1/execute/create",
        data=body_data_not_list,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary_not_list}"},
    )
    with urllib.request.urlopen(req_not_list) as response:
        assert response.status == 200

    # 6. Test args parser falling back to raw comma-split on JSON decode failure.
    boundary_fallback = "===FALLBACK_BOUNDARY==="
    body_parts_fallback = [
        f"--{boundary_fallback}",
        'Content-Disposition: form-data; name="args"',
        "",
        "1-end,right",
        f"--{boundary_fallback}--",
        "",
    ]
    body_data_fallback = "\r\n".join(body_parts_fallback).encode("latin1")
    req_fallback = urllib.request.Request(
        f"{base_url}/v1/execute/create",
        data=body_data_fallback,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary_fallback}"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_fallback)
    assert exc_info.value.code == 400

    # 6b. Confirm the same fallback path succeeds when the split tokens are valid specs
    boundary_fallback_ok = "===FALLBACK_OK_BOUNDARY==="
    body_parts_fallback_ok = [
        f"--{boundary_fallback_ok}",
        'Content-Disposition: form-data; name="args"',
        "",
        "1(A4),2(letter)",
        f"--{boundary_fallback_ok}--",
        "",
    ]
    body_data_fallback_ok = "\r\n".join(body_parts_fallback_ok).encode("latin1")
    req_fallback_ok = urllib.request.Request(
        f"{base_url}/v1/execute/create",
        data=body_data_fallback_ok,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary_fallback_ok}"},
    )
    with urllib.request.urlopen(req_fallback_ok) as response:
        assert response.status == 200
        pdf_bytes = response.read()
        with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
            assert len(pdf.pages) == 3

    # (Cases 7-11, including the corrupt-cleanup case above, all moved to
    # test_run_single_operation_in_subprocess_result_shapes and
    # test_run_single_operation_in_subprocess_swallows_close_failure.)


def test_server_replace_without_preexisting(server) -> None:
    """Verifies replace checks on ports with nothing listening fail cleanly and proceed."""
    ms = server(extra_args=["replace"])
    base_url = ms.base_url

    with urllib.request.urlopen(f"{base_url}/v1/status") as response:
        assert response.status == 200


def test_server_keyboard_interrupt_graceful_exit() -> None:
    """Verifies that catching a KeyboardInterrupt during socket server execution is handled cleanly."""
    server_port = 4117
    server_host = "127.0.0.1"

    # Patch the base class that actually defines serve_forever
    with patch("http.server.HTTPServer.serve_forever", side_effect=KeyboardInterrupt):
        result = run_server_op([f"port={server_port}", f"host={server_host}"])
        assert result.success is True


def test_server_blocked_recursive_server(server) -> None:
    """Verifies that executing the 'server' operation over the API returns HTTP 403 Forbidden."""
    ms = server()
    base_url = ms.base_url

    req = urllib.request.Request(f"{base_url}/v1/execute/server", data=b"{}")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 403
    error_data = json.loads(exc_info.value.read().decode("utf-8"))
    assert "Forbidden" in error_data["error"]


def test_server_shutdown_endpoint_stops_serving(server) -> None:
    """Verifies POST /v1/shutdown actually terminates the server loop end-to-end."""
    ms = server()
    base_url = ms.base_url

    shutdown_req = urllib.request.Request(f"{base_url}/v1/shutdown", data=b"", method="POST")
    with urllib.request.urlopen(shutdown_req) as response:
        assert response.status == 200
        payload = json.loads(response.read().decode("utf-8"))
        assert payload == {"status": "shutting_down"}

    wait_until_down(base_url, timeout=15.0)
    ms.thread.join(timeout=15.0)
    assert not ms.thread.is_alive()


def test_server_pipeline_invalid_content_type(server) -> None:
    """Verifies that executing a pipeline route without a multipart header returns HTTP 400."""
    ms = server()
    base_url = ms.base_url

    req = urllib.request.Request(
        f"{base_url}/v1/execute/pipeline",
        data=b"invalid-payload",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 400
    assert "multipart/form-data" in exc_info.value.read().decode("utf-8")


def test_server_pipeline_execution_success(server) -> None:
    """End-to-end: a real pipeline request round-trips through the actual
    subprocess and returns a valid PDF. Mocking run_pipeline/parse_pipeline_steps
    doesn't work here since the subprocess is a separate 'spawn'-started
    interpreter that never sees this process's mock patches -- see
    test_run_pipeline_in_subprocess_calls_run_pipeline below for that
    layer of coverage instead.
    """
    ms = server()
    base_url = ms.base_url

    boundary = "===PIPELINE_BOUNDARY==="
    body_parts = [
        f"--{boundary}",
        'Content-Disposition: form-data; name="args"',
        "",
        '[{"operation": "create", "args": ["1(A4)"]}]',
        f"--{boundary}--",
        "",
    ]
    body_data = "\r\n".join(body_parts).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/v1/execute/pipeline",
        data=body_data,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req) as response:
        assert response.status == 200
        assert response.headers["Content-Type"] == "application/pdf"
        with pikepdf.open(io.BytesIO(response.read())) as pdf:
            assert len(pdf.pages) == 1


def test_run_pipeline_in_subprocess_calls_run_pipeline() -> None:
    """Unit-level check (in-process, so mocking works normally) that
    _run_pipeline_in_subprocess parses/validates and calls run_pipeline
    with the expected arguments."""
    from pdftl.server import _run_pipeline_in_subprocess

    with patch("pdftl.server.subprocess_workers.run_pipeline") as mock_run:
        mock_run.return_value = b"%PDF-FAKE"
        steps = [{"operation": "create", "args": ["1(A4)"]}]
        result = _run_pipeline_in_subprocess(steps, [])

    assert result == b"%PDF-FAKE"
    mock_run.assert_called_once()


def test_run_single_operation_in_subprocess_error_mapping() -> None:
    """ValueError and RuntimeError raised by run_operation propagate
    unchanged out of _run_single_operation_in_subprocess; it's
    _run_with_error_handling's job (tested separately) to map exception
    type to HTTP status. Exercised in-process, not via a real subprocess,
    since only the translation logic is under test here."""
    with patch("pdftl.core.executor.run_operation", side_effect=ValueError("boom")):
        with pytest.raises(ValueError, match="boom"):
            _run_single_operation_in_subprocess("noop", [], [])

    with patch("pdftl.core.executor.run_operation", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError, match="boom"):
            _run_single_operation_in_subprocess("noop", [], [])


def test_run_single_operation_in_subprocess_result_shapes() -> None:
    """Covers every OpResult shape _run_single_operation_in_subprocess must
    flatten correctly for the trip back across the process boundary:
    dict/list data, plain string/int data, a raw non-OpResult return, an
    empty OpResult, and a PDF result."""
    from pdftl.core.core_types import OpResult

    def _mock_result(data=None, pdf=None):
        return OpResult(success=True, data=data, pdf=pdf)

    with patch(
        "pdftl.core.executor.run_operation", return_value=_mock_result(data={"key": "val"})
    ):
        result_bytes, meta = _run_single_operation_in_subprocess("noop", [], [])
    assert result_bytes is None
    assert meta == {"kind": "data", "data": {"key": "val"}}

    with patch(
        "pdftl.core.executor.run_operation", return_value=_mock_result(data=["a", "b", "c"])
    ):
        result_bytes, meta = _run_single_operation_in_subprocess("noop", [], [])
    assert meta == {"kind": "data", "data": ["a", "b", "c"]}

    with patch("pdftl.core.executor.run_operation", return_value=_mock_result(data="raw string")):
        result_bytes, meta = _run_single_operation_in_subprocess("noop", [], [])
    assert meta == {"kind": "data", "data": "raw string"}

    with patch("pdftl.core.executor.run_operation", return_value=_mock_result(data=42)):
        result_bytes, meta = _run_single_operation_in_subprocess("noop", [], [])
    assert meta == {"kind": "data", "data": 42}

    with patch("pdftl.core.executor.run_operation", return_value=None):
        result_bytes, meta = _run_single_operation_in_subprocess("noop", [], [])
    assert meta == {"kind": "empty"}

    with patch("pdftl.core.executor.run_operation", return_value=OpResult(success=True)):
        result_bytes, meta = _run_single_operation_in_subprocess("noop", [], [])
    assert meta == {"kind": "empty"}

    mock_pdf = pikepdf.new()
    mock_pdf.add_blank_page()
    with patch("pdftl.core.executor.run_operation", return_value=_mock_result(pdf=mock_pdf)):
        result_bytes, meta = _run_single_operation_in_subprocess("noop", [], [])
    assert meta == {"kind": "pdf"}
    with pikepdf.open(io.BytesIO(result_bytes)) as pdf:
        assert len(pdf.pages) == 1


def test_run_single_operation_in_subprocess_swallows_close_failure(tmp_path) -> None:
    """A pikepdf close() failure during subprocess cleanup must not mask
    the real result."""
    from pdftl.core.core_types import OpResult

    pdf_path = tmp_path / "input.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(str(pdf_path))

    def _bad_run_operation(operation, call_context):
        opened = call_context.get("opened_pdfs") or []
        if opened:

            def bad_close():
                raise OSError("Simulated PDF close failure")

            opened[0].close = bad_close
        return OpResult(success=True)

    with patch("pdftl.core.executor.run_operation", side_effect=_bad_run_operation):
        result_bytes, meta = _run_single_operation_in_subprocess(
            "noop", [], [{"name": "file", "filename": "input.pdf", "path": str(pdf_path)}]
        )
    assert meta == {"kind": "empty"}


def test_shutdown_invalid_token() -> None:
    """Verifies that an invalid shutdown token returns a 401 Unauthorized."""
    handler = PdftlServerRequestHandlerMixIn()
    handler.headers = {"X-Shutdown-Token": "wrong-password"}
    handler.send_response = MagicMock()
    handler.end_headers = MagicMock()
    handler.wfile = MagicMock()
    with patch.dict(os.environ, {"PDFTL_SERVER_SHUTDOWN_TOKEN": "correct-password"}):
        handler.do_shutdown()
    handler.send_response.assert_called_once_with(401)
    handler.end_headers.assert_called_once()
    handler.wfile.write.assert_called_once_with(b'{"error": "Invalid shutdown token"}')


def test_shutdown_no_env_var() -> None:
    """Verifies that shutdown usually works."""
    handler = PdftlServerRequestHandlerMixIn()
    handler.headers = {"X-Shutdown-Token": "wrong-password"}
    handler.send_response = MagicMock()
    handler.end_headers = MagicMock()
    handler.wfile = MagicMock()
    handler.send_header = MagicMock()
    handler.server = MagicMock()
    with patch.dict(os.environ, clear=True):
        handler.do_shutdown()
    handler.send_response.assert_called_once_with(200)
    handler.end_headers.assert_called_once()
    handler.server.shutdown.assert_called_once()


def test_server_operational_error_on_raw_socket_collision() -> None:
    """Verifies that binding fails cleanly with OperationError when the port
    is occupied by a plain listening socket (not a pdftl server), exercising
    the bind()-time OSError fallback in run_server_op directly rather than
    the proactive _port_is_occupied() pre-check."""
    host = "127.0.0.1"
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind((host, 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        with pytest.raises(OperationError) as exc_info:
            run_server_op([f"port={port}", f"host={host}"])
        assert "Failed to start server on" in str(exc_info.value)
        assert str(port) in str(exc_info.value)
    finally:
        blocker.close()


def test_server_operational_error_on_bind_race(monkeypatch) -> None:
    """Verifies the except OSError fallback in run_server_op still raises a
    clean OperationError if a bind() collision occurs despite the proactive
    _port_is_occupied() check reporting the port as free (e.g. a race between
    the check and the actual bind call)."""
    host = "127.0.0.1"
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind((host, 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    monkeypatch.setattr("pdftl.operations.server_op._port_is_occupied", lambda h, p: False)
    try:
        with pytest.raises(OperationError) as exc_info:
            run_server_op([f"port={port}", f"host={host}"])
        assert "Failed to start server on" in str(exc_info.value)
    finally:
        blocker.close()


def test_require_shutdown_token_if_non_loopback(monkeypatch) -> None:
    """Verifies security gate blocks empty tokens on public binds (lines 63-64)."""
    _require_shutdown_token_if_non_loopback("127.0.0.1")

    with pytest.raises(OperationError, match="Refusing to bind to non-loopback host"):
        _require_shutdown_token_if_non_loopback("0.0.0.0")

    monkeypatch.setenv("PDFTL_SERVER_SHUTDOWN_TOKEN", "valid-test-token")
    _require_shutdown_token_if_non_loopback("0.0.0.0")


def test_sweep_stale_pipeline_temp_files() -> None:
    """Verifies loose server temp files are routinely cleared on startup if older than 60s."""
    import time

    paths_to_cleanup = []
    # Create both server and upload temp files backdated in time
    for label in ("server", "upload"):
        fd, path = tempfile.mkstemp(prefix=f"pdftl_{label}_", suffix=".pdf")
        os.close(fd)
        paths_to_cleanup.append(path)
        # Backdate the file by 100 seconds to exceed the 60-second threshold
        past_time = time.time() - 600.0
        os.utime(path, (past_time, past_time))
        assert os.path.exists(path)

    # Create a fresh file that should NOT be deleted
    fd_fresh, fresh_path = tempfile.mkstemp(prefix="pdftl_server_", suffix=".pdf")
    os.close(fd_fresh)
    assert os.path.exists(fresh_path)

    try:
        _sweep_stale_pipeline_temp_files()

        # Verify old ones are swept and fresh one remains intact
        for path in paths_to_cleanup:
            assert not os.path.exists(path)
        assert os.path.exists(fresh_path)
    finally:
        if os.path.exists(fresh_path):
            os.unlink(fresh_path)


def test_sweep_stale_pipeline_temp_files_oserror(monkeypatch, tmp_path) -> None:
    """Verifies cleanup handles OSErrors gracefully rather than crashing."""
    # Force tempfile to use a unique, worker-isolated folder for this test
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

    # Mock os.remove to raise OSError only within this worker process
    def mock_remove(path):
        raise OSError("Simulated Permission Denied")

    monkeypatch.setattr("os.remove", mock_remove)

    fd, path = tempfile.mkstemp(prefix="pdftl_server_", suffix=".pdf")
    os.close(fd)

    # Backdate the file so now - mtime > 300 is True, triggering os.remove()
    import time

    os.utime(path, (time.time() - 600, time.time() - 600))

    try:
        _sweep_stale_pipeline_temp_files()  # Must not bubble the OSError
    finally:
        # Defensive cleanup in case the file wasn't deleted
        try:
            os.unlink(path)
        except OSError:
            pass


def test_mixin_run_with_error_handling_timeout() -> None:
    """Verifies _run_with_error_handling properly translates TimeoutError into a 504."""
    handler = PdftlServerRequestHandlerMixIn()
    handler._send_error = MagicMock()

    def raise_timeout():
        raise TimeoutError("Test Pipeline Timed Out")

    handler._run_with_error_handling("test_op", raise_timeout)
    handler._send_error.assert_called_once_with(504, "Test Pipeline Timed Out")


def _sleep_past_timeout() -> None:
    """Module-level (hence picklable under 'spawn') stand-in for a slow
    operation. Must stay top-level -- a test-local closure can't be
    pickled to hand off to a spawned child process."""
    time.sleep(0.2)


def test_mixin_run_with_timeout() -> None:
    """Verifies _run_with_timeout thread abandonment on exceed limits."""
    handler = PdftlServerRequestHandlerMixIn()
    handler.op_timeout_seconds = 0.05  # extremely short bound

    with pytest.raises(TimeoutError, match="exceeded the 0.05s server timeout"):
        handler._run_with_timeout(_sleep_past_timeout, op_name="test_op")


def test_mixin_reject_if_oversized() -> None:
    """Verifies logical threshold flag correctly bounds HTTP payloads."""
    handler = PdftlServerRequestHandlerMixIn()
    handler.max_upload_bytes = 100
    handler._send_error = MagicMock()

    assert handler._reject_if_oversized(50) is False
    handler._send_error.assert_not_called()

    assert handler._reject_if_oversized(150) is True
    handler._send_error.assert_called_once()
    args, kwargs = handler._send_error.call_args
    assert args[0] == 413
    assert "exceeds the server's" in args[1]


def test_server_payload_too_large(server) -> None:
    """Verifies early 413 rejection for oversized payloads over active TCP."""
    ms = server(extra_args=["max_upload_mb=1"])  # 1 MB limit for the test process
    base_url = ms.base_url

    boundary = "===OVERSIZE_BOUNDARY==="
    body_data = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="big.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        ).encode()
        + (b"0" * (1024 * 1024 * 1))
        + (f"\r\n--{boundary}--\r\n").encode()
    )

    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}

    def assert_early_rejection(request: urllib.request.Request, check_body: bool = False) -> None:
        try:
            urllib.request.urlopen(request)
            pytest.fail("Server accepted an oversized payload without error")
        except urllib.error.HTTPError as exc:
            # Expected path on Linux where the stack gracefully reads the response headers
            assert exc.code == 413
            if check_body:
                assert "exceeds the server's" in exc.read().decode("utf-8")
        except urllib.error.URLError as exc:
            # Expected path on macOS/Darwin where the OS TCP stack forcefully tears
            # down the connection (RST) mid-upload when the server closes its socket.
            underlying = exc.reason
            assert isinstance(underlying, OSError), f"Expected OSError, got {type(underlying)}"
            assert underlying.errno in (errno.ECONNRESET, errno.EPIPE), (
                f"Expected ECONNRESET (54) or EPIPE (32), got errno {underlying.errno}"
            )

    # Execute endpoint
    req_exec = urllib.request.Request(
        f"{base_url}/v1/execute/create", data=body_data, headers=headers
    )
    assert_early_rejection(req_exec, check_body=True)

    # Pipeline endpoint
    req_pipe = urllib.request.Request(
        f"{base_url}/v1/execute/pipeline", data=body_data, headers=headers
    )
    assert_early_rejection(req_pipe, check_body=False)


def test_run_single_operation_in_subprocess_json_output_to_dict():
    """json_output=True + a data object with to_dict() takes the to_dict path."""
    from pdftl.core.core_types import OpResult

    class FakeData:
        def to_dict(self):
            return {"a": 1}

    result = OpResult(success=True, data=FakeData(), meta={"json_output": True})

    with patch("pdftl.core.executor.run_operation", return_value=result):
        result_bytes, meta = _run_single_operation_in_subprocess("noop", [], [])
    assert result_bytes is None
    assert meta == {"kind": "data", "data": {"a": 1}}


def test_run_single_operation_in_subprocess_pdfinfo_text():
    """PdfInfo data (by class name) with json_output=False renders via write_info."""
    from pdftl.core.core_types import OpResult

    class PdfInfo:
        pass

    result = OpResult(
        success=True,
        data=PdfInfo(),
        meta={"json_output": False, "escape_xml": True, "extra_info": False},
    )

    def fake_write_info(writer, data, escape_xml, extra_info):
        writer("NumberOfPages: 1")

    with (
        patch("pdftl.core.executor.run_operation", return_value=result),
        patch("pdftl.info.output_info.write_info", side_effect=fake_write_info),
    ):
        result_bytes, meta = _run_single_operation_in_subprocess("noop", [], [])
    assert result_bytes is None
    assert meta == {"kind": "text", "text": "NumberOfPages: 1\n"}


def test_run_single_operation_in_subprocess_data_fallback_str():
    """A data object that's not dict/list/str/int/float/bool/None falls
    through to str()."""
    from pdftl.core.core_types import OpResult

    class Weird:
        def __str__(self):
            return "weird-repr"

    with patch(
        "pdftl.core.executor.run_operation",
        return_value=OpResult(success=True, data=Weird()),
    ):
        result_bytes, meta = _run_single_operation_in_subprocess("noop", [], [])
    assert meta == {"kind": "data", "data": "weird-repr"}


def test_run_pipeline_in_subprocess_swallows_close_failure(tmp_path):
    pdf_path = tmp_path / "input.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(str(pdf_path))

    from pdftl.server import _run_pipeline_in_subprocess

    real_open = pikepdf.open

    def fake_open(path):
        obj = real_open(path)
        obj.close = lambda: (_ for _ in ()).throw(OSError("boom"))
        return obj

    with (
        patch("pdftl.server.subprocess_workers.run_pipeline", return_value=b"%PDF-OK"),
        patch("pikepdf.open", side_effect=fake_open),
    ):
        result = _run_pipeline_in_subprocess(
            [], [{"name": "A", "filename": "input.pdf", "path": str(pdf_path)}]
        )
    assert result == b"%PDF-OK"


def test_subprocess_worker_entrypoint_success_and_error():
    import multiprocessing
    import json
    from pdftl.server import _subprocess_worker_entrypoint

    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)

    _subprocess_worker_entrypoint(child_conn, lambda: 42, ())
    meta_bytes = parent_conn.recv_bytes()
    metadata = json.loads(meta_bytes.decode("utf-8"))
    payload_bytes = parent_conn.recv_bytes()
    assert metadata["status"] == "ok"
    assert metadata["meta"] == 42
    assert payload_bytes == b""

    def boom():
        raise ValueError("bad")

    parent_conn2, child_conn2 = ctx.Pipe(duplex=False)
    _subprocess_worker_entrypoint(child_conn2, boom, ())
    meta_bytes2 = parent_conn2.recv_bytes()
    metadata2 = json.loads(meta_bytes2.decode("utf-8"))
    assert metadata2["status"] == "err"
    assert metadata2["error_class"] == "ValueError"
    assert metadata2["message"] == "bad"


def test_mixin_run_with_error_handling_generic_exception():
    handler = PdftlServerRequestHandlerMixIn()
    handler._send_error = MagicMock()

    def raise_generic():
        raise RuntimeError("kaboom")

    handler._run_with_error_handling("test_op", raise_generic)
    handler._send_error.assert_called_once_with(500, "Execution failed: kaboom")


def test_mixin_run_with_timeout_escalates_to_kill():
    handler = PdftlServerRequestHandlerMixIn()
    handler.op_timeout_seconds = 0.05

    mock_proc = MagicMock()
    mock_proc.is_alive.return_value = True
    mock_proc.pid = 1234
    mock_ctx = MagicMock()
    mock_ctx.Process.return_value = mock_proc
    mock_ctx.Pipe.return_value = (MagicMock(poll=lambda *a, **k: False), MagicMock())

    with patch("pdftl.server.handler.multiprocessing.get_context", return_value=mock_ctx):
        with pytest.raises(TimeoutError, match="exceeded the 0.05s server timeout"):
            handler._run_with_timeout(lambda: None, op_name="test_op")

    mock_proc.terminate.assert_called_once()
    mock_proc.kill.assert_called_once()


def test_split_multipart_segments_direct():
    boundary = b"--BOUND"
    body = (
        boundary + b"\r\n"
        b'Content-Disposition: form-data; name="a"\r\n\r\n'
        b"value1\r\n" + boundary + b"--\r\n"
    )
    segments = split_multipart_segments(body, boundary)
    assert len(segments) == 1
    assert b"value1" in segments[0]


def test_parse_multipart_segment_missing_header_terminator():
    result = parse_multipart_segment(b"no header terminator here")
    assert result == (None, None, None)


def test_iter_body_chunks_breaks_on_short_read():
    chunks = list(iter_body_chunks(io.BytesIO(b"short"), 1000))  # requests more than available
    assert b"".join(chunks) == b"short"


def test_parse_multipart_payload_handles_empty_segment_and_tail_trim(monkeypatch):
    monkeypatch.setattr("pdftl.server.multipart._STREAM_CHUNK_SIZE", 16)
    boundary = "BOUND"
    body = (
        f"--{boundary}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="args"\r\n\r\n'
        "some moderately long value that spans several tiny chunks"
        f"\r\n--{boundary}--\r\n"
    ).encode()

    fields, files = parse_multipart_payload(
        io.BytesIO(body), f"multipart/form-data; boundary={boundary}", len(body)
    )
    assert "some moderately long value" in fields["args"]


def test_cleanup_uploaded_files_swallows_oserror():
    with patch("os.remove", side_effect=OSError("denied")):
        cleanup_uploaded_files([{"path": "/nonexistent/file.pdf"}])  # must not raise


def test_serialize_subprocess_result_all_branches():
    handler = PdftlServerRequestHandlerMixIn()
    handler._send_text_response = MagicMock()
    handler._send_json = MagicMock()

    handler._serialize_subprocess_result(None, {"kind": "text", "text": "hello"})
    handler._send_text_response.assert_called_with("hello")

    handler._serialize_subprocess_result(None, {"kind": "data", "data": {"x": 1}})
    handler._send_json.assert_called_with({"success": True, "result": {"x": 1}})

    handler._send_text_response.reset_mock()
    handler._serialize_subprocess_result(None, {"kind": "data", "data": 42})
    handler._send_text_response.assert_called_with("42")

    handler._send_json.reset_mock()
    handler._serialize_subprocess_result(None, {"kind": "empty"})
    handler._send_json.assert_called_with({"success": True})


def test_cleanup_pdfs_swallows_close_failure():
    handler = PdftlServerRequestHandlerMixIn()
    bad_pdf = MagicMock()
    bad_pdf.close.side_effect = OSError("fail")
    handler._cleanup_pdfs([bad_pdf])  # must not raise


def test_parse_multipart_payload_preserves_large_multi_chunk_field(monkeypatch):
    """A field value spanning multiple _STREAM_CHUNK_SIZE reads before its
    delimiter is found must survive intact -- regression test for a bug
    where the old tail-trim discarded in-progress segment bytes down to
    a boundary-length remainder on every chunk that didn't yet contain
    the closing delimiter."""
    monkeypatch.setattr("pdftl.server.multipart._STREAM_CHUNK_SIZE", 16)
    boundary = "BOUND"
    long_value = "some moderately long value that spans several tiny chunks"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="args"\r\n\r\n'
        f"{long_value}"
        f"\r\n--{boundary}--\r\n"
    ).encode()

    fields, files = parse_multipart_payload(
        io.BytesIO(body), f"multipart/form-data; boundary={boundary}", len(body)
    )
    assert fields["args"] == long_value


def test_parse_multipart_payload_skips_empty_and_dashdash_segments():
    boundary = "BOUND"
    # Leading \r\n before the first boundary produces an empty first segment;
    # an immediate "--" between two delimiter matches produces a b"--" segment.
    body = (
        b"\r\n--" + boundary.encode() + b"--"
        b"\r\n--" + boundary.encode() + b"\r\n"
        b'Content-Disposition: form-data; name="args"\r\n\r\n'
        b"payload"
        b"\r\n--" + boundary.encode() + b"--\r\n"
    )

    fields, files = parse_multipart_payload(
        io.BytesIO(body), f"multipart/form-data; boundary={boundary}", len(body)
    )
    assert fields["args"] == "payload"


def test_send_text_response_writes_expected_headers_and_body():
    """Direct (unmocked) exercise of _send_text_response's status/header/
    body-encoding behavior -- test_serialize_subprocess_result_all_branches
    mocks this method out, so it never runs for real there."""
    handler = PdftlServerRequestHandlerMixIn()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.wfile = MagicMock()

    handler._send_text_response("hello world")

    handler.send_response.assert_called_once_with(200)
    handler.send_header.assert_called_once_with("Content-Type", "text/plain; charset=utf-8")
    handler.end_headers.assert_called_once()
    handler.wfile.write.assert_called_once_with(b"hello world")


# ==============================================================================
# Coverage: handler.py internals not reachable via real end-to-end HTTP tests
# ==============================================================================


def test_spawn_worker_start_failure_closes_parent_conn():
    """If ctx.Process.start() raises, _spawn_worker must close parent_conn
    before re-raising, since the caller never receives a handle to close it
    itself (only child_conn's cleanup runs via the `finally` in that case)."""
    handler = PdftlServerRequestHandlerMixIn()

    mock_parent_conn = MagicMock()
    mock_child_conn = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.Pipe.return_value = (mock_parent_conn, mock_child_conn)
    mock_proc = MagicMock()
    mock_proc.start.side_effect = OSError("Cannot allocate memory")
    mock_ctx.Process.return_value = mock_proc

    with pytest.raises(OSError, match="Cannot allocate memory"):
        handler._spawn_worker(mock_ctx, lambda: None, ())

    mock_parent_conn.close.assert_called_once()
    mock_child_conn.close.assert_called_once()


def test_poll_ipc_connection_immediate_timeout():
    """A zero/negative remaining budget on entry returns 'timeout' without
    ever touching the connection."""
    handler = PdftlServerRequestHandlerMixIn()
    handler.op_timeout_seconds = -1
    mock_parent_conn = MagicMock()
    mock_proc = MagicMock()

    status, metadata, payload = handler._poll_ipc_connection(mock_parent_conn, mock_proc)
    assert status == "timeout"
    assert metadata == {}
    assert payload == b""


def test_poll_ipc_connection_corrupt_metadata():
    """Malformed bytes on the pipe (not valid JSON) must surface as a
    transport-level 'err' rather than propagating a raw exception."""
    handler = PdftlServerRequestHandlerMixIn()
    handler.op_timeout_seconds = 5
    mock_parent_conn = MagicMock()
    mock_parent_conn.poll.return_value = True
    mock_parent_conn.recv_bytes.return_value = b"not valid json"
    mock_proc = MagicMock()
    mock_proc.is_alive.return_value = True

    status, metadata, payload = handler._poll_ipc_connection(mock_parent_conn, mock_proc)
    assert status == "err"
    assert metadata["error_class"] == "RuntimeError"
    assert "IPC transport failure" in metadata["message"]
    assert payload == b""


def test_poll_ipc_connection_process_crashed():
    """If the worker process dies and no follow-up data ever arrives, this
    is reported as a crash rather than a silent hang."""
    handler = PdftlServerRequestHandlerMixIn()
    handler.op_timeout_seconds = 5
    mock_parent_conn = MagicMock()
    mock_parent_conn.poll.return_value = False
    mock_proc = MagicMock()
    mock_proc.is_alive.return_value = False

    status, metadata, payload = handler._poll_ipc_connection(mock_parent_conn, mock_proc)
    assert status == "err"
    assert metadata["error_class"] == "RuntimeError"
    assert "crashed unexpectedly" in metadata["message"]


def test_poll_ipc_connection_recovers_from_race():
    """If proc.is_alive() reports dead but a follow-up poll finds pending
    data (a race between the worker exiting and its last write landing),
    the loop must recover and return the real result instead of a false
    'crashed' error."""
    handler = PdftlServerRequestHandlerMixIn()
    handler.op_timeout_seconds = 5
    mock_parent_conn = MagicMock()
    # call1: main poll -> False; call2: race-recheck poll -> True (continue);
    # call3: main poll -> True (recv metadata, continue);
    # call4: main poll -> True (recv payload, return).
    mock_parent_conn.poll.side_effect = [False, True, True, True]
    mock_parent_conn.recv_bytes.side_effect = [
        json.dumps(
            {
                "status": "ok",
                "error_class": None,
                "message": None,
                "meta": 7,
                "is_tuple": False,
            }
        ).encode("utf-8"),
        b"",
    ]
    mock_proc = MagicMock()
    mock_proc.is_alive.return_value = False

    status, metadata, payload = handler._poll_ipc_connection(mock_parent_conn, mock_proc)
    assert status == "ok"
    assert metadata["meta"] == 7
    assert payload == b""


def test_ensure_process_cleanup_kills_after_join_timeout():
    """A worker that's still alive after the join grace period must be
    force-killed and its handle released, mirroring the escalation path
    already covered for the timeout branch."""
    handler = PdftlServerRequestHandlerMixIn()
    mock_proc = MagicMock()
    mock_proc.is_alive.return_value = True

    handler._ensure_process_cleanup(mock_proc)

    mock_proc.join.assert_any_call(timeout=2.0)
    mock_proc.kill.assert_called_once()
    mock_proc.close.assert_called_once()


def test_reconstruct_exception_builtin_fallback():
    """Standard library exceptions (not imported into handler.py's own
    globals()) must be found via the builtins fallback and re-raised with
    their original type, not silently downgraded to RuntimeError."""
    handler = PdftlServerRequestHandlerMixIn()
    with pytest.raises(ValueError, match="bad value"):
        handler._reconstruct_and_raise_exception(
            {"error_class": "ValueError", "message": "bad value"}
        )


def test_reconstruct_exception_via_module_lookup():
    """An exception type that's neither in handler.py's globals() nor a
    builtin, but does exist in one of the searched pdftl modules, is
    reconstructed with its real type via that module lookup."""
    handler = PdftlServerRequestHandlerMixIn()
    with pytest.raises(OperationError, match="op failed"):
        handler._reconstruct_and_raise_exception(
            {"error_class": "OperationError", "message": "op failed"}
        )


def test_reconstruct_exception_unknown_class_fallback():
    """An error_class that can't be found anywhere falls back to a plain
    RuntimeError carrying the original class name and message, rather than
    crashing the reconstruction itself."""
    handler = PdftlServerRequestHandlerMixIn()
    with pytest.raises(RuntimeError, match="TotallyFakeExceptionXYZ: something broke"):
        handler._reconstruct_and_raise_exception(
            {"error_class": "TotallyFakeExceptionXYZ", "message": "something broke"}
        )


def test_run_with_timeout_server_at_capacity():
    """When the worker semaphore can't be acquired within the timeout
    window, the server reports capacity exhaustion rather than silently
    hanging or crashing."""
    handler = PdftlServerRequestHandlerMixIn()
    handler.op_timeout_seconds = 0.05

    with patch("pdftl.server.handler._worker_semaphore") as mock_sem:
        mock_sem.acquire.return_value = False
        with pytest.raises(TimeoutError, match="Server is at capacity"):
            handler._run_with_timeout(lambda: None, op_name="test_op")


# ==============================================================================
# Coverage: subprocess_workers.py internals that only run inside a real
# spawned child (and thus aren't captured by coverage in end-to-end tests)
# ==============================================================================


def test_raise_on_file_arg_blocks_disallowed_path(tmp_path):
    """A path that exists on disk but isn't one of the request's own
    upload-spool paths is rejected as local file inclusion."""
    from pdftl.server.subprocess_workers import _raise_on_file_arg

    bad_file = tmp_path / "secret.txt"
    bad_file.write_text("shh")

    with pytest.raises(UserCommandLineError, match="forbidden over the REST API"):
        _raise_on_file_arg([str(bad_file)], allowed_paths=set())


def test_apply_worker_resource_limits_import_error(monkeypatch):
    """On platforms without the `resource` module (Windows), the memory
    clamp is skipped gracefully rather than crashing the worker."""
    from pdftl.server.subprocess_workers import _apply_worker_resource_limits
    import builtins as _builtins

    real_import = _builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "resource":
            raise ImportError("no resource module on this platform")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(_builtins, "__import__", fake_import)
    _apply_worker_resource_limits()  # must not raise


@pytest.mark.skipif(os.name == "nt", reason="resource module not available on Windows")
def test_apply_worker_resource_limits_platform_rejection(monkeypatch):
    """If the platform's own hard ceiling rejects the requested limit
    (observed on macOS), the worker must not crash -- memory clamping is
    best-effort, not a correctness requirement."""
    import resource
    from pdftl.server.subprocess_workers import _apply_worker_resource_limits

    monkeypatch.setattr(
        resource,
        "setrlimit",
        MagicMock(side_effect=ValueError("current limit exceeds maximum limit")),
    )
    _apply_worker_resource_limits()  # must not raise


def test_serialize_worker_payload_bytes_only():
    """A raw bytes result (no metadata tuple) serializes with an empty/None
    meta rather than being misidentified as a tuple result."""
    from pdftl.server.subprocess_workers import _serialize_worker_payload

    payload_bytes, meta, is_tuple = _serialize_worker_payload(b"%PDF-RAW")
    assert payload_bytes == b"%PDF-RAW"
    assert meta is None
    assert is_tuple is False


def test_serialize_worker_payload_fallback_scalar():
    """A bare scalar result (neither a 2-tuple nor bytes) falls through to
    the generic meta-only shape."""
    from pdftl.server.subprocess_workers import _serialize_worker_payload

    payload_bytes, meta, is_tuple = _serialize_worker_payload(42)
    assert payload_bytes == b""
    assert meta == 42
    assert is_tuple is False


def test_send_safe_ipc_response_swallows_oserror():
    """If the parent has already disconnected by the time the worker tries
    to respond, the write failure is logged rather than crashing the
    worker's own exit path."""
    from pdftl.server.subprocess_workers import _send_safe_ipc_response

    mock_conn = MagicMock()
    mock_conn.send_bytes.side_effect = BrokenPipeError("parent gone")

    _send_safe_ipc_response(mock_conn, {"status": "ok"}, b"data")  # must not raise


def test_serialize_worker_payload_tuple_with_bytes():
    """A (bytes, meta) 2-tuple result -- the shape returned by
    _run_single_operation_in_subprocess for PDF results -- serializes with
    is_tuple=True and passes the bytes through unchanged."""
    from pdftl.server.subprocess_workers import _serialize_worker_payload

    payload_bytes, meta, is_tuple = _serialize_worker_payload((b"%PDF-DATA", {"kind": "pdf"}))
    assert payload_bytes == b"%PDF-DATA"
    assert meta == {"kind": "pdf"}
    assert is_tuple is True


def test_serialize_worker_payload_tuple_with_none_bytes():
    """A (None, meta) 2-tuple -- the shape for non-PDF OpResult data, e.g.
    JSON or text output -- normalizes the None to b"" rather than passing
    None through as the payload."""
    from pdftl.server.subprocess_workers import _serialize_worker_payload

    payload_bytes, meta, is_tuple = _serialize_worker_payload((None, {"kind": "data", "data": 1}))
    assert payload_bytes == b""
    assert meta == {"kind": "data", "data": 1}
    assert is_tuple is True


def test_substitute_file_handles_resolves_at_handle():
    """An exact '@<handle>' token resolves to that handle's real spool
    path; unrelated tokens (including a bare handle name with no '@')
    pass through unchanged."""
    from pdftl.server.subprocess_workers import _substitute_file_handles

    raw_file_paths = [
        {"name": "A", "filename": "a.pdf", "path": "/tmp/spool_a.pdf"},
        {"name": "B", "filename": "b.pdf", "path": "/tmp/spool_b.pdf"},
    ]
    args = ["@B", "B", "1-3", "@C"]
    resolved = _substitute_file_handles(args, raw_file_paths)
    assert resolved == ["/tmp/spool_b.pdf", "B", "1-3", "@C"]


def test_substitute_file_handles_ignores_unnamed_files():
    """Uploaded files with no handle name (e.g. the plain 'file' field)
    are simply not substitutable -- no name means no lookup key."""
    from pdftl.server.subprocess_workers import _substitute_file_handles

    raw_file_paths = [{"name": "", "filename": "x.pdf", "path": "/tmp/spool_x.pdf"}]
    resolved = _substitute_file_handles(["@"], raw_file_paths)
    assert resolved == ["@"]


def test_run_pipeline_in_subprocess_substitutes_at_handle_per_step(tmp_path):
    """The '@<handle>' substitution applies independently to every step's
    own args, and doesn't require the handle to be in that step's 'inputs'."""
    from pdftl.server import _run_pipeline_in_subprocess

    stamp_path = tmp_path / "stamp.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(str(stamp_path))

    captured_steps = {}

    def fake_run_pipeline(steps, opened_pdfs, aliases):
        captured_steps["steps"] = steps
        return b"%PDF-OK"

    with patch("pdftl.server.subprocess_workers.run_pipeline", side_effect=fake_run_pipeline):
        result = _run_pipeline_in_subprocess(
            [{"operation": "stamp", "args": ["@S"], "inputs": ["_"]}],
            [{"name": "S", "filename": "stamp.pdf", "path": str(stamp_path)}],
        )

    assert result == b"%PDF-OK"
    assert captured_steps["steps"][0]["args"] == [str(stamp_path)]
    # Confirm substitution didn't mutate 'inputs' -- S is resolved via args
    # only, never added to the opened-PDF input list.
    assert captured_steps["steps"][0]["inputs"] == ["_"]


def test_run_single_operation_in_subprocess_substitutes_at_handle(tmp_path):
    """End-to-end (in-process) check that '@<handle>' in operation_args is
    resolved to a real path before run_operation is called, and that the
    resolved path passes the LFI guard since it's a legitimate upload."""
    from pdftl.core.core_types import OpResult

    pdf_path = tmp_path / "stamp_source.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(str(pdf_path))

    captured_args = {}

    def fake_run_operation(operation, call_context):
        captured_args["args"] = call_context["operation_args"]
        return OpResult(success=True)

    with patch("pdftl.core.executor.run_operation", side_effect=fake_run_operation):
        _run_single_operation_in_subprocess(
            "noop",
            ["@S"],
            [{"name": "S", "filename": "stamp_source.pdf", "path": str(pdf_path)}],
        )

    assert captured_args["args"] == [str(pdf_path)]


def test_serialize_operation_data_uses_registered_api_serializer():
    """When an operation is registered with an api_serializer, that must
    take priority over the generic shape-based fallback -- exercises the
    dump_streams-style dispatch path directly, in-process."""
    from pdftl.server.subprocess_workers import _serialize_operation_data
    from pdftl.core.registry import registry

    def fake_api_serializer(data, meta):
        return None, {"kind": "text", "text": f"SERIALIZED:{data}"}

    original = registry.operations.get("fake_stream_op")
    registry.operations["fake_stream_op"] = {"api_serializer": fake_api_serializer}
    try:
        result_meta = _serialize_operation_data("raw-data", {}, operation="fake_stream_op")
    finally:
        if original is not None:
            registry.operations["fake_stream_op"] = original
        else:
            registry.operations.pop("fake_stream_op", None)

    assert result_meta == {"kind": "text", "text": "SERIALIZED:raw-data"}


def test_serialize_operation_data_api_serializer_receives_data_and_meta():
    """Confirms both positional arguments (data, meta) are forwarded to the
    api_serializer exactly as received, not reordered or partially applied."""
    from pdftl.server.subprocess_workers import _serialize_operation_data
    from pdftl.core.registry import registry

    captured = {}

    def fake_api_serializer(data, meta):
        captured["data"] = data
        captured["meta"] = meta
        return None, {"kind": "data", "data": "ok"}

    original = registry.operations.get("fake_stream_op2")
    registry.operations["fake_stream_op2"] = {"api_serializer": fake_api_serializer}
    try:
        _serialize_operation_data({"pages": 3}, {"json_output": True}, operation="fake_stream_op2")
    finally:
        if original is not None:
            registry.operations["fake_stream_op2"] = original
        else:
            registry.operations.pop("fake_stream_op2", None)

    assert captured["data"] == {"pages": 3}
    assert captured["meta"] == {"json_output": True}


def test_serialize_operation_data_falls_back_without_api_serializer():
    """An operation with no api_serializer registered must fall through to
    the generic shape-based logic unchanged (regression guard: adding the
    api_serializer branch must not affect ops that don't define one)."""
    from pdftl.server.subprocess_workers import _serialize_operation_data
    from pdftl.core.registry import registry

    original = registry.operations.get("plain_op")
    registry.operations["plain_op"] = {}  # no api_serializer key
    try:
        result_meta = _serialize_operation_data({"a": 1}, {}, operation="plain_op")
    finally:
        if original is not None:
            registry.operations["plain_op"] = original
        else:
            registry.operations.pop("plain_op", None)

    assert result_meta == {"kind": "data", "data": {"a": 1}}


def test_serialize_operation_data_falls_back_when_operation_none():
    """operation=None (the default) must skip the api_serializer lookup
    entirely and go straight to the generic fallback -- covers the `if
    operation:` guard's False branch."""
    from pdftl.server.subprocess_workers import _serialize_operation_data

    result_meta = _serialize_operation_data("plain string", {}, operation=None)

    assert result_meta == {"kind": "data", "data": "plain string"}


def test_dump_streams_end_to_end_over_server_uses_api_serializer(server) -> None:
    """Full HTTP + subprocess round-trip: dump_streams over the API must
    return formatted text (via its registered api_serializer), not a raw
    JSON dump of the (header, bytes, warnings) tuples it actually returns
    as .data -- exercising the real registry entry, not a fake one."""
    ms = server()
    base_url = ms.base_url

    pdf_bytes = make_pdf_bytes(1)
    with post_multipart(
        f"{base_url}/v1/execute/dump_streams", {"file": pdf_bytes}, "[]"
    ) as response:
        assert response.status == 200
        assert "text/plain" in response.headers.get("Content-Type", "")
        text = response.read().decode("utf-8")
        assert "Page 1" in text
        assert "===" in text  # confirms real block-format output, not raw tuples
