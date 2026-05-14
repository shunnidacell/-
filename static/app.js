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
const futuresDemoBalanceEl = document.querySelector("#futures-demo-balance");
const relativeDemoBalanceEl = document.querySelector("#relative-demo-balance");
const demoCashEl = document.querySelector("#demo-cash");
const lastTickEl = document.querySelector("#last-tick");
const runNoticeEl = document.querySelector("#run-notice");
const futuresRankingEl = document.querySelector("#futures-spread-research");
const positionsEl = document.querySelector("#futures-positions");
const historyRefreshButton = document.querySelector("#history-refresh-button");
const historyAppLogEl = document.querySelector("#history-app-log");
const historyTradesEl = document.querySelector("#history-trades");
const historyFuturesPaperEl = document.querySelector("#history-futures-paper");
const demoArbHistoryButton = document.querySelector("#demo-arb-history-button");
const demoArbHistoryModal = document.querySelector("#demo-arb-history-modal");
const demoArbHistoryClose = document.querySelector("#demo-arb-history-close");
const historyFilesEl = document.querySelector("#history-files");
const historicalCandlesButton = document.querySelector("#historical-candles-button");
const historicalCandlesStatusEl = document.querySelector("#historical-candles-status");
const eventReportEl = document.querySelector("#event-report");
const scanDurationEl = document.querySelector("#scan-duration");
const scanRequestsEl = document.querySelector("#scan-requests");
const scanSuccessEl = document.querySelector("#scan-success");
const scanNoQuoteEl = document.querySelector("#scan-noquote");
const scanErrorEl = document.querySelector("#scan-error");
const loadBarFillEl = document.querySelector("#load-bar-fill");
const activeSymbolsEl = document.querySelector("#active-symbols");
const activeSymbolsMetaEl = document.querySelector("#active-symbols-meta");
const relativeSymbolEl = document.querySelector("#relative-symbol");
const relativeShortSymbolsEl = document.querySelector("#relative-short-symbols");
const relativeAmountEl = document.querySelector("#relative-amount");
const relativeOpenManualButton = document.querySelector("#relative-open-manual");
const relativeOpenAutoButton = document.querySelector("#relative-open-auto");
const relativeStrongEl = document.querySelector("#relative-strong");
const relativeWeakEl = document.querySelector("#relative-weak");
const relativePositionsEl = document.querySelector("#relative-positions");
const relativeTradesEl = document.querySelector("#relative-trades");

let settingsApplied = false;
let relativeSelectionTouched = false;
let openChartSymbol = "";
let openChartData = null;

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
  positionsEl.innerHTML = positions.map((position) => {
    const unrealized = Number(position.quote_amount || 0) *
      ((Number(position.entry_spread_pct || 0) - Number(position.last_spread_pct || 0)) / 100);
    return `
    <article class="ranking-row positive-rank">
      <div>
        <strong>${escapeHtml(position.symbol)}</strong>
        <span>${escapeHtml(position.direction || "")}</span>
      </div>
      <div>
        <span>Entry ${numberText(position.entry_spread_pct)}%</span>
        <span>Last ${numberText(position.last_spread_pct)}% / Max ${numberText(position.max_spread_pct)}%</span>
        <span>Add ${position.add_count || 0} / 想定上限 ${numberText(position.max_expected_spread_pct)}%</span>
      </div>
      <div class="rank-net ${unrealized >= 0 ? "positive" : "negative"}">${unrealized >= 0 ? "+" : ""}${moneyText(unrealized)}</div>
    </article>
  `;
  }).join("");
}

function renderCandlestick(candles, meta = {}) {
  const rows = (candles || []).map((candle) => ({
    time: candle.time || "",
    open: Number(candle.open),
    high: Number(candle.high),
    low: Number(candle.low),
    close: Number(candle.close),
  })).filter((candle) => [candle.open, candle.high, candle.low, candle.close].every(Number.isFinite));
  const candlePrices = rows.flatMap((candle) => [candle.open, candle.high, candle.low, candle.close]);
  const minPrice = candlePrices.length ? Math.min(...candlePrices) : 0;
  const maxPrice = candlePrices.length ? Math.max(...candlePrices) : 1;
  const priceRange = maxPrice - minPrice || 1;
  const candleWidth = rows.length ? Math.max(0.22, Math.min(1.4, 82 / rows.length)) : 0.8;
  const candleNodes = rows.map((candle, index) => {
    const x = rows.length <= 1 ? 50 : (index / (rows.length - 1)) * 100;
    const yHigh = 25 - ((candle.high - minPrice) / priceRange) * 22;
    const yLow = 25 - ((candle.low - minPrice) / priceRange) * 22;
    const yOpen = 25 - ((candle.open - minPrice) / priceRange) * 22;
    const yClose = 25 - ((candle.close - minPrice) / priceRange) * 22;
    const top = Math.min(yOpen, yClose);
    const height = Math.max(0.7, Math.abs(yClose - yOpen));
    const klass = candle.close >= candle.open ? "up" : "down";
    return `<g class="candle ${klass}"><line x1="${x.toFixed(2)}" y1="${yHigh.toFixed(2)}" x2="${x.toFixed(2)}" y2="${yLow.toFixed(2)}"></line><rect x="${(x - candleWidth / 2).toFixed(2)}" y="${top.toFixed(2)}" width="${candleWidth.toFixed(2)}" height="${height.toFixed(2)}"></rect></g>`;
  }).join("");
  const leftTime = rows[0]?.time || "-";
  const midTime = rows.length ? rows[Math.floor((rows.length - 1) / 2)].time : "-";
  const rightTime = rows.length ? rows[rows.length - 1].time : "-";
  const chartLabel = rows.length
    ? `${numberText(meta.days, 0) || "?"}d / ${escapeHtml(meta.timeframe || "?")} / ${escapeHtml(meta.exchange || "")}`
    : "No candles";
  return `
    <div class="mini-volume-chart loaded-chart">
      <span>${chartLabel}</span>
      <svg class="candlestick-chart" viewBox="0 0 100 28" preserveAspectRatio="none" aria-label="candlestick chart">
        ${candleNodes}
      </svg>
      <div class="chart-axis">
        <b>${leftTime}</b>
        <b>High ${numberText(maxPrice, 4)}</b>
        <b>${midTime}</b>
        <b>${rightTime}</b>
      </div>
    </div>
  `;
}

function renderRelativeList(target, rows, emptyText) {
  if (!target) return;
  const items = (rows || []).slice(0, 6);
  if (!items.length) {
    target.className = "ranking-list empty";
    target.textContent = emptyText;
    return;
  }
  target.className = "ranking-list";
  target.innerHTML = items.map((item) => {
    const pctBadge = (label, value) => {
      const number = Number(value || 0);
      return `<span class="pct-badge ${number >= 0 ? "positive" : "negative"}">${label} ${number >= 0 ? "+" : ""}${numberText(number, 2)}%</span>`;
    };
    const stateBadge = item.long_candidate ? '<span class="signal-badge long">LONG</span>'
      : item.short_candidate ? '<span class="signal-badge short">SHORT</span>'
      : item.eligible ? '<span class="signal-badge neutral">WATCH</span>'
      : '<span class="signal-badge blocked">BLOCK</span>';
    const exclusions = (item.exclude_reasons || []).length ? item.exclude_reasons.join(', ') : 'none';
    const chartOpen = openChartSymbol === item.symbol;
    const chartHtml = chartOpen && openChartData
      ? renderCandlestick(openChartData.candles || [], openChartData)
      : chartOpen ? '<div class="chart-placeholder">Loading chart...</div>' : '';
    return `
      <article class="ranking-row relative-row compact-relative-row">
        <div>
          <strong>${escapeHtml(item.symbol)} ${stateBadge}</strong>
          <div class="pct-strip">
            ${pctBadge("15m", item.return_15m_pct)}
            ${pctBadge("1h", item.return_1h_pct)}
            ${pctBadge("4h", item.return_4h_pct)}
            ${pctBadge("24h", item.return_24h_pct)}
          </div>
          <span>Vol 15m ${numberText(item.volume_change_15m_pct, 1)}% / 1h ${numberText(item.volume_change_1h_pct, 1)}% / 4h ${numberText(item.volume_change_4h_pct, 1)}%</span>
          <span>OI 1h ${numberText(item.oi_change_1h_pct, 1)}% / 4h ${numberText(item.oi_change_4h_pct, 1)}% / Funding ${numberText(item.funding_rate, 4)}%</span>
          <span>VWAP ${numberText(item.vwap_position_pct, 3)}% / EMA20 ${numberText(item.ema20_position_pct, 3)}% / RSI ${numberText(item.rsi, 1)} / ATR ${numberText(item.atr_pct, 3)}%</span>
          <span>Spread ${numberText(item.spread_pct, 4)}% / Depth ${moneyText(item.liquidity_quote)} / Excl ${escapeHtml(exclusions)}</span>
          ${chartHtml}
        </div>
        <div class="chart-action-cell">
          <button type="button" class="mini-button relative-chart-button" data-symbol="${escapeHtml(item.symbol)}">${chartOpen ? "Hide" : "Chart"}</button>
        </div>
        <div class="rank-net ${Number(item.relative_score) >= 0 ? "positive" : "negative"}">${numberText(item.relative_score, 2)}pt</div>
      </article>
    `;
  }).join("");
}

function renderRelativePositions(positions) {
  if (!relativePositionsEl) return;
  if (!positions || !positions.length) {
    relativePositionsEl.className = "ranking-list empty";
    relativePositionsEl.textContent = "現在の建玉はありません";
    return;
  }
  relativePositionsEl.className = "ranking-list";
  relativePositionsEl.innerHTML = positions.map((position) => {
    const pnl = Number(position.unrealized_profit || 0);
    return `
      <article class="ranking-row">
        <div>
          <strong>Long ${escapeHtml(position.long_symbol)}</strong>
          <span>Short ${escapeHtml((position.short_symbols || []).join(", "))}</span>
        </div>
        <div>
          <span>Relative ${numberText(position.last_relative_pct)}% ${position.relative_basis === "vol_adjusted" ? "vol調整" : ""}</span>
          <span>Long ${numberText(position.long_return_pct)}% / Short ${numberText(position.short_return_pct)}%</span>
          <span>${moneyText(position.quote_amount)} USDT / ${escapeHtml(position.mode || "manual")}</span>
        </div>
        <div class="rank-net ${pnl >= 0 ? "positive" : "negative"}">${pnl >= 0 ? "+" : ""}${moneyText(pnl)}</div>
        <button type="button" class="mini-button relative-close" data-position-id="${escapeHtml(position.id)}">決済</button>
      </article>
    `;
  }).join("");
}

async function toggleRelativeChart(symbol) {
  if (openChartSymbol === symbol) {
    openChartSymbol = "";
    openChartData = null;
    await loadState();
    return;
  }
  openChartSymbol = symbol;
  openChartData = null;
  renderRelativeList(relativeStrongEl, window.lastRelativeStrong || [], "履歴が貯まると表示します");
  renderRelativeList(relativeWeakEl, window.lastRelativeWeak || [], "履歴が貯まると表示します");
  const response = await fetch(`/api/relative/chart/${encodeURIComponent(symbol)}`);
  openChartData = await response.json();
  renderRelativeList(relativeStrongEl, window.lastRelativeStrong || [], "履歴が貯まると表示します");
  renderRelativeList(relativeWeakEl, window.lastRelativeWeak || [], "履歴が貯まると表示します");
}

function renderRelativeTrades(trades) {
  if (!relativeTradesEl) return;
  relativeTradesEl.innerHTML = (trades || []).map((trade) => {
    const pnl = Number(trade.profit_quote || 0);
    return `
      <tr>
        <td>${trade.timestamp ? new Date(trade.timestamp).toLocaleTimeString() : "-"}</td>
        <td>${escapeHtml(trade.long_symbol || "-")}</td>
        <td>${escapeHtml((trade.short_symbols || []).join(", "))}</td>
        <td>${escapeHtml(trade.status || "-")}</td>
        <td>${numberText(trade.relative_pct)}%</td>
        <td class="${pnl >= 0 ? "positive" : "negative"}">${pnl >= 0 ? "+" : ""}${moneyText(pnl)}</td>
      </tr>
    `;
  }).join("");
  if (!relativeTradesEl.innerHTML) {
    relativeTradesEl.innerHTML = `<tr><td colspan="6" class="empty-cell">まだ結果はありません</td></tr>`;
  }
}

function renderActiveSymbols(state, perf) {
  if (!activeSymbolsEl) return;
  const symbols = state.futures_active_symbols || [];
  const baseCount = state.futures_base_symbols?.length || perf.base_symbol_count || symbols.length;
  const activeCount = perf.active_symbol_count || symbols.length;
  const boostSymbols = new Set(state.futures_boost_symbols || []);
  const movementSymbols = state.futures_movement_symbols || {};
  if (activeSymbolsMetaEl) {
    activeSymbolsMetaEl.textContent = `${activeCount}銘柄 / 候補${baseCount}銘柄`;
  }
  if (!symbols.length) {
    activeSymbolsEl.className = "symbol-chip-list empty";
    activeSymbolsEl.textContent = "起動後に表示します";
    return;
  }
  activeSymbolsEl.className = "symbol-chip-list";
  if (activeSymbolsMetaEl) {
    activeSymbolsMetaEl.textContent = `${activeCount}銘柄 / 候補${baseCount}銘柄 / 高速${boostSymbols.size}銘柄`;
  }
  activeSymbolsEl.innerHTML = symbols.map((symbol) => {
    const movement = Number(movementSymbols[symbol] || 0);
    const label = movement > 0 ? `${symbol} ${numberText(movement, 2)}%` : symbol;
    return `<span class="symbol-chip ${boostSymbols.has(symbol) ? "boost" : ""}">${escapeHtml(label)}</span>`;
  }).join("");
}

function optionHtml(symbol, label = symbol) {
  return `<option value="${escapeHtml(symbol)}">${escapeHtml(label)}</option>`;
}

function selectedOptions(selectEl) {
  if (!selectEl) return [];
  return selectEl.value ? [selectEl.value] : [];
}

function renderRelativeSelectors(state) {
  if (!relativeSymbolEl || !relativeShortSymbolsEl || relativeSelectionTouched) return;
  const strong = state.relative_rankings?.strong || [];
  const weak = state.relative_rankings?.weak || [];
  const active = state.futures_active_symbols || [];
  const strongSymbols = [...new Set([...strong.map((item) => item.symbol), ...active])].filter(Boolean);
  const weakSymbols = [...new Set([...weak.map((item) => item.symbol), ...active])].filter(Boolean);
  if (strongSymbols.length) {
    relativeSymbolEl.innerHTML = strongSymbols.map((symbol) => {
      const row = strong.find((item) => item.symbol === symbol);
      const label = row ? `${symbol}  9時 ${numberText(row.return_since_9jst_pct)}%` : symbol;
      return optionHtml(symbol, label);
    }).join("");
  }
  if (weakSymbols.length) {
    relativeShortSymbolsEl.innerHTML = weakSymbols.map((symbol, index) => {
      const row = weak.find((item) => item.symbol === symbol);
      const label = row ? `${symbol}  9時 ${numberText(row.return_since_9jst_pct)}%` : symbol;
      return `<option value="${escapeHtml(symbol)}" ${index === 0 ? "selected" : ""}>${escapeHtml(label)}</option>`;
    }).join("");
  }
}

function renderState(state) {
  const marketStatuses = state.market_statuses || [];
  const latestFutures = (state.futures_spread_history || []).at(-1);
  const points = latestFutures?.points || [];
  const portfolio = state.portfolio || {};
  const futuresPnl = state.futures_paper_pnl || {};
  const futuresAccount = state.futures_paper_account || {};
  const relativeAccount = state.relative_paper_account || {};
  const perf = state.futures_perf || {};

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
  const totalPnl = Number(futuresPnl.total ?? portfolio.realized_profit ?? 0);
  paperProfitEl.textContent = moneyText(totalPnl);
  paperProfitEl.classList.toggle("positive", totalPnl >= 0);
  if (futuresDemoBalanceEl) {
    const equity = Number(futuresAccount.equity ?? 10000);
    futuresDemoBalanceEl.textContent = `${moneyText(equity)} USDT`;
    futuresDemoBalanceEl.classList.toggle("positive", Number(futuresAccount.total ?? futuresPnl.total ?? 0) >= 0);
  }
  if (relativeDemoBalanceEl) {
    const equity = Number(relativeAccount.equity ?? 10000);
    const total = Number(state.relative_pnl?.total ?? 0);
    relativeDemoBalanceEl.textContent = `${moneyText(equity)} USDT`;
    relativeDemoBalanceEl.classList.toggle("positive", total >= 0);
    relativeDemoBalanceEl.classList.toggle("negative", total < 0);
  }
  demoCashEl.textContent = "Paper";
  lastTickEl.textContent = state.last_tick ? new Date(state.last_tick).toLocaleString() : "未取得";

  if (scanDurationEl) {
    scanDurationEl.textContent = perf.last_scan_seconds
      ? `${numberText(perf.last_scan_seconds, 2)}秒 / 目標 ${numberText(perf.poll_seconds, 0)}秒`
      : "-";
    scanRequestsEl.textContent = perf.request_count ?? "-";
    scanSuccessEl.textContent = perf.ok_count ?? "-";
    scanNoQuoteEl.textContent = perf.no_quote_count ?? "-";
    scanErrorEl.textContent = perf.error_count ?? "-";
    const loadPct = Number(perf.load_pct || 0);
    loadBarFillEl.style.width = `${Math.max(3, Math.min(100, loadPct))}%`;
    loadBarFillEl.className = loadPct >= 100 ? "bad" : loadPct >= 70 ? "warn" : "good";
  }

  renderActiveSymbols(state, perf);
  renderRelativeSelectors(state);

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
  renderRelativeList(relativeStrongEl, state.relative_rankings?.strong || [], "履歴が貯まると表示します");
  renderRelativeList(relativeWeakEl, state.relative_rankings?.weak || [], "履歴が貯まると表示します");
  renderRelativePositions(state.relative_positions || []);

  renderRelativeTrades(state.relative_closed_trades || []);
  const candleStatus = state.historical_candle_status || {};
  if (historicalCandlesStatusEl && candleStatus.status) {
    historicalCandlesStatusEl.textContent = candleStatus.status === "running"
      ? `読込中 ${candleStatus.markets || ""}市場`
      : `保存済み ${candleStatus.candle_count || 0}本 / ${candleStatus.ok_count || 0}市場`;
  }

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
  if (historyFuturesPaperEl) {
    historyFuturesPaperEl.innerHTML = (history.futures_paper_demo || []).map((event) => `
      <tr>
        <td>${event.timestamp ? new Date(event.timestamp).toLocaleString() : "-"}</td>
        <td>${escapeHtml(event.action || "-")}</td>
        <td>${escapeHtml(event.symbol || "-")}</td>
        <td>${escapeHtml(event.direction || "-")}</td>
        <td>${moneyText(event.quote_amount)}</td>
        <td>${numberText(event.net_spread_pct)}%</td>
        <td>${numberText(event.round_trip_cost_pct)}%</td>
        <td class="${Number(event.profit_quote || event.unrealized_profit || 0) >= 0 ? "positive" : "negative"}">${Number(event.profit_quote || event.unrealized_profit || 0) >= 0 ? "+" : ""}${moneyText(event.profit_quote || event.unrealized_profit || 0)}</td>
      </tr>
    `).join("");
    if (!historyFuturesPaperEl.innerHTML) {
      historyFuturesPaperEl.innerHTML = `<tr><td colspan="8" class="empty-cell">増やしたデモ取引の記録はまだありません</td></tr>`;
    }
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
    保存先: ${escapeHtml(files.app_log || "")} / ${escapeHtml(files.trades || "")} / ${escapeHtml(files.futures_paper_demo || "")} / ${escapeHtml(files.futures_spread_history || "")}
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

relativeOpenManualButton?.addEventListener("click", async () => {
  const state = await postJson("/api/relative/open", {
    symbol: relativeSymbolEl.value,
    short_symbols: selectedOptions(relativeShortSymbolsEl),
    mode: "manual",
    quote_amount: Number(relativeAmountEl.value || 10),
  });
  if (state) renderState(state);
});

relativeOpenAutoButton?.addEventListener("click", async () => {
  const state = await postJson("/api/relative/open", {
    symbol: "",
    short_symbols: [],
    mode: "auto",
    quote_amount: Number(relativeAmountEl.value || 10),
  });
  if (state) renderState(state);
});

relativeSymbolEl?.addEventListener("change", () => {
  relativeSelectionTouched = true;
});

relativeShortSymbolsEl?.addEventListener("change", () => {
  relativeSelectionTouched = true;
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest(".relative-chart-button");
  if (!button) return;
  await toggleRelativeChart(button.dataset.symbol || "");
});

relativePositionsEl?.addEventListener("click", async (event) => {
  const button = event.target.closest(".relative-close");
  if (!button) return;
  const id = encodeURIComponent(button.dataset.positionId || "");
  const response = await fetch(`/api/relative/close/${id}`, { method: "POST" });
  renderState(await response.json());
});

historyRefreshButton.addEventListener("click", loadHistory);

demoArbHistoryButton?.addEventListener("click", async () => {
  demoArbHistoryModal?.classList.remove("hidden");
  await loadHistory();
});

demoArbHistoryClose?.addEventListener("click", () => {
  demoArbHistoryModal?.classList.add("hidden");
});

demoArbHistoryModal?.addEventListener("click", (event) => {
  if (event.target === demoArbHistoryModal) {
    demoArbHistoryModal.classList.add("hidden");
  }
});

historicalCandlesButton?.addEventListener("click", async () => {
  historicalCandlesButton.disabled = true;
  historicalCandlesStatusEl.textContent = "過去チャート読込中...";
  const settings = settingsFromForm();
  const response = await fetch("/api/historical-candles/backfill", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      symbols: settings.symbols,
      exchanges: settings.futures_exchanges,
      timeframe: "4h",
      days: 30,
      limit_per_market: 200,
    }),
  });
  const payload = await response.json();
  historicalCandlesButton.disabled = false;
  if (!response.ok) {
    historicalCandlesStatusEl.textContent = payload.detail || "読込失敗";
    return;
  }
  historicalCandlesStatusEl.textContent = `保存済み ${payload.candle_count || 0}本 / ${payload.ok_count || 0}市場`;
  await loadHistory();
});

loadState();
loadHistory();
setInterval(loadState, 5000);
