# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/core/registry.py

"""Registry for various data, loaded at runtime"""

import inspect
import os
from collections import OrderedDict


class Registry:
    """Registry for data which is loaded upon initialization"""

    def __init__(self):
        self.operations = OrderedDict()
        self.options = OrderedDict()

    def __getitem__(self, key):
        if key in ("operations", "options"):
            return getattr(self, key)
        raise KeyError(f"Unknown registry key: {key}")

    def __contains__(self, key):
        return key in ("operations", "options")

    def filter(self, main_key, sub_key, test, transform=None):
        """Helper to filter and transform registry entries."""
        return {
            transform(x) if transform is not None else x
            for x, y in self[main_key].items()
            if sub_key in y and test(y[sub_key])
        }

    def register_operation(self, name, **metadata):
        """Decorator to register a command."""

        def decorator(func):
            caller = inspect.currentframe().f_back
            registry.operations[name] = {
                "caller": os.path.basename(caller.f_code.co_filename),
                "function": func,
                **metadata,
            }
            return func  # needed to allow stacking decorators

        return decorator

    def register_option(self, name, **metadata):
        """Decorator to register an option."""

        def decorator(func):
            registry.options[name] = {"handler": func, **metadata}
            return func  # needed to allow stacking decorators

        return decorator


registry = Registry()


def register_operation(*args, **kwargs):
    """Register an operation in the global registry"""
    return registry.register_operation(*args, **kwargs)


def register_option(*args, **kwargs):
    """Register an option in the global registry"""
    return registry.register_option(*args, **kwargs)
