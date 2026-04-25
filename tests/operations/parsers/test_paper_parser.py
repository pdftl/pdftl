from pdftl.operations.parsers.paper_parser import parse_paper_spec


def test_parse_paper_spec_missing_underscore_fallback():
    # Get baseline dimensions for proper landscape formatting
    landscape_dims = parse_paper_spec("a4_l")
    assert landscape_dims is not None

    # Test the fallback logic (e.g. "a4l" instead of "a4_l")
    fallback_dims = parse_paper_spec("a4l")

    # It should successfully parse and match the landscape dimensions
    assert fallback_dims == landscape_dims

    # Verify it is actually landscape (width > height)
    width, height = fallback_dims
    assert width > height

    # Test another size to ensure the logic works generally
    assert parse_paper_spec("letterl") == parse_paper_spec("letter_l")
