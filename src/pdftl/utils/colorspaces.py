# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/colorspaces.py

"""Utility functions for resolving PDF color spaces into standardized dictionaries."""

import logging

logger = logging.getLogger(__name__)

from pdftl.utils.icc_profiles import extract_icc_profile_name


def family_from_device_op(op: str) -> str:
    """Return the color family implied by a device-space shorthand operator."""
    if op in ("g", "G"):
        return "gray"
    if op in ("rg", "RG"):
        return "rgb"
    if op in ("k", "K"):
        return "cmyk"
    return "unknown"


def resolve_colorspace(cs_obj, resources, pikepdf) -> dict:
    """Recursively resolve a pikepdf color space object to a normalized dict."""
    if cs_obj is None:
        return {"family": "unknown", "raw": "None"}

    if isinstance(cs_obj, pikepdf.Name):
        return resolve_name(str(cs_obj))

    if isinstance(cs_obj, pikepdf.Array):
        return resolve_array(cs_obj, resources, pikepdf)

    return resolve_name(str(cs_obj))


def resolve_name(name: str) -> dict:
    """Resolve a simple color space name."""
    core_families = {
        "/DeviceGray": "gray",
        "/DeviceRGB": "rgb",
        "/DeviceCMYK": "cmyk",
        "/Pattern": "pattern",
    }
    if name in core_families:
        return {"family": core_families[name]}
    return {"family": "unknown", "raw": name}


def resolve_array(cs_array, resources, pikepdf) -> dict:
    """Resolve an array-based color space."""
    if not cs_array:
        return {"family": "unknown", "raw": "[]"}

    tag = str(cs_array[0])

    if tag == "/ICCBased":
        return resolve_icc(cs_array, pikepdf)
    if tag == "/Separation":
        return resolve_separation(cs_array, resources, pikepdf)
    if tag == "/DeviceN":
        return resolve_device_n(cs_array, resources, pikepdf)
    if tag == "/Indexed":
        return resolve_indexed(cs_array, resources, pikepdf)
    if tag == "/CalGray":
        return resolve_calgray(cs_array, pikepdf)
    if tag == "/CalRGB":
        return resolve_calrgb(cs_array, pikepdf)
    if tag == "/Lab":
        return resolve_lab(cs_array, pikepdf)
    if tag == "/Pattern":
        if len(cs_array) > 1:
            base = resolve_colorspace(cs_array[1], resources, pikepdf)
            return {"family": "pattern", "base": base}
        return {"family": "pattern"}

    return {"family": "unknown", "raw": tag}


def resolve_icc(cs_array, pikepdf) -> dict:
    result: dict = {"family": "icc"}
    try:
        stream = cs_array[1]
        n = int(stream.get("/N", 0))
        result["components"] = n
        result["icc_family"] = {1: "gray", 3: "rgb", 4: "cmyk"}.get(n, "unknown")

        alt = stream.get("/Alternate")
        if alt is not None:
            result["alternate"] = str(alt).lstrip("/").lower()

        try:
            raw = stream.read_raw_bytes()
            profile_name = extract_icc_profile_name(raw)
            if profile_name:
                result["profile_name"] = profile_name
        except (pikepdf.PdfError, AttributeError, ValueError, TypeError) as err:
            logger.debug("ICC profile name extraction failed: %s", err)
    except (IndexError, AttributeError) as err:
        logger.debug("ICC colorspace parse failed: %s", err)
    return result


def resolve_separation(cs_array, resources, pikepdf) -> dict:
    """Resolve a Separation (spot color) space."""
    result: dict = {"family": "spot"}
    try:
        colorant = str(cs_array[1]).lstrip("/")
        result["colorants"] = [colorant]
        if len(cs_array) > 2:
            alt = resolve_colorspace(cs_array[2], resources, pikepdf)
            result["alternate_family"] = alt.get("icc_family", alt.get("family", "unknown"))
    except (IndexError, AttributeError) as err:
        logger.debug("Separation colorspace parse failed: %s", err)
    return result


def resolve_device_n(cs_array, resources, pikepdf) -> dict:
    """Resolve a DeviceN color space."""
    result: dict = {"family": "spot"}
    try:
        names_array = cs_array[1]
        result["colorants"] = [str(n).lstrip("/") for n in names_array]
        if len(cs_array) > 2:
            alt = resolve_colorspace(cs_array[2], resources, pikepdf)
            result["alternate_family"] = alt.get("icc_family", alt.get("family", "unknown"))
    except (IndexError, AttributeError, TypeError) as err:
        logger.debug("DeviceN colorspace parse failed: %s", err)
    return result


def resolve_indexed(cs_array, resources, pikepdf) -> dict:
    """Resolve an Indexed color space."""
    result: dict = {"family": "indexed"}
    try:
        base = resolve_colorspace(cs_array[1], resources, pikepdf)
        result["base_family"] = base.get("icc_family", base.get("family", "unknown"))
        result["hival"] = int(cs_array[2])
    except (IndexError, AttributeError, TypeError) as err:
        logger.debug("Indexed colorspace parse failed: %s", err)
    return result


def resolve_calgray(cs_array, pikepdf) -> dict:
    """Resolve a CalGray color space."""
    result: dict = {"family": "calgray"}
    try:
        dictionary = cs_array[1]
        white_point = dictionary.get("/WhitePoint")
        if white_point is not None:
            result["white_point"] = [float(v) for v in white_point]
        black_point = dictionary.get("/BlackPoint")
        if black_point is not None:
            result["black_point"] = [float(v) for v in black_point]
        gamma = dictionary.get("/Gamma")
        if gamma is not None:
            result["gamma"] = float(gamma)
    except (IndexError, AttributeError, TypeError, ValueError) as err:
        logger.debug("CalGray colorspace parse failed: %s", err)
    return result


def resolve_calrgb(cs_array, pikepdf) -> dict:
    """Resolve a CalRGB color space."""
    result: dict = {"family": "calrgb"}
    try:
        dictionary = cs_array[1]
        white_point = dictionary.get("/WhitePoint")
        if white_point is not None:
            result["white_point"] = [float(v) for v in white_point]
        black_point = dictionary.get("/BlackPoint")
        if black_point is not None:
            result["black_point"] = [float(v) for v in black_point]
        gamma = dictionary.get("/Gamma")
        if gamma is not None:
            result["gamma"] = [float(v) for v in gamma]
        matrix = dictionary.get("/Matrix")
        if matrix is not None:
            result["matrix"] = [float(v) for v in matrix]
    except (IndexError, AttributeError, TypeError, ValueError) as err:
        logger.debug("CalRGB colorspace parse failed: %s", err)
    return result


def resolve_lab(cs_array, pikepdf) -> dict:
    """Resolve a Lab color space."""
    result: dict = {"family": "lab"}
    try:
        dictionary = cs_array[1]
        white_point = dictionary.get("/WhitePoint")
        if white_point is not None:
            result["white_point"] = [float(v) for v in white_point]
        black_point = dictionary.get("/BlackPoint")
        if black_point is not None:
            result["black_point"] = [float(v) for v in black_point]
        range_val = dictionary.get("/Range")
        if range_val is not None:
            result["range"] = [float(v) for v in range_val]
    except (IndexError, AttributeError, TypeError, ValueError) as err:
        logger.debug("Lab colorspace parse failed: %s", err)
    return result


def resolve_named_cs(name_str: str, resources, pikepdf) -> dict:
    """Look up a color space name in the page's /Resources /ColorSpace dict."""
    if resources is not None:
        try:
            cs_dict_obj = resources.get("/ColorSpace")
            if cs_dict_obj is not None:
                key = name_str if name_str.startswith("/") else f"/{name_str}"
                mapped_obj = cs_dict_obj.get(key)
                if mapped_obj is not None:
                    return resolve_colorspace(mapped_obj, resources, pikepdf)
        except (AttributeError, TypeError) as err:
            logger.debug("Named colorspace lookup failed: %s", err)
    return resolve_name(name_str if name_str.startswith("/") else f"/{name_str}")


def image_colorspace(xobj, resources, pikepdf) -> dict:
    """Extract color space from an Image XObject."""
    cs = xobj.get("/ColorSpace")
    if cs is None:
        return {"family": "unknown", "raw": "Image without ColorSpace"}
    return resolve_colorspace(cs, resources, pikepdf)


def resolve_shading_cs(shading_name: str, resources, pikepdf) -> dict:
    """Look up a shading's color space configuration."""
    try:
        shadings = resources.get("/Shading")
        if shadings is None:
            return {"family": "pattern"}
        shading_obj = shadings.get(shading_name)
        if shading_obj is None:
            return {"family": "pattern"}
        cs_obj = shading_obj.get("/ColorSpace")
        base = resolve_colorspace(cs_obj, resources, pikepdf)
        return {"family": "pattern", "base": base}
    except (AttributeError, TypeError) as err:
        logger.debug("Shading colorspace lookup failed: %s", err)
    return {"family": "pattern"}


def effective_family(cs_dict: dict) -> str:
    """Return a single summary family name for aggregation."""
    fam = cs_dict.get("family", "unknown")
    if fam == "icc":
        return cs_dict.get("icc_family", "icc")
    return fam
