# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/cli/help.py

"""Help for CLI interface"""

import logging
import sys

logger = logging.getLogger(__name__)

from pdftl.cli.console import get_console
from pdftl.cli.help_data import (
    SPECIAL_HELP_TOPICS_MAP,
    SYNOPSIS_TEMPLATE,
)
from pdftl.cli.help_render import (
    format_examples_block,
    format_tags,
    load_hprint,
    usage_as_markdown,
)
from pdftl.cli.metadata import get_project_version
from pdftl.cli.whoami import PACKAGE, WHOAMI
from pdftl.core.core_types import HelpExample
from pdftl.core.registry import register_help_topic, registry
from pdftl.utils.string_utils import before_space

TAG_PREFIX = "tag:"


def get_synopsis():
    """Returns the main synopsis text for the application."""
    # Note: Using Markdown syntax here provides better output when rendered by rich
    special_help_topics = " | ".join([f"{x[0]}" for x in SPECIAL_HELP_TOPICS_MAP.keys()])
    ret = SYNOPSIS_TEMPLATE.strip().format(whoami=WHOAMI, special_help_topics=special_help_topics)
    return ret


def _resolve_special_help_topic(topic_name):
    for x, y in SPECIAL_HELP_TOPICS_MAP.items():
        if topic_name in x or topic_name == y:
            return y
    return None


def _print_help_trailer(hprint, topic_data, topic_name):
    if tags := topic_data.get("tags", None):
        hprint(format_tags(tags))

    if caller := topic_data.get("caller", None):
        hprint(f"\n*Source: {caller}*")

    topic_type = type(topic_data).__name__

    if rtd_url := _get_rtd_url(topic_name, topic_type, topic_data, caller):
        hprint(f"\n*Read online: [{rtd_url}]({rtd_url})*")

    hprint(f"\n*Type: {type(topic_data).__name__}*")


def _print_topic_help(hprint, topic_data, topic_name):
    """Prints the detailed help for a specific command or topic."""
    safe_topic_name = (
        topic_name if any(topic_name in x for x in SPECIAL_HELP_TOPICS_MAP) else f"`{topic_name}`"
    )

    hprint(f"# {PACKAGE}: help for {safe_topic_name}")

    if desc := topic_data.get("desc", None):
        hprint(f"\n{desc.strip()}")

    if usage := topic_data.get("usage", None):
        usage_string = f"## Usage\n> {WHOAMI} {usage_as_markdown(usage)}"
        hprint(usage_string)

    if long_desc := topic_data.get("long_desc", None):
        cleaned_desc = long_desc.strip()
        hprint("\n## Details\n" + cleaned_desc)

    if examples := topic_data.get("examples", None):
        example_markdown = format_examples_block(examples)
        hprint(example_markdown)

    _print_help_trailer(hprint, topic_data, topic_name)


def _get_rtd_url(topic_name, topic_type, topic_data, caller) -> None | str:
    """Dynamically build the docs URL based on the caller namespace.

    e.g., "pdftl.operations.cat" -> category becomes \"operations\" """
    if not caller:
        return None

    current_version = get_project_version()
    anchor = None
    if any(x in current_version for x in ("post", "rc", "dev", "+")):
        docs_version = "latest"
    else:
        docs_version = "stable"

    if special_help_topic := _resolve_special_help_topic(topic_name):
        category = "general"
        topic_name_raw = special_help_topic
    elif topic_type.endswith("HelpTopic"):
        category = "help"
        topic_name_raw = topic_data.name
    elif topic_type.endswith("Option"):
        import re

        category = "misc"
        anchor = re.sub("[^-a-zA-Z]", "", re.sub("[_ ]", "-", topic_name)).lower()
        topic_name_raw = "output_options"
    else:
        topic_name_raw = topic_name
        parts = caller.split(".")
        if len(parts) <= 1:
            return None
        category = parts[1]

    # Safely encode spaces for URLs (e.g., "shell completion" -> "shell%20completion")
    url_topic = topic_name_raw.replace(" ", "%20")
    doc_url = f"https://pdftl.readthedocs.io/en/{docs_version}/{category}/{url_topic}.html"
    if anchor:
        doc_url += f"#{anchor}"

    # Using Markdown syntax so `rich` parses it into a clickable terminal link
    return doc_url


def print_main_help(dest=None, raw=False):
    """
    Prints the main help screen using either the Fast Path (Rich)
    or the Doc Path (Raw Markdown).
    """
    if raw:
        # --- Strategy 1: Raw Markdown (For docs/grep) ---
        # Uses built-in print, outputs Markdown syntax
        target = dest if dest is not None else sys.stdout

        def print_title(main, sub):
            print(f"# **{main}**\n_{sub}_", file=target)

        def print_usage(text):
            print(f"## Usage\n\n```\n{text.strip()}\n```", file=target)

        def print_table(title, data):
            # Reuses the original markdown table logic
            out = f"|{title}||\n|-|-|\n"
            for op, info in sorted(data.items()):
                out += f"|`{op}`|{info.get('desc', '')}|\n"
            print(out, file=target)

    else:
        # --- Strategy 2: Fast Rich (Restored Visuals) ---
        import re

        from rich import box  # Low cost import for styling
        from rich.console import Console
        from rich.markup import escape
        from rich.padding import Padding
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        # Setup console
        if dest is None or dest is sys.stdout or dest is sys.stderr:
            console = get_console()
        else:
            console = Console(file=dest, width=80, force_terminal=False)
        if console is None:
            raise RuntimeError("Rich console is not available")

        def print_title(main, sub):
            # 1. Restore the Heavy Box around the title
            console.print(
                Panel(
                    Text(main, justify="left", style="bold"),
                    box=box.HEAVY,
                    style="white",
                    expand=True,
                )
            )
            # 2. Subtitle
            console.print(Text(sub, style="dim"))  # 'dim' often looks cleaner than italic
            console.print()

        def print_usage(text):
            console.print(Text("Usage", style="bold underline"))
            usage_raw = f"\n{text.strip()}\n"
            usage_styled = Text(usage_raw)

            # # next style is probably orthodox (c.f. man pages)
            # # but it looks vile, so let's omit it
            # usage_styled.highlight_regex(r"<[^>]+>", "underline")

            usage_styled.highlight_regex(
                r"((\b(pdftl|help|all|sign|filter|pages|completion|examples|output|bash|powershell|zsh)|--version)\b|---|[^<]input[^>])",
                "bold cyan",
            )
            usage_styled.highlight_regex(r"[][|()]", "dim")

            # Indent the synopsis slightly for readability
            console.print(Padding(usage_styled, (0, 0, 1, 2)))

        def print_table(title, data):
            # 3. Restore the "Markdown Table" look
            # box.HEAVY_HEAD puts a thick line (━) under the header, matching old output
            # show_edge=False removes the outer side borders
            t = Table(box=box.SIMPLE_HEAVY, show_edge=False, pad_edge=False, show_lines=False)

            # The header becomes the section title ("Operations", "Options")
            t.add_column(f" {title}", style="bold cyan", no_wrap=True)
            t.add_column("")  # Empty string or 'Description' for 2nd col

            for op, info in sorted(data.items()):
                description_string = info.get("desc", "")
                safe_desc = escape(description_string)
                formatted_desc = re.sub(r"`(.*?)`", r"[cyan]\1[/cyan]", safe_desc)
                # Add a leading space to operation name for breathing room
                t.add_row(f" {op}", formatted_desc)

            console.print(Padding(t, (0, 2, 0, 2)))
            console.print()

    # We define the layout and content ONCE here.

    print_title(
        f"{PACKAGE} - PDF tackle {get_project_version()}",
        "A wannabe CLI compatible clone/extension of pdftk",
    )

    print_usage(get_synopsis())

    print_table("Operations", registry.operations)
    print_table("Options", registry.options)


def _print_output_options_help(hprint):
    """Prints detailed help for all output options."""
    hprint("# pdftl: Options for PDF output\n")
    for opt, info in sorted(registry.options.items()):
        safe_opt = opt
        hprint(f"\n## `{safe_opt}`")
        hprint(f"\n> {info.get('desc', '')}\n")
        if hasattr(info, "long_desc"):
            cleaned_desc = info.long_desc.strip()
            if cleaned_desc:
                hprint("\n## Details\n")
                hprint(cleaned_desc)
        if hasattr(info, "examples"):
            example_markdown = format_examples_block(info.examples)
            hprint(example_markdown)
        _print_help_trailer(hprint, info, opt)


def _print_examples_help(hprint):
    """Prints all examples and not much else (uses HelpMarkdown output)."""
    hprint(format_examples_block(_discover_examples(), show_topics=True))


def _discover_examples():
    """Find all known CLI examples."""
    all_examples = []
    import itertools

    for topic, topic_data in itertools.chain(
        registry.operations.items(),
        registry.options.items(),
        registry.help_topics.items(),
    ):
        new_examples = topic_data.get("examples", [])
        for example in new_examples:
            example.topic = topic
        all_examples.extend(new_examples)
    return all_examples


def _print_help_dispatch_table():
    """Return a dispatch table for print_help"""
    import itertools

    dispatch_table = {
        before_space(op): (
            lambda hprint, op_info=info, op_name=op: _print_topic_help(hprint, op_info, op_name)
        )
        for op, info in itertools.chain(registry.operations.items(), registry.options.items())
    }
    dispatch_table.update(
        {
            topic: (lambda hprint, t_data=data: _print_topic_help(hprint, t_data, t_data["title"]))
            for topic, data in itertools.chain(
                registry.help_topics.items(),
            )
        }
    )
    dispatch_table["output_options"] = lambda hprint, op_info=None, op_name=None: (
        _print_output_options_help(hprint)
    )
    dispatch_table["examples"] = lambda hprint, op_info=None, op_name=None: _print_examples_help(
        hprint
    )
    return dispatch_table


def print_help(command=None, dest=None, raw=False):
    """
    Displays help information for the tool, a specific command, or a topic.

    Args:
        command (str, optional): The command or topic to get help for.
                                 If None, the main help screen is shown.
        dest (file object, optional): The destination to write help text to.
                                      Defaults to sys.stdout.
        raw (bool, optional): If True, output raw markdown/text instead of rendered Rich output.
    """

    hprint = load_hprint(dest, raw)

    safe_command = command.lower() if command else None

    if safe_command is None:
        print_main_help(dest=dest, raw=raw)
    elif safe_command.startswith(TAG_PREFIX):
        import itertools

        tag = safe_command[len(TAG_PREFIX) :]
        taggable_topics = itertools.chain(
            registry.operations.items(),
            registry.options.items(),
            registry.help_topics.items(),
        )
        tagged_topics = [
            before_space(k) for k, t in taggable_topics if hasattr(t, "tags") and tag in t.tags
        ]
        _print_multiple_topics(tagged_topics, hprint, dest, raw)
    elif safe_command in (dispatch_table := _print_help_dispatch_table()):
        dispatch_table[safe_command](hprint)
    elif safe_command == "all":
        # Iterates through all topics
        all_topics = [
            None,
            "input",
            *sorted(registry.operations),
            "filter_mode",
            "output_options",
            "signing",
            "page_specs",
            "pipeline",
            "shell_completion",
            "help",
        ]
        _print_multiple_topics(all_topics, hprint, dest, raw)
    else:
        logger.warning("Unknown help topic '%s' requested, showing default help\n", command)
        print_main_help(dest=dest, raw=raw)


def _print_multiple_topics(topics, hprint, dest, raw):
    for i, topic in enumerate(topics):
        if i > 0:
            hprint("\n---\n")
        print_help(topic, dest=dest, raw=raw)


def find_special_topic_command(topic):
    """Searches for a command within a predefined set of special topics."""
    if topic is None:
        return None
    for keywords, command in SPECIAL_HELP_TOPICS_MAP.items():
        if topic in keywords:
            return command
    return None


def find_operator_topic_command(help_topics):
    """Searches for a command that matches a known operator."""
    return next((topic for topic in help_topics if topic.lower() in registry.operations), None)


def find_option_topic_command(help_topics):
    """Searches for a command that matches a known option."""
    known_options = [before_space(opt) for opt in registry.options]
    return next((topic for topic in help_topics if topic in known_options), None)


@register_help_topic(
    "help",
    title="pdftl help",
    desc="Get help",
    examples=[
        HelpExample(
            desc=(
                "Get all help. This is nice if you set `FORCE_COLORS=1` and pipe "
                "the output to `less -R`, with the complete command "
                "`FORCE_COLORS=1 pdftl help all | less -R`."
            ),
            cmd="help all",
        ),
        HelpExample(desc=("Get help topics tagged with `encryption`"), cmd="help tag:encryption"),
        HelpExample(
            desc=("Gather all examples from the various help topics"), cmd="help examples"
        ),
    ],
)
def _help_help_topic():
    """If a `help` argument is given, the remaining arguments are
    scanned for a keyword. This can be `tag:<tagname>`, or one of the
    operation names, or an option name, or a special help topic, or an
    alias. If a match is found, the help is printed. Tags are printed
    at the end of any help topics which have tags.

    By default, colors are used if printing directly to the
    terminal, and usually not in other situations (e.g., if
    the output is redirected). If the environment variable
    `FORCE_COLORS` is set, then colors should appear in all
    cases.

    The special help topic `all` is particularly interesting.

    """
