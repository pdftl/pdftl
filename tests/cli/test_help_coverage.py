import io
import logging
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure the module under test is imported
from pdftl.cli.help import (
    _format_examples_block,
    _print_topic_help,
    find_special_topic_command,
    print_help,
)


class TestHelpLogicEdgeCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Mute the noisy markdown_it debug logs
        logging.getLogger("markdown_it").setLevel(logging.WARNING)

    def test_format_examples_grouping(self):
        """
        Covers line 115: Multiple examples for the same topic.
        """
        examples = [
            {"topic": "foo", "desc": "d1", "cmd": "c1"},
            {"topic": "foo", "desc": "d2", "cmd": "c2"},
            {"topic": "bar", "desc": "d3", "cmd": "c3"},
        ]

        output = _format_examples_block(examples, show_topics=True)

        # FIX: The logic uses "Example" for the first one, not "Example 1"
        self.assertIn("Example for '`foo`'", output)  # First example
        self.assertIn("Example 2 for '`foo`'", output)  # Second example increments
        self.assertIn("Example for '`bar`'", output)  # Resets for new topic

    def test_print_topic_help_caller_source(self):
        """Covers line 166: Printing the 'Source: ...' line."""
        mock_hprint = MagicMock()
        topic_data = {"desc": "Test desc", "caller": "my_plugin_module"}
        _print_topic_help(mock_hprint, topic_data, "test_topic")
        mock_hprint.assert_any_call("\n*Source: my_plugin_module*")

    def test_find_special_topic_none(self):
        """Covers line 372: Early exit when topic is None."""
        self.assertIsNone(find_special_topic_command(None))


class TestHelpRichRendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.getLogger("markdown_it").setLevel(logging.WARNING)

    def test_render_to_file_triggers_rich_logic(self):
        """
        Covers 313-316 (File console) and 260-300 (Rich objects).
        """
        buffer = io.StringIO()
        # This will now pass if you applied the 'from rich.console import Console' fix
        print_help(command=None, dest=buffer, raw=False)

        output = buffer.getvalue()
        self.assertIn("PDF tackle", output)
        # Check for Rich box-drawing characters (indicating LeftJustifiedHeading worked)
        # doesn't work on windows: Rich degrades. So restrict test to linux only.
        if "linux" in sys.platform:
            self.assertTrue(any(c in output for c in ["┏", "━", "┃"]))

    @patch("pdftl.cli.help.get_console")
    def test_rich_object_internals(self, mock_get_console):
        """Covers 310, 297, 293-294."""
        mock_console_instance = MagicMock()
        mock_get_console.return_value = mock_console_instance

        print_help(command=None, dest=None, raw=False)

        # FIX: inspect call_args_list[0] (the first call), not call_args (the last call)
        # print_help calls print() multiple times. The header is in the first call.
        first_call_args = mock_console_instance.print.call_args_list[0]
        renderable = first_call_args[0][0]  # args[0]

        self.assertEqual(type(renderable).__name__, "HelpMarkdown")
        self.assertIn("# **pdftl**", str(renderable))

    def test_raw_mode_bypass(self):
        buffer = io.StringIO()
        print_help(command=None, dest=buffer, raw=True)
        self.assertNotIn("┏", buffer.getvalue())
