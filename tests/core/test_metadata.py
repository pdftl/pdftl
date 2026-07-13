# tests/core/test_metadata.py

import importlib.metadata
import sys
from unittest.mock import MagicMock, patch


from pdftl.core.metadata import _get_status, get_dependencies_status, get_project_version


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
        with patch.dict(sys.modules, {"pdftl._version": mock_fallback_module}):
            assert get_project_version() == "2.0.0"

    @patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError)
    def test_get_project_version_unknown(self, mock_version):
        """Test the ultimate fallback when both metadata and _version.py fail."""
        # Setting a module to None in sys.modules forces an ImportError when imported
        with patch.dict(sys.modules, {"pdftl._version": None}):
            assert get_project_version() == "unknown-dev-version"


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
