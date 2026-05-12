const form = document.querySelector("#settings-form");
const stopButton = document.querySelector("#stop-button");
const resetDemoButton = document.querySelector("#reset-demo-button");
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
const symbolTextarea = document.querySelector('textarea[name="symbols"]');
const symbolOptionsEl = document.querySelector("#symbol-options");
const clearSymbolsButton = document.querySelector("#clear-symbols-button");
let settingsApplied = false;

const symbolUniverse = [
  "BTC/USDT",
  "ETH/USDT",
  "SOL/USDT",
  "XRP/USDT",
  "DOGE/USDT",
  "BNB/USDT",
  "ADA/USDT",
  "AVAX/USDT",
  "LINK/USDT",
  "TRX/USDT",
  "DOT/USDT",
  "TON/USDT",
  "LTC/USDT",
  "BCH/USDT",
  "UNI/USDT",
  "NEAR/USDT",
  "APT/USDT",
  "ARB/USDT",
  "OP/USDT",
  "SUI/USDT",
  "PEPE/USDT",
  "SHIB/USDT",
  "WIF/USDT",
  "FET/USDT",
  "INJ/USDT",
  "ATOM/USDT",
  "FIL/USDT",
  "ETC/USDT",
  "HBAR/USDT",
  "ICP/USDT",
];

function selectedSymbols() {
  return new Set(
    symbolTextarea.value
      .split(",")
      .map((item) => item.trim().toUpperCase())
      .filter(Boolean),
  );
}

function syncSymbolTextarea(symbols) {
  symbolTextarea.value = [...symbols].join(",");
}

function renderSymbolOptions() {
  const selected = selectedSymbols();
  symbolOptionsEl.innerHTML = symbolUniverse
    .map(
      (symbol) => `
        <button type="button" class="symbol-chip ${selected.has(symbol) ? "selected" : ""}" data-symbol="${symbol}">
          ${symbol.replace("/USDT", "")}
        </button>
      `,
    )
    .join("");
}

function settingsFromForm() {
  const data = new FormData(form);
  return {
    exchanges: data.get("exchanges"),
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
    if (field.type === "checkbox") {
      field.checked = Boolean(value);
    } else if (key !== "live_confirm") {
      field.value = value ?? "";
    }
  }
  liveConfirmRow.classList.toggle("hidden", modeSelect.value !== "live");
  renderSymbolOptions();
}

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

function statusLabel(status) {
  const labels = {
    pending: "待機",
    ready: "準備完了",
    ok: "取得成功",
    no_quote: "板不足",
    error: "エラー",
  };
  return labels[status] || status || "-";
}

function feeSourceLabel(source) {
  const labels = {
    account: "実アカウント",
    market: "市場情報",
    fallback: "設定値",
  };
  return labels[source] || source || "設定値";
}

function renderState(state) {
  const marketStatuses = state.market_statuses || [];
  const settings = state.settings || {};
  const portfolio = state.portfolio || {};

  statusEl.textContent = state.running ? "実行中" : "停止中";
  statusEl.classList.toggle("running", state.running);
  modeBadgeEl.textContent = (settings.mode || "demo").toUpperCase();
  modeBadgeEl.classList.toggle("live", settings.mode === "live");

  marketCountEl.textContent = marketStatuses.length;
  opportunityCountEl.textContent = state.opportunities.length;
  bestProfitEl.textContent = state.opportunities[0] ? `${numberText(state.opportunities[0].net_profit_pct)}%` : "-";
  demoProfitEl.textContent = moneyText(portfolio.realized_profit || 0);
  demoCashEl.textContent = `Cash ${moneyText(portfolio.cash || 0)}`;
  lastTickEl.textContent = state.last_tick ? new Date(state.last_tick).toLocaleString() : "未取得";
  liveReadyEl.textContent = state.live_ready ? "LIVE_TRADING=true" : "LIVE_TRADING=false";

  marketsEl.innerHTML = marketStatuses
    .map(
      (item) => `
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
      `,
    )
    .join("");

  if (state.opportunities.length === 0) {
    oppsEl.className = "opportunity-list empty";
    oppsEl.textContent = state.last_error || "まだ候補はありません";
  } else {
    oppsEl.className = "opportunity-list";
    const maxProfit = Math.max(...state.opportunities.map((item) => Number(item.net_profit_pct)));
    oppsEl.innerHTML = state.opportunities
      .map((item) => {
        const width = Math.max(8, Math.min(100, (Number(item.net_profit_pct) / maxProfit) * 100));
        return `
          <article class="opportunity">
            <div>
              <div class="route">${escapeHtml(item.symbol)} <span>${escapeHtml(item.buy_exchange)}</span> → <span>${escapeHtml(item.sell_exchange)}</span></div>
              <div>Buy ${numberText(item.buy_price, 8)} / Sell ${numberText(item.sell_price, 8)}</div>
            </div>
            <div>
              <div class="profit-bar"><div style="width:${width}%"></div></div>
              <div>Gross ${numberText(item.gross_profit_pct)}% / Size ${moneyText(item.quote_amount)}</div>
            </div>
            <div class="profit-number">+${numberText(item.net_profit_pct)}%</div>
          </article>
        `;
      })
      .join("");
  }

  tradesEl.innerHTML = (state.trades || [])
    .map(
      (trade) => `
        <tr>
          <td>${new Date(trade.timestamp).toLocaleTimeString()}</td>
          <td>${escapeHtml(trade.symbol)}</td>
          <td>${escapeHtml(trade.buy_exchange)} → ${escapeHtml(trade.sell_exchange)}</td>
          <td class="positive">+${moneyText(trade.profit_quote)}</td>
        </tr>
      `,
    )
    .join("");

  demoAccountEl.innerHTML = `
    <div class="account-item">
      <span>現金残高</span>
      <strong>${moneyText(portfolio.cash || 0)} USDT</strong>
    </div>
    <div class="account-item">
      <span>実現損益</span>
      <strong class="${Number(portfolio.realized_profit || 0) >= 0 ? "positive" : ""}">${moneyText(portfolio.realized_profit || 0)} USDT</strong>
    </div>
    <div class="account-item">
      <span>取引回数</span>
      <strong>${portfolio.trade_count || 0}</strong>
    </div>
  `;

  if (!state.balances || state.balances.length === 0) {
    balancesEl.className = "balances empty";
    balancesEl.textContent = "本番モード起動時に表示されます";
  } else {
    balancesEl.className = "balances";
    balancesEl.innerHTML = state.balances
      .map((balance) => {
        const totals = balance.total
          ? Object.entries(balance.total)
              .map(([asset, amount]) => `<span>${escapeHtml(asset)} ${numberText(amount, 8)}</span>`)
              .join("")
          : `<span>${escapeHtml(balance.message || balance.status)}</span>`;
        return `<div class="balance"><b>${escapeHtml(balance.exchange_id)}</b>${totals}</div>`;
      })
      .join("");
  }

  logsEl.innerHTML = state.logs
    .map(
      (log) => `
        <div class="log ${escapeHtml(log.level)}">
          <span>${escapeHtml(log.time)}</span>
          <b>${escapeHtml(log.level)}</b>
          <span>${escapeHtml(log.message)}</span>
        </div>
      `,
    )
    .join("");
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

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const state = await postJson("/api/start", settingsFromForm());
  if (state) renderState(state);
});

const saveSettingsButton = document.createElement("button");
saveSettingsButton.type = "button";
saveSettingsButton.textContent = "設定保存";
saveSettingsButton.id = "save-settings-button";
document.querySelector(".actions")?.appendChild(saveSettingsButton);

saveSettingsButton.addEventListener("click", async () => {
  const state = await postJson("/api/settings", settingsFromForm());
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

modeSelect.addEventListener("change", () => {
  liveConfirmRow.classList.toggle("hidden", modeSelect.value !== "live");
});

symbolOptionsEl.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-symbol]");
  if (!button) return;
  const selected = selectedSymbols();
  const symbol = button.dataset.symbol;
  if (selected.has(symbol)) {
    selected.delete(symbol);
  } else {
    selected.add(symbol);
  }
  syncSymbolTextarea(selected);
  renderSymbolOptions();
});

symbolTextarea.addEventListener("input", renderSymbolOptions);

clearSymbolsButton.addEventListener("click", () => {
  symbolTextarea.value = "";
  renderSymbolOptions();
});

renderSymbolOptions();
loadState();
setInterval(loadState, 1500);

const manualDemoHtml = `
  <form id="manual-demo-form" class="manual-demo">
    <div class="section-title compact"><h2>手動デモ取引</h2></div>
    <div class="grid-2">
      <label><span>銘柄</span><input name="symbol" value="BTC/USDT" /></label>
      <label><span>取引額</span><input name="quote_amount" type="number" step="1" value="100" /></label>
      <label><span>買い取引所</span><input name="buy_exchange" value="binance" /></label>
      <label><span>売り取引所</span><input name="sell_exchange" value="okx" /></label>
      <label><span>損益 USDT</span><input name="profit_quote" type="number" step="0.01" value="1" /></label>
    </div>
    <button type="submit" class="primary">手動でデモ約定</button>
  </form>
`;

const controlPanel = document.querySelector('.control-panel');
if (controlPanel && !document.querySelector('#manual-demo-form')) {
  controlPanel.insertAdjacentHTML('beforeend', manualDemoHtml);
}

const manualDemoForm = document.querySelector('#manual-demo-form');
manualDemoForm?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const data = new FormData(manualDemoForm);
  const state = await postJson('/api/manual-demo-trade', {
    symbol: data.get('symbol'),
    buy_exchange: data.get('buy_exchange'),
    sell_exchange: data.get('sell_exchange'),
    quote_amount: Number(data.get('quote_amount')),
    profit_quote: Number(data.get('profit_quote')),
  });
  if (state) renderState(state);
});

const demoPriceHtml = `
  <form id="demo-price-form" class="manual-demo">
    <div class="section-title compact"><h2>デモ価格操作</h2></div>
    <div class="grid-2">
      <label><span>取引所</span><input name="exchange_id" value="okx" /></label>
      <label><span>銘柄</span><input name="symbol" value="BTC/USDT" /></label>
      <label><span>Bid調整 %</span><input name="bid_adjust_pct" type="number" step="0.01" value="1" /></label>
      <label><span>Ask調整 %</span><input name="ask_adjust_pct" type="number" step="0.01" value="0" /></label>
    </div>
    <div class="actions single-row">
      <button type="submit" class="primary">デモ価格を反映</button>
      <button type="button" id="clear-demo-price-button">価格操作クリア</button>
    </div>
  </form>
`;

if (controlPanel && !document.querySelector('#demo-price-form')) {
  controlPanel.insertAdjacentHTML('beforeend', demoPriceHtml);
}

const demoPriceForm = document.querySelector('#demo-price-form');
demoPriceForm?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const data = new FormData(demoPriceForm);
  const state = await postJson('/api/demo-price-adjustment', {
    exchange_id: data.get('exchange_id'),
    symbol: data.get('symbol'),
    bid_adjust_pct: Number(data.get('bid_adjust_pct')),
    ask_adjust_pct: Number(data.get('ask_adjust_pct')),
  });
  if (state) renderState(state);
});

document.querySelector('#clear-demo-price-button')?.addEventListener('click', async () => {
  const response = await fetch('/api/clear-demo-price-adjustments', { method: 'POST' });
  renderState(await response.json());
});

const rankingPanelHtml = `
  <section class="main-panel" id="spread-ranking-panel">
    <div class="section-title">
      <h2>価格差ランキング</h2>
      <span>現在の板から自動判定</span>
    </div>
    <div id="spread-ranking" class="ranking-list empty">価格取得後に表示されます</div>
  </section>
`;
const mainLayout = document.querySelector('.layout');
const firstSplit = document.querySelector('.split');
if (mainLayout && firstSplit && !document.querySelector('#spread-ranking-panel')) {
  firstSplit.insertAdjacentHTML('beforebegin', rankingPanelHtml);
}
const spreadRankingEl = document.querySelector('#spread-ranking');
const tradesTableHead = tradesEl?.closest('table')?.querySelector('thead');
if (tradesTableHead) {
  tradesTableHead.innerHTML = `
    <tr>
      <th>時刻</th>
      <th>銘柄</th>
      <th>経路</th>
      <th>取引額</th>
      <th>純利益率</th>
      <th>利益</th>
    </tr>
  `;
}

function computeSpreadRanking(marketStatuses, settings) {
  const bySymbol = new Map();
  for (const item of marketStatuses || []) {
    if (item.status !== 'ok') continue;
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
    if (!buy || !sell || buy.exchange_id === sell.exchange_id) continue;
    const buyAsk = Number(buy.ask);
    const sellBid = Number(sell.bid);
    const gross = ((sellBid - buyAsk) / buyAsk) * 100;
    const cost = Number(buy.taker_fee_pct || settings.default_taker_fee_pct || 0) +
      Number(sell.taker_fee_pct || settings.default_taker_fee_pct || 0) +
      slippage * 2;
    const net = gross - cost;
    rows.push({ symbol, buy, sell, gross, cost, net });
  }
  return rows.sort((a, b) => b.net - a.net).slice(0, 12);
}

function renderSpreadRanking(state) {
  if (!spreadRankingEl) return;
  const rows = computeSpreadRanking(state.market_statuses || [], state.settings || {});
  if (rows.length === 0) {
    spreadRankingEl.className = 'ranking-list empty';
    spreadRankingEl.textContent = '価格取得後に表示されます';
    return;
  }
  spreadRankingEl.className = 'ranking-list';
  spreadRankingEl.innerHTML = rows.map((row) => `
    <article class="ranking-row ${row.net >= 0 ? 'positive-rank' : ''}">
      <div>
        <strong>${escapeHtml(row.symbol)}</strong>
        <span>${escapeHtml(row.buy.exchange_id)}で買い → ${escapeHtml(row.sell.exchange_id)}で売り</span>
      </div>
      <div>
        <span>Gross ${numberText(row.gross)}%</span>
        <span>Cost ${numberText(row.cost)}%</span>
      </div>
      <div class="rank-net ${row.net >= 0 ? 'positive' : ''}">${numberText(row.net)}%</div>
    </article>
  `).join('');
}

const originalRenderStateForRanking = renderState;
renderState = function patchedRenderState(state) {
  originalRenderStateForRanking(state);
  renderSpreadRanking(state);
  tradesEl.innerHTML = (state.trades || [])
    .map(
      (trade) => `
        <tr>
          <td>${new Date(trade.timestamp).toLocaleTimeString()}</td>
          <td>${escapeHtml(trade.symbol)}</td>
          <td>${escapeHtml(trade.buy_exchange)} → ${escapeHtml(trade.sell_exchange)}</td>
          <td>${moneyText(trade.quote_amount)}</td>
          <td>${numberText(trade.net_profit_pct)}%</td>
          <td class="${Number(trade.profit_quote) >= 0 ? 'positive' : 'negative'}">${Number(trade.profit_quote) >= 0 ? '+' : ''}${moneyText(trade.profit_quote)}</td>
        </tr>
      `,
    )
    .join('');
};

const spreadChartPanelHtml = `
  <section class="main-panel" id="spread-chart-panel">
    <div class="section-title">
      <h2>価格差推移グラフ</h2>
      <span>純価格差 %</span>
    </div>
    <div id="spread-chart" class="spread-chart empty">スキャン開始後に表示されます</div>
  </section>
`;
const rankingPanel = document.querySelector('#spread-ranking-panel');
if (rankingPanel && !document.querySelector('#spread-chart-panel')) {
  rankingPanel.insertAdjacentHTML('beforebegin', spreadChartPanelHtml);
}
const spreadChartEl = document.querySelector('#spread-chart');
const chartColors = ['#21c7a8', '#70a7ff', '#f5c84b', '#ff6b6b', '#b48cff', '#58d68d', '#ff9f43', '#4dd0e1'];

function renderSpreadChart(history) {
  if (!spreadChartEl) return;
  const rows = history || [];
  const symbols = [...new Set(rows.flatMap((row) => (row.points || []).map((point) => point.symbol)))].slice(0, 12);
  if (rows.length < 2 || symbols.length === 0) {
    spreadChartEl.className = 'spread-chart empty';
    spreadChartEl.textContent = 'スキャン開始後に表示されます';
    return;
  }

  const width = 900;
  const height = 260;
  const pad = 34;
  const series = symbols.map((symbol) => ({
    symbol,
    values: rows.map((row, index) => {
      const point = (row.points || []).find((item) => item.symbol === symbol);
      return { index, value: point ? Number(point.net_pct) : null };
    }),
  }));
  const values = series.flatMap((item) => item.values.map((point) => point.value)).filter((value) => Number.isFinite(value));
  const minValue = Math.min(...values, -0.1);
  const maxValue = Math.max(...values, 0.1);
  const span = maxValue - minValue || 1;
  const xFor = (index) => pad + (index / Math.max(1, rows.length - 1)) * (width - pad * 2);
  const yFor = (value) => height - pad - ((value - minValue) / span) * (height - pad * 2);
  const zeroY = yFor(0);

  const paths = series.map((item, seriesIndex) => {
    const points = item.values.filter((point) => point.value !== null);
    if (points.length < 2) return '';
    const d = points.map((point, pointIndex) => `${pointIndex === 0 ? 'M' : 'L'} ${xFor(point.index).toFixed(1)} ${yFor(point.value).toFixed(1)}`).join(' ');
    return `<path d="${d}" fill="none" stroke="${chartColors[seriesIndex % chartColors.length]}" stroke-width="2" />`;
  }).join('');

  const legend = symbols.map((symbol, index) => `
    <span><i style="background:${chartColors[index % chartColors.length]}"></i>${escapeHtml(symbol)}</span>
  `).join('');

  spreadChartEl.className = 'spread-chart';
  spreadChartEl.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="価格差推移グラフ">
      <line x1="${pad}" y1="${zeroY}" x2="${width - pad}" y2="${zeroY}" stroke="#46505c" stroke-dasharray="4 4" />
      <text x="${pad}" y="20" fill="#a8b0bd">${numberText(maxValue)}%</text>
      <text x="${pad}" y="${height - 8}" fill="#a8b0bd">${numberText(minValue)}%</text>
      ${paths}
    </svg>
    <div class="chart-legend">${legend}</div>
  `;
}

const previousRenderStateForChart = renderState;
renderState = function chartRenderState(state) {
  previousRenderStateForChart(state);
  renderSpreadChart(state.spread_history || []);
};

// Cleaner chart renderer: show only the most relevant symbols and clamp the visual range.
renderSpreadChart = function renderSpreadChartCompact(history) {
  if (!spreadChartEl) return;
  const rows = history || [];
  const latest = rows.at(-1);
  const rankedSymbols = (latest?.points || [])
    .slice()
    .sort((a, b) => Number(b.net_pct) - Number(a.net_pct))
    .map((point) => point.symbol)
    .slice(0, 6);
  const symbols = rankedSymbols.length
    ? rankedSymbols
    : [...new Set(rows.flatMap((row) => (row.points || []).map((point) => point.symbol)))].slice(0, 6);

  if (rows.length < 2 || symbols.length === 0) {
    spreadChartEl.className = 'spread-chart empty';
    spreadChartEl.textContent = 'スキャン開始後に表示されます';
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
  const values = series.flatMap((item) => item.values.map((point) => point.value)).filter((value) => Number.isFinite(value));
  values.sort((a, b) => a - b);
  const p05 = values[Math.floor(values.length * 0.05)] ?? -0.3;
  const p95 = values[Math.floor(values.length * 0.95)] ?? 0.1;
  const minValue = Math.min(-0.05, p05);
  const maxValue = Math.max(0.05, p95);
  const span = maxValue - minValue || 1;
  const clamp = (value) => Math.max(minValue, Math.min(maxValue, value));
  const xFor = (index) => pad + (index / Math.max(1, rows.length - 1)) * (width - pad * 2);
  const yFor = (value) => height - pad - ((clamp(value) - minValue) / span) * (height - pad * 2);
  const zeroY = yFor(0);

  const paths = series.map((item, seriesIndex) => {
    const points = item.values.filter((point) => point.value !== null);
    if (points.length < 2) return '';
    const d = points.map((point, pointIndex) => `${pointIndex === 0 ? 'M' : 'L'} ${xFor(point.index).toFixed(1)} ${yFor(point.value).toFixed(1)}`).join(' ');
    return `<path d="${d}" fill="none" stroke="${chartColors[seriesIndex % chartColors.length]}" stroke-width="2.5" />`;
  }).join('');

  const legend = symbols.map((symbol, index) => {
    const latestPoint = latest?.points?.find((point) => point.symbol === symbol);
    const latestNet = latestPoint ? ` ${numberText(latestPoint.net_pct)}%` : '';
    return `<span><i style="background:${chartColors[index % chartColors.length]}"></i>${escapeHtml(symbol)}${latestNet}</span>`;
  }).join('');

  spreadChartEl.className = 'spread-chart';
  spreadChartEl.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="価格差推移グラフ">
      <line x1="${pad}" y1="${zeroY}" x2="${width - pad}" y2="${zeroY}" stroke="#46505c" stroke-dasharray="4 4" />
      <text x="${pad}" y="20" fill="#a8b0bd">${numberText(maxValue)}%</text>
      <text x="${pad}" y="${height - 8}" fill="#a8b0bd">${numberText(minValue)}%</text>
      ${paths}
    </svg>
    <div class="chart-legend">${legend}</div>
  `;
};

async function refreshCompactSpreadChartOnly() {
  try {
    const response = await fetch('/api/state');
    const state = await response.json();
    renderSpreadChart(state.spread_history || []);
  } catch (_) {
    // Keep the existing chart if a refresh fails.
  }
}

setTimeout(refreshCompactSpreadChartOnly, 800);
setInterval(refreshCompactSpreadChartOnly, 3000);

const runNoticeHtml = `<div id="run-notice" class="run-notice stopped">停止中</div>`;
const topbar = document.querySelector('.topbar');
if (topbar && !document.querySelector('#run-notice')) {
  topbar.insertAdjacentHTML('afterend', runNoticeHtml);
}
const runNoticeEl = document.querySelector('#run-notice');

const previousRenderStateForNotice = renderState;
renderState = function noticeRenderState(state) {
  previousRenderStateForNotice(state);
  if (!runNoticeEl) return;
  if (state.running) {
    runNoticeEl.className = 'run-notice running';
    runNoticeEl.textContent = '実行中: リアルタイム監視しています';
  } else {
    const stoppedText = state.stopped_at ? ` 最終停止: ${new Date(state.stopped_at).toLocaleString()}` : '';
    runNoticeEl.className = 'run-notice stopped';
    runNoticeEl.textContent = `停止中: 価格監視と自動デモ取引は止まっています。${stoppedText}`;
  }
};

const applySettingsButton = document.querySelector('#save-settings-button');
if (applySettingsButton) {
  applySettingsButton.textContent = '設定適用';
}

const exchangeUniverse = ['binance', 'okx', 'bitget'];
const exchangeInput = document.querySelector('input[name="exchanges"]');
const slippageInput = document.querySelector('input[name="slippage_pct"]');
if (slippageInput) {
  slippageInput.step = '0.001';
  slippageInput.min = '0';
}

const exchangePickerHtml = `
  <div class="symbol-picker exchange-picker">
    <div class="picker-head"><span>取引所クイック選択</span></div>
    <div id="exchange-options" class="symbol-options"></div>
  </div>
`;
if (exchangeInput && !document.querySelector('#exchange-options')) {
  exchangeInput.closest('label')?.insertAdjacentHTML('afterend', exchangePickerHtml);
}
const exchangeOptionsEl = document.querySelector('#exchange-options');

function selectedExchanges() {
  return new Set((exchangeInput?.value || '').split(',').map((item) => item.trim().toLowerCase()).filter(Boolean));
}

function renderExchangeOptions() {
  if (!exchangeOptionsEl) return;
  const selected = selectedExchanges();
  exchangeOptionsEl.innerHTML = exchangeUniverse.map((exchange) => `
    <button type="button" class="symbol-chip ${selected.has(exchange) ? 'selected' : ''}" data-exchange="${exchange}">${exchange}</button>
  `).join('');
}

exchangeOptionsEl?.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-exchange]');
  if (!button || !exchangeInput) return;
  const selected = selectedExchanges();
  const exchange = button.dataset.exchange;
  if (selected.has(exchange)) selected.delete(exchange);
  else selected.add(exchange);
  exchangeInput.value = [...selected].join(',');
  renderExchangeOptions();
});

exchangeInput?.addEventListener('input', renderExchangeOptions);
renderExchangeOptions();

const preflightPanelHtml = `
  <section class="main-panel" id="preflight-panel">
    <div class="section-title">
      <h2>本番前チェック</h2>
      <button type="button" id="preflight-button">チェック実行</button>
    </div>
    <div class="table-wrap tall">
      <table>
        <thead>
          <tr>
            <th>取引所</th>
            <th>銘柄</th>
            <th>判定</th>
            <th>手数料</th>
            <th>Fee source</th>
            <th>最小金額</th>
            <th>最小数量</th>
            <th>Quote残高</th>
            <th>Base残高</th>
            <th>詳細</th>
          </tr>
        </thead>
        <tbody id="preflight-results"></tbody>
      </table>
    </div>
  </section>
`;
const balancesPanel = document.querySelector('#balances')?.closest('.split');
if (balancesPanel && !document.querySelector('#preflight-panel')) {
  balancesPanel.insertAdjacentHTML('beforebegin', preflightPanelHtml);
}
const preflightResultsEl = document.querySelector('#preflight-results');
const preflightButton = document.querySelector('#preflight-button');

function renderPreflightResults(results) {
  if (!preflightResultsEl) return;
  preflightResultsEl.innerHTML = (results || []).map((item) => `
    <tr>
      <td>${escapeHtml(item.exchange_id)}</td>
      <td>${escapeHtml(item.symbol)}</td>
      <td><span class="pill ${item.status === 'ok' ? 'ok' : item.status === 'warn' ? 'no_quote' : 'error'}">${escapeHtml(item.status)}</span></td>
      <td>${numberText(item.taker_fee_pct)}%</td>
      <td>${escapeHtml(feeSourceLabel(item.fee_source))}</td>
      <td>${numberText(item.min_cost, 8)}</td>
      <td>${numberText(item.min_amount, 8)}</td>
      <td>${numberText(item.quote_balance, 8)}</td>
      <td>${numberText(item.base_balance, 8)}</td>
      <td class="detail">${escapeHtml(item.message || '')}</td>
    </tr>
  `).join('');
}

const previousRenderStateForPreflight = renderState;
renderState = function preflightRenderState(state) {
  previousRenderStateForPreflight(state);
  renderPreflightResults(state.preflight_results || []);
};

preflightButton?.addEventListener('click', async () => {
  const state = await postJson('/api/preflight', {
    exchanges: settingsFromForm().exchanges,
    symbols: settingsFromForm().symbols,
    quote_amount: settingsFromForm().trade_size_quote || 25,
  });
  if (state) renderState(state);
});
