# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/pdf_text/text_diff_calc.py

import re
from pdftl.utils.pdf_text.bboxes import merge_bounding_boxes
from pdftl.utils.pdf_text.global_stream_mapper import GlobalStreamMapper


def _tokenize_to_chars(text_a: str, text_b: str, regex_pattern: str) -> tuple[str, str, list[str]]:
    tokens_array = []
    tokens_hash = {}

    def encode(text: str) -> str:
        chars = []
        for token in re.split(regex_pattern, text):
            if token and token not in tokens_hash:
                tokens_hash[token] = chr(len(tokens_array))
                tokens_array.append(token)
            if token:
                chars.append(tokens_hash.get(token, ""))
        return "".join(chars)

    return encode(text_a), encode(text_b), tokens_array


def compute_diff_chunks(
    text_a: str, text_b: str, granularity: str, ignore_soft_hyphens: bool
) -> list[tuple[int, str]]:
    import diff_match_patch as dmp_module

    dmp = dmp_module.diff_match_patch()
    dmp.Diff_Timeout = 0

    if ignore_soft_hyphens:
        text_a = text_a.replace("\ufffe\n", "").replace("\ufffe", "")
        text_b = text_b.replace("\ufffe\n", "").replace("\ufffe", "")

    if granularity == "char":
        diff_chunks = dmp.diff_main(text_a, text_b)
    else:
        patterns = {"word": r"(\W+)", "line": r"(\n)", "paragraph": r"(\n\s*\n)"}
        pattern = patterns.get(granularity, r"(\W+)")
        encoded_a, encoded_b, tokens = _tokenize_to_chars(text_a, text_b, pattern)
        diff_chunks = dmp.diff_main(encoded_a, encoded_b, False)
        dmp.diff_charsToLines(diff_chunks, tokens)

    bridged = []
    for i, (op, text) in enumerate(diff_chunks):
        if (
            op == 0
            and not text.strip()
            and i > 0
            and i < len(diff_chunks) - 1
            and diff_chunks[i - 1][0] != 0
            and diff_chunks[i + 1][0] != 0
        ):
            bridged.append((-1, text))
            bridged.append((1, text))
        else:
            bridged.append((op, text))
    diff_chunks = bridged
    # No flattening step needed; diff_chunks is always a flat list of tuples

    dmp.diff_cleanupSemantic(diff_chunks)
    dmp.diff_cleanupMerge(diff_chunks)
    return diff_chunks


def _get_page_hit_data(
    mapper: GlobalStreamMapper, page_num: int, start: int, end: int, merge: bool
) -> dict | None:
    raw_text = mapper.global_text[page_num][start:end]
    clean_chars, bboxes = [], []
    has_margins = any(mapper.margins.values())
    w, h = mapper.page_sizes[page_num]

    for i, char in enumerate(raw_text):
        if char == "\n":
            clean_chars.append("\n")
            continue

        box = next(iter(mapper.tp.get_bboxes_for_lines(page_num, start + i, start + i + 1)), None)
        if box and has_margins and mapper._is_inside_margin(box, w, h):
            continue

        clean_chars.append(char)
        if box and (box[2] > box[0] and box[3] > box[1]):
            bboxes.append(box)

    text = "".join(clean_chars).strip(" \t\n\r")
    if not text:
        return None

    return {
        "page": page_num + 1,
        "text": text,
        "bboxes": merge_bounding_boxes(bboxes) if merge else bboxes,
    }


def _extract_hit_data(mapper: GlobalStreamMapper, start: int, end: int, merge: bool) -> list[dict]:
    spans = mapper.resolve_span(start, end)
    return [
        d for d in [_get_page_hit_data(mapper, p, l_s, l_e, merge) for p, l_s, l_e in spans] if d
    ]


def _build_change_entry(op: int, hits: list[dict], include_bboxes: bool) -> dict:
    """Builds the insert/delete entry payload and manages bbox stripping."""
    is_insert = op == 1
    entry = {
        "type": "insert" if is_insert else "delete",
        "file_a": None if is_insert else hits,
        "file_b": hits if is_insert else None,
    }
    if not include_bboxes:
        for h in hits:
            h.pop("bboxes", None)
    return entry


def process_diff_stream(
    diffs, mapper_a, mapper_b, ignore_whitespace, include_bboxes, merge_bboxes
) -> list[dict]:
    results = {"-1": [mapper_a, 0], "1": [mapper_b, 0]}
    output = []

    for op, text in diffs:
        chunk_len = len(text)
        if op == 0:
            results["-1"][1] += chunk_len
            results["1"][1] += chunk_len
            continue

        if not (ignore_whitespace and not text.strip()):
            mapper, start_idx = results[str(op)]
            hits = _extract_hit_data(mapper, start_idx, start_idx + chunk_len, merge_bboxes)
            if hits:
                entry = _build_change_entry(op, hits, include_bboxes)
                output.append(entry)

        results[str(op)][1] += chunk_len

    return output
