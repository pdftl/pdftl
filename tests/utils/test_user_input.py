import os
from unittest.mock import MagicMock, call

# --- Import the module and functions to test ---
from pdftl.utils.user_input import (
    FILENAME_COMPLETER_DELIMS,
    UserInputContext,
    _get_all_filename_matches,
    dirname_completer,
    filename_completer,
    get_input,
    pdf_filename_completer,
)


def test_user_input_context():
    """Tests that the UserInputContext dataclass holds callables."""
    mock_input = lambda: "input"
    mock_pass = lambda: "pass"

    context = UserInputContext(get_input=mock_input, get_pass=mock_pass)

    assert context.get_input is mock_input
    assert context.get_pass is mock_pass
    assert context.get_input() == "input"
    assert context.get_pass() == "pass"


def test_get_input_no_completer(mocker):
    """Tests get_input in its simplest form (no completer)."""
    try:
        import readline
    except ImportError:
        return

    # Patch the built-in input() function
    mock_input = mocker.patch("builtins.input", return_value="test_output")

    # Patch the lazy-loaded readline module
    mock_readline = MagicMock()
    mocker.patch.dict("sys.modules", {"readline": mock_readline})

    result = get_input("My Prompt: ")

    # Check that input() was called correctly
    mock_input.assert_called_once_with("My Prompt: ")
    assert result == "test_output"

    # Check that readline was NOT used
    mock_readline.set_completer.assert_not_called()


def test_get_input_with_completer(mocker):
    """
    Tests that get_input correctly sets up and tears down
    the readline completer.
    """
    try:
        import readline
    except ImportError:
        return

    # Patch the built-in input() function
    mock_input = mocker.patch("builtins.input", return_value="test_file.pdf")

    # Patch the lazy-loaded readline module
    mock_readline = MagicMock()
    mocker.patch.dict("sys.modules", {"readline": mock_readline})

    # --- Arrange mock readline state ---
    # Store the (mock) old state
    mock_old_completer = "OLD_COMPLETER"
    mock_old_delims = "OLD_DELIMS"
    mock_readline.get_completer.return_value = mock_old_completer
    mock_readline.get_completer_delims.return_value = mock_old_delims

    # This is the new completer function we're passing in
    my_completer = MagicMock(name="my_completer")

    # --- Act ---
    result = get_input("File: ", completer=my_completer)

    # --- Assert ---
    # 1. Check that input() was called
    mock_input.assert_called_once_with("File: ")
    assert result == "test_file.pdf"

    # 2. Check that readline was set up correctly
    mock_readline.set_completer.assert_any_call(my_completer)
    mock_readline.set_completer_delims.assert_any_call(FILENAME_COMPLETER_DELIMS)
    mock_readline.parse_and_bind.assert_called_with("tab: complete")

    # 3. Check that the 'finally' block restored the old state
    # This is the most critical part
    assert mock_readline.set_completer.call_args_list == [
        call(my_completer),  # First call (setup)
        call(mock_old_completer),  # Second call (teardown)
    ]
    assert mock_readline.set_completer_delims.call_args_list == [
        call(FILENAME_COMPLETER_DELIMS),  # First call (setup)
        call(mock_old_delims),  # Second call (teardown)
    ]


def test_get_all_filename_matches(mocker):
    """Tests the filesystem-matching helper function."""
    # Patch the functions that touch the filesystem
    mock_glob = mocker.patch("pdftl.utils.user_input.glob.glob")
    mock_isdir = mocker.patch("pdftl.utils.user_input.os.path.isdir")
    mock_expanduser = mocker.patch("pdftl.utils.user_input.os.path.expanduser")

    # Define the mock filesystem behavior
    mock_expanduser.side_effect = lambda p: p.replace("~", "/home/user")
    mock_glob.return_value = ["/home/user/my_dir", "/home/user/file.txt"]
    mock_isdir.side_effect = lambda f: f == "/home/user/my_dir"

    # --- Act ---
    matches = _get_all_filename_matches("~/")

    # --- Assert ---
    # Check that it correctly added a trailing slash to the directory
    assert matches == ["/home/user/my_dir" + os.path.sep, "/home/user/file.txt"]

    # Check that the correct path was passed to glob
    mock_expanduser.assert_called_with("~/")
    mock_glob.assert_called_with("/home/user/*")


def test_filename_completer(mocker):
    """Tests the basic filename_completer."""
    # Mock the helper function it relies on
    mock_get_all = mocker.patch(
        "pdftl.utils.user_input._get_all_filename_matches",
        return_value=["file1.txt", "file2.txt"],
    )

    # State 0: Get the first match
    assert filename_completer("file", 0) == "file1.txt"
    # State 1: Get the second match
    assert filename_completer("file", 1) == "file2.txt"
    # State 2: No more matches
    assert filename_completer("file", 2) is None

    # Check that the helper was called correctly
    mock_get_all.assert_called_with("file", "*")


def test_pdf_filename_completer(mocker):
    """Tests the PDF-specific completer."""
    mock_get_all = mocker.patch(
        "pdftl.utils.user_input._get_all_filename_matches",
        return_value=["doc.pdf", "report.PDF"],
    )

    assert pdf_filename_completer("d", 0) == "doc.pdf"
    assert pdf_filename_completer("d", 1) == "report.PDF"
    assert pdf_filename_completer("d", 2) is None

    # Check that it passed the PDF-specific glob suffix
    mock_get_all.assert_called_with("d", "*.[Pp][Dd][Ff]")


def test_dirname_completer(mocker):
    """Tests the directory-only completer."""
    # Note: The helper adds the trailing separator, which is what
    # this function uses to filter.
    mock_get_all = mocker.patch(
        "pdftl.utils.user_input._get_all_filename_matches",
        return_value=["/docs" + os.path.sep, "/file.txt", "/other" + os.path.sep],
    )

    # State 0: Gets the first directory
    assert dirname_completer("/", 0) == "/docs" + os.path.sep
    # State 1: Skips the file and gets the second directory
    assert dirname_completer("/", 1) == "/other" + os.path.sep
    # State 2: No more matches
    assert dirname_completer("/", 2) is None

    # Check that it called the helper normally
    mock_get_all.assert_called_with("/", "*")
