import pdftl.commands.parsers.inject_parser as cp


def test_parse_inject_args_empty():
    """
    Tests that the parser handles an empty list.
    """
    args = []
    heads, tails, remaining = cp.parse_inject_args(args)
    assert heads == []
    assert tails == []
    assert remaining == []


def test_parse_inject_args_simple_head():
    """
    Tests the main example from the docs:
    'head' 'code' (with default "1-end" page spec).
    """
    args = ["head", "2 0 0 2 0 0 cm"]
    heads, tails, remaining = cp.parse_inject_args(args)

    expected_heads = [{"specs": ["1-end"], "code": "2 0 0 2 0 0 cm"}]
    assert heads == expected_heads
    assert tails == []
    assert remaining == []


def test_parse_inject_args_simple_tail():
    """
    Tests a simple 'tail' command with the default page spec.
    """
    args = ["tail", "q Q"]
    heads, tails, remaining = cp.parse_inject_args(args)

    expected_tails = [{"specs": ["1-end"], "code": "q Q"}]
    assert heads == []
    assert tails == expected_tails
    assert remaining == []


def test_parse_inject_args_head_with_one_spec():
    """
    Tests 'head' with a single preceding page spec.
    """
    args = ["1-5", "head", "h_code"]
    heads, tails, remaining = cp.parse_inject_args(args)

    expected_heads = [{"specs": ["1-5"], "code": "h_code"}]
    assert heads == expected_heads
    assert tails == []
    assert remaining == []


def test_parse_inject_args_tail_with_multiple_specs():
    """
    Tests 'tail' with multiple preceding page specs.
    """
    args = ["1-5", "even", "odd", "tail", "t_code"]
    heads, tails, remaining = cp.parse_inject_args(args)

    expected_tails = [{"specs": ["1-5", "even", "odd"], "code": "t_code"}]
    assert heads == []
    assert tails == expected_tails
    assert remaining == []


def test_parse_inject_args_multiple_mixed_commands():
    """
    Tests a complex sequence of multiple commands, checking
    that page specs are correctly consumed and reset.
    """
    args = [
        "1",
        "head",
        "h1",  # Head on page 1
        "2-end",
        "odd",
        "tail",
        "t1",  # Tail on odd pages from 2-end
        "head",
        "h2",  # Head on all pages (default)
    ]
    heads, tails, remaining = cp.parse_inject_args(args)

    expected_heads = [
        {"specs": ["1"], "code": "h1"},
        {"specs": ["1-end"], "code": "h2"},
    ]
    expected_tails = [{"specs": ["2-end", "odd"], "code": "t1"}]

    assert heads == expected_heads
    assert tails == expected_tails
    assert remaining == []


def test_parse_inject_args_specs_left_over():
    """
    Tests that any page specs not followed by 'head' or 'tail'
    are returned in the 'remaining_specs' list.
    """
    args = ["1-5", "even"]
    heads, tails, remaining = cp.parse_inject_args(args)

    assert heads == []
    assert tails == []
    assert remaining == ["1-5", "even"]


def test_parse_inject_args_incomplete_command():
    """
    Tests that a 'head' command without a following code
    block just gets returned as a remaining spec.
    """
    args = ["1-5", "head"]
    heads, tails, remaining = cp.parse_inject_args(args)

    assert heads == []
    assert tails == []
    # The parser consumed '1-5' into 'specs', then saw 'head',
    # ended the loop, and returned the 'specs' list.
    assert remaining == ["1-5"]


def test_parse_inject_args_incomplete_command_at_end():
    """
    Tests that 'head' at the end of a valid command
    is returned as a remaining spec.
    """
    args = ["1-5", "head", "h1", "odd", "tail"]
    heads, tails, remaining = cp.parse_inject_args(args)

    expected_heads = [{"specs": ["1-5"], "code": "h1"}]

    assert heads == expected_heads
    assert tails == []
    # 'odd' was collected, then 'tail' was seen, loop ended.
    assert remaining == ["odd"]
