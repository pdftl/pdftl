from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def get_xobject_ocg_ids(xobj) -> set:
    """Extracts a set of OCG object IDs from an XObject's /OC dictionary."""
    import pikepdf

    ocg_ids = set()
    if "/OC" not in xobj:
        return ocg_ids

    oc = xobj.OC
    if not isinstance(oc, pikepdf.Dictionary):
        return ocg_ids

    # An OCMD can contain a single OCG or an array of OCGs
    if oc.get("/Type") == "/OCMD":
        ocgs = oc.get("/OCGs")
        if isinstance(ocgs, pikepdf.Array):
            for o in ocgs:
                if hasattr(o, "objgen"):
                    ocg_ids.add(int(o.objgen[0]))
        elif hasattr(ocgs, "objgen"):
            ocg_ids.add(int(ocgs.objgen[0]))

    # Sometimes it's just a direct reference to an OCG
    elif oc.get("/Type") == "/OCG" or hasattr(oc, "objgen"):
        if hasattr(oc, "objgen"):
            ocg_ids.add(int(oc.objgen[0]))

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

    # 1. Clean Master OCGs Array
    ocgs = pdf.Root.get(NamePath.OCProperties.OCGs)
    if ocgs is not None:
        _remove_targets_from_array(ocgs, target_ids)

    # 2. Clean Default Config
    for key in ["/ON", "/OFF", "/Order"]:
        arr = pdf.Root.get(NamePath("/OCProperties", "/D", key))
        if arr is not None:
            _remove_targets_from_array(arr, target_ids)

    # 3. Clean Alternate Configs
    configs = pdf.Root.get(NamePath.OCProperties.Configs)
    if configs is not None:
        for config in configs:
            for key in ["/ON", "/OFF", "/Order"]:
                arr = config.get(key)
                if arr is not None:
                    _remove_targets_from_array(arr, target_ids)

    # 4. If NO layers are left, completely destroy the OCProperties shell
    if ocgs is not None and len(ocgs) == 0:
        del pdf.Root["/OCProperties"]


def create_layer(pdf, layer_name: str):
    """
    Creates a new Optional Content Group (layer) and registers it globally
    in the PDF's /OCProperties. Returns the OCG object.
    """
    from pikepdf import Name, Dictionary, Array

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
