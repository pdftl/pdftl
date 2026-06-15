# tests/fonts/test_file_locator.py

import os
import sys
from unittest.mock import MagicMock, patch

from pdftl.fonts import file_locator
from pdftl.fonts.file_locator import (
    _get_internal_font_names,
    _get_font_directories,
    _resolve_linux_fontconfig,
    _has_fonttools,
    _iter_system_font_files,
    _is_font_match,
    _scan_system_font_dirs,
    resolve_system_font_path,
)

# --- _get_internal_font_names tests ---


def test_get_internal_font_names_import_error():
    """Covers lines 25-26: Gracefully returns an empty set if fontTools is missing."""
    with patch.dict(sys.modules, {"fontTools.ttLib": None}):
        assert _get_internal_font_names("dummy.ttf") == set()


def test_get_internal_font_names_missing_name_table():
    """Covers line 32: Returns empty set if 'name' table is absent in the font."""
    mock_tt = MagicMock()
    mock_tt.__contains__.return_value = False  # "name" not in tt

    with patch("fontTools.ttLib.TTFont", return_value=mock_tt):
        assert _get_internal_font_names("dummy.ttf") == set()


def test_get_internal_font_names_unicode_error():
    """Covers lines 39-40: Continues processing if a record field throws UnicodeError."""
    mock_record_bad = MagicMock()
    mock_record_bad.nameID = 1
    mock_record_bad.toUnicode.side_effect = UnicodeError

    mock_record_good = MagicMock()
    mock_record_good.nameID = 4
    mock_record_good.toUnicode.return_value = "Good Font"

    mock_tt = MagicMock()
    mock_tt.__contains__.return_value = True
    mock_tt.__getitem__.return_value.names = [mock_record_bad, mock_record_good]

    # Configure the context manager context hook
    mock_tt.__enter__.return_value = mock_tt

    with patch("fontTools.ttLib.TTFont", return_value=mock_tt):
        names = _get_internal_font_names("dummy.ttf")
        assert "goodfont" in names
        assert len(names) == 1


def test_get_internal_font_names_general_exception(caplog):
    """Covers lines 41-42: Logs a debug message and returns an empty set on table reading failures."""
    with patch("fontTools.ttLib.TTFont", side_effect=TypeError("Corrupted byte structure")):
        with caplog.at_level("DEBUG"):
            names = _get_internal_font_names("dummy.ttf")
            assert names == set()
            assert "FontTools failed to read" in caplog.text


# --- _get_font_directories tests ---


def test_get_font_directories_windows():
    """Covers lines 50-51: Resolves correct Windows system locations."""
    with patch("sys.platform", "win32"), patch.dict(os.environ, {"WINDIR": "C:\\MockWin"}):
        dirs = _get_font_directories()
        assert os.path.join("C:\\MockWin", "Fonts") in dirs
        assert os.path.join("C:\\MockWin", "Local\\Microsoft\\Windows\\Fonts") in dirs


def test_get_font_directories_darwin():
    """Covers line 57: Resolves standard macOS library pathways."""
    with (
        patch("sys.platform", "darwin"),
        patch("os.path.expanduser", return_value="/Users/mock/Library/Fonts"),
    ):
        dirs = _get_font_directories()
        assert "/Library/Fonts" in dirs
        assert "/System/Library/Fonts" in dirs
        assert "/Users/mock/Library/Fonts" in dirs


# --- _resolve_linux_fontconfig tests ---


def test_resolve_linux_fontconfig_not_linux():
    """Covers line 71: Instantly bails out with None if evaluated on non-Linux platforms."""
    with patch("sys.platform", "darwin"):
        assert _resolve_linux_fontconfig("Arial") is None


def test_resolve_linux_fontconfig_invalid_output_format():
    """Covers line 82: Returns None if fc-match generates an unparseable stream layout."""
    mock_res = MagicMock(stdout="malformed-output-no-pipe-delimiter")
    with patch("sys.platform", "linux"), patch("subprocess.run", return_value=mock_res):
        assert _resolve_linux_fontconfig("Arial") is None


def test_resolve_linux_fontconfig_generic_fallback_ignored():
    """Covers loop filtering logic (returning None) if the OS returns an unrelated generic font."""
    # Searching for "CustomFont", but system yields "Noto Sans" path
    mock_res = MagicMock(stdout="/usr/share/fonts/noto/NotoSans.ttf|Noto Sans")
    with patch("sys.platform", "linux"), patch("subprocess.run", return_value=mock_res):
        assert _resolve_linux_fontconfig("CustomFont") is None


def test_resolve_linux_fontconfig_success():
    """Covers lines 92-93: Returns the actual path on a strict matching criteria."""
    mock_res = MagicMock(stdout="/usr/share/fonts/arial.ttf|Arial")
    with (
        patch("sys.platform", "linux"),
        patch("subprocess.run", return_value=mock_res),
        patch("os.path.isfile", return_value=True),
    ):
        assert _resolve_linux_fontconfig("Arial") == "/usr/share/fonts/arial.ttf"


def test_resolve_linux_fontconfig_exceptions():
    """Covers lines 95-96: Returns None gracefully if subprocess commands aren't available."""
    with patch("sys.platform", "linux"), patch("subprocess.run", side_effect=FileNotFoundError):
        assert _resolve_linux_fontconfig("Arial") is None


# --- Remaining file_locator internal helpers ---


def test_has_fonttools_missing():
    """Covers line 107-108: Explicitly yields False when fontTools package is completely uninstalled."""
    with patch.dict(sys.modules, {"fontTools": None}):
        assert _has_fonttools() is False


def test_iter_system_font_files_skips_nonexistent_dir():
    """Covers lines 114-115: Validates that non-existent system paths are ignored cleanly."""
    with (
        patch.object(file_locator, "_get_font_directories", return_value=["/fake/dir"]),
        patch("os.path.exists", return_value=False),
    ):
        results = list(_iter_system_font_files())
        assert results == []


def test_is_font_match_without_fonttools():
    """Covers lines 129-130: Checks filename match fallback strategy without using fontTools structural metrics."""
    assert (
        _is_font_match("/path/Arial Bold.ttf", "Arial Bold.ttf", "arialbold", use_fonttools=False)
        is True
    )
    assert _is_font_match("/path/Arial.ttf", "Arial.ttf", "times", use_fonttools=False) is False


def test_resolve_system_font_path_shortcut():
    """Covers line 151: Validates early return if fontconfig resolves the track dynamically."""
    with patch.object(
        file_locator, "_resolve_linux_fontconfig", return_value="/resolved/path.ttf"
    ):
        assert resolve_system_font_path("AnyFont") == "/resolved/path.ttf"


def test_scan_system_font_dirs_finds_match():
    """Covers line 139: Ensures a matching file during folder iteration returns the path."""
    with (
        patch(
            "pdftl.fonts.file_locator._iter_system_font_files",
            return_value=[("/mock/dir/TestFont.ttf", "TestFont.ttf")],
        ),
        patch("pdftl.fonts.file_locator._is_font_match", return_value=True),
    ):
        result = _scan_system_font_dirs("testfont")
        assert result == "/mock/dir/TestFont.ttf"


# ==============================================================================
# Target Coverage Gap Fixes
# ==============================================================================


def test_get_font_directories_linux_fallback():
    """Covers Line 60: Validates standard Linux/Fallback paths are returned."""
    with patch("sys.platform", "linux"):
        dirs = _get_font_directories()
        assert "/usr/share/fonts" in dirs
        assert "/usr/local/share/fonts" in dirs
        assert any("~/.fonts" in d or ".fonts" in d for d in dirs)


def test_iter_system_font_files_directory_walk():
    """Covers Lines 117-120: Exercises file extension matching logic during os.walk."""
    import tempfile

    fake_dir = tempfile.gettempdir()  # guaranteed valid absolute path on every OS
    mock_walk_data = [
        (fake_dir, ["subfolder"], ["Arial.ttf", "Ubuntu.otf", "readme.txt", "Courier.ttc"])
    ]
    with (
        patch.object(file_locator, "_get_font_directories", return_value=[fake_dir]),
        patch("os.path.exists", return_value=True),
        patch("os.walk", return_value=mock_walk_data),
    ):
        results = list(_iter_system_font_files())
        # Should cleanly register the 3 font files and filter out the .txt file
        assert len(results) == 3
        assert results == [
            (os.path.join(fake_dir, f), f) for f in ("Arial.ttf", "Ubuntu.otf", "Courier.ttc")
        ]


def test_is_font_match_using_fonttools_branch():
    """Covers Lines 126-127: Forces execution through internal metadata name parsing when use_fonttools=True."""
    with patch("pdftl.fonts.file_locator._get_internal_font_names", return_value={"opensans"}):
        assert _is_font_match("/path/font.ttf", "font.ttf", "opensans", use_fonttools=True) is True
        assert (
            _is_font_match("/path/font.ttf", "font.ttf", "notfound", use_fonttools=True) is False
        )


def test_scan_system_font_dirs_returns_none_fallback():
    """Covers Line 141: Reaches the termination fallback when no font files match the criteria."""
    with patch("pdftl.fonts.file_locator._iter_system_font_files", return_value=[]):
        assert _scan_system_font_dirs("nonexistent_font_name") is None


def test_resolve_system_font_path_falls_through_to_scan():
    """Covers Lines 153-154: Forces linux fontconfig to fail so execution reaches the directory scanning logic."""
    with (
        patch.object(file_locator, "_resolve_linux_fontconfig", return_value=None),
        patch.object(
            file_locator, "_scan_system_font_dirs", return_value="/mock/path/Font.ttf"
        ) as mock_scan,
    ):
        path = resolve_system_font_path("ScanMeFont")
        assert path == "/mock/path/Font.ttf"
        mock_scan.assert_called_once_with("scanmefont")
