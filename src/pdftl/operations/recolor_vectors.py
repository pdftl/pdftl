"""Convert color instructions in page content streams to shades of gray."""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.core_types import OpResult
from pdftl.utils.page_specs import page_numbers_matching_page_spec

logger = logging.getLogger(__name__)

_RECOLOR_VECTORS_LONG_DESC = """
The `recolor_vectors` operation converts color-setting instructions within PDF
content streams. Currently it only supports grayscale conversion. The UI is expected to change.

Note: This modifies vector path geometry, text coloring operators, and layout boundaries.
Raster image payloads (XObjects) are skipped by this operation.
"""

_RECOLOR_VECTORS_EXAMPLES = [
    {
        "cmd": "in.pdf recolor_vectors 1-3 output out.pdf",
        "desc": "Convert non-image elements on pages 1-3 to grayscale",
    },
    {
        "cmd": "in.pdf recolor_vectors output out.pdf",
        "desc": "Convert non-image elements on all pages to grayscale",
    },
]


@register_operation(
    "recolor_vectors",
    tags=["in_place", "content_stream", "experimental"],
    type="single input operation",
    desc="Make non-image page content gray",
    long_desc=_RECOLOR_VECTORS_LONG_DESC,
    usage="<input> recolor_vectors [<pages>...] output <output>",
    examples=_RECOLOR_VECTORS_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def recolor_vectors_in_content_streams(pdf, specs) -> OpResult:
    """Orchestrator entry point registered into pdftl's core layout pipeline."""
    pages_processed = set()
    if not specs:
        specs = ["-"]
    for spec in specs:
        pages_processed.update(
            _apply_recolor_vectors_spec_in_content_streams(pdf, spec, pages_processed)
        )
    logger.info("Recolored vector content on %d page(s) to grayscale.", len(pages_processed))
    return OpResult(success=True, pdf=pdf)


def _apply_recolor_vectors_spec_in_content_streams(pdf, spec, pages_processed):
    if not spec:
        return pages_processed
    num_pages = len(pdf.pages)
    replacer = GreyscaleReplaceContentStream(pdf)
    for page_num in page_numbers_matching_page_spec(spec, num_pages):
        if page_num in pages_processed:
            continue
        logger.debug("Processing content stream color definitions for page: %s", page_num)
        replacer.apply(page_num)
        pages_processed.add(page_num)
    return pages_processed


@dataclass
class GreyscaleReplaceContentStream:
    """Traverses layout elements and morphs color operators into /DeviceGray."""

    pdf: "Pdf"
    _processed_xobjects: set = None

    def __post_init__(self):
        if self._processed_xobjects is None:
            self._processed_xobjects = set()

    def apply(self, page_num: int):
        """Extracts and mutates a page layout structure."""
        import pikepdf

        page = self.pdf.pages[page_num - 1]

        # Process the page's structural canvas drawing pipeline
        instructions = pikepdf.parse_content_stream(page)
        new_content = self._process_instructions(instructions)
        page.Contents = self.pdf.make_stream(new_content)

        # Recursively search for sub-forms and elements nested inside page resource forks
        if "/Resources" in page:
            self._process_resources(page.Resources)

    def _process_resources(self, resources):
        """Recursively updates child structural layouts (Form XObjects)."""
        if "/XObject" not in resources:
            return

        for _, xobj in resources.XObject.items():
            if xobj.objgen in self._processed_xobjects:
                continue
            self._processed_xobjects.add(xobj.objgen)

            if xobj.get("/Subtype") == "/Form":
                import pikepdf

                instructions = pikepdf.parse_content_stream(xobj)
                new_content = self._process_instructions(instructions)
                xobj.write(new_content)

                if "/Resources" in xobj:
                    self._process_resources(xobj.Resources)

    def _process_instructions(self, instructions):
        """Iterates content tokens, swapping out RGB/CMYK selectors for Recolor_Vectors."""
        import pikepdf

        new_instructions = []
        # Target operators: Fill color (rg, k), Stroke color (RG, K)
        target_ops = {"rg", "RG", "k", "K"}

        for operands, operator in instructions:
            op_str = str(operator)
            if op_str in target_ops:
                gray_operands, gray_operator = self.to_gray(operands, op_str)
                if gray_operator is None:
                    logger.warning("Failed converting instruction matrix: %s %s", operands, op_str)
                    new_instructions.append((operands, operator))
                else:
                    new_instructions.append((gray_operands, pikepdf.Operator(gray_operator)))
            else:
                new_instructions.append((operands, operator))

        return pikepdf.unparse_content_stream(new_instructions)

    def to_gray(self, nums, operator: str) -> tuple[list[float], str | None]:
        """Calculates exact NTSC/PDF spec luminance values for RGB or CMYK tokens."""
        try:
            # Determine stroke vs non-stroke assignment context
            # Lowercase operators = Fill context; Uppercase operators = Stroke context
            gray_operator = "g" if operator.islower() else "G"

            # Case 1: RGB Space (rg / RG) -> Needs exactly 3 parameters
            if operator.lower() == "rg":
                if len(nums) != 3:
                    return [], None
                r, g, b = map(float, nums)

            # Case 2: CMYK Space (k / K) -> Needs exactly 4 parameters
            elif operator.lower() == "k":
                if len(nums) != 4:
                    return [], None
                c, m, y, k = map(float, nums)
                # Convert CMYK vectors to RGB proxies before matrix multiplication
                r = 1.0 - min(1.0, c + k)
                g = 1.0 - min(1.0, m + k)
                b = 1.0 - min(1.0, y + k)
            else:
                return [], None

            # Apply official NTSC / PDF component luminance mapping
            gray_value = 0.3 * r + 0.59 * g + 0.11 * b
            return [round(max(0.0, min(1.0, gray_value)), 4)], gray_operator

        except (ValueError, TypeError) as e:
            logger.debug("Exception running raster math logic transformation: %s", e)
            return [], None
