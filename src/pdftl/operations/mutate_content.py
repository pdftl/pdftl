"""Mutate page content streams using a user-supplied Python script"""

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import HelpExample, OpResult

if TYPE_CHECKING:
    from pikepdf import Pdf

logger = logging.getLogger(__name__)

_MUTATE_CONTENT_LONG_DESC = """ The `mutate_content` operation allows you to run custom Python
logic against the raw instruction streams of a PDF. You can specify
your own Python script, and the function from that script to run. The
default function search for if no function is specified is `mutate`.

### The Context Dictionary
Your function receives a `context` dictionary with the following keys:

- `pdf`: The current `pikepdf.Pdf` object.
- `page_num`: The current page number (1-indexed) or `None` for XObjects.
- `is_xobject`: Boolean, `True` if mutating a Form XObject.
- `object`: the `pikepdf.Object` (either a page or an XObject) containing the content stream.
- `args`: A list of extra arguments passed via CLI after the script name.


### Passing Arguments
You can pass custom parameters to your script:
`pdftl in.pdf mutate_content script.py 1.5 2.0 output out.pdf`
In this case, `context['args']` would be `['1.5', '2.0']`.
"""

_MUTATE_CONTENT_EXAMPLES = [
    HelpExample(
        desc=(
            "Mutate content streams in all pages"
            " using the `mutate` function in `mutate_grayscale.py`"
        ),
        cmd="in.pdf mutate_content mutate_grayscale.py output out.pdf",
    ),
    HelpExample(
        desc=(
            "Fix hairlines using `mutate_hairline_fixer.py`,"
            " passing the script the argument '0.5'"
        ),
        cmd="in.pdf mutate_content mutate_hairline_fixer.py 0.5 output out.pdf",
    ),
]


@register_operation(
    "mutate_content",
    tags=["content_stream", "custom", "advanced"],
    type="single input operation",
    desc="Mutate page content streams using a user-supplied Python script",
    long_desc=_MUTATE_CONTENT_LONG_DESC,
    examples=_MUTATE_CONTENT_EXAMPLES,
    usage="<input> mutate_content <script.py[::func]> [<script_args>...] output <output>",
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def mutate_content(pdf: "Pdf", args: list) -> OpResult:
    import importlib.util

    if not args:
        raise ValueError("mutate_content requires a script path.")

    # 1. Separate the script/func from the following arguments
    script_info = args[0]
    extra_args = args[1:]  # Everything else is passed to context['args']

    if "::" in script_info:
        script_path, func_name = script_info.rsplit("::", 1)
    else:
        script_path, func_name = script_info, "mutate"

    # 2. Load the user's script
    path = Path(script_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Script not found: {path}")

    module_name = f"pdftl_user_mutator_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load script module from path: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    mutate_func = getattr(module, func_name, None)
    if mutate_func is None or not callable(mutate_func):
        raise AttributeError(f"Function '{func_name}' not found or not callable in script: {path}")

    # 3. Execution Engine
    # Note: We pass extra_args here to be included in the context
    engine = ContentMutationEngine(pdf, mutate_func, extra_args)

    # Process all pages of the document
    # The mutation function can selectively mutate pages/XObjects as needed
    num_pages = len(pdf.pages)
    for page_num in range(1, num_pages + 1):
        engine.apply(page_num)

    return OpResult(success=True, pdf=pdf)


@dataclass
class ContentMutationEngine:
    pdf: "Pdf"
    mutate_func: Callable
    user_args: list[str] = field(default_factory=list)
    _processed_xobjs: set = field(default_factory=set)

    def apply(self, page_num: int):
        import pikepdf

        page = self.pdf.pages[page_num - 1]

        context = {
            "pdf": self.pdf,
            "object": page,
            "page_num": page_num,
            "is_xobject": False,
            "args": self.user_args,
        }

        instructions = pikepdf.parse_content_stream(page)
        mutated = self.mutate_func(instructions, context)
        page.Contents = self.pdf.make_stream(pikepdf.unparse_content_stream(mutated))

        if "/Resources" in page:
            self._process_resources(page.Resources)

    def _process_resources(self, resources):
        if "/XObject" not in resources:
            return

        for name, xobj in resources.XObject.items():
            if xobj.objgen in self._processed_xobjs:
                continue
            self._processed_xobjs.add(xobj.objgen)

            if xobj.get("/Subtype") == "/Form":
                import pikepdf

                context = {
                    "pdf": self.pdf,
                    "object": xobj,
                    "page_num": None,
                    "is_xobject": True,
                    "args": self.user_args,
                    "xobject_name": str(name),
                }

                instructions = pikepdf.parse_content_stream(xobj)
                mutated = self.mutate_func(instructions, context)
                xobj.write(pikepdf.unparse_content_stream(mutated))

                if "/Resources" in xobj:
                    self._process_resources(xobj.Resources)
