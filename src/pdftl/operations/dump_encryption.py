# src/pdftl/operations/dump_encryption.py

"""Dump encryption and permissions info"""

import json
import logging
from typing import TYPE_CHECKING, Any

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.utils.dump import get_json_flag
from pdftl.utils.io_helpers import smart_open_maybe_dash

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)

_DUMP_ENCRYPTION_LONG_DESC = """
Extracts the document's security handlers, encryption algorithm, and
user access permissions, printing them to the console (or a specified file).

If the input document is encrypted with a user password, you must
provide it using the input_pw option to read the permissions.

By default, the output is a plain text, line-based format containing
key-value pairs designed to mirror pdftk's stanza style. Passing the
json parameter will produce a structured JSON output instead.

### Output Format Details

#### Text / Stanza Output
The default output starts with `IsEncrypted: <true|false>`.  If the input document is encrypted, an
`EncryptionBegin` stanza is next with the following fields:

 * `EncryptionAlgorithm: <algorithm_name>`
 * `EncryptionFilter: <security_handler_name>`
 * `EncryptionParameterV: <integer>`
 * `EncryptionParameterR: <integer>`
 * `KeyLengthBits: <integer>`
 * `RawPermissionsFlag: <integer>`

Permission Fields in the following `PermissionBegin` stanza correspond
directly to the arguments used by the `allow` option. Values will be
either `true` (permission allowed) or `false` (permission denied).

 * `PermissionPrinting`
 * `PermissionDegradedPrinting`
 * `PermissionModifyContents`
 * `PermissionAssembly`
 * `PermissionCopyContents`
 * `PermissionScreenReaders`
 * `PermissionModifyAnnotations`
 * `PermissionFillIn`

#### JSON Output
When the `json` flag is provided, the data is structured hierarchically.
`Encryption` is a dictionary containing the algorithm details, and
`Permissions` is a dictionary mapping each permission to a boolean.
"""

_DUMP_ENCRYPTION_EXAMPLES = [
    {
        "cmd": "in.pdf dump_encryption",
        "desc": "Print encryption info for secure.pdf in text format",
    },
    {
        "cmd": 'in.pdf input_pw "secret" dump_encryption json',
        "desc": "Print encryption info for a password-protected file in JSON format",
    },
]

# Map pdftl's permission names to pikepdf's Permissions object attributes
PERM_MAPPING = {
    "Printing": "print_highres",
    "DegradedPrinting": "print_lowres",
    "ModifyContents": "modify_other",
    "Assembly": "modify_assembly",
    "CopyContents": "extract",
    "ScreenReaders": "accessibility",
    "ModifyAnnotations": "modify_annotation",
    "FillIn": "modify_form",
}


def _write_stanza_text_output(data: dict, file_obj) -> None:
    """Formats and writes the encryption data in a pdftk-style stanza text format."""
    is_enc = data.get("IsEncrypted")
    is_enc_str = "true" if is_enc else "false"

    def writer(text):
        print(text, file=file_obj)

    writer(f"IsEncrypted: {is_enc_str}")

    if is_enc:
        writer("EncryptionBegin")
        encryption_details = data.get("Encryption", {})
        for key, val in encryption_details.items():
            writer(f"{key}: {val}")

    if "Permissions" in data:
        writer("PermissionBegin")
        for perm_name, is_allowed in data["Permissions"].items():
            val_str = "true" if is_allowed else "false"
            writer(f"Permission{perm_name}: {val_str}")


def dump_encryption_cli_hook(result: OpResult, stage, _pipeline):
    """
    CLI Hook for dump_encryption.
    Formats the raw structured data into the line-based text format or JSON.
    """
    if not result.data:
        return

    logger.debug("result.meta=%s", result.meta)
    output_file = result.meta.get(c.META_OUTPUT_FILE)  # type: ignore[union-attr]
    json_output = result.meta.get(c.META_JSON_OUTPUT, False)  # type: ignore[union-attr]

    with smart_open_maybe_dash(output_file) as f:
        if json_output:
            json.dump(result.data, f, indent=2)
            f.write("\n")
        else:
            _write_stanza_text_output(result.data, f)


def _determine_algorithm(v: int, length: int, encrypt_dict) -> tuple[str, int]:
    """Determines the encryption algorithm name
    and actual key length based on /V and CryptFilters."""
    if v == 1:
        return "RC4 (40-bit)", length or 40
    if v == 2:
        return "RC4 (128-bit)", length or 128
    if v == 5:
        return "AES (256-bit)", length or 256
    if v == 6:
        return "AES (256-bit, PDF 2.0)", length or 256
    if v == 4:
        return _determine_v4_algorithm(length, encrypt_dict)

    return f"Unknown (V={v})", length


def _determine_v4_algorithm(length: int, encrypt_dict) -> tuple[str, int]:

    algo_name = "AES / RC4 (128-bit)"
    try:
        cfm = encrypt_dict["/CF"]["/StdCF"]["/CFM"]
        if cfm == "/AESV2":
            algo_name = "AES (128-bit)"
        elif cfm == "/V2":
            algo_name = "RC4 (128-bit)"
    except (KeyError, TypeError):
        # Missing or malformed crypt filter structure: keep default v4 algorithm label.
        logger.debug(
            "Could not determine V=4 encryption algorithm from encryption dictionary; using '%s'.",
            algo_name,
            exc_info=True,
        )
    return algo_name, length or 128


def _extract_encryption_info(encrypt_dict) -> dict:
    """Extracts and formats the encryption metadata from the /Encrypt dictionary."""
    filter_val = str(encrypt_dict.get("/Filter", ""))
    v = int(encrypt_dict.get("/V", 0))
    r = int(encrypt_dict.get("/R", 0))
    p = int(encrypt_dict.get("/P", 0))
    length = int(encrypt_dict.get("/Length", 0))

    algo_name, final_length = _determine_algorithm(v, length, encrypt_dict)

    return {
        "EncryptionAlgorithm": algo_name,
        "EncryptionFilter": filter_val.strip("/"),
        "EncryptionParameterV": v,
        "EncryptionParameterR": r,
        "EncryptionKeyLengthBits": final_length,
        "EncryptionRawPermissionsFlag": p,
    }


def _extract_permissions(pdf: "pikepdf.Pdf") -> dict:
    """Maps pikepdf's permission properties to pdftl's permission names."""
    if not pdf.is_encrypted:
        return {perm_name: True for perm_name in PERM_MAPPING.keys()}

    perms = pdf.allow
    return {perm_name: getattr(perms, pike_attr) for perm_name, pike_attr in PERM_MAPPING.items()}


@register_operation(
    "dump_encryption",
    tags=["security", "encryption", "info"],
    type="single input operation",
    desc="Print PDF encryption details and user permissions",
    long_desc=_DUMP_ENCRYPTION_LONG_DESC,
    examples=_DUMP_ENCRYPTION_EXAMPLES,
    cli_hook=dump_encryption_cli_hook,
    usage="<input> dump_encryption [json] [output <output>]",
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {"output_file": c.OUTPUT}),
    skip_pipeline_save=True,
)
def dump_encryption(pdf: "pikepdf.Pdf", operation_args=None, output_file=None) -> OpResult:
    """
    Dumps comprehensive encryption and permission data from a PDF.
    """
    logger.debug("Dumping encryption data for PDF")

    operation_args = operation_args or []
    json_output = get_json_flag(operation_args, "dump_encryption")

    data: dict[str, Any] = {
        "IsEncrypted": pdf.is_encrypted,
    }

    if pdf.is_encrypted:
        encrypt_dict = pdf.trailer.get("/Encrypt")
        if encrypt_dict is not None:
            data["Encryption"] = _extract_encryption_info(encrypt_dict)

    data["Permissions"] = _extract_permissions(pdf)

    return OpResult(
        success=True,
        data=data,
        pdf=pdf,
        is_discardable=True,
        meta={
            c.META_OUTPUT_FILE: output_file,
            c.META_JSON_OUTPUT: json_output,
        },
    )
