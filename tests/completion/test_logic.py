def test_parser_serialization():
    from pdftl.cli.complete import rebuild_cache

    # Force a build
    parser = rebuild_cache()
    assert parser is not None

    # Verify we can actually use it
    from lark.exceptions import UnexpectedCharacters

    try:
        parser.parse("help \x01")
    except UnexpectedCharacters as e:
        assert "HELP_SUB_KW" in e.allowed


def test_resolve_candidates():
    from pdftl.cli.complete import rebuild_cache, resolve_candidates

    parser = rebuild_cache()

    # Simulate Lark saying HELP_SUB_KW is allowed
    candidates = resolve_candidates({"HELP_SUB_KW", "HELP_FLAG"}, parser)

    assert "sign" in candidates
    assert "examples" in candidates
    assert "--help" in candidates
    assert "__PDF_FILE__" not in candidates  # Should only be there if PDF_PATH is allowed


def test_file_triggers():
    from pdftl.cli.complete import rebuild_cache, resolve_candidates

    parser = rebuild_cache()

    # Simulate start of a command (expects input PDF)
    candidates = resolve_candidates({"PDF_PATH", "HELP_KW"}, parser)

    assert "__PDF_FILE__" in candidates
    assert "help" in candidates


def test_args_flag_offered_at_start():
    """--args should be a candidate at the very beginning of a command."""
    from pdftl.cli.complete import rebuild_cache, resolve_candidates

    parser = rebuild_cache()

    try:
        parser.parse("\x01")
    except Exception as e:
        allowed = getattr(e, "allowed", getattr(e, "expected", set()))
        candidates = resolve_candidates(allowed, parser)
        assert "--args" in candidates


def test_file_path_offered_after_args_flag():
    """After '--args ', only __FILE__ should be offered (no keywords, no __PDF_FILE__)."""
    from pdftl.cli.complete import rebuild_cache, resolve_candidates
    from lark.exceptions import UnexpectedCharacters, UnexpectedToken, UnexpectedEOF

    parser = rebuild_cache()

    try:
        parser.parse("--args \x01")
        assert False, "expected a parse error"
    except (UnexpectedCharacters, UnexpectedToken, UnexpectedEOF) as e:
        allowed = getattr(e, "allowed", getattr(e, "expected", set()))
        candidates = resolve_candidates(allowed, parser)
        assert candidates == {"__FILE__"}


def test_args_flag_not_offered_mid_input_section():
    from pdftl.cli.complete import rebuild_cache, resolve_candidates

    parser = rebuild_cache()

    try:
        parser.parse("t.pdf \x01")
    except Exception as e:
        allowed = getattr(e, "allowed", getattr(e, "expected", set()))
        candidates = resolve_candidates(allowed, parser)
        assert "__PDF_FILE__" in candidates
        assert "--args" not in candidates  # global_flag unreachable before any option
