# tools/bash_setup.sh
#
# Source this file: source tools/bash_setup.sh

# -----------------------------
# Helpers
# -----------------------------

fail() {
    echo "pdftl-env: $*" >&2
    return 1 2>/dev/null || exit 1
}

path_prepend() {
    case ":$PATH:" in
        *":$1:"*) ;;
        *) export PATH="$1:$PATH" ;;
    esac
}

# -----------------------------
# Resolve BASE_DIR
# -----------------------------

resolve_base_dir() {
    if [[ -n "${TL:-}" ]]; then
        echo "$TL"
        return
    fi

    local dir="$PWD"

    while [[ "$dir" != "/" ]]; do
        if [[ "$(basename "$dir")" == "pdftl" ]]; then
            echo "$dir"
            return
        fi
        dir="$(dirname "$dir")"
    done

    fail "could not locate 'pdftl' directory from $PWD"
}

BASE_DIR="$(resolve_base_dir)"
export BASE_DIR

# -----------------------------
# Single source of truth
# -----------------------------

declare -A TLVARS=(
    [TL]="$BASE_DIR"
    [TLS]="$BASE_DIR/src/pdftl"
    [TLO]="$BASE_DIR/src/pdftl/operations"
    [TLU]="$BASE_DIR/src/pdftl/utils"
    [TLT]="$BASE_DIR/tests"
    [TLTO]="$BASE_DIR/tests/operations"
    [TLTU]="$BASE_DIR/tests/utils"
)

# Export everything from one place
for k in "${!TLVARS[@]}"; do
    export "$k=${TLVARS[$k]}"
done

# -----------------------------
# PATH
# -----------------------------

path_prepend "$BASE_DIR/tools"

# -----------------------------
# Alias
# -----------------------------

alias t="run_test.sh"

# -----------------------------
# Output (derived from same source)
# -----------------------------

print_status() {
    cat <<EOF
pdftl-env loaded (BASE_DIR=$BASE_DIR)

aliases:
  t => run_test.sh

variables:
EOF

    for k in "${!TLVARS[@]}"; do
        printf "  %s=%s\n" "$k" "${TLVARS[$k]}"
    done | sort
}

print_status
