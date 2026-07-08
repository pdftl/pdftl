#!/usr/bin/env bash

# Exit immediately if a pipeline returns a non-zero status
set -e

# Ensure at least one argument is provided
if [ $# -eq 0 ]; then
    echo "Usage: $0 <files/paths/dirs...> [pytest_options...]"
    exit 1
fi

TEST_PATHS=()
COV_ARGS=()
EXTRA_ARGS=(-rf)

cd "${PROJECT_BASE:-$(cat "${HOME}/.config/run_test/base.txt")}"

# Loop through all arguments passed to the script
for INPUT in "$@"; do
    # If the argument starts with a hyphen, it's a pytest option
    if [[ "$INPUT" == -* ]]; then
        EXTRA_ARGS+=("$INPUT")
        continue
    fi

    # 1. Strip extensions, known prefixes, and trailing slashes
    CLEANED="${INPUT%.py}"           # Strip .py if present
    CLEANED="${CLEANED#src/pdftl/}"  # Strip src/pdftl/ if present
    CLEANED="${CLEANED#tests/}"      # Strip tests/ if present
    CLEANED="${CLEANED%/}"           # Strip trailing slash if present

    # 2. PRIORITY: Check if the cleaned path is actually a directory
    if [ -n "$CLEANED" ] && { [ -d "src/pdftl/$CLEANED" ] || [ -d "tests/$CLEANED" ]; }; then
        TEST_PATH="tests/$CLEANED"
        MOD_DIR="${CLEANED//\//.}"
        COV_MOD="pdftl.${MOD_DIR}"

        TEST_PATHS+=("$TEST_PATH")
        COV_ARGS+=("--cov=$COV_MOD")
        continue
    fi

    # 3. File search logic (if it wasn't a directory)
    DIR_PART=$(dirname "$CLEANED")
    FILE_PART=$(basename "$CLEANED")
    CORE_NAME="${FILE_PART#test_}"   # Strip test_ if present

    FOUND_PATH=""

    if [ "$DIR_PART" = "." ]; then
        # Search by exact filename globally
        [ -d "src/pdftl" ] && FOUND_PATH=$(find src/pdftl -type f -name "${CORE_NAME}.py" 2>/dev/null | head -n 1)
        if [ -z "$FOUND_PATH" ]; then
            [ -d "tests" ] && FOUND_PATH=$(find tests -type f -name "test_${CORE_NAME}.py" 2>/dev/null | head -n 1)
        fi
    else
        # Search by matching the partial path provided
        [ -d "src/pdftl" ] && FOUND_PATH=$(find src/pdftl -type f -path "*/${DIR_PART}/${CORE_NAME}.py" 2>/dev/null | head -n 1)
        if [ -z "$FOUND_PATH" ]; then
            [ -d "tests" ] && FOUND_PATH=$(find tests -type f -path "*/${DIR_PART}/test_${CORE_NAME}.py" 2>/dev/null | head -n 1)
        fi
    fi

    # 4. Extract the exact relative directory based on search results
    if [ -n "$FOUND_PATH" ]; then
        CLEANED_PATH="${FOUND_PATH#src/pdftl/}"
        CLEANED_PATH="${CLEANED_PATH#tests/}"
        EXACT_DIR=$(dirname "$CLEANED_PATH")
    else
        # Fallback if no files exist yet
        EXACT_DIR="$DIR_PART"
        echo "Warning: '$INPUT' not found in search. Assuming relative path is '$EXACT_DIR'."
    fi

    # 5. Construct target test path and module dot-notation for coverage
    if [ "$EXACT_DIR" = "." ]; then
        TEST_PATH="tests/test_${CORE_NAME}.py"
        COV_MOD="pdftl.${CORE_NAME}"
    else
        TEST_PATH="tests/${EXACT_DIR}/test_${CORE_NAME}.py"
        # Replace slashes with dots for the module path
        MOD_DIR="${EXACT_DIR//\//.}"
        COV_MOD="pdftl.${MOD_DIR}.${CORE_NAME}"
    fi

    TEST_PATHS+=("$TEST_PATH")
    COV_ARGS+=("--cov=$COV_MOD")
done

# 6. Print and execute the single massive command
echo "Constructing pytest run..."
echo "Running: pytest ${TEST_PATHS[*]} ${COV_ARGS[*]} --cov-report=term-missing ${EXTRA_ARGS[*]}"
echo "----------------------------------------------------------------------"

pytest "${TEST_PATHS[@]}" "${COV_ARGS[@]}" --cov-report=term-missing "${EXTRA_ARGS[@]}"
