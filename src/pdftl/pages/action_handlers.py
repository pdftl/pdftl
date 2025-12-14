# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/pages/action_handlers.py
"""
Public API for handling PDF link actions.

This module provides the `ACTION_HANDLERS` dictionary to dispatch link
remapping tasks. It preserves the original API by acting as a compatibility
layer over the robust, class-based implementation in `link_remapper.py`.
"""


from pdftl.pages.link_remapper import LinkRemapper


def _handle_goto_action(remapper: LinkRemapper, original_action):
    """Handles GoTo actions by delegating to the LinkRemapper class."""
    return remapper.remap_goto_action(original_action)


def _handle_self_contained_action(remapper: LinkRemapper, original_action):
    """Handles self-contained actions (URI, etc.) by delegating to LinkRemapper."""
    return remapper.copy_self_contained_action(original_action)


def _handle_unsupported_action(remapper: LinkRemapper, original_action):
    """Fallback for complex actions by delegating to LinkRemapper."""
    return remapper.copy_unsupported_action(original_action)


# ACTION_HANDLERS provides the stable, public API for this module.
ACTION_HANDLERS = {
    "/GoTo": _handle_goto_action,
    "/GoToR": _handle_self_contained_action,
    "/Launch": _handle_self_contained_action,
    "/URI": _handle_self_contained_action,
    "/Sound": _handle_self_contained_action,
    "/JavaScript": _handle_unsupported_action,
    "/SubmitForm": _handle_unsupported_action,
    "/ResetForm": _handle_unsupported_action,
    "/ImportData": _handle_unsupported_action,
}

# public constant
# pylint: disable=invalid-name
DEFAULT_ACTION_HANDLER = _handle_unsupported_action
