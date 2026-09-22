# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/cli/help.py

"""Help for CLI interface"""

import logging
import sys

from pdftl.cli.console import get_console
from pdftl.cli.constants import HELP_FLAGS
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
from pdftl.core.metadata import get_project_version
from pdftl.cli.whoami import PACKAGE, WHOAMI
from pdftl.core.core_types import HelpExample
from pdftl.core.registry import register_help_topic, registry
from pdftl.utils.string_utils import before_space

logger = logging.getLogger(__name__)

TAG_PREFIX = "tag:"
IMAGE_MOD_PREFIX = "mod:"

_HELP_RECURSION_DEPTH = 0


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
        cleaned_desc = str(long_desc).strip()
        hprint("\n## Details\n" + cleaned_desc)

    if examples := topic_data.get("examples", None):
        example_markdown = format_examples_block(examples)
        hprint(example_markdown)

    _print_help_trailer(hprint, topic_data, topic_name)


def _get_rtd_url(topic_name, topic_type, topic_data, caller) -> None | str:
    """Dynamically build the docs URL based on the caller namespace.

    e.g., "pdftl.operations.cat" -> category becomes \"operations\"
    """
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
    """Prints the main help screen using either the Fast Path (Rich)

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
        registry.image_modifiers.items(),
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
        for op, info in itertools.chain(
            registry.operations.items(), registry.options.items(), registry.image_modifiers.items()
        )
    }
    dispatch_table.update(
        {
            topic: (lambda hprint, t_data=data: _print_topic_help(hprint, t_data, t_data["title"]))
            for topic, data in registry.help_topics.items()
        }
    )
    dispatch_table["output_options"] = lambda hprint, *_: _print_output_options_help(hprint)
    dispatch_table["examples"] = lambda hprint, *_: _print_examples_help(hprint)
    return dispatch_table


def print_help(command=None, dest=None, raw=False):
    """Displays help information for the tool, a specific command, or a topic.

    Args:
        command (str, optional): The command or topic to get help for.
        dest (file-like object, optional): The destination to print to.
                                           Defaults to sys.stdout.
        raw (bool, optional): If True, output raw markdown/text instead of rendered Rich output.
    """
    global _HELP_RECURSION_DEPTH
    use_rich_console = not raw and (dest is None or dest is sys.stdout or dest is sys.stderr)

    # Check if we are printing to an interactive terminal, not a pipe or file
    is_tty = sys.stdout.isatty() if dest is None else (hasattr(dest, "isatty") and dest.isatty())

    # Disable auto-paging for defaults, version flags, or non-interactive pipes
    safe_command = str(command).lower() if command else None
    should_page = (
        command is not None and safe_command not in ("version", "--version", "-v") and is_tty
    )

    # Wrap only the outer-most call invocation inside the capture layout, if paging is allowed
    if use_rich_console and _HELP_RECURSION_DEPTH == 0 and should_page:
        _HELP_RECURSION_DEPTH += 1
        try:
            from pdftl.cli.help_render import page_captured_output

            with page_captured_output():
                _print_help_core(command, dest, raw)
        finally:
            _HELP_RECURSION_DEPTH = 0
    else:
        _HELP_RECURSION_DEPTH += 1
        try:
            _print_help_core(command, dest, raw)
        finally:
            _HELP_RECURSION_DEPTH = max(0, _HELP_RECURSION_DEPTH - 1)


def _print_help_core(command=None, dest=None, raw=False):
    """Internal core rendering routine logic."""
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
            "args",
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
        _print_help_core(topic, dest=dest, raw=raw)


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


def find_image_mod_topic_command(help_topics):
    """Searches for a command that matches a known option."""
    known_filters = [before_space(opt) for opt in registry.image_modifiers]
    return next(
        (
            topic.removeprefix(IMAGE_MOD_PREFIX)
            for topic in help_topics
            if topic.removeprefix(IMAGE_MOD_PREFIX) in known_filters
        ),
        None,
    )


def _resolve_topic_token(token):
    """Resolve a single raw CLI token to a help topic name, or None.

    Priority mirrors the original per-category search order: special
    topics, then operators, then options, then image modifiers.
    """
    if (cmd := find_special_topic_command(token.lower())) is not None:
        return cmd
    if (cmd := find_operator_topic_command([token])) is not None:
        return cmd
    if (cmd := find_option_topic_command([token])) is not None:
        return cmd
    if (cmd := find_image_mod_topic_command([token])) is not None:
        return cmd
    return None


def _stage_ranges(cli_args):
    """Split cli_args into (start, end) index ranges, one per '---'-delimited stage."""
    ranges = []
    start = 0
    for i, arg in enumerate(cli_args):
        if arg == "---":
            ranges.append((start, i - 1))
            start = i + 1
    ranges.append((start, len(cli_args) - 1))
    return ranges


def _stage_range_for_index(ranges, index):
    for start, end in ranges:
        if start <= index <= end:
            return start, end
    return None


def _is_valid_pipeline_done(cli_args, idx):
    """A 'DONE' token only resolves to the pipeline topic if it actually
    closes an EACH...DONE block. If a '---' stage boundary intervenes
    between it and the nearest preceding EACH, the block was already
    severed and DONE is not a resolvable pipeline candidate here."""
    for i in range(idx - 1, -1, -1):
        if cli_args[i] == "EACH":
            return True
        if cli_args[i] == "---":
            return False
    return False


def _nearest_topic(cli_args, candidate_indices, help_indices):
    """Find the topic among candidate_indices nearest to any help_indices.

    On an exact distance tie, a pipeline-keyword candidate (EACH/DONE/---)
    wins over a plain operator/option candidate. Any remaining tie is
    broken by earliest position in cli_args, so behavior stays
    deterministic for equidistant matches.
    """
    best_idx = None
    best_dist = None
    best_topic = None
    best_is_pipeline = False
    for idx in candidate_indices:
        token = cli_args[idx]
        if token in HELP_FLAGS:
            continue
        if token == "DONE" and not _is_valid_pipeline_done(cli_args, idx):
            continue
        topic = _resolve_topic_token(token)
        if topic is None:
            continue
        is_pipeline = topic == "pipeline"
        dist = min(abs(idx - h) for h in help_indices)
        if (
            best_dist is None
            or dist < best_dist
            or (dist == best_dist and is_pipeline and not best_is_pipeline)
            or (dist == best_dist and is_pipeline == best_is_pipeline and idx < best_idx)
        ):
            best_idx, best_dist, best_topic, best_is_pipeline = idx, dist, topic, is_pipeline
    return best_topic


def find_help_command(cli_args):
    """
    Determines the specific help command based on CLI arguments.

    Search order:
      1. tag: queries always win outright.
      2. The nearest recognized topic keyword to any 'help' occurrence,
         searched within that help's own '---'-delimited stage first.
      3. If nothing matched within a stage, the nearest topic keyword
         anywhere in the full argument list (ignoring stage boundaries).
      4. A legacy fallback: if a second help-flag token is itself a
         special topic name (e.g. 'help all', 'help help'), use that.
    """
    tag_queries = [arg for arg in cli_args if arg.startswith(TAG_PREFIX)]
    if tag_queries:
        return tag_queries[0]

    help_indices = [i for i, arg in enumerate(cli_args) if arg in HELP_FLAGS]
    if not help_indices:
        return None

    ranges = _stage_ranges(cli_args)
    same_stage_indices = set()
    for h in help_indices:
        stage_range = _stage_range_for_index(ranges, h)
        if stage_range:
            same_stage_indices.update(range(stage_range[0], stage_range[1] + 1))

    if topic := _nearest_topic(cli_args, sorted(same_stage_indices), help_indices):
        return topic

    if topic := _nearest_topic(cli_args, range(len(cli_args)), help_indices):
        return topic

    help_args = [arg for arg in cli_args if arg in HELP_FLAGS]
    if len(help_args) > 1:
        # help_args only ever contains tokens drawn from HELP_FLAGS, so a
        # second entry here is itself a help flag (e.g. "help help") --
        # it names the "help" topic directly.
        return "help"

    return None


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
    pass
