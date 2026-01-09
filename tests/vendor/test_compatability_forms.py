import pypdfium2 as pdfium
import pytest

import pdftl

# Skip this test if reportlab isn't installed (dev dependency)
reportlab = pytest.importorskip("reportlab")
from reportlab.lib.colors import black
from reportlab.pdfgen import canvas


@pytest.fixture
def form_pdf_with_resources(tmp_path):
    """
    Uses ReportLab to generate a robust, valid PDF with a text form field.
    This automatically handles AcroForm dictionaries, Page Annots, and Resources.
    """
    pdf_path = tmp_path / "form_simple.pdf"

    c = canvas.Canvas(str(pdf_path))

    # 1. Add base content so the page isn't empty
    c.setFont("Helvetica", 12)
    c.drawString(100, 700, "Base Content (ReportLab)")

    # 2. Add a Text Field (Widget)
    c.acroForm.textfield(
        name="comment_field",
        tooltip="Enter comment here",
        x=50,
        y=550,
        width=200,
        height=50,
        fontSize=12,
        textColor=black,
        borderStyle="inset",
        forceBorder=True,
    )

    c.save()
    return pdf_path


def test_fill_form_complex_characters(form_pdf_with_resources, tmp_path):
    """
    Verifies that pdftl can fill form fields with complex UTF-8 characters
    and flatten the result using a robust ReportLab-generated input.
    """
    # 1. Define tricky UTF-8 data
    tricky_text = "Hello Jürgen & Ñuñez"

    # 2. Create FDF Data File
    # Manually construct FDF to ensure UTF-16BE encoding of the value
    fdf_path = tmp_path / "data.fdf"
    fdf_content = (
        b"%FDF-1.2\n"
        b"1 0 obj\n"
        b"<< /FDF << /Fields [ << /T (comment_field) /V (\xfe\xff"
        + tricky_text.encode("utf-16-be")
        + b") >> ] >> >>\n"
        b"endobj\n"
        b"trailer << /Root 1 0 R >>\n"
        b"%%EOF"
    )
    fdf_path.write_bytes(fdf_content)

    output_pdf = tmp_path / "filled_flattened.pdf"

    # 3. Execution
    (
        pdftl.pipeline(str(form_pdf_with_resources))
        .fill_form(operation_args=[str(fdf_path)])
        .save(str(output_pdf), flatten=True)
    )

    # 4. Verification
    assert output_pdf.exists()

    # Use pypdfium2 to extract text (same logic as `pdftl dump_text`).
    # This automatically handles XObjects, CMap encoding, and flattening.
    pdf = pdfium.PdfDocument(str(output_pdf))
    try:
        page = pdf[0]
        text_page = page.get_textpage()
        extracted_text = text_page.get_text_range()

        print(f"DEBUG Extracted Text:\n{extracted_text}")

        # Verify the characters exist in the rendered output
        assert "Jürgen" in extracted_text
        assert "Ñuñez" in extracted_text

    finally:
        pdf.close()
