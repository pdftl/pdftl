# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/dependencies.py

"""Utilities for handling dependencies"""

import importlib.util

from pdftl.exceptions import InvalidArgumentError


def ensure_dependencies(
    feature_name: str, dependencies: dict[str, str] | list[str] | set[str], extra_tag: str
):
    """
    Checks for multiple dependencies.

    Args:
        feature_name: Name of the pdftl command.
        dependencies: Dict of {module: display_name} or a list/set of module names.
        extra_tag: The pip install extra name (e.g., 'render').
    """
    if not isinstance(dependencies, dict):
        dependencies = {k: k for k in dependencies}

    missing = []
    for module, display in dependencies.items():
        if importlib.util.find_spec(module) is None:
            missing.append(display)

    if missing:
        deps_str = " and ".join(missing)
        raise InvalidArgumentError(
            f"The '{feature_name}' feature requires {deps_str}.\n"
            f"Please install with: pip install pdftl[{extra_tag}]"
        )
