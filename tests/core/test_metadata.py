# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/core/test_metadata.py

import importlib.metadata
import sys
from unittest.mock import MagicMock, patch

from pdftl.core.metadata import (
    _get_status,
    get_dependencies_status,
    get_project_version,
    _should_fallback_to_changelog,
    _build_changelog_fallback_version,
    _parse_changelog_version,
)


class TestGetProjectVersion:
    @patch("importlib.metadata.version")
    def test_get_project_version_from_metadata(self, mock_version):
        """Test reading the version successfully from standard package metadata."""
        mock_version.return_value = "1.2.3"
        assert get_project_version() == "1.2.3"
        mock_version.assert_called_once_with("pdftl")

    @patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError)
    def test_get_project_version_from_fallback(self, mock_version):
        """Test reading from _version.py fallback when package metadata is missing."""
        mock_fallback_module = MagicMock()
        mock_fallback_module.version = "2.0.0"

        # Inject our mocked module into sys.modules so the internal import succeeds
        # We must mock _parse_changelog_version to return (None, None) so it doesn't find the real CHANGELOG.md
        with patch.dict(sys.modules, {"pdftl._version": mock_fallback_module}):
            with patch("pdftl.core.metadata._parse_changelog_version", return_value=(None, None)):
                assert get_project_version() == "2.0.0"

    @patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError)
    def test_get_project_version_unknown(self, mock_version):
        """Test the ultimate fallback when both metadata, _version.py, and changelog parsing fail."""
        with patch.dict(sys.modules, {"pdftl._version": None}):
            with patch("pdftl.core.metadata._parse_changelog_version", return_value=(None, None)):
                assert get_project_version() == "unknown-dev-version"

    @patch("importlib.metadata.version")
    @patch("pdftl.core.metadata._parse_changelog_version", return_value=(None, None))
    def test_get_project_version_fallback_to_metadata_when_changelog_missing(
        self, mock_parse, mock_version
    ):
        """Test fallback when metadata is a 0.0.x version but changelog is missing."""
        mock_version.return_value = "0.0.2"
        assert get_project_version() == "0.0.2"

    @patch("importlib.metadata.version")
    @patch("pdftl.core.metadata._parse_changelog_version", return_value=("1.2.0", False))
    def test_get_project_version_dirty_fallback_success(self, mock_parse, mock_version):
        """Test full fallback to changelog when directory is dirty."""
        mock_version.value = "0.0.0.post1+dirty"
        mock_version.return_value = "0.0.0.post1+dirty"
        assert get_project_version() == "1.2.0+changelog.dirty"

    @patch("importlib.metadata.version")
    @patch("pdftl.core.metadata._parse_changelog_version", return_value=("1.2.0", False))
    def test_get_project_version_clean_fallback_success(self, mock_parse, mock_version):
        """Test full fallback to changelog when directory is clean but version is 0.0.1."""
        mock_version.return_value = "0.0.1"
        assert get_project_version() == "1.2.0+changelog"


class TestParseChangelogVersion:
    @patch("pathlib.Path.is_file", return_value=True)
    @patch("builtins.open")
    def test_parse_changelog_version_found_unreleased(self, mock_open, mock_is_file):
        """Verify regex correctly detects Unreleased markers with custom brackets and formats."""
        mock_file = MagicMock()
        mock_file.readlines.return_value = ["## [Unreleased] (Draft notes)\n", "## v1.5.0\n"]
        mock_open.return_value.__enter__.return_value = mock_file

        ver, is_unreleased = _parse_changelog_version()
        assert ver == "1.5.0"
        assert is_unreleased is True

    @patch("pathlib.Path.is_file", return_value=True)
    @patch("builtins.open")
    def test_parse_changelog_version_bracketless_unreleased(self, mock_open, mock_is_file):
        """Verify regex catches unreleased markers without square brackets."""
        mock_file = MagicMock()
        mock_file.readlines.return_value = ["## Unreleased modifications\n", "## v1.8.0-rc1\n"]
        mock_open.return_value.__enter__.return_value = mock_file

        ver, is_unreleased = _parse_changelog_version()
        assert ver == "1.8.0-rc1"
        assert is_unreleased is True

    @patch("pathlib.Path.is_file", return_value=True)
    @patch("builtins.open")
    def test_parse_changelog_version_standard_header(self, mock_open, mock_is_file):
        """Verify normal parsing of release version structures without prior unreleased markers."""
        mock_file = MagicMock()
        mock_file.readlines.return_value = ["## [2.1.3] - 2026-07-14\n"]
        mock_open.return_value.__enter__.return_value = mock_file

        ver, is_unreleased = _parse_changelog_version()
        assert ver == "2.1.3"
        assert is_unreleased is False

    @patch("pathlib.Path.is_file", return_value=True)
    @patch("builtins.open", side_effect=OSError("Permission Denied"))
    def test_parse_changelog_version_io_failure_safe_return(self, mock_open, mock_is_file):
        """Verify that filesystem read errors are captured safely without bubbling."""
        ver, is_unreleased = _parse_changelog_version()
        assert ver is None
        assert is_unreleased is None

    @patch("pathlib.Path.is_file", side_effect=[False, True, False, False, False])
    @patch("builtins.open")
    def test_parse_changelog_version_skips_missing_files(self, mock_open, mock_is_file):
        """Verify that directory search skips missing candidate files and continues climbing."""
        mock_file = MagicMock()
        mock_file.readlines.return_value = ["## [1.0.0]\n"]
        mock_open.return_value.__enter__.return_value = mock_file

        ver, is_unreleased = _parse_changelog_version()
        assert ver == "1.0.0"
        assert is_unreleased is False
        assert mock_is_file.call_count >= 2


class TestVersioningHelpers:
    def test_should_fallback_to_changelog(self):
        """Validate logical flags for triggering deep SCM metadata fallbacks."""
        assert _should_fallback_to_changelog(None) is True
        assert _should_fallback_to_changelog("0.0.0.post1+dirty") is True
        assert _should_fallback_to_changelog("1.0.0") is False
        assert _should_fallback_to_changelog("2.3.4.post0") is True
        assert _should_fallback_to_changelog("0.0.1") is True

    def test_build_changelog_fallback_version(self):
        """Verify PEP 440 local metadata generation formatting builds correct string labels."""
        # Clean build, already released
        assert _build_changelog_fallback_version("1.2.0", False, False) == "1.2.0+changelog"
        # Dirty build, already released
        assert _build_changelog_fallback_version("1.2.0", False, True) == "1.2.0+changelog.dirty"
        # Clean build, unreleased changes ahead of stable
        assert (
            _build_changelog_fallback_version("1.3.0", True, False)
            == "1.3.0.post0.dev0+unreleased.changelog"
        )
        # Dirty build, unreleased changes ahead of stable
        assert (
            _build_changelog_fallback_version("1.3.0", True, True)
            == "1.3.0.post0.dev0+unreleased.changelog.dirty"
        )


class TestGetDependenciesStatus:
    @patch("importlib.metadata.requires")
    def test_dependencies_status_package_not_found(self, mock_requires):
        """Test behavior when the target package is not installed."""
        mock_requires.side_effect = importlib.metadata.PackageNotFoundError
        assert get_dependencies_status() == ([], [])

    @patch("importlib.metadata.requires")
    def test_dependencies_status_no_requirements(self, mock_requires):
        """Test behavior when package has no requirements (returns None)."""
        mock_requires.return_value = None
        assert get_dependencies_status() == ([], [])

    @patch("importlib.metadata.requires")
    @patch("pdftl.core.metadata._get_status")
    def test_dependencies_status_parsing_logic(self, mock_get_status, mock_requires):
        """Test parsing logic for required, ignored extras, optional, and self-references."""
        mock_requires.return_value = [
            "pdftl",  # Should be ignored (self-reference)
            "requests>=2.0",  # Should be marked as REQUIRED
            "pytest; extra == 'test'",  # Should be ignored (dev group, single quote)
            'sphinx; extra == "docs"',  # Should be ignored (dev group, double quote)
            "black; extra == 'dev'",  # Should be ignored
            "tox; extra == 'dev-all'",  # Should be ignored
            "rich; extra == 'cli'",  # Should be marked as OPTIONAL
            "click>8.0; extra == 'gui'",  # Should be marked as OPTIONAL
        ]

        # Mock _get_status to just return a dummy tuple so we can verify what was passed to it
        mock_get_status.side_effect = lambda pkgs: [(p, "dummy") for p in pkgs]

        req_status, opt_status = get_dependencies_status()

        # Check that _get_status was called twice (once for required, once for optional)
        assert mock_get_status.call_count == 2

        # Extract the sets of packages passed to _get_status
        req_pkgs_called = mock_get_status.call_args_list[0][0][0]
        opt_pkgs_called = mock_get_status.call_args_list[1][0][0]

        assert set(req_pkgs_called) == {"requests"}
        assert set(opt_pkgs_called) == {"rich", "click"}


class TestGetStatus:
    @patch("importlib.metadata.version")
    def test_get_status_sorting_and_resolution(self, mock_version):
        """Test that _get_status correctly identifies missing vs installed packages and sorts them."""

        # Mock version resolution: return a version for 'installed_pkg', raise error for 'missing_pkg'
        def version_side_effect(pkg_name):
            if pkg_name == "installed_pkg":
                return "1.5.0"
            raise importlib.metadata.PackageNotFoundError

        mock_version.side_effect = version_side_effect

        # Pass an unsorted set to ensure _get_status sorts it alphabetically
        pkgs = {"missing_pkg", "installed_pkg"}

        result = _get_status(pkgs)

        assert result == [("installed_pkg", "1.5.0"), ("missing_pkg", None)]
