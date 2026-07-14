# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/core/metadata.py

import re
from pathlib import Path


def _parse_changelog_version() -> tuple[str, bool] | tuple[None, None]:
    """Climbs the directory tree to find CHANGELOG.md and parses the
    latest semantic version header from it.

    Returns:
        A tuple of (version_string, is_unreleased). Both are None if not found.
    """
    start_path = Path(__file__).resolve()
    # Search the directory of this file and up to 4 parents
    for parent in [start_path] + list(start_path.parents)[:4]:
        candidate = parent / "CHANGELOG.md"
        if not candidate.is_file():
            continue

        try:
            with open(candidate, encoding="utf-8") as f:
                lines = f.readlines()
        except (OSError, ValueError):
            continue

        is_unreleased = False
        for line in lines:
            stripped = line.strip()

            # 1. Look for unreleased markers (e.g., "## [Unreleased]" or "## Unreleased")
            if re.search(r"^#+[^A-Za-z]*Unreleased", stripped, re.IGNORECASE):
                is_unreleased = True
                continue

            # 2. Look for headers containing a version: e.g., "## [1.2.0]" or "## v1.2.0-beta"
            match = re.search(r"^#+\s*\[?v?(\d+\.\d+\.\d+[^\]\s\)]*)", stripped)
            if match:
                version_str = match.group(1).strip()
                return version_str, is_unreleased

    return None, None


def _get_metadata_version() -> str | None:
    """Tries standard package metadata, then the generated local _version.py fallback."""
    import importlib.metadata

    try:
        return importlib.metadata.version("pdftl")
    except importlib.metadata.PackageNotFoundError:
        pass

    try:
        from pdftl._version import version

        return version
    except ImportError:
        return None


def _should_fallback_to_changelog(version_str: str | None) -> bool:
    """Evaluates if the parsed version should trigger a high-fidelity changelog fallback."""
    if not version_str:
        return True
    if "dirty" in version_str or "post" in version_str:
        return True
    if version_str.startswith("0.0."):
        return True
    return False


def _build_changelog_fallback_version(
    changelog_ver: str, is_unreleased: bool, is_dirty: bool
) -> str:
    """Constructs a PEP 440-compliant local version label showing fallback provenance."""
    local_labels = []

    if is_unreleased:
        base = f"{changelog_ver}.post0.dev0"
        local_labels.append("unreleased")
    else:
        base = changelog_ver

    local_labels.append("changelog")

    if is_dirty:
        local_labels.append("dirty")

    # Combine public version identifier with the local version label
    local_str = ".".join(local_labels)
    return f"{base}+{local_str}"


def get_project_version() -> str:
    """Gets the project version with high-fidelity fallbacks and clean error boundaries.
    Ensures complexity matches strict static analysis rules.
    """
    metadata_version = _get_metadata_version()

    if not _should_fallback_to_changelog(metadata_version):
        return metadata_version or "unknown-dev-version"

    changelog_ver, is_unreleased = _parse_changelog_version()
    if not changelog_ver:
        return metadata_version or "unknown-dev-version"

    is_dirty = bool(metadata_version and "dirty" in metadata_version)
    return _build_changelog_fallback_version(changelog_ver, is_unreleased, is_dirty)


def get_dependencies_status():
    import importlib.metadata
    from packaging.requirements import Requirement

    try:
        raw_reqs = importlib.metadata.requires("pdftl") or []
    except importlib.metadata.PackageNotFoundError:
        return [], []

    required_pkgs = set()
    optional_pkgs = set()

    # We will ignore dependencies that ONLY exist for these extras
    IGNORED_GROUPS = {"dev", "docs", "test", "dev-all"}

    for req_str in raw_reqs:
        req = Requirement(req_str)

        # 1. Ignore self-references
        if req.name == "pdftl":
            continue

        # 2. Must be optional
        if not req.marker:
            required_pkgs.add(req.name)
            continue

        # 3. FILTER: Check if this requirement belongs to an "ignored" extra.
        marker_str = str(req.marker)
        is_dev_tool = any(
            f"extra == '{g}'" in marker_str or f'extra == "{g}"' in marker_str
            for g in IGNORED_GROUPS
        )

        if is_dev_tool:
            continue

        optional_pkgs.add(req.name)

    return _get_status(required_pkgs), _get_status(optional_pkgs)


def _get_status(pkgs):
    import importlib.metadata

    # Check status of each identified package
    results = []
    for pkg in sorted(pkgs):
        try:
            ver = importlib.metadata.version(pkg)
            results.append((pkg, ver))
        except importlib.metadata.PackageNotFoundError:
            results.append((pkg, None))
    return results
