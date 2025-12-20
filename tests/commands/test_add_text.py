# src/pdftl/commands/test_add_text.py

import importlib
import sys
import unittest
from unittest.mock import MagicMock, patch

import pytest
from pikepdf import Array, Name, Pdf, Rectangle

# --- Local Imports ---
# We import the module to reload it during cleanup
from pdftl.commands.add_text import _build_static_context, add_text_pdf

# Handle optional exception import
try:
    from pdftl.exceptions import InvalidArgumentError
except ImportError:
    InvalidArgumentError = ValueError


class TestAddTextLogic(unittest.TestCase):
    """
    Unit tests for pure logic helpers.
    """

    def setUp(self):
        self.mock_pdf = MagicMock(spec=Pdf)
        self.mock_pdf.filename = "my-file.pdf"
        self.mock_pdf.docinfo = {Name.Title: "Title", Name.Author: "Author"}

    def test_build_static_context(self):
        context = _build_static_context(self.mock_pdf, 10)
        self.assertEqual(context["total"], 10)
        self.assertEqual(context["filename"], "my-file.pdf")
        self.assertEqual(context["metadata"]["Title"], "Title")

    def test_build_static_context_missing_info(self):
        self.mock_pdf.filename = None
        self.mock_pdf.docinfo = {}
        context = _build_static_context(self.mock_pdf, 5)
        self.assertEqual(context["filename"], "")
        self.assertEqual(context["metadata"], {})


from .sandbox import ModuleSandboxMixin


class TestAddTextOrchestration(ModuleSandboxMixin, unittest.TestCase):
    """
    Happy-path orchestration tests.

    Since the import of TextDrawer happens INSIDE `add_text_pdf`,
    we execute a patch on the source definition.
    """

    def setUp(self):
        super().setUp()
        self.mock_parser = MagicMock()
        self.patcher_drawer = patch("pdftl.commands.helpers.text_drawer.TextDrawer")
        self.mock_TextDrawer_cls = self.patcher_drawer.start()
        self.mock_drawer_instance = self.mock_TextDrawer_cls.return_value
        self.parser_patcher = patch(
            "pdftl.commands.parsers.add_text_parser.parse_add_text_specs_to_rules",
            self.mock_parser,
        )
        self.parser_patcher.start()

        self.pdf = Pdf.new()
        self.mock_rule = {"text": lambda c: "Test", "font": "Arial", "size": 10}

    def test_add_text_pdf_orchestration(self):
        """Standard happy path."""
        self.pdf.add_blank_page(page_size=(500, 800))
        self.mock_parser.return_value = {0: [self.mock_rule]}

        # This calls add_text_pdf, which runs:
        # "from pdftl.commands.helpers.text_drawer import TextDrawer"
        # Since we patched that source path in setUp, it imports our Mock.
        result = add_text_pdf(self.pdf, ["spec"])

        self.assertIs(result, self.pdf)

        # Verify call count:
        # 1. Initial Check (instantiated to check deps)
        # 2. Page 0 Processing
        self.assertEqual(self.mock_TextDrawer_cls.call_count, 2)

        # Verify Dependency Check Arg
        init_kwargs = self.mock_TextDrawer_cls.call_args_list[0][1]
        self.assertIsInstance(init_kwargs["page_box"], Rectangle)

        # Verify Page Processing Arg
        page_kwargs = self.mock_TextDrawer_cls.call_args_list[1][1]
        self.assertEqual(page_kwargs["page_box"].width, 500)
        self.assertEqual(page_kwargs["page_box"].height, 800)

        self.mock_drawer_instance.draw_rule.assert_called()
        self.mock_drawer_instance.save.assert_called()

    def test_add_text_pdf_with_array_mediabox(self):
        """Tests handling of raw Array MediaBox."""
        self.pdf.add_blank_page()
        self.pdf.pages[0].obj[Name.MediaBox] = Array([0, 0, 612, 792])
        self.mock_parser.return_value = {0: [self.mock_rule]}

        add_text_pdf(self.pdf, ["spec"])

        self.assertEqual(self.mock_TextDrawer_cls.call_count, 2)
        page_kwargs = self.mock_TextDrawer_cls.call_args_list[1][1]
        # Should be converted to Rectangle
        self.assertIsInstance(page_kwargs["page_box"], Rectangle)
        self.assertEqual(page_kwargs["page_box"].width, 612)

    def test_add_text_pdf_with_array_trimbox(self):
        """Tests handling of raw Array TrimBox."""
        self.pdf.add_blank_page(page_size=(1000, 1000))
        self.pdf.pages[0].obj[Name.TrimBox] = Array([10, 10, 510, 510])
        self.mock_parser.return_value = {0: [self.mock_rule]}

        add_text_pdf(self.pdf, ["spec"])

        self.assertEqual(self.mock_TextDrawer_cls.call_count, 2)
        page_kwargs = self.mock_TextDrawer_cls.call_args_list[1][1]
        # Should be converted to Rectangle (510 - 10)
        self.assertEqual(page_kwargs["page_box"].width, 500)


class TestAddTextMissingDependency(unittest.TestCase):
    """
    Isolated tests for when reportlab is missing.
    """

    def setUp(self):
        self.pdf = Pdf.new()
        self.pdf.add_blank_page(page_size=(100, 100))

        # Mock parser so we get far enough to hit the drawer
        self.patch_parser = patch(
            "pdftl.commands.parsers.add_text_parser.parse_add_text_specs_to_rules"
        )
        self.mock_parser = self.patch_parser.start()
        self.mock_parser.return_value = {0: ["dummy"]}

    def tearDown(self):
        self.patch_parser.stop()
        self.pdf.close()

        # --- AGGRESSIVE CLEANUP ---
        # We MUST clear the module cache and reload to prevent
        # the "Poison Pill" from leaking into other tests.

        # 1. Remove the poisoned helper module
        if "pdftl.commands.helpers.text_drawer" in sys.modules:
            del sys.modules["pdftl.commands.helpers.text_drawer"]

        # 2. Reload the orchestrator so it forgets the poisoned class
        if "pdftl.commands.add_text" in sys.modules:
            importlib.reload(sys.modules["pdftl.commands.add_text"])

    def test_missing_reportlab_raises_error(self):
        """
        Simulates missing reportlab by poisoning sys.modules.
        """
        # 1. Define Poison Pill (block reportlab completely)
        poison_pill = {"reportlab": None}
        for k in list(sys.modules.keys()):
            if k.startswith("reportlab"):
                poison_pill[k] = None

        # 2. Apply Poison
        with patch.dict(sys.modules, poison_pill):

            # 3. Remove text_drawer from cache so it MUST re-import
            #    (and fail to find reportlab)
            if "pdftl.commands.helpers.text_drawer" in sys.modules:
                del sys.modules["pdftl.commands.helpers.text_drawer"]

            # 4. Reload orchestrator to force it to import the new (dummy) drawer
            if "pdftl.commands.add_text" in sys.modules:
                # It exists? Force it to refresh (so it hits the poison)
                module_obj = sys.modules["pdftl.commands.add_text"]
                importlib.reload(module_obj)
            else:
                # It was wiped? Just import it (it will hit the poison naturally)
                pass

            # 5. Run Command
            from pdftl.exceptions import InvalidArgumentError as CurrentError

            with pytest.raises(CurrentError):
                add_text_pdf(self.pdf, ["dummy"])
