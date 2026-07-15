# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/server/test_server_pipeline.py

"""Integration tests for the pdftl server's multi-stage pipeline endpoint."""

import os
import io
import json
import urllib.error
import urllib.request

import pikepdf
import pytest

from unittest.mock import MagicMock, patch

from pdftl.exceptions import UserCommandLineError
from pdftl.core.registry import registry
from pdftl.server.server_pipeline import (
    ApiInputContext,
    build_pipeline_stages,
    parse_pipeline_steps,
    run_pipeline,
)

from tests.server.server_fixtures import make_pdf_bytes, post_multipart


@pytest.fixture(autouse=True)
def _no_shutdown_token(monkeypatch):
    monkeypatch.delenv("PDFTL_SERVER_SHUTDOWN_TOKEN", raising=False)


# Explicitly disable keep-alive to prevent deadlocking single-threaded servers
# Ignore connection errors since the server is shutting down or already down.
# (moved to tests/operations/conftest.py: `server` fixture, make_pdf_bytes, post_multipart)


# ==============================================================================
# Unit tests for the parsing/building helpers
# ==============================================================================


def test_parse_pipeline_steps_accepts_valid_json_array() -> None:
    """A well-formed JSON array of step objects parses into equivalent dicts."""
    steps = parse_pipeline_steps('[{"operation": "cat", "args": ["A", "B"]}]')
    assert steps == [{"operation": "cat", "args": ["A", "B"]}]


def test_parse_pipeline_steps_rejects_invalid_json() -> None:
    """Malformed JSON in the args field is reported as a user command-line error."""
    with pytest.raises(UserCommandLineError, match="not valid JSON"):
        parse_pipeline_steps("not json")


def test_parse_pipeline_steps_rejects_empty_or_non_list() -> None:
    """Both an empty list and a non-list JSON value are rejected."""
    with pytest.raises(UserCommandLineError, match="non-empty JSON array"):
        parse_pipeline_steps("[]")
    with pytest.raises(UserCommandLineError, match="non-empty JSON array"):
        parse_pipeline_steps('{"operation": "cat"}')


def test_parse_pipeline_steps_rejects_step_without_operation() -> None:
    """Each step must be an object carrying an 'operation' key."""
    with pytest.raises(UserCommandLineError, match="'operation' key"):
        parse_pipeline_steps('[{"args": ["right"]}]')


def test_parse_pipeline_steps_blocks_forbidden_step_operations() -> None:
    """Steps cannot request 'server' or 'pipeline' as their operation."""
    with pytest.raises(UserCommandLineError, match="cannot be used as a pipeline step"):
        parse_pipeline_steps('[{"operation": "server"}]')
    with pytest.raises(UserCommandLineError, match="cannot be used as a pipeline step"):
        parse_pipeline_steps('[{"operation": "pipeline"}]')


def test_parse_pipeline_steps_rejects_unknown_operation() -> None:
    """An operation name not present in the registry is rejected early."""
    with pytest.raises(UserCommandLineError, match="is not supported"):
        parse_pipeline_steps('[{"operation": "definitely_not_a_real_op"}]')


def test_parse_pipeline_steps_rejects_malformed_args_inputs_options() -> None:
    """The 'args', 'inputs', and 'options' fields must be the expected JSON types."""
    with pytest.raises(UserCommandLineError, match="'args' must be a list"):
        parse_pipeline_steps('[{"operation": "rotate", "args": "right"}]')
    with pytest.raises(UserCommandLineError, match="'inputs' must be a list"):
        parse_pipeline_steps('[{"operation": "cat", "inputs": "A"}]')
    with pytest.raises(UserCommandLineError, match="'options' must be an object"):
        parse_pipeline_steps('[{"operation": "rotate", "options": ["bad"]}]')


def test_build_pipeline_stages_defaults_first_stage_to_all_aliases() -> None:
    """The first stage consumes every uploaded handle unless 'inputs' is given.
    Later stages default to the implicit chain sentinel '_' to consume the
    previous stage's output.
    """
    steps = [{"operation": "cat", "args": ["A", "B"]}, {"operation": "rotate", "args": ["right"]}]
    stages = build_pipeline_stages(steps, ["A", "B"], "unused_output.pdf")

    assert stages[0].inputs == ["A", "B"]
    assert stages[0].input_passwords == [None, None]
    assert stages[1].inputs == ["_"]
    assert stages[1].input_passwords == [None]


def test_build_pipeline_stages_honors_explicit_inputs() -> None:
    """An explicit 'inputs' list on a step overrides the default handle expansion."""
    steps = [{"operation": "cat", "args": ["A"], "inputs": ["A"]}]
    stages = build_pipeline_stages(steps, ["A", "B"], "unused_output.pdf")

    assert stages[0].inputs == ["A"]
    assert stages[0].input_passwords == [None]


def test_api_input_context_get_input_raises() -> None:
    """The API input context cannot satisfy interactive prompts."""
    context = ApiInputContext()
    with pytest.raises(UserCommandLineError, match="Interactive input"):
        context.get_input("Enter a filename: ")


def test_build_pipeline_stages_raises_on_forbidden_output() -> None:
    """Direct parent-process unit test to cover the forbidden output check
    in build_pipeline_stages in-process (Line 134 coverage)."""
    steps = [
        {"operation": "create", "args": ["1(A4)"], "options": {"output": "/tmp/forbidden.pdf"}}
    ]
    with pytest.raises(
        UserCommandLineError, match="The 'output' option is forbidden in pipeline steps"
    ):
        build_pipeline_stages(steps, [], "/tmp/final_unused.pdf")


# ==============================================================================
# End-to-end HTTP tests
# ==============================================================================


def test_pipeline_endpoint_concatenates_then_rotates(server) -> None:
    """A two-step pipeline (cat, then rotate) runs server-side in one request."""
    ms = server()
    base_url = ms.base_url

    pdf_a = make_pdf_bytes(1)
    pdf_b = make_pdf_bytes(2)
    steps = json.dumps(
        [
            {"operation": "cat", "args": ["A", "B"]},
            {"operation": "rotate", "args": ["right"]},
        ]
    )

    with post_multipart(
        f"{base_url}/v1/execute/pipeline", {"A": pdf_a, "B": pdf_b}, steps
    ) as response:
        assert response.status == 200
        assert response.headers["Content-Type"] == "application/pdf"
        with pikepdf.open(io.BytesIO(response.read())) as pdf:
            assert len(pdf.pages) == 3
            assert int(pdf.pages[0].obj.get("/Rotate", 0)) == 90


def test_pipeline_endpoint_applies_output_encryption(server) -> None:
    """The final step's 'options' can request owner/user password encryption."""
    ms = server()
    base_url = ms.base_url

    pdf_a = make_pdf_bytes(1)
    steps = json.dumps(
        [
            {
                "operation": "rotate",
                "args": ["right"],
                "options": {"owner_pw": "ownersecret", "encrypt_aes256": True},
            }
        ]
    )

    with post_multipart(f"{base_url}/v1/execute/pipeline", {"A": pdf_a}, steps) as response:
        assert response.status == 200
        with pikepdf.open(io.BytesIO(response.read())) as pdf:
            assert pdf.is_encrypted


def test_pipeline_endpoint_dump_text_final_step_returns_text(server) -> None:
    """A pipeline whose last stage is a skip_pipeline_save operation (e.g.
    dump_text) with no 'output' now succeeds, returning that operation's
    own data/text instead of requiring a PDF."""
    ms = server()
    base_url = ms.base_url

    pdf_a = make_pdf_bytes(1)
    steps = json.dumps([{"operation": "dump_text"}])

    with post_multipart(f"{base_url}/v1/execute/pipeline", {"A": pdf_a}, steps) as response:
        assert response.status == 200


def test_pipeline_endpoint_rejects_blocked_step_operation(server) -> None:
    """Requesting 'server' as a pipeline step is rejected before execution."""
    ms = server()
    base_url = ms.base_url

    pdf_a = make_pdf_bytes(1)
    steps = json.dumps([{"operation": "server"}])

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        post_multipart(f"{base_url}/v1/execute/pipeline", {"A": pdf_a}, steps)
    assert exc_info.value.code == 400


def test_pipeline_endpoint_requires_multipart_content_type(server) -> None:
    """Non-multipart requests to the pipeline endpoint are rejected outright."""
    ms = server()
    base_url = ms.base_url

    req = urllib.request.Request(
        f"{base_url}/v1/execute/pipeline",
        data=b"not multipart",
        headers={"Content-Type": "application/octet-stream"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 400


def test_run_pipeline_saves_in_memory_pdf_when_no_stage_writes_output() -> None:
    """When no stage's operation requires 'output', pipeline_pdf is saved
    exactly once, in-memory, and its bytes are returned directly."""

    class FakeManagerNoOutput:
        def __init__(self, stages, input_context, handles=None) -> None:
            self.stages = stages
            self.pipeline_pdf = MagicMock()

        def run(self) -> None:
            pass

    with patch(
        "pdftl.server.server_pipeline.PipelineManager",
        FakeManagerNoOutput,
    ):
        result_bytes, meta = run_pipeline([], [], {})

    assert isinstance(result_bytes, bytes)
    assert meta == {"kind": "pdf"}


def test_run_pipeline_reads_bytes_from_temp_output_file() -> None:
    """When the final stage's operation requires 'output', PipelineManager's
    own CLI-driven save writes the finalized PDF to the injected temp path;
    run_pipeline must read those bytes back rather than calling .save() a
    second time on the same pikepdf.Pdf object."""

    class FakeManagerWritesOutput:
        def __init__(self, stages, input_context, handles=None) -> None:
            self.stages = stages
            self.pipeline_pdf = None

        def run(self) -> None:
            out_path = self.stages[-1].options.get("output")
            with open(out_path, "wb") as f:
                f.write(b"%PDF-FAKE-FINALIZED-BYTES")
            self.pipeline_pdf = MagicMock()

    fake_op_registry = {
        "stub_requires_output": {"usage": "<input> stub_requires_output output <file>"}
    }
    with (
        patch.object(registry, "operations", fake_op_registry),
        patch(
            "pdftl.server.server_pipeline.PipelineManager",
            FakeManagerWritesOutput,
        ),
    ):
        result_bytes, meta = run_pipeline(
            [{"operation": "stub_requires_output", "args": []}], [], {}
        )

    assert result_bytes == b"%PDF-FAKE-FINALIZED-BYTES"
    assert meta == {"kind": "pdf"}


def test_run_pipeline_cleanup_swallows_missing_temp_file() -> None:
    """The finally block's os.remove(tmp_path) must not raise if the temp
    file was already removed or never created by the fake manager."""

    class FakeManagerNoFileWritten:
        def __init__(self, stages, input_context, handles=None) -> None:
            self.stages = stages
            self.pipeline_pdf = MagicMock()

        def run(self) -> None:
            # Deliberately never writes to the injected temp path, and
            # removes it early to simulate it already being gone by the
            # time run_pipeline's own cleanup runs.
            out_path = self.stages[-1].options.get("output") if self.stages else None
            # No file created; nothing to remove.
            assert out_path is None or not __import__("os").path.exists(out_path)

    with patch(
        "pdftl.server.server_pipeline.PipelineManager",
        FakeManagerNoFileWritten,
    ):
        result_bytes, meta = run_pipeline([], [], {})

    assert isinstance(result_bytes, bytes)
    assert meta == {"kind": "pdf"}


def test_run_pipeline_cleanup_swallows_already_removed_temp_file() -> None:
    """The finally block's os.remove(tmp_path) must not raise if something
    else (e.g. the CLI's own save step) already removed the temp file
    before run_pipeline's own cleanup runs."""
    import os as os_module

    class FakeManagerRemovesTempFile:
        def __init__(self, stages, input_context, handles=None) -> None:
            self.stages = stages
            self.pipeline_pdf = MagicMock()

        def run(self) -> None:
            out_path = self.stages[-1].options.get("output") if self.stages else None
            if out_path and os_module.path.exists(out_path):
                os_module.remove(out_path)

    with patch(
        "pdftl.server.server_pipeline.PipelineManager",
        FakeManagerRemovesTempFile,
    ):
        # No stage requires 'output', so build_pipeline_stages never sets it,
        # but mkstemp still creates tmp_path up front; delete it directly to
        # force the finally block's os.remove to hit a missing file.
        with patch("os.remove", side_effect=OSError("already gone")):
            result_bytes, meta = run_pipeline([], [], {})

    assert isinstance(result_bytes, bytes)
    assert meta == {"kind": "pdf"}


def test_run_pipeline_raises_when_pipeline_pdf_is_none() -> None:
    """Line 203: Verifies that a UserCommandLineError is raised if the manager fails to yield a final PDF."""
    with patch("pdftl.server.server_pipeline.PipelineManager") as mock_pm_class:
        mock_instance = mock_pm_class.return_value
        mock_instance.pipeline_pdf = None

        with pytest.raises(
            UserCommandLineError, match="Pipeline completed but produced no output PDF."
        ):
            run_pipeline([], [], {})


def test_server_pipeline_forbidden_output_option(server) -> None:
    """Verifies that passing an 'output' parameter in pipeline step options
    is forbidden and raises UserCommandLineError (HTTP 400)."""
    ms = server()
    base_url = ms.base_url

    boundary = "===PIPELINE_OUT_BOUNDARY==="
    body_parts = [
        f"--{boundary}",
        'Content-Disposition: form-data; name="args"',
        "",
        '[{"operation": "create", "args": ["1(A4)"], "options": {"output": "/tmp/forbidden.pdf"}}]',
        f"--{boundary}--",
        "",
    ]
    body_data = "\r\n".join(body_parts).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/v1/execute/pipeline",
        data=body_data,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 400
    assert "forbidden" in exc_info.value.read().decode("utf-8").lower()


def test_server_single_operation_lfi_sandbox(server) -> None:
    """Verifies that attempting single-operation parameter LFI (absolute paths,
    path traversal) throws a UserCommandLineError (HTTP 400)."""
    ms = server()
    base_url = ms.base_url

    boundary = "===LFI_BOUNDARY==="
    abs_path = r"C:\\Windows\\System32\\drivers\\etc\\hosts" if os.name == "nt" else "/etc/passwd"
    # Attempting to use an absolute path or path traversal in arguments
    for invalid_arg in (abs_path, "../secret_file.txt", r"..\\nested\\secret.pdf"):
        body_parts = [
            f"--{boundary}",
            'Content-Disposition: form-data; name="args"',
            "",
            f'["{invalid_arg}"]',
            f"--{boundary}--",
            "",
        ]
        body_data = "\r\n".join(body_parts).encode("utf-8")

        req = urllib.request.Request(
            f"{base_url}/v1/execute/create",
            data=body_data,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 400
        assert "forbidden over the rest api" in exc_info.value.read().decode("utf-8").lower()


def test_server_pipeline_lfi_sandbox(server) -> None:
    """Verifies that attempting pipeline parameter LFI (absolute paths,
    path traversal) throws a UserCommandLineError (HTTP 400)."""
    ms = server()
    base_url = ms.base_url

    boundary = "===PIPE_LFI_BOUNDARY==="
    body_parts = [
        f"--{boundary}",
        'Content-Disposition: form-data; name="args"',
        "",
        '[{"operation": "create", "args": ["../secret_file.txt"]}]',
        f"--{boundary}--",
        "",
    ]
    body_data = "\r\n".join(body_parts).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/v1/execute/pipeline",
        data=body_data,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 400
    assert "forbidden over the rest api" in exc_info.value.read().decode("utf-8").lower()


def test_pipeline_invalid_compilation_error(server) -> None:
    """Triggers and verifies error handling path during invalid pipeline stage
    construction, checking clean validation failure (Line 187 coverage)."""
    ms = server()
    base_url = ms.base_url

    steps = json.dumps([{"operation": "non_existent_operation_name", "args": []}])

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        with post_multipart(f"{base_url}/v1/execute/pipeline", {}, steps):
            pass
    assert exc_info.value.code == 404 or exc_info.value.code == 400


def test_pipeline_forbidden_output_option(server) -> None:
    """Verifies that passing an 'output' parameter in pipeline step options
    is explicitly forbidden at the pipeline compilation stage (Line 134 coverage)."""
    ms = server()
    base_url = ms.base_url

    steps = json.dumps(
        [{"operation": "create", "args": ["1(A4)"], "options": {"output": "/tmp/forbidden.pdf"}}]
    )

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        with post_multipart(f"{base_url}/v1/execute/pipeline", {}, steps):
            pass
    assert exc_info.value.code == 400
    assert "forbidden in pipeline steps" in exc_info.value.read().decode("utf-8").lower()


def test_run_pipeline_final_step_returns_json_data(tmp_path):
    """A pipeline whose final step is a skip_pipeline_save (data-producing)
    operation now succeeds, returning (bytes|None, meta) instead of being
    hard-rejected."""
    from pdftl.core.core_types import OpResult

    class FakeManagerDataResult:
        def __init__(self, stages, input_context, handles=None) -> None:
            self.stages = stages
            self.pipeline_pdf = MagicMock()
            self.results = [OpResult(success=True, data={"pages": 3}, meta={"json_output": True})]

        def run(self) -> None:
            pass

    fake_op_registry = {"dump_data": {"skip_pipeline_save": True}}
    with (
        patch.object(registry, "operations", fake_op_registry),
        patch("pdftl.server.server_pipeline.PipelineManager", FakeManagerDataResult),
    ):
        result_bytes, meta = run_pipeline([{"operation": "dump_data", "args": ["json"]}], [], {})

    assert result_bytes is None
    assert meta == {"kind": "data", "data": {"pages": 3}}


def test_run_pipeline_final_step_data_producing_no_results_raises():
    """If the final step is flagged skip_pipeline_save but somehow produced
    no OpResult at all, this is a clean error, not a silent empty response."""

    class FakeManagerNoResults:
        def __init__(self, stages, input_context, handles=None) -> None:
            self.stages = stages
            self.pipeline_pdf = None
            self.results = []

        def run(self) -> None:
            pass

    fake_op_registry = {"dump_data": {"skip_pipeline_save": True}}
    with (
        patch.object(registry, "operations", fake_op_registry),
        patch("pdftl.server.server_pipeline.PipelineManager", FakeManagerNoResults),
    ):
        with pytest.raises(UserCommandLineError, match="produced no result"):
            run_pipeline([{"operation": "dump_data", "args": []}], [], {})


def test_pipeline_endpoint_final_step_dump_data_returns_json(server) -> None:
    """End-to-end: a pipeline ending in dump_data (json) returns a JSON
    response instead of a PDF, via the real HTTP + subprocess path."""
    ms = server()
    base_url = ms.base_url

    pdf_a = make_pdf_bytes(1)
    steps = json.dumps([{"operation": "dump_data", "args": ["json"]}])

    with post_multipart(f"{base_url}/v1/execute/pipeline", {"A": pdf_a}, steps) as response:
        assert response.status == 200
        assert "application/json" in response.headers.get("Content-Type", "")
        data = json.loads(response.read().decode("utf-8"))
        assert data["success"] is True
        assert "result" in data


def test_unpack_ipc_result_plain_non_tuple_payload():
    """When the worker's underlying result wasn't a 2-tuple (is_tuple=False,
    per _serialize_worker_payload), _unpack_ipc_result returns the raw
    payload bytes directly rather than a (bytes, meta) pair."""
    from pdftl.server.handler import PdftlServerRequestHandlerMixIn

    handler = PdftlServerRequestHandlerMixIn()
    metadata = {"is_tuple": False, "meta": 42}

    result = handler._unpack_ipc_result(metadata, b"raw-payload-bytes")

    assert result == b"raw-payload-bytes"
