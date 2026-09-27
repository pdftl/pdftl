# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/mrc/__init__.py

"""Mixed Raster Content (ITU-T T.44) compression support."""


class MrcError(Exception):
    """An MRC step that could not produce a verified result."""
