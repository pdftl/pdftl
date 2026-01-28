# tests/completion/test_grammar.py

from pdftl.cli.complete import GRAMMAR_VERSION as COMPLETE_PY_GRAMMAR_VERSION
from pdftl.completion.grammar import GRAMMAR_VERSION


def test_completion_cache_version_matches_grammar():
    """
    Ensure that if we update the grammar version, we also updated
    the completion script's cache key to force a rebuild for users.
    """
    # They must be identical strings (e.g. "1" == "1")
    assert GRAMMAR_VERSION == COMPLETE_PY_GRAMMAR_VERSION, (
        f"Version Mismatch! Grammar is v{GRAMMAR_VERSION}, but "
        f"complete.py is v{COMPLETE_PY_GRAMMAR_VERSION}. \n"
        "Did you bump the version in src/pdftl/cli/complete.py?"
    )


from unittest.mock import patch

from pdftl.completion.grammar import GrammarBuilder


def test_grammar_builder_empty_registry():
    """Targets lines 114 and 119: Fallback when registry is empty."""
    builder = GrammarBuilder()

    # We patch the actual registry object's dictionaries to be empty
    with patch("pdftl.completion.grammar.registry") as mock_registry:
        mock_registry.operations = {}
        mock_registry.options = {}

        # We also need to prevent initialize_registry from re-populating them
        with patch("pdftl.completion.grammar.initialize_registry"):
            grammar = builder.build()

            # Check for the 'noop' fallback strings
            assert '!operation: "noop"' in grammar
            assert '!option: "noop"' in grammar
