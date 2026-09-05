const AGENT_ID = "agent_ui_01";
const MERCHANT = "m_kalaghar";
let TOKEN = "tok_demo_001";

// Ids are what the protocol and the audit trail carry; these are what a shopper reads.
const WALLETS = {
  tok_demo_001:       "Aarav's wallet",
  tok_expired:        "Aarav's old wallet — expired",
  tok_other_merchant: "Aarav's wallet for another store",
};
let minted = 0;
const STORES = { m_kalaghar: "Kalaghar" };
const WHY = {
  spend_cap_exceeded:       "That would go over your spending limit",
  category_not_allowed:     "You never approved this kind of item",
  merchant_not_allowed:     "You never approved this store",
  token_expired:            "This wallet has expired",
  token_not_found:          "No such wallet",
  out_of_stock:             "Sold out while the shopper was deciding",
  price_above_declared_max: "The price went up since the shopper checked",
};
const item = c => c ? c.replace(/_/g, " ").replace(/^./, m => m.toUpperCase()) + "s" : "";
let ALL = [];      // the whole catalog, so a filter never offers an empty shelf
const ITEMS = {};  // sku -> what the thing is actually called
const indexItems = ps => ps.forEach(p =>
  p.variants.forEach(v => { ITEMS[v.sku] = `${p.name} · ${size(v.size)}`; }));
const store = m => STORES[m] || m;

const $ = id => document.getElementById(id);
const inr = n => "₹" + Number(n).toLocaleString("en-IN", {minimumFractionDigits: 2});
const esc = s => String(s).replace(/[<>&]/g, c => ({"<":"&lt;",">":"&gt;","&":"&amp;"}[c]));

async function api(path, opts) {
  const r = await fetch(path, opts);
  let body;
  try { body = await r.json(); } catch { body = {detail: await r.text()}; }
  return { ok: r.ok, status: r.status, body };
}

async function loadHealth() {
  const { body } = await api("/health");
  $("health").innerHTML =
    `<b style="color:var(--ok)">●</b> open for AI shoppers` +
    `<span class="sep"> · </span>${body.processor_mode === "razorpay_test"
      ? "Razorpay test mode" : "payments offline"}` +
    `<span class="sep"> · </span>speaks <b>AP2</b>`;
}

async function runSync() {
  const btn = $("syncBtn"), was = btn.textContent;
  btn.textContent = "syncing…"; btn.disabled = true;
  await api("/sync", {method: "POST"});
  btn.textContent = was; btn.disabled = false;
  await loadAll();
  emptyShelf();
}

// ── catalog ──────────────────────────────────────────────────────────────────
const cap1 = s => s.replace(/^./, m => m.toUpperCase());
const size = s => (s === "Free" ? "Free size" : s);

// Would this product survive the price and stock filters, plus whichever facet the
// caller is not currently rebuilding?
function matches(p, only) {
  const max = Number($("fMax").value) || Infinity;
  const inStock = $("fStock").checked;
  if (only.category && p.category.canonical !== only.category) return false;
  if (only.color && p.attributes.color.canonical !== only.color) return false;
  return p.variants.some(v =>
    v.price <= max && (!inStock || v.availability.status === "in_stock"));
}

function facet(sel, anyLabel, keep, products, key, label) {
  const counts = new Map();
  products.forEach(p => counts.set(key(p), (counts.get(key(p)) || 0) + 1));
  sel.innerHTML = "";
  sel.add(new Option(anyLabel, ""));
  [...counts.keys()].sort().forEach(k => sel.add(new Option(label(k), k)));
  sel.value = counts.has(keep) ? keep : "";   // a filter that went empty clears itself
}

function refreshFacets() {
  const cat = $("fCategory").value, col = $("fColor").value;
  facet($("fCategory"), "Everything", cat, ALL.filter(p => matches(p, {color: col})),
        p => p.category.canonical, item);
  facet($("fColor"), "Any colour", col, ALL.filter(p => matches(p, {category: cat})),
        p => p.attributes.color.canonical, cap1);
}

// Changing a filter only keeps the other filter's options honest. Nothing is
// fetched until Query is pressed.
function onFilter() { refreshFacets(); }

// The shelf stays empty until the agent actually runs a query.
function emptyShelf() {
  window._products = [];
  $("queryUrl").textContent = "";
  $("products").innerHTML =
    `<p class="empty">Set the filters, then press Query to see what the store returns.</p>`;
}

async function loadAll() {
  const { body } = await api("/.well-known/agent-catalog.json?limit=200");
  ALL = body.products || [];
  indexItems(ALL);
  refreshFacets();
}

async function runQuery() {
  const p = new URLSearchParams();
  if ($("fCategory").value) p.set("category", $("fCategory").value);
  if ($("fColor").value) p.set("color", $("fColor").value);
  if ($("fMax").value) p.set("max_price", $("fMax").value);
  if ($("fStock").checked) p.set("availability", "in_stock");
  const url = "/.well-known/agent-catalog.json?" + p;
  $("queryUrl").textContent = "GET " + url;
  const { body } = await api(url);
  render(body.products || []);
}

function norm(a) {
  if (!a || !a.canonical) return "";
  const shade = a.shade ? "/" + a.shade : "";
  const flag = a.model_confidence < 0.9
    ? `<span class="flag tint-amb">${a.model_confidence.toFixed(2)}</span>` : "";
  return `<span class="raw">${esc(a.raw)}</span> → <span class="can">${esc(a.canonical + shade)}</span>${flag}`;
}

function render(products) {
  window._products = products;
  indexItems(products);
  if (!products.length) { $("products").innerHTML = `<p class="empty">Nothing in stock matches.</p>`; return; }
  $("products").innerHTML = products.map((p, i) => `
    <div class="prod">
      <div class="name">${esc(p.name)}</div>
      <div class="norm">${[norm(p.category), norm(p.attributes.color), norm(p.attributes.material)]
        .filter(Boolean).join('<span class="sep">·</span>')}</div>
      <div class="row">
        ${p.variants.map((v, j) => v.availability.status === "in_stock"
          ? `<button class="buy tint-ok" onclick="buy(${i},${j})">${esc(size(v.size))} · ${inr(v.price)}</button>`
          : `<span class="oos">${esc(size(v.size))} · ${esc(v.availability.status)}</span>`).join("")}
      </div>
    </div>`).join("");
}

// ── the AP2 message the agent sends ──────────────────────────────────────────
// ponytail: mirrors protocols.build_ap2_cart. The UI plays the buyer, so it builds its
// own message; /intent 400s loudly if this ever drifts from the translator.
function buildCart(product, variant, protocol) {
  const cartId = "cart_ui_" + variant.sku;
  return {
    protocol, version: "0.1", agent: { id: AGENT_ID },
    intent_mandate: {
      natural_language_description:
        `Buy ${product.name} if it is in stock and within my remaining budget`,
      merchants: [product.merchant_id], requires_refundability: false },
    cart_mandate: { contents: {
      id: cartId, merchant_name: product.merchant_id, user_cart_confirmation_required: false,
      payment_request: {
        method_data: [{ supported_methods: "razorpay" }],
        details: {
          id: "req_" + cartId,
          display_items: [{ label: product.name, sku: variant.sku,
            product_id: product.product_id, quantity: 1,
            amount: { currency: "INR", value: variant.price } }],
          total: { label: "Total", amount: { currency: "INR", value: variant.price } } } } } }
  };
}

async function buy(i, j) {
  const product = window._products[i], variant = product.variants[j];
  const message = buildCart(product, variant, $("protocol").value);
  $("wireCard").hidden = false;
  $("wireReq").textContent = JSON.stringify(message, null, 2);
  $("wireRes").textContent = "…";
  $("decision").innerHTML = `<p class="empty">asking the store…</p>`;

  const res = await api("/intent", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ token_id: TOKEN, message })
  });
  $("wireRes").textContent = JSON.stringify(res.body, null, 2);

  if (!res.ok) {
    // 501 unsupported protocol, or 400 malformed: neither reaches the gate.
    $("decision").innerHTML = `<div class="banner tint-amb">
      <b>The store could not read that order</b>${esc(res.body.detail || "")}
      <div class="det">HTTP ${res.status} · no order was ever created, so nothing reached
      the payment step</div></div>`;
  } else {
    const g = res.body.gate_result, w = res.body.protocol_response;
    const money = /cap|used|remaining|attempted|price/;
    const det = Object.entries(g.details || {})
      .map(([k, v]) => `${k.replace(/_/g, " ")} ${Array.isArray(v)
        ? v.map(x => item(x) || store(x)).join(", ")
        : (money.test(k) && typeof v === "number" ? inr(v) : v)}`).join(" · ");
    $("decision").innerHTML = g.status === "completed"
      ? `<div class="banner tint-ok"><b>Paid ${inr(g.amount)}</b>
         Razorpay order ${esc(g.razorpay_order_id)}
         <div class="det">AP2 ${w.payment_status} · ${esc(det)} · receipt #${g.audit_id}</div></div>`
      : `<div class="banner tint-no"><b>${esc(WHY[g.reason] || g.message)}</b>
         ${esc(g.message)}
         <div class="det">AP2 ${w.payment_status} · ${esc(g.reason)} · ${esc(det)}
         · nothing was charged · receipt #${g.audit_id}</div></div>`;
  }
  loadToken();
}

// ── authority ────────────────────────────────────────────────────────────────
async function loadToken() {
  TOKEN = $("tokenPick").value || TOKEN;
  const { ok, body } = await api("/tokens/" + TOKEN);
  if (!ok) { $("tokenOut").textContent = "no such wallet"; return; }
  $("newCap").value = body.spend_cap;
  const pct = Math.min(100, 100 * body.used_amount / body.spend_cap);
  const expired = new Date(body.valid_until) < new Date();
  $("bar").className = "bar" + (pct >= 100 ? " full" : "");
  $("bar").firstElementChild.style.width = pct + "%";
  const sep = `<span class="sep">·</span>`;
  // Lowering a limit below what is already spent leaves the wallet overdrawn rather
  // than un-spending anything, so say that instead of printing a negative balance.
  const over = body.remaining < 0;
  $("tokenHead").innerHTML =
    `<span style="color:var(--${body.remaining > 0 ? "ok" : "red"})">` +
    `${inr(over ? 0 : body.remaining)}</span>` +
    `<span class="of">${over
      ? `left — already ${inr(-body.remaining)} over the ${inr(body.spend_cap)} limit`
      : `left of the ${inr(body.spend_cap)} you allowed`}</span>`;
  loadAudit();
  $("tokenOut").innerHTML =
    `spendable at <b>${body.merchant_allowlist.map(store).join(", ") || "any store"}</b>` + sep +
    `on <b>${body.category_allowlist.map(item).join(" & ") || "anything"}</b>` + sep +
    (expired ? `<b style="color:var(--red)">EXPIRED</b>`
             : `good until ${new Date(body.valid_until).toLocaleDateString("en-IN",
                 {day: "numeric", month: "short", year: "numeric"})}`);
}

// Raising or lowering the limit on the wallet already on screen. issue_token replaces
// the row but carries used_amount across, so a limit can never be moved out from under
// the spending it has already authorised — and everything else about the wallet has to
// be sent back verbatim or it would be clobbered.
async function setLimit() {
  const cap = Number($("newCap").value);
  if (!(cap > 0)) return;
  const { ok, body } = await api("/tokens/" + TOKEN);
  if (!ok) return;
  await api("/tokens", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({
      token_id: TOKEN, user_id: body.user_id, spend_cap: cap,
      merchant_allowlist: body.merchant_allowlist,
      category_allowlist: body.category_allowlist,
      valid_until: body.valid_until })
  });
  loadToken();
}

async function issueToken() {
  // A new token_id, not a reset: re-issuing an id keeps its used_amount, and the gate
  // never resets anything.
  const id = "tok_ui_" + Date.now().toString(36);
  const cap = Number($("newCap").value);
  await api("/tokens", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ token_id: id, user_id: "user_ui", spend_cap: cap,
      merchant_allowlist: [MERCHANT], category_allowlist: ["kurta", "dupatta"] })
  });
  const name = `New wallet ${++minted}`;
  $("tokenPick").add(new Option(name, id, true, true), 0);
  WALLETS[id] = name;
  loadToken();
}

// ── audit ────────────────────────────────────────────────────────────────────
async function loadAudit() {
  // Scoped to the wallet on screen, so switching wallets shows that wallet's history
  // rather than one undifferentiated pile.
  const all = $("auditAll").checked;
  const { body } = await api("/audit?limit=15" + (all ? "" : "&token_id=" + TOKEN));
  if (!body.entries.length) {
    $("auditSum").innerHTML = "";
    $("auditOut").innerHTML = `<p class="empty">This wallet has not tried to buy anything yet.</p>`;
    return;
  }

  // The headline number is what the trail is for: money the gate stopped.
  const stopped = body.entries
    .filter(e => e.gate_decision === "blocked")
    .reduce((sum, e) => sum + e.amount, 0);
  $("auditSum").innerHTML =
    `<span class="tally"><b style="color:var(--ok)">${body.allowed}</b> paid</span>` +
    `<span class="tally"><b style="color:var(--red)">${body.blocked}</b> refused</span>` +
    `<span class="tally"><b>${inr(stopped)}</b> of attempted spending stopped,
       none of it charged</span>`;

  $("auditOut").innerHTML = `<table><thead><tr>
      <th>when</th><th>item</th><th>result</th><th>reason</th>
      <th class="right">amount</th><th>charged</th></tr></thead><tbody>` +
    body.entries.map(e => `<tr class="${e.gate_decision}">
      <td class="when" title="${esc(e.ts || "")}">${esc(when(e.ts))}</td>
      <td class="name" title="${esc(e.sku || "")}">${esc(ITEMS[e.sku] || e.sku || "—")}</td>
      <td class="dec">${e.gate_decision === "completed" ? "✓ paid" : "✕ refused"}</td>
      <td class="reason" title="${esc(e.reason || "")}">${
        e.gate_decision === "completed" ? "—" : esc(WHY[e.reason] || e.reason || "—")}</td>
      <td class="right">${inr(e.amount)}</td>
      <td>${e.razorpay_order_id
        ? `<span title="Razorpay order id">${esc(e.razorpay_order_id)}</span>`
        : `<span class="none">nothing charged</span>`}</td></tr>`).join("") +
    `</tbody></table>`;
}

const when = ts => ts
  ? new Date(ts).toLocaleTimeString("en-IN", {hour: "2-digit", minute: "2-digit"})
  : "—";

(async function boot() {
  loadHealth();
  await loadAll();
  Object.entries(WALLETS).forEach(([id, name]) => $("tokenPick").add(new Option(name, id)));
  loadToken(); emptyShelf();
})();
