We welcome bug reports and other development contributions.

If you have an idea for a feature and you are unsure if it fits, open a [Discussion](https://github.com/pdftl/pdftl/discussions) before writing code.

## Reporting bugs

Please report bugs under [Issues](https://github.com/pdftl/pdftl/issues).
The most useful thing you can include is the exact command that triggered
the bug and, if possible, a PDF that reproduces it. Even a small synthetic
PDF that shows the same problem is far more useful than a description alone.

Run `pdftl --version` and include the output — it shows pdftl and
dependency versions which often matter. Also run the problematic command with the `--debug` flag and paste the output.

## Development setup

```bash
git clone https://github.com/pdftl/pdftl
cd pdftl
python -m venv .venv
source .venv/bin/activate  # on Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -n auto
```

## Running tests

```bash
pytest -n auto                  # full suite except pdftk-java tests
pytest vendor_tests/pdftk-java/ # pdftk-java tests only
pytest tests/operations/        # specific module
pytest -k test_cat              # specific test
pytest --cov=pdftl -n auto      # with coverage
```

## Code style

We use `ruff` for linting and formatting. Before submitting:

```bash
ruff format src tests
ruff check src --fix    # should be no warnings here
ruff check tests --fix  # ruff usually flags things in tests/ but --fix is still useful
```

## Adding an operation

1. Create `src/pdftl/operations/your_op.py`
2. Decorate with `@register_operation` — see any existing operation for the pattern
3. Add tests in `tests/operations/test_your_op.py`
4. Add an entry to `CHANGELOG.md` under `Unreleased`

The operation will be auto-discovered at startup — no registration file to update.


## What makes a good PR

- Tests for new behaviour
- An entry in CHANGELOG.md
- pdftk compatibility preserved where applicable — check COMPATIBILITY.md
- For new operations, at least one example in the `examples` metadata
