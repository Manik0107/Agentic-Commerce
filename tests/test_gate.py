import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ["AC_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentic_commerce import db, payment, protocols  # noqa: E402

CALLS: list[int] = []


def fake_processor(amount_paise, receipt, notes):
    CALLS.append(amount_paise)
    return f"order_test_{len(CALLS)}", "test_stub"


payment._create_razorpay_order = fake_processor

MERCHANT = "m_test"
FUTURE = "2099-01-01T00:00:00+00:00"
PAST = "2020-01-01T00:00:00+00:00"


def setup():
    db.init()
    conn = db.connect()
    conn.execute("DELETE FROM variants")
    conn.execute("DELETE FROM tokens")
    conn.execute("DELETE FROM audit")
    for sku, cat, price, stock in [
        ("SKU-A", "kurta", 120000, "in_stock"),
        ("SKU-B", "kurta", 180000, "in_stock"),
        ("SKU-C", "saree", 100000, "in_stock"),
        ("SKU-D", "kurta", 100000, "out_of_stock"),
    ]:
        conn.execute(
            "INSERT OR REPLACE INTO variants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sku,
                "p_" + sku,
                MERCHANT,
                "Test " + sku,
                cat,
                cat,
                1.0,
                "blue",
                "navy",
                "blue",
                1.0,
                "cotton",
                "cotton",
                1.0,
                "M",
                price,
                "INR",
                stock,
                "now",
                "now",
            ),
        )
    conn.close()


def token(tid, cap=150000, merchants=(MERCHANT,), categories=("kurta",), until=FUTURE):
    payment.issue_token(tid, "u1", cap, list(merchants), list(categories), until)
    return tid


def intent(sku, declared=200000.0, quantity=1):  # declared max, in paise
    msg = protocols.build_ap2_cart(
        "p_" + sku, sku, "Test", declared / 100, MERCHANT, "agent_test", quantity
    )
    return protocols.translate(msg)


def audit_count():
    conn = db.connect()
    n = conn.execute("SELECT COUNT(*) c FROM audit").fetchone()["c"]
    conn.close()
    return n


def run(name, sku, tid, expect_status, expect_reason=None, declared=200000.0):
    before_calls, before_audit = len(CALLS), audit_count()
    result = payment.process(intent(sku, declared), tid)

    assert result["status"] == expect_status, (
        f"{name}: expected {expect_status}, got {result['status']} ({result.get('reason')})"
    )
    if expect_reason:
        assert result["reason"] == expect_reason, (
            f"{name}: expected reason {expect_reason}, got {result['reason']}"
        )

    assert audit_count() == before_audit + 1, f"{name}: expected exactly one audit row"
    assert result.get("audit_id"), f"{name}: response carries no audit reference"

    if expect_status == "blocked":
        assert len(CALLS) == before_calls, (
            f"{name}: BLOCKED ATTEMPT REACHED THE PROCESSOR -- {len(CALLS) - before_calls} call(s)"
        )
    else:
        assert len(CALLS) == before_calls + 1, f"{name}: expected exactly one processor call"
    print(f"  ok  {name:34} -> {expect_status:9} {result.get('reason') or ''}")


def test_gate_checks():
    print("gate checks (PRD 8.3), each blocking with zero charge:")
    run("allowed, inside every limit", "SKU-A", token("t_ok"), "completed")
    run("token expired", "SKU-A", token("t_exp", until=PAST), "blocked", "token_expired")
    run(
        "merchant not allowed",
        "SKU-A",
        token("t_m", merchants=("m_other",)),
        "blocked",
        "merchant_not_allowed",
    )
    run("category not allowed", "SKU-C", token("t_c"), "blocked", "category_not_allowed")
    run(
        "spend cap exceeded",
        "SKU-B",
        token("t_cap"),
        "blocked",
        "spend_cap_exceeded",
        declared=180000.0,
    )
    run("no such token", "SKU-A", "t_missing", "blocked", "token_not_found")
    run("out of stock", "SKU-D", token("t_stock"), "blocked", "out_of_stock")
    run(
        "live price above declared max",
        "SKU-B",
        token("t_dec", cap=500000),
        "blocked",
        "price_above_declared_max",
        declared=100000.0,
    )


def test_cap_accumulates():
    print("cap accumulates across purchases on one token:")
    tid = token("t_acc", cap=250000)
    run("first purchase, 1200 of 2500", "SKU-A", tid, "completed")
    run("second, 1200 more, still fits", "SKU-A", tid, "completed")
    run("third would reach 3600 > 2500", "SKU-A", tid, "blocked", "spend_cap_exceeded")
    remaining = payment.get_token(tid)["remaining"]
    assert remaining == 100.0, f"expected 100.0 remaining, got {remaining}"
    print(f"  ok  remaining after two purchases    -> INR {remaining}")


def test_failed_processor_releases_reservation():
    print("a processor failure must not consume the user's authority:")
    tid = token("t_rel", cap=500000)

    def boom(*a, **k):
        raise RuntimeError("processor unreachable")

    original = payment._create_razorpay_order
    payment._create_razorpay_order = boom
    try:
        before = audit_count()
        result = payment.process(intent("SKU-A"), tid)
        assert result["status"] == "error", result
        assert audit_count() == before + 1, "a crashed attempt must still be audited"
    finally:
        payment._create_razorpay_order = original

    used = payment.get_token(tid)["used_amount"]
    assert used == 0.0, f"failed charge consumed {used} of the cap; must be 0.0"
    print("  ok  reservation released, used_amount  -> INR 0.0")


def test_concurrent_intents_cannot_split_one_cap():
    print("two concurrent intents against one cap:")
    tid = token("t_race", cap=150000)  # 1500: fits SKU-A (1200) exactly once
    results = []
    barrier = threading.Barrier(2)

    def attempt():
        barrier.wait()
        results.append(payment.process(intent("SKU-A"), tid))

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    completed = [r for r in results if r["status"] == "completed"]
    assert len(completed) == 1, (
        f"expected exactly 1 to succeed, got {len(completed)} -- the cap was double-spent"
    )
    used = payment.get_token(tid)["used_amount"]
    assert used == 1200.0, f"used_amount is {used}, expected 1200.0"
    print(f"  ok  exactly one succeeded, used       -> INR {used}")


def test_every_attempt_is_audited():
    conn = db.connect()
    rows = conn.execute("SELECT gate_decision, reason, raw_intent FROM audit").fetchall()
    conn.close()
    assert len(rows) == audit_count()
    for r in rows:
        assert r["gate_decision"] in ("completed", "blocked", "error"), r["gate_decision"]
        if r["gate_decision"] == "blocked":
            assert r["reason"], "a blocked entry with no reason is not explainable"
    print(f"\naudit trail: {len(rows)} entries, every one carrying a decision")


if __name__ == "__main__":
    setup()
    test_gate_checks()
    print()
    test_cap_accumulates()
    print()
    test_failed_processor_releases_reservation()
    print()
    test_concurrent_intents_cannot_split_one_cap()
    test_every_attempt_is_audited()
    print(f"processor calls total: {len(CALLS)} (one per completed attempt, zero per block)")
    print("\nALL CHECKS PASSED")
