# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/style_text.py

"""Change simple rendering parameters of PDF text"""

import logging
import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pikepdf as pikepdf_t
    from pikepdf import Pdf

import pdftl.core.constants as c
from pdftl.exceptions import InvalidArgumentError
from pdftl.core.registry import register_operation
from pdftl.core.core_types import OpResult
from pdftl.utils.page_specs import page_numbers_matching_page_spec
from pdftl.utils.keyval_parser import parse_keyval_list

logger = logging.getLogger(__name__)

_STYLE_TEXT_LONG_DESC = """

The `style_text` operation converts all filled text (Tr 0)
into filled and stroked text (Tr 2) and applies a user-defined
stroke width. Page ranges can be specified using `<pages>`.
The default page range is all pages.

**Key=val options:**

* `stroke=<float|percent>`  The width of the text outline. May be an absolute value (e.g. 0.5)
or a percentage of the current font size (e.g. 2%). Defaults to 0.5
if stroke_color is set, otherwise text outlines are not altered.
* `color=<r,g,b>` Set both fill and stroke colour (comma-separated floats, 0.0–1.0).
* `fill_color=<r,g,b>` Set fill colour only.
* `stroke_color=<r,g,b>`    Set stroke colour only.
"""

_STYLE_TEXT_EXAMPLES = [
    {
        "cmd": "in.pdf style_text 1-3 stroke=0.5 output out.pdf",
        "desc": "Add a 0.5pt text stroke to pages 1-3",
    },
    {
        "cmd": "in.pdf style_text stroke=2% color=0,0,0 output out.pdf",
        "desc": "Add a 2% font-size text stroke and change all text to black",
    },
    {
        "cmd": "in.pdf style_text stroke=0.2 stroke_color=1,0,0 output out.pdf",
        "desc": "Add a red 0.2pt text stroke without changing fill colour",
    },
]


@register_operation(
    "style_text",
    tags=["in_place", "content_stream", "custom"],
    type="single input operation",
    desc="Change appearance of text",
    long_desc=_STYLE_TEXT_LONG_DESC,
    usage="<input> style_text [<pages>...] [key=val...] output <output>",
    examples=_STYLE_TEXT_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def style_text_in_content_streams(pdf, args) -> OpResult:
    """
    Apply text strokes in page content streams.
    """
    if not args:
        args = []

    bare_tokens = []
    parsed_kwargs = parse_keyval_list(
        args,
        bare_tokens=bare_tokens,
        allowed_keys=["stroke", "color", "fill_color", "stroke_color"],
        context="style_text",
    )

    specs = bare_tokens if bare_tokens else ["-"]

    stroke_width = None
    stroke_width_type = "absolute"
    try:
        sw = parsed_kwargs.get("stroke", None)
        if sw is not None:
            sw = sw.rstrip(" ")
            if sw.endswith("%"):
                stroke_width_type = "percentage"
                sw = sw.rstrip("%")
            stroke_width = float(sw)
    except ValueError as exc:
        raise InvalidArgumentError(
            f"Invalid stroke width provided: '{sw}'. "
            "Must be a non-negative number or a percentage."
        ) from exc
    if stroke_width is not None and stroke_width < 0:
        raise InvalidArgumentError(
            f"Invalid negative stroke width provided: '{sw}'. "
            "Must be a non-negative number or a percentage."
        )

    color = _get_color_or_raise(parsed_kwargs, "color")
    fill_color = _get_color_or_raise(parsed_kwargs, "fill_color") or color
    stroke_color = _get_color_or_raise(parsed_kwargs, "stroke_color") or color

    tr_mode = None
    if stroke_color is not None and stroke_width is None:
        stroke_width = 0.5
    if stroke_width is not None:
        tr_mode = 2

    for spec in specs:
        _apply_style_text_spec_in_content_streams(
            pdf, spec, stroke_width, stroke_width_type, stroke_color, fill_color, tr_mode
        )

    return OpResult(success=True, pdf=pdf)


def _get_color_or_raise(data, key):
    val = data.get(key, None)
    if val is None:
        return None
    try:
        return [float(x) for x in val.split(",")]
    except (ValueError, AttributeError) as exc:
        raise InvalidArgumentError(
            f"Invalid color provided for '{key}': '{val}'. "
            "Must be a comma-separated list of numbers."
        ) from exc


def _apply_style_text_spec_in_content_streams(
    pdf,
    spec,
    stroke_width: float | None,
    stroke_width_type: str,
    stroke_color: list | None,
    fill_color: list | None,
    tr_mode: int | None,
):
    if not spec:
        return
    num_pages = len(pdf.pages)
    page_spec, replacer = _parse_style_spec(
        pdf, spec, stroke_width, stroke_width_type, stroke_color, fill_color, tr_mode
    )
    for page_num in page_numbers_matching_page_spec(page_spec, num_pages):
        logger.debug("page: %s", page_num)
        replacer.apply(page_num)


def _parse_style_spec(
    pdf,
    spec,
    stroke_width: float | None,
    stroke_width_type,
    stroke_color: list | None,
    fill_color: list | None,
    tr_mode: int | None,
):
    replacer_args = {"stroke_width_type": stroke_width_type}
    if stroke_width is not None:
        replacer_args["stroke_width"] = stroke_width
    if stroke_color is not None:
        replacer_args["stroke_color"] = stroke_color
    if fill_color is not None:
        replacer_args["fill_color"] = fill_color
    if tr_mode is not None:
        replacer_args["tr_mode"] = tr_mode
    logger.debug("%s", replacer_args)
    replacer = TextStrokeReplaceContentStream(pdf, **replacer_args)
    return (spec, replacer)


@dataclass
class TextStrokeReplaceContentStream:
    """A replacer for PDF content streams to enforce text strokes"""

    pdf: "Pdf"
    fill_color: list[float] | None = None
    stroke_color: list[float] | None = None
    stroke_width: float | None = None
    stroke_width_type: str = "absolute"
    tr_mode: int | None = None

    _processed: set = field(default_factory=set, repr=False, compare=False)
    _pikepdf: "pikepdf_t | None" = field(default=None, init=False, repr=False, compare=False)

    @property
    def pikepdf(self) -> "pikepdf_t":
        if self._pikepdf is None:
            import pikepdf

            self._pikepdf = pikepdf
        return self._pikepdf

    # -------------------------
    # main entry
    # -------------------------
    def apply(self, page_num: int):
        page = self.pdf.pages[page_num - 1]

        contents = page.get("/Contents")
        if contents is None:
            return
        elif isinstance(contents, self.pikepdf.Array):
            streams = list(contents)
        else:
            streams = [contents]

        for stream in streams:
            if stream.objgen in self._processed:
                continue
            instructions = self.pikepdf.parse_content_stream(stream)
            new_content = self._process_instructions(instructions)
            stream.write(new_content)
            self._processed.add(stream.objgen)

        if "/Resources" in page:
            self._process_resources(page.Resources)

    # -------------------------
    # XObject recursion
    # -------------------------
    def _process_resources(self, resources):
        if "/XObject" not in resources:
            return

        for _, xobj in resources.XObject.items():
            if xobj.objgen in self._processed:
                continue

            if xobj.get("/Subtype") == "/Form":
                instructions = self.pikepdf.parse_content_stream(xobj)
                new_content = self._process_instructions(instructions)
                xobj.write(new_content)
                self._processed.add(xobj.objgen)

                if "/Resources" in xobj:
                    self._process_resources(xobj.Resources)
            # Non-Form XObjects (images, PostScript fragments, etc.) contain no
            # text operators so there is nothing to restyle. We intentionally do
            # not add them to _processed so that other pages sharing the same
            # image object are not silently skipped.

    # -------------------------
    # color helpers
    # -------------------------
    def _color_instruction(self, operands, fill_or_stroke="fill"):
        if len(operands) == 1:
            op, instr = list(operands), "g"
        elif len(operands) == 3:
            op, instr = list(operands), "rg"
        elif len(operands) == 4:
            op, instr = list(operands[:4]), "k"
        else:
            raise ValueError(f"Bad color operands: {operands}")

        if fill_or_stroke != "fill":
            instr = instr.upper()

        return (op, instr)

    def _colors_to_list(self, operands, length):
        if not operands:
            return []
        return [float(x) for x in operands[:length]]

    _DEFAULT_FONT_SIZE = 12.0

    def _get_absolute_stroke_width(self, state):
        if self.stroke_width_type == "percentage":
            font_size = state["font_size"]
            if font_size is None:
                logger.debug(
                    "percentage stroke width requested but no font size known yet; "
                    "using default %s",
                    self._DEFAULT_FONT_SIZE,
                )
                font_size = self._DEFAULT_FONT_SIZE
            return font_size * self.stroke_width / 100
        return self.stroke_width

    def _state_matches_desired(self, state):
        """Return True if all non-None desired values are already in current state."""
        if self.tr_mode is not None and state["render_mode"] != self.tr_mode:
            return False
        if self.stroke_width is not None and state[
            "stroke_width"
        ] != self._get_absolute_stroke_width(state):
            return False
        if self.stroke_color is not None and state["stroke_color"] != self.stroke_color:
            return False
        if self.fill_color is not None and state["fill_color"] != self.fill_color:
            return False
        return True

    def _force_style_state(self, new_instructions, state):
        if self.fill_color is not None and state["fill_color"] != self.fill_color:
            new_instructions.append(self._color_instruction(self.fill_color, "fill"))
        if self.stroke_color is not None and state["stroke_color"] != self.stroke_color:
            new_instructions.append(self._color_instruction(self.stroke_color, "stroke"))
        abs_stroke_width = self._get_absolute_stroke_width(state)
        if self.stroke_width is not None and state["stroke_width"] != abs_stroke_width:
            logger.debug(
                "state[stroke_width]=%s, target=%s", state["stroke_width"], abs_stroke_width
            )
            new_instructions.append(([abs_stroke_width], "w"))
        if self.tr_mode is not None and state["render_mode"] != self.tr_mode:
            new_instructions.append(([self.tr_mode], "Tr"))

    # -------------------------
    # instruction transformer
    # -------------------------
    def _process_instructions(self, instructions):
        new_instructions = []
        stack = []
        state = {
            "font": None,
            "font_size": None,
            "render_mode": 0,
            "stroke_color": [0.0, 0.0, 0.0],
            "stroke_width": 0.0,
            "fill_color": [0.0, 0.0, 0.0],
        }

        for operands, operator in instructions:
            op_str = str(operator)
            self._process_op(op_str, operands, state, stack, new_instructions)
            new_instructions.append((operands, operator))

        return self.pikepdf.unparse_content_stream(new_instructions)

    def _process_op(self, op_str, operands, state, stack, new_instructions):
        if op_str == "q":
            stack.append(copy.deepcopy(state))
        elif op_str == "Q" and stack:
            state.clear()
            state.update(stack.pop())
        elif op_str == "Tf":
            state["font"] = str(operands[0])
            state["font_size"] = float(operands[1])
        elif op_str == "Tr":
            state["render_mode"] = int(operands[0])
        elif op_str == "w":
            state["stroke_width"] = float(operands[0])
        elif op_str == "K":
            state["stroke_color"] = self._colors_to_list(operands, 4)
        elif op_str == "k":
            state["fill_color"] = self._colors_to_list(operands, 4)
        elif op_str == "G":
            state["stroke_color"] = self._colors_to_list(operands, 1)
        elif op_str == "g":
            state["fill_color"] = self._colors_to_list(operands, 1)
        elif op_str == "RG":
            state["stroke_color"] = self._colors_to_list(operands, 3)
        elif op_str == "rg":
            state["fill_color"] = self._colors_to_list(operands, 3)
        elif op_str in ("Tj", "TJ", "'", '"') and not self._state_matches_desired(state):
            self._force_style_state(new_instructions, state)
            self._update_state(state)

    def _update_state(self, state):
        # Update state to reflect what we just injected
        if self.fill_color is not None:
            state["fill_color"] = list(self.fill_color)
        if self.stroke_color is not None:
            state["stroke_color"] = list(self.stroke_color)
        if self.stroke_width is not None:
            state["stroke_width"] = self._get_absolute_stroke_width(state)
        if self.tr_mode is not None:
            state["render_mode"] = self.tr_mode
