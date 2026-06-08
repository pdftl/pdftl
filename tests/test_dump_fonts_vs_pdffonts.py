import subprocess

import pikepdf
import pytest

# Adjust this import based on your actual project structure
from pdftl.operations.dump_fonts import dump_fonts


def get_pdffonts_data(pdf_path: str) -> set[tuple]:
    """
    Runs pdffonts via CLI, parses its fixed-width tabular output,
    and returns a set of logical font tuples for comparison.
    """
    result = subprocess.run(["pdffonts", pdf_path], capture_output=True, text=True, check=True)
    lines = result.stdout.splitlines()

    if len(lines) < 2:
        return set()

    # Dynamically find column start indices based on the header string
    header = lines[0]
    idx_type = header.find("type")
    idx_enc = header.find("encoding")
    idx_emb = header.find("emb")
    idx_sub = header.find("sub")
    idx_uni = header.find("uni")
    idx_obj = header.find("object ID")

    pdffonts_parsed = set()

    # Skip header (line 0) and separator (line 1)
    for line in lines[2:]:
        if not line.strip():
            continue

        # Extract all 7 columns using the header boundaries
        name = line[0:idx_type].strip()
        subtype = line[idx_type:idx_enc].strip()
        encoding = line[idx_enc:idx_emb].strip()
        is_embedded = line[idx_emb:idx_sub].strip() == "yes"
        is_subset = line[idx_sub:idx_uni].strip() == "yes"
        has_unicode = line[idx_uni:idx_obj].strip() == "yes"

        # The object ID column contains "ID  GEN", we just want the ID
        obj_str = line[idx_obj:].strip().split()[0]
        obj_id = int(obj_str) if obj_str.isdigit() else None

        pdffonts_parsed.add((obj_id, name, subtype, encoding, is_embedded, is_subset, has_unicode))

    return pdffonts_parsed


def get_pdftl_data(pdf_path: str) -> set[tuple]:
    """
    Runs our native pdftl dump_fonts operation and maps the output
    to the exact same tuple structure as our pdffonts parser.
    """
    with pikepdf.Pdf.open(pdf_path) as pdf:
        # Execute the operation (passing None for specs to hit all pages)
        result = dump_fonts(pdf, specs=None)

    assert result.success is True, "pdftl dump_fonts operation failed"

    pdftl_parsed = set()
    for font in result.data["fonts"]:
        pdftl_parsed.add(
            (
                font["obj_id"],
                font.get("name", ""),  # Fallback to empty string if missing
                font.get("subtype", ""),
                font.get("encoding", ""),
                font.get("is_embedded", False),
                font.get("is_subset", False),
                font.get("has_to_unicode", False),
            )
        )

    return pdftl_parsed


# --- The Actual Test ---
@pytest.mark.slow
@pytest.mark.parametrize(
    "pdf_filename",
    [
        "cs229_main_notes.pdf",
        "1.pdf",
        "lorem.pdf",
        "iso-5pages.pdf",
        "ClarityOCGs.pdf",
        "pdf20-utf8-test.pdf",
        "russian.pdf",
        "truetype.pdf",
        "type3.pdf",
        "searchAcrossLinesDoubleColumn.pdf",
    ],
)
def test_dump_fonts_parity_with_poppler(pdf_filename, get_pdf_path):
    """
    Asserts that pdftl extracts the exact same font objects with the
    exact same metadata flags as the industry-standard pdffonts tool.
    """
    # Adjust this path logic to point to your test assets directory
    pdf_path = get_pdf_path(pdf_filename)

    pdffonts_set = get_pdffonts_data(pdf_path)
    pdftl_set = get_pdftl_data(pdf_path)

    # By using sets, if there is a mismatch, pytest will print a beautiful
    # line-by-line diff showing exactly which font tuples differ.
    assert pdftl_set == pdffonts_set, "pdftl output does not match pdffonts!"

    # Double check the exact total count just to be absolutely certain
    # no duplicates accidentally masked each other in the set mapping
    assert len(pdftl_set) == len(pdffonts_set)
