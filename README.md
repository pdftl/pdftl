# pdftl

[![PyPI](https://img.shields.io/pypi/v/pdftl)](https://pypi.org/project/pdftl/)
[![CI](https://github.com/pdftl/pdftl/actions/workflows/ci.yml/badge.svg)](https://github.com/pdftl/pdftl/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/pdftl/pdftl/graph/badge.svg)](https://codecov.io/gh/pdftl/pdftl)
[![Documentation Status](https://readthedocs.org/projects/pdftl/badge/?version=latest)](https://pdftl.readthedocs.io/en/latest/?badge=latest)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/pdftl)](https://pypi.org/project/pdftl/)

**pdftl** ("PDF Tackle") is a CLI tool for PDF manipulation written in Python. It is intended to be a command-line compatible extension of the venerable `pdftk`.

Leveraging the power of `pikepdf` (qpdf), `reportlab`, and other modern libraries, it offers advanced capabilities like geometric chopping, regex text replacement, and content stream injection.

## Why pdftl?

* **Familiar Syntax:** Designed to be a comfortable switch for `pdftk` users.
* **Modern Security:** Supports AES-256 encryption and modern permission flags out of the box.
* **Advanced Geometry:** Crop pages or `chop` huge pages (like blueprints or scanned spreads) into tiles.
* **Content Editing:** Find & replace text via Regex, inject raw PDF operators, or overlay dynamic text.
* **Pipelining:** Chain multiple operations in a single command using `---`.

## Installation

Install with all features enabled (recommended):

```bash
pip install "pdftl[full]"
```

*Note: The full install includes `ocrmypdf`, `reportlab`, and `pdfium` for image optimization, text generation and text extraction.*

## Key Features

### 📄 Standard Operations

* **Combine:** `cat`, `shuffle` (interleave pages from multiple docs).
* **Split:** `burst` (split into single pages), `delete` pages.
* **Metadata:** `dump_data`, `update_info`, `attach_files`, `unpack_files`.
* **Watermarking:** `stamp` / `background` (single page), `multistamp` / `multibackground`.

### ✂️ Geometry & Splitting

* **Rotate:** `rotate` pages (absolute or relative).
* **Crop:** `crop` to margins or standard paper sizes (e.g., "A4").
* **Chop:** `chop` pages into grids or rows (e.g., split a scanned spread into two pages).
* **Spin:** `spin` content *inside* the page boundaries without changing page orientation.

### 📝 Forms & Annotations

* **Forms:** `fill_form` (FDF/XFDF), `generate_fdf`, `dump_data_fields`.
* **Annotations:** `modify_annots` (surgical edits to link properties, colors, borders), `delete_annots`, `dump_annots`.

### 🛠️ Advanced / Power User

* **Text Replacement:** `replace` text in content streams using Regex (experimental).
* **Code Injection:** `inject` raw PDF operators at the head/tail of content streams.
* **Optimization:** `optimize_images` (smart compression via OCRmyPDF).
* **Dynamic Text:** `add_text` adds page numbers, filenames, or timestamps to pages.
* **Cleanup:** `normalize` content streams, `linearize` for web viewing.

## Usage Examples

### Basic Concatenation

```bash
# Merge two files
pdftl in1.pdf in2.pdf cat output combined.pdf
```

### Complex Geometry

```bash
# Take pages 1-5, rotate them 90 degrees East, and crop to A4
pdftl in.pdf cat 1-5east --- crop "(a4)" output out.pdf
```

### Advanced Pipelining

You can chain operations without intermediate files using `---`:

```bash
# Burst a file, but rotate and stamp every page first
pdftl in.pdf rotate south \
  --- stamp watermark.pdf \
  --- burst output page_%04d.pdf
```

### Forms and Metadata

```bash
# Fill a form and flatten it (make it non-editable)
pdftl form.pdf fill_form data.fdf flatten output signed.pdf
```

### Modify Annotations

```bash
# Change all Highlight annotations on odd pages to Red
pdftl docs.pdf modify_annots "odd/Highlight(C=[1 0 0])" output red_notes.pdf
```

## Operations and options

```bash
$ pdftl
pdftl - PDF Tackle x.y.z

  A wannabe CLI compatible clone/extension of pdftk

Usage:

  pdftl <input>... <operation> [<option...>]
  pdftl <input>... <operation> --- <operation>... [<option...>]
  pdftl help [<operation> | <option>]
  pdftl help [filter | input | --- | output | examples | all]
  pdftl --version

Operations:

  add_text               Add user-specified text strings to PDF pages
  background             Use a 1-page PDF as the background for each page
  burst                  Split a single PDF into individual page files
  cat                    Concatenate pages from input PDFs into a new PDF
  chop                   Chop pages into multiple smaller pieces
  crop                   Crop pages
  delete                 Delete pages from an input PDF
  delete_annots          Delete annotation info
  dump_annots            Dump annotation info
  dump_data              Metadata, page and bookmark info (XML-escaped)
  dump_data_annots       Dump annotation info in pdftk style
  dump_data_fields       Print PDF form field data with XML-style escaping
  dump_data_fields_utf8  Print PDF form field data in UTF-8
  dump_data_utf8         Metadata, page and bookmark info (in UTF-8)
  dump_dests             Print PDF named destinations data to the console
  dump_text              Print PDF text data to the console or a file
  fill_form              Fill a PDF form
  filter                 Do nothing. (The default if <operation> omitted.)
  generate_fdf           Generate an FDF file containing PDF form data
  inject                 Inject code at start or end of page content streams
  list_files             List file attachments
  modify_annots          Modify properties of existing annotations
  multibackground        Use multiple pages as backgrounds
  multistamp             Stamp multiple pages onto an input PDF
  normalize              Reformat page content streams
  optimize_images        Optimize images
  replace                Regex replacement on page content streams
  rotate                 Rotate pages in a PDF
  shuffle                Interleave pages from multiple input PDFs
  spin                   Spin page content in a PDF
  stamp                  Stamp a 1-page PDF onto each page of an input PDF
  unpack_files           Unpack file attachments
  update_info            Update PDF metadata
  update_info_utf8       Update PDF metadata from dump_data_utf8 instructions

Options for PDF output:

  allow <perm>...        Specify permissions for encrypted files
  attach_files <file>... Attach files to the output PDF
  compress               (default) Compress output file streams
  drop_info              Discard document-level info metadata
  drop_xmp               Discard document-level XMP metadata
  encrypt_128bit         Use 128 bit encryption (obsolete, maybe insecure)
  encrypt_40bit          Use 40 bit encryption (obsolete, highly insecure)
  encrypt_aes128         Use 128 bit AES encryption (maybe obsolete)
  encrypt_aes256         Use 256 bit AES encryption
  flatten                Flatten all annotations
  keep_final_id          Copy final input PDF's ID metadata to output
  keep_first_id          Copy first input PDF's ID metadata to output
  linearize              Linearize output file(s)
  need_appearances       Set a form rendering flag in the output PDF
  output <file>          The output file path, or a template for 'burst'
  owner_pw <pw>          Set owner password and encrypt output
  uncompress             Disables compression of output file streams
  user_pw <pw>           Set user password and encrypt output
  verbose                Turn on verbose output

```

## License

This project is licensed under the [Mozilla Public License 2.0](LICENSE).
