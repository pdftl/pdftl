# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/stage_model.py

"""Immutable Pipeline editing and command-line text. Qt-free."""

from __future__ import annotations

import string
from dataclasses import replace
from pathlib import Path

import pdftl.core.constants as c
from pdftl.core.registry import registry
from pdftl.gui import shell_style
from pdftl.gui.interfaces import InputFile, Pipeline, Stage
from pdftl.gui.op_policy import is_source

MASK = "PROMPT"

SAVE_ONLY_TAGS = {"encryption", "signatures"}
"""Option tags meaning the option prompts or needs real credentials
(encryption passwords, signing keys): only ever applied at Save, never on a
live preview run. See `is_save_only_option`."""


def _option_tags() -> dict[str, set[str]]:
    from pdftl import registry_init

    registry_init.initialize_registry()
    return {
        name.split(" ")[0]: set(getattr(opt, "tags", None) or ())
        for name, opt in registry.options.items()
    }


def output_password_options() -> frozenset[str]:
    """Encryption options that take a value: that value is a password."""
    from pdftl import registry_init

    registry_init.initialize_registry()
    return frozenset(
        name.split(" ")[0]
        for name, opt in registry.options.items()
        if "encryption" in (getattr(opt, "tags", None) or ())
        and getattr(opt, "type", "") == "one mandatory argument"
    )


def output_option_names() -> set[str]:
    """Every registered save/output option's bare keyword (`output` excluded)."""
    return {name for name in _option_tags() if name != c.OUTPUT}


def is_save_only_option(name: str) -> bool:
    """Whether `name` (e.g. `owner_pw`, `sign_key`) only makes sense at Save time."""
    return bool(_option_tags().get(name, set()) & SAVE_ONLY_TAGS)


def option_tokens(options: dict) -> list[str]:
    """Render a `parse_options_and_specs`-style `{option: value}` dict as CLI tokens."""
    tokens: list[str] = []
    for key, value in options.items():
        if key == c.OUTPUT:
            continue
        if isinstance(value, (set, frozenset, list, tuple)):
            tokens.append(key)
            tokens.extend(sorted(value) if isinstance(value, (set, frozenset)) else value)
        elif value is True:
            tokens.append(key)
        else:
            tokens.extend([key, str(value)])
    return tokens


def _parse_raw_options(text: str) -> dict:
    """Tokenize and parse Output-box text into a `{option: value}` dict,
    `output` included if present. Raises ValueError for unbalanced quotes,
    a `---` separator, or a token that isn't a recognized option."""
    from pdftl import registry_init
    from pdftl.cli.parser import parse_options_and_specs
    from pdftl.exceptions import UserCommandLineError

    registry_init.initialize_registry()
    tokens = shell_style.split(text, shell_style.default_style())
    if "---" in tokens:
        raise ValueError("'---' separates stages; output options only follow the last one")
    try:
        specs, options = parse_options_and_specs(tokens)
    except UserCommandLineError as exc:
        raise ValueError(str(exc)) from exc
    if specs:
        raise ValueError(f"not a recognized output option: {specs[0]!r}")
    return options


def parse_output_options(text: str) -> dict:
    """Parse Output-box text (as typed) into a `{option: value}` dict.

    Raises ValueError for unbalanced quotes, a `---` separator, `output`
    itself (use `extract_output_clause` for text that may still contain it),
    or a token that isn't a recognized save/output option.
    """
    options = _parse_raw_options(text)
    if c.OUTPUT in options:
        raise ValueError("'output' isn't allowed here; Save picks the output path")
    return options


def extract_output_clause(text: str) -> tuple[str, Path | None]:
    """Pull a leading `output <path>` clause out of Output-box text, if present.

    Typing `output <path>` there sets the save target directly rather than
    erroring, mirroring what the CLI's own `output` keyword means. Returns
    (remaining option text, the path or None). Raises the same ValueErrors as
    `parse_output_options`, `output` itself excepted.
    """
    options = _parse_raw_options(text)
    target = options.pop(c.OUTPUT, None)
    remaining = shell_style.join(option_tokens(options), shell_style.default_style())
    return remaining, Path(target) if target is not None else None


def word_at(text: str, pos: int) -> str:
    """The whitespace-delimited word touching `pos`, or `''` between/outside words."""
    start = pos
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    end = pos
    while end < len(text) and not text[end].isspace():
        end += 1
    return text[start:end]


def _option_entries() -> dict:
    from pdftl import registry_init

    registry_init.initialize_registry()
    return {
        name.split(" ")[0]: opt
        for name, opt in registry.options.items()
        if name.split(" ")[0] != c.OUTPUT
    }


def _option_section(name: str, opt: object) -> str:
    parts = [f"### `{name}`"]
    desc = (getattr(opt, "desc", "") or "").strip()
    if desc:
        parts.append(desc)
    long_desc = (getattr(opt, "long_desc", "") or "").strip()
    if long_desc:
        parts.append(long_desc)
    return "\n\n".join(parts)


def _ordered(names: list[str], highlight: str | None) -> list[str]:
    """`names`, with `highlight` moved to the front when it's one of them."""
    if highlight in names:
        return [highlight, *(n for n in names if n != highlight)]
    return names


def output_options_help_markdown(highlight: str | None = None) -> str:
    """Markdown help for every output option, grouped like `pdftl help output_options`.

    `highlight` (an option name the cursor is on) is moved to the front of
    whichever group it's in, so it's the first thing shown.
    """
    entries = _option_entries()
    general = _ordered(sorted(n for n in entries if not is_save_only_option(n)), highlight)
    save_only = _ordered(sorted(n for n in entries if is_save_only_option(n)), highlight)
    parts = [
        "# Output options",
        (
            "Typed in the Output box below the last stage; applied after it, exactly like"
            " typing them on the `pdftl` command line."
        ),
    ]
    if general:
        parts.append("## General")
        parts += [_option_section(n, entries[n]) for n in general]
    if save_only:
        parts.append("## Encryption & signing (Save only)")
        parts += [_option_section(n, entries[n]) for n in save_only]
    return "\n\n".join(parts)


def add_input(p: Pipeline, path: Path, password: str | None = None) -> Pipeline:
    """Append an input under the lowest free handle. ValueError when A-Z are all used."""
    used = {f.handle for f in p.inputs}
    free = [h for h in string.ascii_uppercase if h not in used]
    if not free:
        raise ValueError("all 26 input handles A-Z are in use")
    return replace(p, inputs=(*p.inputs, InputFile(free[0], Path(path), password)))


def remove_input(p: Pipeline, handle: str) -> Pipeline:
    """Drop the input with `handle`; stages naming it are left alone. KeyError if absent."""
    if handle not in {f.handle for f in p.inputs}:
        raise KeyError(handle)
    return replace(p, inputs=tuple(f for f in p.inputs if f.handle != handle))


def _check_index(p: Pipeline, index: int, extra: int = 0) -> None:
    if not 0 <= index < len(p.stages) + extra:
        raise IndexError(f"stage index {index} out of range")


def insert_stage(p: Pipeline, index: int, stage: Stage) -> Pipeline:
    """Insert `stage` before `index`; `index == len(stages)` appends."""
    _check_index(p, index, extra=1)
    return replace(p, stages=(*p.stages[:index], stage, *p.stages[index:]))


def remove_stage(p: Pipeline, index: int) -> Pipeline:
    _check_index(p, index)
    return replace(p, stages=p.stages[:index] + p.stages[index + 1 :])


def replace_stage(p: Pipeline, index: int, stage: Stage) -> Pipeline:
    _check_index(p, index)
    return replace(p, stages=(*p.stages[:index], stage, *p.stages[index + 1 :]))


def move_stage(p: Pipeline, index: int, delta: int) -> tuple[Pipeline, int]:
    """Move a stage by `delta`, clamped to the ends. Returns the pipeline and its new index."""
    _check_index(p, index)
    target = max(0, min(index + delta, len(p.stages) - 1))
    rest = p.stages[:index] + p.stages[index + 1 :]
    return replace(p, stages=(*rest[:target], p.stages[index], *rest[target:])), target


def _problems_in(stage: Stage, index: int, handles: set[str]) -> list[str]:
    try:
        tokens = stage.tokens()
    except ValueError as exc:
        return [f"Cannot parse arguments: {exc}"]
    found = []
    if "output" in tokens:
        found.append("Remove 'output': Save chooses where output goes")
    names = output_option_names()
    found += [
        f"'{token}' is an output option; put it in the Output box"
        for token in tokens
        if token.lower() in names
    ]
    if index and is_source(stage.op):
        found.append(f"'{stage.op}' makes a new PDF, so it must be the first stage")
    if index == 0 and stage.inputs:
        found.append("The first stage always reads every input file")
    elif undefined := [h for h in stage.inputs if h not in handles]:
        found.append(f"Undefined input handle(s): {', '.join(undefined)}")
    return found


def stage_problems(p: Pipeline) -> list[tuple[int, str]]:
    """Static problems found without running anything, as (stage index, message).

    An output option's hint fires on every stage, not just the last: a real
    `pdftl` run only honors the final stage's options, so one typed earlier
    is silently dropped by `to_argv`/`to_cli` even though the GUI engine's
    isolated per-stage runs would apply it there too.
    """
    handles = {f.handle for f in p.inputs}
    return [
        (i, msg) for i, stage in enumerate(p.stages) for msg in _problems_in(stage, i, handles)
    ]


MAX_STEMS = 3


def _file_safe(text: str) -> str:
    return "".join(c for c in text if c.isalnum() or c in "_-.")


def output_stem(p: Pipeline, index: int) -> str:
    """A file name stem for stage `index`'s output: the input files it derives
    from (at most MAX_STEMS, then a count), then its operation."""
    by_handle = {f.handle: f for f in p.inputs}
    handles = [] if is_source(p.stages[0].op) else list(by_handle)
    for stage in p.stages[1 : index + 1]:
        handles += [h for h in stage.inputs if h in by_handle and h not in handles]
    stems = [_file_safe(Path(by_handle[h].path).stem) for h in handles]
    if len(stems) > MAX_STEMS:
        stems = [*stems[:MAX_STEMS], f"{len(stems) - MAX_STEMS}more"]
    op = _file_safe(p.stages[index].op) or "stage"
    return "-".join([*filter(None, stems), op])


def _split_options(text: str) -> list[str] | None:
    try:
        return shell_style.split(text, shell_style.default_style())
    except ValueError:
        return None


def _replace_password_values(text: str, value_for) -> str:
    """`text` with each owner_pw/user_pw value `v` of option `o` replaced by
    `value_for(o, v)`; unchanged if it doesn't split or nothing changes."""
    tokens = _split_options(text)
    if tokens is None:
        return text
    secret = output_password_options()
    new = [
        value_for(tokens[i - 1], t) if i and tokens[i - 1] in secret else t
        for i, t in enumerate(tokens)
    ]
    return text if new == tokens else shell_style.join(new, shell_style.default_style())


def output_passwords(text: str) -> dict[str, str]:
    """The owner_pw/user_pw values in Output-box text; {} if it doesn't split."""
    tokens = _split_options(text) or []
    secret = output_password_options()
    return {t: tokens[i + 1] for i, t in enumerate(tokens[:-1]) if t in secret}


def fill_output_passwords(text: str, values: dict[str, str]) -> str:
    """`text` with each PROMPT owner_pw/user_pw value taken from `values`, where present."""
    return _replace_password_values(
        text, lambda option, value: values.get(option, value) if value == MASK else value
    )


def with_masked_passwords(p: Pipeline) -> Pipeline:
    """Replace every input password, and owner_pw/user_pw in the output options,
    with PROMPT, pdftl's keyword for asking interactively."""
    masked = tuple(f if f.password is None else replace(f, password=MASK) for f in p.inputs)
    options = _replace_password_values(p.output_options, lambda _option, _value: MASK)
    return replace(p, inputs=masked, output_options=options)


def to_cli(
    p: Pipeline,
    output: Path | None = None,
    *,
    style: str | None = None,
    mask_passwords: bool = True,
) -> str:
    """The pipeline as a `pdftl ...` command line, quoted for a POSIX or Windows shell.

    `style` is `posix`, `windows`, or None (default) for the platform's own
    style (`shell_style.default_style()`). Masked passwords become PROMPT,
    pdftl's keyword for asking interactively.
    """
    if style is None:
        style = shell_style.default_style()
    if style not in ("posix", "windows"):
        raise ValueError(f"unknown style {style!r}")
    if mask_passwords:
        p = with_masked_passwords(p)
    return " ".join(["pdftl", *(shell_style.quote(arg, style) for arg in p.to_argv(output))])
