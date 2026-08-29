import datetime as dt
import json
import os
import sqlite3

import httpx

from . import db
from .protocols import OrderIntent

# No key configured means offline mode: the gate and audit trail still run in full
# and only the processor call is stubbed, so the demo works without network.
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_ORDERS_ENDPOINT = "https://api.razorpay.com/v1/orders"

BLOCK_REASONS = {
    "token_not_found": "No such payment token.",
    "token_expired": "The payment token has expired.",
    "merchant_not_allowed": "This merchant is not in the token's allowlist.",
    "category_not_allowed": "This product category is not in the token's allowlist.",
    "spend_cap_exceeded": "This purchase would exceed the token's spend cap.",
    "sku_not_found": "No such SKU in the catalog.",
    "out_of_stock": "The SKU is not in stock.",
    "price_above_declared_max": "Live price is above the agent's declared maximum.",
}


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _audit(conn, **fields) -> int:
    columns = (
        "ts",
        "token_id",
        "agent_id",
        "merchant_id",
        "product_id",
        "sku",
        "amount_paise",
        "protocol_used",
        "gate_decision",
        "reason",
        "razorpay_order_id",
        "raw_intent",
    )
    values = [fields.get(c) for c in columns]
    values[0] = _now()
    cur = conn.execute(
        f"INSERT INTO audit ({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
        values,
    )
    return cur.lastrowid


def _blocked(reason: str, amount_paise: int | None = None, **details) -> dict:
    """The structured rejection an agent receives (PRD 8.5)."""
    return {
        "status": "blocked",
        "reason": reason,
        "message": BLOCK_REASONS.get(reason, reason),
        "amount": (amount_paise / 100) if amount_paise is not None else None,
        "currency": "INR",
        "details": details,
    }


def _create_razorpay_order(amount_paise: int, receipt: str, notes: dict) -> tuple[str, str]:
    """Create a Razorpay test-mode order. Returns (order_id, mode).

    Authorisation only. Capture needs the customer-facing Checkout handshake, which
    an autonomous agent does not perform.
    """
    if not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET):
        return f"order_offline_{receipt}", "offline_stub"

    response = httpx.post(
        RAZORPAY_ORDERS_ENDPOINT,
        auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
        json={
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt[:40],
            "notes": notes,
        },
        timeout=20.0,
    )
    response.raise_for_status()
    return response.json()["id"], "razorpay_test"


def process(intent: OrderIntent, token_id: str) -> dict:
    """Run one Order Intent through the gate. Always returns; always audits."""
    conn = db.connect()
    audit_row = {
        "agent_id": intent.agent_id,
        "product_id": intent.product_id,
        "sku": intent.sku,
        "protocol_used": intent.protocol_used,
        "token_id": token_id,
        "raw_intent": json.dumps(
            intent.raw_protocol_payload, sort_keys=True, separators=(",", ":")
        ),
    }
    outcome: dict | None = None
    reserved_paise = 0

    try:
        # Live price/stock re-check (PRD 9) before the gate sees an amount: the
        # catalog can be stale and the agent's declared price is its own claim.
        variant = conn.execute("SELECT * FROM variants WHERE sku = ?", (intent.sku,)).fetchone()
        if variant is None:
            outcome = _blocked("sku_not_found", sku=intent.sku)
            return outcome
        audit_row["merchant_id"] = variant["merchant_id"]

        if variant["availability_status"] != "in_stock":
            outcome = _blocked(
                "out_of_stock", sku=intent.sku, status=variant["availability_status"]
            )
            return outcome

        amount_paise = variant["price_paise"] * intent.quantity
        audit_row["amount_paise"] = amount_paise

        declared_paise = int(round(intent.declared_max_price * 100))
        if amount_paise > declared_paise:
            outcome = _blocked(
                "price_above_declared_max",
                amount_paise,
                live_price=amount_paise / 100,
                declared_max=intent.declared_max_price,
            )
            return outcome

        token = conn.execute("SELECT * FROM tokens WHERE token_id = ?", (token_id,)).fetchone()
        if token is None:
            outcome = _blocked("token_not_found", amount_paise, token_id=token_id)
            return outcome

        # Gate checks in PRD 8.3 order. First failure wins.
        if dt.datetime.fromisoformat(token["valid_until"]) <= dt.datetime.now(dt.UTC):
            outcome = _blocked("token_expired", amount_paise, valid_until=token["valid_until"])
            return outcome

        if variant["merchant_id"] not in json.loads(token["merchant_allowlist"]):
            outcome = _blocked(
                "merchant_not_allowed",
                amount_paise,
                merchant_id=variant["merchant_id"],
                allowed=json.loads(token["merchant_allowlist"]),
            )
            return outcome

        categories = json.loads(token["category_allowlist"])
        if categories and variant["category_canonical"] not in categories:
            outcome = _blocked(
                "category_not_allowed",
                amount_paise,
                category=variant["category_canonical"],
                allowed=categories,
            )
            return outcome

        # Cap check and reservation in one transaction. Checking then incrementing
        # afterwards lets two concurrent intents both read used_amount and both
        # spend the same headroom. Released below if the processor call fails.
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT used_amount_paise, spend_cap_paise FROM tokens WHERE token_id = ?",
                (token_id,),
            ).fetchone()
            if current["used_amount_paise"] + amount_paise > current["spend_cap_paise"]:
                conn.execute("ROLLBACK")
                outcome = _blocked(
                    "spend_cap_exceeded",
                    amount_paise,
                    cap=current["spend_cap_paise"] / 100,
                    already_used=current["used_amount_paise"] / 100,
                    attempted=amount_paise / 100,
                    remaining=(current["spend_cap_paise"] - current["used_amount_paise"]) / 100,
                )
                return outcome
            conn.execute(
                "UPDATE tokens SET used_amount_paise = used_amount_paise + ? WHERE token_id = ?",
                (amount_paise, token_id),
            )
            conn.execute("COMMIT")
            reserved_paise = amount_paise
        except sqlite3.OperationalError as e:
            conn.execute("ROLLBACK")
            outcome = _blocked(
                "spend_cap_exceeded",
                amount_paise,
                note=f"could not acquire cap lock: {e}",
            )
            return outcome

        # Past this line, and only past this line, money can move.
        try:
            order_id, mode = _create_razorpay_order(
                amount_paise,
                receipt=f"{intent.sku}-{int(dt.datetime.now().timestamp())}",
                notes={
                    "agent_id": intent.agent_id,
                    "token_id": token_id,
                    "protocol": intent.protocol_used,
                },
            )
        except Exception as e:
            # No charge happened, so the reservation must not consume the cap.
            conn.execute(
                "UPDATE tokens SET used_amount_paise = used_amount_paise - ? WHERE token_id = ?",
                (reserved_paise, token_id),
            )
            reserved_paise = 0
            audit_row["reason"] = f"processor_error: {type(e).__name__}"
            outcome = {
                "status": "error",
                "reason": "processor_error",
                "message": str(e)[:200],
                "amount": amount_paise / 100,
                "currency": "INR",
                "details": {},
            }
            return outcome

        audit_row["razorpay_order_id"] = order_id
        outcome = {
            "status": "completed",
            "reason": None,
            "message": "Payment authorised within token limits.",
            "amount": amount_paise / 100,
            "currency": "INR",
            "razorpay_order_id": order_id,
            "processor_mode": mode,
            "details": {
                "cap": token["spend_cap_paise"] / 100,
                "used_after": (current["used_amount_paise"] + amount_paise) / 100,
                "remaining": (
                    token["spend_cap_paise"] - current["used_amount_paise"] - amount_paise
                )
                / 100,
            },
        }
        return outcome

    except Exception as e:
        if reserved_paise:
            conn.execute(
                "UPDATE tokens SET used_amount_paise = used_amount_paise - ? WHERE token_id = ?",
                (reserved_paise, token_id),
            )
        audit_row["reason"] = f"{type(e).__name__}: {e}"[:200]
        outcome = {
            "status": "error",
            "reason": "internal_error",
            "message": f"{type(e).__name__}: {e}"[:200],
            "amount": None,
            "currency": "INR",
            "details": {},
        }
        return outcome

    finally:
        # Runs on every path, including the ones that raised.
        audit_row["gate_decision"] = (outcome or {}).get("status", "error")
        audit_row.setdefault("reason", (outcome or {}).get("reason"))
        audit_id = _audit(conn, **audit_row)
        if outcome is not None:
            outcome["audit_id"] = audit_id
        conn.close()


def issue_token(
    token_id: str,
    user_id: str,
    spend_cap_paise: int,
    merchant_allowlist: list[str],
    category_allowlist: list[str],
    valid_until: str,
) -> dict:
    conn = db.connect()
    conn.execute(
        "INSERT OR REPLACE INTO tokens (token_id, user_id, spend_cap_paise, currency,"
        " merchant_allowlist, category_allowlist, valid_until, used_amount_paise)"
        " VALUES (?,?,?,'INR',?,?,?,COALESCE("
        "   (SELECT used_amount_paise FROM tokens WHERE token_id = ?), 0))",
        (
            token_id,
            user_id,
            spend_cap_paise,
            db.jdump(merchant_allowlist),
            db.jdump(category_allowlist),
            valid_until,
            token_id,
        ),
    )
    conn.close()
    return get_token(token_id)


def get_token(token_id: str) -> dict | None:
    conn = db.connect()
    row = conn.execute("SELECT * FROM tokens WHERE token_id = ?", (token_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "token_id": row["token_id"],
        "user_id": row["user_id"],
        "spend_cap": row["spend_cap_paise"] / 100,
        "currency": row["currency"],
        "merchant_allowlist": json.loads(row["merchant_allowlist"]),
        "category_allowlist": json.loads(row["category_allowlist"]),
        "valid_until": row["valid_until"],
        "used_amount": row["used_amount_paise"] / 100,
        "remaining": (row["spend_cap_paise"] - row["used_amount_paise"]) / 100,
    }


def audit_log(limit: int = 100, token_id: str | None = None) -> list[dict]:
    conn = db.connect()
    sql = "SELECT * FROM audit"
    params: list = []
    if token_id:
        sql += " WHERE token_id = ?"
        params.append(token_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    entries = []
    for r in rows:
        entry = dict(r)
        entry["amount"] = (entry.pop("amount_paise") or 0) / 100
        entry.pop("raw_intent", None)  # kept in the table, too bulky to list
        entries.append(entry)
    return entries
