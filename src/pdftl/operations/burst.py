# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/burst.py

"""Burst a PDF file into individual pages"""

import io
import logging

import pdftl.api
import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.outline_select import get_outlines_to_level_pages
from pdftl.utils.page_specs import page_numbers_matching_page_specs

logger = logging.getLogger(__name__)

_BURST_LONG_DESC = """

The `burst` operation splits a single input PDF into multiple
single-page PDF files, or into multiple files containing consecutive
runs of pages with given split points, or split points based on
bookmarks and/or a file size limit.

An optional output template can be provided.

`split_spec` is an optional page specification, giving the 'split
points', i.e.,, the initial page of each split chunk. The list of
split points will be sorted and deduplicated before it is used, so
order is irrelevant here. If omitted, burst defaults to splitting into
single pages (equivalent to `split_spec` being `1-end`).

You can also use `level<n>` as a `split_spec`, where `<n>` is a
positive integer, to choose all bookmarks (a.k.a. outlines) at level
up to n as split points. Similarly, `level<n>only` splits using only
bookmarks at level `<n>`.

What is a bookmark level? The highest level of the bookmark heirarchy
is level 1, and this is the level of the root of the bookmark tree and
its siblings. Children of these bookmark items have level 2, and so
on.

You can also specify `size<limit>` as one `split_spec` to burst the
PDF into chunks that do not exceed a given file size, at least
approximately, where possible. The file size limit can be specified in
bytes, kilobytes (K/KB), or megabytes (M/MB). For example, `size5M` or
`size500K`.  Size bursting can be combined with standard split points,
in which case chunks may be sub-divided to fit into the given size
limit.


"""

_BURST_EXAMPLES = [
    {
        "cmd": "my.pdf burst",
        "desc": "Burst a file into page_1.pdf, page_2.pdf, etc.",
    },
    {
        "cmd": "my.pdf burst output out%04d.pdf",
        "desc": "Burst a file into single-page files out0001.pdf, out0002.pdf, etc.",
    },
    {
        "cmd": "my.pdf burst level2 output out%04d.pdf",
        "desc": "Burst a file into files with split points from the bookmarks at levels 1 and 2",
    },
    {
        "cmd": "my.pdf burst level2only output out%04d.pdf",
        "desc": "Burst a file into files with split points from the bookmarks at level 2 only",
    },
    {
        "cmd": "my.pdf burst step3 output out%04d.pdf",
        "desc": (
            "Burst a file into chunks out0001.pdf with pages 1-3, "
            "out0002.pdf with pages 4-6, etc."
        ),
    },
    {
        "cmd": "my.pdf burst size5M output chunk%02d.pdf",
        "desc": "Burst a file into chunks that are approximately 5 Megabytes or smaller.",
    },
    {
        "cmd": "my.pdf burst step3 size250kb output out%04d.pdf",
        "desc": (
            "Burst a file into chunks with pages 1-3, "
            "4-6, etc., subdividing as needed to make files of size at most 250kb"
        ),
    },
]


def burst_cli_hook(result: OpResult, stage, pipeline):
    """
    CLI-specific side effect: Writes the burst pages to disk.
    This function is only called by the CLI pipeline.
    """
    # The generator yields (filename, pil_image) tuples

    burst_generator = result.data

    if not burst_generator:
        logger.debug("No burst_generator")
        return

    logger.info("Bursting pages to disk...")
    count = 0
    for filename, pdf in burst_generator:
        pipeline.save_pdf_file(pdf, filename, stage)
        pdf.close()
        count += 1

    logger.info("Burst to %s files.", count)
    pdftl.api.dump_data(result.pdf, output="doc_data.txt", run_cli_hook=True)


@register_operation(
    "burst",
    tags=["from_scratch"],
    type="single input operation with optional output",
    desc="Split a single PDF into multiple files",
    long_desc=_BURST_LONG_DESC,
    examples=_BURST_EXAMPLES,
    usage="<input> burst [split_spec...] [output <template>]",
    args=(
        [c.OPENED_PDFS, c.OPERATION_ARGS],
        {
            c.OUTPUT_PATTERN: c.OUTPUT_PATTERN,
        },
    ),
    cli_hook=burst_cli_hook,
    skip_pipeline_save=True,
)
def burst_pdf(opened_pdfs, operation_args=None, output_pattern="pg_%04d.pdf") -> OpResult:
    """Split one or more PDFs into multiple files,
    single-page files by default.

    Args:
        opened_pdfs (list): A list of opened PDF files to burst

        operation_args (list): User-supplied arguments

        output_pattern (str): A C-style format string for the output
                              filenames, e.g., "page_%03d.pdf".

    Return: the first input pdf (for pipeline chainability)


    Note: Uses the hook side-effect to actually burst

    Bugs:

    * Discards various parts of the PDF file that may still be
      relevant to single-page files, e.g., internal links

    """
    specs = operation_args or []

    size_limit_bytes = None
    standard_specs = []

    # Separate the size spec from the page/level specs
    for spec in specs:
        if spec.lower().startswith("size"):
            if size_limit_bytes is not None:
                raise InvalidArgumentError("More than one `size` spec passed to `burst`")
            size_limit_bytes = _parse_size_to_bytes(spec[4:])
        else:
            standard_specs.append(spec)

    # If the user only passed a size (e.g., 'burst size5M'), default the primary chunks to 1-end
    if not standard_specs:
        standard_specs = ["1-end"] if size_limit_bytes is None else ["1"]

    generator = _generate_burst_chunks(
        opened_pdfs, standard_specs, output_pattern, max_bytes=size_limit_bytes
    )

    return OpResult(
        success=True,
        data=generator,
        pdf=opened_pdfs[0],  # for subsequent pipeline/dump_data rather than for saving
    )


def _make_chunk_pdf(pages, start_idx, end_idx):
    """Create a new PDF containing pages[start_idx:end_idx+1]."""
    import pikepdf

    new_pdf = pikepdf.Pdf.new()
    new_pdf.pages.extend(pages[start_idx : end_idx + 1])
    return new_pdf


def _find_max_fitting_end(source_pdf, start_idx, end_idx, max_bytes):
    """Binary search for the last page index that keeps the chunk under max_bytes.
    Returns start_idx if even a single page exceeds the limit."""
    low, high, best_end = start_idx, end_idx, start_idx
    while low <= high:
        mid = (low + high) // 2
        if get_chunk_size(source_pdf, start_idx, mid) <= max_bytes:
            best_end = mid
            low = mid + 1
        else:
            high = mid - 1
    return best_end


def _warn_if_oversized(source_pdf, page_idx, max_bytes):
    """Log a warning if a single page exceeds the size limit."""
    size = get_chunk_size(source_pdf, page_idx, page_idx)
    if size > max_bytes:
        logger.warning(
            "Page %d (%d bytes) exceeds the maximum limit of %d bytes. Yielding as-is.",
            page_idx + 1,
            size,
            max_bytes,
        )


def _yield_size_constrained_chunks(
    source_pdf, pages, chunk_start, chunk_end, pattern, chunk_counter, max_bytes
):
    """Yield one or more (filename, pdf) pairs from a page range, split to respect max_bytes."""
    current_start = chunk_start
    while current_start <= chunk_end:
        best_end = _find_max_fitting_end(source_pdf, current_start, chunk_end, max_bytes)
        if best_end == current_start:
            _warn_if_oversized(source_pdf, current_start, max_bytes)
        page_file = pattern % chunk_counter
        yield page_file, _make_chunk_pdf(pages, current_start, best_end)
        chunk_counter += 1
        current_start = best_end + 1
    return chunk_counter


def _iter_chunks(source_pdf, specs, pattern, chunk_counter, max_bytes):
    """Yield all (filename, pdf) pairs for one source PDF."""
    pages = source_pdf.pages
    effective_specs = get_effective_specs(source_pdf, specs)
    split_points = sorted(set(page_numbers_matching_page_specs(effective_specs, len(pages))))
    logger.debug("source_pdf=%s split_points=%s", source_pdf, split_points)

    previous_page_num = 1
    for page_num in [*split_points, len(pages) + 1]:
        chunk_start = previous_page_num - 1
        chunk_end = page_num - 2
        previous_page_num = page_num

        if chunk_start > chunk_end:
            logger.debug("Empty chunk: %s to %s", previous_page_num, page_num)
            continue

        if max_bytes is None:
            yield pattern % chunk_counter, _make_chunk_pdf(pages, chunk_start, chunk_end)
            chunk_counter += 1
        else:
            for item in _yield_size_constrained_chunks(
                source_pdf, pages, chunk_start, chunk_end, pattern, chunk_counter, max_bytes
            ):
                yield item
                chunk_counter += 1

    return chunk_counter


def _generate_burst_chunks(opened_pdfs, specs, output_pattern, max_bytes=None):
    pattern = output_pattern or "pg_%04d.pdf"
    if "%" not in pattern:
        raise InvalidArgumentError("Output pattern must include a format specifier (e.g., %d)")

    chunk_counter = 1
    try:
        for source_pdf in opened_pdfs:
            for item in _iter_chunks(source_pdf, specs, pattern, chunk_counter, max_bytes):
                yield item
                chunk_counter += 1
    finally:
        for source_pdf in opened_pdfs:
            source_pdf.close()


def get_effective_specs(source_pdf, specs):
    effective_specs = specs
    for i, spec in enumerate(effective_specs):
        if spec.lower().startswith("level"):
            spec = spec[len("level") :]
            try:
                eq = spec.lower().endswith("only")
                if eq:
                    spec = spec[: -len("only")]
                level = int(spec)
                if level <= 0:
                    raise ValueError("must be at least 1")
            except ValueError as exc:
                raise InvalidArgumentError(f"Invalid bookmark level '{spec}': {exc}")
            effective_specs[i] = ",".join(
                [
                    str(x)
                    for x in get_outlines_to_level_pages(source_pdf, level, last_level_only=eq)
                ]
            )
    return effective_specs


def _parse_size_to_bytes(size_str: str) -> int:
    """Converts a size string like '5M', '500K', or '1048576' to bytes."""
    size_str = size_str.strip().upper()
    try:
        if size_str.endswith("MB") or size_str.endswith("M"):
            val = float(size_str.replace("MB", "").replace("M", ""))
            return int(val * 1024 * 1024)
        elif size_str.endswith("KB") or size_str.endswith("K"):
            val = float(size_str.replace("KB", "").replace("K", ""))
            return int(val * 1024)
        else:
            return int(size_str)
    except ValueError:
        raise InvalidArgumentError(
            f"Invalid size format: '{size_str}'. Use format like 5M or 500K."
        )


def get_chunk_size(src_pdf, start_idx: int, end_idx: int) -> int:
    import pikepdf

    dst = pikepdf.Pdf.new()
    for i in range(start_idx, end_idx + 1):
        dst.pages.append(src_pdf.pages[i])

    buf = io.BytesIO()
    dst.save(buf, linearize=False)
    return buf.tell()
