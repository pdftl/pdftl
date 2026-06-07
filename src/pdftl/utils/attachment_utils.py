def attachment_stored_size(attachment) -> int | None:
    try:
        return int(attachment.obj.get("/EF").get("/F").get("/Length"))
    except (AttributeError, TypeError, ValueError, KeyError):
        return None


def attachment_file_size(attachment) -> int | None:
    import pikepdf

    try:
        return attachment.get_file().size
    except (AttributeError, TypeError, ValueError, KeyError, pikepdf.PdfError):
        return None


def attachment_compression(attachment) -> str | None:
    try:
        filter_val = attachment.obj.get("/EF").get("/F").get("/Filter")
        if filter_val is None:
            return None
        return str(filter_val)
    except (AttributeError, TypeError):
        return None


def _extract_stream_metadata(f_stream, meta: dict) -> None:
    """Extract metadata available specifically on the embedded file stream dictionary."""
    params = f_stream.get("/Params")
    if params is not None:
        if (val := params.get("/Size")) is not None:
            meta["file_size"] = int(val)
        for pdf_key, dict_key in (
            ("/CreationDate", "created"),
            ("/ModDate", "modified"),
        ):
            if (val := params.get(pdf_key)) is not None:
                meta[dict_key] = str(val)

    if (val := f_stream.get("/Length")) is not None:
        meta["stored_size"] = int(val)

    if (val := f_stream.get("/Filter")) is not None:
        meta["compression"] = str(val)

    if (val := f_stream.get("/Subtype")) is not None:
        meta["mime_type"] = str(val)


def _extract_filespec_metadata(attachment, meta: dict) -> None:
    """Extract higher-level metadata from the filespec definition."""
    if (val := attachment.description) is not None and str(val):
        meta["description"] = str(val)

    if (val := attachment.relationship) is not None:
        meta["relationship"] = str(val).strip("/")


def attachment_metadata(attachment) -> dict:
    """Extract all available metadata from a filespec attachment object."""
    meta = {}
    try:
        f_stream = attachment.obj.get("/EF").get("/F")
    except (AttributeError, KeyError):
        return meta

    if f_stream is not None:
        _extract_stream_metadata(f_stream, meta)

    _extract_filespec_metadata(attachment, meta)

    return meta
