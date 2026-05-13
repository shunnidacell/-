const form = document.querySelector("#settings-form");
const stopButton = document.querySelector("#stop-button");
const resetDemoButton = document.querySelector("#reset-demo-button");
const saveSettingsButton = document.querySelector("#save-settings-button");
const statusEl = document.querySelector("#status");
const modeBadgeEl = document.querySelector("#mode-badge");
const marketsEl = document.querySelector("#markets");
const logsEl = document.querySelector("#logs");
const summaryEl = document.querySelector("#opportunities");
const tradesEl = document.querySelector("#trades");
const marketCountEl = document.querySelector("#market-count");
const pairCountEl = document.querySelector("#opportunity-count");
const bestNetEl = document.querySelector("#best-profit");
const paperProfitEl = document.querySelector("#demo-profit");
const demoCashEl = document.querySelector("#demo-cash");
const lastTickEl = document.querySelector("#last-tick");
const runNoticeEl = document.querySelector("#run-notice");
const futuresRankingEl = document.querySelector("#futures-spread-research");
const positionsEl = document.querySelector("#futures-positions");
const historyRefreshButton = document.querySelector("#history-refresh-button");
const historyAppLogEl = document.querySelector("#history-app-log");
const historyTradesEl = document.querySelector("#history-trades");
const historyFilesEl = document.querySelector("#history-files");
const eventReportEl = document.querySelector("#event-report");

let settingsApplied = false;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function numberText(value, digits = 4) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return number.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function moneyText(value) {
  return numberText(value, 4);
}

function settingsFromForm() {
  const data = new FormData(form);
  return {
    exchanges: data.get("exchanges"),
    futures_exchanges: data.get("futures_exchanges"),
    symbols: data.get("symbols"),
    trade_size_quote: Number(data.get("trade_size_quote")),
    optimize_trade_size: true,
    max_trade_size_quote: Number(data.get("max_trade_size_quote")),
    min_net_profit_pct: Number(data.get("min_net_profit_pct")),
    default_taker_fee_pct: Number(data.get("default_taker_fee_pct")),
    slippage_pct: Number(data.get("slippage_pct")),
    poll_seconds: Number(data.get("poll_seconds")),
    orderbook_limit: Number(data.get("orderbook_limit")),
    mode: "demo",
    auto_execute: data.get("auto_execute") === "on",
    live_confirm: "",
  };
}

function applySettingsToForm(settings = {}) {
  for (const [key, value] of Object.entries(settings)) {
    const field = form.elements.namedItem(key);
    if (!field) continue;
    if (field.type === "checkbox") field.checked = Boolean(value);
    else field.value = value ?? "";
  }
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) {
    alert(payload.detail || "リクエストに失敗しました");
    return null;
  }
  return payload;
}

function statusLabel(status) {
  return { ok: "取得成功", error: "エラー", no_quote: "板不足", pending: "待機", ready: "準備完了" }[status] || status || "-";
}

function pointNet(point) {
  return Number(point?.net_spread_pct ?? point?.spread_pct);
}

function renderPointList(target, points, emptyText, limit = 12) {
  const rows = (points || []).slice(0, limit);
  if (!rows.length) {
    target.className = "ranking-list empty";
    target.textContent = emptyText;
    return;
  }
  target.className = "ranking-list";
  target.innerHTML = rows.map((point) => {
    const net = pointNet(point);
    return `
      <article class="ranking-row ${net >= 0.2 ? "positive-rank" : ""}">
        <div>
          <strong>${escapeHtml(point.symbol)}</strong>
          <span>${escapeHtml(point.direction || "")}</span>
        </div>
        <div>
          <span>Gross ${numberText(point.spread_pct)}%</span>
          <span>Cost ${numberText(point.round_trip_cost_pct)}% / Cap ${moneyText(point.capacity_quote)}</span>
        </div>
        <div class="rank-net ${net >= 0.2 ? "positive" : ""}">${numberText(net)}%</div>
      </article>
    `;
  }).join("");
}

function renderSummary(points) {
  const rows = (points || []).slice(0, 8);
  if (!rows.length) {
    summaryEl.className = "opportunity-list empty";
    summaryEl.textContent = "開始後に先物価格差を表示します";
    return;
  }
  const maxNet = Math.max(...rows.map(pointNet));
  summaryEl.className = "opportunity-list";
  summaryEl.innerHTML = rows.map((point) => {
    const net = pointNet(point);
    const width = Math.max(8, Math.min(100, maxNet > 0 ? (net / maxNet) * 100 : 8));
    return `
      <article class="opportunity">
        <div>
          <div class="route">${escapeHtml(point.symbol)} <span>${escapeHtml(point.long_exchange)}</span> long → <span>${escapeHtml(point.short_exchange)}</span> short</div>
          <div class="muted">Low ${numberText(point.low_mid, 8)} / High ${numberText(point.high_mid, 8)}</div>
        </div>
        <div>
          <div class="profit-bar"><div style="width:${width}%"></div></div>
          <div class="muted">Gross ${numberText(point.spread_pct)}% / Cost ${numberText(point.round_trip_cost_pct)}% / Cap ${moneyText(point.capacity_quote)}</div>
        </div>
        <div class="profit-number">${numberText(net)}%</div>
      </article>
    `;
  }).join("");
}

function renderPositions(positions) {
  if (!positions || !positions.length) {
    positionsEl.className = "ranking-list empty";
    positionsEl.textContent = "現在の建玉はありません";
    return;
  }
  positionsEl.className = "ranking-list";
  positionsEl.innerHTML = positions.map((position) => `
    <article class="ranking-row positive-rank">
      <div>
        <strong>${escapeHtml(position.symbol)}</strong>
        <span>${escapeHtml(position.direction || "")}</span>
      </div>
      <div>
        <span>Entry ${numberText(position.entry_spread_pct)}%</span>
        <span>Last ${numberText(position.last_spread_pct)}% / Add ${position.add_count || 0}</span>
      </div>
      <div class="rank-net">${moneyText(position.quote_amount)} USDT</div>
    </article>
  `).join("");
}

function renderState(state) {
  const marketStatuses = state.market_statuses || [];
  const latestFutures = (state.futures_spread_history || []).at(-1);
  const points = latestFutures?.points || [];
  const portfolio = state.portfolio || {};

  statusEl.textContent = state.running ? "実行中" : "停止中";
  statusEl.classList.toggle("running", state.running);
  modeBadgeEl.textContent = "DEMO";
  runNoticeEl.className = `run-notice ${state.running ? "running" : "stopped"}`;
  runNoticeEl.textContent = state.running
    ? "実行中: 先物価格差を記録し、紙トレード条件を監視しています"
    : "停止中: 先物価格差の記録は止まっています";

  marketCountEl.textContent = marketStatuses.length;
  pairCountEl.textContent = points.length;
  bestNetEl.textContent = points[0] ? `${numberText(pointNet(points[0]))}%` : "-";
  paperProfitEl.textContent = moneyText(portfolio.realized_profit || 0);
  paperProfitEl.classList.toggle("positive", Number(portfolio.realized_profit || 0) >= 0);
  demoCashEl.textContent = "Paper";
  lastTickEl.textContent = state.last_tick ? new Date(state.last_tick).toLocaleString() : "未取得";

  marketsEl.innerHTML = marketStatuses.map((item) => `
    <tr>
      <td>${escapeHtml(item.exchange_id)}</td>
      <td>${escapeHtml(item.symbol)}</td>
      <td><span class="pill ${escapeHtml(item.status)}">${escapeHtml(statusLabel(item.status))}</span></td>
      <td>${numberText(item.bid, 8)}</td>
      <td>${numberText(item.ask, 8)}</td>
      <td>${numberText(item.mid, 8)}</td>
      <td>${moneyText(Math.min(Number(item.bid_capacity_quote || 0), Number(item.ask_capacity_quote || 0)))}</td>
      <td class="detail">${escapeHtml(item.message || item.futures_symbol || "")}</td>
    </tr>
  `).join("");

  renderSummary(points);
  renderPointList(futuresRankingEl, points, "開始後に表示します", 16);
  renderPositions(state.futures_positions || []);

  tradesEl.innerHTML = (state.trades || []).map((trade) => `
    <tr>
      <td>${new Date(trade.timestamp).toLocaleTimeString()}</td>
      <td>${escapeHtml(trade.symbol)}</td>
      <td>${escapeHtml(trade.direction || `${trade.buy_exchange || ""} → ${trade.sell_exchange || ""}`)}</td>
      <td>${moneyText(trade.quote_amount)}</td>
      <td>${numberText(trade.net_profit_pct)}%</td>
      <td class="${Number(trade.profit_quote) >= 0 ? "positive" : "negative"}">${Number(trade.profit_quote) >= 0 ? "+" : ""}${moneyText(trade.profit_quote)}</td>
    </tr>
  `).join("");

  logsEl.innerHTML = (state.logs || []).map((log) => `
    <div class="log ${escapeHtml(log.level)}"><span>${escapeHtml(log.time)}</span><b>${escapeHtml(log.level)}</b><span>${escapeHtml(log.message)}</span></div>
  `).join("");
}

async function loadState() {
  const response = await fetch("/api/state");
  const state = await response.json();
  if (!settingsApplied) {
    applySettingsToForm(state.settings);
    settingsApplied = true;
  }
  renderState(state);
}

async function loadHistory() {
  const response = await fetch("/api/history?limit=300");
  const history = await response.json();
  const appLines = history.app_log || [];
  historyAppLogEl.textContent = appLines.length ? appLines.join("\n") : "まだファイルログはありません";
  historyTradesEl.innerHTML = (history.trades || []).map((trade) => `
    <tr>
      <td>${trade.timestamp ? new Date(trade.timestamp).toLocaleString() : "-"}</td>
      <td>${escapeHtml(trade.symbol || "-")}</td>
      <td>${escapeHtml(trade.direction || `${trade.buy_exchange || ""} → ${trade.sell_exchange || ""}`)}</td>
      <td>${moneyText(trade.quote_amount)}</td>
      <td>${numberText(trade.net_profit_pct)}%</td>
      <td class="${Number(trade.profit_quote) >= 0 ? "positive" : "negative"}">${Number(trade.profit_quote) >= 0 ? "+" : ""}${moneyText(trade.profit_quote)}</td>
    </tr>
  `).join("");
  if (!historyTradesEl.innerHTML) {
    historyTradesEl.innerHTML = `<tr><td colspan="6" class="empty-cell">決済履歴はまだありません</td></tr>`;
  }
  eventReportEl.innerHTML = (history.futures_event_report || []).map((event) => `
    <tr>
      <td>${event.entry_time ? new Date(event.entry_time).toLocaleString() : "-"}</td>
      <td>${escapeHtml(event.symbol || "-")}</td>
      <td>${numberText(event.entry_spread_pct)}%</td>
      <td>${numberText(event.max_spread_pct)}%</td>
      <td>${event.exit_time ? `${numberText(event.exit_spread_pct)}%` : `OPEN ${numberText(event.exit_spread_pct)}%`}</td>
      <td>${numberText(event.held_minutes, 1)}分</td>
      <td>${event.add_count || 0}回 / ${moneyText(event.amount_with_add)} USDT</td>
      <td class="${Number(event.pnl_no_add) >= 0 ? "positive" : "negative"}">${Number(event.pnl_no_add) >= 0 ? "+" : ""}${moneyText(event.pnl_no_add)}</td>
      <td class="${Number(event.pnl_with_add) >= 0 ? "positive" : "negative"}">${Number(event.pnl_with_add) >= 0 ? "+" : ""}${moneyText(event.pnl_with_add)}</td>
    </tr>
  `).join("");
  if (!eventReportEl.innerHTML) {
    eventReportEl.innerHTML = `<tr><td colspan="9" class="empty-cell">1%超えイベントはまだありません</td></tr>`;
  }
  const files = history.files || {};
  historyFilesEl.innerHTML = `
    保存先: ${escapeHtml(files.app_log || "")} / ${escapeHtml(files.trades || "")} / ${escapeHtml(files.futures_spread_history || "")}
    <a href="/api/history/app-log.txt" target="_blank" rel="noreferrer">app.logを開く</a>
  `;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const state = await postJson("/api/start", settingsFromForm());
  if (state) renderState(state);
});

stopButton.addEventListener("click", async () => {
  const response = await fetch("/api/stop", { method: "POST" });
  renderState(await response.json());
});

saveSettingsButton.addEventListener("click", async () => {
  const state = await postJson("/api/settings", settingsFromForm());
  if (state) renderState(state);
});

resetDemoButton.addEventListener("click", async () => {
  const response = await fetch("/api/reset-demo", { method: "POST" });
  renderState(await response.json());
});

historyRefreshButton.addEventListener("click", loadHistory);

loadState();
loadHistory();
setInterval(loadState, 1500);
