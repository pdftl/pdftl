# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/registry_init.py

"""
Single entry point for initializing the application registry.
This function populates registry options and discovers all commands.
"""

import importlib
import logging
import pkgutil

import pdftl

logger = logging.getLogger(__name__)


def _discover_modules(parent_modules, label):
    """
    Import submodules.

    This ensures that decorators are executed,
    so the global registry is fully populated before use.
    """
    loaded_modules = []
    for pkg in parent_modules:
        # iter_modules requires the path to be iterable (a list)
        path = getattr(pkg, "__path__", None)
        if path is None:
            logger.warning("Skipping discovery for %s (no __path__)", pkg.__name__)
            continue

        for _, module_name, _ in pkgutil.iter_modules(path):
            fq_name = f"{pkg.__name__}.{module_name}"
            importlib.import_module(fq_name)
            loaded_modules.append(fq_name)

    logger.debug("[registry_init] Loaded %s %s modules:", len(loaded_modules), label)
    for module in loaded_modules:
        logger.debug("  - %s", module)

    return loaded_modules


def initialize_registry():
    """
    Initialize the entire application registry.

    This function is idempotent (safe to call multiple times).
    It populates static options and discovers all commands/operations.
    """

    if getattr(initialize_registry, "initialized", False):
        return

    # 1. Import the packages to be discovered
    # We must explicitly import 'utils' and 'cli' so their submodules (like
    # page_specs.py and pipeline.py) can be discovered and their decorators executed.
    for module in ["commands", "core", "output", "cli", "utils"]:
        importlib.import_module(f"pdftl.{module}")

    # 2. Discover and register all commands and help topics
    # We scan all relevant packages to find @register_operation and @register_help_topic
    _discover_modules([pdftl.commands, pdftl.core, pdftl.cli, pdftl.utils], "operation")
    _discover_modules([pdftl.output], "option")

    initialize_registry.initialized = True
