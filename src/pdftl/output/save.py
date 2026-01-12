# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/output/save.py

"""Methods for saving PDF files (and other files), with options registered for CLI."""

import inspect
import io
import logging
import sys
from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)
import pdftl.core.constants as c
from pdftl.core.registry import register_option
from pdftl.exceptions import InvalidArgumentError, MissingArgumentError
from pdftl.output.attach import attach_files
from pdftl.output.flatten import flatten_pdf
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
    tags=["security", "encryption", "obselete"],
)
@register_option(
    "encrypt_128bit",
    desc="Use 128 bit encryption (obsolete and insecure)",
    type="flag",
    tags=["security", "encryption", "obselete"],
)
@register_option(
    "encrypt_aes128",
    desc="Use 128 bit AES encryption (default)",
    type="flag",
    tags=["security", "encryption", "legacy"],
)
@register_option(
    "encrypt_aes256",
    desc="Use 256 bit AES encryption",
    type="flag",
    tags=["security", "encryption"],
)
@register_option(
    "no_encrypt_metadata",
    desc="Leave metadata unencrypted (allowing search engines to read title/author)",
    type="flag",
    tags=["encryption", "metadata"],
)
def _encrypt_options():
    pass


_ALLOW_LONG_DESC = """
Files saved with encryption have various possible permissions.
The default encryption permissions are to forbid all possible actions.
Use the `allow` output option to allow permissions selectively.

Arguments `<perm>...` must be zero or more permissions from among
the following. If omitted, the default is to allow all permissions.
Upper/lowercase characters are treated the same.

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
    type="zero or more arguments",
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
@register_option("drop_xfa", desc="Discard form XFA data if present", type="flag")
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
                logger.warning("Password was over 32 characters and will be truncated.")
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
    if perm not in c.ALLOW_PERMISSIONS_MAP:
        raise ValueError(f"Unknown permission '{perm}' in 'allow' list.")
    for flag_name in c.ALLOW_PERMISSIONS_MAP[perm]:
        if flag_name in permissions_dict:
            permissions_dict[flag_name] = True
        else:
            raise ValueError(f"Permission '{perm}' maps to an unknown flag '{flag_name}'.")


def _build_permissions_object(allow_options: list):
    """Builds a pikepdf.Permissions object from the 'allow' options list."""
    import pikepdf

    # default if no options explicitly selected is same as "AllFeatures"
    if not allow_options or "AllFeatures" in allow_options:
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
    default_method_key = "encrypt_aes128"

    encryption_methods = OrderedDict(
        [
            # AES-256 (PDF 2.0 / Extension Level 3)
            ("encrypt_aes256", {"R": 6, "aes": True, "metadata": True}),
            # AES-128 (PDF 1.6 Standard)
            # default
            ("encrypt_aes128", {"R": 4, "aes": True, "metadata": True}),
            # RC4-128 (Legacy / PDF 1.4)
            # despite having to set metadata=False, pikepdf DOES (and CAN ONLY) encrypt metadata when R < 4
            ("encrypt_128bit", {"R": 3, "aes": False, "metadata": False}),
            # RC4-40 (Ancient / PDF 1.1)
            # despite having to set metadata=False, pikepdf DOES (and CAN ONLY) encrypt metadata when R < 4
            ("encrypt_40bit", {"R": 2, "aes": False, "metadata": False}),
        ]
    )

    chosen_method_keys = [opt for opt in options if opt in encryption_methods]
    if len(chosen_method_keys) > 1:
        raise InvalidArgumentError(
            f"Too many encryption options given: {chosen_method_keys}. Choose one."
        )

    if not chosen_method_keys and not passwords:
        return False

    chosen_encryption_method_name = (
        chosen_method_keys[0] if chosen_method_keys else default_method_key
    )
    chosen_encryption_method = encryption_methods[chosen_encryption_method_name].copy()
    if options.get("no_encrypt_metadata"):
        if chosen_encryption_method["aes"]:
            chosen_encryption_method["metadata"] = False
        else:
            logger.warning(
                "Ignoring 'no_encrypt_metadata': this requires an AES encryption method, but '%s' is selected.",
                chosen_encryption_method_name,
            )

    encrypt_opts = {
        "user": passwords.get("user", ""),
        "owner": passwords.get("owner", ""),
    }
    encrypt_opts.update(chosen_encryption_method)

    allow_options = options.get("allow")
    encrypt_opts["allow"] = _build_permissions_object(allow_options or [])

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


def _remove_source_info(pdf):
    for page in pdf.pages:
        if hasattr(page, c.PDFTL_SOURCE_INFO_KEY):
            del page["/" + c.PDFTL_SOURCE_INFO_KEY]


# ---------------------------------------------------------------------------
# Public save API
# ---------------------------------------------------------------------------
def save_content(content, output_path, input_context, **kwargs):
    """
    Determines the appropriate saving strategy based on the content type.
    """
    import types

    # Handle Generators (e.g., burst or render)
    if isinstance(content, (types.GeneratorType, list)):
        for filename, item in content:
            try:
                _save_by_type(item, filename, input_context, **kwargs)
            finally:
                _cleanup_item(item)

    # Handle Single Objects
    else:
        _save_by_type(content, output_path, input_context, **kwargs)


def _cleanup_item(item):
    """Closes objects if they require it (like pikepdf.Pdf)."""
    import pikepdf

    if isinstance(item, pikepdf.Pdf):
        logger.debug("Closing pikepdf object during generator cleanup.")
        item.close()
    # PIL Images are garbage collected once the reference is gone,
    # but you can call .close() on them too if they are file-based.
    elif hasattr(item, "close"):
        item.close()


def _save_by_type(item, path, input_context, **kwargs):
    """The actual 'figuring out' part for saving an unknown item."""

    # 1. Is it a PIL Image? (from render)
    if hasattr(item, "format") or str(type(item)).find("PIL") != -1:
        logger.debug("Routing to image saver: %s", path)
        # Note: PIL.save usually doesn't take the same kwargs as pikepdf
        item.save(path)

    # 2. Is it a PDF? (from pikepdf)
    elif hasattr(item, "save"):
        logger.debug("Routing to PDF saver: %s", path)
        save_pdf(item, path, input_context, **kwargs)

    else:
        raise TypeError(f"Unknown content object type: {type(item)}")


def _action_drop_flags(pdf, options):
    if options.get("drop_info"):
        del pdf.docinfo
    if options.get("drop_xmp") and "/Metadata" in pdf.Root:
        del pdf.Root.Metadata
    if options.get("drop_xfa"):
        if "/AcroForm" in pdf.Root:
            acro_form = pdf.Root["/AcroForm"]
            if "/XFA" in acro_form:
                # Delete the XFA entry
                del acro_form["/XFA"]


def save_pdf(pdf, output_filename, input_context, options=None, set_pdf_id=None):
    """
    Saves a PDF with various options like encryption, compression, and attachments.
    """
    if options is None:
        options = {}
    if not output_filename:
        raise MissingArgumentError("An output file must be specified with the 'output' keyword.")

    logger.debug("Preparing to save to '%s' with options %s", output_filename, options)

    _remove_source_info(pdf)

    _action_drop_flags(pdf, options)

    if options.get("flatten"):
        # breakpoint()
        # pdf.flatten_annotations()
        pdf = flatten_pdf(pdf)

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
        if output_filename == "-":
            raise NotImplementedError("Signing and saving to stdout is not yet implemented")
        sign_cfg = parse_sign_options(options, input_context)
        save_and_sign(pdf, sign_cfg, save_opts, output_filename)
    else:
        if output_filename == "-":
            save_to_stdout(pdf, save_opts)

        else:
            pdf.save(output_filename, **save_opts)


def save_to_stdout(pdf: "pikepdf.Pdf", save_opts: dict):
    import pikepdf

    # 1. Create an in-memory bytes buffer
    with io.BytesIO() as buffer:
        # 2. Save the pikepdf object into the buffer
        pdf.save(buffer, **save_opts)

        # 3. Get the raw bytes content
        pdf_bytes = buffer.getvalue()

        # 4. Write raw bytes to the stdout buffer
        # 'sys.stdout' expects text (str), but 'sys.stdout.buffer' expects bytes.
        try:
            sys.stdout.buffer.write(pdf_bytes)
            sys.stdout.buffer.flush()
        except BrokenPipeError:
            # Handle the case where the consumer (e.g., 'head') closes the pipe early
            # This prevents ugly tracebacks in your CLI tool
            sys.stderr.close()
