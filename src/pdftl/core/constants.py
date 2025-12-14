# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/core/constants.py


"""Core static project data"""

from collections import OrderedDict

##################################################
# Permissions constants
# fixme: co-locate permissions constants in output/ ?


ALLOW_PERMISSIONS_MAP = OrderedDict(
    [
        ("Printing", ["print_highres"]),
        ("DegradedPrinting", ["print_lowres"]),
        ("ModifyContents", ["modify_other", "modify_assembly"]),
        ("Assembly", ["modify_assembly"]),
        ("CopyContents", ["extract", "accessibility"]),
        ("ScreenReaders", ["accessibility"]),
        ("ModifyAnnotations", ["modify_annotation"]),
        ("FillIn", ["modify_form"]),
        ("AllFeatures", ["all"]),
    ]
)
ALLOW_PERMISSIONS = ALLOW_PERMISSIONS_MAP.keys()
ALLOW_PERMISSIONS_L = OrderedDict([(x.lower(), x) for x in ALLOW_PERMISSIONS])

#################################################
# Paper sizes as seen in
# https://web.mit.edu/PostScript/Adobe/Documents/5003.PPD_Spec_v4.3.pdf


PAPER_SIZES = {
    "10x11": (720, 792),
    "10x13": (720, 936),
    "10x14": (720, 1008),
    "12x11": (864, 792),
    "15x11": (1080, 792),
    "7x9": (504, 648),
    "8x10": (576, 720),
    "9x11": (648, 792),
    "9x12": (648, 864),
    "a0": (2384, 3370),
    "a1": (1684, 2384),
    "a2": (1191, 1684),
    "a3": (842, 1191),
    "a3.transverse": (842, 1191),
    "a3extra": (913, 1262),
    "a3extra.transverse": (913, 1262),
    "a3rotated": (1191, 842),
    "a4": (595, 842),
    "a4.transverse": (595, 842),
    "a4extra": (667, 914),
    "a4plus": (595, 936),
    "a4rotated": (842, 595),
    "a4small": (595, 842),
    "a5": (420, 595),
    "a5.transverse": (420, 595),
    "a5extra": (492, 668),
    "a5rotated": (595, 420),
    "a6": (297, 420),
    "a6rotated": (420, 297),
    "a7": (210, 297),
    "a8": (148, 210),
    "a9": (105, 148),
    "a10": (73, 105),
    "ansic": (1224, 1584),
    "ansid": (1584, 2448),
    "ansie": (2448, 3168),
    "archa": (648, 864),
    "archb": (864, 1296),
    "archc": (1296, 1728),
    "archd": (1728, 2592),
    "arche": (2592, 3456),
    "b0": (2920, 4127),
    "b1": (2064, 2920),
    "b2": (1460, 2064),
    "b3": (1032, 1460),
    "b4": (729, 1032),
    "b4rotated": (1032, 729),
    "b5": (516, 729),
    "b5.transverse": (516, 729),
    "b5rotated": (729, 516),
    "b6": (363, 516),
    "b6rotated": (516, 363),
    "b7": (258, 363),
    "b8": (181, 258),
    "b9": (127, 181),
    "b10": (91, 127),
    "c4": (649, 918),
    "c5": (459, 649),
    "c6": (323, 459),
    "comm10": (297, 684),
    "dl": (312, 624),
    "doublepostcard": (567, 419.5),
    "doublepostcardrotated": (419.5, 567),
    "env9": (279, 639),
    "env10": (297, 684),
    "env11": (324, 747),
    "env12": (342, 792),
    "env14": (360, 828),
    "envc0": (2599, 3676),
    "envc1": (1837, 2599),
    "envc2": (1298, 1837),
    "envc3": (918, 1296),
    "envc4": (649, 918),
    "envc5": (459, 649),
    "envc6": (323, 459),
    "envc65": (324, 648),
    "envc7": (230, 323),
    "envchou3": (340, 666),
    "envchou3rotated": (666, 340),
    "envchou4": (255, 581),
    "envchou4rotated": (581, 255),
    "envdl": (312, 624),
    "envinvite": (624, 624),
    "envisob4": (708, 1001),
    "envisob5": (499, 709),
    "envisob6": (499, 354),
    "envitalian": (312, 652),
    "envkaku2": (680, 941),
    "envkaku2rotated": (941, 680),
    "envkaku3": (612, 785),
    "envkaku3rotated": (785, 612),
    "envmonarch": (279, 540),
    "envpersonal": (261, 468),
    "envprc1": (289, 468),
    "envprc1rotated": (468, 289),
    "envprc2": (289, 499),
    "envprc2rotated": (499, 289),
    "envprc3": (354, 499),
    "envprc3rotated": (499, 354),
    "envprc4": (312, 590),
    "envprc4rotated": (590, 312),
    "envprc5": (312, 624),
    "envprc5rotated": (624, 312),
    "envprc6": (340, 652),
    "envprc6rotated": (652, 340),
    "envprc7": (454, 652),
    "envprc7rotated": (652, 454),
    "envprc8": (340, 876),
    "envprc8rotated": (876, 340),
    "envprc9": (649, 918),
    "envprc9rotated": (918, 649),
    "envprc10": (918, 1298),
    "envprc10rotated": (1298, 918),
    "envyou4": (298, 666),
    "envyou4rotated": (666, 298),
    "executive": (522, 756),
    "fanfoldus": (1071, 792),
    "fanfoldgerman": (612, 864),
    "fanfoldgermanlegal": (612, 936),
    "folio": (595, 935),
    "isob0": (2835, 4008),
    "isob1": (2004, 2835),
    "isob2": (1417, 2004),
    "isob3": (1001, 1417),
    "isob4": (709, 1001),
    "isob5": (499, 709),
    "isob5extra": (569.7, 782),
    "isob6": (354, 499),
    "isob7": (249, 354),
    "isob8": (176, 249),
    "isob9": (125, 176),
    "isob10": (88, 125),
    "ledger": (1224, 792),
    "legal": (612, 1008),
    "legalextra": (684, 1080),
    "letter": (612, 792),
    "letter.transverse": (612, 792),
    "letterextra": (684, 864),
    "letterextra.transverse": (684, 864),
    "letterplus": (612, 913.7),
    "letterrotated": (792, 612),
    "lettersmall": (612, 792),
    "monarch": (279, 540),
    "note": (612, 792),
    "postcard": (284, 419),
    "postcardrotated": (419, 284),
    "prc16k": (414, 610),
    "prc16krotated": (610, 414),
    "prc32k": (275, 428),
    "prc32kbig": (275, 428),
    "prc32kbigrotated": (428, 275),
    "prc32krotated": (428, 275),
    "quarto": (610, 780),
    "statement": (396, 612),
    "supera": (643, 1009),
    "superb": (864, 1380),
    "tabloid": (792, 1224),
    "tabloidextra": (864, 1296),
}

##################################################
# Named boxes in PDF files


PAGE_BOXES = [
    "/MediaBox",
    "/CropBox",
    "/TrimBox",
    "/BleedBox",
    "/ArtBox",
]

##################################################
# Conversion data for units

UNITS = {"pt": 1.0, "cm": 72 / 2.54, "mm": 72 / 25.4, "in": 72}


##################################################
# Data for creating FDF files.

FDF_START = b"""%FDF-1.2
%\xe2\xe3\xcf\xd3
1 0 obj

<<
/FDF
<<
/Fields ["""

FDF_END = b"""
]
>>
>>
endobj

trailer

<<
/Root 1 0 R
>>
%%EOF
"""

##################################################
# State data for info (metadata) methods
# fixme: co-locate in info/ ?

PAGE_LABEL_STYLE_MAP = {
    "DecimalArabicNumerals": "/D",
    "UppercaseRomanNumerals": "/R",
    "LowercaseRomanNumerals": "/r",
    "UppercaseLetters": "/A",
    "LowercaseLetters": "/a",
    "NoNumber": None,
}
