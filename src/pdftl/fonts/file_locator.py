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
from collections.abc import Callable, Iterator

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

# Suffixes/fragments that indicate a font is a distinct decorative or
# special-purpose design rather than a weight/style variant of the
# requested family. A substring match against these residuals (e.g.
# "garamond" matching inside "garamondinitials") must be rejected even
# though the base name is contained within it.
_DESIGN_VARIANT_BLOCKLIST = {
    "initials",
    "caps",
    "titling",
    "swash",
    "ornaments",
    "ornament",
    "expert",
    "alternate",
    "alt",
    "stencil",
    "outline",
    "shadow",
    "decorative",
    "engraved",
    "fraktur",
    "text",
    "display",
    "poster",
    "sc",  # small caps abbreviation
}


def _style_traits_match(search_term: str, found_names: set[str]) -> bool:
    """
    Ensures bold/italic style traits implied by the search term match
    those implied by the candidate's names.
    """
    is_bold_req = "bold" in search_term
    is_italic_req = "italic" in search_term or "oblique" in search_term

    font_is_bold = any("bold" in name for name in found_names)
    font_is_italic = any("italic" in name or "oblique" in name for name in found_names)

    return is_bold_req == font_is_bold and is_italic_req == font_is_italic


def _is_direct_name_match(search_term: str, name: str) -> bool:
    """
    Checks whether a single candidate name is an acceptable direct (non-alias)
    match for the search term, applying the design-variant blocklist to
    substring matches where the candidate name is more specific.
    """
    if search_term == name or name in search_term:
        return True
    if search_term in name:
        residual = name.replace(search_term, "", 1)
        return not any(marker in residual for marker in _DESIGN_VARIANT_BLOCKLIST)
    return False


def _matches_standard_alias(search_term: str, found_names: set[str]) -> bool:
    """Checks whether the search term matches via a known metric-compatible alias."""
    for base, aliases in _STANDARD_ALIASES.items():
        if base in search_term and any(alias in name for alias in aliases for name in found_names):
            return True
    return False


def _is_acceptable_alias(search_term: str, found_names: set[str] | str) -> bool:
    """
    Checks if a set of found font names (e.g. internal names + filename)
    is an acceptable match for the search term, accounting for standard
    metric-compatible aliases (e.g., Arial for Helvetica) and ensuring
    basic style traits (bold/italic) match across the entire font entity.
    """
    if isinstance(found_names, str):
        found_names = {found_names}

    if not _style_traits_match(search_term, found_names):
        return False

    if any(_is_direct_name_match(search_term, name) for name in found_names):
        return True

    return _matches_standard_alias(search_term, found_names)


def _get_internal_font_names(filepath: str) -> set[str]:
    """Uses fontTools to read the internal names of a font file."""
    names = set()
    try:
        from fontTools.ttLib import TTFont, TTLibError
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
    except (OSError, KeyError, AttributeError, ValueError, TypeError, TTLibError) as e:
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


def _parse_fc_match_output(output: str) -> tuple[str, str] | None:
    """Splits fc-match's `%{file}|%{family}` output into (path, family)."""
    if not output or "|" not in output:
        return None
    path, family = output.split("|", 1)
    return path, family


def _fc_candidate_path(
    font_name: str, path: str, family: str, predicate: Callable[[str], bool] | None
) -> str | None:
    """
    Validates a single fc-match candidate: name-acceptable, well-formed
    font extension, existing file, and (if given) predicate-acceptable.
    Returns the path if all checks pass, else None.
    """
    search_term = font_name.lower().replace(" ", "")
    family_clean = family.lower().replace(" ", "")
    path_clean = os.path.basename(path).lower().replace(" ", "")

    if not _is_acceptable_alias(search_term, {family_clean, path_clean}):
        return None
    if not path.lower().endswith((".ttf", ".otf", ".ttc", ".pfb")) or not os.path.isfile(path):
        return None
    if predicate is not None and not predicate(path):
        # fc-match's single best guess didn't satisfy the caller's
        # requirements (e.g. wrong outline format) - let the caller fall
        # through to the full directory crawl for an alternate match,
        # rather than giving up on the name entirely.
        return None
    return path


def _resolve_linux_fontconfig(
    font_name: str, predicate: Callable[[str], bool] | None = None
) -> str | None:
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
        parsed = _parse_fc_match_output(result.stdout.strip())
        if parsed is None:
            return None
        path, family = parsed
        return _fc_candidate_path(font_name, path, family, predicate)
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        return None  # locator command unavailable or failed


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


def _is_font_match(
    filepath: str,
    filename: str,
    search_term: str,
    use_fonttools: bool,
    predicate: Callable[[str], bool] | None = None,
) -> bool:
    """Checks if a specific file matches the requested font search term."""
    clean_name = os.path.splitext(filename)[0].lower().replace(" ", "")
    found_names = {clean_name}

    if use_fonttools:
        found_names.update(_get_internal_font_names(filepath))

    if not _is_acceptable_alias(search_term, found_names):
        return False
    return predicate is None or predicate(filepath)


def _scan_system_font_dirs(
    search_term: str,
    dirs_to_scan: list[str],
    predicate: Callable[[str], bool] | None = None,
) -> str | None:
    """Scans specified directories to find a matching font file."""
    use_fonttools = _has_fonttools()

    for filepath, filename in _iter_system_font_files(dirs_to_scan):
        if _is_font_match(filepath, filename, search_term, use_fonttools, predicate):
            return filepath

    return None


def resolve_system_font_path(
    font_name: str,
    custom_dirs: list[str] | None = None,
    use_system: bool = True,
    predicate: Callable[[str], bool] | None = None,
) -> str | None:
    """
    Attempts to resolve a font name to an absolute file path across different OS.
    Prioritizes user-provided custom directories over system fonts.
    If `predicate` is given, only paths for which `predicate(path)` is True
    are considered acceptable matches; the search keeps going past any
    name-matching-but-rejected candidate (whether surfaced by fc-match or
    found during the directory crawl) rather than stopping at the first
    name match.
    Returns None if the font cannot be found.
    """
    search_term = font_name.lower().replace(" ", "")

    # 1. Always prioritize custom user directories if provided
    if custom_dirs:
        custom_match = _scan_system_font_dirs(search_term, custom_dirs, predicate)
        if custom_match:
            return custom_match

    # 2. Halt if the user explicitly requested exclusion of host OS fonts
    if not use_system:
        return None

    # 3. Fast-path lookup for Linux
    fc_path = _resolve_linux_fontconfig(font_name, predicate)
    if fc_path:
        return fc_path

    # 4. Fallback exhaustive crawl of standard OS font directories
    return _scan_system_font_dirs(search_term, _get_font_directories(), predicate)
