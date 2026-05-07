import pytest

from pdftl.cli.help import _get_rtd_url


def test_get_rtd_url_standard_command(mocker):
    # Mock version to a stable release
    mocker.patch("pdftl.cli.help.get_project_version", return_value="1.2.0")

    # Simulate a command in pdftl.operations.cat
    url = _get_rtd_url("cat", "Command", {}, "pdftl.operations.cat")
    assert url == "https://pdftl.readthedocs.io/en/stable/operations/cat.html"


def test_get_rtd_url_dev_version(mocker):
    # Mock version to a dev release
    mocker.patch("pdftl.cli.help.get_project_version", return_value="1.2.0.dev1")

    url = _get_rtd_url("merge", "Command", {}, "pdftl.operations.merge")
    assert "/en/latest/" in url


def test_get_rtd_url_with_anchor_for_options(mocker):
    mocker.patch("pdftl.cli.help.get_project_version", return_value="1.0.0")

    # Simulate an OutputOption help request
    url = _get_rtd_url("Grid Columns", "OutputOption", {}, "pdftl.core.options")
    # Should resolve to output_options.html#grid-columns
    assert url == "https://pdftl.readthedocs.io/en/stable/misc/output_options.html#grid-columns"


from pdftl.cli.help import _resolve_special_help_topic


@pytest.mark.parametrize(
    "input_topic,expected",
    [
        ("shell completion", "shell_completion"),
        ("bash", "shell_completion"),
        ("shell_completion", "shell_completion"),
        ("all", "all"),
        ("invalid", None),
    ],
)
def test_resolve_special_help_topic(input_topic, expected):
    assert _resolve_special_help_topic(input_topic) == expected


from pdftl.cli.help import _print_help_trailer


def test_print_help_trailer_formatting(mocker):
    # Mock the print function to capture output
    mock_hprint = mocker.Mock()
    topic_data = {"tags": ["cli", "setup"], "caller": "pdftl.cli.completion_setup"}
    mocker.patch("pdftl.cli.help.get_project_version", return_value="1.0.0")

    _print_help_trailer(mock_hprint, topic_data, "shell_completion")

    # Check if RTD URL was printed in markdown format
    calls = [call[0][0] for call in mock_hprint.call_args_list]

    assert any("Read online:" in c for c in calls)
    assert any(
        "https://pdftl.readthedocs.io/en/stable/general/shell_completion.html" in c for c in calls
    )
    assert any("*Type: dict*" in c for c in calls)


@pytest.mark.parametrize(
    "topic, expected_suffix",
    [
        ("Grid Columns", "misc/output_options.html#grid-columns"),
        ("Sign-Certificate", "misc/output_options.html#sign-certificate"),
        # Special topics should NOT have anchors
        ("shell_completion", "general/shell_completion.html"),
    ],
)
def test_rtd_url_structure(mocker, topic, expected_suffix):
    mocker.patch("pdftl.cli.help.get_project_version", return_value="1.0.0")

    # We treat these as Options to test the conditional anchor logic
    url = _get_rtd_url(topic, "OutputOption", {}, "pdftl.core.options")

    assert url.endswith(expected_suffix)
    if "#" in expected_suffix:
        assert "#" in url
    else:
        assert "#" not in url
