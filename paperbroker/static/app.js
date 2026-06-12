const ACCT = document.body.dataset.account;
const $ = (sel) => document.querySelector(sel);
const fmt = (v, d = 2) => (v == null || isNaN(v)) ? "–" : Number(v).toFixed(d);
const pnlCls = (v) => v > 0 ? "pos" : (v < 0 ? "neg" : "");

async function api(path, opts) {
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

async function refresh() {
  try {
    const [summary, positions, orders, trades] = await Promise.all([
      api(`/v1/api/portfolio/${ACCT}/summary`),
      api(`/v1/api/portfolio/${ACCT}/positions/0`),
      api(`/v1/api/iserver/account/orders`),
      api(`/v1/api/iserver/account/trades`),
    ]);
    renderSummary(summary);
    renderPositions(positions);
    renderOrders(orders.orders);
    renderTrades(trades);
  } catch (e) {
    $("#msg").textContent = e.message;
  }
}

function renderSummary(s) {
  const items = [
    ["Net Liquidation", s.netliquidation], ["Cash", s.totalcashvalue],
    ["Positions Value", s.grosspositionvalue], ["Total P&L", s.unrealizedpnl],
  ];
  $("#summary").innerHTML = items.map(([label, v]) =>
    `<div><span>${label}</span><b class="${label.includes("P&L") ? pnlCls(v.amount) : ""}">
     ${fmt(v.amount)} ${v.currency}</b></div>`).join("");
}

function renderPositions(rows) {
  $("#positions tbody").innerHTML = rows.map(p => `<tr>
    <td>${p.ticker}</td><td>${p.position}</td><td>${fmt(p.avgCost, 4)}</td>
    <td>${fmt(p.mktPrice, 4)}</td><td>${fmt(p.mktValue)}</td>
    <td class="${pnlCls(p.unrealizedPnl)}">${fmt(p.unrealizedPnl)}</td>
    <td class="${pnlCls(p.realizedPnl)}">${fmt(p.realizedPnl)}</td></tr>`).join("")
    || `<tr><td colspan="7">No positions</td></tr>`;
}

function renderOrders(rows) {
  $("#orders tbody").innerHTML = rows.map(o => `<tr>
    <td>${o.orderId}</td><td>${o.ticker ?? o.conid}</td><td>${o.side}</td>
    <td>${o.orderType}</td><td>${o.totalSize}</td>
    <td>${fmt(o.price ?? o.stop_price, 4)}</td>
    <td>${o.status}${o.rejectReason ? " — " + o.rejectReason : ""}</td>
    <td>${fmt(o.avgPrice, 4)}</td>
    <td>${o.status === "Submitted"
      ? `<button class="cancel" data-id="${o.orderId}">Cancel</button>` : ""}</td>
    </tr>`).join("") || `<tr><td colspan="9">No orders</td></tr>`;
}

function renderTrades(rows) {
  $("#trades tbody").innerHTML = rows.map(t => `<tr>
    <td>${new Date(t.trade_time_r).toLocaleString()}</td><td>${t.ticker}</td>
    <td>${t.side === "B" ? "BUY" : "SELL"}</td><td>${t.size}</td>
    <td>${fmt(t.price, 4)}</td><td>${fmt(t.commission)}</td></tr>`).join("")
    || `<tr><td colspan="6">No trades</td></tr>`;
}

$("#order-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = new FormData(ev.target);
  const order = {
    ticker: f.get("ticker").toUpperCase(), side: f.get("side"),
    orderType: f.get("orderType"), quantity: Number(f.get("quantity")),
    tif: "DAY",
  };
  if (f.get("price")) order.price = Number(f.get("price"));
  if (f.get("auxPrice")) order.auxPrice = Number(f.get("auxPrice"));
  try {
    const [res] = await api(`/v1/api/iserver/account/${ACCT}/orders`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ orders: [order] }),
    });
    $("#msg").textContent = `Order ${res.order_id}: ${res.order_status}` +
      (res.text ? ` — ${res.text}` : "");
    refresh();
  } catch (e) { $("#msg").textContent = e.message; }
});

$("#price-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = new FormData(ev.target);
  try {
    const res = await api("/paper/price", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: f.get("symbol").toUpperCase(),
                             last: Number(f.get("last")) }),
    });
    $("#msg").textContent = `Price set: conid ${res.conid} last ${res.last}`;
    refresh();
  } catch (e) { $("#msg").textContent = e.message; }
});

$("#reset-btn").addEventListener("click", async () => {
  if (!confirm("Reset account? This wipes all positions, orders and trades.")) return;
  await api("/paper/reset", { method: "POST" });
  refresh();
});

$("#orders").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button.cancel");
  if (!btn) return;
  try {
    await api(`/v1/api/iserver/account/${ACCT}/order/${btn.dataset.id}`,
              { method: "DELETE" });
    refresh();
  } catch (e) { $("#msg").textContent = e.message; }
});

refresh();
setInterval(refresh, 3000);
