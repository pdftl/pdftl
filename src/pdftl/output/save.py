# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/output/save.py

"""Methods for saving PDF files, with options registered for CLI."""

import inspect
import logging
from collections import OrderedDict

logger = logging.getLogger(__name__)
from pdftl.core.constants import ALLOW_PERMISSIONS_MAP
from pdftl.core.registry import register_option
from pdftl.exceptions import InvalidArgumentError, MissingArgumentError
from pdftl.output.attach import attach_files
from pdftl.output.sign import parse_sign_options, save_and_sign

# ---------------------------------------------------------------------------
# Register options for PDF output
# ---------------------------------------------------------------------------


@register_option(
    "output <file>",
    desc="The output file path, or a template for `burst`",
    type="one mandatory argument",
)
def _output_option():
    pass


@register_option(
    "owner_pw <pw>",
    desc="Set owner password and encrypt output",
    type="one mandatory argument",
    tags=["security", "encryption"],
)
def _owner_pw_option():
    pass


@register_option(
    "user_pw <pw>",
    desc="Set user password and encrypt output",
    type="one mandatory argument",
    tags=["security", "encryption"],
)
def _user_pw_option():
    pass


@register_option(
    "encrypt_40bit",
    desc="Use 40 bit encryption (obsolete, highly insecure)",
    type="flag",
    tags=["obselete"],
)
@register_option(
    "encrypt_128bit",
    desc="Use 128 bit encryption (obsolete, maybe insecure)",
    type="flag",
    tags=["obselete"],
)
@register_option(
    "encrypt_aes128",
    desc="Use 128 bit AES encryption (maybe obsolete)",
    type="flag",
    tags=["obselete"],
)
@register_option(
    "encrypt_aes256",
    desc="Use 256 bit AES encryption",
    type="flag",
    tags=["security", "encryption"],
)
def _encrypt_options():
    pass


_ALLOW_LONG_DESC = """
Files saved with encryption have various possible permissions.
The default encryption permissions are to forbid all possible actions.
Use the `allow` output option to allow permissions selectively.

Arguments `<perm>...` must be one or more permissions from among
the following. Upper/lowercase characters are treated the same.

|Permission `<perm>`|Allows|
|-|-|
|Printing|           standard printing|
|DegradedPrinting|   low quality printing|
|ModifyContents|     modification and "assembly"|
|Assembly|           "assembly"|
|CopyContents|       copying and "screenreaders"|
|ScreenReaders|      "screenreaders"|
|ModifyAnnotations|  modifying annotations|
|FillIn|             filling in forms|
|AllFeatures|        all of the above|

Note: Screenreaders are allowed to be used by modern PDF readers,
regardless of these permissions settings.
"""


@register_option(
    "allow <perm>...",
    desc="Specify permissions for encrypted files",
    type="one or more mandatory arguments",
    long_desc=_ALLOW_LONG_DESC,
    tags=["security", "encryption"],
)
def _allow_option():
    pass


@register_option("compress", desc="Compress output file streams (default)", type="flag")
@register_option(
    "uncompress",
    desc="Disable compression of output file streams",
    type="flag",
    tags=["compression"],
)
def _compress_options():
    pass


@register_option("linearize", desc="Linearize output file(s)", type="flag")
def _linearize_option():
    pass


@register_option("drop_info", desc="Discard document-level info metadata", type="flag")
@register_option("drop_xmp", desc="Discard document-level XMP metadata", type="flag")
def _drop_options():
    pass


@register_option("flatten", desc="Flatten all annotations", type="flag")
def _flatten_option():
    pass


@register_option("keep_first_id", desc="Copy first input PDF's ID metadata to output", type="flag")
@register_option("keep_final_id", desc="Copy final input PDF's ID metadata to output", type="flag")
def _keep_id_options():
    pass


@register_option(
    "need_appearances", desc="Set a form rendering flag in the output PDF", type="flag"
)
def _need_appearances_option():
    pass


# ---------------------------------------------------------------------------
# Internal helpers for saving
# ---------------------------------------------------------------------------


def _get_passwords_from_options(options, input_context):
    """Handles password retrieval, including interactive prompts."""
    passwords = {}
    for pw_type in ["user", "owner"]:
        pw = options.get(f"{pw_type}_pw")
        if pw == "PROMPT":
            prompt = (
                f"Please enter the {pw_type} password "
                "for the output PDF (max 32 chars, can be empty): "
            )
            pw = input_context.get_pass(prompt=prompt)
            if len(pw) > 32:
                print("Warning: Password was over 32 characters and will be truncated.")
                pw = pw[:32]
        if pw is not None:
            passwords[pw_type] = pw
    return passwords


def _default_permissions_object():
    """Return default permission flags: all False (permission denied)"""
    import pikepdf

    return {
        flag: False
        for flag, _ in inspect.getmembers(pikepdf.Permissions(), lambda x: isinstance(x, bool))
    }


def _set_permission_or_raise_error(perm, permissions_dict):
    if perm not in ALLOW_PERMISSIONS_MAP:
        raise ValueError(f"Unknown permission '{perm}' in 'allow' list.")
    for flag_name in ALLOW_PERMISSIONS_MAP[perm]:
        if flag_name in permissions_dict:
            permissions_dict[flag_name] = True
        else:
            raise ValueError(f"Permission '{perm}' maps to an unknown flag '{flag_name}'.")


def _build_permissions_object(allow_options: list):
    """Builds a pikepdf.Permissions object from the 'allow' options list."""
    import pikepdf

    if "AllFeatures" in allow_options:
        # The default pikepdf.Permissions constructor seems to allow all
        # except for assembly. So we specify that.
        return pikepdf.Permissions(modify_assembly=True)

    # our default is all permissions denied
    permissions_dict = _default_permissions_object()

    for perm in allow_options:
        _set_permission_or_raise_error(perm, permissions_dict)

    return pikepdf.Permissions(**permissions_dict)


def _build_encryption_object(options, input_context):
    """Constructs the pikepdf.Encryption object from all related options."""
    passwords = _get_passwords_from_options(options, input_context)

    encryption_methods = OrderedDict(
        [
            ("encrypt_aes256", {"R": 6}),
            ("encrypt_aes128", {"aes": True, "R": 4}),
            ("encrypt_128bit", {"aes": False, "metadata": False, "R": 3}),
            ("encrypt_40bit", {"R": 2, "metadata": False, "aes": False}),
        ]
    )

    chosen_methods = [opt for opt in options if opt in encryption_methods]
    if len(chosen_methods) > 1:
        raise InvalidArgumentError(
            f"Too many encryption options given: {chosen_methods}. Choose one."
        )

    if not chosen_methods and not passwords:
        return False

    encrypt_opts = {
        "user": passwords.get("user", ""),
        "owner": passwords.get("owner", ""),
    }
    if chosen_methods:
        encrypt_opts.update(encryption_methods[chosen_methods[0]])

    allow_options = options.get("allow")
    if allow_options:
        encrypt_opts["allow"] = _build_permissions_object(allow_options)
    elif passwords or chosen_methods:
        encrypt_opts["allow"] = _build_permissions_object([])

    logger.debug("Final encryption options: %s", encrypt_opts)

    import pikepdf

    return pikepdf.Encryption(**encrypt_opts)


def _build_save_options(options, input_context):
    """Builds the final keyword arguments dictionary for pikepdf.save()."""
    import pikepdf

    encryption_object = _build_encryption_object(options, input_context)
    if options.get("allow") and not encryption_object:
        logger.warning("Encryption not requested, so 'allow' permissions will be ignored.")

    use_uncompress = options.get("uncompress", False)
    return {
        "linearize": bool(options.get("linearize")),
        "encryption": encryption_object,
        "compress_streams": not use_uncompress,
        "object_stream_mode": (
            pikepdf.ObjectStreamMode.disable
            if use_uncompress
            else pikepdf.ObjectStreamMode.generate
        ),
    }


# ---------------------------------------------------------------------------
# Public save API
# ---------------------------------------------------------------------------


def save_pdf(pdf, output_filename, input_context, options=None, set_pdf_id=None):
    """
    Saves a PDF with various options like encryption, compression, and attachments.
    """
    if options is None:
        options = {}
    if not output_filename:
        raise MissingArgumentError("An output file must be specified with the 'output' keyword.")

    logger.debug("Preparing to save to '%s' with options %s", output_filename, options)

    if options.get("flatten"):
        pdf.flatten_annotations()

    attach_files(pdf, options, input_context)

    if options.get("need_appearances"):
        import pikepdf

        try:
            pdf.Root.AcroForm[pikepdf.Name.NeedAppearances] = True
        except AttributeError as e:
            logger.warning("Problem setting need_appearances: %s %s", e.__class__.__name__, e)

    save_opts = _build_save_options(options, input_context)

    if set_pdf_id:
        pdf.trailer.ID = set_pdf_id

    logger.debug("Save options for pikepdf: %s", save_opts)

    is_signing = any(k.startswith("sign_") for k in options)
    if is_signing:
        sign_cfg = parse_sign_options(options, input_context)
        save_and_sign(pdf, sign_cfg, save_opts, output_filename)
    else:
        pdf.save(output_filename, **save_opts)
