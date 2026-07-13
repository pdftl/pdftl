# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/server_op.py

"""Registration, CLI arg parsing, and process lifecycle for the `server`
operation. The actual HTTP handler, multipart parsing, and subprocess
execution live in `pdftl.server` (see src/pdftl/server/)."""

import os
import logging
import socketserver
import time
from typing import TYPE_CHECKING
from collections.abc import Callable

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.utils.keyval_parser import parse_keyval_list, constrained_int
from pdftl.exceptions import OperationError, UserCommandLineError, InvalidArgumentError
from pdftl.server import PdftlServerRequestHandlerMixIn, ThreadedHTTPServerMixIn

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)

# Defaults for the two resource-abuse guardrails below. Chosen to be
# generous enough not to interfere with legitimate large-PDF workflows,
# while still bounding worst-case memory/CPU exposure on a public deployment.
DEFAULT_MAX_UPLOAD_MB = 100
DEFAULT_TIMEOUT_SECONDS = 300


def _is_loopback_host(host: str) -> bool:
    """True if `host` only ever accepts local-machine connections.

    Conservative on purpose: anything not explicitly recognized (including
    "0.0.0.0", which binds all interfaces) is treated as non-loopback, so
    the mandatory-token check below fails closed rather than open.
    """
    return host in ("127.0.0.1", "localhost", "::1")


def _require_shutdown_token_if_non_loopback(host: str) -> None:
    """Refuses to proceed on a non-loopback host without a shutdown token set.

    Extracted as its own function (rather than inlined in run_server_op) so
    this guard can be unit tested without exercising a real socket bind.
    """
    if _is_loopback_host(host):
        return
    if not os.environ.get("PDFTL_SERVER_SHUTDOWN_TOKEN"):
        raise OperationError(
            f"Refusing to bind to non-loopback host '{host}' without "
            "PDFTL_SERVER_SHUTDOWN_TOKEN set. Binding to a non-loopback "
            "address exposes the shutdown endpoint (and the rest of the "
            "API) to the network. Set the environment variable before "
            "starting the server, e.g.:\n"
            '  export PDFTL_SERVER_SHUTDOWN_TOKEN="$(openssl rand -hex 32)"\n'
            "or bind to 127.0.0.1 / localhost for local-only use."
        )


_SERVER_LONG_DESC = r"""
Starts a stateless pdftl API server.
Exposes all pdftl operations over HTTP.

Syntax: `server [port=<port>] [host=<host>] [replace] [max_upload_mb=<mb>] [timeout=<seconds>]`

Arguments:

* `port`: Port number to listen on (default: 4080).

* `host`: Host address to bind to (default: 127.0.0.1).

* `replace`: Shutdown an existing active server on this port before binding.

* `max_upload_mb`: Maximum accepted request body size, in megabytes
  (default: 100). Requests whose `Content-Length` exceeds this are
  rejected with HTTP 413 before the body is read. Raise this if you
  routinely process very large PDFs.

* `timeout`: Maximum wall-clock seconds allowed for a single operation
  or pipeline request to run before it is abandoned with HTTP 504
  (default: 300). The operation runs in an isolated child process; if
  the timeout is exceeded, that process is forcibly terminated (SIGTERM,
  escalating to SIGKILL if needed), so this bounds both response latency
  and actual CPU/memory usage of a runaway operation.

## Authentication

By default the server has **no authentication on any endpoint** -- this
is intentional for local/trusted-network use. The one exception is
binding to a non-loopback `host` (anything other than `127.0.0.1`,
`localhost`, or `::1`, including `0.0.0.0`): in that case,
`PDFTL_SERVER_SHUTDOWN_TOKEN` **must** be set in the environment before
starting the server, or it refuses to bind at all. This exists solely to
stop an unauthenticated `/v1/shutdown` from being reachable from the
network; it does not gate any other endpoint.

```
export PDFTL_SERVER_SHUTDOWN_TOKEN="$(openssl rand -hex 32)"
pdftl server host=0.0.0.0 port=4080
```

Include the token on shutdown requests:

```
curl -X POST http://your-host:4080/v1/shutdown \
  -H "X-Shutdown-Token: $PDFTL_SERVER_SHUTDOWN_TOKEN"
```

`server replace` also forwards this token automatically (read from the
same environment variable) when shutting down the instance it's
replacing.

## Calling the server from cURL

We outline how to interact with the stateless `pdftl` HTTP server directly
using standard `curl` commands.
Of course, other clients can be used.

The daemon by default binds to:

* **Base URL:** `http://127.0.0.1:4080`

### 1. System Operations

#### Query Daemon Status and Registry

To retrieve active server information, version metadata, and a dynamically reflected list
of supported operations:

```
curl -X GET http://127.0.0.1:4080/v1/status
```

#### Shutdown the Server

To cleanly terminate the loopback socket listener administratively:

```
curl -X POST http://127.0.0.1:4080/v1/shutdown
```

If a shutdown token is configured (mandatory for non-loopback binds, see
above), include it:

```
curl -X POST http://127.0.0.1:4080/v1/shutdown \
  -H "X-Shutdown-Token: $PDFTL_SERVER_SHUTDOWN_TOKEN"
```

### 2. Document Processing Operations

All execution endpoints follow the format: `POST /v1/execute/{operation}`.
Payloads are submitted as `multipart/form-data`.

#### A. Zero-Input Operations (e.g., `create`)

Operations like `create` generate a new document from scratch and do not require uploading a
seed PDF. Pass configuration strings in the `args` field:

```
curl -X POST http://127.0.0.1:4080/v1/execute/create \
  -F "args=[\"1(A4)\"]" \
  --output blank_page.pdf
```

#### B. Single-Input Operations (e.g., `crop`)

To send an existing PDF file to be processed, pass the document in the `file` parameter.
You can submit parameters via a JSON array in the `args` field:

```
curl -X POST http://127.0.0.1:4080/v1/execute/crop \
  -F "file=@input.pdf" \
  -F "args=[\"10pt,20pt\"]" \
  --output cropped.pdf
```

> **Tip:** If the operation returns a modified PDF, write it directly to a file
using `--output` or stream it.

#### C. Multi-Input Operations (e.g., `cat`)

The server dynamically translates named form-data parts (e.g. `A`, `B`)
into operational handle aliases.
This allows you to upload multiple files at once and reference them in your sequence arguments:

```
curl -X POST http://127.0.0.1:4080/v1/execute/cat \
  -F "A=@first.pdf" \
  -F "B=@second.pdf" \
  -F "args=[\"A\", \"B\"]" \
  --output merged.pdf
```

##### Selective Page Splicing

You can also reference page-specific slices inside your handles:

```
curl -X POST http://127.0.0.1:4080/v1/execute/cat \
  -F "A=@first.pdf" \
  -F "B=@second.pdf" \
  -F "args=[\"A1-3\", \"B2-end\"]" \
  --output spliced.pdf
```

#### D. JSON Extraction Operations (e.g., `dump_data`)

For operations that return structural text or JSON payloads instead of a PDF file,
the server responds with raw text/JSON containing metadata content:

```
curl -X POST http://127.0.0.1:4080/v1/execute/dump_data \
  -F "file=@input.pdf" \
  -F "args=[\"json\"]"
```

#### E. Pipelines (multiple operations in one request)

To chain several operations together server-side — without downloading and
re-uploading an intermediate file — POST to `/v1/execute/pipeline` instead
of a single operation name. The `args` field becomes a JSON array of step
objects, each naming an `operation` and its own `args`:

```
curl -X POST http://127.0.0.1:4080/v1/execute/pipeline \
  -F "A=@first.pdf" \
  -F "B=@second.pdf" \
  -F 'args=[{"operation": "cat", "args": ["A", "B"]}, {"operation": "rotate", "args": ["left"]}]' \
  --output merged_rotated.pdf
```

Uploaded file handles (`A`, `B`, ...) are visible to every step, matching
the handle behaviour of the CLI pipeline. Each step after the first
implicitly receives the previous step's output, unless it specifies its
own `inputs` list of handle names.

The final step may include an `options` object to control output
encryption, e.g. `{"operation": "rotate", "args": ["right"], "options":
{"owner_pw": "secret", "encrypt_aes256": true}}`.

### 3. Limits

Requests exceeding `max_upload_mb` receive HTTP 413. Requests whose
operation runs longer than `timeout` seconds receive HTTP 504.

"""


def _parse_server_args(args: list[str]) -> tuple[str, int, bool, int, int]:
    """Configure networking host interfaces, binding port ranges, upload
    size / timeout limits, and replace flags.

    Returns (host, port, replace, max_upload_bytes, timeout_seconds).
    """
    bare_tokens: list[str] = []
    try:
        kv_args = parse_keyval_list(
            args,
            schema={
                "port": constrained_int(0, 65535),
                "host": str,
                "max_upload_mb": constrained_int(1),
                "timeout": constrained_int(1),
            },
            bare_tokens=bare_tokens,
            context="server",
        )
    except InvalidArgumentError as e:
        raise UserCommandLineError(str(e))

    port = kv_args.get("port", 4080)
    host = kv_args.get("host", "127.0.0.1")
    max_upload_mb = kv_args.get("max_upload_mb", DEFAULT_MAX_UPLOAD_MB)
    timeout_seconds = kv_args.get("timeout", DEFAULT_TIMEOUT_SECONDS)

    replace = False
    for token in bare_tokens:
        if token == "replace":
            replace = True
        else:
            raise UserCommandLineError(
                f"Unexpected argument: '{token}'. Expected port=<int>, host=<str>, "
                "max_upload_mb=<int>, timeout=<int>, or replace."
            )

    return host, port, replace, max_upload_mb * 1024 * 1024, timeout_seconds


def _attempt_replace_existing(host: str, port: int) -> None:
    """Sends a shutdown sequence to an existinon active on the targeted port."""
    import urllib.request
    import urllib.error

    shutdown_url = f"http://{host}:{port}/v1/shutdown"
    token = os.environ.get("PDFTL_SERVER_SHUTDOWN_TOKEN")
    headers = {"X-Shutdown-Token": token} if token else {}
    req = urllib.request.Request(shutdown_url, data=b"", method="POST", headers=headers)
    try:
        # Give a short timeout to prevent blocking on inactive connections
        with urllib.request.urlopen(req, timeout=1.0) as response:
            if response.status == 200:
                logger.info("Sent shutdown signal to active server on %s:%s", host, port)
                time.sleep(1.0)  # Pause to allow the socket interface to free the port
    except (urllib.error.URLError, ConnectionRefusedError, OSError) as exc:
        # If the server is offline or doesn't exist, ignore and proceed
        logger.debug("Failed to connect to existing server for replacement: %s", exc)


def _port_is_occupied(host: str, port: int) -> bool:
    """Checks whether something is actively listening on host:port.

    Uses a real connect() probe rather than relying on bind()'s
    SO_REUSEADDR semantics, which differ across platforms (Windows permits
    rebinding a live listener; POSIX blocks rebinding but also blocks a
    stale TIME_WAIT socket that isn't actually occupied).
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        try:
            sock.connect((host, port))
            return True
        except (ConnectionRefusedError, OSError):
            return False


def _sweep_stale_pipeline_temp_files() -> None:
    """Removes any pdftl_server_*.pdf temp files left behind by a prior
    process that didn't exit cleanly (e.g. was killed mid-request).

    Best-effort: individual removal failures are logged at debug level and
    swallowed -- this must never prevent the server from starting.
    """
    import glob
    import tempfile
    import time

    STALE_TIME_S = 300

    now = time.time()
    for label in ("server", "upload"):
        pattern = os.path.join(tempfile.gettempdir(), f"pdftl_{label}_*.pdf")
        for stale_path in glob.glob(pattern):
            try:
                if now - os.path.getmtime(stale_path) > STALE_TIME_S:
                    os.remove(stale_path)
                logger.info("Removed stale temp file from a previous run: %s", stale_path)
            except OSError as exc:
                logger.debug("Could not remove stale temp file %s: %s", stale_path, exc)


@register_operation(
    "server",
    tags=["server", "utility"],
    type="source operation",  # No input PDF required
    desc="Start the pdftl API server",
    long_desc=_SERVER_LONG_DESC,
    usage="server [port=] [host=] [replace] [max_upload_mb=] [timeout=]",
    examples=[  # these do not terminate, so we do not test them automatically
        {
            "cmd": "server",
            "desc": "Start the server on default localhost:4080.",
            "test_example": False,
        },
        {
            "cmd": "server port=8080 host=0.0.0.0 replace",
            "desc": (
                "Shutdown any active server on port 8080, then start binding to all "
                "interfaces. Requires PDFTL_SERVER_SHUTDOWN_TOKEN to be set in the "
                "environment, since host is non-loopback."
            ),
            "test_example": False,
        },
        {
            "cmd": "server max_upload_mb=500 timeout=600",
            "desc": "Start on default localhost:4080 with higher upload/timeout limits.",
            "test_example": False,
        },
    ],
    args=([c.OPERATION_ARGS], {}),
)
def run_server_op(
    args: list[str],
    _bound_port_callback: Callable[[int], None] | None = None,
) -> OpResult:
    """Bootstrap and start the stateless HTTP daemon server."""
    import http.server

    host, port, replace, max_upload_bytes, timeout_seconds = _parse_server_args(args)

    _require_shutdown_token_if_non_loopback(host)

    _sweep_stale_pipeline_temp_files()

    if replace:
        _attempt_replace_existing(host, port)
    elif _port_is_occupied(host, port):
        raise OperationError(
            f"Failed to start server on {host}:{port}: address already in use. "
            "If the address is already in use, consider using the 'replace' "
            "keyword argument: 'server replace'"
        )

    class ThreadedHTTPServer(
        ThreadedHTTPServerMixIn, socketserver.ThreadingMixIn, http.server.HTTPServer
    ):
        pass

    class PdftlServerRequestHandler(
        PdftlServerRequestHandlerMixIn, http.server.BaseHTTPRequestHandler
    ):
        pass

    PdftlServerRequestHandler.max_upload_bytes = max_upload_bytes
    PdftlServerRequestHandler.op_timeout_seconds = timeout_seconds
    try:
        # Start loopback socket handler
        with ThreadedHTTPServer((host, port), PdftlServerRequestHandler) as server:
            # When port=0 was requested, the OS picks an ephemeral port; report
            # the *actual* bound port back to any caller that needs to know it
            # (e.g. test harnesses) before we block in serve_forever().
            bound_port = server.server_address[1]
            if _bound_port_callback is not None:
                _bound_port_callback(bound_port)
            logger.info("pdftl API Server started successfully at http://%s:%s/", host, bound_port)
            logger.info("Press Ctrl+C to terminate or POST /v1/shutdown to exit.")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                logger.info("Shutdown signal received. Exiting.")
    except OSError as exc:
        raise OperationError(
            f"Failed to start server on {host}:{port}: {exc.strerror or str(exc)}. "
            "If the address is already in use, consider using the 'replace' "
            "keyword argument: 'server replace'"
        ) from exc

    return OpResult(success=True)
