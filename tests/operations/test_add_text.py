# tests/operations/test_add_text.py

import importlib
import io
import logging
import sys
import unittest
from unittest.mock import MagicMock, PropertyMock, patch

import pikepdf
import pytest
from pikepdf import Array, Name, Pdf, Rectangle

import pdftl.core.constants as c
from pdftl.exceptions import InvalidArgumentError

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

        self.created_instances = []

        def text_drawer_factory(*args, **kwargs):
            instance = MagicMock()

            def mock_save_valid_pdf(*args, **kwargs):
                buffer = io.BytesIO()
                with pikepdf.Pdf.new() as empty_pdf:
                    empty_pdf.add_blank_page(page_size=(100, 100))
                    empty_pdf.save(buffer)
                return buffer.getvalue()

            instance.save.side_effect = mock_save_valid_pdf

            if "page_box" in kwargs:
                instance.page_box = kwargs["page_box"]

            self.created_instances.append(instance)
            return instance

        self.mock_TextDrawer_cls.side_effect = text_drawer_factory

        self.parser_patcher = patch(
            "pdftl.operations.parsers.add_text_parser.parse_add_text_specs_to_rules",
            self.mock_parser,
        )
        self.parser_patcher.start()

        self.pdf = Pdf.new()
        self.mock_rule = {"text": lambda c: [("Test", None)], "font": "Arial", "size": 10}

    def test_page_with_no_rules_is_skipped(self):
        # Line 283: _process_page returns early when no rules match the page
        # Add two pages but only give rules for page 0; page 1 should be skipped
        self.pdf.add_blank_page(page_size=(500, 800))
        self.pdf.add_blank_page(page_size=(500, 800))
        self.mock_parser.return_value = {0: [self.mock_rule]}  # only page 0

        add_text_pdf(self.pdf, ["spec"])

        # TextDrawer instantiated once for dep check + once for page 0 only
        self.assertEqual(self.mock_TextDrawer_cls.call_count, 2)

    def test_add_text_pdf_orchestration(self):
        self.pdf.add_blank_page(page_size=(500, 800))
        self.mock_parser.return_value = {0: [self.mock_rule]}

        result = add_text_pdf(self.pdf, ["spec"]).pdf

        self.assertIs(result, self.pdf)
        self.assertEqual(self.mock_TextDrawer_cls.call_count, 2)

        init_kwargs = self.mock_TextDrawer_cls.call_args_list[0][1]
        self.assertIsInstance(init_kwargs["page_box"], Rectangle)

        shared_drawer = self.created_instances[1]
        shared_drawer.reset_page_box.assert_called_once()
        reset_box = shared_drawer.reset_page_box.call_args[0][0]
        self.assertEqual(reset_box.width, 500)
        self.assertEqual(reset_box.height, 800)

        shared_drawer.draw_rule.assert_called()
        shared_drawer.save.assert_called()

    def test_add_text_pdf_with_array_mediabox(self):
        self.pdf.add_blank_page()
        self.pdf.pages[0].obj[Name.MediaBox] = Array([0, 0, 612, 792])
        self.mock_parser.return_value = {0: [self.mock_rule]}

        add_text_pdf(self.pdf, ["spec"])

        self.assertEqual(self.mock_TextDrawer_cls.call_count, 2)
        shared_drawer = self.created_instances[1]
        reset_box = shared_drawer.reset_page_box.call_args[0][0]
        self.assertIsInstance(reset_box, Rectangle)
        self.assertEqual(reset_box.width, 612)

    def test_add_text_pdf_with_array_trimbox(self):
        self.pdf.add_blank_page(page_size=(1000, 1000))
        self.pdf.pages[0].obj[Name.TrimBox] = Array([10, 10, 510, 510])
        self.mock_parser.return_value = {0: [self.mock_rule]}

        add_text_pdf(self.pdf, ["spec"])

        self.assertEqual(self.mock_TextDrawer_cls.call_count, 2)
        shared_drawer = self.created_instances[1]
        reset_box = shared_drawer.reset_page_box.call_args[0][0]
        self.assertEqual(reset_box.width, 500)

    def test_rotation_90_visual_dimensions_swapped(self):
        from pikepdf import Name

        self.pdf.add_blank_page(page_size=(500, 800))
        self.pdf.pages[0].obj[Name.Rotate] = 90
        self.mock_parser.return_value = {0: [self.mock_rule]}

        add_text_pdf(self.pdf, ["spec"])

        shared_drawer = self.created_instances[1]
        reset_box = shared_drawer.reset_page_box.call_args[0][0]
        self.assertEqual(reset_box.width, 800)
        self.assertEqual(reset_box.height, 500)


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


# --- merged from test_add_text_coverage.py ---


@pytest.fixture
def pdf():
    p = pikepdf.new()
    p.add_blank_page()  # Page 1
    p.add_blank_page()  # Page 2
    return p


class TestAddTextCoverage(ModuleSandboxMixin):
    def test_add_text_parser_error(self, pdf):
        """Test wrapping of parser ValueError."""
        with patch(
            "pdftl.operations.parsers.add_text_parser.parse_add_text_specs_to_rules"
        ) as mock_parse:
            mock_parse.side_effect = ValueError("Bad syntax")

            with pytest.raises(InvalidArgumentError, match="Error in add_text spec"):
                add_text_pdf(pdf, ["bad-spec"])

    def test_add_text_skip_page(self, pdf):
        """Test that pages with no rules are skipped."""
        # 1. Add pages so the loop actually executes
        pdf.add_blank_page()
        pdf.add_blank_page()

        # 2. Explicitly import the module to prevent sys.modules cross-talk
        import pdftl.operations.helpers.text_drawer

        spec = "1/Hello/"
        with patch.object(pdftl.operations.helpers.text_drawer, "TextDrawer") as MockDrawer:
            instance = MockDrawer.return_value
            instance.save.return_value = b"%PDF-1.0 dummy stream"

            from pdftl.operations.add_text import add_text_pdf

            add_text_pdf(pdf, [spec])

            # Instantiated once for dependency check, once for Page 1.
            # Should NOT be instantiated for Page 2.
            assert MockDrawer.call_count == 2

    def test_add_text_overlay_exception(self, pdf, caplog):
        """Test handling exception during overlay application."""
        # Ensure we capture WARNING logs
        caplog.set_level(logging.WARNING)

        spec = "1/Hello/"

        with patch("pdftl.operations.helpers.text_drawer.TextDrawer") as MockDrawer:
            instance = MockDrawer.return_value
            instance.save.return_value = b"%PDF-1.0 dummy"

            # Make Pdf.open raise exception immediately to simulate corrupt overlay or IO error
            with patch("pikepdf.Pdf.open") as MockPdfOpen:
                MockPdfOpen.side_effect = pikepdf.PdfError("Corrupt overlay")

                add_text_pdf(pdf, [spec])

        assert "Failed to apply global resource overlay map" in caplog.text


def test_source_meta_passed_to_draw_rule():
    """Source pipeline metadata stashed on a page is surfaced in the draw_rule context."""
    import pikepdf
    from pikepdf import Dictionary

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))

    # Stash pipeline source metadata exactly as add_pages does
    pdf.pages[0][f"/{c.PDFTL_SOURCE_INFO_KEY}"] = Dictionary(
        {
            "/source_filename": "old.pdf",
            "/source_page": 5,
        }
    )

    captured_contexts = []

    def capturing_spec(ctx):
        captured_contexts.append(dict(ctx))
        return [("X", None)]

    mock_rule = {"text": capturing_spec, "font": "Helvetica", "size": 10}

    with patch(
        "pdftl.operations.parsers.add_text_parser.parse_add_text_specs_to_rules",
        return_value={0: [mock_rule]},
    ):
        add_text_pdf(pdf, ["dummy"])

    assert captured_contexts, "draw_rule was never called"
    ctx = captured_contexts[0]
    assert ctx["source_filename"] == "old.pdf"
    assert ctx["source_page"] == 5


def test_process_page_uses_trimbox(tmp_path):
    """Tests that TrimBox is used when available."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    # Set a TrimBox smaller than MediaBox
    pdf.pages[0]["/TrimBox"] = pikepdf.Array([10, 10, 190, 290])
    result = add_text_pdf(pdf, ["/TEST/(position=mid-center)"])
    assert result.success


def test_process_page_handles_empty_overlay(tmp_path):
    """Tests graceful handling when overlay PDF has no pages."""
    from unittest.mock import patch

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))

    # Return bytes that parse as a valid but empty PDF
    empty_pdf_bytes = io.BytesIO()
    with pikepdf.new() as empty:
        empty.save(empty_pdf_bytes)

    with patch(
        "pdftl.operations.helpers.text_drawer.TextDrawer.save",
        return_value=empty_pdf_bytes.getvalue(),
    ):
        result = add_text_pdf(pdf, ["/TEST/(position=mid-center)"])
        assert result.success  # Should not crash


def test_add_text_rotated_page_90():
    """Tests visual dimension swap for rotated pages."""
    import pikepdf

    from pdftl.operations.add_text import add_text_pdf

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    pdf.pages[0]["/Rotate"] = 90
    # This triggers the rotation branch in _process_page
    result = add_text_pdf(pdf, ["/TEST/(position=mid-center)"])
    assert result.success


def test_add_text_rotated_page_270():
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    pdf.pages[0]["/Rotate"] = 270
    result = add_text_pdf(pdf, ["/TEST/(position=mid-center)"])
    assert result.success


def test_add_text_no_rules_returns_early():
    """Line 268: empty spec list produces no rules."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    # A spec that matches no pages
    result = add_text_pdf(pdf, ["99/TEST/(position=mid-center)"])
    assert result.success


def test_add_text_bad_metadata_handled_gracefully():
    """Lines 204-206: corrupted docinfo doesn't crash."""
    from unittest.mock import PropertyMock, patch

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    with patch(
        "pikepdf.Pdf.docinfo", new_callable=PropertyMock, side_effect=AttributeError("no docinfo")
    ):
        result = add_text_pdf(pdf, ["1-end/TEST/(position=mid-center)"])
        assert result.success


# --- ADD_TEXT MOPPING ---


def test_add_text_metadata_failure(caplog):
    """Mops lines 171-173: Metadata read failure handling."""
    mock_pdf = MagicMock()
    # Trigger a TypeError when accessing docinfo
    type(mock_pdf).docinfo = PropertyMock(side_effect=TypeError("Corrupt Info"))
    mock_pdf.filename = "test.pdf"
    mock_pdf.pages = [1, 2, 3]

    with caplog.at_level(logging.WARNING):
        ctx = build_static_context(mock_pdf)
        assert ctx["metadata"] == {}
        assert "Could not read PDF metadata" in caplog.text


def test_add_text_no_rules():
    """Mops line 229: Return early if no rules are parsed."""
    mock_pdf = MagicMock()
    mock_pdf.pages = [1]
    # Passing empty specs or specs that result in no rules
    result = add_text_pdf(mock_pdf, [])
    assert result.success is True
    assert result.pdf == mock_pdf
