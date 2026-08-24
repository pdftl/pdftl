# src/pdftl/utils/run_opendataloader.py
import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


def _get_converter():
    """Helper wrapper for lazy third-party import."""
    import opendataloader_pdf

    return opendataloader_pdf


def run_opendataloader_extraction(pdf):
    """Run OpenDataLoader PDF conversion and load output JSON data."""
    try:
        opendataloader_pdf = _get_converter()
    except (ImportError, ModuleNotFoundError) as e:
        logger.error("opendataloader_pdf is not installed.")
        raise e

    with tempfile.TemporaryDirectory() as tmp_dir:
        input_pdf_path = os.path.join(tmp_dir, "input.pdf")
        pdf.save(input_pdf_path)
        logger.debug("Saved temporary PDF for extraction to %s", input_pdf_path)

        logger.debug("Executing opendataloader_pdf.convert(...)")
        opendataloader_pdf.convert(
            input_path=input_pdf_path,
            output_dir=tmp_dir,
            format="json",
            quiet=True,
        )
        json_path = os.path.join(tmp_dir, "input.json")
        if not os.path.exists(json_path):
            logger.debug("Conversion failed: output JSON not found at %s", json_path)
            return None

        logger.debug("Reading extracted JSON content from %s", json_path)
        with open(json_path, encoding="utf-8") as f:
            return json.load(f)
