# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/pages/link_remapper.py
"""
PDF link remapping utilities.

This module defines the :class:`LinkRemapper` class, which manages the process of
rewriting link actions when combining or transforming PDF documents.

When pages are copied, rotated, scaled, or merged between PDFs, any internal
link destinations (e.g., GoTo actions or named destinations) must be updated so
they continue to point to the correct targets in the output document. This module
encapsulates that logic and provides safe mechanisms to:

  • Copy or rebind action dictionaries into a new PDF context.
  • Resolve named and explicit link destinations.
  • Apply coordinate transformations for rotated or scaled pages.
  • Preserve or rewrite named destinations with unique identifiers.

It depends on :mod:`pikepdf` for PDF object manipulation and expects precomputed
maps describing the relationship between source and target pages.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from pikepdf import Pdf, Dictionary, Array

from pdftl.utils.transform import transform_destination_coordinates


@dataclass
class RemapperContext:
    """Holds all stable, run-time context for the LinkRemapper."""

    page_map: dict
    rev_maps: dict
    dest_caches: dict
    pdf_to_input_index: dict
    page_transforms: dict
    include_instance: bool
    include_pdf_id: bool


class LinkRemapper:
    """
    Encapsulates all state and logic required to remap PDF link actions
    from a source document into a transformed or merged target PDF.

    This class uses page and destination mappings, transformation parameters,
    and PDF ownership context to ensure that GoTo and related link actions
    remain valid after document modifications.

    Attributes:
        pdf (Pdf): The destination PDF to which actions will be remapped.
        source_pdf (Pdf): The original PDF from which actions are copied.
        instance_num (int): Identifier for the current transformation instance.
        include_instance (bool): Whether to include the instance number in new destination names.
        include_pdf_id (bool): Whether to include the input PDF index in new destination names.
        page_map (dict): Maps (source_pdf_id, page_index, instance_num) → target page object.
        rev_maps (dict): Maps source_pdf_id → {objgen → page_index}.
        dest_caches (dict): Maps source_pdf_id → {destination_name → destination_array}.
        pdf_to_input_index (dict): Maps source_pdf_id → input index in a merged context.
        page_transforms (dict): Maps target_page.obj.objgen → ((rotation, relative), scale).
    """

    def __init__(self, context: RemapperContext):
        self.context = context
        # Initialize Per-Call Context
        self.pdf: Pdf | None = None
        self.source_pdf: Pdf | None = None
        self.instance_num: int | None = None

    def set_call_context(self, pdf: "Pdf", source_pdf: "Pdf", instance_num: int):
        """Set per-call remapper context variables.

        pdf: target pdf
        source_pdf: source pdf
        instance_num: instance number
        """
        self.pdf = pdf
        self.source_pdf = source_pdf
        self.instance_num = instance_num

    def _copy_action(self, original_action: "Dictionary") -> Union["Dictionary", None]:
        """
        Safely copy a PDF action dictionary from the source to the target PDF.

        This creates a new, indirect object in the source context and then
        copies it into the target PDF’s object graph. Shallow copies are
        never safe, as pikepdf objects must be owned by a single Pdf instance.

        Args:
            original_action (Dictionary): The original PDF action dictionary.

        Returns:
            Dictionary | None: A deep copy of the action, owned by `self.pdf`,
            or None if copying failed due to a ForeignObjectError.
        """
        from pikepdf import ForeignObjectError

        try:
            indirect_action = self.source_pdf.make_indirect(original_action)
            return self.pdf.copy_foreign(indirect_action)
        except ForeignObjectError as fo_error:
            logger.warning("Failed to copy action object: %s", fo_error)
            return None

    def _get_new_destination_name(self, original_name: str) -> str:
        """
        Construct a unique name for a remapped named destination.

        Depending on configuration flags, the new name can incorporate
        the instance number and/or the source PDF index, ensuring that
        named destinations remain distinct across multiple input documents.

        Args:
            original_name (str): The destination’s original name.

        Returns:
            str: The new unique destination name.
        """
        if not self.context.include_instance and not self.context.include_pdf_id:
            return original_name

        if self.context.include_pdf_id:
            input_index = self.context.pdf_to_input_index[id(self.source_pdf)]
            return f"{input_index}-{self.instance_num}-{original_name}"

        return f"{self.instance_num}-{original_name}"

    def _transform_destination_array(self, dest_array: "Array", target_page) -> "Array":
        """
        Apply page rotation and scaling transforms to an explicit destination array.

        PDF /XYZ destinations encode a specific position and zoom level:
        [page_obj, /XYZ, x, y, zoom]. When a page has been rotated or scaled,
        those coordinates must be adjusted to match the transformed page.

        Args:
            dest_array (Array): The explicit destination array.
            target_page: The corresponding target page object.

        Returns:
            Array: A transformed destination array referencing the target page.
        """
        from pikepdf import Array, Name

        d_details = list(dest_array)[1:]
        rotation, scale = self.context.page_transforms.get(
            target_page.obj.objgen, ((0, False), 1.0)
        )
        angle, _ = rotation

        # Only transform explicit /XYZ destinations when rotation/scale applies
        if (angle != 0 or scale != 1.0) and d_details and d_details[0] == Name.XYZ:
            page_box = target_page.get(Name.CropBox, target_page.MediaBox)

            xyz_params = list(d_details[1:])
            while len(xyz_params) < 3:
                xyz_params.append(None)  # Pad to [x, y, zoom]

            transformed_params = transform_destination_coordinates(
                xyz_params, page_box, angle, scale
            )

            # Replace transformed coordinates, removing extra None padding
            d_details[1:] = [p for p in transformed_params if p is not None]

        return Array([target_page.obj] + d_details)

    def _find_remapped_page(self, source_dest_array: "Array"):
        """
        Locate the corresponding page in the output PDF for a given source destination.

        Args:
            source_dest_array (Array): The original destination array, typically
                of the form [source_page_obj, /XYZ, ...].

        Returns:
            Page | None: The remapped page object, or None if no mapping exists.
        """
        from pikepdf import Array

        if not isinstance(source_dest_array, Array) or len(source_dest_array) == 0:
            return None

        target_ref = source_dest_array[0]
        if not hasattr(target_ref, "objgen"):
            return None

        source_map = self.context.rev_maps.get(id(self.source_pdf), {})
        target_idx = source_map.get(target_ref.objgen)
        if target_idx is None:
            return None

        page_key = (id(self.source_pdf), target_idx, self.instance_num)
        return self.context.page_map.get(page_key)

    def _remap_explicit_destination_data(self, resolved_array: "Array"):
        """
        Remap an explicit (array-based) GoTo destination.

        This function identifies the corresponding target page, applies any
        required coordinate transformations, and returns the adjusted array.

        Args:
            resolved_array (Array): The resolved destination array.

        Returns:
            tuple: (new_action_dest, new_named_dest)
                - new_action_dest (Array | None): The transformed destination array.
                - new_named_dest (None): Explicit destinations do not create new names.
        """
        target_page = self._find_remapped_page(resolved_array)
        if not target_page:
            return None, None

        new_action_dest = self._transform_destination_array(resolved_array, target_page)
        return new_action_dest, None

    def _remap_named_destination_data(self, resolved_name):
        """
        Remap a named destination from the source PDF into the target PDF.

        Looks up the named destination in the source cache, finds the corresponding
        target page, applies any necessary transformations, and creates a new named
        destination dictionary in the target PDF.

        Args:
            resolved_name (String | Name): The name of the destination.

        Returns:
            tuple: (new_action_dest, new_named_dest)
                - new_action_dest (String | None): The name string to assign to the action.
                - new_named_dest (tuple | None): A (String, Dictionary) pair defining the
                  new named destination, or None if remapping failed.
        """
        from pikepdf import Dictionary, Name, String

        original_name = str(resolved_name).lstrip("/")
        source_dests = self.context.dest_caches.get(id(self.source_pdf), {})

        if original_name not in source_dests:
            logger.warning(
                "Named destination '%s' not found. Link will be dropped.",
                original_name,
            )
            return None, None

        dest_obj = source_dests[original_name]
        dest_array = (
            dest_obj.D if isinstance(dest_obj, Dictionary) and Name.D in dest_obj else dest_obj
        )

        target_page = self._find_remapped_page(dest_array)
        if not target_page:
            return None, None

        new_dest_array = self._transform_destination_array(dest_array, target_page)

        new_name_str = self._get_new_destination_name(original_name)
        new_name_obj = String(new_name_str)
        dest_dict = self.pdf.make_indirect(Dictionary(D=new_dest_array))

        new_named_dest = (new_name_obj, dest_dict)
        new_action_dest = new_name_obj

        return new_action_dest, new_named_dest

    def remap_goto_action(self, action: "Dictionary"):
        """
        Remap a /GoTo action, handling both named and explicit destinations.

        If the destination cannot be remapped, returns (None, None).

        Args:
            action (Dictionary): The original /GoTo action dictionary.

        Returns:
            tuple: (new_action, new_named_dest)
                - new_action (Dictionary | None): The remapped action object.
                - new_named_dest (tuple | None): A new named destination, if created.
        """
        from pikepdf import Array, Dictionary, Name, String

        dest = action.D
        resolved = dest.resolve() if isinstance(dest, Dictionary) else dest
        new_action_dest = None
        new_named_dest = None

        # Dispatch based on destination type
        if isinstance(resolved, (String, Name)):
            new_action_dest, new_named_dest = self._remap_named_destination_data(resolved)
        elif isinstance(resolved, Array):
            new_action_dest, new_named_dest = self._remap_explicit_destination_data(resolved)

        if new_action_dest is None:
            return None, None

        new_action = self._copy_action(action)
        if new_action:
            new_action.D = new_action_dest

        return new_action, new_named_dest

    def copy_self_contained_action(self, action: "Dictionary"):
        """
        Copy an action that requires no remapping (e.g., URI, Launch, GoToR).

        Args:
            action (Dictionary): The self-contained action dictionary.

        Returns:
            tuple: (new_action, None)
        """
        return self._copy_action(action), None

    def copy_unsupported_action(self, action: "Dictionary"):
        """
        Copy an unsupported or non-remappable action, such as JavaScript or Forms.

        A warning is logged, as such actions are likely to be non-functional after
        transformation.

        Args:
            action (Dictionary): The unsupported action dictionary.

        Returns:
            tuple: (new_action, None)
        """
        from pikepdf import Name

        action_type = action.get(Name.S, "Unknown")
        logger.warning(
            "Unsupported action type '%s' copied without remapping. "
            "It is highly likely to be broken in the final document.",
            action_type,
        )
        return self._copy_action(action), None


def _build_link_caches(source_pages_to_process, source_pdfs):
    """
    Build caches for reverse page mapping and named destinations.
    (This function is copied from links.py)
    """
    from pikepdf import Name, NameTree

    source_rev_maps, source_named_dests_cache = {}, {}
    include_instance = any(inst > 0 for _, _, inst in source_pages_to_process)
    include_pdf_id = len(source_pdfs) > 1

    for source_pdf in source_pdfs:
        pdf_id = id(source_pdf)
        # Reverse mapping: page.obj.objgen → page index
        source_rev_maps[pdf_id] = {p.obj.objgen: i for i, p in enumerate(source_pdf.pages)}

        dests = {}
        if Name.Names in source_pdf.Root and Name.Dests in source_pdf.Root.Names:
            # Extract named destinations from NameTree
            nt = NameTree(source_pdf.Root.Names.Dests)
            dests = dict(nt.items())

        source_named_dests_cache[pdf_id] = dests

    return (
        source_rev_maps,
        source_named_dests_cache,
        include_instance,
        include_pdf_id,
    )


def create_link_remapper(
    page_map: dict,
    page_transforms: dict,
    processed_page_info: list,
    unique_source_pdfs: set,
    pdf_to_input_index: dict,
) -> LinkRemapper:
    """
    Factory function to build a fully configured LinkRemapper.
    """
    rev_maps, dest_caches, include_instance, include_pdf_id = _build_link_caches(
        processed_page_info, unique_source_pdfs
    )

    remapper_context = RemapperContext(
        page_map=page_map,
        rev_maps=rev_maps,
        dest_caches=dest_caches,
        pdf_to_input_index=pdf_to_input_index,
        page_transforms=page_transforms,
        include_instance=include_instance,
        include_pdf_id=include_pdf_id,
    )

    return LinkRemapper(remapper_context)
