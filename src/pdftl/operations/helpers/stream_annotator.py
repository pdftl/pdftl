# src/pdftl/operations/helpers/stream_annotator.py

"""Stream annotation & formatting helpers for dump_streams"""

from pdftl.operations.data.pdf_operators import PDF_OPERATORS
from pdftl.operations.data.pdf_operator_args import PDF_OPERATOR_ARGS
from pdftl.utils.scope_tracker import ScopeTracker


def _interpret_operands(op: str, tokens: list[str]) -> str:
    """Return a human-readable operand string for the operator, or empty string."""
    try:
        arg_fn = PDF_OPERATOR_ARGS.get(op)
        if arg_fn:
            return arg_fn(tokens) or ""
    except (ValueError, IndexError, KeyError, TypeError):
        pass
    return ""


def _resolve_tf_extras(tokens: list[str], resources, font_cache: dict) -> list[str]:
    """
    Resolve the BaseFont name for a Tf operator, caching by font resource
    name within the current annotate_stream() call. Tf is typically called
    repeatedly for the same font within a stream, so this avoids re-walking
    the pikepdf resource dictionary (resources.Font.get(...) + attribute
    access) for every occurrence.
    """
    try:
        if len(tokens) < 3 or not resources or "/Font" not in resources:
            return []
        font_name = tokens[-3]
        if font_name in font_cache:
            return font_cache[font_name]
        result: list[str] = []
        font_obj = resources.Font.get(font_name)
        if font_obj is not None and "/BaseFont" in font_obj:
            result = [f"-> {font_obj.BaseFont}"]
        font_cache[font_name] = result
        return result
    except (AttributeError, KeyError, TypeError, ValueError):
        return []


def _resolve_do_extras(tokens: list[str], resources) -> list[str]:
    try:
        if len(tokens) >= 2 and resources and "/XObject" in resources:
            xobj = resources.XObject.get(tokens[-2])
            if xobj is not None and "/Subtype" in xobj:
                return [f"Subtype: {xobj.Subtype}"]
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
    return []


def _resolve_gs_extras(tokens: list[str], resources) -> list[str]:
    try:
        if len(tokens) >= 2 and resources and "/ExtGState" in resources:
            gs_obj = resources.ExtGState.get(tokens[-2])
            if gs_obj is not None:
                return [
                    *([f"fill-alpha: {gs_obj.ca}"] if "/ca" in gs_obj else []),
                    *([f"stroke-alpha: {gs_obj.CA}"] if "/CA" in gs_obj else []),
                    *([f"blend: {gs_obj.BM}"] if "/BM" in gs_obj else []),
                ]
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
    return []


def _resolve_resource_extras(op: str, tokens: list[str], resources, font_cache: dict) -> list[str]:
    if op == "Tf":
        return _resolve_tf_extras(tokens, resources, font_cache)
    if op == "Do":
        return _resolve_do_extras(tokens, resources)
    if op == "gs":
        return _resolve_gs_extras(tokens, resources)
    return []


def _build_comment(
    op: str,
    tokens: list[str],
    resources,
    tracker: ScopeTracker,
    font_cache: dict,
) -> str:
    """
    Build the full annotation comment string for one operator line.
    Returns the comment including leading '% ' and indentation.
    """
    kind, content_type, desc = PDF_OPERATORS[op]
    extra_parts: list[str] = []

    # Scope tracking — must happen before reading current_depth for indent.
    is_close = op in ("Q", "ET", "EMC", "EX")
    depth_before = tracker.current_depth
    scope_label = tracker.push(op)
    depth_after = tracker.current_depth

    if scope_label:
        extra_parts.append(scope_label)

    # Operand interpretation.
    operand_str = _interpret_operands(op, tokens)
    if operand_str:
        # For Tf, resource lookup extends the operand string rather than adding a new part.
        if op == "Tf":
            res_extras = _resolve_resource_extras(op, tokens, resources, font_cache)
            suffix = f" {' '.join(res_extras)}" if res_extras else ""
            extra_parts.append(operand_str + suffix)
        else:
            extra_parts.append(operand_str)
    else:
        extra_parts.extend(_resolve_resource_extras(op, tokens, resources, font_cache))

    # Indent: open operators sit at outer depth, close operators at post-pop depth,
    # interior operators at their current depth.
    if is_close:
        indent_depth = depth_after
    elif scope_label:  # open operator
        indent_depth = depth_before
    else:
        indent_depth = depth_after

    indent = "  " * indent_depth
    extra = f" [{', '.join(extra_parts)}]" if extra_parts else ""
    return f"% {indent}{kind}/{content_type}: {desc}{extra}"


def annotate_stream(
    content: bytes,
    resources=None,
    min_comment_col: int = 40,
    max_comment_col: int = 80,
) -> bytes:
    """
    Return a copy of a normalized content stream with a PDF comment appended
    to each line explaining the operator, its operands, and its scope role.

    Scope pairs (q/Q, BT/ET, BMC/BDC/EMC, BX/EX) are assigned a shared ID
    so open and close ends can be correlated, e.g. [gs#1 open] / [gs#1 close].
    The comment body is indented to reflect the current nesting depth.
    """
    decoded = [line.decode("latin-1") for line in content.splitlines()]
    tracker = ScopeTracker()
    font_cache: dict = {}

    # Single pass: tokenize each line once, build its comment (if any), and
    # track the alignment column as we go. Output is buffered unformatted
    # here and padded in a second, cheap pass below — this avoids a second
    # full strip/split/PDF_OPERATORS lookup over every line, which the old
    # two-pass _comment_alignment_col() + main-loop structure duplicated.
    comment_col = min_comment_col
    buffered: list[tuple[str, str | None]] = []

    for line in decoded:
        stripped = line.strip()
        if not stripped or stripped.startswith("%"):
            buffered.append((line, None))
            continue

        tokens = stripped.split()
        op = tokens[-1]

        if op not in PDF_OPERATORS:
            buffered.append((line, None))
            continue

        comment = _build_comment(op, tokens, resources, tracker, font_cache)
        comment_col = max(comment_col, min(len(line) + 2, max_comment_col))
        buffered.append((line, comment))

    out_lines = []
    for line, comment in buffered:
        if comment is None:
            out_lines.append(line.encode("latin-1"))
        elif len(line) + 2 <= comment_col:
            out_lines.append(f"{line:<{comment_col}}{comment}".encode("latin-1"))
        else:
            out_lines.append(f"{line}  {comment}".encode("latin-1"))

    return b"\n".join(out_lines)
