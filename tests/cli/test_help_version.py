from unittest.mock import patch

import pytest

from pdftl.cli.help_version import print_version


def test_print_version_no_console():
    with patch("pdftl.cli.help_version.get_console", return_value=None):
        with pytest.raises(RuntimeError, match="Rich console is not available"):
            print_version()
