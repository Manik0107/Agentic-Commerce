TAXONOMY_VERSION = "1.0.0"

CATEGORIES = [
    "kurta",
    "saree",
    "lehenga",
    "salwar_suit",
    "dupatta",
    "sherwani",
    "nehru_jacket",
    "palazzo",
    "blouse",
    "stole",
]

# canonical -> spellings a merchant might ship
CATEGORY_SYNONYMS = {
    "kurta": [
        "kurti",
        "kurtha",
        "kurtaa",
        "long kurta",
        "kurta top",
        "ladies kurti",
        "kurti - ladies",
        "straight kurta",
        "anarkali kurta",
    ],
    "saree": ["sari", "saari", "seree", "silk saree", "sarees"],
    "lehenga": ["lehnga", "lehanga", "ghagra", "chaniya choli", "lehenga choli"],
    "salwar_suit": [
        "salwar kameez",
        "salwar",
        "churidar suit",
        "suit set",
        "punjabi suit",
        "kameez",
    ],
    "dupatta": ["duppata", "chunni", "odhni"],
    "sherwani": ["shervani", "sherwaani", "mens sherwani"],
    "nehru_jacket": ["nehru coat", "modi jacket", "bandhgala jacket", "koti"],
    "palazzo": ["palazo", "palazzo pants", "plazo"],
    "blouse": ["choli", "saree blouse", "blowse"],
    "stole": ["shawl", "scarf", "wrap"],
}

# Family -> its shades. The family is indexed; the shade is preserved on the variant.
COLOR_FAMILIES = {
    "blue": ["navy", "royal", "sky", "teal", "indigo", "turquoise", "cobalt"],
    "red": ["maroon", "crimson", "rust", "cherry", "wine", "scarlet"],
    "green": ["olive", "emerald", "mint", "bottle", "sage", "lime"],
    "yellow": ["mustard", "lemon", "gold", "ochre", "amber"],
    "pink": ["rose", "fuchsia", "blush", "magenta", "peach"],
    "purple": ["violet", "lavender", "mauve", "plum", "lilac"],
    "black": ["jet", "charcoal"],
    "white": ["ivory", "cream", "off white", "pearl"],
    "brown": ["beige", "tan", "coffee", "camel", "khaki"],
    "orange": ["saffron", "coral", "apricot"],
    "grey": ["silver", "slate", "ash"],
}

COLOR_SYNONYMS = {
    "blue": ["blu", "bluee", "navy blu", "nevy"],
    "grey": ["gray"],
    "white": ["wht", "offwhite"],
    "red": ["maroom"],
}

MATERIALS = [
    "cotton",
    "silk",
    "linen",
    "rayon",
    "georgette",
    "chiffon",
    "velvet",
    "wool",
    "polyester",
    "denim",
]

MATERIAL_SYNONYMS = {
    "cotton": [
        "cotton blend",
        "pure cotton",
        "cotton 100%",
        "kora cotton",
        "cottn",
        "handloom cotton",
    ],
    "silk": ["raw silk", "art silk", "banarasi silk", "silk blend", "tussar"],
    "georgette": ["georgett", "faux georgette"],
    "rayon": ["viscose rayon", "viscose"],
    "polyester": ["poly", "poly blend", "polyster"],
    "linen": ["linen blend", "pure linen"],
}


def _flatten(canon_to_aliases: dict[str, list[str]], canon_list=None) -> dict[str, str]:
    """alias -> canonical, including each canonical as its own alias."""
    table = {}
    for canon in canon_list or canon_to_aliases:
        table[canon.lower()] = canon
        table[canon.replace("_", " ").lower()] = canon
    for canon, aliases in canon_to_aliases.items():
        for alias in aliases:
            table[alias.lower()] = canon
    return table


CATEGORY_LOOKUP = _flatten(CATEGORY_SYNONYMS, CATEGORIES)
MATERIAL_LOOKUP = _flatten(MATERIAL_SYNONYMS, MATERIALS)

# Two-level: COLOR_LOOKUP maps a shade or a family name to its family, and SHADE_OF
# records which shades exist so the raw string's shade can be recovered.
COLOR_LOOKUP = _flatten(COLOR_SYNONYMS, list(COLOR_FAMILIES))
SHADE_OF: dict[str, str] = {}
for _family, _shades in COLOR_FAMILIES.items():
    for _shade in _shades:
        COLOR_LOOKUP[_shade] = _family
        SHADE_OF[_shade] = _shade

LOOKUPS = {
    "category": CATEGORY_LOOKUP,
    "color": COLOR_LOOKUP,
    "material": MATERIAL_LOOKUP,
}

VALID = {
    "category": set(CATEGORIES),
    "color": set(COLOR_FAMILIES),
    "material": set(MATERIALS),
}
