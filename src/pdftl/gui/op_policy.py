# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/op_policy.py

"""Classify pdftl operations for use inside a GUI pipeline.

Qt-free. Reads `pdftl.core.registry.registry` (populated by
`pdftl.registry_init.initialize_registry`) and derives each operation's
`OpKind` from its registry entry alone, so plugin operations need no GUI
changes:

- a source operation not tagged `source` (it runs a program, as `gui` and
  `server` do) is BLOCKED;
- one that takes `is_last_stage` hands a PDF on unless it is last: PDF;
- `skip_pipeline_save` with an `output_dir` or `output_pattern` argument, or
  the `export` tag, writes files of its own: SIDE_EFFECT;
- otherwise `skip_pipeline_save` means DATA, and anything else is PDF.
"""

from __future__ import annotations

from pdftl.gui.interfaces import OpInfo, OpKind

_FILE_WRITING_ARGS = {"output_dir", "output_pattern"}

_BLOCKED_REASON = "Runs a program of its own rather than making a PDF; cannot be a pipeline stage."
_LAST_STAGE_REASON = (
    "Passes a PDF down the pipeline; the files it writes when last in a"
    " command line (such as page images) need the command line."
)
_SIDE_EFFECT_REASON = "Writes files of its own into a scratch folder, not the pipeline's output."
_DATA_REASON = "Produces text or data, not a PDF."


def _context_args(entry: object) -> set[str]:
    """The names in the entry's `args` keyword map (e.g. `output_dir`)."""
    args = getattr(entry, "args", None)
    return set(args[1]) if args and len(args) > 1 else set()


def _example_pair(example: object) -> tuple[str, str]:
    if isinstance(example, dict):
        return example["cmd"], example.get("desc", "")
    return example.cmd, example.desc  # type: ignore[attr-defined]


def _kind_and_reason(entry: object) -> tuple[OpKind, str]:
    tags = set(getattr(entry, "tags", None) or ())
    args = _context_args(entry)
    skip = getattr(entry, "skip_pipeline_save", False)
    if getattr(entry, "type", None) == "source operation" and "source" not in tags:
        return OpKind.BLOCKED, _BLOCKED_REASON
    if "is_last_stage" in args:
        return OpKind.PDF, _LAST_STAGE_REASON
    if skip and (args & _FILE_WRITING_ARGS or "export" in tags):
        return OpKind.SIDE_EFFECT, _SIDE_EFFECT_REASON
    if skip:
        return OpKind.DATA, _DATA_REASON
    return OpKind.PDF, ""


def _text(entry: object, field: str) -> str:
    """A help field as text; some registry entries hold a lazy object with `__str__`."""
    value = getattr(entry, field, None)
    return "" if value is None else str(value)


def _op_info(name: str, entry: object) -> OpInfo:
    kind, reason = _kind_and_reason(entry)
    examples = tuple(_example_pair(ex) for ex in getattr(entry, "examples", None) or ())
    return OpInfo(
        name=name,
        kind=kind,
        desc=_text(entry, "desc"),
        usage=_text(entry, "usage"),
        long_desc=_text(entry, "long_desc"),
        examples=examples,
        reason=reason,
    )


def all_ops() -> list[OpInfo]:
    """Every registered pdftl operation, sorted by name.

    Includes BLOCKED operations; callers that build a pipeline-stage picker
    should filter those out themselves.
    """
    from pdftl import registry_init
    from pdftl.core.registry import registry

    registry_init.initialize_registry()
    return [_op_info(name, entry) for name, entry in sorted(registry.operations.items())]


def classify(name: str) -> OpInfo:
    """OpInfo for one operation. Raises KeyError for an unknown name."""
    from pdftl import registry_init
    from pdftl.core.registry import registry

    registry_init.initialize_registry()
    if name not in registry.operations:
        raise KeyError(name)
    return _op_info(name, registry.operations[name])


def is_source(name: str) -> bool:
    """Whether `name` makes a PDF from scratch and takes no input files, as `create` does."""
    from pdftl import registry_init
    from pdftl.core.registry import registry

    registry_init.initialize_registry()
    return getattr(registry.operations.get(name), "type", None) == "source operation"
