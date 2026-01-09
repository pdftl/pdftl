# tests/core/test_magic_string_audit.py
import ast
import os

import pytest

import pdftl.core.constants as c

# Define the list of "Forbidden" magic strings (those that MUST be constants)
# We filter to ensure we only include strings, avoiding unhashable types like odict_keys
CONTRACT_KEYS = {
    getattr(c, attr)
    for attr in dir(c)
    if not attr.startswith("_") and isinstance(getattr(c, attr), str)
}


def get_command_files():
    """Finds all python files in the commands directory."""
    cmd_dir = os.path.join("src", "pdftl", "operations")
    if not os.path.exists(cmd_dir):
        return []
    return [os.path.join(cmd_dir, f) for f in os.listdir(cmd_dir) if f.endswith(".py")]


@pytest.mark.parametrize("filepath", get_command_files())
def test_ensure_no_magic_strings_in_registration(filepath):
    """
    Parses command files and ensures @register does not use raw strings
    for keys that exist in pdftl.core.constants.
    """
    with open(filepath) as f:
        try:
            tree = ast.parse(f.read())
        except SyntaxError:
            pytest.skip(f"Syntax error in {filepath}")

    errors = []

    for node in ast.walk(tree):
        # We only care about function definitions
        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                # Check if it's the @register decorator
                # Handles both @register and @register(...)
                is_register = False
                if (
                    isinstance(decorator, ast.Call)
                    and getattr(decorator.func, "id", "") == "register"
                ):
                    is_register = True
                elif isinstance(decorator, ast.Name) and decorator.id == "register":
                    continue  # Naked decorator, no args to check

                if is_register:
                    # Look for string literals inside the decorator arguments
                    for sub_node in ast.walk(decorator):
                        if isinstance(sub_node, ast.Constant) and isinstance(sub_node.value, str):
                            val = sub_node.value

                            # If the string literal matches a contract key, it should have been a constant
                            if val in CONTRACT_KEYS:
                                # Exception: the first positional argument (command name) is allowed to be a string
                                if (
                                    decorator.args
                                    and isinstance(decorator.args[0], ast.Constant)
                                    and val == decorator.args[0].value
                                ):
                                    continue

                                errors.append(
                                    f"Found magic string '{val}' in @register at {filepath}:{sub_node.lineno}. "
                                    f"Use pdftl.core.constants instead."
                                )

    if errors:
        pytest.fail("\n".join(errors))


def test_constants_completeness():
    """Verify that core contract strings are properly represented as constants."""
    # Ensure the list isn't empty due to a filtering bug
    assert "inputs" in CONTRACT_KEYS
    assert "operation_args" in CONTRACT_KEYS
    assert "opened_pdfs" in CONTRACT_KEYS
