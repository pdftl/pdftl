# src/pdftl/operations/helpers/stream_annotator.py

"""Stream annotation & formatting helpers for dump_streams"""

from pdftl.operations.data.pdf_operators import PDF_OPERATORS
from pdftl.operations.data.pdf_operator_args import PDF_OPERATOR_ARGS
from pdftl.utils.scope_tracker import ScopeTracker


def _comment_alignment_col(decoded: list[str], min_col: int, max_col: int) -> int:
    """First pass: find the column at which to align annotation comments."""
    col = min_col
    for line in decoded:
        stripped = line.strip()
        if not stripped or stripped.startswith("%"):
            continue
        tokens = stripped.split()
        if PDF_OPERATORS.get(tokens[-1]):
            col = max(col, min(len(line) + 2, max_col))
    return col


def _interpret_operands(op: str, tokens: list[str]) -> str:
    """Return a human-readable operand string for the operator, or empty string."""
    try:
        arg_fn = PDF_OPERATOR_ARGS.get(op)
        if arg_fn:
            return arg_fn(tokens) or ""
    except (ValueError, IndexError, KeyError, TypeError):
        pass
    return ""


def _resolve_tf_extras(tokens: list[str], resources) -> list[str]:
    try:
        if len(tokens) >= 3 and resources and "/Font" in resources:
            font_obj = resources.Font.get(tokens[-3])
            if font_obj is not None and "/BaseFont" in font_obj:
                return [f"-> {font_obj.BaseFont}"]
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
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


_RESOURCE_RESOLVERS = {
    "Tf": _resolve_tf_extras,
    "Do": _resolve_do_extras,
    "gs": _resolve_gs_extras,
}


def _resolve_resource_extras(op: str, tokens: list[str], resources) -> list[str]:
    resolver = _RESOURCE_RESOLVERS.get(op)
    return resolver(tokens, resources) if resolver else []


def _build_comment(
    op: str,
    tokens: list[str],
    resources,
    tracker: ScopeTracker,
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
            res_extras = _resolve_resource_extras(op, tokens, resources)
            suffix = f" {' '.join(res_extras)}" if res_extras else ""
            extra_parts.append(operand_str + suffix)
        else:
            extra_parts.append(operand_str)
    else:
        extra_parts.extend(_resolve_resource_extras(op, tokens, resources))

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
    comment_col = _comment_alignment_col(decoded, min_comment_col, max_comment_col)
    tracker = ScopeTracker()
    out_lines = []

    for line in decoded:
        stripped = line.strip()
        if not stripped or stripped.startswith("%"):
            out_lines.append(line.encode("latin-1"))
            continue

        tokens = stripped.split()
        op = tokens[-1]

        if op not in PDF_OPERATORS:
            out_lines.append(line.encode("latin-1"))
            continue

        comment = _build_comment(op, tokens, resources, tracker)

        if len(line) + 2 <= comment_col:
            annotated = f"{line:<{comment_col}}{comment}"
        else:
            annotated = f"{line}  {comment}"
        out_lines.append(annotated.encode("latin-1"))

    return b"\n".join(out_lines)
