# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/file_locator.py

"""
Utility module for locating font files (.ttf, .otf, .ttc, .pfb) on the host operating system.
"""

import logging
import os
import subprocess
import sys
from collections.abc import Iterator

logger = logging.getLogger(__name__)

_STANDARD_ALIASES = {
    "helvetica": ["arial", "nimbussans", "liberationsans", "texgyreheros", "freesans", "albany"],
    "times": [
        "timesnewroman",
        "nimbusroman",
        "liberationserif",
        "texgyretermes",
        "freeserif",
        "thorndale",
    ],
    "courier": [
        "couriernew",
        "nimbusmono",
        "liberationmono",
        "texgyrecursor",
        "freemono",
        "cumberland",
    ],
    "symbol": ["standardsymbols", "symbolneu", "opensymbol"],
    "zapfdingbats": ["dingbats", "urwdingbats", "d050000l"],
}


def _is_acceptable_alias(search_term: str, found_names: set[str] | str) -> bool:
    """
    Checks if a set of found font names (e.g. internal names + filename)
    is an acceptable match for the search term, accounting for standard
    metric-compatible aliases (e.g., Arial for Helvetica) and ensuring
    basic style traits (bold/italic) match across the entire font entity.
    """
    if isinstance(found_names, str):
        found_names = {found_names}

    is_bold_req = "bold" in search_term
    is_italic_req = "italic" in search_term or "oblique" in search_term

    font_is_bold = any("bold" in name for name in found_names)
    font_is_italic = any("italic" in name or "oblique" in name for name in found_names)

    if is_bold_req != font_is_bold or is_italic_req != font_is_italic:
        return False

    if any(search_term in name or name in search_term for name in found_names):
        return True

    for base, aliases in _STANDARD_ALIASES.items():
        if base in search_term:
            if any(alias in name for alias in aliases for name in found_names):
                return True

    return False


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
        if _is_acceptable_alias(search_term, {family_clean, path_clean}):
            if path.lower().endswith((".ttf", ".otf", ".ttc", ".pfb")) and os.path.isfile(path):
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


def _iter_system_font_files(dirs_to_scan: list[str]) -> Iterator[tuple[str, str]]:
    """Yields tuples of (absolute_path, filename) for all font files in the given directories."""
    for directory in dirs_to_scan:
        if not os.path.exists(directory):
            continue

        for root, _, files in os.walk(directory):
            for file in files:
                if file.lower().endswith((".ttf", ".otf", ".ttc", ".pfb")):
                    yield os.path.join(root, file), file


def _is_font_match(filepath: str, filename: str, search_term: str, use_fonttools: bool) -> bool:
    """Checks if a specific file matches the requested font search term."""
    clean_name = os.path.splitext(filename)[0].lower().replace(" ", "")
    found_names = {clean_name}

    if use_fonttools:
        found_names.update(_get_internal_font_names(filepath))

    return _is_acceptable_alias(search_term, found_names)


def _scan_system_font_dirs(search_term: str, dirs_to_scan: list[str]) -> str | None:
    """Scans specified directories to find a matching font file."""
    use_fonttools = _has_fonttools()

    for filepath, filename in _iter_system_font_files(dirs_to_scan):
        if _is_font_match(filepath, filename, search_term, use_fonttools):
            return filepath

    return None


def resolve_system_font_path(
    font_name: str, custom_dirs: list[str] | None = None, use_system: bool = True
) -> str | None:
    """
    Attempts to resolve a font name to an absolute file path across different OS.
    Prioritizes user-provided custom directories over system fonts.
    Returns None if the font cannot be found.
    """
    search_term = font_name.lower().replace(" ", "")

    # 1. Always prioritize custom user directories if provided
    if custom_dirs:
        custom_match = _scan_system_font_dirs(search_term, custom_dirs)
        if custom_match:
            return custom_match

    # 2. Halt if the user explicitly requested exclusion of host OS fonts
    if not use_system:
        return None

    # 3. Fast-path lookup for Linux
    fc_path = _resolve_linux_fontconfig(font_name)
    if fc_path:
        return fc_path

    # 4. Fallback exhaustive crawl of standard OS font directories
    return _scan_system_font_dirs(search_term, _get_font_directories())
