# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/server/handler.py

"""HTTP request handler mixin: routing, dispatch, timeout orchestration,
and response serialization for the pdftl stateless server.

Multipart parsing lives in `pdftl.server.multipart`; subprocess-isolated
operation/pipeline execution lives in `pdftl.server.subprocess_workers`.
"""

import json
import logging
import multiprocessing
import builtins
import socketserver
import threading
import time
import os
from functools import lru_cache
from collections.abc import Callable
from typing import Any

from pdftl.core.metadata import get_project_version
from pdftl.exceptions import UserCommandLineError
from pdftl.server.server_pipeline import parse_pipeline_steps
from pdftl.server import multipart
from pdftl.server.subprocess_workers import (
    _run_single_operation_in_subprocess,
    _run_pipeline_in_subprocess,
    _subprocess_worker_entrypoint,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_UPLOAD_MB = 100
DEFAULT_TIMEOUT_SECONDS = 300

MAX_CONCURRENT_WORKERS = int(
    os.environ.get("PDFTL_MAX_CONCURRENT_WORKERS", min(4, os.cpu_count() or 1))
)
_worker_semaphore = threading.BoundedSemaphore(MAX_CONCURRENT_WORKERS)


@lru_cache(maxsize=1)
def _builder_html() -> bytes:
    """Loads the static pipeline-builder demo page, cached after first read."""
    import importlib.resources
    from pathlib import Path

    html_bytes = (
        importlib.resources.files("pdftl.operations")
        .joinpath(Path("static") / "builder.html")
        .read_bytes()
    )
    return html_bytes


class PdftlServerRequestHandlerMixIn:
    """Stateless HTTP server daemon mixin for routing PDF processing requests."""

    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_MB * 1024 * 1024
    op_timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    def log_message(self, format_msg: str, *args_msg: Any) -> None:
        logger.info(format_msg, *args_msg)

    def _extract_op_meta(self, op_data: Any) -> dict[str, Any]:
        desc = (
            op_data.get("desc", "") if isinstance(op_data, dict) else getattr(op_data, "desc", "")
        )
        usage = (
            op_data.get("usage", "")
            if isinstance(op_data, dict)
            else getattr(op_data, "usage", "")
        )
        op_type = (
            op_data.get("type", "") if isinstance(op_data, dict) else getattr(op_data, "type", "")
        )
        tags = (
            op_data.get("tags", []) if isinstance(op_data, dict) else getattr(op_data, "tags", [])
        )
        return {"desc": desc, "usage": usage, "type": op_type, "tags": tags}

    def _handle_builder(self) -> None:
        body = _builder_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/v1/status"):
            self._handle_status_route()
        elif self.path.split("?", 1)[0] == "/builder":
            self._handle_builder()
        else:
            self.send_error(404, "Endpoint not found")

    def _handle_status_route(self) -> None:
        from pdftl.core.registry import registry

        ops_meta = {
            op_name: self._extract_op_meta(op_data)
            for op_name, op_data in registry.operations.items()
        }
        self._send_json(
            {
                "status": "online",
                "version": get_project_version(),
                "engine": "pdftl",
                "operations": ops_meta,
            }
        )

    def do_shutdown(self):
        import os

        required_token = os.environ.get("PDFTL_SERVER_SHUTDOWN_TOKEN")
        if required_token:
            import secrets

            auth_header = self.headers.get("X-Shutdown-Token", "")
            if not secrets.compare_digest(auth_header, required_token):
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"error": "Invalid shutdown token"}')
                return

        self._send_json({"status": "shutting_down"})
        threading.Thread(target=self.server.shutdown).start()
        return

    def do_POST(self) -> None:
        if self.path == "/v1/shutdown":
            return self.do_shutdown()

        parts = [p for p in self.path.split("/") if p]
        if len(parts) == 3 and parts[0] == "v1" and parts[1] == "execute":
            self._dispatch_execution_route(parts[2])
        else:
            self.send_error(404, "Endpoint not found")

    def _run_with_error_handling(self, operation: str, handler: Callable[[], None]) -> None:
        try:
            handler()
        except (ValueError, TypeError, KeyError, UserCommandLineError) as e:
            logger.error("Client parameters error for operation '%s': %s", operation, e)
            self._send_error(400, f"Bad request parameters: {str(e)}")
        except TimeoutError as e:
            logger.error("Timeout executing operation '%s': %s", operation, e)
            self._send_error(504, str(e))
        except Exception as e:
            logger.exception("Internal error executing operation '%s'", operation)
            self._send_error(500, f"Execution failed: {str(e)}")

    def _spawn_worker(
        self, ctx: multiprocessing.context.BaseContext, fn: Callable[..., Any], args: tuple
    ) -> tuple[Any, multiprocessing.Process]:
        parent_conn, child_conn = ctx.Pipe(duplex=False)
        proc = ctx.Process(
            target=_subprocess_worker_entrypoint, args=(child_conn, fn, args), daemon=True
        )
        try:
            proc.start()
        except Exception:  # noqa: BLE001 - start() can fail for many reasons
            # (OSError from resource exhaustion, platform-specific spawn
            # failures on Windows, etc); whatever it is, we must still
            # release parent_conn here since the caller never receives it
            # to clean up itself.
            parent_conn.close()
            raise
        finally:
            child_conn.close()  # Close parent copy of child handle to prevent leaks and deadlocks
        return parent_conn, proc

    def _read_next_ipc_message(
        self, parent_conn: Any, metadata: dict[str, Any] | None
    ) -> tuple[dict[str, Any] | None, tuple[str, dict[str, Any], bytes] | None]:
        """Reads the next sequential message from the IPC pipe.

        Returns (updated_metadata, result_tuple). If result_tuple is returned,
        the transaction is complete or has encountered an error.
        """
        try:
            if metadata is None:
                meta_bytes = parent_conn.recv_bytes()
                new_metadata = json.loads(meta_bytes.decode("utf-8"))
                return new_metadata, None
            payload_bytes = parent_conn.recv_bytes()
            return metadata, (metadata["status"], metadata, payload_bytes)
        except Exception as e:  # noqa: BLE001
            return (
                None,
                (
                    "err",
                    {
                        "status": "err",
                        "error_class": "RuntimeError",
                        "message": f"IPC transport failure: {str(e)}",
                    },
                    b"",
                ),
            )

    def _handle_worker_crash(self, parent_conn: Any) -> tuple[str, dict[str, Any], bytes] | None:
        """Evaluates deadlocks and handles sudden child-worker execution crashes."""
        if parent_conn.poll(0.1):
            return None
        return (
            "err",
            {
                "status": "err",
                "error_class": "RuntimeError",
                "message": "Worker process crashed unexpectedly (OOM or segfault).",
            },
            b"",
        )

    def _poll_ipc_connection(
        self, parent_conn: Any, proc: multiprocessing.Process
    ) -> tuple[str, dict[str, Any], bytes]:
        start_time = time.time()
        metadata = None
        while True:
            if time.time() - start_time > self.op_timeout_seconds:
                return "timeout", {}, b""

            if parent_conn.poll(0.5):
                metadata, result = self._read_next_ipc_message(parent_conn, metadata)
                if result is not None:
                    return result
            elif not proc.is_alive():
                crash_result = self._handle_worker_crash(parent_conn)
                if crash_result is not None:
                    return crash_result

    def _terminate_runaway_process(self, proc: multiprocessing.Process, op_name: str) -> None:
        logger.warning(
            "Operation '%s' exceeded %ds timeout; killing worker.",
            op_name,
            self.op_timeout_seconds,
        )
        proc.terminate()
        proc.join(timeout=1.0)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=1.0)
        proc.close()

    def _ensure_process_cleanup(self, proc: multiprocessing.Process) -> None:
        proc.join(timeout=2.0)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=1.0)
        proc.close()

    def _reconstruct_and_raise_exception(self, metadata: dict[str, Any]) -> None:
        exc_class_name = metadata["error_class"]
        msg = metadata["message"]

        cls = globals().get(exc_class_name)
        if cls is None:
            # builtin exceptions (ValueError, AttributeError, etc.) aren't
            # in this module's globals(); fall back to the builtins module.
            cls = getattr(builtins, exc_class_name, None)
        if cls and issubclass(cls, BaseException):
            raise cls(msg)

        for module_path in ["pdftl.exceptions", "pdftl.errors", "pdftl.cli"]:
            try:
                import importlib

                mod = importlib.import_module(module_path)
                cls = getattr(mod, exc_class_name, None)
                if cls and issubclass(cls, BaseException):
                    raise cls(msg)
            except ImportError:
                continue

        raise RuntimeError(f"{exc_class_name}: {msg}")

    def _unpack_ipc_result(self, metadata: dict[str, Any], payload_bytes: bytes) -> Any:
        if metadata["is_tuple"]:
            kind = metadata["meta"].get("kind") if isinstance(metadata["meta"], dict) else None
            actual_bytes = None if kind != "pdf" and not payload_bytes else payload_bytes
            return actual_bytes, metadata["meta"]
        return payload_bytes

    def _run_with_timeout(self, fn: Callable[..., Any], *args: Any, op_name: str) -> Any:
        if not _worker_semaphore.acquire(timeout=self.op_timeout_seconds):
            raise TimeoutError("Server is at capacity. Failed to acquire a worker slot.")

        ctx = multiprocessing.get_context("spawn")
        parent_conn, proc = self._spawn_worker(ctx, fn, args)

        try:
            status, metadata, payload_bytes = self._poll_ipc_connection(parent_conn, proc)

            if status == "timeout":
                self._terminate_runaway_process(proc, op_name)
                raise TimeoutError(
                    f"Operation '{op_name}' exceeded the "
                    f"{self.op_timeout_seconds}s server timeout."
                )

            self._ensure_process_cleanup(proc)

            if status == "err":
                self._reconstruct_and_raise_exception(metadata)

            return self._unpack_ipc_result(metadata, payload_bytes)
        finally:
            parent_conn.close()
            _worker_semaphore.release()

    def _dispatch_execution_route(self, operation: str) -> None:
        from pdftl.core.registry import registry

        if operation == "server":
            logger.warning(
                "Blocked attempt to execute 'server' operation recursively over the API."
            )
            self._send_error(
                403, "Forbidden: Server operation cannot be called recursively via the API."
            )
            return

        if operation == "pipeline":
            self._run_with_error_handling(operation, self._handle_pipeline_execute)
            return

        if operation not in registry.operations:
            self._send_error(404, f"Operation '{operation}' not found in registry.")
            return

        self._run_with_error_handling(operation, lambda: self._handle_execute(operation))

    def _reject_if_oversized(self, content_length: int) -> bool:
        if content_length <= self.max_upload_bytes:
            return False
        # The client may still be mid-upload when we reject here. Don't try
        # to keep the connection alive for a pipelined next request -- the
        # remaining body bytes are still in flight and nothing will drain
        # them, which can leave this handler thread (and its socket) never
        # cleanly exiting under keep-alive. Force the connection closed.
        self.close_connection = True
        limit_mb = self.max_upload_bytes / (1024 * 1024)
        self._send_error(
            413,
            f"Request body of {content_length} bytes exceeds the server's "
            f"{limit_mb:.0f}MB upload limit (max_upload_mb={round(limit_mb)}).",
        )
        return True

    def _initialize_pdfs(
        self, uploaded_files: list[dict[str, Any]]
    ) -> tuple[list[Any], dict[str, int]]:
        import pikepdf

        opened_pdfs = []
        aliases = {}
        for idx, file_info in enumerate(uploaded_files):
            try:
                pdf_obj = pikepdf.open(file_info["path"])
                opened_pdfs.append(pdf_obj)
                field_name = file_info["name"]
                if field_name:
                    aliases[field_name] = idx
            except (pikepdf.PdfError, ValueError, TypeError) as exc:
                logger.error(
                    "Failed to open uploaded PDF file '%s': %s", file_info["filename"], exc
                )
                raise ValueError(
                    f"Failed to open uploaded PDF file '{file_info['filename']}': {exc}"
                ) from exc
        return opened_pdfs, aliases

    def _parse_args(self, parsed_fields: dict[str, Any]) -> list[str]:
        if "args" not in parsed_fields:
            return []
        args_raw = parsed_fields["args"]
        try:
            operation_args = json.loads(args_raw)
            if not isinstance(operation_args, list):
                return [str(operation_args)]
            return operation_args
        except json.JSONDecodeError:
            return [a.strip() for a in args_raw.split(",") if a.strip()]

    def _handle_execute(self, operation: str) -> None:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._send_error(400, "Content-Type must be 'multipart/form-data'")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if self._reject_if_oversized(content_length):
            return
        parsed_fields, uploaded_files = multipart.parse_multipart_payload(
            self.rfile, content_type, content_length
        )

        try:
            opened_pdfs, _aliases = self._initialize_pdfs(uploaded_files)
            self._cleanup_pdfs(opened_pdfs)

            operation_args = self._parse_args(parsed_fields)
            result_bytes, result_meta = self._run_with_timeout(
                _run_single_operation_in_subprocess,
                operation,
                operation_args,
                uploaded_files,
                op_name=operation,
            )
            self._serialize_subprocess_result(result_bytes, result_meta)
        finally:
            multipart.cleanup_uploaded_files(uploaded_files)

    def _handle_pipeline_execute(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._send_error(400, "Content-Type must be 'multipart/form-data'")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if self._reject_if_oversized(content_length):
            return
        parsed_fields, uploaded_files = multipart.parse_multipart_payload(
            self.rfile, content_type, content_length
        )

        try:
            opened_pdfs, _aliases = self._initialize_pdfs(uploaded_files)
            self._cleanup_pdfs(opened_pdfs)

            steps = parse_pipeline_steps(parsed_fields.get("args", "[]"))
            result_bytes, result_meta = self._run_with_timeout(
                _run_pipeline_in_subprocess, steps, uploaded_files, op_name="pipeline"
            )
            self._serialize_subprocess_result(result_bytes, result_meta)
        finally:
            multipart.cleanup_uploaded_files(uploaded_files)

    def _serialize_subprocess_result(self, pdf_bytes: bytes | None, meta: dict[str, Any]) -> None:
        kind = meta.get("kind")
        if kind == "pdf" and pdf_bytes is not None:
            self._send_pdf_bytes(pdf_bytes)
        elif kind == "text":
            self._send_text_response(meta["text"])
        elif kind == "data":
            data = meta["data"]
            if isinstance(data, (dict, list)):
                self._send_json({"success": True, "result": data})
            else:
                self._send_text_response(str(data))
        else:
            self._send_json({"success": True})

    def _send_pdf_bytes(self, pdf_data: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(pdf_data)))
        self.end_headers()
        self.wfile.write(pdf_data)

    def _send_text_response(self, text: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(text.encode("utf-8"))

    def _cleanup_pdfs(self, opened_pdfs: list[Any]) -> None:
        import pikepdf

        for pdf_obj in opened_pdfs:
            try:
                pdf_obj.close()
            except (AttributeError, pikepdf.PdfError, OSError) as exc:
                logger.debug("Failed to close pdf_obj during cleanup: %s", exc)

    def _send_json(self, payload: Any) -> None:
        content = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_error(self, code: int, message: str) -> None:
        payload = {"success": False, "error": message}
        content = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


class ThreadedHTTPServerMixIn:
    """Allows parallel request processing for server mode."""

    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self):
        # Skip HTTPServer's socket.getfqdn() reverse-DNS lookup — it can
        # hang on sandboxed CI runners since it's a real reverse-DNS call.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port
