# pdftl

<img align="right" width="100" src="https://raw.githubusercontent.com/pdftl/pdftl/main/.github/assets/pdftl.svg">

[![PyPI](https://img.shields.io/pypi/v/pdftl)](https://pypi.org/project/pdftl/)
[![CI](https://github.com/pdftl/pdftl/actions/workflows/ci.yml/badge.svg)](https://github.com/pdftl/pdftl/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/pdftl/pdftl/graph/badge.svg)](https://codecov.io/gh/pdftl/pdftl)
[![Documentation Status](https://readthedocs.org/projects/pdftl/badge/?version=latest)](https://pdftl.readthedocs.io/en/latest/?badge=latest)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/pdftl)](https://pypi.org/project/pdftl/)
[![Static Badge](https://img.shields.io/badge/platform-linux%20%7C%20windows%20%7C%20macos-lightgrey)](#quick-start)

**pdftl** ("PDF tackle") is a command-line interface for PDF manipulation written in Python.
It provides a [`pdftk`-compatible interface](https://github.com/pdftl/pdftl/blob/main/COMPATIBILITY.md)
for standard operations while adding extended capabilities for text extraction, image modification, and document analysis.

The core of `pdftl` relies on [`pikepdf`](https://github.com/pikepdf/pikepdf)
(a Python binding for [`qpdf`](https://github.com/qpdf/qpdf)).

For online documentation: [_Read the Docs_][3].

## Installation

`pdftl` requires Python 3.10 or later and runs on Windows, macOS and Linux.

Because `pdftl` is a command-line tool, the recommended installation method is via [`pipx`](https://pipx.pypa.io/), which installs the application into an isolated environment so its dependencies don't conflict with your system Python.

To install the core package (covers standard `pdftk` functionality):

```bash
pipx install pdftl
```

Many of the extended features require additional Python dependencies. To install the tool with all optional features enabled:

```bash
pipx install "pdftl[full]"
```

Some features also require system software such as `java`.

<details>
<summary><b>Alternative installation methods</b></summary>

- You can also use standard `pip` if you prefer to manage your own virtual environments.
- If you only need specific features you can specify e.g., `pipx install "pdftl[signing,optimize-images]"`. Features are listed under `[project.optional-dependencies]` in [pyproject.toml](https://github.com/pdftl/pdftl/blob/main/pyproject.toml).

</details>

## Feature overview

### Standard operations

* **Combine and organize:** [`create`](https://pdftl.readthedocs.io/en/latest/operations/create.html), [`cat`](https://pdftl.readthedocs.io/en/latest/operations/cat.html), [`shuffle`](https://pdftl.readthedocs.io/en/latest/operations/shuffle.html), [`insert`](https://pdftl.readthedocs.io/en/latest/operations/insert.html), and [`move`](https://pdftl.readthedocs.io/en/latest/operations/move.html).
* **Split:** [`burst`](https://pdftl.readthedocs.io/en/latest/operations/burst.html), [`delete`](https://pdftl.readthedocs.io/en/latest/operations/delete.html), or [`delete_blank`](https://pdftl.readthedocs.io/en/latest/operations/delete_blank.html).
* **Metadata:** [`dump_data`](https://pdftl.readthedocs.io/en/latest/operations/dump_data.html), [`update_info`](https://pdftl.readthedocs.io/en/latest/operations/update_info.html), [`set`](https://pdftl.readthedocs.io/en/latest/operations/set.html).
* **Attachments:** [`attach_files`](https://pdftl.readthedocs.io/en/latest/operations/attach_files.html), [`unpack_files`](https://pdftl.readthedocs.io/en/latest/operations/unpack_files.html), [`dump_files`](https://pdftl.readthedocs.io/en/latest/operations/dump_files.html), [`delete_attachments`](https://pdftl.readthedocs.io/en/latest/operations/delete_attachments.html).
* **Bookmarks and links:** [`dump_bookmarks`](https://pdftl.readthedocs.io/en/latest/operations/dump_bookmarks.html), [`add_bookmarks`](https://pdftl.readthedocs.io/en/latest/operations/add_bookmarks.html), [`delete_bookmarks`](https://pdftl.readthedocs.io/en/latest/operations/delete_bookmarks.html), [`update_bookmarks`](https://pdftl.readthedocs.io/en/latest/operations/update_bookmarks.html), and [`dump_dests`](https://pdftl.readthedocs.io/en/latest/operations/dump_dests.html).
* **Watermarking:** [`stamp`](https://pdftl.readthedocs.io/en/latest/operations/stamp.html) / [`background`](https://pdftl.readthedocs.io/en/latest/operations/background.html), [`multistamp`](https://pdftl.readthedocs.io/en/latest/operations/multistamp.html) / [`multibackground`](https://pdftl.readthedocs.io/en/latest/operations/multibackground.html).

### Geometry and splitting

* **Geometry:** [`rotate`](https://pdftl.readthedocs.io/en/latest/operations/rotate.html) or [`zoom`](https://pdftl.readthedocs.io/en/latest/operations/rotate.html).
* **Clip and crop:** [`crop`](https://pdftl.readthedocs.io/en/latest/operations/crop.html) to margins or standard paper sizes, or [`clip`](https://pdftl.readthedocs.io/en/latest/operations/clip.html) content outside a given region.
* **Chop:** [`chop`](https://pdftl.readthedocs.io/en/latest/operations/chop.html) pages into grids or rows.
* **Layout:** Shift, scale, and spin content with [`place`](https://pdftl.readthedocs.io/en/latest/operations/place.html), or automatically [`deskew`](https://pdftl.readthedocs.io/en/latest/operations/deskew.html).
* **Montage:** [`montage`](https://pdftl.readthedocs.io/en/latest/operations/montage.html) multiple pages onto a grid layout.
* **Booklet:** Create a printable [`booklet`](https://pdftl.readthedocs.io/en/latest/operations/booklet.html).

### Text, forms and annotations

* **Search, redaction and comparison:** [`grep`](https://pdftl.readthedocs.io/en/latest/operations/grep.html) and [`redact`](https://pdftl.readthedocs.io/en/latest/operations/redact.html) and [`diff_text`](https://pdftl.readthedocs.io/en/latest/operations/diff_text.html).
* **Extraction:** [`dump_text`](https://pdftl.readthedocs.io/en/latest/operations/dump_text.html), [`dump_tables`](https://pdftl.readthedocs.io/en/latest/operations/dump_tables.html), and [`dump_fonts`](https://pdftl.readthedocs.io/en/latest/operations/dump_fonts.html).
* **Forms:** [`fill_form`](https://pdftl.readthedocs.io/en/latest/operations/fill_form.html), [`generate_fdf`](https://pdftl.readthedocs.io/en/latest/operations/generate_fdf.html), [`dump_data_fields`](https://pdftl.readthedocs.io/en/latest/operations/dump_data_fields.html) and [`stamp_fields`](https://pdftl.readthedocs.io/en/latest/operations/stamp_fields.html).
* **Annotations:** [`modify_annots`](https://pdftl.readthedocs.io/en/latest/operations/modify_annots.html), [`delete_annots`](https://pdftl.readthedocs.io/en/latest/operations/delete_annots.html), [`dump_annots`](https://pdftl.readthedocs.io/en/latest/operations/dump_annots.html), [`dump_data_annots`](https://pdftl.readthedocs.io/en/latest/operations/dump_data_annots.html), and [`highlight`](https://pdftl.readthedocs.io/en/latest/operations/highlight.html).
* **Actions and scripts:** [`dump_actions`](https://pdftl.readthedocs.io/en/latest/operations/dump_actions.html) and [`delete_actions`](https://pdftl.readthedocs.io/en/latest/operations/delete_actions.html).
* **Accessibility and structure:** [`tag`](https://pdftl.readthedocs.io/en/latest/operations/tag.html) for auto-tagging, and [`dump_tags`](https://pdftl.readthedocs.io/en/latest/operations/dump_tags.html) to inspect the structure tree.

### Security

* **Decryption:** [`input_pw`](https://pdftl.readthedocs.io/en/latest/general/input.html).
* **Encryption:** [`owner_pw`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#owner-pw-pw), [`user_pw`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#user-pw-pw), [`encrypt_aes256`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#encrypt-aes256), and [`allow`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#allow-perm). Inspect with [`dump_encryption`](https://pdftl.readthedocs.io/en/latest/operations/dump_encryption.html).
* **Signatures:** [`sign_key`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#sign-key-file), [`sign_cert`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#sign-cert-file), and [`dump_signatures`](https://pdftl.readthedocs.io/en/latest/operations/dump_signatures.html).

### Images and Vectors

* **Information:** [`dump_images`](https://pdftl.readthedocs.io/en/latest/operations/dump_images.html) and [`dump_colorspaces`](https://pdftl.readthedocs.io/en/latest/operations/dump_colorspaces.html).
* **Conversion:** [`render`](https://pdftl.readthedocs.io/en/latest/operations/render.html) pages as images.
* **Optimization:** [`optimize_images`](https://pdftl.readthedocs.io/en/latest/operations/optimize_images.html), [`delete_images`](https://pdftl.readthedocs.io/en/latest/operations/delete_images.html), [`resample_images`](https://pdftl.readthedocs.io/en/latest/operations/resample_images.html).
* **Editing:** [`recolor_images`](https://pdftl.readthedocs.io/en/latest/operations/recolor_images.html), [`modify_images`](https://pdftl.readthedocs.io/en/latest/operations/modify_images.html), [`add_images`](https://pdftl.readthedocs.io/en/latest/operations/add_images.html), or [`export_images`](https://pdftl.readthedocs.io/en/latest/operations/export_images.html), edit externally and then [`import_images`](https://pdftl.readthedocs.io/en/latest/operations/import_images.html).
* **Vectors:** [`simplify_vectors`](https://pdftl.readthedocs.io/en/latest/operations/simplify_vectors.html) and [`recolor_vectors`](https://pdftl.readthedocs.io/en/latest/operations/recolor_vectors.html).
* **Barcodes:** Generate and place on pages with [`barcode`](https://pdftl.readthedocs.io/en/latest/operations/barcode.html).

### Advanced

* **Content streams:** [`replace`](https://pdftl.readthedocs.io/en/latest/operations/replace.html) parts of content streams, [`inject`](https://pdftl.readthedocs.io/en/latest/operations/inject.html) PDF operators, or inspect with [`dump_streams`](https://pdftl.readthedocs.io/en/latest/operations/dump_streams.html), edit and import with [`import_streams`](https://pdftl.readthedocs.io/en/latest/operations/import_streams.html).
* **Selective content deletion:** [`excise`](https://pdftl.readthedocs.io/en/latest/operations/excise.html) removes page content (images, vector paths, and text glyphs) inside a given rectangle, or alternatively, outside that rectangle.
* **Fonts:** [`export_fonts`](https://pdftl.readthedocs.io/en/latest/operations/export_fonts.html), edit as needed and then [`import_fonts`](https://pdftl.readthedocs.io/en/latest/operations/import_fonts.html). And [`embed_fonts`](https://pdftl.readthedocs.io/en/latest/operations/embed_fonts.html), or [`subset_fonts`](https://pdftl.readthedocs.io/en/latest/operations/subset_fonts.html).
* **Dynamic text:** [`add_text`](https://pdftl.readthedocs.io/en/latest/operations/add_text.html) for Bates stamping, page numbers, etc., and [`style_text`](https://pdftl.readthedocs.io/en/latest/operations/style_text.html) to change appearance.
* **Cleanup:** [`normalize`](https://pdftl.readthedocs.io/en/latest/operations/normalize.html) and [`linearize`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#linearize).
* **Layers (OCGs):** [`dump_layers`](https://pdftl.readthedocs.io/en/latest/operations/dump_layers.html) and [`modify_layers`](https://pdftl.readthedocs.io/en/latest/operations/modify_layers.html).
* **Presentations:** Remove slide transition frames with [`unpause`](https://pdftl.readthedocs.io/en/latest/operations/unpause.html).
* **Plugins:** Write custom operations in Python or use [`mutate_content`](https://pdftl.readthedocs.io/en/latest/operations/mutate_content.html).

## Examples

For additional examples, run `pdftl help examples`.

### Concatenation

```bash
# Merge two files
pdftl in1.pdf in2.pdf cat output combined.pdf

# Now with in2.pdf zoomed in
pdftl A=in1.pdf B=in2.pdf cat A Bz1 output combined2.pdf
```

### Geometry

```bash
# Take pages 1-5, rotate them 90 degrees East, and crop to A4
pdftl in.pdf cat 1-5east --- crop A4 output out.pdf
```

### Pipelining

You can chain operations without intermediate files using `---`:

```bash
# Burst a file, but rotate and stamp every page first
pdftl in.pdf rotate south \
  --- stamp watermark.pdf \
  --- burst output page_%04d.pdf
```

```bash
# Merge, crop to letter paper, rotate the last page, and output with encryption
pdftl A=a.pdf B=b.pdf cat A1-5 B2-end \
  --- crop '4-8,12(letter)' \
  --- rotate endright \
  output out.pdf owner_pw foo user_pw bar encrypt_aes256
```

### Forms and metadata

```bash
# Fill a form and flatten it (make it non-editable)
pdftl form.pdf fill_form data.fdf flatten output signed.pdf
```

### Modify annotations

```bash
# Change all Highlight annotations on odd pages to Red
pdftl docs.pdf modify_annots "odd/Highlight(C=[1 0 0])" output red_notes.pdf
```

### Modify content

```bash
# Add a watermark
pdftl in.pdf stamp watermark.pdf output marked1.pdf
```

```bash
# Add a semi-transparent red watermark on odd pages
pdftl in.pdf add_text 'odd/YOUR AD HERE/(position=mid-center, font=Helvetica-Bold, size=72, rotate=45, color=1 0 0 0.5)' output with_ads.pdf
```

```bash
# Add Bates numbering starting at 000121
# Result: DEF-000121, DEF-000122, ...
pdftl in.pdf \
  add_text "/DEF-{page+120:06d}/(position=bottom-center, offset-y=10)" \
  output bates.pdf
```

```bash
# Content stream replacement with regular expressions
# Change black to red
pdftl in.pdf replace '/0 0 0 (RG|rg)/1 0 0 \1/' output redder.pdf
```

## Python API

While `pdftl` is primarily a CLI tool, it also exposes a Python API for integrating PDF workflows into your scripts. It supports both a Functional interface (similar to the CLI) and a Fluent interface (for method chaining).

```python
from pdftl import pipeline

# Chain operations fluently without saving intermediate files
(
    pipeline("input.pdf")
    .rotate("right")
    .stamp("watermark.pdf")
    .save("output.pdf")
)
```

See the [**API Tutorial**][4] for more details.

A simple [`server`](https://pdftl.readthedocs.io/en/latest/operations/server.html) interface to API is provided; [try it here](https://pdftl.onrender.com/builder).

## Operations and options

| Operation                                                                                               | Description                                                     |
|---------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------|
| [`add_bookmarks`](https://pdftl.readthedocs.io/en/latest/operations/add_bookmarks.html)                 | Add top-level bookmarks                                         |
| [`add_images`](https://pdftl.readthedocs.io/en/latest/operations/add_images.html)                       | Stamp user-specified images onto PDF pages                      |
| [`add_text`](https://pdftl.readthedocs.io/en/latest/operations/add_text.html)                           | Add user-specified text strings to PDF pages                    |
| [`attach_files`](https://pdftl.readthedocs.io/en/latest/operations/attach_files.html)                   | Attach files to the output PDF                                  |
| [`background`](https://pdftl.readthedocs.io/en/latest/operations/background.html)                       | Use a 1-page PDF as the background for each page                |
| [`barcode`](https://pdftl.readthedocs.io/en/latest/operations/barcode.html)                             | Generate and add a barcode to pages                             |
| [`booklet`](https://pdftl.readthedocs.io/en/latest/operations/booklet.html)                             | Impose pages into printable booklet signatures                  |
| [`burst`](https://pdftl.readthedocs.io/en/latest/operations/burst.html)                                 | Split a single PDF into multiple files                          |
| [`cat`](https://pdftl.readthedocs.io/en/latest/operations/cat.html)                                     | Concatenate pages from input PDFs into a new PDF                |
| [`chop`](https://pdftl.readthedocs.io/en/latest/operations/chop.html)                                   | Chop pages into multiple smaller pieces                         |
| [`clip`](https://pdftl.readthedocs.io/en/latest/operations/clip.html)                                   | Clip page content to a rectangle                                |
| [`create`](https://pdftl.readthedocs.io/en/latest/operations/create.html)                               | Create a new PDF                                                |
| [`crop`](https://pdftl.readthedocs.io/en/latest/operations/crop.html)                                   | Crop pages to a rectangle                                       |
| [`delete`](https://pdftl.readthedocs.io/en/latest/operations/delete.html)                               | Delete pages from an input PDF                                  |
| [`delete_actions`](https://pdftl.readthedocs.io/en/latest/operations/delete_actions.html)               | Delete action info                                              |
| [`delete_annots`](https://pdftl.readthedocs.io/en/latest/operations/delete_annots.html)                 | Delete annotation info                                          |
| [`delete_attachments`](https://pdftl.readthedocs.io/en/latest/operations/delete_attachments.html)       | Delete file attachments based on criteria                       |
| [`delete_blank`](https://pdftl.readthedocs.io/en/latest/operations/delete_blank.html)                   | Delete blank or near-blank pages                                |
| [`delete_bookmarks`](https://pdftl.readthedocs.io/en/latest/operations/delete_bookmarks.html)           | Delete bookmarks                                                |
| [`delete_images`](https://pdftl.readthedocs.io/en/latest/operations/delete_images.html)                 | Delete images                                                   |
| [`deskew`](https://pdftl.readthedocs.io/en/latest/operations/deskew.html)                               | Automatically detect and correct document skew                  |
| [`diff_text`](https://pdftl.readthedocs.io/en/latest/operations/diff_text.html)                         | Diff the text content of two PDFs and output bounding boxes     |
| [`dump_actions`](https://pdftl.readthedocs.io/en/latest/operations/dump_actions.html)                   | Dump action info                                                |
| [`dump_annots`](https://pdftl.readthedocs.io/en/latest/operations/dump_annots.html)                     | Dump annotation info                                            |
| [`dump_bookmarks`](https://pdftl.readthedocs.io/en/latest/operations/dump_bookmarks.html)               | Extract PDF bookmarks into YAML or JSON                         |
| [`dump_colorspaces`](https://pdftl.readthedocs.io/en/latest/operations/dump_colorspaces.html)           | Report color spaces used                                        |
| [`dump_data`](https://pdftl.readthedocs.io/en/latest/operations/dump_data.html)                         | Metadata, page and bookmark info (XML-escaped)                  |
| [`dump_data_annots`](https://pdftl.readthedocs.io/en/latest/operations/dump_data_annots.html)           | Dump annotation info in pdftk style                             |
| [`dump_data_fields`](https://pdftl.readthedocs.io/en/latest/operations/dump_data_fields.html)           | Print PDF form field data with XML-style escaping               |
| [`dump_data_fields_utf8`](https://pdftl.readthedocs.io/en/latest/operations/dump_data_fields_utf8.html) | Print PDF form field data in UTF-8                              |
| [`dump_data_utf8`](https://pdftl.readthedocs.io/en/latest/operations/dump_data_utf8.html)               | Metadata, page and bookmark info (in UTF-8)                     |
| [`dump_dests`](https://pdftl.readthedocs.io/en/latest/operations/dump_dests.html)                       | Print PDF named destinations data to the console                |
| [`dump_encryption`](https://pdftl.readthedocs.io/en/latest/operations/dump_encryption.html)             | Print PDF encryption details and permissions                    |
| [`dump_files`](https://pdftl.readthedocs.io/en/latest/operations/dump_files.html)                       | List file attachments                                           |
| [`dump_fonts`](https://pdftl.readthedocs.io/en/latest/operations/dump_fonts.html)                       | Extract font metadata                                           |
| [`dump_images`](https://pdftl.readthedocs.io/en/latest/operations/dump_images.html)                     | Extract PDF embedded image metadata to JSON                     |
| [`dump_layers`](https://pdftl.readthedocs.io/en/latest/operations/dump_layers.html)                     | Dump layer info (JSON)                                          |
| [`dump_signatures`](https://pdftl.readthedocs.io/en/latest/operations/dump_signatures.html)             | List and validate digital signatures                            |
| [`dump_streams`](https://pdftl.readthedocs.io/en/latest/operations/dump_streams.html)                   | Dump page content streams as seen by `replace`                  |
| [`dump_tables`](https://pdftl.readthedocs.io/en/latest/operations/dump_tables.html)                     | Extract tables to JSON, CSV, or Markdown                        |
| [`dump_tags`](https://pdftl.readthedocs.io/en/latest/operations/dump_tags.html)                         | Inspect the PDF structure tree and reading order                |
| [`dump_text`](https://pdftl.readthedocs.io/en/latest/operations/dump_text.html)                         | Print PDF text data to the console or a file                    |
| [`embed_fonts`](https://pdftl.readthedocs.io/en/latest/operations/embed_fonts.html)                     | Automatically locate and embed missing system fonts             |
| [`excise`](https://pdftl.readthedocs.io/en/latest/operations/excise.html)                               | Delete page content inside or outside a rectangle              |
| [`export_fonts`](https://pdftl.readthedocs.io/en/latest/operations/export_fonts.html)                   | Export fonts and a JSON manifest for external editing           |
| [`export_images`](https://pdftl.readthedocs.io/en/latest/operations/export_images.html)                 | Export images and a JSON manifest for external editing          |
| [`fill_form`](https://pdftl.readthedocs.io/en/latest/operations/fill_form.html)                         | Fill a PDF form                                                 |
| [`filter`](https://pdftl.readthedocs.io/en/latest/operations/filter.html)                               | Do nothing (the default if `<operation>` is absent)             |
| [`generate_fdf`](https://pdftl.readthedocs.io/en/latest/operations/generate_fdf.html)                   | Generate an FDF file containing PDF form data                   |
| [`grep`](https://pdftl.readthedocs.io/en/latest/operations/grep.html)                                   | Match text patterns and get bounding boxes                      |
| [`highlight`](https://pdftl.readthedocs.io/en/latest/operations/highlight.html)                         | Highlight text matching a regex pattern                         |
| [`import_fonts`](https://pdftl.readthedocs.io/en/latest/operations/import_fonts.html)                   | Import edited fonts from a directory using a JSON manifest      |
| [`import_images`](https://pdftl.readthedocs.io/en/latest/operations/import_images.html)                 | Import edited images from a directory using a JSON manifest     |
| [`import_streams`](https://pdftl.readthedocs.io/en/latest/operations/import_streams.html)               | Import and apply modified content streams                       |
| [`inject`](https://pdftl.readthedocs.io/en/latest/operations/inject.html)                               | Inject code at start or end of page content streams             |
| [`insert`](https://pdftl.readthedocs.io/en/latest/operations/insert.html)                               | Insert blank pages                                              |
| [`modify_annots`](https://pdftl.readthedocs.io/en/latest/operations/modify_annots.html)                 | Modify properties of existing annotations                       |
| [`modify_images`](https://pdftl.readthedocs.io/en/latest/operations/modify_images.html)                 | Apply in-place image pixel modifications and effects            |
| [`modify_layers`](https://pdftl.readthedocs.io/en/latest/operations/modify_layers.html)                 | Merge or strip specific layers                                  |
| [`montage`](https://pdftl.readthedocs.io/en/latest/operations/montage.html)                             | Impose pages onto a grid layout                                 |
| [`move`](https://pdftl.readthedocs.io/en/latest/operations/move.html)                                   | Move pages to a new location                                    |
| [`multibackground`](https://pdftl.readthedocs.io/en/latest/operations/multibackground.html)             | Use multiple pages as backgrounds                               |
| [`multistamp`](https://pdftl.readthedocs.io/en/latest/operations/multistamp.html)                       | Stamp multiple pages onto an input PDF                          |
| [`mutate_content`](https://pdftl.readthedocs.io/en/latest/operations/mutate_content.html)               | Mutate page content streams using a user-supplied Python script |
| [`normalize`](https://pdftl.readthedocs.io/en/latest/operations/normalize.html)                         | Reformat page content streams                                   |
| [`optimize_images`](https://pdftl.readthedocs.io/en/latest/operations/optimize_images.html)             | Optimize images                                                 |
| [`place`](https://pdftl.readthedocs.io/en/latest/operations/place.html)                                 | Shift, scale, and spin page content                             |
| [`redact`](https://pdftl.readthedocs.io/en/latest/operations/redact.html)                               | Find and destroy text matching a pattern, optionally boxing it out |
| [`render`](https://pdftl.readthedocs.io/en/latest/operations/render.html)                               | Render PDF pages as images                                      |
| [`recolor_images`](https://pdftl.readthedocs.io/en/latest/operations/recolor_images.html)               | Convert images to grayscale                                     |
| [`recolor_vectors`](https://pdftl.readthedocs.io/en/latest/operations/recolor_vectors.html)             | Make non-image page content gray                                |
| [`replace`](https://pdftl.readthedocs.io/en/latest/operations/replace.html)                             | Regex replacement on page content streams                       |
| [`resample_images`](https://pdftl.readthedocs.io/en/latest/operations/resample_images.html)             | Resample images                                                 |
| [`rotate`](https://pdftl.readthedocs.io/en/latest/operations/rotate.html)                               | Rotate pages in a PDF                                           |
| [`server`](https://pdftl.readthedocs.io/en/latest/operations/server.html)                               | Start the pdftl API server                                      |
| [`set`](https://pdftl.readthedocs.io/en/latest/operations/set.html)                                     | Set document properties, viewer preferences, and page labels    |
| [`shuffle`](https://pdftl.readthedocs.io/en/latest/operations/shuffle.html)                             | Interleave pages from multiple input PDFs                       |
| [`simplify_vectors`](https://pdftl.readthedocs.io/en/latest/operations/simplify_vectors.html)           | Reduce vector path complexity                                   |
| [`stamp`](https://pdftl.readthedocs.io/en/latest/operations/stamp.html)                                 | Stamp a 1-page PDF onto each page of an input PDF               |
| [`stamp_fields`](https://pdftl.readthedocs.io/en/latest/operations/stamp_fields.html)                   | Stamp PDF content into form fields                              |
| [`style_text`](https://pdftl.readthedocs.io/en/latest/operations/style_text.html)                       | Change appearance of text                                       |
| [`subset_fonts`](https://pdftl.readthedocs.io/en/latest/operations/subset_fonts.html)                   | Shrink embedded fonts to only the glyphs actually used          |
| [`tag`](https://pdftl.readthedocs.io/en/latest/operations/tag.html)                                     | Auto-tag a PDF for accessibility using OpenDataLoader           |
| [`unpack_files`](https://pdftl.readthedocs.io/en/latest/operations/unpack_files.html)                   | Unpack file attachments                                         |
| [`unpause`](https://pdftl.readthedocs.io/en/latest/operations/unpause.html)                             | Remove 'pause' frames from a slide deck                         |
| [`update_bookmarks`](https://pdftl.readthedocs.io/en/latest/operations/update_bookmarks.html)           | Replace PDF bookmarks from a YAML or JSON file                  |
| [`update_info`](https://pdftl.readthedocs.io/en/latest/operations/update_info.html)                     | Update PDF metadata from dump_data instructions                 |
| [`update_info_utf8`](https://pdftl.readthedocs.io/en/latest/operations/update_info_utf8.html)           | Update PDF metadata from dump_data_utf8 instructions            |
| [`zoom`](https://pdftl.readthedocs.io/en/latest/operations/zoom.html)                                   | Rescale entire pages                                            |

| Option                                                                                                             | Description                                               |
|--------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------|
| [`allow <perm>`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#allow-perm)                       | Specify permissions for encrypted files                   |
| [`compress`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#compress)                             | Compress output file streams (default)                    |
| [`drop_info`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#drop-info)                           | Discard document-level info metadata                      |
| [`drop_xfa`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#drop-xfa)                             | Discard form XFA data if present                          |
| [`drop_xmp`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#drop-xmp)                             | Discard document-level XMP metadata                       |
| [`encrypt_128bit`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#encrypt-128bit)                 | Use 128 bit encryption (obsolete, maybe insecure)         |
| [`encrypt_40bit`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#encrypt-40bit)                   | Use 40 bit encryption (obsolete, highly insecure)         |
| [`encrypt_aes128`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#encrypt-aes128)                 | Use 128 bit AES encryption (maybe obsolete)               |
| [`encrypt_aes256`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#encrypt-aes256)                 | Use 256 bit AES encryption                                |
| [`fast`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#fast)                                     | Skip stream recompression for faster saving               |
| [`flatten`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#flatten)                               | Flatten all annotations                                   |
| [`keep_final_id`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#keep-final-id)                   | Copy final input PDF's ID metadata to output              |
| [`keep_first_id`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#keep-first-id)                   | Copy first input PDF's ID metadata to output              |
| [`linearize`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#linearize)                           | Linearize output file(s)                                  |
| [`no_encrypt_metadata`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#no-encrypt-metadata)       | Leave metadata unencrypted                                |
| [`need_appearances`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#need-appearances)             | Set a form rendering flag in the output PDF               |
| [`output <file>`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#output-file)                     | The output file path, or a template for burst             |
| [`owner_pw <pw>`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#owner-pw-pw)                     | Set owner password and encrypt output                     |
| [`replacement_font <file>`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#replacement-font-file) | Replace the font used for all form fields with a TTF file |
| [`sign_cert <file>`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#sign-cert-file)               | Path to certificate PEM                                   |
| [`sign_field <name>`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#sign-field-name)             | Signature field name (default: Signature1)                |
| [`sign_key <file>`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#sign-key-file)                 | Path to private key PEM                                   |
| [`sign_pass_env <var>`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#sign-pass-env-var)         | Environment variable with sign_cert passphrase            |
| [`sign_pass_prompt`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#sign-pass-prompt)             | Prompt for sign_cert passphrase                           |
| [`uncompress`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#uncompress)                         | Disable compression of output file streams                |
| [`user_pw <pw>`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#user-pw-pw)                       | Set user password and encrypt output                      |
| [`verbose`](https://pdftl.readthedocs.io/en/latest/misc/output_options.html#verbose)                               | Turn on verbose output                                    |

## Links

* **License:** This project is licensed under the [Mozilla Public License 2.0][1].
* **Changelog:** [CHANGELOG.md][2].
* **Online documentation:** [_pdftl on Read the Docs_][3].

[1]: https://raw.githubusercontent.com/pdftl/pdftl/main/LICENSE
[2]: https://github.com/pdftl/pdftl/blob/main/CHANGELOG.md
[3]: https://pdftl.readthedocs.io
[4]: https://pdftl.readthedocs.io/en/latest/api_tutorial.html

