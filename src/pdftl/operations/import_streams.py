# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/import_streams.py

"""Import and apply modified content streams from a text file."""

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.keyval_parser import parse_keyval_list
from pdftl.utils.normalize import normalize_page_content_stream
from pdftl.utils.io_helpers import smart_open

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)

# Matches headers like: "=== Page 1 / Contents" or "=== Page 1 / Contents (3:0)"
CONTENTS_RE = re.compile(r"^===\s+Page\s+(\d+)\s+/\s+Contents(?:\s+\(.*?\)|\s+None)?$")
# Matches headers like: "=== Page 1 / XObject /Fm1" or "=== Page 1 / XObject /Fm1 (5:0)"
XOBJECT_RE = re.compile(r"^===\s+Page\s+(\d+)\s+/\s+XObject\s+(/[^\s]+)(?:\s+\(.*?\))?$")


@dataclass
class StreamTarget:
    """Represents a structural target in the PDF to apply a stream to."""

    page_num: int
    target_type: str  # "Contents" or "XObject"
    name: str | None = None


def _parse_stream_file(file_obj: BinaryIO) -> list[tuple["StreamTarget", bytes]]:
    """Parse a stream dump text file into targets and their raw byte buffers."""
    targets: list[tuple[StreamTarget, bytes]] = []
    current_target: StreamTarget | None = None
    current_buffer: list[bytes] = []

    for line_bytes in file_obj:
        line_str = line_bytes.decode("latin-1").rstrip("\r\n")

        if line_str.startswith("==="):
            # Terminate current target block if we hit a resources block
            if line_str.endswith(" / Resources"):
                if current_target:
                    targets.append((current_target, b"\n".join(current_buffer)))
                current_target = None
                current_buffer = []
                continue

            # Check for Page Contents header
            m_contents = CONTENTS_RE.match(line_str)
            if m_contents:
                if current_target:
                    targets.append((current_target, b"\n".join(current_buffer)))
                current_target = StreamTarget(int(m_contents.group(1)), "Contents")
                current_buffer = []
                continue

            # Check for Page XObject header
            m_xobject = XOBJECT_RE.match(line_str)
            if m_xobject:
                if current_target:
                    targets.append((current_target, b"\n".join(current_buffer)))
                current_target = StreamTarget(
                    int(m_xobject.group(1)), "XObject", m_xobject.group(2)
                )
                current_buffer = []
                continue

            # It's a non-target header block (e.g. "=== Warning:", "================")
            # Safe to skip.
            continue

        if current_target is not None:
            current_buffer.append(line_bytes.rstrip(b"\r\n"))

    # Flush the final block
    if current_target:
        targets.append((current_target, b"\n".join(current_buffer)))

    return targets


def _apply_stream_target(
    pdf: "pikepdf.Pdf", target: StreamTarget, content: bytes, normalize: bool
) -> None:
    """Overwrite the target PDF object with the newly provided byte content.

    Note: if the page's /Contents is currently an array of streams, this
    operation collapses them into a single stream.  That matches the output
    of `dump_streams`, which concatenates array sub-streams before exporting.
    """
    import pikepdf

    try:
        page = pdf.pages[target.page_num - 1]
    except IndexError:
        # Explanatory comment: User requested applying a stream to a page that
        # does not exist in the current document. We safely ignore it.
        logger.warning("Page %d does not exist in the PDF, skipping", target.page_num)
        return

    if target.target_type == "Contents":
        if isinstance(page.get("/Contents"), pikepdf.Array):
            logger.warning(
                "Page %d /Contents is an array of streams; collapsing to a single stream",
                target.page_num,
            )
        page.Contents = pdf.make_stream(content)
        if normalize:
            try:
                normalize_page_content_stream(pdf, page)
            except (pikepdf.PdfError, ValueError, TypeError) as e:
                # Explanatory comment: We attempted to parse and normalize the imported stream,
                # but it was syntactically invalid. We keep the raw bytes exactly as provided.
                logger.warning(
                    "Could not normalize imported Contents stream for page %d: %s",
                    target.page_num,
                    e,
                )

    elif target.target_type == "XObject":
        try:
            # ISO 32000-2 7.8.3 Resource Dictionaries
            resources = page.Resources
            xobjects = resources.XObject
            if target.name in xobjects:
                xobj = xobjects[target.name]
                # We overwrite the stream content of the existing XObject.
                # This ensures that any other pages referencing this exact object
                # (Shared Form XObjects) inherit the change simultaneously.
                xobj.write(content)
                if normalize:
                    try:
                        parsed = pikepdf.parse_content_stream(xobj)
                        normalized = pikepdf.unparse_content_stream(parsed)
                        xobj.write(normalized)
                    except (pikepdf.PdfError, ValueError, TypeError) as e:
                        # Explanatory comment: Normalization failed on syntax. We keep raw bytes.
                        logger.warning(
                            "Could not normalize imported XObject %s on page %d: %s",
                            target.name,
                            target.page_num,
                            e,
                        )
            else:
                logger.warning(
                    "XObject %s not found on page %d, skipping", target.name, target.page_num
                )
        except (AttributeError, KeyError) as e:
            # Explanatory comment: The target page is missing the /Resources or /XObject
            # dictionary entirely, meaning the target cannot possibly exist on this page.
            logger.warning(
                "Could not find XObject %s on page %d (missing Resources dict): %s",
                target.name,
                target.page_num,
                e,
            )


_IMPORT_STREAMS_LONG_DESC = """
The `import_streams` operation reads a text file containing PDF content
streams and applies them directly into the target PDF document.

It is designed to perfectly round-trip the output from `dump_streams`.
The primary workflow is to dump a document's streams to text, use standard
Unix text tools (like `sed`, `awk`, or manual text editing) to modify the
PDF operators, and then pipe the resulting text file back into `pdftl`
using `import_streams`.

### Addressing Mechanism
The operation strictly targets semantic paths described in the headers,
ignoring any internal Object IDs (`(4:0)`).
* `=== Page 1 / Contents` overwrites the main content stream of Page 1.
* `=== Page 2 / XObject /Fm1` overwrites the stream of the XObject mapped as
  `/Fm1` within Page 2's resources.

Because it relies on semantic paths, you can easily use text tools to
migrate streams across entirely different PDF documents by simply swapping
the header paths (e.g. using `sed` to rename the target XObject).

### Options
* `normalize=true` (default) — Before writing the stream into the PDF,
  `pdftl` uses `pikepdf`'s internal engine to parse and normalize the syntax.
  This automatically strips any `%` comments (such as those generated by
  `dump_streams annotate`), cleans up whitespace, and validates the PDF syntax.
  Pass `normalize=false` if you want to force raw text byte injection.
"""

_IMPORT_STREAMS_EXAMPLES = [
    {
        "cmd": (
            "in.pdf dump_streams 1 | sed -e 's/Apple/Banana/' | "
            "pdftl in.pdf import_streams - output out.pdf"
        ),
        "desc": "Dump, patch, and re-import Page 1's content stream using a Unix pipe",
        "test_example": False,
    },
    {
        "cmd": "in.pdf import_streams patched_streams.txt output out.pdf",
        "desc": "Apply modifications from a saved text file back into the document",
    },
    {
        "cmd": "in.pdf import_streams normalize=false streams.txt output out.pdf",
        "desc": "Inject stream text verbatim, without parsing or stripping comments",
    },
]


@register_operation(
    "import_streams",
    tags=["in_place", "content_stream", "dangerous"],
    type="single input operation",
    desc="Import and apply modified content streams",
    long_desc=_IMPORT_STREAMS_LONG_DESC,
    usage="<input> import_streams [normalize=true] <stream_file> output <output>",
    examples=_IMPORT_STREAMS_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def import_streams(pdf: "pikepdf.Pdf", specs: list[str]) -> OpResult:
    """Import and apply streams into the PDF."""
    positional: list[str] = []
    kvs = parse_keyval_list(
        specs,
        bare_tokens=positional,
        allowed_keys=["normalize"],
        lowercase_values=True,
        context="import_streams",
    )

    normalize = kvs.get("normalize", "true") != "false"

    if not positional:
        raise InvalidArgumentError(
            "import_streams requires a <stream_file> argument (or '-' for stdin)."
        )

    stream_file = positional[0]

    # Binary read mode handles both normal files and sys.stdin.buffer fallback seamlessly.
    # smart_open treats None as stdin; we pass the raw token and let it own the sentinel logic.
    with smart_open(stream_file if stream_file != "-" else None, "rb") as f:
        targets = _parse_stream_file(f)

    for target, content in targets:
        _apply_stream_target(pdf, target, content, normalize)

    return OpResult(success=True, pdf=pdf)
