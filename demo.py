import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from agentic_commerce import app as application
from agentic_commerce import db, protocols

REPORT_PATH = Path(__file__).parent / "reports" / "demo_audit.json"
REPORT_PATH.parent.mkdir(exist_ok=True)

AGENT_ID = "agent_shopper_01"
TOKEN_ID = "tok_demo_001"

BOLD, DIM, GREEN, RED, YELLOW, RESET = (
    "\033[1m",
    "\033[2m",
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[0m",
)


def rule(title):
    print(f"\n{BOLD}{'─' * 74}\n{title}\n{'─' * 74}{RESET}")


def money(value):
    return f"INR {value:,.2f}"


def buy(client, product, variant, budget_claim):
    """The agent expresses purchase intent in AP2 and reads the reply."""
    message = protocols.build_ap2_cart(
        product_id=product["product_id"],
        sku=variant["sku"],
        name=product["name"],
        price=budget_claim,
        merchant_id=product["merchant_id"],
        agent_id=AGENT_ID,
        cart_id=f"cart_{variant['sku']}",
    )

    print(
        f"\n{DIM}agent -> merchant   AP2 CartMandate  "
        f"{variant['sku']}  total {money(budget_claim)}{RESET}"
    )

    response = client.post("/intent", json={"token_id": TOKEN_ID, "message": message})
    body = response.json()
    gate, wire = body["gate_result"], body["protocol_response"]

    if gate["status"] == "completed":
        print(
            f"{GREEN}merchant -> agent   AP2 {wire['payment_status']}  "
            f"{money(gate['amount'])}  order {gate['razorpay_order_id']}{RESET}"
        )
        print(
            f"{DIM}                    cap {money(gate['details']['cap'])}, "
            f"remaining {money(gate['details']['remaining'])}, "
            f"audit #{gate['audit_id']}{RESET}"
        )
    else:
        print(f"{RED}merchant -> agent   AP2 {wire['payment_status']}  {gate['reason']}{RESET}")
        print(f"{RED}                    {gate['message']}{RESET}")
        for key, value in gate["details"].items():
            print(f"{DIM}                    {key}: {value}{RESET}")
        print(f"{DIM}                    no charge attempted, audit #{gate['audit_id']}{RESET}")
    return gate


def reset_demo_token():
    """Zero the demo token's spent balance and return the current audit high-water id.

    The spend cap is deliberately persistent, so a second run would otherwise find the
    budget exhausted and refuse everything. Only the demo token is reset, and only
    here; the gate itself never resets anything and the audit trail is left intact.
    """
    conn = db.connect()
    conn.execute("UPDATE tokens SET used_amount_paise = 0 WHERE token_id = ?", (TOKEN_ID,))
    high_water = conn.execute("SELECT COALESCE(MAX(id), 0) m FROM audit").fetchone()["m"]
    conn.close()
    return high_water


def main():
    with TestClient(application.app) as client:
        since = reset_demo_token()

        rule("1. Merchant onboarding — one link tag, zero backend changes")
        page = client.get("/").text
        tag = next(line.strip() for line in page.splitlines() if "agent-catalog" in line)
        print(f"  merchant page carries:  {tag}")
        print(f"  {DIM}that tag is the entire integration surface for discovery{RESET}")

        rule("2. Catalog sync — merchant vocabulary into a canonical taxonomy")
        stats = client.post("/sync").json()
        print(
            f"  {stats['products']} products, {stats['variants_written']} variants, "
            f"taxonomy v{stats['taxonomy_version']}"
        )
        print(
            f"  {stats['attributes_flagged_for_review']} attributes below the "
            f"auto-accept confidence bar, flagged for review"
        )
        sources = client.get("/catalog/stats").json()["normalisations_by_source"]
        print(f"  normalised by: {sources}")
        print(f"  {DIM}exact = synonym table, gemini = LLM, difflib = offline fallback{RESET}")

        rule("3. Agent discovers and queries — deterministic, no LLM in this path")
        query = "?category=kurta&color=blue&max_price=1500&availability=in_stock"
        catalog = client.get("/.well-known/agent-catalog.json" + query).json()
        print(f"  GET /.well-known/agent-catalog.json{query}")
        print(f"  {catalog['count']} matching products\n")
        for product in catalog["products"]:
            colour = product["attributes"]["color"]
            print(
                f"    {product['name'][:30]:32} "
                f"{colour['canonical']}/{colour['shade'] or '-':9} "
                f"raw={colour['raw']!r:14} conf={colour['model_confidence']}"
            )
            for variant in product["variants"]:
                print(
                    f"      {DIM}{variant['sku']:16} {variant['size']:5} "
                    f"{money(variant['price'])}{RESET}"
                )

        rule("4. The user's authority — a scoped payment token")
        token = client.get(f"/tokens/{TOKEN_ID}").json()
        print(f"  spend cap          {money(token['spend_cap'])}")
        print(f"  merchants          {token['merchant_allowlist']}")
        print(f"  categories         {token['category_allowlist']}")
        print(f"  valid until        {token['valid_until']}")
        print(f"  {DIM}the agent cannot exceed any of these, whatever it decides{RESET}")

        if not catalog["products"]:
            sys.exit("catalog is empty; run POST /sync first")

        rule("5. Purchase one — inside every limit")
        first = catalog["products"][0]
        variant = next(v for v in first["variants"] if v["availability"]["status"] == "in_stock")
        print(f"  agent decides: {first['name']} / {variant['size']} at {money(variant['price'])}")
        buy(client, first, variant, variant["price"])

        rule("6. Purchase two — the same agent, now over the cap")
        second = catalog["products"][-1]
        variant2 = next(v for v in second["variants"] if v["availability"]["status"] == "in_stock")
        remaining = client.get(f"/tokens/{TOKEN_ID}").json()["remaining"]
        print(
            f"  agent decides: {second['name']} / {variant2['size']} at {money(variant2['price'])}"
        )
        print(f"  {YELLOW}remaining authority is {money(remaining)} — this will be refused{RESET}")
        buy(client, second, variant2, variant2["price"])

        rule("7. Purchase three — a category the user never authorised")
        sarees = client.get("/.well-known/agent-catalog.json?category=saree&limit=1").json()[
            "products"
        ]
        if sarees:
            saree = sarees[0]
            variant3 = saree["variants"][0]
            print(f"  agent decides: {saree['name']} at {money(variant3['price'])}")
            buy(client, saree, variant3, variant3["price"])

        rule("8. Audit trail — every money action, allowed and blocked")
        # Scoped to this run: the table is append-only and keeps every earlier
        # attempt, but reprinting the whole history is unreadable by the third run.
        full = client.get(f"/audit?token_id={TOKEN_ID}").json()
        entries = [e for e in full["entries"] if e["id"] > since]
        audit = {
            "entries": entries,
            "count": len(entries),
            "allowed": sum(1 for e in entries if e["gate_decision"] == "completed"),
            "blocked": sum(1 for e in entries if e["gate_decision"] == "blocked"),
            "errors": sum(1 for e in entries if e["gate_decision"] == "error"),
        }
        print(
            f"  {audit['count']} entries this run: "
            f"{GREEN}{audit['allowed']} allowed{RESET}, "
            f"{RED}{audit['blocked']} blocked{RESET}, {audit['errors']} errors"
            f"  {DIM}({full['count']} total in the trail){RESET}\n"
        )
        header = f"  {'#':>3}  {'decision':10} {'reason':26} {'amount':>11}  {'processor'}"
        print(BOLD + header + RESET)
        for entry in reversed(audit["entries"]):
            colour = GREEN if entry["gate_decision"] == "completed" else RED
            print(
                f"  {entry['id']:>3}  {colour}{entry['gate_decision']:10}{RESET} "
                f"{(entry['reason'] or '—'):26} {money(entry['amount']):>11}  "
                f"{entry['razorpay_order_id'] or DIM + 'no charge attempted' + RESET}"
            )

        final = client.get(f"/tokens/{TOKEN_ID}").json()
        print(
            f"\n  token after the run: spent {money(final['used_amount'])} of "
            f"{money(final['spend_cap'])}, {money(final['remaining'])} left"
        )
        print(f"  {DIM}the blocked attempts consumed none of it{RESET}")

        rule("What the demo just proved")
        print(f"  {GREEN}•{RESET} a merchant became agent-readable by adding one link tag")
        print(
            f"  {GREEN}•{RESET} messy merchant vocabulary was normalised, with a "
            f"confidence score on every value"
        )
        print(
            f"  {GREEN}•{RESET} the agent bought using AP2, and never learned what "
            f"processor was underneath"
        )
        print(
            f"  {GREEN}•{RESET} the spend cap and category allowlist each refused a "
            f"purchase, with a reason the agent can read"
        )
        print(
            f"  {GREEN}•{RESET} all {audit['count']} attempts this run are in the "
            f"audit trail; the {audit['blocked']} refusals cost the user nothing\n"
        )

        with open(REPORT_PATH, "w") as f:
            json.dump(audit, f, indent=2)
        print(f"  {DIM}full audit written to {REPORT_PATH}{RESET}\n")


if __name__ == "__main__":
    main()
