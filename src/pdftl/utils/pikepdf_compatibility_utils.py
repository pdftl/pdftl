# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/pikepdf_compatibility_utils.py


def pikepdf_version():
    from pikepdf import __version__ as version

    parsed = []
    for part in version.split("."):
        try:
            parsed.append(int(part))
        except ValueError:
            break
    return parsed or [0, 0, 0]


def pikepdf_version_at_least(x):
    ver = pikepdf_version()
    if isinstance(x, int):
        x = [x]
    if isinstance(x, list):
        return ver >= x
    return False


def outline_item_has_style_properties():
    """pikepdf >= 10.11.0 exposes OutlineItem.bold/.italic/.color properties
    that correctly persist through outline commit. Before that version (and
    in fact for versions >= 10.11.0 too, but harmlessly), styling had to be
    applied by mutating item.obj.F / item.obj.C directly after
    to_dictionary_object(). In 10.11.0+, raw obj mutations on freshly built
    (not-yet-committed) OutlineItems are silently dropped when the outline
    is serialized, so the property setters must be used instead."""
    return pikepdf_version_at_least([10, 11, 0])


def set_outline_item_style_compat(item, color=None, bold=False, italic=False):
    """Apply color/bold/italic styling to a freshly-built, not-yet-committed
    pikepdf.OutlineItem, in a way that persists correctly across pikepdf
    versions.

    pikepdf >= 10.11.0 introduced .color/.bold/.italic property setters on
    OutlineItem. These must be used on that version and later: raw
    item.obj.C / item.obj.F mutations on a not-yet-committed item are
    silently dropped when the outline is serialized. Older pikepdf lacks
    these properties entirely, so we fall back to direct obj mutation
    there, which works correctly on those versions.
    """
    if pikepdf_version_at_least([10, 11, 0]):
        if color is not None:
            item.color = tuple(color)
        if bold:
            item.bold = True
        if italic:
            item.italic = True
    else:
        import pikepdf

        if color is not None:
            item.obj.C = pikepdf.Array(color)
        if bold or italic:
            item.obj.F = (2 if bold else 0) + (1 if italic else 0)


def as_pil_image_compat(image):
    if pikepdf_version_at_least([10, 10, 0]):
        return image.as_pil_image(apply_mask=False)
    return image.as_pil_image()
