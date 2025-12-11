# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/create_pdf2.py

from pikepdf import Dictionary, Name, Pdf


def create_custom_pdf(filename: str):
    """
    Creates a minimal, two-page A4 PDF for testing.

    Each page includes:
    - A gray box around the page border.
    - A large, black arrow pointing up in the center.
    - The page number in a large font in the top-left corner.
    """
    # Standard A4 size in points
    WIDTH, HEIGHT = 595, 842

    # Define the drawing commands for the shared elements
    # These commands are written in the PDF content stream language.
    border_and_arrow_stream = f"""
    q                    # Save graphics state
    0.8 0.8 0.8 RG       # Set stroke color to gray
    2 w                  # Set line width to 2 points
    5 5 {WIDTH-10} {HEIGHT-10} re  # Define the rectangle for the border
    S                    # Stroke the path (draw the border)
    Q                    # Restore graphics state

    q                    # Save graphics state
    0 0 0 RG             # Set stroke color to black
    2 w                  # Set line width
    {WIDTH/2} {HEIGHT*0.4} m  # Move to the arrow's base
    {WIDTH/2} {HEIGHT*0.6} l  # Draw the main line of the arrow
    S                    # Stroke the path
    {WIDTH/2 - 15} {HEIGHT*0.6 - 15} m # Move to the left arrowhead point
    {WIDTH/2} {HEIGHT*0.6} l          # Draw to the arrow's tip
    {WIDTH/2 + 15} {HEIGHT*0.6 - 15} l # Draw to the right arrowhead point
    S                    # Stroke the path
    Q                    # Restore graphics state
    """.encode()

    # --- Create the PDF ---
    pdf = Pdf.new()

    for i in range(1, 3):
        # Create a new blank page. This method automatically adds the page to the PDF.
        page = pdf.add_blank_page()

        # Define the content stream for the page-specific text
        text_stream = f"""
        BT                   # Begin Text Block
        /F1 48 Tf            # Set Font to F1, size 48
        30 {HEIGHT-70} Td   # Position the text (top-left)
        (Page {i}) Tj         # Show the text
        ET                   # End Text Block
        """.encode()

        # Combine the drawing and text streams
        full_content_stream = border_and_arrow_stream + b"\\n" + text_stream

        # A page needs a /Resources dictionary that defines the font /F1.
        # We create a dictionary of fonts and add it to the page's resources.
        font_dictionary = Dictionary(
            F1=Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
        )

        page.add_resource(font_dictionary, Name.Font)

        # Set the combined content stream as the page's content
        page.Contents = pdf.make_stream(full_content_stream)

        # DO NOT append the page again. The add_blank_page() call
        # has already added it to the document.

    print(f"Saving custom 2-page PDF to: {filename}")
    pdf.save(filename)


if __name__ == "__main__":
    # This allows you to run the script directly to generate the file
    create_custom_pdf("custom_test.pdf")
