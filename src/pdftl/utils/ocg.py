from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def get_xobject_ocg_ids(xobj) -> set:
    """Extracts a set of OCG object IDs from an XObject's /OC dictionary."""
    import pikepdf

    if "/OC" not in xobj:
        return set()

    oc = xobj.OC
    if not isinstance(oc, pikepdf.Dictionary):
        return set()

    # An OCMD can contain a single OCG or an array of OCGs
    if oc.get("/Type") == "/OCMD":
        return _ocg_ids_from_ocmd(oc.get("/OCGs"), pikepdf.Array)

    # Sometimes it's just a direct reference to an OCG
    if oc.get("/Type") == "/OCG" or hasattr(oc, "objgen"):
        if oc.objgen[0] != 0:
            return {int(oc.objgen[0])}

    return set()


def _ocg_ids_from_ocmd(ocgs, Array):
    ocg_ids = set()
    if isinstance(ocgs, Array):
        for o in ocgs:
            if hasattr(o, "objgen"):
                ocg_ids.add(int(o.objgen[0]))
    elif hasattr(ocgs, "objgen"):
        ocg_ids.add(int(ocgs.objgen[0]))
    return ocg_ids


def get_page_layer_map(resources) -> tuple[dict, dict]:
    """
    Analyzes a resource dictionary and returns maps for both inline properties and XObjects.
    Returns: (properties_map, xobject_map)
    """
    import pikepdf

    prop_map = {}
    xobj_map = {}

    if resources is None:
        return prop_map, xobj_map

    # 1. Map /Properties (Used for inline BDC tags)
    if "/Properties" in resources:
        for local_name, pdf_obj in resources.Properties.items():
            if isinstance(pdf_obj, pikepdf.Dictionary) and pdf_obj.get("/Type") == "/OCG":
                prop_map[str(local_name)] = int(pdf_obj.objgen[0])

    # 2. Map /XObject (Used for 'Do' commands)
    if "/XObject" in resources:
        for local_name, xobj in resources.XObject.items():
            ocg_ids = get_xobject_ocg_ids(xobj)
            if ocg_ids:
                xobj_map[str(local_name)] = ocg_ids

    return prop_map, xobj_map


def _remove_targets_from_array(node, target_ids: set):
    """Recursively removes objects matching the target IDs from a pikepdf Array."""
    import pikepdf

    for i in range(len(node) - 1, -1, -1):
        item = node[i]
        if isinstance(item, pikepdf.Array):
            _remove_targets_from_array(item, target_ids)
            if len(item) == 0:
                del node[i]
        elif hasattr(item, "objgen"):
            if item.objgen[0] in target_ids:
                del node[i]


def clean_ocproperties(pdf, target_ids: set):
    """Safely purges stripped/flattened layers from the PDF's global metadata."""
    from pikepdf import NamePath

    if "/OCProperties" not in pdf.Root:
        return

    ocgs = _clean_master_ocg_array(pdf, target_ids, NamePath)
    _clean_default_config(pdf, target_ids, NamePath)
    _clean_alternate_configs(pdf, target_ids, NamePath)
    _clean_empty_shell(pdf, ocgs)


def _clean_master_ocg_array(pdf, target_ids, NamePath):
    # 1. Clean Master OCGs Array
    ocgs = pdf.Root.get(NamePath.OCProperties.OCGs)
    if ocgs is not None:
        _remove_targets_from_array(ocgs, target_ids)
    return ocgs


def _clean_default_config(pdf, target_ids, NamePath):
    # 2. Clean Default Config
    for key in ["/ON", "/OFF", "/Order"]:
        arr = pdf.Root.get(NamePath("/OCProperties", "/D", key))
        if arr is not None:
            _remove_targets_from_array(arr, target_ids)


def _clean_alternate_configs(pdf, target_ids, NamePath):
    # 3. Clean Alternate Configs
    configs = pdf.Root.get(NamePath.OCProperties.Configs)
    if configs is not None:
        for config in configs:
            for key in ["/ON", "/OFF", "/Order"]:
                arr = config.get(key)
                if arr is not None:
                    _remove_targets_from_array(arr, target_ids)


def _clean_empty_shell(pdf, ocgs):
    # 4. If NO layers are left, completely destroy the OCProperties shell
    if ocgs is not None and len(ocgs) == 0:
        del pdf.Root["/OCProperties"]


def create_layer(pdf, layer_name: str):
    """
    Creates a new Optional Content Group (layer) and registers it globally
    in the PDF's /OCProperties. Returns the OCG object.
    """
    from pikepdf import Array, Dictionary, Name

    # 1. Create the base Optional Content Group (OCG)
    ocg = pdf.make_indirect(Dictionary(Type=Name.OCG, Name=layer_name))

    # 2. Ensure the global /OCProperties dictionary exists
    if "/OCProperties" not in pdf.Root:
        pdf.Root.OCProperties = Dictionary(OCGs=Array(), D=Dictionary(Order=Array(), ON=Array()))

    oc_props = pdf.Root.OCProperties

    # 3. Safely append to the master OCG list
    if "/OCGs" not in oc_props:
        oc_props.OCGs = Array()
    oc_props.OCGs.append(ocg)

    # 4. Safely append to the Default view dictionary (/D) and Order array
    if "/D" not in oc_props:
        oc_props.D = Dictionary(Order=Array(), ON=Array())
    if "/Order" not in oc_props.D:
        oc_props.D.Order = Array()

    oc_props.D.Order.append(ocg)

    # Best practice: ensure it is toggled ON by default
    if "/ON" not in oc_props.D:
        oc_props.D.ON = Array()
    oc_props.D.ON.append(ocg)

    return ocg


def set_layer_state(pdf, target_ids: set, action: str):
    """
    Applies state changes (show/hide/lock/unlock) to the global /OCProperties.
    """
    import pikepdf

    if "/OCProperties" not in pdf.Root or "/D" not in pdf.Root.OCProperties:
        return

    d_dict = pdf.Root.OCProperties.D

    def _ensure_array(key):
        if key not in d_dict:
            d_dict[key] = pikepdf.Array()
        return d_dict[key]

    ocgs = pdf.Root.OCProperties.get("/OCGs", [])
    target_ocgs = [
        ocg for ocg in ocgs if hasattr(ocg, "objgen") and int(ocg.objgen[0]) in target_ids
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

    if "/OCProperties" not in pdf.Root or "/OCGs" not in pdf.Root.OCProperties:
        return

    for ocg in pdf.Root.OCProperties.OCGs:
        _process_ocg_layer_usage(ocg, action, target_ids, Dictionary, Name)


def _process_ocg_layer_usage(ocg, action, target_ids, Dictionary, Name):
    if not (hasattr(ocg, "objgen") and int(ocg.objgen[0]) in target_ids):
        return

    if "/Usage" not in ocg:
        ocg.Usage = Dictionary()

    if action in ("print", "noprint"):
        if "/Print" not in ocg.Usage:
            ocg.Usage.Print = Dictionary(Subtype=Name.Print, PrintState=Name.ON)
        ocg.Usage.Print.PrintState = Name.ON if action == "print" else Name.OFF

    elif action in ("screen", "noscreen"):
        if "/View" not in ocg.Usage:
            ocg.Usage.View = Dictionary(ViewState=Name.ON)
        ocg.Usage.View.ViewState = Name.ON if action == "screen" else Name.OFF
