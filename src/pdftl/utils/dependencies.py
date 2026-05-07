# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/dependencies.py

"""Utilities for handling dependencies"""

import importlib.util
import sys
from pathlib import Path

from pdftl.exceptions import InvalidArgumentError


def is_pipx_install() -> bool:
    """Guess if the current tool is running from a pipx environment."""
    # pipx usually installs to ~/.local/share/pipx/venvs/<package>
    return "pipx" in Path(sys.prefix).parts


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
        pip_cmd = f"pip install pdftl[{extra_tag}]"
        pipx_cmd = f"pipx inject pdftl pdftl[{extra_tag}]"
        cmd = pipx_cmd if is_pipx_install() else pip_cmd
        deps_str = " and ".join(missing)
        raise InvalidArgumentError(
            f"The '{feature_name}' feature requires {deps_str}.\nPlease install with: {cmd}"
        )
