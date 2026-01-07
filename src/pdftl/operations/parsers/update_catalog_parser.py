# src/pdftl/operations/parsers/update_catalog_parser.py

from pdftl.catalog.catalog_types import PdfCatalog
from pdftl.catalog.parse_catalog import parse_catalog_dump
from pdftl.utils.io_helpers import smart_open_output


def update_catalog_parser(op_args: list[str], data: dict = None) -> PdfCatalog:
    """
    Parses 'update_catalog' CLI arguments (filename).
    Identical design to update_info_parser.
    """
    # op_args[0] is the metadata filename
    with smart_open_output(op_args[0], mode="r") as meta_file:
        meta_dict = parse_catalog_dump(meta_file.readlines())
        return PdfCatalog.from_dict(meta_dict)
