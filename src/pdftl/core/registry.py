# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/core/registry.py

"""Registry for various data, loaded at runtime"""

import inspect
import os
from collections import OrderedDict
from dataclasses import fields

from pdftl.core.types import Compatibility, HelpExample, HelpTopic, Operation, Option


def caller_dict(current_frame):
    """Helper to get caller ("source") data for registry decorators"""
    caller = current_frame.f_back
    return {"caller": os.path.basename(caller.f_code.co_filename)}


class Registry:
    """Registry for data which is loaded upon initialization"""

    def __init__(self):
        self.operations = OrderedDict()
        self.options = OrderedDict()
        self.help_topics = OrderedDict()

    def __getitem__(self, key):
        if key in ("operations", "options", "help_topics"):
            return getattr(self, key)
        raise KeyError(f"Unknown registry key: {key}")

    def __contains__(self, key):
        return key in ("operations", "options", "help_topics")

    def filter(self, main_key, sub_key, test, transform=None):
        """
        Helper to filter and transform registry entries.

        Iterates over the registry section specified by main_key.
        Checks if each entry has an attribute/key named sub_key that satisfies
        the test function. Returns a set of the matching keys (default) or
        transformed keys.
        """
        results = set()
        for key, item in self[main_key].items():
            val = None
            found = False

            # 1. Try Object attribute access (New Style)
            if hasattr(item, sub_key):
                val = getattr(item, sub_key)
                found = True
            # 2. Try Dictionary key access (Legacy/Test Style)
            elif isinstance(item, dict) and sub_key in item:
                val = item[sub_key]
                found = True

            if found and test(val):
                res = transform(key) if transform is not None else key
                results.add(res)
        return results

    def _create_and_extend(self, cls, fixed_args, metadata):
        """
        Create a dataclass instance safely, handling unknown metadata.

        1. Identifies fields that strictly belong to the dataclass `cls`.
        2. Separates `metadata` into `known` (constructor args) and `extra` (dynamic attrs).
        3. Instantiates `cls` with `fixed_args` + `known` metadata.
        4. Attaches `extra` metadata as dynamic attributes to the instance.

        This ensures strict type checking for known fields while preserving
        backward compatibility for ad-hoc metadata keys (e.g. 'category', 'type').
        """
        valid_fields = {f.name for f in fields(cls)}

        init_kwargs = {}
        extra_kwargs = {}

        for k, v in metadata.items():
            if k in valid_fields:
                init_kwargs[k] = v
            else:
                extra_kwargs[k] = v

        # fixed_args take precedence (e.g. name, function)
        init_kwargs.update(fixed_args)

        # Create strict object
        obj = cls(**init_kwargs)

        # Attach extra metadata dynamically so legacy dict access finds it
        for k, v in extra_kwargs.items():
            setattr(obj, k, v)

        return obj

    def register_operation(self, name, **metadata):
        """Decorator to register a command."""

        def decorator(func):

            if "examples" in metadata:
                safe_examples = [_to_help_example_helper(ex, name) for ex in metadata["examples"]]

                metadata["examples"] = safe_examples

            if "compatibility" in metadata:
                compat = metadata["compatibility"]
                if isinstance(compat, dict):
                    metadata["compatibility"] = Compatibility(**compat)

            fixed_args = {
                "name": name,
                "function": func,
                **caller_dict(inspect.currentframe()),
            }

            op = self._create_and_extend(Operation, fixed_args, metadata)
            registry.operations[name] = op
            return func  # needed to allow stacking decorators

        return decorator

    def register_option(self, name, **metadata):
        """Decorator to register an option."""

        def decorator(func):
            full_metadata = {
                **metadata,
                **caller_dict(inspect.currentframe()),
            }
            fixed_args = {"name": name, "handler": func}
            op = self._create_and_extend(Option, fixed_args, full_metadata)
            registry.options[name] = op
            return func  # needed to allow stacking decorators

        return decorator

    def register_help_topic(self, name, title, desc, examples=None):
        """
        Decorator to register a standalone help topic.
        Uses the decorated function's docstring as the `long_desc`.
        """
        if examples is None:
            examples = []

        safe_examples = []
        for ex in examples:
            if isinstance(ex, dict):
                safe_examples.append(HelpExample(**ex))
            elif isinstance(ex, HelpExample):
                safe_examples.append(ex)
            else:
                raise ValueError(f"Invalid example format in topic '{name}'")

        def decorator(func):
            long_desc = inspect.getdoc(func) or ""

            topic = HelpTopic(
                title=title,
                desc=desc,
                long_desc=long_desc,
                examples=safe_examples,
            )
            caller_details = caller_dict(inspect.currentframe())
            for k, v in caller_details.items():
                setattr(topic, k, v)

            registry.help_topics[name] = topic
            return func

        return decorator


def _to_help_example_helper(ex, name):
    if isinstance(ex, dict):
        return HelpExample(**ex)
    elif isinstance(ex, HelpExample):
        return ex
    else:
        raise ValueError(f"Invalid example format in operation '{name}'")


registry = Registry()


def register_operation(*args, **kwargs):
    """Register an operation in the global registry"""
    return registry.register_operation(*args, **kwargs)


def register_option(*args, **kwargs):
    """Register an option in the global registry"""
    return registry.register_option(*args, **kwargs)


def register_help_topic(*args, **kwargs):
    """Register a help topic in the global registry"""
    return registry.register_help_topic(*args, **kwargs)
