# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/__init__.py

"""Desktop GUI for building pdftl pipelines with per-stage page previews.

Requires the optional `gui` extra (PySide6-Essentials). Only the Qt-free
modules (`interfaces`, `keymap`, `pagesel`, `stage_model`, `engine`,
`op_policy`) may be imported without it.
"""
