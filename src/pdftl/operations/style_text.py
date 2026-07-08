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

The `style_text` operation updates text styling based on the provided parameters.
It adjusts the underlying text rendering mode (Tr) to accommodate the requested
changes. For example, setting a color on invisible text makes it visible filled
text (Tr 0), and setting a stroke color adds an outline without removing the
existing fill (Tr 2).

Page ranges can be specified using `<pages>`. The default page range is all pages.

**Key=val options:**

* `stroke=<float|percent>`  The width of the text outline. May be an absolute value (e.g. 0.5)
or a percentage of the current font size (e.g. 2%). Defaults to 0.5 if stroke_color is set.
* `color=<r g b>` Set both fill and stroke fallback colour (floats 0.0–1.0 separated by spaces).
* `fill_color=<r g b>` Set fill colour only.
* `stroke_color=<r g b>` Set stroke colour only.

**Input format**

Arguments may be given as a parenthesized spec block:

```
[page-range](key=val, ...)
```

or as the equivalent shorthand (separate arguments, no parentheses):

```
[page-range] key=val ...
```

Multiple spec blocks may be used to apply different styles to different page ranges:

```
in.pdf style_text '1-3(stroke=0.5)' '4-end(stroke=2%)' output out.pdf
```
"""

_STYLE_TEXT_EXAMPLES = [
    {
        "cmd": "in.pdf style_text 1-3 stroke=0.5 output out.pdf",
        "desc": "Add a 0.5pt text stroke to pages 1-3",
    },
    {
        "cmd": "in.pdf style_text stroke=2% 'color=0 0 0' output out.pdf",
        "desc": "Add a 2% font-size text stroke and change all text to black",
    },
    {
        "cmd": "in.pdf style_text stroke=0.2 'stroke_color=1 0 0' output out.pdf",
        "desc": "Add a red 0.2pt text stroke without changing fill colour",
    },
    {
        "cmd": (
            "in.pdf style_text "
            "'1-3(stroke=0.5,stroke_color=0.2 0.4 0)' '4-end(stroke=2%)' output out.pdf"
        ),
        "desc": "Different stroke styles on different page ranges",
    },
]

_ALLOWED_KEYS = ["stroke", "color", "fill_color", "stroke_color"]


@register_operation(
    "style_text",
    tags=["in_place", "content_stream", "custom"],
    type="single input operation",
    desc="Change appearance of text",
    long_desc=_STYLE_TEXT_LONG_DESC,
    usage="<input> style_text [<pages>...] [key=val...] output <output>",
    examples=_STYLE_TEXT_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS_EXPANDED], {}),
)
def style_text_in_content_streams(pdf, args) -> OpResult:
    """
    Apply text styles in page content streams.
    """
    if not args:
        args = []

    specs = _parse_style_text_args(args)

    for page_spec, style_params in specs:
        replacer = _build_replacer(pdf, style_params)
        if replacer is not None:
            _apply_to_pages(pdf, page_spec, replacer)

    return OpResult(success=True, pdf=pdf)


def _parse_style_text_args(args: list[str]) -> list[tuple[str, dict]]:
    if not args:
        return [("-", {})]

    results = []
    for arg in args:
        if "(" in arg:
            page_spec, _, rest = arg.partition("(")
            content = rest.rstrip(")")
            tokens = [t.strip() for t in content.split(",") if t.strip()]
            kwargs = parse_keyval_list(
                tokens,
                allowed_keys=_ALLOWED_KEYS,
                context="style_text",
            )
        else:
            page_spec = arg
            kwargs = {}

        page_spec = page_spec or "-"
        results.append((page_spec, kwargs))

    return results


def _build_replacer(pdf, parsed_kwargs: dict) -> "TextStrokeReplaceContentStream | None":
    stroke_width, stroke_width_type = _parse_stroke_width(parsed_kwargs)

    color = _get_color_or_raise(parsed_kwargs, "color")
    fill_color = _get_color_or_raise(parsed_kwargs, "fill_color")
    stroke_color = _get_color_or_raise(parsed_kwargs, "stroke_color")

    has_fill_intent = (color is not None) or (fill_color is not None)
    has_stroke_intent = (parsed_kwargs.get("stroke") is not None) or (stroke_color is not None)

    if not has_fill_intent and not has_stroke_intent:
        return None

    # Resolve fallbacks to pass concrete data to the replacer
    final_fill_color = fill_color or color
    final_stroke_color = stroke_color or color or fill_color or [0.0, 0.0, 0.0]
    final_stroke_width = stroke_width if stroke_width is not None else 0.5

    return TextStrokeReplaceContentStream(
        pdf=pdf,
        has_fill_intent=has_fill_intent,
        has_stroke_intent=has_stroke_intent,
        fill_color=final_fill_color,
        stroke_color=final_stroke_color,
        stroke_width=final_stroke_width,
        stroke_width_type=stroke_width_type,
    )


def _parse_stroke_width(parsed_kwargs: dict) -> tuple[float | None, str]:
    stroke_width = None
    stroke_width_type = "absolute"
    sw = parsed_kwargs.get("stroke", None)
    if sw is None:
        return None, stroke_width_type

    sw_hint = "Must be a non-negative number or percentage."
    try:
        sw = sw.strip()
        if sw.endswith("%"):
            stroke_width_type = "percentage"
            sw = sw.removesuffix("%")
        stroke_width = float(sw)
    except ValueError as exc:
        raise InvalidArgumentError(f"Invalid stroke width provided: '{sw}'. " + sw_hint) from exc

    if stroke_width < 0:
        raise InvalidArgumentError(f"Invalid negative stroke width provided: '{sw}'. " + sw_hint)

    return stroke_width, stroke_width_type


def _apply_to_pages(pdf, spec: str, replacer: "TextStrokeReplaceContentStream"):
    num_pages = len(pdf.pages)
    for page_num in page_numbers_matching_page_spec(spec, num_pages):
        logger.debug("page: %s", page_num)
        replacer.apply(page_num)


def _get_color_or_raise(data, key):
    val = data.get(key, None)
    if val is None:
        return None
    try:
        return [float(x) for x in val.split()]
    except (ValueError, AttributeError) as exc:
        raise InvalidArgumentError(
            f"Invalid color provided for '{key}': '{val}'. "
            "Must be a space-separated list of numbers."
        ) from exc


@dataclass
class TextStrokeReplaceContentStream:
    """A replacer for PDF content streams to enforce text styles dynamically"""

    pdf: "Pdf"
    has_fill_intent: bool = False
    has_stroke_intent: bool = False
    fill_color: list[float] | None = None
    stroke_color: list[float] | None = None
    stroke_width: float | None = None
    stroke_width_type: str = "absolute"

    _processed: set = field(default_factory=set, repr=False, compare=False)
    _pikepdf: "pikepdf_t | None" = field(default=None, init=False, repr=False, compare=False)

    @property
    def pikepdf(self) -> "pikepdf_t":
        if self._pikepdf is None:
            import pikepdf

            self._pikepdf = pikepdf
        return self._pikepdf

    def apply(self, page_num: int):
        page = self.pdf.pages[page_num - 1]

        contents = page.get("/Contents")
        if contents is None:
            return

        pikepdf_page = self.pikepdf.Page(page)
        pikepdf_page.contents_coalesce()

        stream = page["/Contents"]
        if stream.objgen in self._processed:
            return

        instructions = self.pikepdf.parse_content_stream(stream)
        new_content = self._process_instructions(instructions)
        stream.write(new_content)
        self._processed.add(stream.objgen)

        if "/Resources" in page:
            self._process_resources(page.Resources)

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

    def _get_target_tr_mode(self, current_mode: int) -> int:
        """Determines the target Tr mode dynamically based on the current state."""
        target = current_mode
        if self.has_fill_intent:
            target = {0: 0, 1: 2, 2: 2, 3: 0}.get(target, target)
        if self.has_stroke_intent:
            target = {0: 2, 1: 1, 2: 2, 3: 1}.get(target, target)
        return target

    def _state_matches_desired(self, state):
        target_tr_mode = self._get_target_tr_mode(state["render_mode"])
        if state["render_mode"] != target_tr_mode:
            return False

        if (
            self.has_fill_intent
            and self.fill_color is not None
            and state["fill_color"] != self.fill_color
        ):
            return False

        if self.has_stroke_intent and target_tr_mode in (1, 2):
            if self.stroke_color is not None and state["stroke_color"] != self.stroke_color:
                return False
            if self.stroke_width is not None and state[
                "stroke_width"
            ] != self._get_absolute_stroke_width(state):
                return False

        return True

    def _force_style_state(self, new_instructions, state):
        target_tr_mode = self._get_target_tr_mode(state["render_mode"])

        if (
            self.has_fill_intent
            and self.fill_color is not None
            and state["fill_color"] != self.fill_color
        ):
            new_instructions.append(self._color_instruction(self.fill_color, "fill"))

        if self.has_stroke_intent and target_tr_mode in (1, 2):
            if self.stroke_color is not None and state["stroke_color"] != self.stroke_color:
                new_instructions.append(self._color_instruction(self.stroke_color, "stroke"))

            abs_stroke_width = self._get_absolute_stroke_width(state)
            if self.stroke_width is not None and state["stroke_width"] != abs_stroke_width:
                new_instructions.append(([abs_stroke_width], "w"))

        if state["render_mode"] != target_tr_mode:
            new_instructions.append(([target_tr_mode], "Tr"))

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
        _FILL_COLOR_OPS = {"k": 4, "g": 1, "rg": 3}
        _STROKE_COLOR_OPS = {"K": 4, "G": 1, "RG": 3}
        _UNKNOWN_FILL_OPS = {"cs", "scn", "sc"}
        _UNKNOWN_STROKE_OPS = {"CS", "SCN", "SC"}
        _TEXT_OPS = {"Tj", "TJ", "'", '"'}

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
        elif op_str in _FILL_COLOR_OPS:
            state["fill_color"] = self._colors_to_list(operands, _FILL_COLOR_OPS[op_str])
        elif op_str in _STROKE_COLOR_OPS:
            state["stroke_color"] = self._colors_to_list(operands, _STROKE_COLOR_OPS[op_str])
        elif op_str in _UNKNOWN_FILL_OPS:
            state["fill_color"] = None
        elif op_str in _UNKNOWN_STROKE_OPS:
            state["stroke_color"] = None
        elif op_str in _TEXT_OPS and not self._state_matches_desired(state):
            self._force_style_state(new_instructions, state)
            self._update_state(state)

    def _update_state(self, state):
        target_tr_mode = self._get_target_tr_mode(state["render_mode"])

        if self.has_fill_intent and self.fill_color is not None:
            state["fill_color"] = list(self.fill_color)

        if self.has_stroke_intent and target_tr_mode in (1, 2):
            if self.stroke_color is not None:
                state["stroke_color"] = list(self.stroke_color)
            if self.stroke_width is not None:
                state["stroke_width"] = self._get_absolute_stroke_width(state)

        state["render_mode"] = target_tr_mode
