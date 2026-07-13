# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tools/client.py

"""
Generic CLI Client Utility for interacting with the pdftl HTTP Daemon Server.
Sends multi-part form payloads, handles file streaming, and renders response headers.
"""

import argparse
import json
import os
import sys
from typing import Any
import urllib.error
import urllib.request


def print_success(msg: str) -> None:
    print(f"\033[92m[+] {msg}\033[0m")


def print_info(msg: str) -> None:
    print(f"\033[94m[*] {msg}\033[0m")


def print_error(msg: str) -> None:
    print(f"\033[91m[!] {msg}\033[0m", file=sys.stderr)


class MultipartFormEncoder:
    """Helper class to encode multipart/form-data without external dependencies."""

    def __init__(self) -> None:
        self.boundary = "===PDFTL_CLIENT_BOUNDARY_MARKER==="
        self.parts: list[bytes] = []

    def add_field(self, name: str, value: str) -> None:
        """Add a simple text field part."""
        self.parts.append(f"--{self.boundary}".encode())
        self.parts.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        self.parts.append(b"")
        self.parts.append(value.encode("utf-8"))

    def add_file(self, name: str, filename: str, content: bytes) -> None:
        """Add a binary file part."""
        self.parts.append(f"--{self.boundary}".encode())
        self.parts.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode()
        )
        self.parts.append(b"Content-Type: application/pdf")
        self.parts.append(b"")
        self.parts.append(content)

    def finalize(self) -> tuple[bytes, str]:
        """Consolidate boundaries and output content types."""
        self.parts.append(f"--{self.boundary}--".encode())
        self.parts.append(b"")
        body = b"\r\n".join(self.parts)
        content_type = f"multipart/form-data; boundary={self.boundary}"
        return body, content_type


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generic Command Line client to interact with the pdftl REST daemon."
    )
    parser.add_argument(
        "operation",
        help="The PDF processing operation to run (e.g. crop, rotate, status, create, tag)",
    )
    parser.add_argument(
        "--file",
        "-f",
        action="append",
        default=[],
        help=(
            "Path to an input PDF document to send. Can be specified multiple times. "
            "Supports handle mapping, e.g. -f A=first.pdf -f B=second.pdf"
        ),
    )
    parser.add_argument(
        "--args",
        "-a",
        help="Arguments to pass to the operation. Can be a raw string or a JSON array.",
    )
    parser.add_argument(
        "--output", "-o", help="Path where the processed output PDF should be saved."
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address where the server is running (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=4080,
        help="Port of the running pdftl server (default: 4080)",
    )
    return parser.parse_args()


def _handle_status_query(base_url: str) -> None:
    print_info(f"Querying status index at {base_url}/v1/status")
    try:
        with urllib.request.urlopen(f"{base_url}/v1/status") as response:
            payload = json.loads(response.read().decode("utf-8"))
            print_success("Connection established! Current server metadata:")
            print(json.dumps(payload, indent=2))
            sys.exit(0)
    except urllib.error.URLError as err:
        print_error(f"Failed to connect to the server: {err}")
        sys.exit(1)


def _parse_file_spec(idx: int, file_spec: str, total_files: int) -> tuple[str, str]:
    """Extract file part name and physical path."""
    if "=" in file_spec:
        return file_spec.split("=", 1)
    part_name = f"file_{idx}" if total_files > 1 else "file"
    return part_name, file_spec


def _add_file_part(encoder: MultipartFormEncoder, part_name: str, file_path: str) -> None:
    """Read a file and add it to the multi-part payload."""
    if not os.path.exists(file_path):
        print_error(f"Input file not found on disk: {file_path}")
        sys.exit(1)

    print_info(
        f"Reading target input file into memory: {file_path} (uploading as handle '{part_name}')"
    )
    with open(file_path, "rb") as f:
        pdf_bytes = f.read()
    encoder.add_file(part_name, os.path.basename(file_path), pdf_bytes)


def _add_args_part(encoder: MultipartFormEncoder, args_str: str) -> None:
    """Determine JSON list formats and add to payload fields."""
    try:
        json.loads(args_str)
        encoder.add_field("args", args_str)
    except ValueError:
        encoder.add_field("args", json.dumps([args_str]))


def _prepare_multipart_body(files: list[str], args_str: str | None) -> tuple[bytes, str]:
    encoder = MultipartFormEncoder()

    if files:
        for idx, file_spec in enumerate(files):
            part_name, file_path = _parse_file_spec(idx, file_spec, len(files))
            _add_file_part(encoder, part_name, file_path)

    if args_str:
        _add_args_part(encoder, args_str)

    return encoder.finalize()


def _process_response(response: Any, operation: str, output_override: str | None) -> None:
    res_content_type = response.headers.get("Content-Type", "")

    # Scenario A: Result is binary PDF content
    if "application/pdf" in res_content_type:
        output_path = output_override or f"processed_{operation}.pdf"
        print_success(f"Received PDF byte payload. Writing directly to: {output_path}")
        with open(output_path, "wb") as out_file:
            out_file.write(response.read())
        print_success("File saved successfully!")

    # Scenario B: Result is JSON or raw text
    else:
        raw_response = response.read().decode("utf-8")
        try:
            formatted_json = json.loads(raw_response)
            print_success("Received operational success response JSON:")
            print(json.dumps(formatted_json, indent=2))
        except ValueError:
            print_success(f"Received plain text response:\n{raw_response}")


def main() -> None:
    args = parse_arguments()
    base_url = f"http://{args.host}:{args.port}"

    # Handle status lookup directly
    if args.operation == "status":
        _handle_status_query(base_url)
        return

    # Initialize execution request
    execute_url = f"{base_url}/v1/execute/{args.operation}"
    print_info(f"Preparing execution request: {args.operation.upper()} -> {execute_url}")

    body, content_type = _prepare_multipart_body(args.file, args.args)

    request = urllib.request.Request(
        execute_url,
        data=body,
        headers={"Content-Type": content_type, "Content-Length": str(len(body))},
        method="POST",
    )

    try:
        print_info("Streaming multipart bytes to the remote server...")
        with urllib.request.urlopen(request) as response:
            _process_response(response, args.operation, args.output)

    except urllib.error.HTTPError as err:
        print_error(f"Server returned an execution error [HTTP {err.code}]")
        try:
            err_body = json.loads(err.read().decode("utf-8"))
            print_error(f"Diagnostic Error Message: {err_body.get('error', 'Unknown Error')}")
        except (ValueError, OSError):
            print_error(f"Details: {err.reason}")
        sys.exit(1)
    except urllib.error.URLError as err:
        print_error(f"Network transport level connection failure: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
