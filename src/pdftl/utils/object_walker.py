# pdftl/utils/object_walker.py

# Lazily-cached pikepdf classes. Importing pikepdf inside every hot-path
# helper (as this module previously did) costs a sys.modules lookup plus
# attribute-chain traversal on every one of the millions of calls made
# during attribution. Resolve the classes once and hand back a tuple.
_PIKEPDF_TYPES: tuple | None = None


def pikepdf_types():
    global _PIKEPDF_TYPES
    if _PIKEPDF_TYPES is None:
        import pikepdf

        _PIKEPDF_TYPES = (pikepdf.Stream, pikepdf.Dictionary, pikepdf.Array, pikepdf.PdfError)
    return _PIKEPDF_TYPES


def stream_or_dict(obj):
    Stream, Dictionary, _, _ = pikepdf_types()
    if isinstance(obj, Stream):
        return obj.stream_dict
    if isinstance(obj, Dictionary):
        return obj
    return None


def _push_children(obj, stack, Array, PdfError):
    d = stream_or_dict(obj)

    if d is not None:
        for key in d.keys():
            try:
                stack.append(d[key])
            except (KeyError, AttributeError, PdfError):
                continue
    elif isinstance(obj, Array):
        stack.extend(obj)


def walk_objects(pdf, visit):
    """Visit every reachable object once. `visit(obj, objgen)` is called
    for each distinct indirect object (objgen != (0,0)); direct objects
    are still walked into but not deduplicated/visited themselves."""
    _, _, Array, PdfError = pikepdf_types()
    seen = set()
    stack = [pdf.trailer]
    while stack:
        obj = stack.pop()
        try:
            objgen = obj.objgen
        except AttributeError:
            objgen = None
        is_indirect = objgen is not None and objgen != (0, 0)
        if is_indirect:
            if objgen in seen:
                continue
            seen.add(objgen)
            visit(obj, objgen)
        _push_children(obj, stack, Array, PdfError)
