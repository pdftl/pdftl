# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/output/sign.py

"""Signing PDF files using pyhanko"""

import io
import logging
import os

from pdftl.core.registry import register_help_topic, register_option
from pdftl.exceptions import UserCommandLineError

logger = logging.getLogger(__name__)


def save_and_sign(pdf, sign_cfg, save_opts, output_filename):

    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers

    # 1. Correctly extract the Encryption object
    enc_obj = save_opts.get("encryption")
    user_pw = ""
    owner_pw = ""

    if enc_obj:
        # Extract the actual values from the pikepdf.Encryption object
        user_pw = enc_obj.user or ""
        owner_pw = enc_obj.owner or ""
        print(f"[DEBUG] Found encryption object. User: {user_pw}, Owner: {owner_pw}")

    # 2. Save pikepdf state to buffer WITH the encryption object
    # Passing the enc_obj directly back to save() is the most reliable way
    buffer = io.BytesIO()
    pdf.save(buffer, encryption=enc_obj, static_id=True)
    buffer.seek(0)

    # Verify the buffer
    if b"/Encrypt" in buffer.getvalue():
        print("[DEBUG] SUCCESS: Buffer is now encrypted.")
    else:
        print("[DEBUG] ERROR: Buffer still missing /Encrypt. Check if pdf object is modified.")

    # 3. Setup pyHanko writer
    w = IncrementalPdfFileWriter(buffer)
    if enc_obj:
        # Use the single-argument encrypt() which handles both
        # decrypting the reader and cloning the encryption for the writer.
        pw_to_use = (user_pw or owner_pw).encode("utf-8")
        w.encrypt(user_pwd=pw_to_use)
        print("[DEBUG] pyHanko writer encrypted (cloned from base)")

    # 4. Load the Signer
    key_and_cert = (sign_cfg["key"], sign_cfg["cert"])
    key_passphrase = sign_cfg["passphrase"].encode() if sign_cfg["passphrase"] else None

    # (Logger logic omitted for brevity, but keep it if you prefer)
    cms_signer = signers.SimpleSigner.load(*key_and_cert, key_passphrase=key_passphrase)

    # 5. Apply Signature
    requested_field = sign_cfg["field"] or "Signature1"
    with open(output_filename, "wb") as out_file:
        signers.sign_pdf(
            w,
            signers.PdfSignatureMetadata(field_name=requested_field),
            signer=cms_signer,
            output=out_file,
        )


def parse_sign_options(options, input_context):
    """
    Extracts and validates sign_* keywords from the options dictionary.
    """
    sign_cfg = {
        "key": options.get("sign_key"),
        "cert": options.get("sign_cert"),
        "field": options.get("sign_field"),
        "passphrase": None,
    }

    if not sign_cfg["key"] or not sign_cfg["cert"]:
        raise UserCommandLineError("Digital signing requires both 'sign_key' and 'sign_cert'.")

    if "sign_pass_env" in options:
        sign_cfg["passphrase"] = os.environ.get(options["sign_pass_env"])
        if not sign_cfg["passphrase"]:
            raise UserCommandLineError(
                f"Environment variable {options['sign_pass_env']} not found."
            )
    elif options.get("sign_pass_prompt"):
        # Uses the context helper to securely get the password
        sign_cfg["passphrase"] = input_context.get_pass("Enter private key passphrase: ")

    return sign_cfg


@register_option(
    "sign_key <file>",
    desc="Path to private key PEM",
    type="one mandatory argument",
    long_desc="""
    The path to your private key file (`.pem`). This is required for signing. See also `sign_cert`.
    """,
    tags=["security", "signatures"],
)
def _sign_key_option():
    pass


@register_option(
    "sign_cert <file>",
    desc="Path to certificate PEM",
    type="one mandatory argument",
    long_desc=""" The path to your certificate file (`.pem`), also
    known as the public key. This is required for signing.  """,
    tags=["security", "signatures"],
)
def _sign_cert_option():
    pass


@register_option(
    "sign_field <name>",
    desc="Signature field name (default: `Signature1`)",
    type="one mandatory argument",
    tags=["security", "signatures"],
)
def _sign_field_option():
    pass


@register_option(
    "sign_pass_env <var>",
    desc="Environment variable with `sign_cert` passphrase",
    long_desc=""" The name of an environment variable containing the
    passphrase for your public signing certificate, as specificed by
    `sign_cert`.  """,
    type="one mandatory argument",
    tags=["security", "signatures"],
)
def _sign_pass_env_option():
    pass


@register_option(
    "sign_pass_prompt",
    desc="Prompt for `sign_cert` passphrase",
    type="flag",
    tags=["security", "signatures"],
)
def _sign_pass_prompt_option():
    pass


@register_help_topic(
    "signing",
    title="signing PDF files",
    desc="Adding crytographic signatures to PDF files",
)
def _help_topic_signing():
    """`pdftl` supports high-integrity digital signing of PDF
    documents. These signatures are applied using **Incremental
    Updates**, ensuring that the original document structure is
    preserved and the signature remains cryptographically valid.

    ### Key Concepts

    * **Cryptographic Integrity:** Every signature ensures the
        document has not been modified since the signature was
        applied.

    * **Incremental Saving:** `pdftl` saves the document and then
        appends the signature. This is the industry-standard method
        for signing PDFs without corrupting existing data.

    * **Invisible Signatures:** By default, signatures are
        "invisible." They do not appear as a stamp on the page but are
        fully recognized by the "Signatures" panel in Adobe Acrobat,
        `pdfsig`, and other professional validators.

    ### Command Line Usage

    To sign a document, you must provide both a private key and a
    matching certificate in PEM format.

    #### Basic Signing
    ```bash
    pdftl input.pdf output signed_output.pdf \\
          sign_key path/to/private_key.pem \\
          sign_cert path/to/certificate.pem

    ```

    #### Arguments
    | Argument | Description | Required |
    | --- | --- | --- |
    | `sign_key` | Path to your private key file (`.pem`). | Yes (for signing) |
    | `sign_cert` | Path to your certificate file (`.pem`). | Yes (for signing) |
    |`sign_pass_env` <`var`>|An environment variable with your public certificate passphrase |No|
    |`sign_pass_prompt` | Ask to be prompted for your public certificate passphrase | No |
    |`sign_field` <`name`> | The name of a signature field to use (default: `Signature1`)| No |

    ### Technical Specifications

    * **Algorithm:** All signatures use **RSA with SHA-256** (OIDs:
        `1.2.840.113549.1.1.11` and `2.16.840.1.101.3.4.2.1`).

    * **Sub-Filter:** Uses `adbe.pkcs7.detached`, ensuring
        compatibility with virtually all PDF viewers.

    * **ByteRange:** The signature covers the entire file contents.

    ### Verification

    You can verify the signature using standard third-party tools.

    #### Using `pdfsig` (Linux/Poppler)
    ```bash
    pdfsig signed_output.pdf

    ```

    **Expected Output:**

    > `- Signature Validation: Signature is Valid.`

    #### Using `okular`
    Open the file and click the "signatures" button that should appear.


    #### Using Adobe Acrobat

    1. Open the PDF in Adobe Acrobat Reader.

    2. Look for the **"Signature Panel"** button at the top right.

    3. The status should show:

    *"Signature is valid, signed by [Your Name]."*

    ### Troubleshooting

    * **"Missing EKU Error"**: Ensure your certificate includes the
        **Adobe PDF Signing OID** (`1.2.840.113583.1.1.5`).

    * **Invalid Signature**: If you attempt to modify a signed PDF
        using a tool that does not support incremental updates, the
        signature will break. Always perform your edits (merging, text
        overlays) **before** or **during** the `pdftl` command that
        applies the signature.


    ##  Generating files needed for PDF signing

    Generating a compatible certificate for PDF signing requires a
    specific extension called **Extended Key Usage (EKU)**. Without
    it, many PDF viewers (like Adobe Acrobat) will display a warning
    that the certificate is not intended for digital signatures.

    You can generate these files using the OpenSSL command line.

    ### 1. Create a Configuration File

    Standard OpenSSL commands for "web" certificates don't include the
    Adobe-specific OID. Create a small file named `pdf_cert.conf`:

    ```ini
    [req]
    distinguished_name = req_distinguished_name
    x509_extensions = v3_ext
    prompt = no

    [req_distinguished_name]
    CN = PDTTL Test Certificate

    [v3_ext]
    # This OID (1.2.840.113583.1.1.5) tells viewers this is a PDF Signing cert
    extendedKeyUsage = 1.2.840.113583.1.1.5

    ```

    ### 2. Generate an Unencrypted Key & Cert

    If you want to test the basic functionality without a passphrase
    (the "unencrypted" path):

    ```bash
    openssl req -x509 -newkey rsa:2048 -nodes -keyout test_key.pem \
        -out test_cert.pem -days 365 -config pdf_cert.conf

    ```

    ### 3. Generate an Encrypted Key

    To generate a passowrd protected key:

    ```bash
    # This will prompt you for a password during generation
    openssl req -x509 -newkey rsa:2048 -keyout test_key_encrypted.pem \
        -out test_cert.pem -days 365 -config pdf_cert.conf

    ```

    ### Verifying the Certificate

    Before using it in `pdftl`, you can verify that the "PDF Signing"
    extension was correctly embedded:

    ```bash
    openssl x509 -in test_cert.pem -text -noout | grep -A 1 "Extended Key Usage"

    ```

    **Expected Output:**

    > `X509v3 Extended Key Usage:`
    > `1.2.840.113583.1.1.5`

    ### Summary of Files

    * **`test_key.pem`**: Your private key. Keep this secret.

    * **`test_cert.pem`**: Your public certificate. This is what gets
        embedded in the PDF so others can verify your signature.

    """
