# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/types/move_types.py

"""Type definitions for the move command"""

from typing import NamedTuple


class MoveSpec(NamedTuple):
    source_spec: str
    mode: str  # 'before' or 'after'
    target_spec: str
