# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/server/__init__.py

"""Stateless HTTP server daemon internals for pdftl.

Split out of the single `operations/server.py` module: this package holds
everything that runs the actual HTTP request/response cycle and the
subprocess-isolated operation execution, while `operations/server_op.py`
keeps only the `@register_operation` entrypoint, CLI arg parsing, and
process lifecycle (bind/replace/shutdown) concerns.
"""

from pdftl.server.handler import PdftlServerRequestHandlerMixIn, ThreadedHTTPServerMixIn
from pdftl.server.subprocess_workers import (
    _run_single_operation_in_subprocess,
    _run_pipeline_in_subprocess,
    _subprocess_worker_entrypoint,
)

__all__ = [
    "PdftlServerRequestHandlerMixIn",
    "ThreadedHTTPServerMixIn",
    "_run_single_operation_in_subprocess",
    "_run_pipeline_in_subprocess",
    "_subprocess_worker_entrypoint",
]
