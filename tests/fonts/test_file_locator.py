# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/fonts/test_file_locator.py

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from pdftl.fonts import file_locator
from pdftl.fonts.file_locator import (
    _get_internal_font_names,
    _get_font_directories,
    _resolve_linux_fontconfig,
    _has_fonttools,
    _iter_system_font_files,
    _is_font_match,
    _is_acceptable_alias,
    _scan_system_font_dirs,
    resolve_system_font_path,
)

# --- _is_acceptable_alias tests ---


def test_is_acceptable_alias_exact():
    assert _is_acceptable_alias("helvetica", "helvetica") is True
    assert _is_acceptable_alias("customfont", "customfont") is True


def test_is_acceptable_alias_substitution():
    assert _is_acceptable_alias("helvetica", "arial") is True
    assert _is_acceptable_alias("times-roman", "nimbusroman") is True


def test_is_acceptable_alias_style_enforcement():
    # Should not match regular arial if bold is requested
    assert _is_acceptable_alias("helvetica-bold", "arial") is False
    # Should match bold
    assert _is_acceptable_alias("helvetica-bold", "arialbold") is True
    # Should match oblique to italic
    assert _is_acceptable_alias("helvetica-oblique", "arialitalic") is True


# --- _get_internal_font_names tests ---


def test_get_internal_font_names_import_error():
    """Gracefully returns an empty set if fontTools is missing."""
    with patch.dict(sys.modules, {"fontTools.ttLib": None}):
        assert _get_internal_font_names("dummy.ttf") == set()


def test_get_internal_font_names_missing_name_table():
    """Returns empty set if 'name' table is absent in the font."""
    mock_tt = MagicMock()
    mock_tt.__contains__.return_value = False  # "name" not in tt

    with patch("fontTools.ttLib.TTFont", return_value=mock_tt):
        assert _get_internal_font_names("dummy.ttf") == set()


def test_get_internal_font_names_unicode_error():
    """Continues processing if a record field throws UnicodeError."""
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
    """Logs a debug message and returns an empty set on table reading failures."""
    with patch("fontTools.ttLib.TTFont", side_effect=TypeError("Corrupted byte structure")):
        with caplog.at_level("DEBUG"):
            names = _get_internal_font_names("dummy.ttf")
            assert names == set()
            assert "FontTools failed to read" in caplog.text


# --- _get_font_directories tests ---


def test_get_font_directories_windows():
    """Resolves correct Windows system locations."""
    with patch("sys.platform", "win32"), patch.dict(os.environ, {"WINDIR": "C:\\MockWin"}):
        dirs = _get_font_directories()
        assert os.path.join("C:\\MockWin", "Fonts") in dirs
        assert os.path.join("C:\\MockWin", "Local\\Microsoft\\Windows\\Fonts") in dirs


def test_get_font_directories_darwin():
    """Resolves standard macOS library pathways."""
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
    """Instantly bails out with None if evaluated on non-Linux platforms."""
    with patch("sys.platform", "darwin"):
        assert _resolve_linux_fontconfig("Arial") is None


def test_resolve_linux_fontconfig_invalid_output_format():
    """Returns None if fc-match generates an unparseable stream layout."""
    mock_res = MagicMock(stdout="malformed-output-no-pipe-delimiter")
    with patch("sys.platform", "linux"), patch("subprocess.run", return_value=mock_res):
        assert _resolve_linux_fontconfig("Arial") is None


def test_resolve_linux_fontconfig_generic_fallback_ignored():
    """Loop filtering logic (returning None) if the OS returns an unrelated generic font."""
    # Searching for "CustomFont", but system yields "Noto Sans" path
    mock_res = MagicMock(stdout="/usr/share/fonts/noto/NotoSans.ttf|Noto Sans")
    with patch("sys.platform", "linux"), patch("subprocess.run", return_value=mock_res):
        assert _resolve_linux_fontconfig("CustomFont") is None


def test_resolve_linux_fontconfig_success():
    """Returns the actual path on a strict matching criteria."""
    mock_res = MagicMock(stdout="/usr/share/fonts/arial.ttf|Arial")
    with (
        patch("sys.platform", "linux"),
        patch("subprocess.run", return_value=mock_res),
        patch("os.path.isfile", return_value=True),
    ):
        assert _resolve_linux_fontconfig("Arial") == "/usr/share/fonts/arial.ttf"


def test_resolve_linux_fontconfig_exceptions():
    """Returns None gracefully if subprocess commands aren't available."""
    with patch("sys.platform", "linux"), patch("subprocess.run", side_effect=FileNotFoundError):
        assert _resolve_linux_fontconfig("Arial") is None


# --- Remaining file_locator internal helpers ---


def test_has_fonttools_missing():
    """Explicitly yields False when fontTools package is completely uninstalled."""
    with patch.dict(sys.modules, {"fontTools": None}):
        assert _has_fonttools() is False


def test_iter_system_font_files_skips_nonexistent_dir():
    """Validates that non-existent system paths are ignored cleanly."""
    with patch("os.path.exists", return_value=False):
        results = list(_iter_system_font_files(["/fake/dir"]))
        assert results == []


def test_is_font_match_without_fonttools():
    """Checks filename match fallback strategy without using fontTools structural metrics."""
    assert (
        _is_font_match("/path/Arial Bold.ttf", "Arial Bold.ttf", "arialbold", use_fonttools=False)
        is True
    )
    assert _is_font_match("/path/Arial.ttf", "Arial.ttf", "times", use_fonttools=False) is False


def test_resolve_system_font_path_shortcut():
    """Validates early return if fontconfig resolves the track dynamically."""
    with patch.object(
        file_locator, "_resolve_linux_fontconfig", return_value="/resolved/path.ttf"
    ):
        assert resolve_system_font_path("AnyFont") == "/resolved/path.ttf"


def test_scan_system_font_dirs_finds_match():
    """Ensures a matching file during folder iteration returns the path."""
    with (
        patch(
            "pdftl.fonts.file_locator._iter_system_font_files",
            return_value=[("/mock/dir/TestFont.ttf", "TestFont.ttf")],
        ),
        patch("pdftl.fonts.file_locator._is_font_match", return_value=True),
    ):
        result = _scan_system_font_dirs("testfont", ["/mock/dir"])
        assert result == "/mock/dir/TestFont.ttf"


def test_get_font_directories_linux_fallback():
    """Validates standard Linux/Fallback paths are returned."""
    with patch("sys.platform", "linux"):
        dirs = _get_font_directories()
        assert "/usr/share/fonts" in dirs
        assert "/usr/local/share/fonts" in dirs
        assert any("~/.fonts" in d or ".fonts" in d for d in dirs)


def test_iter_system_font_files_directory_walk():
    """Exercises file extension matching logic during os.walk."""
    import tempfile

    fake_dir = tempfile.gettempdir()  # guaranteed valid absolute path on every OS
    mock_walk_data = [
        (
            fake_dir,
            ["subfolder"],
            ["Arial.ttf", "Ubuntu.otf", "readme.txt", "Courier.ttc", "Heros.pfb"],
        )
    ]
    with (
        patch("os.path.exists", return_value=True),
        patch("os.walk", return_value=mock_walk_data),
    ):
        results = list(_iter_system_font_files([fake_dir]))
        # Should cleanly register the 4 font files and filter out the .txt file
        assert len(results) == 4
        assert results == [
            (os.path.join(fake_dir, f), f)
            for f in ("Arial.ttf", "Ubuntu.otf", "Courier.ttc", "Heros.pfb")
        ]


def test_is_font_match_using_fonttools_branch():
    """Forces execution through internal metadata name parsing when use_fonttools=True."""
    with patch("pdftl.fonts.file_locator._get_internal_font_names", return_value={"opensans"}):
        assert _is_font_match("/path/font.ttf", "font.ttf", "opensans", use_fonttools=True) is True
        assert (
            _is_font_match("/path/font.ttf", "font.ttf", "notfound", use_fonttools=True) is False
        )


def test_scan_system_font_dirs_returns_none_fallback():
    """Reaches the termination fallback when no font files match the criteria."""
    with patch("pdftl.fonts.file_locator._iter_system_font_files", return_value=[]):
        assert _scan_system_font_dirs("nonexistent_font_name", ["/mock"]) is None


def test_resolve_system_font_path_falls_through_to_scan():
    """Forces linux fontconfig to fail so execution reaches the directory scanning logic."""
    with (
        patch.object(file_locator, "_resolve_linux_fontconfig", return_value=None),
        patch.object(
            file_locator, "_scan_system_font_dirs", return_value="/mock/path/Font.ttf"
        ) as mock_scan,
    ):
        path = resolve_system_font_path("ScanMeFont")
        assert path == "/mock/path/Font.ttf"
        mock_scan.assert_called_once_with("scanmefont", file_locator._get_font_directories(), None)


def test_resolve_system_font_path_custom_dirs_priority():
    """Ensures custom directories are checked before falling back to the system."""
    with (
        patch.object(
            file_locator, "_scan_system_font_dirs", return_value="/custom/font.ttf"
        ) as mock_scan,
        patch.object(
            file_locator, "_resolve_linux_fontconfig", return_value="/sys/font.ttf"
        ) as mock_fc,
    ):
        path = resolve_system_font_path("MyFont", custom_dirs=["/custom"])
        assert path == "/custom/font.ttf"
        mock_scan.assert_called_once_with("myfont", ["/custom"], None)
        mock_fc.assert_not_called()


def test_resolve_system_font_path_nosys_skips_system_dirs():
    """Ensures that use_system=False skips fontconfig and standard system directories."""
    with (
        patch.object(file_locator, "_scan_system_font_dirs", return_value=None) as mock_scan,
        patch.object(
            file_locator, "_resolve_linux_fontconfig", return_value="/sys/font.ttf"
        ) as mock_fc,
    ):
        path = resolve_system_font_path("MyFont", use_system=False)
        assert path is None
        mock_scan.assert_not_called()
        mock_fc.assert_not_called()


# ============================================================================
# Style Matching Verification Tests
# ============================================================================


def test_resolve_regular_font_does_not_match_bold_italic_file(tmp_path):
    """
    Verifies that a Regular font request (e.g., 'Helvetica') does not match
    a Bold Italic font file (e.g., 'NimbusSans-BoldItalic.otf') merely because
    the family name aligns, ensuring style traits are strictly verified.
    """
    # Import programmatic font builders inline using the standard workspace path
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "fixtures")))
    from font_fixture_builder import SQUARE_500, build_truetype_bytes

    # Create dummy TrueType fonts
    bold_italic_bytes = build_truetype_bytes({"A": SQUARE_500}, font_name="NimbusSans-BoldItalic")

    font_dir = tmp_path / "urw-base35"
    font_dir.mkdir()

    # Write the bold italic file first so it is encountered first during directory walking
    bold_italic_file = font_dir / "NimbusSans-BoldItalic.otf"
    bold_italic_file.write_bytes(bold_italic_bytes)

    # Write the genuine regular file matching our request
    regular_bytes = build_truetype_bytes({"A": SQUARE_500}, font_name="NimbusSans-Regular")
    regular_file = font_dir / "NimbusSans-Regular.otf"
    regular_file.write_bytes(regular_bytes)

    # Mock the internal font names extractor to accurately simulate what fontTools
    # extracts from a REAL NimbusSans-BoldItalic font. Real fonts store the base
    # family name (ID 1, e.g. "Nimbus Sans") independently from the full/style name.
    # Our programmatic fixture builder hardcodes both to whatever font_name we pass,
    # which accidentally hid the bug!
    def mock_internal_names(filepath):
        name = Path(filepath).name
        if "BoldItalic" in name:
            return {"nimbussans", "nimbussansbolditalic", "nimbus-sans-bold-italic"}
        return {"nimbussans", "nimbussansregular", "nimbus-sans-regular"}

    # Patch standard directories to point strictly to our temporary workspace
    # AND disable fc-match so it doesn't accidentally resolve a real system font
    with (
        patch("pdftl.fonts.file_locator._get_font_directories", return_value=[str(font_dir)]),
        patch("pdftl.fonts.file_locator._resolve_linux_fontconfig", return_value=None),
        patch(
            "pdftl.fonts.file_locator._get_internal_font_names", side_effect=mock_internal_names
        ),
    ):
        # We search for 'Helvetica' (Regular). It must NOT return the BoldItalic file path.
        resolved_path = resolve_system_font_path("Helvetica")

        # It should correctly bypass NimbusSans-BoldItalic.otf and find NimbusSans-Regular.otf
        assert resolved_path is not None
        assert Path(resolved_path).name == "NimbusSans-Regular.otf"


# --- Coverage gap: _is_acceptable_alias substring-in-search-term branch (line 79) ---


def test_is_acceptable_alias_name_is_substring_of_search_term():
    """Covers the `name in search_term` True branch distinctly from base alias matches."""
    assert _is_acceptable_alias("helveticaneue", "helvetica") is True


# --- Coverage gap: design-variant blocklist continue branch (line 83) ---


def test_is_acceptable_alias_rejects_design_variant_residual():
    """
    A candidate name containing the search term as a substring must still be
    rejected if the leftover text matches a blocklisted decorative/special-purpose
    design marker (e.g. "garamondinitials" is not an acceptable match for
    "garamond" even though it contains it).
    """
    assert _is_acceptable_alias("garamond", "garamondinitials") is False
    assert _is_acceptable_alias("garamond", {"garamondinitials"}) is False


def test_is_acceptable_alias_rejects_design_variant_but_falls_through_to_other_name():
    """
    Ensures the blocklist `continue` doesn't short-circuit the whole function -
    if a later candidate name in the set is a clean match, it should still succeed.
    """
    assert _is_acceptable_alias("garamond", {"garamondinitials", "garamond"}) is True


# --- Coverage gap: name-table record nameID filtering (line 109->107) ---


def test_get_internal_font_names_skips_unwanted_name_ids():
    """
    Ensures records with nameID outside (1, 4, 6) - e.g. nameID=2 (subfamily,
    "Bold"/"Italic" style descriptor) - are iterated over but not collected.
    """
    mock_record_skipped = MagicMock()
    mock_record_skipped.nameID = 2
    mock_record_skipped.toUnicode.return_value = "Bold"

    mock_record_kept = MagicMock()
    mock_record_kept.nameID = 1
    mock_record_kept.toUnicode.return_value = "Good Font"

    mock_tt = MagicMock()
    mock_tt.__contains__.return_value = True
    mock_tt.__getitem__.return_value.names = [mock_record_skipped, mock_record_kept]
    mock_tt.__enter__.return_value = mock_tt

    with patch("fontTools.ttLib.TTFont", return_value=mock_tt):
        names = _get_internal_font_names("dummy.ttf")
        assert names == {"goodfont"}


# --- Coverage gap: fc-match resolves an acceptable name but bad file (line 166->172) ---


def test_resolve_linux_fontconfig_acceptable_name_but_bad_extension():
    """
    Covers the case where fc-match returns a family name that passes
    _is_acceptable_alias, but the file path doesn't end in a supported font
    extension - the function must fall through and return None rather than
    return the bogus path.
    """
    mock_res = MagicMock(stdout="/usr/share/fonts/arial.conf|Arial")
    with patch("sys.platform", "linux"), patch("subprocess.run", return_value=mock_res):
        assert _resolve_linux_fontconfig("Arial") is None


def test_resolve_linux_fontconfig_acceptable_name_but_missing_file():
    """
    Covers the case where fc-match returns an acceptable name and a
    correctly-extensioned path, but os.path.isfile is False (e.g. a stale
    fontconfig cache entry) - must fall through to return None.
    """
    mock_res = MagicMock(stdout="/usr/share/fonts/arial.ttf|Arial")
    with (
        patch("sys.platform", "linux"),
        patch("subprocess.run", return_value=mock_res),
        patch("os.path.isfile", return_value=False),
    ):
        assert _resolve_linux_fontconfig("Arial") is None


# --- Coverage gap: custom_dirs miss falls through to system resolution (line 232->236) ---


def test_resolve_system_font_path_custom_dirs_miss_falls_through():
    """
    When custom_dirs is provided but yields no match, execution must continue
    past the custom-dirs block into the normal system resolution path (fontconfig
    fast-path, then full directory crawl) rather than returning None early.
    """
    with (
        patch.object(file_locator, "_scan_system_font_dirs") as mock_scan,
        patch.object(
            file_locator, "_resolve_linux_fontconfig", return_value="/sys/font.ttf"
        ) as mock_fc,
    ):
        mock_scan.return_value = None  # custom dir scan finds nothing

        path = resolve_system_font_path("MyFont", custom_dirs=["/custom"])

        assert path == "/sys/font.ttf"
        mock_scan.assert_called_once_with("myfont", ["/custom"], None)
        mock_fc.assert_called_once_with("MyFont", None)


def test_resolve_linux_fontconfig_predicate_rejects_falls_through():
    """
    Covers branch 169->180: fc-match returns a name-acceptable, well-formed,
    existing font file, but the caller's predicate rejects it (e.g. wrong
    outline format) - must fall through to the final `return None` rather
    than returning the path, so resolve_system_font_path can try the
    directory crawl for an alternate.
    """
    mock_res = MagicMock(stdout="/usr/share/fonts/arial.ttf|Arial")
    with (
        patch("sys.platform", "linux"),
        patch("subprocess.run", return_value=mock_res),
        patch("os.path.isfile", return_value=True),
    ):
        result = _resolve_linux_fontconfig("Arial", predicate=lambda p: False)
        assert result is None


def test_resolve_linux_fontconfig_predicate_accepts_returns_path():
    """Sanity-check the accepting side of the same branch, for contrast."""
    mock_res = MagicMock(stdout="/usr/share/fonts/arial.ttf|Arial")
    with (
        patch("sys.platform", "linux"),
        patch("subprocess.run", return_value=mock_res),
        patch("os.path.isfile", return_value=True),
    ):
        result = _resolve_linux_fontconfig("Arial", predicate=lambda p: True)
        assert result == "/usr/share/fonts/arial.ttf"


def test_scan_system_font_dirs_predicate_rejects_first_accepts_second():
    with (
        patch.object(
            file_locator,
            "_iter_system_font_files",
            return_value=iter(
                [
                    ("/fonts/Bliss2R.otf", "Bliss2R.otf"),
                    ("/fonts/Bliss2-Regular.ttf", "Bliss2-Regular.ttf"),
                ]
            ),
        ),
        patch.object(file_locator, "_has_fonttools", return_value=False),
    ):
        result = _scan_system_font_dirs(
            "bliss2", ["/fonts"], predicate=lambda p: not p.endswith(".otf")
        )
    assert result == "/fonts/Bliss2-Regular.ttf"


def test_get_internal_font_names_handles_ttlib_error():
    """TTLibError (malformed/unsupported font) must be caught like the other parse errors."""
    from fontTools.ttLib import TTLibError

    with patch("fontTools.ttLib.TTFont", side_effect=TTLibError("bad font")):
        names = _get_internal_font_names("dummy.ttf")
        assert names == set()


def test_is_acceptable_alias_ordinary_suffix_not_blocked():
    """Sanity check: normal non-decorative suffixes aren't accidentally blocklisted."""
    assert _is_acceptable_alias("garamond", "garamondpro") is True
