import logging

from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.dimensions import get_visible_page_dimensions  # <-- Add this import

from .parser import SpecParser
from .spec_types import PageTransform

logger = logging.getLogger(__name__)


def _handle_no_specs(inputs, opened_pdfs) -> list[PageTransform]:
    page_tuples: list[PageTransform] = []
    if inputs is None:
        return page_tuples
    for input_idx in range(len(inputs)):
        pdf = opened_pdfs[input_idx]
        for i in range(len(pdf.pages)):
            page_tuples.append(PageTransform(pdf=pdf, index=i, rotation=(0, False), scale=1.0))
    return page_tuples


def _resolve_alias_and_spec(spec, opened_pdfs_by_alias, default_alias):
    if spec and spec.startswith("_"):
        alias = default_alias
        page_spec_full = spec[1:]
    elif spec and spec[0].isalpha() and spec[0].upper() in opened_pdfs_by_alias:
        alias = spec[0].upper()
        page_spec_full = spec[1:]
    else:
        alias = default_alias
        page_spec_full = spec

    if not alias or alias not in opened_pdfs_by_alias:
        raise InvalidArgumentError(f"Cannot determine a valid alias for spec '{spec}'")

    pdf = opened_pdfs_by_alias[alias]
    return pdf, page_spec_full, alias


def _filter_page_numbers(page_numbers, qualifiers, omissions, pdf=None):
    if "even" in qualifiers:
        page_numbers = [p for p in page_numbers if p % 2 == 0]
    if "odd" in qualifiers:
        page_numbers = [p for p in page_numbers if p % 2 != 0]

    page_numbers = [
        p
        for p in page_numbers
        if _aspect_ratio_pass(p, "portrait" in qualifiers, "landscape" in qualifiers, pdf)
    ]

    for om_start, om_end in omissions:
        page_numbers = [p for p in page_numbers if not om_start <= p <= om_end]
    return page_numbers


def _aspect_ratio_pass(p, portrait_q, landscape_q, pdf):
    if not pdf or not pdf.pages or len(pdf.pages) < p:
        return True
    dims = get_visible_page_dimensions(pdf.pages[p - 1])
    if not dims:
        return True
    _, _, w, h = dims
    ret = (not portrait_q or h >= w) and (not landscape_q or w >= h)
    logger.debug(
        "potrait_q=%s, landscape_q=%s, p=%s, w=%s, h=%s, ret=%s",
        portrait_q,
        landscape_q,
        p,
        w,
        h,
        ret,
    )
    return ret


def _create_page_tuples_from_numbers(
    page_numbers, pdf, rotate, scale, spec_for_error
) -> list[PageTransform]:
    new_tuples = []
    total_pages = len(pdf.pages)
    pdf_filename = (
        pdf.filename
        if hasattr(pdf, "filename") and pdf.filename != "empty PDF"
        else "pipeline PDF"
    )

    for page_num in page_numbers:
        if not 1 <= page_num <= total_pages:
            raise InvalidArgumentError(
                f"Invalid page.\n  "
                f"Page spec '{spec_for_error}' includes page {page_num} but "
                f"there are only {total_pages} pages in {pdf_filename}"
            )
        new_tuples.append(PageTransform(pdf=pdf, index=page_num - 1, rotation=rotate, scale=scale))
    return new_tuples


def _new_tuples_from_spec_str(
    spec_str, opened_pdfs_by_alias, default_alias
) -> list[PageTransform]:
    pdf, page_spec_full, _alias = _resolve_alias_and_spec(
        spec_str, opened_pdfs_by_alias, default_alias
    )

    # Use SpecParser directly here to avoid circular dependency on parse_sub_page_spec
    # Note: resolver always uses the default SPEC_REGEX (via SpecParser default)
    # as no tests patch regex behavior in this path.
    parser = SpecParser(len(pdf.pages))
    page_spec = parser.parse(page_spec_full)

    direction_sign = 1
    if page_spec.end < page_spec.start:
        direction_sign = -1
    initial_page_numbers = list(
        range(page_spec.start, page_spec.end + direction_sign, direction_sign * page_spec.step)
    )

    # apply even/odd, layout qualifiers, and omissions
    final_page_numbers = _filter_page_numbers(
        initial_page_numbers,
        page_spec.qualifiers,
        page_spec.omissions,
        pdf,  # <-- Pass pdf here
    )

    if page_spec.rep > 1:
        final_page_numbers = [p for p in final_page_numbers for _ in range(page_spec.rep)]

    new_tuples = _create_page_tuples_from_numbers(
        final_page_numbers, pdf, page_spec.rotate, page_spec.scale, spec_str
    )
    return new_tuples
