from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def _get_obj_id(obj) -> int:
    """Extracts a non-zero indirect PDF object ID, returning 0 for direct or invalid objects."""
    try:
        objgen = getattr(obj, "objgen", None)
        if objgen and objgen[0] != 0:
            return int(objgen[0])
    except (AttributeError, TypeError, IndexError):  # codeql[py/empty-except]
        pass
    return 0


def pdf_items(obj) -> list:
    """Items of an array entry; a lone non-array value counts as one item."""
    import pikepdf

    if obj is None:
        return []
    if isinstance(obj, pikepdf.Array):
        return list(obj)
    return [obj]


def dict_items(obj) -> list:
    """The dictionaries among `pdf_items(obj)`."""
    import pikepdf

    return [o for o in pdf_items(obj) if isinstance(o, pikepdf.Dictionary)]


def ocg_id_list(obj) -> list[int]:
    """Indirect object ids among `pdf_items(obj)`, in order."""
    return [i for i in map(_get_obj_id, pdf_items(obj)) if i]


_OC_TYPES = ("/OCG", "/OCMD")
_MAX_EXPRESSION_DEPTH = 64


def _is_oc(obj) -> bool:
    import pikepdf

    return isinstance(obj, pikepdf.Dictionary) and obj.get("/Type") in _OC_TYPES


def get_page_layer_map(resources) -> tuple[dict, dict]:
    """Optional content (OCG or OCMD) by resource name: (properties, xobjects)."""
    import pikepdf

    prop_map: dict = {}
    xobj_map: dict = {}
    if not isinstance(resources, pikepdf.Dictionary):
        return prop_map, xobj_map

    properties = resources.get("/Properties")
    if isinstance(properties, pikepdf.Dictionary):
        for name, obj in properties.items():
            if _is_oc(obj):
                prop_map[str(name)] = obj

    xobjects = resources.get("/XObject")
    if isinstance(xobjects, pikepdf.Dictionary):
        for name, xobj in xobjects.items():
            oc = (
                xobj.get("/OC") if isinstance(xobj, (pikepdf.Stream, pikepdf.Dictionary)) else None
            )
            if _is_oc(oc):
                xobj_map[str(name)] = oc

    return prop_map, xobj_map


def fold_visibility(oc, fixed: dict):
    """Visibility of an OCG or OCMD once the layers in `fixed` (id -> on) are constant.

    Returns True or False when that settles it, else None. An OCMD that still
    depends on other layers is rewritten in place without the fixed ones."""
    import pikepdf

    if not _is_oc(oc):
        return None
    if oc.Type == "/OCG":
        return fixed.get(_get_obj_id(oc))
    if isinstance(oc.get("/VE"), pikepdf.Array):
        if not _mentions(oc.VE, fixed, 0):
            return None
        folded = _fold_expression(oc.VE, fixed, 0)
        if isinstance(folded, bool):
            return folded
        oc.VE = folded
        return None
    return _fold_policy(oc, fixed)


_POLICIES = {
    "/AnyOn": (True, True),
    "/AllOn": (False, True),
    "/AnyOff": (True, False),
    "/AllOff": (False, False),
}


def _fold_policy(ocmd, fixed):
    import pikepdf

    members = dict_items(ocmd.get("/OCGs"))
    if not any(_get_obj_id(m) in fixed for m in members):
        return None
    is_any, wanted = _POLICIES.get(str(ocmd.get("/P", "/AnyOn")), _POLICIES["/AnyOn"])
    rest = []
    for member in members:
        state = fixed.get(_get_obj_id(member))
        if state is None:
            rest.append(member)
        elif (state == wanted) == is_any:
            return is_any
    if not rest:
        return not is_any
    ocmd.OCGs = pikepdf.Array(rest)
    return None


def _mentions(expr, fixed, depth) -> bool:
    import pikepdf

    if isinstance(expr, pikepdf.Dictionary):
        return _get_obj_id(expr) in fixed
    if not isinstance(expr, pikepdf.Array) or depth > _MAX_EXPRESSION_DEPTH:
        return False
    return any(_mentions(e, fixed, depth + 1) for e in list(expr)[1:])


def _fold_expression(expr, fixed, depth):
    """True, False, or the expression with the fixed layers folded away."""
    import pikepdf

    if isinstance(expr, pikepdf.Dictionary):
        state = fixed.get(_get_obj_id(expr))
        return expr if state is None else state
    if not isinstance(expr, pikepdf.Array) or len(expr) < 2 or depth > _MAX_EXPRESSION_DEPTH:
        return expr
    op = str(expr[0])
    args = [_fold_expression(e, fixed, depth + 1) for e in list(expr)[1:]]
    if op == "/Not" and len(args) == 1:
        if isinstance(args[0], bool):
            return not args[0]
        return pikepdf.Array([pikepdf.Name.Not, args[0]])
    if op in ("/And", "/Or"):
        return _fold_junction(op, args)
    return expr


def _fold_junction(op, args):
    import pikepdf

    # True settles an Or, False settles an And.
    settles = op == "/Or"
    if any(a is settles for a in args):
        return settles
    rest = [a for a in args if not isinstance(a, bool)]
    if not rest:
        return not settles
    return pikepdf.Array([pikepdf.Name(op), *rest])


def _remove_targets_from_array(node, target_ids: set):
    """Recursively removes objects matching the target IDs from a pikepdf Array."""
    import pikepdf

    if not isinstance(node, pikepdf.Array):
        return
    for i in range(len(node) - 1, -1, -1):
        item = node[i]
        if isinstance(item, pikepdf.Array):
            _remove_targets_from_array(item, target_ids)
            if len(item) == 0:
                del node[i]
        else:
            obj_id = _get_obj_id(item)
            if obj_id and obj_id in target_ids:
                del node[i]


_CONFIG_OCG_KEYS = ("/ON", "/OFF", "/Locked", "/Order", "/RBGroups")


def clean_ocproperties(pdf, target_ids: set):
    """Safely purges stripped/flattened layers from the PDF's global metadata."""
    import pikepdf

    ocprops = pdf.Root.get("/OCProperties")
    if not isinstance(ocprops, pikepdf.Dictionary):
        return

    ocgs = ocprops.get("/OCGs")
    _remove_targets_from_array(ocgs, target_ids)
    for config in dict_items(ocprops.get("/D")) + dict_items(ocprops.get("/Configs")):
        _clean_config(config, target_ids)

    # If no layers are left, remove the OCProperties shell entirely.
    if isinstance(ocgs, pikepdf.Array) and len(ocgs) == 0:
        del pdf.Root["/OCProperties"]


def _clean_config(config, target_ids):
    for key in _CONFIG_OCG_KEYS:
        _remove_targets_from_array(config.get(key), target_ids)
    for usage_app in dict_items(config.get("/AS")):
        _remove_targets_from_array(usage_app.get("/OCGs"), target_ids)


def dict_entry(parent, key):
    """`parent[key]`, replaced by an empty dictionary if it is not one."""
    import pikepdf

    if not isinstance(parent.get(key), pikepdf.Dictionary):
        parent[key] = pikepdf.Dictionary()
    return parent[key]


def array_entry(parent, key):
    """`parent[key]` as an array; a lone dictionary becomes a one-item array."""
    import pikepdf

    if not isinstance(parent.get(key), pikepdf.Array):
        parent[key] = pikepdf.Array(dict_items(parent.get(key)))
    return parent[key]


def create_layer(pdf, layer_name: str):
    """
    Creates a new Optional Content Group (layer) and registers it globally
    in the PDF's /OCProperties. Returns the OCG object.
    """
    from pikepdf import Dictionary, Name

    # 1. Create the base Optional Content Group (OCG)
    ocg = pdf.make_indirect(Dictionary(Type=Name.OCG, Name=layer_name))

    # 2. Ensure the global /OCProperties dictionary exists
    oc_props = dict_entry(pdf.Root, "/OCProperties")
    array_entry(oc_props, "/OCGs").append(ocg)

    # 3. Append to the default configuration's Order and ON arrays
    d_dict = dict_entry(oc_props, "/D")
    array_entry(d_dict, "/Order").append(ocg)
    array_entry(d_dict, "/ON").append(ocg)

    return ocg


def set_layer_state(pdf, target_ids: set, action: str):
    """
    Applies state changes (show/hide/lock/unlock) to the global /OCProperties.
    """
    import pikepdf

    ocprops = pdf.Root.get("/OCProperties")
    if not isinstance(ocprops, pikepdf.Dictionary) or not isinstance(
        ocprops.get("/D"), pikepdf.Dictionary
    ):
        return

    d_dict = ocprops.D

    def _ensure_array(key):
        return array_entry(d_dict, key)

    target_ocgs = [
        ocg for ocg in pdf_items(ocprops.get("/OCGs")) if _get_obj_id(ocg) in target_ids
    ]

    if not target_ocgs:
        return

    if action in ("show", "hide"):
        on_arr = _ensure_array("/ON")
        off_arr = _ensure_array("/OFF")

        # Remove from both arrays to prevent contradictory states
        _remove_targets_from_array(on_arr, target_ids)
        _remove_targets_from_array(off_arr, target_ids)

        target_arr = on_arr if action == "show" else off_arr
        target_arr.extend(target_ocgs)

    elif action in ("lock", "unlock"):
        locked_arr = _ensure_array("/Locked")
        _remove_targets_from_array(locked_arr, target_ids)

        if action == "lock":
            locked_arr.extend(target_ocgs)


def set_layer_usage(pdf, target_ids: set, action: str):
    """
    Applies usage overrides (print/noprint/screen/noscreen) to OCG dictionaries.
    """
    from pikepdf import Dictionary, Name

    ocprops = pdf.Root.get("/OCProperties")
    if not isinstance(ocprops, Dictionary):
        return

    for ocg in dict_items(ocprops.get("/OCGs")):
        _process_ocg_layer_usage(ocg, action, target_ids, Dictionary, Name)


def _process_ocg_layer_usage(ocg, action, target_ids, pikepdf_dictionary, pikepdf_name):
    if _get_obj_id(ocg) not in target_ids:
        return

    if not isinstance(ocg.get("/Usage"), pikepdf_dictionary):
        ocg.Usage = pikepdf_dictionary()

    if action in ("print", "noprint"):
        if not isinstance(ocg.Usage.get("/Print"), pikepdf_dictionary):
            ocg.Usage.Print = pikepdf_dictionary(
                Subtype=pikepdf_name.Print, PrintState=pikepdf_name.ON
            )
        ocg.Usage.Print.PrintState = pikepdf_name.ON if action == "print" else pikepdf_name.OFF

    elif action in ("screen", "noscreen"):
        if not isinstance(ocg.Usage.get("/View"), pikepdf_dictionary):
            ocg.Usage.View = pikepdf_dictionary(ViewState=pikepdf_name.ON)
        ocg.Usage.View.ViewState = pikepdf_name.ON if action == "screen" else pikepdf_name.OFF
