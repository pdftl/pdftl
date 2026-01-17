# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/pages/forms.py

"""Methods for handling forms when adding pages"""

import logging

logger = logging.getLogger(__name__)


def handle_page_widgets(new_pdf, new_page, source_page, instance_num):
    """
    Scans the new_page for Widget annotations.
    If instance_num > 0 (it's a duplicate), renames the fields to avoid collisions.
    """
    import pikepdf

    if "/Annots" not in new_page:
        return

    # Track cloned parents for THIS page instance so siblings (radio buttons in same group)
    # share the same NEW parent object.
    cloned_parents = {}

    for i, annot in enumerate(new_page.Annots):

        type_str = str(annot.get("/Type", ""))
        subtype_str = str(annot.get("/Subtype", ""))
        is_widget = type_str == "/Widget" or subtype_str == "/Widget"

        if is_widget and instance_num > 0:

            # Case A: Standard Widget (Text/Check) - Has its own Name (/T)
            if "/T" in annot:
                old_name = str(annot.T)
                new_name = f"{old_name}_{instance_num}"
                annot.T = new_name
                logger.debug(f"  > Renamed Widget {old_name} -> {new_name}")

            # Case B: Child Widget (Radio Button) - Inherits Name from Parent
            elif "/Parent" in annot:
                old_parent = annot.Parent

                if old_parent.objgen in cloned_parents:
                    # Point this widget to the existing new parent
                    annot.Parent = cloned_parents[old_parent.objgen]
                else:
                    # Create a deep copy of the parent dictionary structure
                    # CRITICAL FIX: Must be made INDIRECT so it gets a real Object ID
                    new_parent = new_pdf.make_indirect(pikepdf.Dictionary(old_parent))

                    if "/T" in old_parent:
                        old_parent_name = str(old_parent.T)
                        new_parent_name = f"{old_parent_name}_{instance_num}"
                        new_parent.T = new_parent_name

                        cloned_parents[old_parent.objgen] = new_parent
                        annot.Parent = new_parent

                        logger.debug(
                            f"  > Cloned & Renamed Parent: {old_parent_name} -> {new_parent_name}"
                        )
                    else:
                        # Should rarely happen for valid forms
                        pass


def rebuild_acroform_index(pdf):
    """Rebuilds the /AcroForm /Fields array by scanning all pages."""
    import pikepdf

    logger.debug("--- [FORMS] Rebuilding Global AcroForm Index ---")

    fields = []
    seen_fields = set()

    for page in pdf.pages:
        if "/Annots" in page:
            for annot in page.Annots:

                type_str = str(annot.get("/Type", ""))
                subtype_str = str(annot.get("/Subtype", ""))

                if type_str == "/Widget" or subtype_str == "/Widget":

                    candidate = annot
                    if "/Parent" in annot:
                        candidate = annot.Parent

                    # Deduplicate based on Object ID
                    if candidate.objgen not in seen_fields:
                        fields.append(candidate)
                        seen_fields.add(candidate.objgen)

    logger.debug(f"[FORMS] Total unique fields collected: {len(fields)}")

    if fields:
        if "/AcroForm" not in pdf.Root:
            pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array(fields))
        else:
            pdf.Root.AcroForm.Fields = pikepdf.Array(fields)

        pdf.Root.AcroForm.NeedAppearances = True
    else:
        # If no fields, remove AcroForm if it exists to clean up
        if "/AcroForm" in pdf.Root:
            del pdf.Root.AcroForm
