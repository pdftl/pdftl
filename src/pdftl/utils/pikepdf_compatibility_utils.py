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


def as_pil_image_compat(image):
    if pikepdf_version_at_least([10, 10, 0]):
        return image.as_pil_image(apply_mask=False)
    return image.as_pil_image()
