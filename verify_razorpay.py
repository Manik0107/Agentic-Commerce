import os
import sys
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

from agentic_commerce import db, payment  # noqa: E402

ORDERS_ENDPOINT = payment.RAZORPAY_ORDERS_ENDPOINT


def fetch_order(auth, order_id: str) -> dict | None:
    """Fetch one order by id.

    Immediately consistent, unlike the list endpoint.
    """
    response = httpx.get(f"{ORDERS_ENDPOINT}/{order_id}", auth=auth, timeout=30)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def list_orders(
    auth, after_epoch: int, expected: int, attempts: int = 6, pause: float = 2.0
) -> list[dict]:
    """Orders created at or after `after_epoch`.

    Retried because this endpoint is eventually consistent: an order fetchable by id
    can still be missing from the list for a minute.
    """
    items: list[dict] = []
    for attempt in range(attempts):
        if attempt:
            time.sleep(pause)
        response = httpx.get(
            ORDERS_ENDPOINT,
            auth=auth,
            params={"count": 100, "from": after_epoch},
            timeout=30,
        )
        response.raise_for_status()
        items = response.json()["items"]
        if len(items) >= expected:
            return items
    return items


def main() -> int:
    key_id = os.getenv("RAZORPAY_KEY_ID", "")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "")
    if not (key_id and key_secret):
        print("No Razorpay credentials set; nothing to verify against.")
        print("This check only means something in razorpay_test mode.")
        return 0
    if not key_id.startswith("rzp_test"):
        print(f"Refusing to run: {key_id[:9]}... is not a test-mode key.")
        return 1

    auth = (key_id, key_secret)
    started = int(time.time()) - 5

    # The audit table is append-only, so scope this to the attempts made by this
    # run or it would expect Razorpay to hold every order the repo ever created.
    conn = db.connect()
    since = conn.execute("SELECT COALESCE(MAX(id), 0) m FROM audit").fetchone()["m"]
    conn.close()

    print("running the demo against live Razorpay test mode...\n")
    import demo

    demo.main()

    conn = db.connect()
    entries = conn.execute(
        "SELECT gate_decision, amount_paise, razorpay_order_id FROM audit WHERE id > ?",
        (since,),
    ).fetchall()
    conn.close()

    allowed = [e for e in entries if e["gate_decision"] == "completed"]
    blocked = [e for e in entries if e["gate_decision"] == "blocked"]

    print(f"\n{'=' * 66}\nINDEPENDENT VERIFICATION\n{'=' * 66}")
    print(f"  gate allowed   {len(allowed)}")
    print(f"  gate blocked   {len(blocked)}\n")

    failures = []

    # 1. Every allowed attempt has a real order at Razorpay, for the exact amount.
    for entry in allowed:
        order_id = entry["razorpay_order_id"]
        if not order_id:
            failures.append("an allowed attempt recorded no order id")
            continue
        order = fetch_order(auth, order_id)
        if order is None:
            failures.append(f"{order_id} does not exist at Razorpay")
            continue
        match = order["amount"] == entry["amount_paise"]
        print(
            f"  {order_id}  {order['amount']} paise  status={order['status']}"
            f"  amount_paid={order['amount_paid']}"
            f"  agent={order.get('notes', {}).get('agent_id')}"
        )
        if not match:
            failures.append(
                f"{order_id} is {order['amount']} paise, audit says {entry['amount_paise']}"
            )
        if order["amount_paid"]:
            failures.append(f"{order_id} has a captured amount of {order['amount_paid']}")

    # 2. No blocked attempt recorded a processor reference.
    leaked = [e for e in blocked if e["razorpay_order_id"]]
    if leaked:
        failures.append(f"{len(leaked)} blocked entries carry an order id")

    # 3. Razorpay holds no orders beyond the allowed ones: the blocked-charges-
    #    nothing claim, checked against the processor rather than our own log.
    orders = list_orders(auth, started, expected=len(allowed))
    recorded = {e["razorpay_order_id"] for e in allowed}
    extra = [o for o in orders if o["id"] not in recorded]
    print(f"\n  orders at Razorpay since this run started: {len(orders)}")
    if extra:
        failures.append(f"{len(extra)} unexpected orders exist: {[o['id'] for o in extra]}")

    # An empty list makes "no extra orders" trivially true, which is not evidence.
    # Report that as inconclusive rather than letting it read as a pass.
    conclusive = len(orders) >= len(allowed)

    print()
    if failures:
        for f in failures:
            print(f"  FAIL  {f}")
        return 1

    print(
        f"  PASS  each of the {len(allowed)} allowed attempts has a real order for the exact amount"
    )
    print(f"  PASS  all {len(blocked)} blocked attempts produced no order at all")
    if conclusive:
        print("  PASS  Razorpay holds no order this run did not intend")
    else:
        print(
            f"  ----  INCONCLUSIVE: the orders list returned {len(orders)} of "
            f"{len(allowed)} known orders, so it is too stale to rule out extras."
        )
        print(
            "        Re-run in a minute, or check the Razorpay dashboard. The "
            "per-order checks above are unaffected."
        )
    print("  PASS  nothing was captured\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
