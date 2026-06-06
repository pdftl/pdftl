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


def attachment_metadata(attachment) -> dict:
    """Extract all available metadata from a filespec attachment object."""
    meta = {}
    try:
        f_stream = attachment.obj.get("/EF").get("/F")
    except (AttributeError, KeyError):
        return meta

    if f_stream is None:
        return meta

    # Stream-level fields
    params = f_stream.get("/Params")
    if params is not None:
        val = params.get("/Size")
        if val is not None:
            meta["file_size"] = int(val)
        for pdf_key, dict_key in (
            ("/CreationDate", "created"),
            ("/ModDate", "modified"),
        ):
            val = params.get(pdf_key)
            if val is not None:
                meta[dict_key] = str(val)

    val = f_stream.get("/Length")
    if val is not None:
        meta["stored_size"] = int(val)

    val = f_stream.get("/Filter")
    if val is not None:
        meta["compression"] = str(val)

    val = f_stream.get("/Subtype")
    if val is not None:
        meta["mime_type"] = str(val)

    # Filespec-level fields
    val = attachment.description
    if val is not None and str(val):
        meta["description"] = str(val)

    val = attachment.relationship
    if val is not None:
        meta["relationship"] = str(val).strip("/")

    return meta
