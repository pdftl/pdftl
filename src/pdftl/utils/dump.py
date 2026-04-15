# src/pdftl/utils/dump.py

from pdftl.exceptions import InvalidArgumentError


def get_json_flag(op_args, op_name, only_json_allowed=True):
    if only_json_allowed and len(op_args) > 1:
        raise InvalidArgumentError(f"Too many arguments for '{op_name}', at most one is allowed.")
    if op_args:
        if "json" in [x.lower().strip() for x in op_args]:
            return True

        raise InvalidArgumentError(
            f"Invalid '{op_name}' argument. "
            "Only valid argument is 'json', to select JSON output."
        )

    return False
