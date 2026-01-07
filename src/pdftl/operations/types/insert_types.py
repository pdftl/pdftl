# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/types/insert_types.py

"""Types for the insert operation"""


from typing import NamedTuple


class InsertSpec(NamedTuple):
    insert_count: int
    geometry_spec: str | None
    mode: str  # 'after' or 'before'
    target_page_spec: str
