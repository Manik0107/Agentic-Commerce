import datetime as dt
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

from . import catalog, db, mock_merchant, payment, protocols, taxonomy  # noqa: E402


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    for seed in mock_merchant.SEED_TOKENS:
        if payment.get_token(seed["token_id"]) is None:
            payment.issue_token(**seed)
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Agentic Commerce Infrastructure",
    version="1.0.0",
    description=(
        "Catalog layer, protocol adapter and bounded-authority payment gate. "
        "Lets an AI buyer read a merchant's catalog, express purchase intent "
        "in its own protocol, and pay within hard pre-set limits."
    ),
)


# ── Component A: catalog ──────────────────────────────────────────────────────


UI_DIR = Path(__file__).parent.parent / "ui"
app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")


def _page(name: str) -> HTMLResponse:
    """A UI page, read per request, with its assets stamped by mtime.

    StaticFiles sends no Cache-Control, so a browser is free to serve a stale script
    from cache without revalidating. The stamp changes whenever the file does, which
    is the difference between demoing the current code and demoing last hour's.
    """
    stamp = int(max((UI_DIR / f).stat().st_mtime for f in ("style.css", "app.js")))
    html = (UI_DIR / name).read_text().replace("{v}", str(stamp))
    return HTMLResponse(html, headers={"cache-control": "no-store"})


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def storefront():
    """The merchant's existing page, plus one line (PRD 6.6).

    The link tag is the entire integration surface for discovery.
    """
    return _page("index.html")


@app.get("/demo", response_class=HTMLResponse, include_in_schema=False)
def demo_page():
    """The live walk-through of the same endpoints an agent would call."""
    return _page("demo.html")


@app.get("/.well-known/agent-catalog.json")
def agent_catalog(
    category: str | None = None,
    color: str | None = None,
    shade: str | None = None,
    material: str | None = None,
    max_price: float | None = Query(None, description="rupees, inclusive"),
    min_price: float | None = Query(None, description="rupees, inclusive"),
    availability: str | None = None,
    merchant_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    """Deterministic, structured filters only. No model runs in this path (PRD 6.5)."""
    products = catalog.query(
        category=category,
        color=color,
        shade=shade,
        material=material,
        max_price=max_price,
        min_price=min_price,
        availability=availability,
        merchant_id=merchant_id,
        limit=limit,
    )
    return {
        "taxonomy_version": taxonomy.TAXONOMY_VERSION,
        "count": len(products),
        "order_intent_endpoint": "/intent",
        "supported_protocols": list(protocols.SUPPORTED),
        "products": products,
    }


@app.post("/sync")
def sync(use_llm: bool = True):
    """Pull the merchant source, normalise, upsert (PRD 6.1). Idempotent.

    Triggered rather than scheduled; a merchant webhook would call the same endpoint.
    """
    return catalog.sync(use_llm=use_llm)


@app.get("/catalog/stats")
def catalog_stats():
    return catalog.stats()


# ── Component B + C: intent through the gate ──────────────────────────────────


class IntentRequest(BaseModel):
    token_id: str = Field(description="the scoped payment token to spend against")
    message: dict = Field(description="a purchase intent in a supported protocol")


@app.post("/intent")
def order_intent(request: IntentRequest):
    """The whole path of PRD 9: translate -> live re-check -> gate -> processor or
    refusal -> audit, with the answer translated back into the agent's protocol.
    """
    try:
        intent = protocols.translate(request.message)
    except protocols.UnsupportedProtocol as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except protocols.MalformedMessage as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    result = payment.process(intent, request.token_id)
    return {
        "internal_order_intent": intent.model_dump(exclude={"raw_protocol_payload"}),
        "gate_result": result,
        "protocol_response": protocols.respond(intent, result),
    }


# ── Component C: tokens and the audit trail ───────────────────────────────────


class TokenRequest(BaseModel):
    token_id: str
    user_id: str
    spend_cap: float = Field(gt=0, description="rupees")
    merchant_allowlist: list[str]
    category_allowlist: list[str] = []
    valid_until: str | None = Field(None, description="ISO 8601; defaults to +30 days")


@app.post("/tokens")
def create_token(request: TokenRequest):
    valid_until = request.valid_until or (
        dt.datetime.now(dt.UTC) + dt.timedelta(days=30)
    ).isoformat(timespec="seconds")
    return payment.issue_token(
        token_id=request.token_id,
        user_id=request.user_id,
        spend_cap_paise=int(round(request.spend_cap * 100)),
        merchant_allowlist=request.merchant_allowlist,
        category_allowlist=request.category_allowlist,
        valid_until=valid_until,
    )


@app.get("/tokens/{token_id}")
def read_token(token_id: str):
    token = payment.get_token(token_id)
    if token is None:
        raise HTTPException(status_code=404, detail="no such token")
    return token


@app.get("/audit")
def audit(limit: int = Query(100, ge=1, le=1000), token_id: str | None = None):
    """Every payment attempt, allowed or blocked (PRD 8.6)."""
    entries = payment.audit_log(limit=limit, token_id=token_id)
    return {
        "count": len(entries),
        "allowed": sum(1 for e in entries if e["gate_decision"] == "completed"),
        "blocked": sum(1 for e in entries if e["gate_decision"] == "blocked"),
        "errors": sum(1 for e in entries if e["gate_decision"] == "error"),
        "entries": entries,
    }


@app.get("/health")
def health():
    processor = "razorpay_test" if payment.RAZORPAY_KEY_ID else "offline_stub"
    return {
        "status": "ok",
        "processor_mode": processor,
        "supported_protocols": list(protocols.SUPPORTED),
        "declared_protocols": list(protocols.DECLARED),
    }
