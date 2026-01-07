# src/pdftl/operations/update_catalog.py

import pdftl.core.constants as c
from pdftl.catalog.catalog_types import PdfCatalog, merge_catalog_data
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.exceptions import InvalidArgumentError, MissingArgumentError, UserCommandLineError
from pdftl.operations.parsers.update_catalog_parser import update_catalog_parser
from pdftl.utils.arg_helpers import resolve_operation_spec


@register_operation(
    "update_catalog",
    tags=["catalog"],
    type="single input operation",
    desc="Update PDF Catalog from dump instructions",
    usage="<input> update_catalog <metadata> output <filename>",
    args=([c.INPUT_PDF, c.OPERATION_ARGS, c.GET_INPUT], {}, {}),
)
def update_catalog(pdf, op_args, get_input) -> OpResult:
    if len(op_args) > 1:
        raise InvalidArgumentError(f"Unexpected arguments: {' '.join(op_args[1:])}")
    if not op_args:
        raise MissingArgumentError("update_catalog requires a <metadata> argument")

    meta_filename = op_args[0]
    # ... handle PROMPT and '-' exactly like update_info ...
    if meta_filename == "-":
        meta_filename = None

    try:
        result = resolve_operation_spec(
            [meta_filename],
            update_catalog_parser,
            model_class=None,
            data={},
        )

        if isinstance(result, PdfCatalog):
            spec = result
        elif isinstance(result, dict):
            spec = PdfCatalog.from_dict(result)
        else:
            raise TypeError(f"Unexpected result type: {type(result)}")

    except Exception as exc:
        raise UserCommandLineError(exc) from exc

    return execute_update_catalog(pdf, spec)


def execute_update_catalog(pdf, spec: PdfCatalog):
    merge_catalog_data(pdf.Root, spec, pdf)
    return OpResult(success=True, pdf=pdf)
