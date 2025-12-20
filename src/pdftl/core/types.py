# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/core/types.py

"""
Shared type definitions for the pdftl core.
Contains dataclasses and structural schemas used by the registry.
"""

# pylint: disable=too-few-public-methods,too-many-instance-attributes

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FeatureType(str, Enum):
    """Category of the operation relative to pdftk."""

    # Aims for 1:1 behavior with a pdftk command (drop-in replacement).
    # This includes operations that are supersets (parity + enhancements).
    PDFTK_COMPAT = "pdftk_compat"

    # Functionality unique to pdftl (no direct pdftk equivalent).
    PDFTL_EXTENSION = "pdftl_extension"

    # Internal utilities, debug tools, or implementation details.
    INTERNAL = "internal"


class Status(str, Enum):
    """Current development lifecycle stage of the feature."""

    STABLE = "stable"  # Production ready, battle-tested
    BETA = "beta"  # Feature complete and usable, but needs battle-testing
    WIP = "wip"  # Active development, APIs may change
    UNTESTED = "untested"  # Logic implemented but lacks test coverage
    PLANNED = "planned"  # Placeholder for roadmap (not yet implemented)
    DEPRECATED = "deprecated"  # Still exists but should not be used


class LegacyDictAccess:
    """Mixin to provide dictionary-style access to object attributes."""

    def __getitem__(self, key):
        try:
            return getattr(self, key)
        except AttributeError as e:
            raise KeyError(key) from e

    def __contains__(self, key):
        """Support 'key in obj' checks to prevent fallback sequence iteration."""
        return hasattr(self, key)

    def get(self, key, default=None):
        """Safe dictionary-style get."""
        return getattr(self, key, default)


class Parity(str, Enum):
    # Indication of feature completeness vs pdftk (e.g. "100%", "Partial").
    FULL = "full"  # Identical behavior
    SUPERSET = "superset"  # Identical behavior + extra arguments
    PARTIAL = "partial"  # Implemented but missing edge cases/flags
    UNCLEAR = "unclear"  # IDK
    NONE = "none"  # Not applicable (pure extension)


@dataclass
class Compatibility(LegacyDictAccess):
    """
    Structured metadata for feature compatibility tracking.
    """

    type: str  # Use FeatureType constants
    status: str  # Use Status constants
    description: str = ""  # Short summary for the feature matrix

    parity: Parity = Parity.UNCLEAR

    # Specific enhancements over the original pdftk command.
    enhancements: list[str] = field(default_factory=list)

    # Technical context, known issues, or parity gaps.
    notes: str = ""

    # Equivalent pdftk command name.
    # - "cat": Explicit mapping to a named operation
    # - "": Maps to the empty/filter operation
    # - None: No pdftk equivalent (pure extension)
    pdftk_op: str | None = None

    todo: list[str] = field(default_factory=list)  # Future plans


@dataclass
class HelpExample(LegacyDictAccess):
    """A command usage example for help documentation."""

    cmd: str
    desc: str


@dataclass
class HelpTopic(LegacyDictAccess):
    """
    A standalone help topic (e.g. 'pipeline', 'page_specs').
    """

    title: str
    desc: str  # Short description
    long_desc: str  # Full documentation (usually from docstring)
    examples: list[HelpExample] = field(default_factory=list)


@dataclass
class Operation(LegacyDictAccess):
    """
    Registry entry for a CLI operation (e.g., 'cat', 'shuffle').
    """

    name: str
    function: Callable
    caller: str  # Filename where the operation is defined
    desc: str = ""  # Short description for list
    usage: str = ""  # Usage syntax string
    long_desc: str = ""  # Detailed help text
    tags: list[str] = field(default_factory=list)
    examples: list[HelpExample] = field(default_factory=list)
    args: Any = None  # Complex argument spec (tuple structure)
    compatibility: Compatibility | None = None


@dataclass
class Option(LegacyDictAccess):
    """
    Registry entry for a CLI option (e.g., 'verbose', 'compress').
    """

    name: str
    handler: Callable
    desc: str = ""
    long_desc: str = ""
    type: Any = None  # usage hint (str) or python type (type)
    compatibility: Compatibility | None = None
