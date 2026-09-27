from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.keyval_parser import parse_keyval_token

VALID_ACTIONS = {
    "strip",
    "merge",
    "keep",  # Structural
    "show",
    "hide",
    "lock",
    "unlock",  # State
    "print",
    "noprint",
    "screen",
    "noscreen",  # Usage
}

CONFLICTING_ACTIONS = (
    frozenset({"strip", "merge", "keep"}),
    frozenset({"show", "hide"}),
    frozenset({"lock", "unlock"}),
    frozenset({"print", "noprint"}),
    frozenset({"screen", "noscreen"}),
)


def override_actions(base: set, specific: set) -> set:
    """Actions in `specific` replace any conflicting ones in `base`."""
    result = set(specific)
    for action in base:
        if not any(action in group and group & specific for group in CONFLICTING_ACTIONS):
            result.add(action)
    return result


def _check_conflicts(actions: set, target: str, context: str):
    for group in CONFLICTING_ACTIONS:
        clash = group & actions
        if len(clash) > 1:
            raise InvalidArgumentError(
                f"{context}: conflicting actions {sorted(clash)} for {target}."
            )


def parse_modify_layers_rules(args, context="modify_layers") -> tuple[dict, dict, set]:
    """Parses arguments into explicit id_rules, name_rules, and a set of default actions."""
    rules_by_id: dict[int, set] = {}
    rules_by_name: dict[str, set] = {}
    default_actions: set[str] = set()

    i = 0
    while i < len(args):
        action = args[i].lower()
        if action not in VALID_ACTIONS:
            raise InvalidArgumentError(
                f"{context}: unknown action '{action}'. Valid actions are {VALID_ACTIONS}"
            )

        # Check if next argument exists and is NOT another action keyword
        if i + 1 < len(args) and args[i + 1].lower() not in VALID_ACTIONS:
            target_str = str(args[i + 1])
            i += 2
            _process_target(
                action, target_str, rules_by_id, rules_by_name, default_actions, context
            )
        else:
            # No target provided (or next token is another action) -> Applies to ALL
            default_actions.add(action)
            i += 1

    _check_conflicts(default_actions, "all", context)
    for obj_id, actions in rules_by_id.items():
        _check_conflicts(actions, f"id={obj_id}", context)
    for name, actions in rules_by_name.items():
        _check_conflicts(actions, f"name={name}", context)

    # Ensure we always have at least a fallback "keep" if no defaults were set
    if not default_actions:
        default_actions.add("keep")

    return rules_by_id, rules_by_name, default_actions


def _process_target(action, target_str, rules_by_id, rules_by_name, default_actions, context):
    if "=" in target_str:
        k, v = parse_keyval_token(
            target_str,
            allowed_keys=["id", "name"],
            lowercase_values=False,
            context=context,
        )
        if k == "id":
            try:
                # Accumulate actions into a set
                rules_by_id.setdefault(int(v), set()).add(action)
            except ValueError:
                raise InvalidArgumentError(f"{context}: id must be an integer, got '{v}'.")
        else:
            rules_by_name.setdefault(v, set()).add(action)
    elif target_str.lower() == "all":
        default_actions.add(action)
    else:
        rules_by_name.setdefault(target_str, set()).add(action)
