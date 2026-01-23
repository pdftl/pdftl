import logging
from typing import Any

from pdftl.core.types import OpResult
from pdftl.utils.io_helpers import smart_open_maybe_dash

logger = logging.getLogger(__name__)


def consume_output_option(stage):
    """
    Unconditionally removes the 'output' option from the stage.
    Call this ONLY if your hook has successfully written an output file
    and you want to prevent the pipeline from saving the PDF there.
    """
    logger.debug("consume_output_option")
    if hasattr(stage, "options"):
        logger.debug("consuming")
        stage.options.pop("output", None)


def _get_output_path(stage):
    """Simple lookup: checks stage options."""
    return stage.options.get("output", None)


def text_dump_hook(result: OpResult, stage):
    """
    Writes text to file (and consumes output option) or stdout.
    """
    logger.debug("text_dump_hook")
    if not result.success or result.data is None:
        return

    output_path = _get_output_path(stage)
    logger.debug("output_path=%s", output_path)
    data_str = str(result.data)

    with smart_open_maybe_dash(output_path) as f:
        f.write(data_str)
        if len(data_str) > 0 and not str(result.data).endswith("\n"):
            f.write("\n")

    consume_output_option(stage)


def json_dump_hook(result: OpResult, stage):
    """
    Writes JSON to file (and consumes output option) or stdout.
    """
    import json

    if not result.success or result.data is None:
        return

    output_path = _get_output_path(stage)

    with smart_open_maybe_dash(output_path) as f:
        json.dump(result.data, f, indent=2, default=str)
        f.write("\n")

    consume_output_option(stage)


def from_result_meta(result: OpResult, attrib: str) -> Any:
    if result.meta is None:
        raise ValueError("result.meta is None")
    return result.meta.get(attrib)


def str_from_result_meta(result: OpResult, attrib: str) -> str:
    if not isinstance(ret := from_result_meta(result, attrib), str):
        raise TypeError("Wrong type: expected str")
    return ret
