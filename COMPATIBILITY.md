# Compatibililty notes for pdftl

pdftl aspires to be CLI compatible with pdftk and this is an attempt to explain what this might mean, and to note any caveats.

## What is CLI compatibility?

The vague idea is clear: `s/pdftk/pdftl/g` should Just Work.

So the questions are:

1. Which pdftk implementation are we targeting?

2. What is Just Working?

We don't really know the answers. Our approach is to take existing integration tests for various pdftk implementations and to check whether or not pdftl passes them.

Getting pdftl to pass them all is work in progress.

## Known compatibility bugs

### Not yet implemented

- `replacement_font`

### Upstream bugs

- pikepdf cannot yet deal properly with dictionary keys unless they are valid utf-8. So some weird but valid PDF files may break some features of pdftl.

- pikepdf and duplicate keys FIXME
```
E               File "/path/to/site-packages/pikepdf/form.py", line 119, in items
E                 raise RuntimeError(f'Multiple fields with same name: {name}')
E             RuntimeError: Multiple fields with same name: %
```

## Caveats

- Order of lines in `dump_data` etc may differ from pdftk, where not semantically important.
