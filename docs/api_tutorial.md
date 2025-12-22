# Python API Guide

`pdftl` offers a robust Python API that allows you to integrate PDF manipulation capabilities directly into your applications. Unlike the CLI, which works with strings and file paths, the API is designed to work with Python objects and structured data.

## Two Ways to Play

There are two primary ways to interact with `pdftl`: the **Fluent Interface** (recommended for pipelines) and the **Functional Interface** (best for single operations).

### 1. The Fluent Interface (Recommended)

The Fluent API uses method chaining to build readable, multi-step processing pipelines. It automatically manages the passing of PDF objects between steps.

```python
from pdftl import pipeline

# 1. Open a PDF
# 2. Rotate all pages 90 degrees
# 3. Crop pages 1-5 to a 10pt margin
# 4. Save the result
(
    pipeline("input.pdf")
    .rotate(90)
    .crop("1-5(10pt)")
    .save("output.pdf")
)
```

You can also work with existing `pikepdf` objects:

```python
import pikepdf
from pdftl import pipeline

pdf = pikepdf.open("input.pdf")

# Apply transformations and save to a new file
# Note: The pipeline allows chaining, effectively handing the modified
# PDF object to the next step.
pipeline(pdf).add_text("1/Watermark/").save("watermarked.pdf")
```

#### Best Practices: Explicit Arguments

While `pdftl` allows flexible argument passing, it is **best practice to use Keyword Arguments** for complex operations like concatenating files. This makes your pipelines robust and unambiguous.

For operations that accept multiple inputs (like `cat`), use the `inputs` keyword. For simpler operations (like `stamp`), positional arguments are standard.

```python
# Unambiguous and robust
(
    pipeline("chapter1.pdf")
    .cat(inputs=["chapter2.pdf", "chapter3.pdf"])
    .stamp("watermark.pdf")
    .save("full_book.pdf")
)
```

### 2. The Functional Interface

If you need to perform a single, specific action—especially one that returns data (like `dump_data` or `dump_annots`)—the functional interface is often simpler.

Functions are available directly under the `pdftl` namespace.

```python
import pdftl

# Dump metadata
info = pdftl.dump_data(inputs=["report.pdf"])

print(f"Page Count: {info.pages}")
print(f"Metadata: {info.doc_info}")
```

---

## Return Values

By default, the API returns the result of the operation directly (unwrapping the internal `OpResult` container):

* **Modification commands** (like `rotate`, `crop`) return the modified `pikepdf.Pdf` object.
* **Extraction commands** (like `dump_data`, `dump_annots`) return the extracted data (dictionaries, lists, strings).

If an operation fails, it raises an exception (e.g., `pdftl.exceptions.OperationError`).

### Advanced: Full Result Objects

If you need access to the execution summary or want to check success flags explicitly without exceptions, you can request the full result object using `full_result=True`.

| Attribute | Type | Description |
| :--- | :--- | :--- |
| `success` | `bool` | `True` if the operation completed successfully. |
| `data` | `Any` | Structured data (dict, list, string) returned by info/dump commands. |
| `pdf` | `pikepdf.Pdf` | The processed PDF object (for manipulation commands). |
| `summary` | `str` | A human-readable summary of what happened. |

**Example: Handling Return Values**

```python
# Returns a list of dictionaries directly
annotations = pdftl.dump_annots("input.pdf")

for annot in annotations:
    print(f"Found annotation on page {annot['Page']}: {annot['Properties'].get('Subtype', 'Unknown')}")
```

## Advanced Features

### Simulating CLI Behavior (Hooks)

Sometimes you *want* the side effects of the CLI (like printing a formatted report to stdout or writing a file to disk) without writing the logic yourself. You can force this using the `run_cli_hook=True` argument.

```python
# This will write the formatted FDF file to disk, 
# just like running 'pdftl generate_fdf output form.fdf'
pdftl.generate_fdf(
    inputs=["form.pdf"], 
    output="data.fdf", 
    run_cli_hook=True
)
```

### Mixing Inputs

The API is smart about inputs. You can pass file paths, open `pikepdf.Pdf` objects, or a mix of both.

```python
import pikepdf
import pdftl

cover_page = pikepdf.open("cover.pdf")

# Merge an open PDF object with a file on disk
# Using 'inputs' keyword argument is recommended for clarity
pdftl.cat(
    inputs=[cover_page, "chapter1.pdf"], 
    output="book.pdf"
)
```