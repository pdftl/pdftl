# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/cli/help_render.py

"""Help rendering methods"""

import contextlib
import logging
import sys

from pdftl.cli.console import get_console
from pdftl.cli.whoami import WHOAMI
from pdftl.utils.pager import ThresholdPagerStream

logger = logging.getLogger(__name__)


def _load_help_markdown():
    from rich import box
    from rich.console import Console, ConsoleOptions, RenderResult
    from rich.markdown import Heading, Markdown
    from rich.panel import Panel
    from rich.text import Text

    class LeftJustifiedHeading(Heading):
        def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
            text = self.text
            text.justify = "left"
            if self.tag == "h1":
                # Draw a border around h1s
                yield Panel(
                    text,
                    box=box.HEAVY,
                    style="markdown.h1.border",
                )
            else:
                # Styled text for h2 and beyond
                if self.tag == "h2":
                    yield Text("")
                yield text

    class HelpMarkdown(Markdown):
        elements = {
            **Markdown.elements,
            "heading_open": LeftJustifiedHeading,
        }

        def __init__(self, markup, *args, **kwargs):
            self.source = markup
            super().__init__(markup, *args, **kwargs)

        def __str__(self):
            return self.source

    return HelpMarkdown


@contextlib.contextmanager
def page_captured_output():
    """Smart-pages console output via real-time stream interception."""
    console = get_console()
    if console is None:
        raise RuntimeError("Rich console is not available")

    # Set threshold with a 4-line safety margin for the shell prompt
    threshold = max(1, console.height - 4)
    stream = ThresholdPagerStream(threshold)

    # Temporarily hijack the console's output target
    original_file = console.file
    console.file = stream

    try:
        yield console
    except BrokenPipeError:
        # The user quit the pager or a downstream pipe was closed.
        # We can safely abort the rest of the output generation.
        pass
    finally:
        # Guarantee cleanup and reset the console
        console.file = original_file
        stream.close()


def load_hprint(dest, raw):
    def hprint(x):
        HelpMarkdown = _load_help_markdown()
        use_rich_console = not raw and (dest is None or dest is sys.stdout or dest is sys.stderr)

        if use_rich_console:
            console = get_console()
            if console is None:
                raise RuntimeError("Rich console is not available")
            console.print(HelpMarkdown(x))

        elif not raw:
            from rich.console import Console

            console = get_console()
            if console is None:
                raise RuntimeError("Rich console is not available")
            width = console.width if console.width else 80
            # Rendered output for files/pipes (fixes missing table format in files)
            file_console = Console(file=dest, width=width, force_terminal=False)
            file_console.print(HelpMarkdown(x))
        else:
            # Raw output (Markdown source)
            target = dest if dest is not None else sys.stdout
            print(x, file=target)

    return hprint


def format_examples_block(examples, show_topics=False):
    """Formats the examples into a rich-ready Markdown block.

    We convert the structured data into a Markdown code/list structure.
    """
    output = "## Examples\n\n"
    per_example_topic_count = 1
    topic = None

    for ex in examples:
        if not ("desc" in ex and "cmd" in ex):
            logger.warning("Skipping incomplete example: %s", ex)
            continue

        # Add topic heading if necessary
        if show_topics and ex.get("topic"):
            if ex.topic == topic:
                per_example_topic_count += 1
            else:
                per_example_topic_count = 1
            topic = ex.topic

            # Use Markdown heading or strong text for topics
            heading_text = (
                "Example"
                + (f" {per_example_topic_count}" if per_example_topic_count > 1 else "")
                + f" for '`{topic}`'"
            )
            output += f"### {heading_text}\n"

        # Use a quote block for description and a fenced code block for command
        output += f"> {ex.get('desc', '')}\n"
        output += f"```\n{WHOAMI} {ex['cmd'].strip()}\n```\n\n"

    return output.rstrip()


def usage_as_markdown(x: str) -> str:
    return " ".join(map(lambda y: f"`{y}`", x.strip().split()))


def format_tags(tags):
    return f"\n\n**Tags**: {', '.join(tags)}"
