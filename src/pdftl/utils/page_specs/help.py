from pdftl.core.registry import register_help_topic


@register_help_topic(
    "page_specs",
    title="page specification syntax",
    desc="Specifying collections of pages and transformations",
)
def _help_topic_page_specs():
    """The page specification syntax is a powerful mechanism
    used by commands like `cat`, `delete`, and `rotate` to
    select pages and optionally apply transformations to them as
    they are processed.

    A complete page specification string combines up to three
    optional components in the following order:

    1. Page range: Which pages to select.

    2. Qualifiers, step and omissions: Filtering the selected pages by
    parity (even/odd) or aspect ratio (portrait/landscape), a step
    size to use when interpreting ranges, and omitted ranges.

    3. Transformation modifiers: Applying rotation or scaling to
    the selected pages. This is ignored by some operations.

    ### 1. Page Ranges

    A page range defines the starting and ending page
    numbers. If omitted, the specification applies to all pages.

    Multiple ranges can be separated by commas (e.g. `1,3,5-10`).

    A page identifier can be:

    * an integer (e.g., `5`) representing that page (numbered from
      page 1, the first page of the PDF file, regardless of any page
      labelling),

    * the keyword `end`,

    * or `r` followed by one of the two above types, representing
      reverse numbering. So `r1` means the same as `end`, and `rend`
      means the same as `1`.

    The following page range formats are supported:

    * `<I>`: A single page identifier

    * `<I>-<J>`: A range of pages (e.g., `1-5`). If the start page
    number is higher than the end page number (e.g., `5-1`), then the
    pages are treated in reverse order.

    ### 2. Page qualifiers, step and omissions

    #### Parity qualifiers

    Parity qualifiers filter the selected pages based on their
    number. They are added immediately after the page
    range. Valid qualifiers are:

    * `even`: selects only even-numbered pages in the range (e.g.,
    `1-10even`).

    * `odd`: selects only odd-numbered pages in the range (e.g.,
    `odd` alone selects all odd pages).

    * `portrait`: selects only pages with width no bigger than height

    * `landscape`: selects only pages with height no bigger than width

    ### Step size

    An optional step size to use when interpreting page ranges,
    starting from the first page in the range. The step size must an
    integer, and at least 1. The default step size is 1.

    Examples

    * `portraiteven` selects portrait pages with even numbers.

    * `6-20landscapeeven` selects landscape pages with even numbers from
      pages 6-20.

    * `step4` selects every 4th page (pages 1, 5, 9, ...) in the
      document.

    * `5-12step3` selects pages 5, 8, 11

    * `12-5step3` selects pages 12, 9, 6

    * `5-14step3` selects pages 5, 8, 11, 14

    * `5-14step3even` selects pages 8, 11

    You can also use `by` or `every` as alternatives to `step`.

    #### Omissions

    The `~` operator is used to exclude pages from the selection
    defined by the preceding page range and qualifiers.

    * `~<N>-<M>`: Omits a range of pages (e.g., `1-end~5-10` selects
    all pages except 5 through 10).

    * `~<N>`: Omits a single page (e.g., `1-10~5` selects all pages
    from 1 to 10 except page 5).

    * `~r<N>`: Omits a single page counting backwards from the end
    (e.g., `~r1` omits the last page).

    ### 3. Transformation Modifiers


    These optional modifiers can be chained after the range and
    qualifiers to apply changes to the page content.

    #### Rotation (relative)

    These modifiers adjust the page's current rotation property
    by adding or subtracting degrees.

    * `right`: Rotates 90 degrees clockwise (+90),

    * `left`: Rotates 90 degrees counter-clockwise (-90),

    * `down`: Rotates 180 degrees (+180).

    #### Rotation (absolute)

    These modifiers reset and set the page's rotation property
    to a fixed orientation (0, 90, 180, or 270 degrees) relative
    to the page's natural (unrotated) state.

    * `north`: Resets rotation to the natural page orientation,

    * `east`: Sets rotation to 90 degrees clockwise,

    * `south`: Sets rotation to 180 degrees,

    * `west`: Sets rotation to 270 degrees clockwise or 90 degrees
    counter-clockwise.

    #### Scale and zoom

    * `x<N>`: Scales the page content by factor N. N is typically an
    integer or decimal (e.g., `x2` doubles the size, `x0.5`
    halves it).

    * `z<N>`: Zoom in by N steps (or out if N is negative), where a
    zoom of 1 step corresponds to enlarging A4 paper to A3. More
    technically, we scale by factor of 2^(N/2). (N can be any
    number). For example, z1 will scale A4 pages up to A3, and
    `z-1` scales A4 pages down to A5.

    #### Groups (Applies to all contents)

    * `[<Range>, <Range>]<Modifier>`: Applies the modifier to the entire
    disjoint set of pages.

    Example: `[1-3, 5]x2` scales pages 1, 2, 3, and 5.

    **Note:** Group specifications must be distinct arguments. You cannot
    combine them with other ranges using commas (e.g. `[1,2]x3,6x2` is invalid).
    Use spaces instead: `[1,2]x3 6x2`.

    ### Examples

    * `1-5eastx2` selects pages 1 through 5, rotating them 90
    degrees clockwise (east) and scaling them by 2x.


    * `oddleftz-1` selects only the odd pages from the beginning
    to the end, rotating them 90 degrees counter-clockwise
    (left) and applying a zoom factor of z-1.


    * `1-end~3-5` or equivalently `~3-5` selects all pages except
    pages 3-5.

    * `~2downz1` selects all pages except page 2, rotating them by
    180 degrees and zooming in 1 step. This will likely need to
    be quoted to prevent your shell misinterpreting it. (The
    same goes for `~3-5`).

    * `end-r4` selects the last 4 pages, in reverse order.

    * `1,3,5` selects pages 1, 3, and 5.

    * `[1,3,5]x2` selects pages 1, 3, and 5 and scales them all.

    """
