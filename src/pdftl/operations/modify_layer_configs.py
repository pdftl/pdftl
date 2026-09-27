# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Manage named layer configurations (/OCProperties /Configs)"""

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError, MissingArgumentError
from pdftl.operations.modify_layers import resolve_ocg_actions
from pdftl.operations.parsers.modify_layers_parser import parse_modify_layers_rules
from pdftl.utils.ocg import array_entry, dict_entry, dict_items, pdf_items

_MODIFY_LAYER_CONFIGS_LONG_DESC = """

The `modify_layer_configs` operation manages named layer configurations:
presets of layer visibility and locking, stored alongside the document's
default configuration. Use `dump_layers` to list them.

### Subcommands

* `add <name> [<action> [<target>]]...`: Creates a configuration named
  `<name>`, starting from the default configuration and applying the actions.
  With no actions, this snapshots the default configuration.
* `update <name> [<action> [<target>]]...`: Applies the actions to an existing
  configuration.
* `delete <name>`: Deletes a configuration.
* `rename <old> <new>`: Renames a configuration.
* `set_default <name>`: Copies a configuration into the document's default,
  the state used when the document is opened. The configuration itself stays
  in the list. The previous default is saved as a configuration under its own
  name, or `Previous default` if it had none, with ` (2)`, ` (3)`, ... added
  if that name is taken. Running `set_default` on the saved configuration
  restores the previous layer states, but both configurations remain in the
  list and the default takes the saved configuration's name.

### Actions

Actions and targets follow `modify_layers`: `show`, `hide`, `lock` and
`unlock`, each followed by at most one target (`name=<string>`,
`id=<integer>`, `all`, or a bare name). Repeat the action to target several
layers. An action with no target applies to all layers. An `id=` target
takes precedence over a `name=` target, and either takes precedence over
`all`. Conflicting actions for the same target, such as `show A hide A`, are
an error.

Configuration names must be unique for `update`, `delete`, `rename` and
`set_default` to find them.

"""

_TEST_SETUP_LAYERED_PDF = {"copy_pdfs": {"modify_layer_configs.pdf": "in.pdf"}}

_MODIFY_LAYER_CONFIGS_EXAMPLES = [
    {
        "cmd": (
            "in.pdf modify_layer_configs add Services hide all show HVAC show Plumbing"
            " output out.pdf"
        ),
        "desc": "Save a configuration showing only the 'HVAC' and 'Plumbing' layers.",
        "test_setup": _TEST_SETUP_LAYERED_PDF,
    },
    {
        "cmd": "in.pdf modify_layer_configs add Original output out.pdf",
        "desc": "Snapshot the current default configuration.",
        "test_setup": _TEST_SETUP_LAYERED_PDF,
    },
    {
        "cmd": 'in.pdf modify_layer_configs update "Old Draft" lock Architecture output out.pdf',
        "desc": "Lock the 'Architecture' layer in the 'Old Draft' configuration.",
        "test_setup": _TEST_SETUP_LAYERED_PDF,
    },
    {
        "cmd": 'in.pdf modify_layer_configs set_default "M&E View" output out.pdf',
        "desc": "Open the document with the 'M&E View' configuration.",
        "test_setup": _TEST_SETUP_LAYERED_PDF,
    },
    {
        "cmd": 'in.pdf modify_layer_configs rename "Old Draft" Draft output out.pdf',
        "desc": "Rename the 'Old Draft' configuration to 'Draft'.",
        "test_setup": _TEST_SETUP_LAYERED_PDF,
    },
    {
        "cmd": 'in.pdf modify_layer_configs delete "Old Draft" output out.pdf',
        "desc": "Delete the 'Old Draft' configuration.",
        "test_setup": _TEST_SETUP_LAYERED_PDF,
    },
]

CONFIG_ACTIONS = {"show", "hide", "lock", "unlock"}
PREVIOUS_DEFAULT_NAME = "Previous default"
CREATOR = "pdftl"


@register_operation(
    "modify_layer_configs",
    tags=["layers", "modify", "visibility"],
    type="single input operation",
    desc="Add, update, delete or promote named layer configurations",
    long_desc=_MODIFY_LAYER_CONFIGS_LONG_DESC,
    usage=(
        "<input> modify_layer_configs <subcommand> <name> [<arg>...] output <output> [<option>...]"
    ),
    examples=_MODIFY_LAYER_CONFIGS_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {"output_file": c.OUTPUT}),
)
def modify_layer_configs(pdf, *args, output_file=None) -> OpResult:
    cli_args = list(args[0] if len(args) == 1 and isinstance(args[0], (list, tuple)) else args)
    if len(cli_args) < 2:
        raise MissingArgumentError(
            "modify_layer_configs: expected a subcommand and a configuration name."
        )
    subcommand, name, rest = cli_args[0].lower(), cli_args[1], cli_args[2:]
    handler = _SUBCOMMANDS.get(subcommand)
    if handler is None:
        raise InvalidArgumentError(
            f"modify_layer_configs: unknown subcommand '{subcommand}'."
            f" Valid subcommands are {sorted(_SUBCOMMANDS)}."
        )
    ocprops = _require_layers(pdf)
    message = handler(pdf, ocprops, name, rest)
    return OpResult(success=True, data=message, meta={c.META_OUTPUT_FILE: output_file})


def _add(pdf, ocprops, name, rest):
    import pikepdf

    if _find_config_indices(ocprops, name):
        raise InvalidArgumentError(f"modify_layer_configs: configuration '{name}' already exists.")
    d_dict = ocprops.D
    state = _read_state(d_dict)
    _apply_actions(state, ocprops.OCGs, rest)
    config = pikepdf.Dictionary(Name=pikepdf.String(name), Creator=pikepdf.String(CREATOR))
    for key in ("/Intent", "/AS", "/ListMode"):
        if key in d_dict:
            config[key] = _copy(d_dict[key])
    _write_state(config, state, ocprops.OCGs)
    if "/Configs" not in ocprops:
        ocprops.Configs = pikepdf.Array()
    ocprops.Configs.append(config)
    return f"Added layer configuration '{name}'."


def _update(pdf, ocprops, name, rest):
    config = _detached_config(ocprops, _find_unique_config(ocprops, name))
    state = _read_state(config)
    _apply_actions(state, ocprops.OCGs, rest)
    _write_state(config, state, ocprops.OCGs)
    return f"Updated layer configuration '{name}'."


def _delete(pdf, ocprops, name, rest):
    _reject_extra_args("delete", rest)
    index = _find_unique_config(ocprops, name)
    del ocprops.Configs[index]
    if len(ocprops.Configs) == 0:
        del ocprops["/Configs"]
    return f"Deleted layer configuration '{name}'."


def _rename(pdf, ocprops, name, rest):
    import pikepdf

    if len(rest) != 1:
        raise InvalidArgumentError("modify_layer_configs: rename expects <old> <new>.")
    new_name = rest[0]
    index = _find_unique_config(ocprops, name)
    if new_name != name and _find_config_indices(ocprops, new_name):
        raise InvalidArgumentError(
            f"modify_layer_configs: configuration '{new_name}' already exists."
        )
    _detached_config(ocprops, index).Name = pikepdf.String(new_name)
    return f"Renamed layer configuration '{name}' to '{new_name}'."


def _set_default(pdf, ocprops, name, rest):
    import pikepdf

    _reject_extra_args("set_default", rest)
    index = _find_unique_config(ocprops, name)
    config = ocprops.Configs[index]
    old_d = ocprops.D
    ocgs = list(ocprops.OCGs)

    visible = _visible_ids(_read_state(old_d), ocgs)
    visible = _visible_ids(_read_state(config), ocgs, visible)

    previous = _copy(old_d)
    previous.Name = pikepdf.String(_unused_name(ocprops, str(old_d.get("/Name", ""))))
    for key in ("/Order", "/RBGroups"):
        if key not in previous:
            previous[key] = pikepdf.Array()

    new_d = _copy(old_d)
    for key in ("/Name", "/Creator", "/Order", "/RBGroups", "/AS", "/ListMode"):
        if key in config:
            new_d[key] = _copy(config[key])
        elif key in ("/Name", "/Creator") and key in new_d:
            del new_d[key]
    # /D's /Intent may only be View, its default, so the config's is not copied.
    for key in ("/ON", "/OFF", "/Locked", "/Intent"):
        if key in new_d:
            del new_d[key]
    new_d.BaseState = pikepdf.Name.ON
    off = [ocg for ocg in ocgs if _obj_id(ocg) and _obj_id(ocg) not in visible]
    if off:
        new_d.OFF = pikepdf.Array(off)
    if config.get("/Locked"):
        new_d.Locked = _copy(config.Locked)

    _pin_inherited_entries(ocprops, old_d, new_d)
    ocprops.D = new_d
    ocprops.Configs.append(previous)
    return (
        f"Set layer configuration '{name}' as the default."
        f" Previous default saved as '{previous.Name}'."
    )


_SUBCOMMANDS = {
    "add": _add,
    "update": _update,
    "delete": _delete,
    "rename": _rename,
    "set_default": _set_default,
}


def _require_layers(pdf):
    """/OCProperties with /OCGs, /Configs as arrays and /D as a dictionary."""
    import pikepdf

    ocprops = pdf.Root.get("/OCProperties")
    if not isinstance(ocprops, pikepdf.Dictionary) or not dict_items(ocprops.get("/OCGs")):
        raise InvalidArgumentError("modify_layer_configs: this document has no layers.")
    array_entry(ocprops, "/OCGs")
    dict_entry(ocprops, "/D")
    if "/Configs" in ocprops:
        array_entry(ocprops, "/Configs")
    return ocprops


def _detached_config(ocprops, index):
    """The config at `index`, copied first if it is the same object as /D."""
    config = ocprops.Configs[index]
    d_dict = ocprops.D
    if config.is_indirect and d_dict.is_indirect and config.objgen == d_dict.objgen:
        config = _copy(config)
        ocprops.Configs[index] = config
    return config


def _pin_inherited_entries(ocprops, old_d, new_d):
    """Configs that inherit /Order or /RBGroups from /D keep the old value."""
    import pikepdf

    for key in ("/Order", "/RBGroups"):
        old_value = old_d.get(key)
        # Compares layer references, not contents: == would equate same-named layers.
        new_value = new_d.get(key, pikepdf.Array())
        if old_value is not None and old_value.unparse(resolved=True) == new_value.unparse(
            resolved=True
        ):
            continue
        for cfg in _config_dicts(ocprops):
            if key not in cfg:
                cfg[key] = _copy(old_value) if old_value is not None else pikepdf.Array()


def _config_dicts(ocprops):
    import pikepdf

    return [cfg for cfg in ocprops.get("/Configs", []) if isinstance(cfg, pikepdf.Dictionary)]


def _reject_extra_args(subcommand, rest):
    if rest:
        raise InvalidArgumentError(
            f"modify_layer_configs: {subcommand} takes a single name, got extra arguments {rest}."
        )


def _find_config_indices(ocprops, name) -> list[int]:
    import pikepdf

    return [
        i
        for i, cfg in enumerate(ocprops.get("/Configs", []))
        if isinstance(cfg, pikepdf.Dictionary) and "/Name" in cfg and str(cfg.Name) == name
    ]


def _find_unique_config(ocprops, name) -> int:
    indices = _find_config_indices(ocprops, name)
    if not indices:
        raise InvalidArgumentError(f"modify_layer_configs: no configuration named '{name}'.")
    if len(indices) > 1:
        raise InvalidArgumentError(
            f"modify_layer_configs: {len(indices)} configurations are named '{name}'."
        )
    return indices[0]


def _unused_name(ocprops, preferred) -> str:
    base = preferred or PREVIOUS_DEFAULT_NAME
    candidate, n = base, 2
    while _find_config_indices(ocprops, candidate):
        candidate, n = f"{base} ({n})", n + 1
    return candidate


def _obj_id(obj) -> int:
    return obj.objgen[0] if getattr(obj, "is_indirect", False) else 0


def _read_state(config) -> dict:
    """Base state name plus explicit per-OCG visibility and lock sets.

    An OCG wrongly listed in both /ON and /OFF counts as ON."""
    on = {_obj_id(o) for o in pdf_items(config.get("/ON"))} - {0}
    return {
        "base": str(config.get("/BaseState", "/ON")),
        "on": on,
        "off": {_obj_id(o) for o in pdf_items(config.get("/OFF"))} - {0} - on,
        "locked": {_obj_id(o) for o in pdf_items(config.get("/Locked"))} - {0},
    }


def _visible_ids(state, ocgs, unchanged_visible=None) -> set:
    """OCG ids visible once `state` is applied; `/Unchanged` keeps `unchanged_visible`."""
    all_ids = {_obj_id(o) for o in ocgs} - {0}
    if state["base"] == "/OFF":
        visible = set()
    elif state["base"] == "/Unchanged" and unchanged_visible is not None:
        visible = set(unchanged_visible)
    else:
        visible = set(all_ids)
    return ((visible - state["off"]) | state["on"]) & all_ids


def _apply_actions(state, ocgs, rest):
    """Direct OCG dictionaries (spec-invalid in /OCGs) are ignored."""
    ocgs = [o for o in ocgs if _obj_id(o)]
    rules_by_id, rules_by_name, default_actions = parse_modify_layers_rules(
        rest, context="modify_layer_configs"
    )
    # The parser adds "keep" as a fallback; only a typed one is an error.
    typed_keep = {"keep"} & {str(arg).lower() for arg in rest}
    default_actions = default_actions - {"keep"}
    invalid = (
        typed_keep | default_actions | set().union(*rules_by_id.values(), *rules_by_name.values())
    ) - CONFIG_ACTIONS
    if invalid:
        raise InvalidArgumentError(
            f"modify_layer_configs: actions {sorted(invalid)} do not apply to configurations."
            " Use modify_layers instead."
        )
    if "show" in default_actions or "hide" in default_actions:
        state["base"] = "/ON" if "show" in default_actions else "/OFF"
        state["on"], state["off"] = set(), set()
    if "lock" in default_actions:
        state["locked"] = {_obj_id(o) for o in ocgs}
    elif "unlock" in default_actions:
        state["locked"] = set()
    for ocg in ocgs:
        actions = resolve_ocg_actions(ocg, default_actions, rules_by_id, rules_by_name)
        _apply_ocg_actions(state, _obj_id(ocg), actions)


def _apply_ocg_actions(state, obj_id, actions):
    if "show" in actions:
        state["off"].discard(obj_id)
        state["on"].add(obj_id)
    elif "hide" in actions:
        state["on"].discard(obj_id)
        state["off"].add(obj_id)
    if "lock" in actions:
        state["locked"].add(obj_id)
    elif "unlock" in actions:
        state["locked"].discard(obj_id)


def _write_state(config, state, ocgs):
    import pikepdf

    def _array(ids):
        return pikepdf.Array([o for o in ocgs if _obj_id(o) in ids])

    config.BaseState = pikepdf.Name(state["base"])
    for key, ids, redundant_base in (
        ("/ON", state["on"], "/ON"),
        ("/OFF", state["off"], "/OFF"),
        ("/Locked", state["locked"], None),
    ):
        if ids and state["base"] != redundant_base:
            config[key] = _array(ids)
        elif key in config:
            del config[key]


def _copy(obj):
    """Direct copy of a container; nested indirect objects are shared."""
    import pikepdf

    if isinstance(obj, pikepdf.Array):
        return pikepdf.Array([_copy_nested(item) for item in obj])
    if isinstance(obj, pikepdf.Dictionary):
        return pikepdf.Dictionary({key: _copy_nested(value) for key, value in obj.items()})
    return obj


def _copy_nested(obj):
    return obj if getattr(obj, "is_indirect", False) else _copy(obj)
