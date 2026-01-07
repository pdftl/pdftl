# src/pdftl/catalog/parse_catalog.py


def parse_catalog_dump(lines):
    """
    Parses 'dump_catalog' output into a structured dictionary.

    Example input:
    CatalogPageMode: UseOutlines
    CatalogViewerPreferencesCenterWindow: True
    """
    data = {}
    viewer_prefs = {}
    mark_info = {}

    for line in lines:
        line = line.strip()
        if not line or ":" not in line or line == "CatalogBegin":
            continue

        key, value = [p.strip() for p in line.split(":", 1)]
        if not key.startswith("Catalog"):
            continue

        clean_key = key[len("Catalog") :]

        # Simple type conversion
        if value.lower() in ("true", "yes"):
            value = True
        elif value.lower() in ("false", "no"):
            value = False

        # Dispatch based on prefix
        if clean_key.startswith("ViewerPreferences"):
            viewer_prefs[clean_key[len("ViewerPreferences") :]] = value
        elif clean_key.startswith("MarkInfo"):
            mark_info[clean_key[len("MarkInfo") :]] = value
        else:
            data[clean_key] = value

    if viewer_prefs:
        data["ViewerPreferences"] = viewer_prefs
    if mark_info:
        data["MarkInfo"] = mark_info

    return data
