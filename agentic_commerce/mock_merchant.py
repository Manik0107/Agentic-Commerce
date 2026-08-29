MERCHANT_ID = "m_kalaghar"
MERCHANT_NAME = "Kalaghar Ethnics"

# (product_id, name, raw_category, raw_color, raw_material,
#  [(sku, size, price_rupees, stock)])
RAW_PRODUCTS = [
    (
        "p001",
        "Chanderi Straight Kurta",
        "Kurti - Ladies",
        "Navy Blu",
        "Cotton Blend",
        [
            ("SKU-p001-S", "S", 1249, "in_stock"),
            ("SKU-p001-M", "M", 1249, "in_stock"),
            ("SKU-p001-L", "L", 1349, "out_of_stock"),
        ],
    ),
    (
        "p002",
        "Banarasi Silk Saree",
        "sarees",
        "Maroom",
        "Banarasi Silk",
        [("SKU-p002-F", "Free", 4499, "in_stock")],
    ),
    (
        "p003",
        "Bandhani Anarkali",
        "anarkali kurta",
        "mustard",
        "rayon",
        [("SKU-p003-M", "M", 1899, "in_stock"), ("SKU-p003-L", "L", 1899, "in_stock")],
    ),
    (
        "p004",
        "Phulkari Dupatta",
        "duppata",
        "fuchsia",
        "Georgett",
        [("SKU-p004-F", "Free", 799, "in_stock")],
    ),
    (
        "p005",
        "Jaipuri Palazzo",
        "plazo",
        "Indigo",
        "cotton 100%",
        [("SKU-p005-S", "S", 649, "in_stock"), ("SKU-p005-M", "M", 649, "in_stock")],
    ),
    (
        "p006",
        "Raw Silk Sherwani",
        "mens sherwani",
        "Ivory",
        "Raw Silk",
        [
            ("SKU-p006-40", "40", 8999, "in_stock"),
            ("SKU-p006-42", "42", 8999, "in_stock"),
        ],
    ),
    (
        "p007",
        "Handloom Cotton Kurta",
        "STRAIGHT KURTA",
        "Olive",
        "handloom cotton",
        [("SKU-p007-M", "M", 1099, "in_stock"), ("SKU-p007-XL", "XL", 1199, "in_stock")],
    ),
    (
        "p008",
        "Chikankari Kurti",
        "kurtha",
        "off white",
        "cottn",
        [("SKU-p008-S", "S", 1450, "in_stock"), ("SKU-p008-M", "M", 1450, "in_stock")],
    ),
    (
        "p009",
        "Velvet Nehru Jacket",
        "modi jacket",
        "Bottle",
        "velvet",
        [("SKU-p009-40", "40", 2799, "in_stock")],
    ),
    (
        "p010",
        "Kalamkari Lehenga Set",
        "lehnga choli",
        "Rust",
        "Art Silk",
        [
            ("SKU-p010-M", "M", 6499, "in_stock"),
            ("SKU-p010-L", "L", 6499, "out_of_stock"),
        ],
    ),
    (
        "p011",
        "Pashmina Stole",
        "shawl",
        "Charcoal",
        "wool",
        [("SKU-p011-F", "Free", 1599, "in_stock")],
    ),
    (
        "p012",
        "Cotton Salwar Set",
        "punjabi suit",
        "Sky",
        "pure cotton",
        [("SKU-p012-M", "M", 1799, "in_stock"), ("SKU-p012-L", "L", 1799, "in_stock")],
    ),
    (
        "p013",
        "Embroidered Saree Blouse",
        "blowse",
        "Gold",
        "silk blend",
        [("SKU-p013-34", "34", 899, "in_stock"), ("SKU-p013-36", "36", 899, "in_stock")],
    ),
    (
        "p014",
        "Linen Formal Kurta",
        "kurta top",
        "Slate",
        "Linen Blend",
        [("SKU-p014-M", "M", 1650, "in_stock"), ("SKU-p014-L", "L", 1650, "in_stock")],
    ),
    (
        "p015",
        "Georgette Party Saree",
        "seree",
        "Wine",
        "faux georgette",
        [("SKU-p015-F", "Free", 2299, "in_stock")],
    ),
    # Vocabulary that is nowhere in the synonym table: string matching cannot reach
    # these, so they exercise the LLM tier.
    (
        "p016",
        "Festive Ethnic Wear Top",
        "ethnic wear top",
        "peacock",
        "poly blend",
        [("SKU-p016-M", "M", 1399, "in_stock")],
    ),
    (
        "p017",
        "Traditional Festive Drape",
        "festive drape",
        "sea green",
        "tussar",
        [("SKU-p017-F", "Free", 3799, "in_stock")],
    ),
    (
        "p018",
        "Ladies Bottom Wear Flared",
        "bottom wear flared",
        "dusty rose",
        "viscose",
        [("SKU-p018-S", "S", 749, "in_stock"), ("SKU-p018-M", "M", 749, "in_stock")],
    ),
    (
        "p019",
        "Gents Ethnic Long Coat",
        "gents ethnic long coat",
        "midnight",
        "brocade",
        [("SKU-p019-40", "40", 5499, "in_stock")],
    ),
    (
        "p020",
        "Two Piece Ladies Set",
        "two piece ladies set",
        "burnt sienna",
        "chanderi",
        [("SKU-p020-M", "M", 2149, "in_stock")],
    ),
]


def fetch_raw() -> list[dict]:
    """What the Catalog Sync Service reads from the merchant (PRD 6.1)."""
    records = []
    for pid, name, cat, color, material, variants in RAW_PRODUCTS:
        records.append(
            {
                "product_id": pid,
                "merchant_id": MERCHANT_ID,
                "name": name,
                "raw_category": cat,
                "raw_color": color,
                "raw_material": material,
                "variants": [
                    {
                        "sku": sku,
                        "size": size,
                        "price_paise": rupees * 100,
                        "currency": "INR",
                        "stock": stock,
                    }
                    for sku, size, rupees, stock in variants
                ],
            }
        )
    return records


SEED_TOKENS = [
    # The demo's allowed and blocked paths both run against this token.
    {
        "token_id": "tok_demo_001",
        "user_id": "user_aarav",
        "spend_cap_paise": 150000,
        "merchant_allowlist": [MERCHANT_ID],
        "category_allowlist": ["kurta", "dupatta"],
        "valid_until": "2027-12-31T23:59:59+00:00",
    },
    # Expired on purpose: exercises gate check 1.
    {
        "token_id": "tok_expired",
        "user_id": "user_aarav",
        "spend_cap_paise": 500000,
        "merchant_allowlist": [MERCHANT_ID],
        "category_allowlist": [],
        "valid_until": "2024-01-01T00:00:00+00:00",
    },
    # Allows a different merchant only: exercises gate check 2.
    {
        "token_id": "tok_other_merchant",
        "user_id": "user_aarav",
        "spend_cap_paise": 500000,
        "merchant_allowlist": ["m_someone_else"],
        "category_allowlist": [],
        "valid_until": "2027-12-31T23:59:59+00:00",
    },
]
