# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/file_locator.py

"""
Utility module for locating font files (.ttf, .otf, .ttc) on the host operating system.
"""

import logging
import os
import subprocess
import sys
from collections.abc import Iterator

logger = logging.getLogger(__name__)


def _get_internal_font_names(filepath: str) -> set[str]:
    """Uses fontTools to read the internal names of a font file."""
    names = set()
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return names

    try:
        # lazy=True prevents loading the entire font into memory
        with TTFont(filepath, fontNumber=0, lazy=True) as tt:
            if "name" not in tt:
                return names
            for record in tt["name"].names:
                # 1: Family Name, 4: Full Name, 6: PostScript Name
                if record.nameID in (1, 4, 6):
                    try:
                        decoded = record.toUnicode()
                        names.add(decoded.lower().replace(" ", ""))
                    except UnicodeError:
                        continue
    except (OSError, KeyError, AttributeError, ValueError, TypeError) as e:
        logger.debug("FontTools failed to read %s: %s", filepath, e)

    return names


def _get_font_directories() -> list[str]:
    """Returns standard system font directories based on the OS."""
    if sys.platform == "win32":
        windir = os.environ.get("WINDIR", "C:\\Windows")
        return [
            os.path.join(windir, "Fonts"),
            os.path.join(windir, "Local\\Microsoft\\Windows\\Fonts"),
        ]

    if sys.platform == "darwin":
        return ["/Library/Fonts", "/System/Library/Fonts", os.path.expanduser("~/Library/Fonts")]

    # Fallback / Linux directories
    return [
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        os.path.expanduser("~/.fonts"),
        os.path.expanduser("~/.local/share/fonts"),
    ]


def _resolve_linux_fontconfig(font_name: str) -> str | None:
    """Attempts to use Linux's fc-match to find a font accurately."""
    if not sys.platform.startswith("linux"):
        return None

    try:
        result = subprocess.run(
            ["fc-match", font_name, "-f", "%{file}|%{family}"],
            capture_output=True,
            text=True,
            check=True,
        )
        output = result.stdout.strip()
        if not output or "|" not in output:
            return None

        path, family = output.split("|", 1)

        search_term = font_name.lower().replace(" ", "")
        family_clean = family.lower().replace(" ", "")
        path_clean = os.path.basename(path).lower().replace(" ", "")

        # Verify the OS didn't just hand us a generic fallback like "Noto Sans"
        if search_term in family_clean or search_term in path_clean:
            if path.lower().endswith((".ttf", ".otf")) and os.path.isfile(path):
                return path

    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        pass

    return None


def _has_fonttools() -> bool:
    """Safely checks if the optional fontTools dependency is available."""
    try:
        import fontTools  # noqa: F401

        return True
    except ImportError:
        return False


def _iter_system_font_files() -> Iterator[tuple[str, str]]:
    """Yields tuples of (absolute_path, filename) for all system fonts."""
    for directory in _get_font_directories():
        if not os.path.exists(directory):
            continue

        for root, _, files in os.walk(directory):
            for file in files:
                if file.lower().endswith((".ttf", ".otf", ".ttc")):
                    yield os.path.join(root, file), file


def _is_font_match(filepath: str, filename: str, search_term: str, use_fonttools: bool) -> bool:
    """Checks if a specific file matches the requested font search term."""
    if use_fonttools:
        internal_names = _get_internal_font_names(filepath)
        return search_term in internal_names

    clean_name = os.path.splitext(filename)[0].lower().replace(" ", "")
    return search_term in clean_name


def _scan_system_font_dirs(search_term: str) -> str | None:
    """Scans system directories to find a matching font file."""
    use_fonttools = _has_fonttools()

    for filepath, filename in _iter_system_font_files():
        if _is_font_match(filepath, filename, search_term, use_fonttools):
            return filepath

    return None


def resolve_system_font_path(font_name: str) -> str | None:
    """
    Attempts to resolve a font name to an absolute file path across different OS.
    Returns None if the font cannot be found.
    """
    fc_path = _resolve_linux_fontconfig(font_name)
    if fc_path:
        return fc_path

    search_term = font_name.lower().replace(" ", "")
    return _scan_system_font_dirs(search_term)
