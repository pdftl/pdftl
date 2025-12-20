# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/json.py

"""Define pdf_obj_to_json, to convert a pikepdf object to JSON"""

import decimal
import logging

logger = logging.getLogger(__name__)

from pdftl.utils.whatisit import is_page

# def _abbreviate_debug_string(s: str, max_len: int = 250, head: int = 200, tail: int = 50) -> str:
#     """Abbreviates a string for logging if it exceeds a maximum length."""
#     if len(s) > max_len:
#         return f"{s[:head]} ...<ABBREVIATED>... {s[-tail:]}"
#     return s


KEY_ACTION_TYPE = "/S"
KEY_DESTINATION = "/D"
ACTION_GOTO = "/GoTo"
KEY_RESOLVED_DESTINATION = "ResolvedDestination"


def pdf_obj_to_json(obj, page_object_to_num_map=None, named_dests=None):
    """
    Recursively converts a pikepdf object into a JSON-serializable type.

    This is a convenience wrapper around the PdfToJsonConverter class.
    """
    converter = PdfToJsonConverter(page_object_to_num_map, named_dests)
    return converter.convert(obj)


class PdfToJsonConverter:
    """
    Encapsulates the logic for recursively converting a pikepdf object
    into a JSON-serializable structure.

    This class manages the conversion state, including page maps, named
    destinations, and recursion tracking to prevent infinite loops.
    """

    def __init__(self, page_object_to_num_map=None, named_dests=None):
        self.page_object_to_num_map = page_object_to_num_map or {}
        self.named_dests = named_dests or {}

    def convert(self, obj):
        """
        Public entry point for converting a PDF object.
        """
        return self.to_json_recursive(obj, depth=0, ancestors=[])

    def to_json_recursive(self, obj, depth, ancestors):
        """
        The core recursive conversion logic that dispatches to type-specific handlers.
        """
        # 1. Loop detection
        try:
            idx = ancestors.index(obj)
            logger.debug("LOOP detected")
            return f"Go_Up({idx})"
        except ValueError:
            pass  # Not a loop, continue

        # 2. Dispatch to the appropriate handler based on object type
        handler = self._get_handler(obj)
        return handler(obj, depth, ancestors)

    def _get_handler(self, obj):
        """Selects the correct handler method based on the object's type."""
        from pikepdf import Array, Dictionary, Name, Object, String

        # Fallback for any other types
        handler = self._handle_unknown
        if is_page(obj):
            handler = self._handle_page
        elif isinstance(obj, (int, float, bool, str)):
            handler = self._handle_python_primitive
        elif isinstance(obj, (String, Name)):
            handler = self._handle_pdf_string_or_name
        elif isinstance(obj, Array):
            handler = self._handle_array
        elif isinstance(obj, Dictionary):
            handler = self._handle_dictionary
        elif isinstance(obj, Object) and hasattr(obj, "objgen"):
            handler = self._handle_indirect_object
        elif obj is None:
            handler = self._handle_none
        return handler

    # --- Type-Specific Handlers ---

    def _handle_none(self, _obj, depth, _ancestors):
        # logger.debug("%sgot None", self._prefix(depth))
        # return None (implicit)
        pass

    def _handle_python_primitive(self, obj, _depth, _ancestors):
        # logger.debug("%sgot python data %s", self._prefix(depth), obj)
        return obj

    def _handle_pdf_string_or_name(self, obj, _depth, _ancestors):
        # type_name = type(obj).__name__
        # logger.debug("%sgot %s '%s'", self._prefix(depth), type_name, str(obj))
        return str(obj)

    def _handle_array(self, obj, depth, ancestors):
        # debug_string = str(list(obj)).replace("\n", " ")
        # logger.debug("%sgot an Array: %s", self._prefix(depth), debug_string)

        new_ancestors = [obj] + ancestors
        return [self.to_json_recursive(item, depth + 1, new_ancestors) for item in obj]

    def _handle_page(self, obj, depth, _ancestors):
        logger.debug("%sThis seems to be a Page", self._prefix(depth))
        page_num = self.page_object_to_num_map.get(obj.objgen, '"Unknown"')
        logger.debug("page number is %s", page_num)
        return {"Page": page_num}

    def _handle_dictionary(self, obj, depth, ancestors):
        new_ancestors = [obj] + ancestors
        json_dict = {
            str(k): self.to_json_recursive(v, depth + 1, new_ancestors) for k, v in obj.items()
        }

        # Post-process the dictionary for special cases like GoTo actions
        _add_resolved_destination_if_goto(json_dict, self.page_object_to_num_map, self.named_dests)
        return json_dict

    def _handle_indirect_object(self, obj, _depth, _ancestors):
        return f"Ref({obj.objgen[0]}, {obj.objgen[1]})"

    def _handle_unknown(self, obj, depth, _ancestors):
        ret = str(obj)
        if not isinstance(obj, (float, int, str, decimal.Decimal)):
            logger.debug(
                "%sUnknown object of type %s. This may be a bug.",
                self._prefix(depth),
                type(obj),
            )
            logger.debug("Attempting fallback to str(...)=%s", ret)
        return ret

    @staticmethod
    def _prefix(depth):
        return "-" * min(depth, 20) + f"potj<{depth}>: "


def _dest_obj_if_resolvable_goto_action(action_dict, named_dests):
    dest_name = action_dict.get(KEY_DESTINATION)
    if (
        not named_dests
        or action_dict.get(KEY_ACTION_TYPE) != ACTION_GOTO
        or not isinstance(dest_name, str)
    ):
        return None
    dest_obj = named_dests.get(dest_name, None)
    return dest_obj


def _add_resolved_destination_if_goto(
    action_dict,
    page_object_map,
    named_dests,
):
    """
    Resolves and embeds a named GoTo action's destination details in-place.

    If the provided `action_dict` represents a GoTo action with a named
    destination, this function finds the destination's target page number and
    details. It then adds this resolved information to `action_dict` under the
    `ResolvedDestination` key.

    Args:
        action_dict: A dictionary representing a PDF action object. This dictionary
                     is modified in-place if it's a resolvable GoTo action.
        page_object_map: A mapping from a page's object identifier (obj, gen)
                         to its page number (integer).
        named_dests: A dictionary of all named destinations in the PDF.
    """
    from pikepdf import Array, Dictionary

    dest_obj = _dest_obj_if_resolvable_goto_action(action_dict, named_dests)

    # --- Extract the destination array from the destination object ---
    # A destination can be a direct array or a dictionary containing the array.
    dest_array = None
    if isinstance(dest_obj, Dictionary) and KEY_DESTINATION in dest_obj:
        dest_array = dest_obj[KEY_DESTINATION]
    elif isinstance(dest_obj, Array):
        dest_array = dest_obj
    if not dest_array:
        return

    # --- Resolve the target page number ---
    target_page_ref = dest_array[0]
    resolved_page_num = "Unknown"  # Default value
    # Check if we have the necessary map and the object has an identifier
    if page_object_map and hasattr(target_page_ref, "objgen"):
        # The .get() provides a safe lookup with a default value
        resolved_page_num = page_object_map.get(target_page_ref.objgen, "Unknown")

    # --- Process destination details and update the dictionary ---
    dest_details = [pdf_obj_to_json(item) for item in list(dest_array)[1:]]

    action_dict[KEY_RESOLVED_DESTINATION] = {
        "TargetPage": resolved_page_num,
        "DestinationDetails": dest_details,
    }
