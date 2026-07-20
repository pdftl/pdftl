
# Changelog

This changelog format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- Possible headings: Added, Changed, Deprecated, Fixed, Removed, Security -->

## Unreleased

### Changed

- `render`: API now supports explicit `format=png|jpg|jpeg|pdf` output selection. Image formats return a ZIP archive; `format=pdf` returns a single rasterized PDF. The CLI continues to determine output format from `output`.

- `server`:
  - UI enhancements to `builder` interface
  - server pipeline now takes understands `@A` per-operation arguments, allowing use of `stamp`, for example

### Fixed

- `stamp`, `multistamp`, `background`, `multibackground`: a PDF file argument to use as overlay/underlay is now required, per the documentation

- `optimize_images`: better errors on missing `ocrmypdf` system dependencies

- `delete`, `delete_blank`, `unpause`, `insert`, `move`: page labels (`/PageLabels`)
  are now preserved and remapped across page deletion, insertion, and
  reordering, so surviving pages keep their original logical page numbers
  (e.g. roman-numeral front matter) instead of losing custom numbering.

- `burst`: each output chunk now preserves the ORIGINAL document's page
  labels/numbering rather than restarting at "1" — e.g. splitting a book
  by chapter keeps each chapter's pages numbered as they were in the whole
  book. This is an intentional deviation from classic `pdftk` burst
  behaviour, which has no equivalent concept.

- `dump_bookmarks`: strip trailing nulls from outline titles and metadata, like most PDF readers

## [0.25.1] - 2026-07-15

### Fixed

- `server`:
  - Replaced a racy `multiprocessing.Queue`-based IPC mechanism for
    subprocess-isolated operation execution with a `Pipe`-based protocol that
    correctly handles worker timeouts, crashes, and cleanup. This fixes
    intermittent hangs, leaked worker processes/pipes, and dropped/incorrect
    exception types under concurrent load.
  - Fixed a timeout error message that silently dropped the
    configured timeout value.
  - Fixed a keep-alive connection handling bug where rejecting an
    oversized upload could leave the request thread blocked waiting on the
    remainder of an in-flight upload body.
  - Fixed a per-worker memory limit that crashed workers outright
    on some platforms (notably macOS) instead of degrading gracefully.

### Security

- `server`: bounded the number of concurrent subprocess-isolated operation
  workers (default: capped to available CPU count, configurable via
  `PDFTL_MAX_CONCURRENT_WORKERS`). Previously, a burst of concurrent
  requests could spawn an unbounded number of worker processes.

## [0.25.0] - 2026-07-14

### Added

- `server` operation

- `deskew` operation

- `stamp_fields` operation

- `embed_fonts` operation

- `--args <file.yml>` argument insertion

- `coalesce_strokes` option added to `simplify_vectors`

### Changed

- `sign_field` no longer changes field appearances

- `style_text` now adapts Tr (text rendering mode) better to user parameters

- improved project version-finding heuristics

### Fixed

- `dump_text`: if `output` is passed directly, we now write the text, not the PDF.

- `render` now initializes forms, so that they are properly rendered

- `dump_signatures` now correctly reports pyhanko's valid/invalid status

## [0.24.0] - 2026-07-07

### Added

- `import_fonts` operation

- `export_fonts` operation

- added `upscale` and `adaptive_threshold` to `modify_images`

- `import_images` operation

- `export_images` operation

- `import_streams` operation

- `add_bookmarks`: new options for bookmark specs: `uri`, `launch`, `named`, `dest`,
  `color`, `bold`, `italic`

- `dump_colorspaces`: CalGray, CalRGB, and Lab colour spaces now resolved and reported
  (previously silently fell through)

- `dump_tags`: non-standard structure tags now reported as warnings (ISO 32000-2 §14.8)

- `dump_tags`: invalid structure attributes now reported as warnings per standard owner
  definitions (ISO 32000-2 §14.8.5)

- `fill_form`: multi-select choice fields now supported (ISO 32000-2 §12.7.5.4)

- `fill_form`: radio buttons, checkboxes, list-boxes, and nested fields now handled correctly

- `generate_fdf`: optional `status` string in generated FDF output (ISO 32000-2 Table 246)

- `generate_fdf`: multi-select choice field arrays now serialized correctly in FDF output

- `set` operation: `trapped` field for document trapping status (`True`, `False`, or `Unknown`)

- `set` operation: `author` now accepts comma-separated values for multiple authors

- Bookmarks: non-GoTo actions (e.g. `Launch`, `Named`, `JavaScript`) are now preserved when
  copying or round-tripping outlines via `dump_bookmarks` / `add_bookmarks`

### Changed

- Improved verbose pipeline logging feedback loops

- Suppressed noisy metadata field conversion logging strings in `dump_data`

- `dump_streams`: output header format changed from `Page <N>` / `Page <N> / XObject <name> (<obj>:<gen>)`
  to `Page <N> / Contents` / full nested breadcrumb paths (e.g. `Page 1 / XObject /Fm1 / XObject /Fm0`);
  obj:gen suffixes are no longer shown. Form XObjects reachable via more than one path now emit a
  `% ALIAS OF: <canonical path>` stub on repeat references instead of being silently skipped

### Fixed

- `dump_streams`: content stream lines beginning with `===` or `\` are now escaped so they don't
  get misread as structural headers when round-tripped through `import_streams`

- Improve handling of OS errors while saving to disk

- Improve handling of invalid or missing arguments in several operations

- Destination coordinates for all ISO 32000-2 explicit destination types (`/FitH`,
  `/FitV`, `/FitR`, `/FitBH`, `/FitBV`, `/Fit`, `/FitB`) now correctly transformed
  when rotating or scaling pages; previously only `/XYZ` coordinates were handled

- Null coordinate values in `/XYZ` destinations now correctly preserved during
  rotation and scaling

- `fill_form`: digital signature fields are now safely skipped instead of corrupted

- Bookmarks: non-GoTo outline actions (e.g. URI links, launch actions) were silently dropped
  when merging PDFs; they are now preserved

- Named destinations in legacy PDF 1.1 `/Root/Dests` dictionaries are now resolved correctly

- Structure destinations (ISO 32000-2 §12.3.2.3) in outlines now resolve to the correct page,
  with a fallback to page 1 if no content is found

- `set` operation: PDF 2.0 documents now correctly omit deprecated metadata fields from the
  `/Info` dictionary, per ISO 32000-2 §14.3.3

## [0.23.0] - 2026-06-27

### Added

- `delete_actions` operation

- `dump_actions` operation

- `dump_tags` operation

- `tag` operation

- `dump_streams` operation

- `simplify_vectors` operation

- `png_compression` setting for the `render` operation

- operations are now linked in the HTML docs

### Changed

- `replace` now recurses into XObjects by default

### Fixed

- `delete_images` global mode fixed to delete per-page images too

- `burst` now keeps outlines and annotations, like `cat`

- Prevent BrokenPipeError leaking out

## [0.22.0] - 2026-06-20

### Added

- `add_images` operation

- `diff_text` operation

- `dump_tables` operation

- `modify_images` operation

- System font name and file path resolution for `add_text`

- block repetition of page specs via `copy<N>`

- multithreading for `resample_images` and `recolor_images`

### Changed

- page specs now tolerate whitespace (by stripping it before parsing)

- `dump_bookmarks`: named destinations now resolved to `page`/`view` by default; pass `no_resolve`
  to skip

- `update_bookmarks`: strict schema validation; accepts structured data directly via API
  (`bookmarks=` kwarg)

### Fixed

- Preserve outline state (open/closed, formatting) when copying outlines

- Fix a crash when a subpipeline is passed as an operation argument

- bash completion: fix mid-word completion behaviour, update docs

## [0.21.1] - 2026-06-14

### Fixed

- fix `style_text` bugs: (1) coalesce content streams (2) handle `SC` etc

- add pattern support to `recolor_vectors`

### Added

- auto-paged help output on linux/mac

## [0.21.0] - 2026-06-12

### Fixed

- `render` should no longer segfault when an unknown output file extension is passed

- `dump_annots` now properly passing the PDF down the pipeline

- Top margin percentage bug fixed in `clip` and `crop`

### Changed

- pipeline info added to `verbose` mode

- make parentheses optional for `crop`, `clip`, `delete_blank`, `delete_images`, and `style_text`, in many cases

### Added

- repetitions in page specs via`rep<N>`

- `barcode` operation

- `recolor_images` operation

- `recolor_vectors` operation

- `style_text` operation

## [0.20.0] - 2026-06-07

### Added

- Guess shell for `--completion` if it is not specified

- add `{count}` as an alias for `{n}` in `add_text` and `add_bookmarks`

- `unpause` operation

- `dump_colorspaces` operation

- `delete_attachments` operation

- `dump_fonts` operation

- `create` operation

- `resample_images` operation

- `grep` operation

### Changed

- Performance improvements in link/outline remapping

- `dump_files` output format changed to JSON, with more detail

- Several JSON dump output formats changed from flat lists to `{"name": [...]}` format

## [0.19.0] - 2026-06-01

### Fixed

- Fix crash on outline items without a /Title

### Added

- Filter annotations in `dump_annots` and `delete_annots`

- add `EACH` ... `JOB`, documented under `pdftl help pipeline`

- `delete_bookmarks` operation

- `add_bookmarks` operation

- add `{n}` template to `add_text` (and `add_bookmarks`)

### Changed

- Improve error message when too many files are opened

## [0.18.1] - 2026-05-29

### Changed

- When `--debug` is passed, `optimize_images` now outputs ocrmypdf debug messages

- Improve error message for invalid page specs

### Fixed

- `dump_annots`: fix crash caused by named destinations which are not valid python dictionary keys

- Updated API type stubs

- Improve broken pipe handling: suppress tracebacks when piping to a pager

- Improve test robustness (fixes [#25](https://github.com/pdftl/pdftl/issues/25))

## [0.18.0] - 2026-05-05

### Added

- `zoom` operation

- `fast` output option

### Changed

- pipx-specific installation instructions for missing dependencies

- help output uses executable name correctly

- `update_bookmarks` can now read bookmark data from stdin

- `add_text` supports markdown-style hyperlinks

- `set` handles common metadata, in both Info and XMP

- `stamp`, `multistamp`, `background`, `multibackground` accept page specs

- `help`: link to readthedocs page

### Fixed

- `modify_layers` now hooks up print/view events (/AS dictionary)

- `dump_layers` reports /AS dictionary hookup as 'active' state

## [0.17.0] - 2026-05-02

### Added

- `modify_layers` operation

- `dump_images` operation

- `highlight` operation

- `stamp`, `background`, `multistamp`, `multibackground`: option to add content in a new layer

- page ranges accept `portrait` and `landscape` qualifiers

### Changed

- plain `render` arguments are now page specs, and we can output a multi-page render PDF

## [0.16.0] - 2026-04-26

### Added

- `delete_blank` operation

- `delete_images` operation

## [0.15.0] - 2026-04-19

### Added

- `set` operation

- improve handling of unknown command line flags

- suggest possible bug reporting on errors

- CONTRIBUTING.md added

### Fixed

- work around a pyHanko bug affecting `dump_signatures`

## [0.14.0] - 2026-04-17

### Added

- `dump_encryption` operation

- `dump_bookmarks` operation

- `update_bookmarks` operation

### Changed

- `montage`: removed `fit` parameter

### Fixed

- `pdftk` compatibility: default to all permissions denied

- `montage` and `booklet` bug handling pages with negative rotation fixed

- `place` and `add_text` rotation bugs fixed and visual tests added

- `crop` rotation bugs fixed and visual tests added

## [0.13.0] - 2026-04-15

### Added

- page ranges accept `step<n>` to step through a range in regular intervals

- `burst` accepts page specs or bookmark levels for chunk split points, and chunk size limits

### Fixed

- Completions updated

- `dump_text` now works in a pipeline, instead of crashing

- `montage` and `booklet` now handle page rotation better

- `pdftk` compatibility:
  - `burst` now outputs doc_data.txt
  - `dump_data` omits empty fields

### Changed

- Minor shell completion performance improvements

## [0.12.1] - 2026-04-06

### Fixed

- Improved memory management

- Fixed `chop`, `place`, and `booklet` operations to correctly calculate physical boundaries on pages with non-zero origins or non-standard `/Rotate` flags

### Changed

- Paper spec parser now falls back to landscape when missing an underscore (e.g., `a4l` to `a4_l`).

- Pipeline execution now fails with an error if stage arguments cannot be parsed, rather than warning and proceeding.

- Improved error messages for corrupted or invalid PDF files.

## [0.12.0] - 2026-04-04

### Added

- `clip` operation: enclose page content in a clipping rectangle

- Absolute rectangle specifications for `crop` and `clip`

- `montage` operation: impose multiple pages onto a grid layout, useful for contact sheets and N-up handouts. Supports configurable grid size, canvas, margins, gutters, and aspect ratio control (`fit=contain|fill`).

- `booklet` operation: reorder and impose pages for duplex booklet printing. Automatically pads to a multiple of 4, supports signature-based chunking (`sig=N`), custom canvas size, and right-to-left binding (`rtl=true`).

## [0.11.2] - 2026-03-22

### Added

- `--version` now displays core dependencies (pikepdf, libqpdf, python) and optional dependencies, along with a docs URL

### Fixed

- `crop` with an invalid spec now prints a clean error message instead of a traceback

- `--version` no longer crashes if pikepdf is not installed

## [0.11.1] - 2026-02-09

### Fixed

- Bug fix: `render` should no longer save a PDF to the pattern file

## [0.11.0] - 2026-02-08

### Added

- Inline pipeline substitution using new `JOB` and `DONE` keywords, documented under `pdftl help pipeline`

### Changed

- Bump pikepdf required version to 10.3.0 to enable better pdftk compatibility

- Compatibility:
  - Default to encrypt_aes128 encryption, the strongest which is pdftk compatible
  - pdftl now passes the vendored pdftk-java test suite

- Shell completion improvements and optimization

- Respect XDG environment variables for cache and plugin directory on non-Windows

- Performance improvements, particularly in `cat` and `dump_data`

- API now accepts `io.BytesIO` as PDF inputs (as well as `pathlib.Path`, etc)

### Fixed

- Resolved issue with outlines (contents) when using `cat` with named destinations

## [0.10.0] - 2026-01-28

### Added

- Shell completion for bash, zsh and powershell

## [0.9.2] - 2026-01-26

### Changed

- Performance improvements for `cat`, `shuffle`, and `rotate` operations, especially regarding hyperlink handling.

- Invalid page arguments (e.g., requesting page 10 of a 5-page PDF) now return descriptive error messages instead of crashing.

### Fixed

- `delete`: Now properly removes page resources, resulting in smaller output files.

- `generate_fdf`: Fixed an issue where generation could fail or produce invalid output when handling `None` values or specific binary string formats.

- The option parser (used in `add_text`, etc.) now correctly handles unbalanced quotes by raising a descriptive error, preventing data corruption.

### Security

- Replaced regex parsing with a state machine to prevent application hangs (ReDoS) when processing malformed quoted strings.

- Now using `defusedxml` to avoid `xml.etree.ElementTree` vulnerabilities.

- Ensure passwords are redacted from all logging calls.

## [0.9.1] - 2026-01-21

### Fixed

- `README.md`, `NOTICE.md` and `CHANGELOG.md` updated/corrected

## [0.9.0] - 2026-01-21

### Added

- `mutate_content` operation: mutate page content streams
  using a user-supplied Python script

- `update_info`: Support setting `PdfID0` to "RESET" to force valid
  ID regeneration on save.

- `replacement_font` option: change the font used for user
  text in forms

### Changed

- `--version` includes optional dependency versions

- Documentation updated for `dump_data`, `dump_annots`, and
  `dump_data_fields` to better explain output formats and
  pdftk compatibility.

### Fixed

- Compatibility: `generate_fdf` fixed and `replacement_font`
  implemented. Now passes the pdftk-java tests using
  bleeding edge `pikepdf`.

- startup performance improvements: lazy loading, and
  working around heavy Rich formatting for default help text

- Pipeline: Read-only operations (like `dump_text`) now
  implicitly pass the input PDF to the next stage, fixing
  chained commands (e.g., `dump_text --- cat`).

- Handling of features with missing dependencies should be
  more consistent.

## [0.8.0] - 2026-01-17

### Added

- Official support for Python 3.14

- More comprehensive `vendor_tests/pdftk-java` test suite,
  from `pdftk-java`. This test suite only is licensed under
  the GPL 2, see NOTICE.md.

- `no_encrypt_metadata` option: do not encrypt
  metadata. Only supported by AES encryption methods.

- `COMPATIBILITY.md`: compatibility notes

- `dump_data_fields` now extracts tooltips (FieldNameAlt)
  and default values (FieldValueDefault).

- API: inputs now support pathlib.Path objects directly.

### Changed

- Encryption method now defaults to `encrypt_aes128` if
  `user_pw` or `owner_pw` are passed, instead of not
  encrypting. This is more similar to `pdftk`.

- `attach_files` is now an operation, not an output option
  (pdftk compatibility)

- exit codes better aligned with pdftk's exit codes

- CLI: stricter argument parsing now raises
  `DuplicateArgumentError` if keywords are repeated.

### Fixed

- `cat` should now properly handle forms.

- `fill_form` compatibility fixes

- `dump_data_fields_utf8`: fixed crash/output issues.

- `set_info`: added validation for page label indices and
  better error handling for rotation/mediabox.

## [0.7.0] - 2026-01-11

### Added

- automated `pdftk` compatibility testing using third party
  php test suite.

- `drop_xfa` output option to drop XFA form data (pdftk compatibility)

- `render` operation: rasterize pages

- `move`, `update_info`, `update_info_utf8` now accept
  instructions from a JSON "at-file" using `@filename.json`
  in place of CLI arguments

- `dump_data` gives JSON output via the `json` keyword

- extensibility: add custom operations by putting Python
  files in `~/.config/pdftl/operations` (*nix) or
  `%APPDATA%\pdftl\config` (windows)

### Fixed

- bug in `add_pages.py` when a page has an integer key-value

- more comprehensive handling of the five PDF page boxes for
  `dump_data` and `update_info`

- `drop_info` and `drop_xmp` output options should now work
  as claimed

- `flatten` reimplemented for robustness

## [0.6.0] - 2026-01-04

### Added

- `move` operation: move pages within a PDF file

- `place` operation: shift, scale and/or spin content within
  the page

### Changed

- Now handles compound page specifications like `1,3-4,7-end`

### Fixed

- Improved API documentation generation

### Removed

- `spin` operation. See `place` for this functionality.

## [0.5.0] - 2026-01-03

### Added

- `add_text` features:

  - source metadata variables (`source_filename`,
    `source_page`, etc)

  - Bates stamping variable features,
    e.g. `DEF-{page+120:06d}` produces DEF-000121, DEF-000122,
    ...

- `insert` operation: insert blank pages

## [0.4.1] - 2026-01-02

### Fixed

- Broken link in README.md

## [0.4.0] - 2026-01-01

### Added

- API with fluent and functional interfaces
- `docs/api_tutorial.md` and auto-generated API docs

### Changed

- Renamed operation: `list_files` is now `dump_files`

### Fixed

- Fixed bug preventing parsing of the page specification "right"

## [0.3.1] - 2025-12-20

### Fixed

- `README.md` corrected, and "platform" badge added

## [0.3.0] - 2025-12-20

### Added

- Get `help` by tag with `pdftl help tag:<tagname>`

- `dump_signatures`: view and validate PDF signatures

- PDF signature output options:

  - `sign_cert <file>` Path to certificate PEM

  - `sign_field <name>` Signature field name (default: Signature1)

  - `sign_key <file>` Path to private key PEM

  - `sign_pass_env <var>` Environment variable with sign_cert passphrase

  - `sign_pass_prompt` Prompt for sign_cert passphrase

- `dump_layers`: dump PDF optional content groups (OCGs), a.k.a. "layers"

### Fixed

- performance improvements for `cat`

## [0.2.1] - 2025-12-17

### Added

- `crop`: added `fit` and `fit-group`

- artwork

- extended NOTICE.md: acknowledge `pikepdf`/`qpdf` and `pypdfium2`

- Windows testing

### Fixed

- performance improvements (lazy-loaded imports)

- help tweaks: add sources for non-operations; more help topic aliases

## [0.2.0] - 2025-12-13

### Added

- readthedocs integration and docs generation

### Fixed

- Improved help text

## [0.1.1] - 2025-12-12

### Added

- codecov integration
- PyPI publish integration

## [0.1.0] - 2025-12-11

### Added

- Initial public release of `pdftl`.
- Operations:
  - ``add_text``               Add user-specified text strings to PDF pages
  - ``background``             Use a 1-page PDF as the background for each page
  - ``burst``                  Split a single PDF into individual page files
  - ``cat``                    Concatenate pages from input PDFs into a new PDF
  - ``chop``                   Chop pages into multiple smaller pieces
  - ``crop``                   Crop pages
  - ``delete``                 Delete pages from an input PDF
  - ``delete_annots``          Delete annotation info
  - ``dump_annots``            Dump annotation info
  - ``dump_data``              Metadata, page and bookmark info (XML-escaped)
  - ``dump_data_annots``       Dump annotation info in pdftk style
  - ``dump_data_fields``       Print PDF form field data with XML-style escaping
  - ``dump_data_fields_utf8``  Print PDF form field data in UTF-8
  - ``dump_data_utf8``         Metadata, page and bookmark info (in UTF-8)
  - ``dump_dests``             Print PDF named destinations data to the console
  - ``dump_text``              Print PDF text data to the console or a file
  - ``fill_form``              Fill a PDF form
  - ``filter``                 Do nothing. (The default if ``<operation>`` omitted.)
  - ``generate_fdf``           Generate an FDF file containing PDF form data
  - ``inject``                 Inject code at start or end of page content streams
  - ``list_files``             List file attachments
  - ``modify_annots``          Modify properties of existing annotations
  - ``multibackground``        Use multiple pages as backgrounds
  - ``multistamp``             Stamp multiple pages onto an input PDF
  - ``normalize``              Reformat page content streams
  - ``optimize_images``        Optimize images
  - ``replace``                Regex replacement on page content streams
  - ``rotate``                 Rotate pages in a PDF
  - ``shuffle``                Interleave pages from multiple input PDFs
  - ``spin``                   Spin page content in a PDF
  - ``stamp``                  Stamp a 1-page PDF onto each page of an input PDF
  - ``unpack_files``           Unpack file attachments
  - ``update_info``            Update PDF metadata
  - ``update_info_utf8``       Update PDF metadata from dump_data_utf8 instructions
- Output options:
  - ``allow <perm>...``        Specify permissions for encrypted files
  - ``attach_files <file>``... Attach files to the output PDF
  - ``compress``               (default) Compress output file streams
  - ``drop_info``              Discard document-level info metadata
  - ``drop_xmp``               Discard document-level XMP metadata
  - ``encrypt_128bit``         Use 128 bit encryption (obsolete, maybe insecure)
  - ``encrypt_40bit``          Use 40 bit encryption (obsolete, highly insecure)
  - ``encrypt_aes128``         Use 128 bit AES encryption (maybe obsolete)
  - ``encrypt_aes256``         Use 256 bit AES encryption
  - ``flatten``                Flatten all annotations
  - ``keep_final_id``          Copy final input PDF's ID metadata to output
  - ``keep_first_id``          Copy first input PDF's ID metadata to output
  - ``linearize``              Linearize output file(s)
  - ``need_appearances``       Set a form rendering flag in the output PDF
  - ``output <file>``          The output file path, or a template for 'burst'
  - ``owner_pw <pw>``          Set owner password and encrypt output
  - ``uncompress``             Disables compression of output file streams
  - ``user_pw <pw>``           Set user password and encrypt output
  - ``verbose``                Turn on verbose output
