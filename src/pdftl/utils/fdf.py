# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/fdf.py

"""Utilities to help with FDF files"""

import io
import logging
import re

logger = logging.getLogger(__name__)


def extract_main_fdf_dict(data):
    """Extract the literal main FDF dictionary from FDF data"""

    # FIXME make finding the FDF dictionary more robust. Regex?
    # Find the first << ... >> that wraps the main FDF dictionary
    # Use simple bracket counting
    start = data.find(b"<<")
    if start == -1:
        raise ValueError("No << found in FDF")
    depth = 0
    for i in range(start, len(data)):
        if data[i : i + 2] == b"<<":
            depth += 1
            i += 1
        elif data[i : i + 2] == b">>":
            depth -= 1
            i += 1
            if depth == 0:
                end = i + 1
                break
    else:
        raise ValueError("Could not find matching >> for main FDF dict")
    fdf_dict_bytes = data[start:end]
    start = fdf_dict_bytes.find(b"/FDF") + 4
    fdf_dict_bytes = fdf_dict_bytes[start:-2]
    return fdf_dict_bytes


def add_fdf_to_catalog(pdf_bytes, fdf_obj_num):
    """Update Catalog to reference the new FDF object"""
    catalog_match = re.search(rb"(\d+) 0 obj\s*<<.*?/Type\s*/Catalog.*?>>", pdf_bytes, re.DOTALL)
    if not catalog_match:
        raise ValueError("Could not find catalog object in PDF")
    catalog_bytes = catalog_match.group(0)
    if b"/FDF" not in catalog_bytes:
        catalog_bytes = re.sub(b" *>>$", b" /FDF %d 0 R >>" % fdf_obj_num, catalog_bytes)
        pdf_bytes = (
            pdf_bytes[: catalog_match.start()] + catalog_bytes + pdf_bytes[catalog_match.end() :]
        )
    return pdf_bytes


def wrap_fdf_data_in_pdf_bytes(data):
    """Return a file-like object which can be loaded by
    pikepdf, such that the resulting pikepdf PDF object
    'pdf' will have FDF data dictionary (taken from the FDF
    file at 'fdf_path') at 'pdf.Root.FDF'.

    """
    import pikepdf

    fdf_bytes = extract_main_fdf_dict(data)
    logger.debug("Extracted %s bytes of main FDF dictionary", len(fdf_bytes))

    # Create a skeleton PDF with a blank page so pikepdf will load it
    pdf = pikepdf.new()
    pdf.add_blank_page()

    pdf_buffer = io.BytesIO()
    pdf.save(pdf_buffer)
    pdf.close()

    pdf_bytes = pdf_buffer.getvalue()

    # Find the position of the xref table
    xref_pos = pdf_bytes.rfind(b"\nxref")
    if xref_pos == -1:
        raise ValueError("xref not found in skeleton PDF")

    # Determine next free object number
    obj_nums = [int(n) for n in re.findall(rb"(\d+) 0 obj", pdf_bytes)]
    next_obj_number = max(obj_nums) + 1
    logger.debug("Using object number %s for FDF dictionary", next_obj_number)

    # Construct FDF object bytes
    fdf_obj_bytes = b"\n%d 0 obj\n%s\nendobj\n" % (next_obj_number, fdf_bytes)

    # Inject FDF object before xref and add to document root catalog
    new_pdf_bytes = pdf_bytes[:xref_pos] + fdf_obj_bytes + pdf_bytes[xref_pos:]
    new_pdf_bytes = add_fdf_to_catalog(new_pdf_bytes, next_obj_number)

    return io.BytesIO(new_pdf_bytes)
