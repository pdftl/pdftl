from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.keyval_parser import parse_keyval_token


def parse_modify_layers_rules(args) -> tuple[dict, dict, str]:
    """Parses arguments into explicit id_rules, name_rules, and a default action."""
    rules_by_id = {}
    rules_by_name = {}
    default_action = "keep"

    i = 0
    while i < len(args):
        action = args[i].lower()
        if action not in ("strip", "merge", "keep"):
            raise InvalidArgumentError(
                f"modify_layers: unknown action '{action}'. Expected 'strip', 'merge', or 'keep'."
            )

        # Check if next argument exists and is NOT another action keyword
        if i + 1 < len(args) and args[i + 1].lower() not in ("strip", "merge", "keep"):
            target_str = str(args[i + 1])
            i += 2

            if "=" in target_str:
                k, v = parse_keyval_token(
                    target_str,
                    allowed_keys=["id", "name"],
                    lowercase_values=False,
                    context="modify_layers",
                )
                if k == "id":
                    try:
                        rules_by_id[int(v)] = action
                    except ValueError:
                        raise InvalidArgumentError(
                            f"modify_layers: id must be an integer, got '{v}'."
                        )
                else:
                    rules_by_name[v] = action
            elif target_str.lower() == "all":
                default_action = action
            else:
                rules_by_name[target_str] = action
        else:
            # No target provided (or next token is another action) -> Applies to ALL
            default_action = action
            i += 1

    return rules_by_id, rules_by_name, default_action
