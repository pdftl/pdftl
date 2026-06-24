# src/pdftl/operations/helpers/pretty_printers.py


def _format_stream_ref(obj, spaces: str) -> str:
    """Format a single Stream reference as a one-line string."""
    info = "Stream"
    if obj.objgen:
        info += f" ({obj.objgen[0]}:{obj.objgen[1]})"
    subtype = obj.get("/Subtype")
    if subtype:
        info += f" Subtype: {subtype}"
    return f"{spaces}[{info}]"


def _format_array(obj, indent: int) -> list[str]:
    """Format a pikepdf Array, collapsing flat arrays to one line."""
    import pikepdf

    spaces = " " * indent
    has_complex = any(
        isinstance(item, (pikepdf.Dictionary, pikepdf.Array, pikepdf.Stream)) for item in obj
    )
    if not has_complex:
        return [f"{spaces}[{', '.join(str(item) for item in obj)}]"]

    lines = []
    for i, item in enumerate(obj):
        if isinstance(item, pikepdf.Stream):
            lines.append(f"{spaces}- {_format_stream_ref(item, '').strip()}")
        elif isinstance(item, (pikepdf.Dictionary, pikepdf.Array)):
            lines.append(f"{spaces}- [Item {i}]:")
            lines.extend(pretty_format_pdf_obj(item, indent + 4))
        else:
            lines.append(f"{spaces}- {item}")
    return lines


def _format_dict_value(k_str: str, v, indent: int, spaces: str) -> list[str]:
    """Format one key-value pair from a pikepdf Dictionary."""
    import pikepdf

    if isinstance(v, pikepdf.Stream):
        return [f"{spaces}{k_str}: {_format_stream_ref(v, '').strip()}"]
    if isinstance(v, pikepdf.Dictionary):
        return [f"{spaces}{k_str}:"] + pretty_format_pdf_obj(v, indent + 4)
    if isinstance(v, pikepdf.Array):
        has_complex = any(
            isinstance(item, (pikepdf.Dictionary, pikepdf.Array, pikepdf.Stream)) for item in v
        )
        if not has_complex:
            return [f"{spaces}{k_str}: [{', '.join(str(item) for item in v)}]"]
        return [f"{spaces}{k_str}:"] + pretty_format_pdf_obj(v, indent + 4)
    return [f"{spaces}{k_str}: {v}"]


def pretty_format_pdf_obj(obj, indent: int = 0) -> list[str]:
    """
    Recursively format a pikepdf object for clear visual output.
    Streams are shown as one-line references; flat arrays are collapsed.
    """
    import pikepdf

    spaces = " " * indent

    if isinstance(obj, pikepdf.Stream):
        return [_format_stream_ref(obj, spaces)]

    if isinstance(obj, pikepdf.Dictionary):
        lines = []
        for k, v in obj.items():
            lines.extend(_format_dict_value(str(k), v, indent, spaces))
        return lines

    if isinstance(obj, pikepdf.Array):
        return _format_array(obj, indent)

    return [f"{spaces}{obj}"]
