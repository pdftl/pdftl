# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/test_add_text.py

"""
Test suite for the add_text module.

This file tests the orchestration logic of `add_text_pdf`,
mocking the TextDrawer helper class.
"""

import io
import unittest
from unittest.mock import MagicMock, patch

# --- Third-Party Imports ---
from pikepdf import Array, Name, Pdf, Rectangle

# --- Local Application Imports ---
from pdftl.commands.add_text import _build_static_context, add_text_pdf

# Import the new TextDrawer. All reportlab logic is now here.
# We import it to patch it.

# Import custom exception for testing
try:
    from pdftl.exceptions import InvalidArgumentError
except ImportError:
    InvalidArgumentError = ValueError


class TestAddTextLogic(unittest.TestCase):
    """
    Unit tests for the "pure" helper functions in add_text.py.
    These tests are written to match the *actual* implementation.
    """

    def setUp(self):
        self.mock_pdf = MagicMock(spec=Pdf)
        self.mock_pdf.filename = "/path/to/my-file.pdf"
        # Use pikepdf.Name for keys, as pikepdf does
        self.mock_pdf.docinfo = {
            Name.Title: "My Document Title",
            Name.Author: "Test Author",
        }

    def test_build_static_context(self):
        """Tests static context variables are built correctly."""
        context = _build_static_context(self.mock_pdf, 10)  # 10 total pages

        self.assertEqual(context["total"], 10)
        self.assertEqual(context["filename"], "my-file.pdf")
        self.assertEqual(context["filename_base"], "my-file")
        self.assertEqual(context["filepath"], "/path/to/my-file.pdf")

        # Check metadata sub-dict
        self.assertEqual(context["metadata"]["Title"], "My Document Title")
        self.assertEqual(context["metadata"]["Author"], "Test Author")

    def test_build_static_context_missing_info(self):
        """Tests context building with missing filename or docinfo."""
        self.mock_pdf.filename = None  # No filename
        self.mock_pdf.docinfo = {}  # Empty docinfo

        context = _build_static_context(self.mock_pdf, 5)

        self.assertEqual(context["total"], 5)
        self.assertEqual(context["filename"], "")  # Actual behavior
        self.assertEqual(context["filename_base"], "")  # Actual behavior
        self.assertEqual(context["filepath"], None)  # Actual behavior
        self.assertEqual(context["metadata"], {})  # Actual behavior

    def test_build_static_context_docinfo_value_error(self):
        """
        Tests context building when docinfo values fail to stringify.
        The *entire* metadata block should be empty.
        """
        # Simulate a value that raises an error on str()
        mock_bad_string = MagicMock()
        mock_bad_string.__str__.side_effect = ValueError("test error")

        self.mock_pdf.docinfo = {
            Name.Title: "Good Title",
            Name.Creator: mock_bad_string,
        }

        context = _build_static_context(self.mock_pdf, 1)

        # The entire metadata dict should be empty due to the error
        self.assertEqual(context["metadata"], {})


class TestAddTextIntegration(unittest.TestCase):
    """
    Integration tests for the add_text_pdf orchestrator.

    These tests use a REAL pikepdf object and mock the
    dependencies around it (parser and drawer) to catch
    bugs related to the pikepdf API.
    """

    def setUp(self):
        """
        Set up a real Pdf object and mock the "seams".
        """
        # 1. Create a real Pdf object
        self.pdf = Pdf.new()

        # 2. Set up a mock rule to be returned by the parser
        self.mock_rule = {
            "text": lambda ctx: "Test Text",
            "font": "Helvetica",
            "size": 12,
        }

        # 3. Patch the parser
        self.patch_parser = patch(
            "pdftl.commands.add_text.parse_add_text_specs_to_rules"
        )
        self.mock_parser = self.patch_parser.start()
        self.mock_parser.return_value = {}  # Default to no rules

        # 4. Patch the TextDrawer
        self.patch_drawer = patch("pdftl.commands.add_text.TextDrawer")
        self.mock_TextDrawer = self.patch_drawer.start()

        # Configure the mock drawer instance
        self.mock_drawer_instance = self.mock_TextDrawer.return_value
        self.mock_drawer_instance.save.return_value = b"overlay_bytes"

        # 5. Patch Pdf.open to prevent file I/O
        # We must return a real, simple Pdf object
        self.patch_pdf_open = patch("pikepdf.Pdf.open")
        self.mock_pdf_open = self.patch_pdf_open.start()

        # When Pdf.open(BytesIO(...)) is called, return a dummy
        # PDF with one page to be the "overlay"
        self.dummy_overlay_pdf = Pdf.new()
        self.dummy_overlay_pdf.add_blank_page()

        # We need a context manager mock
        self.mock_pdf_open.return_value.__enter__.return_value = self.dummy_overlay_pdf

    def tearDown(self):
        """Stop all patches."""
        self.patch_parser.stop()
        self.patch_drawer.stop()
        self.patch_pdf_open.stop()
        self.pdf.close()
        self.dummy_overlay_pdf.close()

    def test_add_text_pdf_orchestration(self):
        """
        Tests the "happy path" orchestration using a real Pdf object.
        The default add_blank_page() creates a /MediaBox as a Rectangle.
        """
        # Add a page with a Rectangle-based MediaBox
        # Use 'page_size=' with a tuple (width, height)
        self.pdf.add_blank_page(page_size=(500, 800))
        self.mock_parser.return_value = {0: [self.mock_rule]}  # Rule for page 0

        # Run the function
        result_pdf = add_text_pdf(self.pdf, ["spec"])

        # 1. Check it's an in-place operation
        self.assertIs(result_pdf, self.pdf)

        # 2. Check parser was called correctly
        self.mock_parser.assert_called_once_with(["spec"], 1)  # 1 page

        # 3. Check TextDrawer was called twice:
        #    - Once for the dependency check
        #    - Once for the page
        self.assertEqual(self.mock_TextDrawer.call_count, 2)

        # 4. Check the dependency-check call (first call)
        init_call_kwargs = self.mock_TextDrawer.call_args_list[0][1]  # kwargs
        self.assertIsInstance(init_call_kwargs["page_box"], Rectangle)

        # 5. Check the page-processing call (second call)
        page_call_kwargs = self.mock_TextDrawer.call_args_list[1][1]
        page_box = page_call_kwargs["page_box"]

        # Check that it's a Rectangle object
        self.assertIsInstance(page_box, Rectangle)
        self.assertEqual(page_box.width, 500)
        self.assertEqual(page_box.height, 800)

        # 6. Check the drawer instance was used
        self.mock_drawer_instance.draw_rule.assert_called_once_with(
            self.mock_rule, unittest.mock.ANY  # context dict
        )
        self.mock_drawer_instance.save.assert_called_once()
        self.mock_pdf_open.assert_called_once()

        # 7. Check the BytesIO content passed to Pdf.open
        # This confirms the overlay bytes were used
        self.mock_pdf_open.assert_called_with(unittest.mock.ANY)
        call_args = self.mock_pdf_open.call_args[0]
        self.assertIsInstance(call_args[0], io.BytesIO)
        self.assertEqual(call_args[0].getvalue(), b"overlay_bytes")

    def test_add_text_pdf_with_array_mediabox(self):
        """
        Tests the real-world case where /MediaBox is a raw Array
        [0, 0, w, h] instead of a pikepdf.Rectangle.
        This test will FAIL on the buggy code and PASS on the fixed code.
        """
        self.pdf.add_blank_page()
        # Manually set the /MediaBox to a raw Array, simulating the bug
        # Use Name.MediaBox instead of /MediaBox for valid Python syntax
        self.pdf.pages[0].obj[Name.MediaBox] = Array([0, 0, 612, 792])

        self.mock_parser.return_value = {0: [self.mock_rule]}

        # Run the function
        result_pdf = add_text_pdf(self.pdf, ["spec"])

        # 1. Check in-place
        self.assertIs(result_pdf, self.pdf)

        # 2. Check TextDrawer page-processing call (second call)
        self.assertEqual(self.mock_TextDrawer.call_count, 2)
        page_call_kwargs = self.mock_TextDrawer.call_args_list[1][1]
        page_box = page_call_kwargs["page_box"]

        # 3. Check that the Array was correctly converted to a Rectangle
        self.assertIsInstance(page_box, Rectangle)

        # 4. Check the values are correct
        self.assertEqual(page_box.width, 612)
        self.assertEqual(page_box.height, 792)
        self.mock_drawer_instance.draw_rule.assert_called_once()
        self.mock_pdf_open.assert_called_once()

    def test_add_text_pdf_with_array_trimbox(self):
        """
        Tests the box-finding fallback logic.
        Ensures that if /TrimBox is an Array, it is also
        correctly converted to a Rectangle.
        """
        # Use 'page_size=' with a tuple (width, height)
        self.pdf.add_blank_page(page_size=(1000, 1000))
        # Manually set the /TrimBox to a raw Array
        # Use Name.TrimBox instead of /TrimBox for valid Python syntax
        self.pdf.pages[0].obj[Name.TrimBox] = Array([10, 10, 510, 510])

        self.mock_parser.return_value = {0: [self.mock_rule]}

        # Run the function
        result_pdf = add_text_pdf(self.pdf, ["spec"])

        # Check TextDrawer page-processing call
        self.assertEqual(self.mock_TextDrawer.call_count, 2)
        page_call_kwargs = self.mock_TextDrawer.call_args_list[1][1]
        page_box = page_call_kwargs["page_box"]

        # Check it was converted to a Rectangle
        self.assertIsInstance(page_box, Rectangle)

        # Check it used the TrimBox values, not the MediaBox
        self.assertEqual(page_box.width, 500)  # 510 - 10
        self.assertEqual(page_box.height, 500)  # 510 - 10
        self.mock_drawer_instance.draw_rule.assert_called_once()

    def test_add_text_pdf_parser_value_error(self):
        """Tests that a parser ValueError is caught and re-raised."""
        self.mock_parser.side_effect = ValueError("Invalid spec string")

        with self.assertRaises(InvalidArgumentError) as cm:
            add_text_pdf(self.pdf, ["bad-spec"])

        self.assertIn("Error in add_text spec: Invalid spec string", str(cm.exception))

    def test_add_text_pdf_empty_specs(self):
        """
        Tests that running with no specs returns the original pdf.
        The parser *is* called, but the drawer is not.
        """
        # Add a page so total_pages is not 0
        self.pdf.add_blank_page()
        total_pages = len(self.pdf.pages)

        result_pdf = add_text_pdf(self.pdf, [])
        self.assertIs(result_pdf, self.pdf)

        # The parser IS called
        self.mock_parser.assert_called_once_with([], total_pages)

        # The drawer is NOT called
        self.mock_TextDrawer.assert_not_called()

    def test_add_text_pdf_import_error(self):
        """
        Tests that the 'dummy' TextDrawer's error is
        correctly raised to the user.
        """
        # Simulate the 'dummy' class raising the error on init
        error_msg = "pip install pdftl[add_text]"
        self.mock_TextDrawer.side_effect = InvalidArgumentError(error_msg)

        # Add a page so the main loop runs
        self.pdf.add_blank_page()
        self.mock_parser.return_value = {0: [self.mock_rule]}

        with self.assertRaises(InvalidArgumentError) as cm:
            add_text_pdf(self.pdf, ["valid-spec"])

        self.assertIn(error_msg, str(cm.exception))

        # Check that it failed on the *first* call (the init check)
        self.mock_TextDrawer.assert_called_once()
