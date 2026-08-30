# src/pdftl/api.pyi
from typing import Any, Dict, List, Optional, Union
import pikepdf

def add_bookmarks(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def add_images(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def add_marks(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def add_text(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def attach_files(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def background(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def barcode(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def booklet(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def burst(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    opened_pdfs: Optional[List[pikepdf.Pdf]] = ...,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def cat(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    inputs: Optional[List[str]] = ...,
    operation_args: Optional[List[str]] = ...,
    opened_pdfs: Optional[List[pikepdf.Pdf]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def chop(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def clip(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def create(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def crop(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def deduplicate_fonts(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def deduplicate_icc_profiles(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def deduplicate_images(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def delete(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def delete_actions(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def delete_annots(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def delete_attachments(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def delete_blank(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def delete_bookmarks(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def delete_images(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def deskew(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def diff_text(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def dump_actions(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def dump_annots(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def dump_bookmarks(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def dump_colorspaces(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def dump_data(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def dump_data_annots(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def dump_data_fields(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def dump_data_fields_utf8(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def dump_data_utf8(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def dump_dests(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def dump_encryption(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def dump_files(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def dump_fonts(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def dump_images(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def dump_layers(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def dump_signatures(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    password: Optional[str] = ...,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
) -> pikepdf.Pdf: ...
def dump_streams(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def dump_tables(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def dump_tags(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def dump_text(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    password: Optional[str] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
) -> pikepdf.Pdf: ...
def embed_fonts(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def excise(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def export_fonts(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def export_images(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def fill_form(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def filter(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def generate_fdf(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def grep(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def highlight(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def import_fonts(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def import_images(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def import_streams(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def inject(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def insert(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def link_urls(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def modify_annots(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def modify_images(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def modify_layers(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def montage(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def move(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def multibackground(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def multistamp(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def mutate_content(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def normalize(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def optimize_images(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def place(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def recolor_images(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def recolor_vectors(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def redact(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def render(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def replace(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def resample_images(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def rotate(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def server(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def set(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def shuffle(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    inputs: Optional[List[str]] = ...,
    operation_args: Optional[List[str]] = ...,
    opened_pdfs: Optional[List[pikepdf.Pdf]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def simplify_vectors(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def stamp(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def stamp_fields(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def style_text(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def subset_fonts(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def tag(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def unpack_files(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def unpause(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def update_bookmarks(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def update_info(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def update_info_utf8(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def usage(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    output: Optional[str] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
def zoom(
    pdf: Optional[Union[pikepdf.Pdf, str]] = None,
    operation_args: Optional[List[str]] = ...,
    run_cli_hook: bool = False,
    full_result: bool = False,
    password: Optional[str] = None,
) -> pikepdf.Pdf: ...
