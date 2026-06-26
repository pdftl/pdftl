# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/tag.py

"""Auto-tag a PDF for accessibility using OpenDataLoader."""

import logging
import os

from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import OperationError
from pdftl.utils.page_specs import page_numbers_matching_page_specs
from pdftl.utils.dependencies import ensure_dependencies
from pdftl.utils.keyval_parser import parse_keyval_list

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)

_TAG_LONG_DESC = """
Auto-tag a PDF file for accessibility (PDF/UA readiness) using OpenDataLoader.

This operation runs the OpenDataLoader auto-tagging pipeline to perform layout analysis,
structure discovery, and inject standard accessibility tags following the Well-Tagged PDF
specification.

### Options

Options are specified as `key=value` arguments:

* `table_method=<default|cluster>`
  Table detection method. `default` is border-based,
`cluster` uses borders and clustering.
* `reading_order=<off|xycut>`
  Reading order algorithm. Default is `xycut`.
* `pages=<spec>`
  Specific pages to tag (e.g., `1,3,5-7`). Default is all pages.
* `threads=<int>`
  Number of worker threads for parallel per-page processing (native Java
pipeline only).
* `hybrid=<off|docling-fast|hancom-ai>`
  Enable a hybrid backend server for advanced layout
analysis.
* `hybrid_mode=<auto|full>`
  Hybrid triage mode. Default is `auto`.
* `hybrid_url=<url>`
  Custom hybrid backend server URL.

Note: This operation requires Java 11+ to be installed on your system and available on your PATH,
as well as the `opendataloader-pdf` Python package. THe `hybrid` options require further
infrastructure to be set up; see the OpenDataLoader documentation for details.

### See also

The `set` operation may be used to set the document language and title, for enhanced accessibility.

"""

_TAG_EXAMPLES = [
    {
        "cmd": "in.pdf tag output out.pdf",
        "desc": "Auto-tag the entire PDF with default settings.",
    },
    {
        "cmd": "in.pdf tag table_method=cluster hybrid=docling-fast output out.pdf",
        "desc": "Auto-tag using the cluster table method and the docling-fast hybrid backend.",
        "test_example": False,
    },
    {
        "cmd": "in.pdf tag 1-5 threads=4 output out.pdf",
        "desc": "Auto-tag only the first 5 pages using 4 parallel threads.",
    },
]


def _jar_path():
    """Borrow the JAR location context manager from the installed package."""
    import importlib.resources as resources

    try:
        ref = resources.files("opendataloader_pdf").joinpath("jar", "opendataloader-pdf-cli.jar")
        return resources.as_file(ref)
    except (ImportError, AttributeError, FileNotFoundError) as exc:
        raise OperationError(
            "Could not resolve OpenDataLoader JAR path. "
            "Ensure opendataloader-pdf is properly installed."
        ) from exc


def _prepare_args(pdf: "pikepdf.Pdf", op_args: list) -> list[str]:
    """Parse option tokens and match bare page specifiers into mapped CLI flags."""
    allowed_opts = {
        "table_method": "--table-method",
        "reading_order": "--reading-order",
        "threads": "--threads",
        "hybrid": "--hybrid",
        "hybrid_mode": "--hybrid-mode",
        "hybrid_url": "--hybrid-url",
    }

    extra_args = []
    bare_args = []
    parsed = parse_keyval_list(op_args, bare_tokens=bare_args, allowed_keys=[*allowed_opts])

    for key in parsed:
        extra_args.extend([allowed_opts[key], parsed[key]])

    if bare_args:
        pages = [
            str(x) for x in sorted(page_numbers_matching_page_specs(bare_args, len(pdf.pages)))
        ]
        extra_args.extend(["--pages", ",".join(pages)])

    return extra_args


def _run_java_pipeline(jar_path: str, in_pdf: str, out_dir: str, extra_args: list[str]) -> None:
    """Execute the external Java OpenDataLoader process over the input file."""
    cmd = [
        "java",
        "-Djava.awt.headless=true",
        "-Dapple.awt.UIElement=true",
        f"-Djava.io.tmpdir={out_dir}",  # Force JVM to drop all intermediate trash here
        "-jar",
        str(jar_path),
        in_pdf,
        "--format",
        "tagged-pdf",
        "--output-dir",
        out_dir,
    ] + extra_args

    logger.debug("Running OpenDataLoader CLI command: %s", " ".join(cmd))

    try:
        import subprocess

        result = subprocess.run(cmd, capture_output=True, check=False)
    except subprocess.SubprocessError as exc:
        raise OperationError(f"Failed to execute the OpenDataLoader subprocess: {exc}") from exc

    if result.returncode != 0:
        stdout_msg = result.stdout.decode("utf-8", errors="replace")
        stdout_firstline = stdout_msg.split("\n", 1)[0]
        if not stdout_msg.strip():
            stdout_firstline = "No logs available."
        stderr_msg = result.stderr.decode("utf-8", errors="replace").rstrip()
        stderr_msg_filtered = "\n".join(
            [x for x in stderr_msg.split("\n") if not x.startswith("\t")]
        )
        raise OperationError(
            f"OpenDataLoader auto-tagging failed with exit code {result.returncode}.\n"
            + " ".join(cmd)
            + f"\nError details: {stdout_firstline}\n{stderr_msg_filtered}"
        )


def _load_and_mark_pdf(out_dir: str, expected_filename: str) -> "pikepdf.Pdf":
    """Find the verified output document and stamp required PDF/UA accessibility keys."""
    base_name = os.path.splitext(expected_filename)[0]

    matched_files = [
        f for f in os.listdir(out_dir) if f.startswith(base_name) and f.lower().endswith(".pdf")
    ]

    if not matched_files:
        raise OperationError(
            "OpenDataLoader execution succeeded, but the tagged PDF was not found."
        )

    out_pdf_path = os.path.join(out_dir, matched_files[0])

    # 1. Open the file, tag it, and extract its memory representation to an unlinked state
    import pikepdf

    with pikepdf.Pdf.open(out_pdf_path) as disk_pdf:
        if "/MarkInfo" not in disk_pdf.Root:
            disk_pdf.Root["/MarkInfo"] = pikepdf.Dictionary({"/Marked": True})
        else:
            disk_pdf.Root["/MarkInfo"]["/Marked"] = True

        # Write out to a new memory buffer
        from io import BytesIO

        mem_buffer = BytesIO()
        disk_pdf.save(mem_buffer)
        mem_buffer.seek(0)

    # At this point, the file block context block closes, releasing out_pdf_path on Windows.
    # 2. Return a memory-backed instance completely isolated from the file system path
    return pikepdf.Pdf.open(mem_buffer)


@register_operation(
    "tag",
    tags=["accessibility", "tagging", "tagged-pdf", "pdf-ua"],
    type="single input operation",
    desc="Auto-tag a PDF for accessibility using OpenDataLoader",
    long_desc=_TAG_LONG_DESC,
    usage="<input> tag [<option>...] output <file>",
    examples=_TAG_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def tag_pdf(pdf: "pikepdf.Pdf", op_args: list) -> OpResult:
    """Auto-tag a PDF using OpenDataLoader and return the tagged pikepdf.Pdf document."""
    ensure_dependencies(
        feature_name="tag",
        dependencies={"opendataloader_pdf": "opendataloader-pdf"},
        extra_tag="tag",
        required_executables=["java"],
    )

    import tempfile

    extra_args = _prepare_args(pdf, op_args)

    # 1. Initialize NamedTemporaryFile with delete=False
    tmp_in = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp_in_path = tmp_in.name

    try:
        # Close the handle immediately so Windows unlocks the file.
        tmp_in.close()

        # Serialize the current pipeline state
        pdf.save(tmp_in_path)

        # Capture the basename (e.g., 'tmp0tl8pu6f.pdf') to locate it deterministically later
        expected_filename = os.path.basename(tmp_in_path)
        tmp_out_dir = tempfile.mkdtemp()

        try:
            # 2. Execute the sub-process
            with _jar_path() as jar:
                _run_java_pipeline(jar, tmp_in_path, tmp_out_dir, extra_args)

            # 3 & 4. Re-parse result explicitly and stamp PDF/UA metadata
            tagged_pdf = _load_and_mark_pdf(tmp_out_dir, expected_filename)
            return OpResult(success=True, pdf=tagged_pdf)

        finally:
            if os.path.exists(tmp_out_dir):
                import shutil

                shutil.rmtree(tmp_out_dir)

    finally:
        # Explicitly clean up the named temporary file since delete=False was used
        if os.path.exists(tmp_in_path):
            os.unlink(tmp_in_path)
