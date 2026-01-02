import inspect

import pytest

from pdftl.core.registry import registry
from pdftl.core.types import OpResult


def test_all_commands_return_op_result(clean_registry):
    """
    Architecture Test:
    Ensures that ALL registered operations have been migrated to the
    new pattern and explicitly annotate their return type as 'OpResult'.
    """

    legacy_commands = []
    updated_commands = []

    # Iterate over all registered operations
    for name, op_meta in registry.operations.items():
        # CORRECTED: Access the function via the .function attribute
        func = op_meta.function

        # Get type hints
        try:
            sig = inspect.signature(func)
            return_type = sig.return_annotation
        except Exception:
            legacy_commands.append(f"{name} (Could not inspect signature)")
            continue

        # Check if return annotation is explicitly OpResult
        is_op_result = False

        if return_type is OpResult:
            is_op_result = True
        elif isinstance(return_type, str) and "OpResult" in return_type:
            is_op_result = True

        ret_string = f"{name} (Return type: {return_type})"
        if is_op_result:
            updated_commands.append(ret_string)
        else:
            legacy_commands.append(ret_string)

    # Assert that the list of legacy commands is empty
    if legacy_commands:
        pytest.fail(
            f"The following {len(legacy_commands)} commands have not been migrated "
            + "to return 'OpResult':\n\n- "
            + "\n- ".join(legacy_commands)
            + "\n\n"
            + f"The following {len(updated_commands)} commands HAVE been migrated "
            + "to return 'OpResult':\n\n- "
            + "\n- ".join(updated_commands)
        )
