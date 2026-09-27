# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/mrc/splice.py

"""Splice MRC layers into a page's content stream, replacing the scan's `Do`.

Only handles what `classify.classify_page` produces: one top-level `Do`
of the scan image.

The layers are unit-square images drawn where the original `Do` was, so
they inherit its placement. Nothing else on the page is changed.
"""

from __future__ import annotations

from pdftl.utils.mrc import MrcError


def _clone_dict(d) -> object:
    import pikepdf

    return pikepdf.Dictionary(dict(d) if d is not None else {})


def _owned_xobject_dict(pdf, page):
    """Install a fresh copy of the page's /XObject dict and return it.

    Inherited /Resources are first copied onto the page.
    """
    obj = page.obj
    if "/Resources" not in obj:
        from pdftl.utils.mrc.classify import resolve_resources

        inherited = resolve_resources(page)
        obj["/Resources"] = pdf.make_indirect(_clone_dict(inherited))
    resources = obj["/Resources"]

    xobjects = resources.get("/XObject")
    owned = pdf.make_indirect(_clone_dict(xobjects))
    resources["/XObject"] = owned
    return owned


def _fresh_name(xobjects, prefix: str) -> str:
    import pikepdf

    i = 0
    while True:
        candidate = f"/{prefix}{i}"
        if pikepdf.Name(candidate) not in xobjects:
            return candidate
        i += 1


def splice_layers(pdf, page, candidate, bg_xobj, fg_xobj) -> None:
    """Replace the first `Do` of `candidate.xobj_name` with `bg_xobj` then `fg_xobj`.

    `candidate` is a `classify.Candidate`. Raises RuntimeError if no such
    `Do` is found.
    """
    import pikepdf

    xobjects = _owned_xobject_dict(pdf, page)

    bg_name = _fresh_name(xobjects, "MRCBg")
    fg_name = _fresh_name(xobjects, "MRCFg")
    xobjects[pikepdf.Name(bg_name)] = bg_xobj
    xobjects[pikepdf.Name(fg_name)] = fg_xobj

    # Resource names are not scoped by q/Q, so the first matching `Do` is
    # the classified one.
    target_name = candidate.xobj_name
    new_instructions = []
    replaced = False
    for operands, operator in pikepdf.parse_content_stream(page):
        if not replaced and str(operator) == "Do" and operands and str(operands[0]) == target_name:
            new_instructions.append(([pikepdf.Name(bg_name)], pikepdf.Operator("Do")))
            new_instructions.append(([pikepdf.Name(fg_name)], pikepdf.Operator("Do")))
            replaced = True
            continue
        new_instructions.append((operands, operator))

    if not replaced:
        raise MrcError(
            f"MRC splice: could not find the classified placement {target_name!r} "
            f"in page {candidate.page_number}'s content stream"
        )

    orig_name = pikepdf.Name(target_name)
    if orig_name in xobjects:
        del xobjects[orig_name]

    page.obj["/Contents"] = pdf.make_stream(pikepdf.unparse_content_stream(new_instructions))
