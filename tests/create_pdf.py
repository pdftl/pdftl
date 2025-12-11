# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/create_pdf.py

from pikepdf import Dictionary, Name, Pdf

COLOURS = ["1 0 0", "0 .5 0", "0 0 .5", "1 0 1"]


def add_builtin_font_to_page(page, fontname):
    page.Resources.Font = Dictionary(
        F1=Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
    )


def create_custom_pdf(filename: str, pages=2):
    """
    Creates a minimal, two-page A4 PDF for testing.

    Each page includes:
    - A gray box around the page border.
    - A large arrow pointing up in the center.
    - The page number in a large font in the top-left corner.
    """
    # Standard A4 size in points
    WIDTH, HEIGHT = 595, 842

    def drawing(i):
        # Define the drawing commands for the shared elements
        # These commands are written in the PDF content stream language.
        border_and_arrow_stream = f"""q
        0.3 0.3 0.3 RG
        20 w
        5 5 {WIDTH-10} {HEIGHT-10} re
        S
        Q
        q
        {COLOURS[i % len(COLOURS)]} RG
        20 w
        {WIDTH/2} {HEIGHT*0.4} m
        {WIDTH/2} {HEIGHT*0.6} l
        S
        {WIDTH/2 - 50} {HEIGHT*0.6 - 50} m
        {WIDTH/2} {HEIGHT*0.6} l
        {WIDTH/2 + 50} {HEIGHT*0.6 - 50} l
        S
        Q
        """.encode()
        return border_and_arrow_stream

    # --- Create the PDF ---
    pdf = Pdf.new()

    for i in range(1, pages + 1):
        # Create a new blank page. This method automatically adds the page to the PDF.
        page = pdf.add_blank_page(page_size=(WIDTH, HEIGHT))

        # Define the content stream for the page-specific text
        text_stream = f"""BT
        /F1 96 Tf
        30 {HEIGHT-110} Td
        (Page {i}) Tj
        ET
        """.encode()

        # Combine the drawing and text streams
        full_content_stream = drawing(i - 1) + b"\n" + text_stream

        # CORRECT METHOD:
        # Access the page's Resources dictionary and add the Font dictionary to it.
        # This preserves the required '/F1' name for the font.

        add_builtin_font_to_page(page, Name.Helvetica)

        # Set the combined content stream as the page's content
        page.Contents = pdf.make_stream(full_content_stream)

    print(f"Saving custom {pages}-page PDF to: {filename}")
    pdf.save(filename)


if __name__ == "__main__":
    # This allows you to run the script directly to generate the file
    create_custom_pdf("custom_test.pdf")
