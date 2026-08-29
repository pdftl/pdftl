# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/import_streams.py

"""Import and apply modified content streams from a text file."""

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, BinaryIO, Any

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
    """Represents a structural target in the PDF to apply a stream to.

    `target_type` is the root kind the path resolves to first: "Contents",
    "XObject", "Pattern", "SMask", or "Annotation". `xobject_path` is always
    the *nested* Form XObject chain applied after resolving the root (empty
    for a path that terminates at the root itself) -- for target_type
    "XObject" the root IS the first path element, so xobject_path there is
    the full chain including it, matching the pre-existing behavior.
    """

    page_num: int
    target_type: str  # "Contents" | "XObject" | "Pattern" | "SMask" | "Annotation"
    xobject_path: list[str] = field(default_factory=list)
    pattern_name: str | None = None
    extgstate_name: str | None = None
    annot_index: int | None = None  # 0-based, converted from the 1-based breadcrumb
    ap_key: str | None = None
    ap_state: str | None = None


def _parse_xobject_chain(parts: list[str]) -> list[str] | None:
    """Parse a sequence of 'XObject <name>' segments into a name list, or
    None if any segment doesn't match. Shared by the top-level XObject
    target and by the nested-XObject suffix of Pattern/SMask/Annotation
    targets.

    Each part is checked with `startswith("XObject ")` before being
    split, which guarantees a space is present -- so split(" ", 1) always
    yields exactly 2 elements here; no length check is reachable to test.
    """
    xobject_path = []
    for part in parts:
        if not part.startswith("XObject "):
            return None
        xobject_path.append(part.split(" ", 1)[1])
    return xobject_path


def _parse_pattern_target(page_num: int, parts: list[str]) -> StreamTarget | None:
    """Parse 'Pattern <name>' [ / 'XObject <name>' ]...

    parts[0] is guaranteed by the caller's `startswith("Pattern ")` guard
    to contain a space, so split(" ", 1) always yields 2 elements here --
    no length check needed (and none is reachable to test).
    """
    pattern_name = parts[0].split(" ", 1)[1]
    nested = _parse_xobject_chain(parts[1:]) if len(parts) > 1 else []
    if nested is None:
        return None
    return StreamTarget(page_num, "Pattern", xobject_path=nested, pattern_name=pattern_name)


def _parse_smask_target(page_num: int, parts: list[str]) -> StreamTarget | None:
    """Parse 'ExtGState <name> / SMask' [ / 'XObject <name>' ]...

    parts[1] is guaranteed by the caller's `startswith("ExtGState ")`
    guard to contain a space -- same reasoning as _parse_pattern_target.
    """
    extgstate_name = parts[1].split(" ", 1)[1]
    nested = _parse_xobject_chain(parts[3:]) if len(parts) > 3 else []
    if nested is None:
        return None
    return StreamTarget(page_num, "SMask", xobject_path=nested, extgstate_name=extgstate_name)


def _parse_annotation_target(page_num: int, parts: list[str]) -> StreamTarget | None:
    """Parse 'Annot <1-based-index> / AP <key>' [ / 'State <state>' ]
    [ / 'XObject <name>' ]...

    parts[1] and parts[2] are guaranteed by the caller's `startswith`
    guards to contain a space, so split(" ", 1) always yields 2 elements
    for those -- only the numeric index conversion can actually fail.
    """
    try:
        annot_index = int(parts[1].split(" ", 1)[1]) - 1
    except ValueError:
        return None
    if annot_index < 0:
        return None

    ap_key = parts[2].split(" ", 1)[1]

    remaining = parts[3:]
    ap_state = None
    if remaining and remaining[0].startswith("State "):
        ap_state = remaining[0].split(" ", 1)[1]
        remaining = remaining[1:]

    nested = _parse_xobject_chain(remaining) if remaining else []
    if nested is None:
        return None

    return StreamTarget(
        page_num,
        "Annotation",
        xobject_path=nested,
        annot_index=annot_index,
        ap_key=ap_key,
        ap_state=ap_state,
    )


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
        xobject_path = _parse_xobject_chain(parts[1:])
        if xobject_path is None:
            return None
        return StreamTarget(page_num, "XObject", xobject_path=xobject_path)

    if len(parts) >= 2 and parts[1].startswith("Pattern "):
        return _parse_pattern_target(page_num, parts[1:])

    if len(parts) >= 3 and parts[1].startswith("ExtGState ") and parts[2] == "SMask":
        return _parse_smask_target(page_num, parts)

    if len(parts) >= 3 and parts[1].startswith("Annot ") and parts[2].startswith("AP "):
        return _parse_annotation_target(page_num, parts)

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


def _decode_stream_line(line_bytes: bytes) -> tuple[str, bytes]:
    """Helper line transcoder translating incoming bytes cleanly into Unicode representations."""
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
    return text_str, clean_bytes


def _handle_stream_header(line_str: str) -> tuple[StreamTarget | None, bool, bool]:
    """Resolves and parses layout page paths or resources from headers.

    Returns: (parsed_target, is_resources_header, is_valid_header).
    """
    if not line_str.strip("="):
        # Ignore pure structural separator lines like "==================="
        return None, False, False

    if line_str.startswith("=== Page "):
        header_path = line_str[4:].strip()  # Strip "=== "
        is_resources = header_path.endswith(" / Resources")
        target = None if is_resources else _parse_target_path(header_path)
        return target, is_resources, True

    return None, False, False


def _parse_stream_file(file_obj: BinaryIO) -> list[tuple["StreamTarget", bytes]]:
    """Parse a stream dump text file into targets and their raw byte buffers."""
    targets: list[tuple[StreamTarget, bytes]] = []
    current_target: StreamTarget | None = None
    current_buffer: list[bytes] = []

    for line_bytes in file_obj:
        text_str, clean_bytes = _decode_stream_line(line_bytes)
        line_str = text_str.rstrip("\r\n")

        if line_str.startswith("==="):
            target, is_resources, is_valid = _handle_stream_header(line_str)
            if is_valid and (target or is_resources):
                # Terminate and flush current target block whenever we resolve a
                # valid new target path, or hit a Resources block. This prevents
                # warning comments starting with "=== Page " from dropping the
                # target, while ensuring Resources headers (and their body lines)
                # don't leak into the previous target's buffer.
                _flush_target(current_target, current_buffer, targets)
                current_target = target
                current_buffer = []
            continue

        if current_target is not None:
            current_buffer.append(_unescape_content_line(clean_bytes.rstrip(b"\r\n")))

    # Flush the final block
    _flush_target(current_target, current_buffer, targets)

    return targets


def _apply_contents_target(
    pdf: "pikepdf.Pdf", page: Any, page_num: int, content: bytes, normalize: bool
) -> None:
    """Helper method executing /Contents stream injection and standard normalization."""
    import pikepdf

    if isinstance(page.get("/Contents"), pikepdf.Array):
        logger.warning(
            "Page %d /Contents is an array of streams; collapsing to a single stream",
            page_num,
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
                page_num,
                e,
            )


def _descend_xobject_path(start_obj: Any, xobject_path: list[str]) -> Any:
    """Walk a chain of nested Form XObject names starting from `start_obj`
    (a page, Pattern, SMask group, or annotation appearance stream, any of
    which may carry its own /Resources/XObject dict), returning the final
    stream object. Raises AttributeError/KeyError if any hop is missing,
    same as the original inline loop this was extracted from."""
    current_obj = start_obj
    for xobj_name in xobject_path:
        resources = current_obj.Resources
        xobjects = resources.XObject
        current_obj = xobjects[xobj_name]
    return current_obj


def _write_normalized(obj: Any, content: bytes, normalize: bool, warn_context: str) -> None:
    """Write `content` to `obj`, then optionally re-write it normalized,
    falling back to the raw bytes already written if normalization fails."""
    import pikepdf

    obj.write(content)
    if normalize:
        try:
            obj.write(normalize_xobject_stream(obj))
        except (pikepdf.PdfError, ValueError, TypeError) as e:
            # Explanatory comment: Normalization failed on syntax. We keep raw bytes.
            logger.warning("Could not normalize imported %s: %s", warn_context, e)


def _apply_xobject_target(
    page: Any, page_num: int, xobject_path: list[str], content: bytes, normalize: bool
) -> None:
    """Helper method executing /XObject stream extraction and structural replacement."""
    if not xobject_path:
        return
    try:
        # ISO 32000-2 7.8.3 Resource Dictionaries
        current_obj = _descend_xobject_path(page, xobject_path)

        # We overwrite the stream content of the existing XObject.
        # This ensures that any other pages referencing this exact object
        # (Shared Form XObjects) inherit the change simultaneously.
        _write_normalized(
            current_obj,
            content,
            normalize,
            f"XObject {' / '.join(xobject_path)} on page {page_num}",
        )
    except (AttributeError, KeyError) as e:
        # Explanatory comment: The target page is missing the /Resources or /XObject
        # dictionary in the tree path, meaning the target cannot exist here.
        logger.warning(
            "Could not find XObject path %s on page %d (missing Resources dict): %s",
            " / ".join(xobject_path),
            page_num,
            e,
        )


def _apply_pattern_target(
    page: Any, page_num: int, target: "StreamTarget", content: bytes, normalize: bool
) -> None:
    """Helper method executing /Pattern stream extraction and structural
    replacement, with an optional nested XObject descent."""
    try:
        pat = page.Resources.Pattern[target.pattern_name]
        final_obj = _descend_xobject_path(pat, target.xobject_path)
        _write_normalized(
            final_obj,
            content,
            normalize,
            f"Pattern {target.pattern_name} on page {page_num}",
        )
    except (AttributeError, KeyError) as e:
        logger.warning(
            "Could not find Pattern %s on page %d (missing Resources/Pattern dict): %s",
            target.pattern_name,
            page_num,
            e,
        )


def _apply_smask_target(
    page: Any, page_num: int, target: "StreamTarget", content: bytes, normalize: bool
) -> None:
    """Helper method executing an ExtGState /SMask /G group stream
    extraction and structural replacement, with an optional nested
    XObject descent."""
    try:
        gs = page.Resources.ExtGState[target.extgstate_name]
        group = gs.SMask.G
        final_obj = _descend_xobject_path(group, target.xobject_path)
        _write_normalized(
            final_obj,
            content,
            normalize,
            f"SMask group for ExtGState {target.extgstate_name} on page {page_num}",
        )
    except (AttributeError, KeyError) as e:
        logger.warning(
            "Could not find SMask group for ExtGState %s on page %d "
            "(missing Resources/ExtGState/SMask/G): %s",
            target.extgstate_name,
            page_num,
            e,
        )


def _apply_annotation_target(
    page: Any, page_num: int, target: "StreamTarget", content: bytes, normalize: bool
) -> None:
    """Helper method executing an annotation /AP appearance stream
    extraction and structural replacement, with an optional nested
    XObject descent.

    Note: annotation targeting is index-based (`Annot <n>` counts position
    in the page's /Annots array at dump time). If annotations are added,
    removed, or reordered between dump and import, the index may silently
    resolve to the wrong annotation. Prefer re-dumping immediately before
    re-importing when annotation content is being edited.
    """
    try:
        annot = page.Annots[target.annot_index]
        ap_entry = annot.AP[target.ap_key]
        stream_obj = ap_entry[target.ap_state] if target.ap_state is not None else ap_entry
        final_obj = _descend_xobject_path(stream_obj, target.xobject_path)
        _write_normalized(
            final_obj,
            content,
            normalize,
            f"annotation appearance stream (Annot {target.annot_index + 1}, "
            f"AP {target.ap_key}) on page {page_num}",
        )
    except (AttributeError, KeyError, IndexError) as e:
        logger.warning(
            "Could not find annotation appearance stream (Annot %d, AP %s) on page %d: %s",
            target.annot_index + 1,
            target.ap_key,
            page_num,
            e,
        )


def _apply_stream_target(
    pdf: "pikepdf.Pdf", target: StreamTarget, content: bytes, normalize: bool
) -> None:
    """Overwrite the target PDF object with the newly provided byte content.

    Note: if the page's /Contents is currently an array of streams, this
    operation collapses them into a single stream.  That matches the output
    of `dump_streams`, which concatenates array sub-streams before exporting.
    """
    try:
        page = pdf.pages[target.page_num - 1]
    except IndexError:
        # Explanatory comment: User requested applying a stream to a page that
        # does not exist in the current document. We safely ignore it.
        logger.warning("Page %d does not exist in the PDF, skipping", target.page_num)
        return

    if target.target_type == "Contents":
        _apply_contents_target(pdf, page, target.page_num, content, normalize)
    elif target.target_type == "XObject":
        _apply_xobject_target(page, target.page_num, target.xobject_path, content, normalize)
    elif target.target_type == "Pattern":
        _apply_pattern_target(page, target.page_num, target, content, normalize)
    elif target.target_type == "SMask":
        _apply_smask_target(page, target.page_num, target, content, normalize)
    elif target.target_type == "Annotation":
        _apply_annotation_target(page, target.page_num, target, content, normalize)


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
* `=== Page 1 / Pattern /P1` overwrites the stream of the tiling Pattern
  mapped as `/P1` within Page 1's resources.
* `=== Page 1 / ExtGState /GS1 / SMask` overwrites the soft-mask group
  stream referenced by `/GS1`'s `/SMask /G` entry.
* `=== Page 1 / Annot 2 / AP /N` overwrites the direct-stream `/N`
  appearance stream of the 2nd annotation (1-based) in Page 1's /Annots.
* `=== Page 1 / Annot 2 / AP /N / State /On` overwrites the `/On` state
  stream when `/N` is itself a sub-dictionary keyed by appearance state.

Any of the above can be followed by one or more `/ XObject <name>` segments
to target a Form XObject nested inside that Pattern's, SMask group's, or
annotation appearance stream's own resources -- e.g.
`Page 1 / Pattern /P1 / XObject /Fm1`.

**Annotation targeting is index-based and order-sensitive.** The `Annot <n>`
index reflects the annotation's position in `/Annots` at dump time. Adding,
removing, or reordering annotations between `dump_streams` and
`import_streams` will cause the index to silently target the wrong
annotation. Re-dump immediately before re-importing when editing
annotation appearance streams to avoid this.

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
        "test_setup": {"copy_assets": {"streams.txt": "patched_streams.txt"}},
    },
    {
        "cmd": "in.pdf import_streams normalize=false streams.txt output out.pdf",
        "desc": "Inject stream text verbatim, without parsing or stripping comments",
        "test_setup": {"copy_assets": {"streams.txt": "streams.txt"}},
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
