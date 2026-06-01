# tests/operations/test_add_text.py

import importlib
import sys
import unittest
from unittest.mock import MagicMock, patch

import pytest
from pikepdf import Array, Name, Pdf, Rectangle

# --- Local Imports ---
# We import the module to reload it during cleanup
from pdftl.operations.add_text import add_text_pdf
from pdftl.operations.parsers.add_text_parser import parse_add_text_specs_to_rules
from pdftl.utils.text_templates import build_static_context

from .sandbox import ModuleSandboxMixin


class TestAddTextLogic(unittest.TestCase):
    """
    Unit tests for pure logic helpers.
    """

    def setUp(self, num_pages=10):
        self.mock_pdf = MagicMock(spec=Pdf)
        self.mock_pdf.filename = "my-file.pdf"
        self.mock_pdf.docinfo = {Name.Title: "Title", Name.Author: "Author"}
        self.mock_pdf.pages.__len__ = MagicMock(return_value=num_pages)

    def test_build_static_context(self):
        context = build_static_context(self.mock_pdf)
        self.assertEqual(context["total"], 10)
        self.assertEqual(context["filename"], "my-file.pdf")
        self.assertEqual(context["metadata"]["Title"], "Title")

    def test_build_static_context_missing_info(self):
        self.mock_pdf.filename = None
        self.mock_pdf.docinfo = {}
        context = build_static_context(self.mock_pdf)
        self.assertEqual(context["filename"], "")
        self.assertEqual(context["metadata"], {})

    def test_get_page_origin(self):
        """Covers lines 366-367: extracting X, Y from trimbox."""
        from pdftl.operations.add_text import _get_page_origin

        mock_page = MagicMock()
        mock_page.trimbox = [15.5, 20.0, 500, 800]

        ox, oy = _get_page_origin(mock_page)
        self.assertEqual(ox, 15.5)
        self.assertEqual(oy, 20.0)

    def test_translate_rect(self):
        """Covers line 371: translating rectangle coordinates."""
        from pdftl.operations.add_text import _translate_rect

        rect = [10, 10, 50, 50]
        translated = _translate_rect(rect, 5.0, -5.0)
        self.assertEqual(translated, [15.0, 5.0, 55.0, 45.0])

    def test_rotate_rect(self):
        """Covers lines 379-402: mapping visual space to physical space."""
        from pdftl.operations.add_text import _rotate_rect

        mock_page = MagicMock()
        mock_page.trimbox = [0, 0, 500, 800]

        rect = [10, 20, 30, 40]

        # Line 381: Test 0 rotation
        mock_page.get.return_value = 0
        self.assertEqual(_rotate_rect(rect, mock_page), [10, 20, 30, 40])

        # Line 392: Test 90 CW
        mock_page.get.return_value = 90
        self.assertEqual(_rotate_rect(rect, mock_page), [460.0, 10.0, 480.0, 30.0])

        # Line 395: Test 180
        mock_page.get.return_value = 180
        self.assertEqual(_rotate_rect(rect, mock_page), [470.0, 760.0, 490.0, 780.0])

        # Line 400: Test 270 CW
        mock_page.get.return_value = 270
        self.assertEqual(_rotate_rect(rect, mock_page), [20.0, 770.0, 40.0, 790.0])

        # Line 402: Test non-standard rotation fallback (e.g., 45 degrees)
        mock_page.get.return_value = 45
        self.assertEqual(_rotate_rect(rect, mock_page), [10, 20, 30, 40])

    def test_copy_annotations(self):
        """Covers lines 413-423: transferring annotations from overlay to main page."""
        from pikepdf import Array, Dictionary, Name, Pdf

        from pdftl.operations.add_text import _copy_annotations

        # Use real Pdf objects instead of MagicMocks to safely test C++ bindings
        pdf = Pdf.new()
        target_page = pdf.add_blank_page(page_size=(500, 800))

        overlay_pdf = Pdf.new()
        overlay_page = overlay_pdf.add_blank_page(page_size=(500, 800))

        # Create a dummy annotation dictionary on the overlay
        annot = Dictionary(Type=Name.Annot, Subtype=Name.Text, Rect=Array([10, 10, 50, 50]))

        # CRITICAL FIX: Make the annotation an indirect object so copy_foreign can process it
        indirect_annot = overlay_pdf.make_indirect(annot)
        overlay_page.Annots = Array([indirect_annot])

        # Execute
        _copy_annotations(target_page, overlay_page, pdf)

        # Verify lines 413-414: Annots array was successfully created
        self.assertIn(Name.Annots, target_page)
        self.assertEqual(len(target_page.Annots), 1)

        # Verify coordinates were transformed
        self.assertEqual(list(target_page.Annots[0].Rect), [10.0, 10.0, 50.0, 50.0])

        # Verify line 411: Early return if no overlay annots exist
        overlay_page_empty = overlay_pdf.add_blank_page()
        _copy_annotations(
            target_page, overlay_page_empty, pdf
        )  # Should return cleanly without error

    def test_parse_error_raises_invalid_argument_error(self):
        # Line 259-260: ValueError from parser is wrapped in InvalidArgumentError
        from pdftl.exceptions import InvalidArgumentError

        pdf = Pdf.new()
        pdf.add_blank_page()
        with self.assertRaises(InvalidArgumentError):
            add_text_pdf(pdf, ["BADINPUT_NO_DELIMITER"])

    def test_empty_page_rules_returns_early(self):
        # Line 263: if page_rules is empty, return immediately without drawing
        from unittest.mock import patch

        pdf = Pdf.new()
        pdf.add_blank_page()
        # Parser returns empty dict -> early return, no TextDrawer instantiated
        with patch(
            "pdftl.operations.parsers.add_text_parser.parse_add_text_specs_to_rules",
            return_value={},
        ):
            result = add_text_pdf(pdf, ["1/text/"])
        assert result.success is True


class TestAddTextOrchestration(ModuleSandboxMixin, unittest.TestCase):
    """
    Happy-path orchestration tests.

    Since the import of TextDrawer happens INSIDE `add_text_pdf`,
    we execute a patch on the source definition.
    """

    def setUp(self):
        super().setUp()
        self.mock_parser = MagicMock()
        self.patcher_drawer = patch("pdftl.operations.helpers.text_drawer.TextDrawer")
        self.mock_TextDrawer_cls = self.patcher_drawer.start()
        self.mock_drawer_instance = self.mock_TextDrawer_cls.return_value
        self.parser_patcher = patch(
            "pdftl.operations.parsers.add_text_parser.parse_add_text_specs_to_rules",
            self.mock_parser,
        )
        self.parser_patcher.start()

        self.pdf = Pdf.new()
        self.mock_rule = {"text": lambda c: [("Test", None)], "font": "Arial", "size": 10}

    def test_add_text_pdf_orchestration(self):
        """Standard happy path."""
        self.pdf.add_blank_page(page_size=(500, 800))
        self.mock_parser.return_value = {0: [self.mock_rule]}

        # This calls add_text_pdf, which runs:
        # "from pdftl.operations.helpers.text_drawer import TextDrawer"
        # Since we patched that source path in setUp, it imports our Mock.
        result = add_text_pdf(self.pdf, ["spec"]).pdf

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

    def test_page_with_no_rules_is_skipped(self):
        # Line 283: _process_page returns early when no rules match the page
        # Add two pages but only give rules for page 0; page 1 should be skipped
        self.pdf.add_blank_page(page_size=(500, 800))
        self.pdf.add_blank_page(page_size=(500, 800))
        self.mock_parser.return_value = {0: [self.mock_rule]}  # only page 0

        add_text_pdf(self.pdf, ["spec"])

        # TextDrawer instantiated once for dep check + once for page 0 only
        self.assertEqual(self.mock_TextDrawer_cls.call_count, 2)

    def test_rotation_90_visual_dimensions_swapped(self):
        # Lines 297-298: for 90/270 rotation, visual w/h are swapped
        from pikepdf import Name

        self.pdf.add_blank_page(page_size=(500, 800))
        self.pdf.pages[0].obj[Name.Rotate] = 90
        self.mock_parser.return_value = {0: [self.mock_rule]}

        add_text_pdf(self.pdf, ["spec"])

        # With 90-degree rotation: visual width=800, visual height=500
        page_kwargs = self.mock_TextDrawer_cls.call_args_list[1][1]
        self.assertEqual(page_kwargs["page_box"].width, 800)
        self.assertEqual(page_kwargs["page_box"].height, 500)


class TestAddTextMissingDependency(unittest.TestCase):
    """
    Isolated tests for when reportlab is missing.
    """

    def setUp(self):
        self.pdf = Pdf.new()
        self.pdf.add_blank_page(page_size=(100, 100))

        # Mock parser so we get far enough to hit the drawer
        self.patch_parser = patch(
            "pdftl.operations.parsers.add_text_parser.parse_add_text_specs_to_rules"
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
        if "pdftl.operations.helpers.text_drawer" in sys.modules:
            del sys.modules["pdftl.operations.helpers.text_drawer"]

        # 2. Reload the orchestrator so it forgets the poisoned class
        if "pdftl.operations.add_text" in sys.modules:
            importlib.reload(sys.modules["pdftl.operations.add_text"])

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
            if "pdftl.operations.helpers.text_drawer" in sys.modules:
                del sys.modules["pdftl.operations.helpers.text_drawer"]

            # 4. Reload orchestrator to force it to import the new (dummy) drawer
            if "pdftl.operations.add_text" in sys.modules:
                # It exists? Force it to refresh (so it hits the poison)
                module_obj = sys.modules["pdftl.operations.add_text"]
                importlib.reload(module_obj)
            else:
                # It was wiped? Just import it (it will hit the poison naturally)
                pass

            # 5. Run Command
            from pdftl.exceptions import InvalidArgumentError as CurrentError

            with pytest.raises(CurrentError):
                add_text_pdf(self.pdf, ["dummy"])


def test_add_text_sequence_counter_stamping():
    """
    Verify that the {n} counter increments per matched page within a spec
    and resets back to 1 for subsequent specs.
    """
    # Simulate a 6-page PDF document
    total_pages = 6

    # Spec 1 targets pages 1-3 (indices 0, 1, 2) -> expected n = 1, 2, 3
    # Spec 2 targets pages 2-4 (indices 1, 2, 3) -> expected n = 1, 2, 3
    specs = ["1-3/x{n}/(position=top-left)", "2-4/y{n}/(position=top-left)"]

    page_rules = parse_add_text_specs_to_rules(specs, total_pages)

    # --- Page 1 (Index 0) ---
    # Should only have the first spec's rule (n=1)
    rules_p1 = page_rules[0]
    assert len(rules_p1) == 1
    assert getattr(rules_p1[0], "n", None) == 1 or rules_p1[0].get("n") == 1

    # --- Page 2 (Index 1) ---
    # Should have Spec 1 (n=2) and Spec 2 (n=1)
    rules_p2 = page_rules[1]
    assert len(rules_p2) == 2

    n_values_p2 = [getattr(r, "n", None) if hasattr(r, "n") else r.get("n") for r in rules_p2]
    assert n_values_p2 == [2, 1]

    # --- Page 3 (Index 2) ---
    # Should have Spec 1 (n=3) and Spec 2 (n=2)
    rules_p3 = page_rules[2]
    assert len(rules_p3) == 2

    n_values_p3 = [getattr(r, "n", None) if hasattr(r, "n") else r.get("n") for r in rules_p3]
    assert n_values_p3 == [3, 2]

    # --- Page 4 (Index 3) ---
    # Should only have the second spec's rule (n=3)
    rules_p4 = page_rules[3]
    assert len(rules_p4) == 1
    assert getattr(rules_p4[0], "n", None) == 3 or rules_p4[0].get("n") == 3
