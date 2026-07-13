# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Shared server lifecycle helpers/fixtures for server + pipeline integration tests."""

import io
import socket
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import pikepdf
import pytest

from pdftl.operations.server_op import run_server_op


def get_free_port() -> int:
    """Ask the OS for an unused port to avoid cross-worker collisions."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_accepting(host: str, port: int, timeout: float = 0.3) -> bool:
    """Cheap raw-socket liveness probe: does *anything* accept a TCP
    connection on this port right now? This is intentionally decoupled
    from the full HTTP request/response cycle, which can legitimately take
    longer than a tight timeout under CI scheduler/GIL contention (three
    live threads: outgoing server, incoming server, and this poller) even
    when nothing is actually broken. A raw connect either succeeds or gets
    refused almost immediately; it never waits on request processing.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


def wait_for_server(base_url: str, timeout: float = 15.0) -> None:
    """Poll the status endpoint until the server accepts connections."""
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    req = urllib.request.Request(f"{base_url}/v1/status", headers={"Connection": "close"})
    host, port = urllib.parse.urlsplit(base_url).netloc.split(":")
    while time.monotonic() < deadline:
        if not _port_accepting(host, int(port)):
            time.sleep(0.05)
            continue
        try:
            # Something is listening; give the actual HTTP cycle a
            # generous window since GIL/OS contention on CI can make even
            # a healthy response slower than a tight timeout would allow.
            with urllib.request.urlopen(req, timeout=3.0):
                return
        except (urllib.error.URLError, ConnectionRefusedError, OSError) as exc:
            last_exc = exc
            time.sleep(0.1)
    raise AssertionError(f"Server at {base_url} did not become ready in time: {last_exc}")


def wait_until_down(base_url: str, timeout: float = 15.0) -> None:
    """Poll until the status endpoint stops responding (server has shut down)."""
    deadline = time.monotonic() + timeout
    host, port = urllib.parse.urlsplit(base_url).netloc.split(":")
    while time.monotonic() < deadline:
        # A raw connect refusal is the real signal that the listening
        # socket is gone; this avoids waiting on (or being fooled by
        # timing out on) the HTTP layer entirely.
        if not _port_accepting(host, int(port), timeout=0.3):
            return
        time.sleep(0.05)
    raise AssertionError(f"Server at {base_url} did not shut down in time")


def shutdown_server(base_url: str) -> None:
    """POST /v1/shutdown, raising loudly on rejection (an ignored rejection
    leaves the thread alive and causes unrelated later tests to hang)."""
    try:
        req = urllib.request.Request(f"{base_url}/v1/shutdown", data=b"", method="POST")
        with urllib.request.urlopen(req):
            pass
    except urllib.error.HTTPError as exc:
        raise AssertionError(
            f"shutdown request to {base_url} was rejected: {exc.code} {exc.reason}"
        ) from exc
    except (urllib.error.URLError, ConnectionRefusedError):
        pass  # already down


@dataclass
class ManagedServer:
    thread: threading.Thread
    host: str
    port: int
    base_url: str
    errors: list = field(default_factory=list)


@pytest.fixture
def server():
    """Factory fixture: server(port=None, host="127.0.0.1", extra_args=None) -> ManagedServer.

    Guarantees shutdown + thread.join() even if the test raises, so a failed
    assertion can never leak a bound port or a live thread into later tests.
    """
    started: list[ManagedServer] = []

    def _start(port: int | None = None, host: str = "127.0.0.1", extra_args=None) -> ManagedServer:
        # Default to port=0 ("let the OS pick") instead of get_free_port()'s
        # probe-then-close approach: that pattern has a TOCTOU race where
        # another process (notably another xdist worker) can grab the same
        # port between our probe socket closing and the real server binding.
        # Binding with 0 directly in the server process and reading back the
        # actual bound port closes that window entirely. Callers that need a
        # *specific* port (e.g. collision/replace tests) still pass one
        # explicitly and get_free_port() remains available for that.
        requested_port = port if port is not None else 0
        args = [f"port={requested_port}", f"host={host}"] + (extra_args or [])
        errors: list[BaseException] = []
        bound_ports: list[int] = []
        bound_event = threading.Event()

        def _on_bound(actual_port: int) -> None:
            bound_ports.append(actual_port)
            bound_event.set()

        def run():
            try:
                run_server_op(args, _bound_port_callback=_on_bound)
            except Exception as exc:
                errors.append(exc)
                # Unblock the waiter below so a bind failure surfaces
                # promptly instead of via a 15s timeout.
                bound_event.set()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        if not bound_event.wait(timeout=15.0):
            raise RuntimeError(
                f"server thread did not report a bound port within timeout "
                f"(host={host}, requested_port={requested_port})"
            )
        if errors:
            raise RuntimeError(f"server startup failed: {errors[0]!r}") from errors[0]

        actual_port = bound_ports[0]
        base_url = f"http://{host}:{actual_port}"
        try:
            wait_for_server(base_url)
        except AssertionError:
            if errors:
                raise RuntimeError(f"server startup failed: {errors[0]!r}") from errors[0]
            raise
        ms = ManagedServer(
            thread=thread, host=host, port=actual_port, base_url=base_url, errors=errors
        )
        started.append(ms)
        return ms

    yield _start

    # Teardown: runs even if the test body raised.
    for ms in started:
        if ms.thread.is_alive():
            shutdown_server(ms.base_url)
            ms.thread.join(timeout=15.0)
            if ms.thread.is_alive():
                # Don't fail silently: a thread that refuses to die leaves a
                # bound socket (and possibly open pikepdf file handles) alive
                # for the rest of the session, which shows up later as an
                # unrelated, hard-to-diagnose hang or fd-exhaustion failure
                # in some *other* test. Surface it here, at the source.
                raise RuntimeError(
                    f"server thread for {ms.base_url} did not stop cleanly after "
                    "shutdown; a leaked socket/thread will likely break later tests"
                )


def make_pdf_bytes(num_pages: int = 1) -> bytes:
    pdf = pikepdf.new()
    for _ in range(num_pages):
        pdf.add_blank_page()
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def post_multipart(url: str, files: dict[str, bytes], args_value: str) -> Any:
    boundary = "===PIPELINE_TEST_BOUNDARY==="
    parts = []
    for name, content in files.items():
        parts += [
            f"--{boundary}",
            f'Content-Disposition: form-data; name="{name}"; filename="{name}.pdf"',
            "Content-Type: application/pdf",
            "",
            content.decode("latin1"),
        ]
    parts += [
        f"--{boundary}",
        'Content-Disposition: form-data; name="args"',
        "",
        args_value,
        f"--{boundary}--",
        "",
    ]
    body = "\r\n".join(parts).encode("latin1")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    return urllib.request.urlopen(req)
