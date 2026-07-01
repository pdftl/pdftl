#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Helper utility to convert a pdftl-exported font JSON sidecar mapping
into a FontForge-compatible .cidmap file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def convert_json_to_cidmap(json_path: Path, output_path: Path) -> None:
    """
    Reads the pdftl sidecar JSON file and writes out a .cidmap file.

    The FontForge .cidmap format specifies:
      - Line 1: "<total_cids> <total_cids>"
      - Subsequent lines: "<cid_decimal> <unicode_hex>"
    """
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading JSON file {json_path}: {e}")
        sys.exit(1)

    mappings = data.get("mappings", {})
    if not mappings:
        print(f"Warning: No 'mappings' key found in {json_path}. Is this a valid font sidecar?")
        sys.exit(1)

    cid_map: dict[int, str] = {}
    max_cid = 0

    # Parse and sort the CID mappings
    for hex_key, entry in mappings.items():
        # hex_key is a 4-digit hex representation of the CID (e.g. "000A" for CID 10)
        try:
            cid = int(hex_key, 16)
        except ValueError:
            continue

        unicode_str = entry.get("unicode", "")
        if not unicode_str:
            continue

        # Convert character to 4-digit uppercase/lowercase hex
        # FontForge cidmap matches are typically 4-digit lowercase hex
        unicode_hex = "".join(f"{ord(char):04x}" for char in unicode_str)
        cid_map[cid] = unicode_hex
        if cid > max_cid:
            max_cid = cid

    # Establish bounds (Line 1 expects total CIDs including CID 0)
    total_cids = max_cid + 1

    try:
        with open(output_path, "w", encoding="utf-8") as out:
            # First line of a .cidmap is the CID limit count
            out.write(f"{total_cids} {total_cids}\n")

            # Write CID 0 as /.notdef
            out.write("0 /.notdef\n")

            # Write mapped values sequentially
            for cid in sorted(cid_map.keys()):
                if cid == 0:
                    continue
                out.write(f"{cid} {cid_map[cid]}\n")

        print(f"Successfully generated FontForge .cidmap at: {output_path}")
    except Exception as e:
        print(f"Failed to write .cidmap file: {e}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python generate_cidmap.py <path_to_sidecar.json> [output_file.cidmap]")
        sys.exit(1)

    json_file = Path(sys.argv[1])
    if len(sys.argv) >= 3:
        output_file = Path(sys.argv[2])
    else:
        # Default to placing it alongside the JSON sidecar
        output_file = json_file.with_suffix(".cidmap")

    convert_json_to_cidmap(json_file, output_file)
