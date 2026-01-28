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
