# pdftl
<img align="right" width="100" src="https://raw.githubusercontent.com/pdftl/pdftl/main/.github/assets/pdftl.svg">

[![PyPI](https://img.shields.io/pypi/v/pdftl)](https://pypi.org/project/pdftl/)
[![CI](https://github.com/pdftl/pdftl/actions/workflows/ci.yml/badge.svg)](https://github.com/pdftl/pdftl/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/pdftl/pdftl/graph/badge.svg)](https://codecov.io/gh/pdftl/pdftl)
[![Documentation Status](https://readthedocs.org/projects/pdftl/badge/?version=latest)](https://pdftl.readthedocs.io/en/latest/?badge=latest)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/pdftl)](https://pypi.org/project/pdftl/)

**pdftl** ("PDF tackle") is a CLI tool for PDF manipulation written in Python. It is intended to be a command-line compatible extension of the venerable `pdftk`.

Leveraging the power of [`pikepdf`](https://github.com/pikepdf/pikepdf) ([qpdf](https://github.com/qpdf/qpdf)) and other modern libraries, it offers advanced capabilities like cropping, chopping, regex text replacement, adding text and arbitrary content stream injection.

## Why pdftl?

* **Familiar syntax:** Command-line compatible with `pdftk`. So `sed s/pdftk/pdftl/g` should result in working scripts. _(work in progress but mostly done)_
* **Pipelining:** Chain multiple operations in a single command using `---`.
* **Probably performant:** `pdftl` seems faster than `pdftk` for many operations _(untested hunch; data needed)_. Reason: `pdftl` mostly drives `pikepdf` which drives `qpdf`, a fast C++ library.
* **Extra/enhanced operations and features** such as zooming pages, smart merging preserving links and outlines, cropping/chopping up pages, text extraction, optimizing images.
* **Modern security:** Supports AES-256 encryption and modern permission flags out of the box.
* **Content editing:** Find & replace text via regular expressions, inject raw PDF operators, or overlay dynamic text.

`pdftl` maintains command-line compatibility with `pdftk` while introducing features required for modern PDF workflows.

| Feature | `pdftk` (Legacy) | `pdftl` (Modern) |
| :--- | :--- | :--- |
| **Pipelining** | ❌ (Requires temp files) | ✅ **Native** (Chain ops with `---`) |
| **Encryption** | ⚠️ (Obsolete RC4) | ✅ **AES-256 Support** |
| **Syntax** | Standard | ✅ **Compatible Extension** |
| **Page Geometry** | ❌ | ✅ **Crop to fit, Zoom, & Chop** |
| **Pipelined Logic** | ❌ | ✅ **Rotate + Stamp in one command** |
| **Installation** | Often complex binary | ✅ **Simple `pipx install pdftl`** |
| **Performance** | Variable | ✅ **Powered by pikepdf/qpdf** |
| **Link Integrity**| ⚠️ Often breaks TOC/Links|✅ **Preserves internal cross-refs** |

### Comparison Examples

#### 1. The Power of Pipelining (`---`)
In `pdftk`, performing a rotation and then a watermark requires two commands and a temporary file. In `pdftl`, it is a single continuous operation:

**Standard pdftk:**
```bash
pdftk in.pdf rotate 1-endsouth output temp.pdf
pdftk temp.pdf stamp mark.pdf output final.pdf

## Installation

Install [`pipx`](https://pipx.pypa.io), and then:

```bash
pipx install pdftl[full]
```

A simple `pip install pdftl[full]` install is also supported.

**Note:** The `[full]` install includes [`ocrmypdf`](https://pypi.org/project/ocrmypdf/) for image optimization, [`reportlab`](https://pypi.org/project/reportlab/) for text generation, and [`pypdfium2`](https://pypi.org/project/pypdfium2/) for text extraction. Omit `[full]` to omit those features and dependencies.

## Key features

### 📄 Standard operations

* **Combine:** `cat`, `shuffle` (interleave pages from multiple docs).
* **Split:** `burst` (split into single pages), `delete` pages.
* **Metadata:** `dump_data`, `update_info`, `attach_files`, `unpack_files`.
* **Watermarking:** `stamp` / `background` (single page), `multistamp` / `multibackground`.

### ✂️ Geometry & splitting

* **Rotate:** `rotate` pages (absolute or relative).
* **Crop:** `crop` to margins or standard paper sizes (e.g., "A4").
* **Chop:** `chop` pages into grids or rows (e.g., split a scanned spread into two pages).
* **Spin:** `spin` content *inside* the page boundaries without changing page orientation.

### 📝 Forms & annotations

* **Forms:** `fill_form`, `generate_fdf`, `dump_data_fields`.
* **Annotations:** `modify_annots` (surgical edits to link properties, colors, borders), `delete_annots`, `dump_annots`.

### 🛠️ Advanced

* **Text replacement:** `replace` text in content streams using regular expressions (experimental).
* **Code injection:** `inject` raw PDF operators at the head/tail of content streams.
* **Optimization:** `optimize_images` (smart compression via OCRmyPDF).
* **Dynamic text:** `add_text` adds page numbers, filenames, or timestamps to pages.
* **Cleanup:** `normalize` content streams, `linearize` for web viewing.

## Examples

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
pdftl in.pdf cat 1-5east --- crop "(a4)" output out.pdf
```

### Pipelining

You can chain operations without intermediate files using `---`:

```bash
# Burst a file, but rotate and stamp every page first
pdftl in.pdf rotate south \
  --- stamp watermark.pdf \
  --- burst output page_%04d.pdf
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
# Add a watermark, the pdftk way
pdftl in.pdf stamp watermark.pdf output marked1.pdf
```

```
# Add an obnoxious semi-transparent red watermark on odd pages only
pdftl in.pdf add_text 'odd/YOUR AD HERE/(position=mid-center, font=Helvetica-Bold, size=72, rotate=45, color=1 0 0 0.5)' output with_ads.pdf
```

```
# Content stream replacment with regular expressions (YMMV)
# Change black to red
pdftl in.pdf replace '/0 0 0 (RG|rg)/1 0 0 \1/' output redder.pdf
```

## Operations and options

```
$ pdftl
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ pdftl - PDF tackle a.b.c                                                        ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
A wannabe CLI compatible clone/extension of pdftk

Usage

 pdftl <input>... <operation> [<option...>]
 pdftl <input>... <operation> --- <operation>... [<option...>]
 pdftl help [<operation> | <option>]
 pdftl help [help | sign | filter | input | --- | pages | output | example | all]
 pdftl --version



  Operations
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  add_text                Add user-specified text strings to PDF pages
  background              Use a 1-page PDF as the background for each page
  burst                   Split a single PDF into individual page files
  cat                     Concatenate pages from input PDFs into a new PDF
  chop                    Chop pages into multiple smaller pieces
  crop                    Crop pages
  delete                  Delete pages from an input PDF
  delete_annots           Delete annotation info
  dump_annots             Dump annotation info
  dump_data               Metadata, page and bookmark info (XML-escaped)
  dump_data_annots        Dump annotation info in pdftk style
  dump_data_fields        Print PDF form field data with XML-style escaping
  dump_data_fields_utf8   Print PDF form field data in UTF-8
  dump_data_utf8          Metadata, page and bookmark info (in UTF-8)
  dump_dests              Print PDF named destinations data to the console
  dump_signatures         List and validate digital signatures
  dump_text               Print PDF text data to the console or a file
  fill_form               Fill a PDF form
  filter                  Do nothing (the default if <operation> is absent)
  generate_fdf            Generate an FDF file containing PDF form data
  inject                  Inject code at start or end of page content streams
  list_files              List file attachments
  modify_annots           Modify properties of existing annotations
  multibackground         Use multiple pages as backgrounds
  multistamp              Stamp multiple pages onto an input PDF
  normalize               Reformat page content streams
  optimize_images         Optimize images
  replace                 Regex replacement on page content streams
  rotate                  Rotate pages in a PDF
  shuffle                 Interleave pages from multiple input PDFs
  spin                    Spin page content in a PDF
  stamp                   Stamp a 1-page PDF onto each page of an input PDF
  unpack_files            Unpack file attachments
  update_info             Update PDF metadata from dump_data instructions
  update_info_utf8        Update PDF metadata from dump_data_utf8 instructions



  Options
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  allow <perm>...          Specify permissions for encrypted files
  attach_files <file>...   Attach files to the output PDF
  compress                 Compress output file streams (default)
  drop_info                Discard document-level info metadata
  drop_xmp                 Discard document-level XMP metadata
  encrypt_128bit           Use 128 bit encryption (obsolete, maybe insecure)
  encrypt_40bit            Use 40 bit encryption (obsolete, highly insecure)
  encrypt_aes128           Use 128 bit AES encryption (maybe obsolete)
  encrypt_aes256           Use 256 bit AES encryption
  flatten                  Flatten all annotations
  keep_final_id            Copy final input PDF's ID metadata to output
  keep_first_id            Copy first input PDF's ID metadata to output
  linearize                Linearize output file(s)
  need_appearances         Set a form rendering flag in the output PDF
  output <file>            The output file path, or a template for burst
  owner_pw <pw>            Set owner password and encrypt output
  sign_cert <file>         Path to certificate PEM
  sign_field <name>        Signature field name (default: Signature1)
  sign_key <file>          Path to private key PEM
  sign_pass_env <var>      Environment variable with sign_cert passphrase
  sign_pass_prompt         Prompt for sign_cert passphrase
  uncompress               Disable compression of output file streams
  user_pw <pw>             Set user password and encrypt output
  verbose                  Turn on verbose output

```

## Links

* **License:** This project is licensed under the [Mozilla Public License 2.0][1].
* **Changelog:** [CHANGELOG.md][2].
* **Documentation:** [pdftl.readthedocs.io][3].


[1]: https://raw.githubusercontent.com/pdftl/pdftl/main/LICENSE
[2]: https://github.com/pdftl/pdftl/blob/main/CHANGELOG.md
[3]: https://pdftl.readthedocs.io
