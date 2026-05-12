const form = document.querySelector("#settings-form");
const stopButton = document.querySelector("#stop-button");
const resetDemoButton = document.querySelector("#reset-demo-button");
const saveSettingsButton = document.querySelector("#save-settings-button");
const statusEl = document.querySelector("#status");
const modeBadgeEl = document.querySelector("#mode-badge");
const modeSelect = document.querySelector("#mode-select");
const liveConfirmRow = document.querySelector("#live-confirm-row");
const marketsEl = document.querySelector("#markets");
const logsEl = document.querySelector("#logs");
const oppsEl = document.querySelector("#opportunities");
const tradesEl = document.querySelector("#trades");
const balancesEl = document.querySelector("#balances");
const demoAccountEl = document.querySelector("#demo-account");
const marketCountEl = document.querySelector("#market-count");
const opportunityCountEl = document.querySelector("#opportunity-count");
const bestProfitEl = document.querySelector("#best-profit");
const demoProfitEl = document.querySelector("#demo-profit");
const demoCashEl = document.querySelector("#demo-cash");
const lastTickEl = document.querySelector("#last-tick");
const liveReadyEl = document.querySelector("#live-ready");
const runNoticeEl = document.querySelector("#run-notice");
const symbolTextarea = document.querySelector('textarea[name="symbols"]');
const symbolOptionsEl = document.querySelector("#symbol-options");
const clearSymbolsButton = document.querySelector("#clear-symbols-button");
const exchangeInput = document.querySelector('input[name="exchanges"]');
const exchangeOptionsEl = document.querySelector("#exchange-options");
const manualDemoForm = document.querySelector("#manual-demo-form");
const demoPriceForm = document.querySelector("#demo-price-form");
const clearDemoPriceButton = document.querySelector("#clear-demo-price-button");
const spreadChartEl = document.querySelector("#spread-chart");
const spreadRankingEl = document.querySelector("#spread-ranking");
const futuresSpreadResearchEl = document.querySelector("#futures-spread-research");
const preflightButton = document.querySelector("#preflight-button");
const preflightResultsEl = document.querySelector("#preflight-results");
const historyRefreshButton = document.querySelector("#history-refresh-button");
const historyAppLogEl = document.querySelector("#history-app-log");
const historyTradesEl = document.querySelector("#history-trades");
const historyFilesEl = document.querySelector("#history-files");

let settingsApplied = false;
const exchangeUniverse = ["binance", "okx", "bitget"];
const symbolUniverse = [
  "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT", "BNB/USDT",
  "ADA/USDT", "AVAX/USDT", "LINK/USDT", "TRX/USDT", "DOT/USDT", "TON/USDT",
  "LTC/USDT", "BCH/USDT", "UNI/USDT", "NEAR/USDT", "APT/USDT", "ARB/USDT",
  "OP/USDT", "SUI/USDT", "PEPE/USDT", "SHIB/USDT", "WIF/USDT", "FET/USDT",
  "INJ/USDT", "ATOM/USDT", "FIL/USDT", "ETC/USDT", "HBAR/USDT", "ICP/USDT",
];
const chartColors = ["#15b8a6", "#3b82f6", "#d97706", "#ef4444", "#8b5cf6", "#16a34a"];

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

function selectedSymbols() {
  return new Set(symbolTextarea.value.split(",").map((item) => item.trim().toUpperCase()).filter(Boolean));
}

function selectedExchanges() {
  return new Set(exchangeInput.value.split(",").map((item) => item.trim().toLowerCase()).filter(Boolean));
}

function renderSymbolOptions() {
  const selected = selectedSymbols();
  symbolOptionsEl.innerHTML = symbolUniverse.map((symbol) => `
    <button type="button" class="chip ${selected.has(symbol) ? "selected" : ""}" data-symbol="${symbol}">
      ${symbol.replace("/USDT", "")}
    </button>
  `).join("");
}

function renderExchangeOptions() {
  const selected = selectedExchanges();
  exchangeOptionsEl.innerHTML = exchangeUniverse.map((exchange) => `
    <button type="button" class="chip ${selected.has(exchange) ? "selected" : ""}" data-exchange="${exchange}">
      ${exchange}
    </button>
  `).join("");
}

function settingsFromForm() {
  const data = new FormData(form);
  return {
    exchanges: data.get("exchanges"),
    futures_exchanges: data.get("futures_exchanges"),
    symbols: data.get("symbols"),
    trade_size_quote: Number(data.get("trade_size_quote")),
    optimize_trade_size: data.get("optimize_trade_size") === "on",
    max_trade_size_quote: Number(data.get("max_trade_size_quote")),
    min_net_profit_pct: Number(data.get("min_net_profit_pct")),
    default_taker_fee_pct: Number(data.get("default_taker_fee_pct")),
    slippage_pct: Number(data.get("slippage_pct")),
    poll_seconds: Number(data.get("poll_seconds")),
    orderbook_limit: Number(data.get("orderbook_limit")),
    mode: data.get("mode"),
    auto_execute: data.get("auto_execute") === "on",
    live_confirm: data.get("live_confirm") || "",
  };
}

function applySettingsToForm(settings = {}) {
  for (const [key, value] of Object.entries(settings)) {
    const field = form.elements.namedItem(key);
    if (!field) continue;
    if (field.type === "checkbox") field.checked = Boolean(value);
    else if (key !== "live_confirm") field.value = value ?? "";
  }
  liveConfirmRow.classList.toggle("hidden", modeSelect.value !== "live");
  renderSymbolOptions();
  renderExchangeOptions();
}

function statusLabel(status) {
  return {
    pending: "待機",
    ready: "準備完了",
    ok: "取得成功",
    no_quote: "板不足",
    error: "エラー",
  }[status] || status || "-";
}

function feeSourceLabel(source) {
  return {
    account: "実アカウント",
    market: "市場情報",
    fallback: "設定値",
  }[source] || source || "設定値";
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

function computeSpreadRanking(marketStatuses, settings) {
  const bySymbol = new Map();
  for (const item of marketStatuses || []) {
    if (item.status !== "ok") continue;
    const bid = Number(item.bid);
    const ask = Number(item.ask);
    if (!Number.isFinite(bid) || !Number.isFinite(ask) || bid <= 0 || ask <= 0) continue;
    if (!bySymbol.has(item.symbol)) bySymbol.set(item.symbol, []);
    bySymbol.get(item.symbol).push(item);
  }

  const rows = [];
  const slippage = Number(settings.slippage_pct || 0);
  for (const [symbol, items] of bySymbol.entries()) {
    if (items.length < 2) continue;
    const buy = [...items].sort((a, b) => Number(a.ask) - Number(b.ask))[0];
    const sell = [...items].sort((a, b) => Number(b.bid) - Number(a.bid))[0];
    if (buy.exchange_id === sell.exchange_id) continue;
    const gross = ((Number(sell.bid) - Number(buy.ask)) / Number(buy.ask)) * 100;
    const cost = Number(buy.taker_fee_pct || settings.default_taker_fee_pct || 0) +
      Number(sell.taker_fee_pct || settings.default_taker_fee_pct || 0) + slippage * 2;
    rows.push({ symbol, buy, sell, gross, cost, net: gross - cost });
  }
  return rows.sort((a, b) => b.net - a.net).slice(0, 12);
}

function renderSpreadRanking(state) {
  const rows = computeSpreadRanking(state.market_statuses || [], state.settings || {});
  if (!rows.length) {
    spreadRankingEl.className = "ranking-list empty";
    spreadRankingEl.textContent = "価格取得後に表示されます";
    return;
  }
  spreadRankingEl.className = "ranking-list";
  spreadRankingEl.innerHTML = rows.map((row) => `
    <article class="ranking-row ${row.net >= 0 ? "positive-rank" : ""}">
      <div><strong>${escapeHtml(row.symbol)}</strong><span>${escapeHtml(row.buy.exchange_id)}で買い → ${escapeHtml(row.sell.exchange_id)}で売り</span></div>
      <div><span>Gross ${numberText(row.gross)}%</span><span>Cost ${numberText(row.cost)}%</span></div>
      <div class="rank-net ${row.net >= 0 ? "positive" : ""}">${numberText(row.net)}%</div>
    </article>
  `).join("");
}

function renderSpreadChart(history) {
  const rows = history || [];
  const latest = rows.at(-1);
  const symbols = (latest?.points || [])
    .slice()
    .sort((a, b) => Number(b.net_pct) - Number(a.net_pct))
    .map((point) => point.symbol)
    .slice(0, 6);

  if (rows.length < 2 || !symbols.length) {
    spreadChartEl.className = "spread-chart empty";
    spreadChartEl.textContent = "スキャン開始後に表示されます";
    return;
  }

  const width = 900;
  const height = 260;
  const pad = 36;
  const series = symbols.map((symbol) => ({
    symbol,
    values: rows.map((row, index) => {
      const point = (row.points || []).find((item) => item.symbol === symbol);
      return { index, value: point ? Number(point.net_pct) : null };
    }),
  }));
  const values = series.flatMap((item) => item.values.map((point) => point.value)).filter(Number.isFinite).sort((a, b) => a - b);
  const minValue = Math.min(-0.05, values[Math.floor(values.length * 0.05)] ?? -0.3);
  const maxValue = Math.max(0.05, values[Math.floor(values.length * 0.95)] ?? 0.1);
  const span = maxValue - minValue || 1;
  const clamp = (value) => Math.max(minValue, Math.min(maxValue, value));
  const xFor = (index) => pad + (index / Math.max(1, rows.length - 1)) * (width - pad * 2);
  const yFor = (value) => height - pad - ((clamp(value) - minValue) / span) * (height - pad * 2);
  const zeroY = yFor(0);
  const paths = series.map((item, seriesIndex) => {
    const points = item.values.filter((point) => point.value !== null);
    if (points.length < 2) return "";
    const d = points.map((point, index) => `${index === 0 ? "M" : "L"} ${xFor(point.index).toFixed(1)} ${yFor(point.value).toFixed(1)}`).join(" ");
    return `<path d="${d}" fill="none" stroke="${chartColors[seriesIndex % chartColors.length]}" stroke-width="2.5" />`;
  }).join("");
  const legend = symbols.map((symbol, index) => {
    const latestPoint = latest?.points?.find((point) => point.symbol === symbol);
    return `<span><i style="background:${chartColors[index % chartColors.length]}"></i>${escapeHtml(symbol)} ${latestPoint ? numberText(latestPoint.net_pct) : "-"}%</span>`;
  }).join("");

  spreadChartEl.className = "spread-chart";
  spreadChartEl.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="価格差推移グラフ">
      <line x1="${pad}" y1="${zeroY}" x2="${width - pad}" y2="${zeroY}" stroke="#94a3b8" stroke-dasharray="4 4" opacity="0.55" />
      <text x="${pad}" y="20" fill="#64748b">${numberText(maxValue)}%</text>
      <text x="${pad}" y="${height - 8}" fill="#64748b">${numberText(minValue)}%</text>
      ${paths}
    </svg>
    <div class="chart-legend">${legend}</div>
  `;
}

function renderFuturesResearch(history) {
  const latest = (history || []).at(-1);
  const points = (latest?.points || []).slice(0, 12);
  if (!points.length) {
    futuresSpreadResearchEl.className = "ranking-list empty";
    futuresSpreadResearchEl.textContent = "開始後に先物価格差を記録します";
    return;
  }
  futuresSpreadResearchEl.className = "ranking-list";
  futuresSpreadResearchEl.innerHTML = points.map((point) => `
    <article class="ranking-row ${Number(point.spread_pct) >= 0.2 ? "positive-rank" : ""}">
      <div><strong>${escapeHtml(point.symbol)}</strong><span>${escapeHtml(point.direction || "")}</span></div>
      <div><span>Low ${numberText(point.low_mid, 8)}</span><span>High ${numberText(point.high_mid, 8)}</span></div>
      <div class="rank-net ${Number(point.spread_pct) >= 0.2 ? "positive" : ""}">${numberText(point.spread_pct)}%</div>
    </article>
  `).join("");
}

function renderState(state) {
  const marketStatuses = state.market_statuses || [];
  const settings = state.settings || {};
  const portfolio = state.portfolio || {};

  statusEl.textContent = state.running ? "実行中" : "停止中";
  statusEl.classList.toggle("running", state.running);
  modeBadgeEl.textContent = (settings.mode || "demo").toUpperCase();
  modeBadgeEl.classList.toggle("live", settings.mode === "live");

  if (state.running) {
    runNoticeEl.className = "run-notice running";
    runNoticeEl.textContent = "実行中: リアルタイム監視しています";
  } else {
    const stoppedText = state.stopped_at ? ` 最終停止: ${new Date(state.stopped_at).toLocaleString()}` : "";
    runNoticeEl.className = "run-notice stopped";
    runNoticeEl.textContent = `停止中: 価格監視と自動デモ取引は止まっています。${stoppedText}`;
  }

  marketCountEl.textContent = marketStatuses.length;
  opportunityCountEl.textContent = (state.opportunities || []).length;
  bestProfitEl.textContent = state.opportunities?.[0] ? `${numberText(state.opportunities[0].net_profit_pct)}%` : "-";
  demoProfitEl.textContent = moneyText(portfolio.realized_profit || 0);
  demoProfitEl.classList.toggle("positive", Number(portfolio.realized_profit || 0) >= 0);
  demoCashEl.textContent = `Cash ${moneyText(portfolio.cash || 0)}`;
  lastTickEl.textContent = state.last_tick ? new Date(state.last_tick).toLocaleString() : "未取得";
  liveReadyEl.textContent = state.live_ready ? "LIVE_TRADING=true" : "LIVE_TRADING=false";

  marketsEl.innerHTML = marketStatuses.map((item) => `
    <tr>
      <td>${escapeHtml(item.exchange_id)}</td>
      <td>${escapeHtml(item.symbol)}</td>
      <td><span class="pill ${escapeHtml(item.status)}">${escapeHtml(statusLabel(item.status))}</span></td>
      <td>${numberText(item.bid, 8)}</td>
      <td>${numberText(item.ask, 8)}</td>
      <td>${numberText(item.taker_fee_pct)}%</td>
      <td>${escapeHtml(feeSourceLabel(item.fee_source))}</td>
      <td class="detail">${escapeHtml(item.message || "")}</td>
    </tr>
  `).join("");

  if (!state.opportunities?.length) {
    oppsEl.className = "opportunity-list empty";
    oppsEl.textContent = state.last_error || "まだ候補はありません";
  } else {
    oppsEl.className = "opportunity-list";
    const maxProfit = Math.max(...state.opportunities.map((item) => Number(item.net_profit_pct)));
    oppsEl.innerHTML = state.opportunities.map((item) => {
      const width = Math.max(8, Math.min(100, (Number(item.net_profit_pct) / maxProfit) * 100));
      return `
        <article class="opportunity">
          <div>
            <div class="route">${escapeHtml(item.symbol)} <span>${escapeHtml(item.buy_exchange)}</span> → <span>${escapeHtml(item.sell_exchange)}</span></div>
            <div class="muted">Buy ${numberText(item.buy_price, 8)} / Sell ${numberText(item.sell_price, 8)}</div>
          </div>
          <div>
            <div class="profit-bar"><div style="width:${width}%"></div></div>
            <div class="muted">Gross ${numberText(item.gross_profit_pct)}% / Size ${moneyText(item.quote_amount)}</div>
          </div>
          <div class="profit-number">+${numberText(item.net_profit_pct)}%</div>
        </article>
      `;
    }).join("");
  }

  tradesEl.innerHTML = (state.trades || []).map((trade) => `
    <tr>
      <td>${new Date(trade.timestamp).toLocaleTimeString()}</td>
      <td>${escapeHtml(trade.symbol)}</td>
      <td>${escapeHtml(trade.buy_exchange)} → ${escapeHtml(trade.sell_exchange)}</td>
      <td>${moneyText(trade.quote_amount)}</td>
      <td>${numberText(trade.net_profit_pct)}%</td>
      <td class="${Number(trade.profit_quote) >= 0 ? "positive" : "negative"}">${Number(trade.profit_quote) >= 0 ? "+" : ""}${moneyText(trade.profit_quote)}</td>
    </tr>
  `).join("");

  demoAccountEl.innerHTML = `
    <div class="account-item"><span>現金残高</span><strong>${moneyText(portfolio.cash || 0)} USDT</strong></div>
    <div class="account-item"><span>実現損益</span><strong class="${Number(portfolio.realized_profit || 0) >= 0 ? "positive" : "negative"}">${moneyText(portfolio.realized_profit || 0)} USDT</strong></div>
    <div class="account-item"><span>取引回数</span><strong>${portfolio.trade_count || 0}</strong></div>
  `;

  if (!state.balances || !state.balances.length) {
    balancesEl.className = "balances empty";
    balancesEl.textContent = "本番モード起動時に表示されます";
  } else {
    balancesEl.className = "balances";
    balancesEl.innerHTML = state.balances.map((balance) => {
      const totals = balance.total
        ? Object.entries(balance.total).map(([asset, amount]) => `<span>${escapeHtml(asset)} ${numberText(amount, 8)}</span>`).join("")
        : `<span>${escapeHtml(balance.message || balance.status)}</span>`;
      return `<div class="balance"><b>${escapeHtml(balance.exchange_id)}</b>${totals}</div>`;
    }).join("");
  }

  preflightResultsEl.innerHTML = (state.preflight_results || []).map((item) => `
    <tr>
      <td>${escapeHtml(item.exchange_id)}</td>
      <td>${escapeHtml(item.symbol)}</td>
      <td><span class="pill ${item.status === "ok" ? "ok" : item.status === "warn" ? "no_quote" : "error"}">${escapeHtml(item.status)}</span></td>
      <td>${numberText(item.taker_fee_pct)}%</td>
      <td>${escapeHtml(feeSourceLabel(item.fee_source))}</td>
      <td>${numberText(item.min_cost, 8)}</td>
      <td>${numberText(item.min_amount, 8)}</td>
      <td>${numberText(item.quote_balance, 8)}</td>
      <td>${numberText(item.base_balance, 8)}</td>
      <td class="detail">${escapeHtml(item.message || "")}</td>
    </tr>
  `).join("");

  logsEl.innerHTML = (state.logs || []).map((log) => `
    <div class="log ${escapeHtml(log.level)}"><span>${escapeHtml(log.time)}</span><b>${escapeHtml(log.level)}</b><span>${escapeHtml(log.message)}</span></div>
  `).join("");

  renderSpreadRanking(state);
  renderSpreadChart(state.spread_history || []);
  renderFuturesResearch(state.futures_spread_history || []);
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
      <td>${escapeHtml(trade.buy_exchange || "-")} → ${escapeHtml(trade.sell_exchange || "-")}</td>
      <td>${moneyText(trade.quote_amount)}</td>
      <td>${numberText(trade.net_profit_pct)}%</td>
      <td class="${Number(trade.profit_quote) >= 0 ? "positive" : "negative"}">${Number(trade.profit_quote) >= 0 ? "+" : ""}${moneyText(trade.profit_quote)}</td>
    </tr>
  `).join("");
  if (!historyTradesEl.innerHTML) {
    historyTradesEl.innerHTML = `<tr><td colspan="6" class="empty-cell">保存済み取引はまだありません</td></tr>`;
  }
  const files = history.files || {};
  historyFilesEl.innerHTML = `
    保存先: ${escapeHtml(files.app_log || "")} / ${escapeHtml(files.trades || "")} / ${escapeHtml(files.spread_history || "")} / ${escapeHtml(files.futures_spread_history || "")}
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

resetDemoButton.addEventListener("click", async () => {
  const response = await fetch("/api/reset-demo", { method: "POST" });
  renderState(await response.json());
});

saveSettingsButton.addEventListener("click", async () => {
  const state = await postJson("/api/settings", settingsFromForm());
  if (state) renderState(state);
});

manualDemoForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(manualDemoForm);
  const state = await postJson("/api/manual-demo-trade", {
    symbol: data.get("symbol"),
    buy_exchange: data.get("buy_exchange"),
    sell_exchange: data.get("sell_exchange"),
    quote_amount: Number(data.get("quote_amount")),
    profit_quote: Number(data.get("profit_quote")),
  });
  if (state) renderState(state);
});

demoPriceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(demoPriceForm);
  const state = await postJson("/api/demo-price-adjustment", {
    exchange_id: data.get("exchange_id"),
    symbol: data.get("symbol"),
    bid_adjust_pct: Number(data.get("bid_adjust_pct")),
    ask_adjust_pct: Number(data.get("ask_adjust_pct")),
  });
  if (state) renderState(state);
});

clearDemoPriceButton.addEventListener("click", async () => {
  const response = await fetch("/api/clear-demo-price-adjustments", { method: "POST" });
  renderState(await response.json());
});

preflightButton.addEventListener("click", async () => {
  const settings = settingsFromForm();
  const state = await postJson("/api/preflight", {
    exchanges: settings.exchanges,
    symbols: settings.symbols,
    quote_amount: settings.trade_size_quote || 25,
  });
  if (state) renderState(state);
});

historyRefreshButton.addEventListener("click", loadHistory);

modeSelect.addEventListener("change", () => {
  liveConfirmRow.classList.toggle("hidden", modeSelect.value !== "live");
});

symbolOptionsEl.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-symbol]");
  if (!button) return;
  const selected = selectedSymbols();
  const symbol = button.dataset.symbol;
  if (selected.has(symbol)) selected.delete(symbol);
  else selected.add(symbol);
  symbolTextarea.value = [...selected].join(",");
  renderSymbolOptions();
});

clearSymbolsButton.addEventListener("click", () => {
  symbolTextarea.value = "";
  renderSymbolOptions();
});

symbolTextarea.addEventListener("input", renderSymbolOptions);

exchangeOptionsEl.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-exchange]");
  if (!button) return;
  const selected = selectedExchanges();
  const exchange = button.dataset.exchange;
  if (selected.has(exchange)) selected.delete(exchange);
  else selected.add(exchange);
  exchangeInput.value = [...selected].join(",");
  renderExchangeOptions();
});

exchangeInput.addEventListener("input", renderExchangeOptions);

renderSymbolOptions();
renderExchangeOptions();
loadState();
loadHistory();
setInterval(loadState, 1500);
