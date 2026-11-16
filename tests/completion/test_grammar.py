# tests/completion/test_grammar.py

from unittest.mock import patch

from pdftl.cli.complete import GRAMMAR_VERSION as COMPLETE_PY_GRAMMAR_VERSION
from pdftl.completion.grammar import GRAMMAR_VERSION, GrammarBuilder


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


def test_args_flag_is_reachable_from_global_flag():
    """
    Regression test: args_flag was previously defined but never referenced
    by global_flag, making it dead grammar. Ensure the built grammar string
    actually wires it in.
    """
    builder = GrammarBuilder()
    grammar = builder.build()

    # Find the global_flag rule line and confirm args_flag is one of its alternatives
    global_flag_line = next(
        line for line in grammar.splitlines() if line.strip().startswith("global_flag:")
    )
    assert "args_flag" in global_flag_line


def test_args_flag_parses_successfully():
    """End-to-end: the built grammar should actually accept '--args <file>' as input."""
    from lark import Lark

    builder = GrammarBuilder()
    grammar_str = builder.build()
    parser = Lark(grammar_str, parser="earley")

    # Should not raise
    parser.parse("--args foo.yml t.pdf")


def test_handle_rules_allow_multi_letter_handles():
    """pdftk handles are one or more capitals (AB=x.pdf, cat AB1)."""
    grammar = GrammarBuilder().build()

    handle_line = next(
        line for line in grammar.splitlines() if line.strip().startswith("handle_prefix:")
    )
    assert "[A-Z]+=" in handle_line

    cat_line = next(line for line in grammar.splitlines() if line.strip().startswith("cat_spec:"))
    assert "[A-Z]+" in cat_line


def test_multi_letter_handle_input_parses():
    from lark import Lark

    Lark(GrammarBuilder().build(), parser="earley").parse("AB=x.pdf")
