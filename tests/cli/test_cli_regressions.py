def test_help_examples(runner):
    result = runner.run("pdftl", ["help", "examples"])
    assert "Examples" in result.stdout
