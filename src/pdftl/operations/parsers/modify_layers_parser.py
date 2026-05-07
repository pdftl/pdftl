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


def parse_modify_layers_rules(args) -> tuple[dict, dict, set]:
    """Parses arguments into explicit id_rules, name_rules, and a set of default actions."""
    rules_by_id: dict[int, set] = {}
    rules_by_name: dict[str, set] = {}
    default_actions: set[str] = set()

    i = 0
    while i < len(args):
        action = args[i].lower()
        if action not in VALID_ACTIONS:
            raise InvalidArgumentError(
                f"modify_layers: unknown action '{action}'. Valid actions are {VALID_ACTIONS}"
            )

        # Check if next argument exists and is NOT another action keyword
        if i + 1 < len(args) and args[i + 1].lower() not in VALID_ACTIONS:
            target_str = str(args[i + 1])
            i += 2
            _process_target(action, target_str, rules_by_id, rules_by_name, default_actions)
        else:
            # No target provided (or next token is another action) -> Applies to ALL
            default_actions.add(action)
            i += 1

    # Ensure we always have at least a fallback "keep" if no defaults were set
    if not default_actions:
        default_actions.add("keep")

    return rules_by_id, rules_by_name, default_actions


def _process_target(action, target_str, rules_by_id, rules_by_name, default_actions):
    if "=" in target_str:
        k, v = parse_keyval_token(
            target_str,
            allowed_keys=["id", "name"],
            lowercase_values=False,
            context="modify_layers",
        )
        if k == "id":
            try:
                # Accumulate actions into a set
                rules_by_id.setdefault(int(v), set()).add(action)
            except ValueError:
                raise InvalidArgumentError(f"modify_layers: id must be an integer, got '{v}'.")
        else:
            rules_by_name.setdefault(v, set()).add(action)
    elif target_str.lower() == "all":
        default_actions.add(action)
    else:
        rules_by_name.setdefault(target_str, set()).add(action)
