from typing import Any

from pydantic import BaseModel, Field

SUPPORTED = ("AP2",)
DECLARED = ("AP2", "ACP", "x402")


class OrderIntent(BaseModel):
    """The protocol-agnostic internal format (PRD 7.4).

    Everything downstream sees only this and cannot tell which protocol was spoken.
    """

    product_id: str
    sku: str
    quantity: int = Field(ge=1)
    agent_id: str
    declared_max_price: float  # rupees, as the agent stated it
    protocol_used: str
    raw_protocol_payload: dict[str, Any]


class UnsupportedProtocol(Exception):
    pass


class MalformedMessage(Exception):
    pass


def _dig(payload: dict, *path, default=None):
    node = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


# ── AP2 ───────────────────────────────────────────────────────────────────────


def ap2_to_intent(payload: dict) -> OrderIntent:
    """AP2 CartMandate -> OrderIntent.

    declared_max_price comes from the cart total, not the line item: a cart may carry
    several display items, and the total is what the mandate authorised.
    """
    contents = _dig(payload, "cart_mandate", "contents")
    if not contents:
        raise MalformedMessage("AP2 message has no cart_mandate.contents")

    details = _dig(contents, "payment_request", "details", default={})
    items = details.get("display_items") or []
    if not items:
        raise MalformedMessage("AP2 cart has no display_items")
    first = items[0]

    sku = first.get("sku")
    product_id = first.get("product_id")
    if not sku or not product_id:
        raise MalformedMessage("AP2 display_item is missing sku or product_id")

    total = _dig(details, "total", "amount", "value")
    if total is None:
        raise MalformedMessage("AP2 cart has no total.amount.value")

    agent_id = _dig(payload, "agent", "id") or _dig(contents, "agent_id") or "unknown_agent"

    return OrderIntent(
        product_id=product_id,
        sku=sku,
        quantity=int(first.get("quantity", 1)),
        agent_id=agent_id,
        declared_max_price=float(total),
        protocol_used="AP2",
        raw_protocol_payload=payload,
    )


def intent_result_to_ap2(intent: OrderIntent, result: dict) -> dict:
    """Merchant decision -> the AP2 response shape (PRD 7.5).

    A block is a structured decline, not a transport error: an agent that cannot tell
    "your cap is spent" from "the server broke" will retry the first one.
    """
    allowed = result["status"] == "completed"
    body = {
        "protocol": "AP2",
        "version": "0.1",
        "cart_mandate_id": _dig(intent.raw_protocol_payload, "cart_mandate", "contents", "id"),
        "payment_status": "SUCCEEDED" if allowed else "DECLINED",
        "transaction": {
            "sku": intent.sku,
            "amount": {
                "currency": result.get("currency", "INR"),
                "value": result.get("amount"),
            },
            "processor_reference": result.get("razorpay_order_id"),
        },
        "audit_reference": result.get("audit_id"),
    }
    if not allowed:
        body["decline"] = {
            "code": result.get("reason"),
            "message": result.get("message"),
            "details": result.get("details", {}),
            "retryable": False,  # a bounded-authority refusal never becomes valid
        }
    return body


# ── Declared, not implemented ────────────────────────────────────────────────────


def acp_to_intent(payload: dict) -> OrderIntent:
    raise UnsupportedProtocol(
        "ACP translator is declared but not implemented. Add it here; nothing "
        "downstream of OrderIntent needs to change."
    )


def x402_to_intent(payload: dict) -> OrderIntent:
    raise UnsupportedProtocol(
        "x402 translator is declared but not implemented. Add it here; nothing "
        "downstream of OrderIntent needs to change."
    )


TRANSLATORS = {
    "AP2": ap2_to_intent,
    "ACP": acp_to_intent,
    "x402": x402_to_intent,
}

RESPONDERS = {
    "AP2": intent_result_to_ap2,
}


def translate(payload: dict) -> OrderIntent:
    """Dispatch on the protocol the message declares."""
    protocol = payload.get("protocol")
    if protocol not in TRANSLATORS:
        raise UnsupportedProtocol(
            f"unknown protocol {protocol!r}; declared: {', '.join(DECLARED)}, "
            f"implemented: {', '.join(SUPPORTED)}"
        )
    return TRANSLATORS[protocol](payload)


def respond(intent: OrderIntent, result: dict) -> dict:
    responder = RESPONDERS.get(intent.protocol_used)
    if responder is None:
        raise UnsupportedProtocol(f"no responder for {intent.protocol_used}")
    return responder(intent, result)


def build_ap2_cart(
    product_id: str,
    sku: str,
    name: str,
    price: float,
    merchant_id: str,
    agent_id: str,
    quantity: int = 1,
    cart_id: str = "cart_demo_001",
) -> dict:
    """Construct a well-formed AP2 message, for the demo agent and the tests."""
    return {
        "protocol": "AP2",
        "version": "0.1",
        "agent": {"id": agent_id},
        "intent_mandate": {
            "natural_language_description": (
                f"Buy {name} if it is in stock and within my remaining budget"
            ),
            "merchants": [merchant_id],
            "requires_refundability": False,
        },
        "cart_mandate": {
            "contents": {
                "id": cart_id,
                "merchant_name": merchant_id,
                "user_cart_confirmation_required": False,
                "payment_request": {
                    "method_data": [{"supported_methods": "razorpay"}],
                    "details": {
                        "id": f"req_{cart_id}",
                        "display_items": [
                            {
                                "label": name,
                                "sku": sku,
                                "product_id": product_id,
                                "quantity": quantity,
                                "amount": {"currency": "INR", "value": price},
                            }
                        ],
                        "total": {
                            "label": "Total",
                            "amount": {
                                "currency": "INR",
                                "value": round(price * quantity, 2),
                            },
                        },
                    },
                },
            },
            # Round-tripped but not verified: signature checking is out of scope.
            "merchant_authorization": "demo-unsigned",
        },
    }
