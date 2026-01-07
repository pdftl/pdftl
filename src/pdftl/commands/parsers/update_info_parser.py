# src/pdftl/commands/parsers/update_info_parser.py

from pdftl.info.info_types import PdfInfo
from pdftl.info.parse_dump import parse_dump_data
from pdftl.utils.io_helpers import smart_open_output
from pdftl.utils.string import xml_decode_for_info


def update_info_parser(op_args: list[str], data: dict[str, bool]) -> PdfInfo:
    """
    Parses legacy 'update_info' CLI arguments (filename).
    """
    string_decoder = xml_decode_for_info if data.get("xml_strings", False) else lambda x: x

    # op_args is e.g. ["metadata.txt"]
    with smart_open_output(op_args[0], mode="r") as meta_file:
        meta_dict = parse_dump_data(meta_file.readlines(), string_decoder)
        return PdfInfo.from_dict(meta_dict)
