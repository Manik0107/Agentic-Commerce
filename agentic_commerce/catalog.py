import datetime as dt

from . import db, mock_merchant, normalize, taxonomy


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def sync(use_llm: bool = True) -> dict:
    """Read the merchant source, normalise, upsert. Idempotent.

    One normalisation batch per attribute for the whole feed, not one per product.
    """
    raw_products = mock_merchant.fetch_raw()
    ts = _now()

    batches = {
        "category": [p["raw_category"] for p in raw_products],
        "color": [p["raw_color"] for p in raw_products],
        "material": [p["raw_material"] for p in raw_products],
    }
    normalised = {
        attr: normalize.normalise(attr, values, use_llm=use_llm) for attr, values in batches.items()
    }

    conn = db.connect()
    written = flagged = 0
    for product in raw_products:
        cat = normalised["category"][product["raw_category"]]
        col = normalised["color"][product["raw_color"]]
        mat = normalised["material"][product["raw_material"]]
        flagged += sum(1 for a in (cat, col, mat) if a["needs_review"])

        for variant in product["variants"]:
            conn.execute(
                """
                INSERT OR REPLACE INTO variants (
                    sku, product_id, merchant_id, name,
                    category_canonical, category_raw, category_confidence,
                    color_canonical, color_shade, color_raw, color_confidence,
                    material_canonical, material_raw, material_confidence,
                    size, price_paise, currency,
                    availability_status, availability_checked_at, last_updated)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    variant["sku"],
                    product["product_id"],
                    product["merchant_id"],
                    product["name"],
                    cat["canonical"],
                    product["raw_category"],
                    cat["confidence"],
                    col["canonical"],
                    col["shade"],
                    product["raw_color"],
                    col["confidence"],
                    mat["canonical"],
                    product["raw_material"],
                    mat["confidence"],
                    variant["size"],
                    variant["price_paise"],
                    variant["currency"],
                    variant["stock"],
                    ts,
                    ts,
                ),
            )
            written += 1
    conn.close()
    return {
        "products": len(raw_products),
        "variants_written": written,
        "attributes_flagged_for_review": flagged,
        "synced_at": ts,
        "taxonomy_version": taxonomy.TAXONOMY_VERSION,
    }


def query(
    category=None,
    color=None,
    shade=None,
    material=None,
    max_price=None,
    min_price=None,
    availability=None,
    merchant_id=None,
    limit: int = 50,
) -> list[dict]:
    """Structured filters only, no model in this path.

    Prices here are rupees (the agent-facing unit) and are converted to paise before
    comparison, which stays integer throughout.
    """
    where, params = [], []
    for column, value in (
        ("category_canonical", category),
        ("color_canonical", color),
        ("color_shade", shade),
        ("material_canonical", material),
        ("availability_status", availability),
        ("merchant_id", merchant_id),
    ):
        if value:
            where.append(f"{column} = ?")
            params.append(str(value).lower())
    if max_price is not None:
        where.append("price_paise <= ?")
        params.append(int(round(float(max_price) * 100)))
    if min_price is not None:
        where.append("price_paise >= ?")
        params.append(int(round(float(min_price) * 100)))

    sql = "SELECT * FROM variants"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY product_id, price_paise LIMIT ?"
    params.append(int(limit))

    conn = db.connect()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return _group(rows)


def _group(rows) -> list[dict]:
    """Flat variant rows -> the nested product shape of PRD 6.4.

    order_intent_url is a path, not an absolute URL: the agent already knows the host
    it fetched the catalog from.
    """
    products: dict[str, dict] = {}
    for r in rows:
        product = products.setdefault(
            r["product_id"],
            {
                "product_id": r["product_id"],
                "merchant_id": r["merchant_id"],
                "name": r["name"],
                "category": {
                    "canonical": r["category_canonical"],
                    "raw": r["category_raw"],
                    "model_confidence": round(r["category_confidence"], 3),
                },
                "attributes": {
                    "color": {
                        "canonical": r["color_canonical"],
                        "shade": r["color_shade"],
                        "raw": r["color_raw"],
                        "model_confidence": round(r["color_confidence"] or 0, 3),
                    },
                    "material": {
                        "canonical": r["material_canonical"],
                        "raw": r["material_raw"],
                        "model_confidence": round(r["material_confidence"] or 0, 3),
                    },
                },
                "variants": [],
                "order_intent_url": "/intent",
                "last_updated": r["last_updated"],
            },
        )
        product["variants"].append(
            {
                "sku": r["sku"],
                "size": r["size"],
                "price": r["price_paise"] / 100,
                "currency": r["currency"],
                "availability": {
                    "status": r["availability_status"],
                    "checked_at": r["availability_checked_at"],
                },
            }
        )
    return list(products.values())


def get_variant(sku: str) -> dict | None:
    """Live price/stock lookup for the pre-payment re-check (PRD 9).

    The gate charges this price, never the one the agent declared.
    """
    conn = db.connect()
    row = conn.execute("SELECT * FROM variants WHERE sku = ?", (sku,)).fetchone()
    conn.close()
    return dict(row) if row else None


def stats() -> dict:
    conn = db.connect()
    total = conn.execute("SELECT COUNT(*) c FROM variants").fetchone()["c"]
    by_source = conn.execute("SELECT source, COUNT(*) c FROM norm_cache GROUP BY source").fetchall()
    low = conn.execute(
        "SELECT COUNT(*) c FROM norm_cache WHERE confidence < ?", (normalize.AUTO_ACCEPT,)
    ).fetchone()["c"]
    conn.close()
    return {
        "variants": total,
        "normalisations_by_source": {r["source"]: r["c"] for r in by_source},
        "flagged_for_review": low,
    }
