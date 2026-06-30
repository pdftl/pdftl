# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/import_streams.py

"""Import and apply modified content streams from a text file."""

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, BinaryIO

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.helpers.xobject_helpers import normalize_xobject_stream
from pdftl.utils.keyval_parser import parse_keyval_list
from pdftl.utils.normalize import normalize_page_content_stream
from pdftl.utils.io_helpers import smart_open

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)


@dataclass
class StreamTarget:
    """Represents a structural target in the PDF to apply a stream to."""

    page_num: int
    target_type: str  # "Contents" or "XObject"
    xobject_path: list[str] = field(default_factory=list)


def _parse_target_path(header: str) -> StreamTarget | None:
    """Parse a semantic breadcrumb path like 'Page 1 / XObject /A / XObject /B'."""
    # Remove trailing ID if present for backwards compatibility: " (4:0)"
    header = re.sub(r"\s+\(\d+:\d+\)$", "", header)

    parts = header.split(" / ")
    if not parts or not parts[0].startswith("Page "):
        return None

    try:
        page_num = int(parts[0].split(" ")[1])
    except (IndexError, ValueError):
        return None

    if len(parts) == 2 and parts[1] == "Contents":
        return StreamTarget(page_num, "Contents")

    if len(parts) >= 2 and parts[1].startswith("XObject "):
        xobject_path = []
        for part in parts[1:]:
            if not part.startswith("XObject "):
                return None
            name_parts = part.split(" ", 1)
            xobject_path.append(name_parts[1])
        return StreamTarget(page_num, "XObject", xobject_path=xobject_path)

    return None


def _unescape_content_line(line: bytes) -> bytes:
    """Reverse the escaping `dump_streams` applies to content lines that would
    otherwise be misread as a structural '===' header (or as an escape
    sequence itself). A single leading backslash is stripped if present.
    """
    if line.startswith(b"\\"):
        return line[1:]
    return line


def _flush_target(
    current_target: "StreamTarget | None",
    current_buffer: list[bytes],
    targets: list[tuple["StreamTarget", bytes]],
) -> None:
    """Append the buffered content for *current_target* to *targets*, if any.

    Strips trailing blank lines added as formatting spacing by the writer, and
    skips ALIAS stub blocks (the canonical block updates the shared object
    everywhere, so the stub carries no content to apply).
    """
    if not current_target:
        return
    while current_buffer and not current_buffer[-1]:
        current_buffer.pop()
    content = b"\n".join(current_buffer)
    if not content.lstrip().startswith(b"% ALIAS OF:"):
        targets.append((current_target, content))


def _parse_stream_file(file_obj: BinaryIO) -> list[tuple["StreamTarget", bytes]]:
    """Parse a stream dump text file into targets and their raw byte buffers."""
    targets: list[tuple[StreamTarget, bytes]] = []
    current_target: StreamTarget | None = None
    current_buffer: list[bytes] = []

    for line_bytes in file_obj:
        try:
            # If dump_streams was piped through tools (e.g. sed) it likely outputs as UTF-8.
            # We decode UTF-8 and re-encode to latin-1 to restore the exact 1-to-1 PDF bytes,
            # preventing mojibake of high-byte characters inside `(...) Tj` strings.
            text_str = line_bytes.decode("utf-8")
            clean_bytes = text_str.encode("latin-1")
        except UnicodeError:
            # Explanatory comment: If decoding or encoding fails, it means the bytes are already
            # raw binary or purely latin-1. We fall back safely to preserving raw bytes.
            text_str = line_bytes.decode("latin-1", errors="replace")
            clean_bytes = line_bytes

        line_str = text_str.rstrip("\r\n")

        if line_str.startswith("==="):
            if not line_str.strip("="):
                # Ignore pure structural separator lines like "==================="
                continue

            # Only reset targets when encountering an actual semantic path header
            # (or a Resources block, which also marks the end of the prior target).
            if line_str.startswith("=== Page "):
                header_path = line_str[4:].strip()  # Strip "=== "
                is_resources = header_path.endswith(" / Resources")
                target = None if is_resources else _parse_target_path(header_path)

                if target or is_resources:
                    # Terminate and flush current target block whenever we resolve a
                    # valid new target path, or hit a Resources block. This prevents
                    # warning comments starting with "=== Page " from dropping the
                    # target, while ensuring Resources headers (and their body lines)
                    # don't leak into the previous target's buffer.
                    _flush_target(current_target, current_buffer, targets)
                    current_target = target
                    current_buffer = []
                continue

            # If it's a warning or info line (e.g., "=== Warning:..."), skip it.
            continue

        if current_target is not None:
            current_buffer.append(_unescape_content_line(clean_bytes.rstrip(b"\r\n")))

    # Flush the final block
    _flush_target(current_target, current_buffer, targets)

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
        if not target.xobject_path:
            return
        try:
            # ISO 32000-2 7.8.3 Resource Dictionaries
            current_obj = page
            for xobj_name in target.xobject_path:
                resources = current_obj.Resources
                xobjects = resources.XObject
                current_obj = xobjects[xobj_name]

            # We overwrite the stream content of the existing XObject.
            # This ensures that any other pages referencing this exact object
            # (Shared Form XObjects) inherit the change simultaneously.
            current_obj.write(content)

            if normalize:
                try:
                    current_obj.write(normalize_xobject_stream(current_obj))
                except (pikepdf.PdfError, ValueError, TypeError) as e:
                    # Explanatory comment: Normalization failed on syntax. We keep raw bytes.
                    logger.warning(
                        "Could not normalize imported XObject %s on page %d: %s",
                        " / ".join(target.xobject_path),
                        target.page_num,
                        e,
                    )
        except (AttributeError, KeyError) as e:
            # Explanatory comment: The target page is missing the /Resources or /XObject
            # dictionary in the tree path, meaning the target cannot exist here.
            logger.warning(
                "Could not find XObject path %s on page %d (missing Resources dict): %s",
                " / ".join(target.xobject_path),
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
* `=== Page 1 / XObject /Fm1 / XObject /Fm0` overwrites the nested XObject `/Fm0`.

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
