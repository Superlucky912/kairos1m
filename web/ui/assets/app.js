const state = {
  reports: [],
  selectedReport: null,
  selectedTrade: null,
  trades: [],
  diagnosticEntries: [],
  labelCharts: [],
  selectedLabelSymbol: null,
  labelWindowStart: 0,
  labelTargetTp: 0,
  labelStopLoss: 0,
  labelHorizonMinutes: 0,
  tradeReplay: null,
  tradeWindowStart: 0,
};

const statusBox = document.getElementById("statusBox");
const reportList = document.getElementById("reportList");
const reportTitle = document.getElementById("reportTitle");
const reportContent = document.getElementById("reportContent");
const summaryGrid = document.getElementById("summaryGrid");
const tradeList = document.getElementById("tradeList");
const chartTitle = document.getElementById("chartTitle");
const chartMeta = document.getElementById("chartMeta");
const candleChart = document.getElementById("candleChart");
const tradeCandleTooltip = document.getElementById("tradeCandleTooltip");
const tradeWindowSlider = document.getElementById("tradeWindowSlider");
const tradeWindowText = document.getElementById("tradeWindowText");
const runButton = document.getElementById("runButton");
const refreshButton = document.getElementById("refreshButton");
const loadLabelMapButton = document.getElementById("loadLabelMapButton");
const labelMapMeta = document.getElementById("labelMapMeta");
const labelMapGrid = document.getElementById("labelMapGrid");
const modelInput = document.getElementById("modelInput");
const thresholdInput = document.getElementById("thresholdInput");
const candidateInput = document.getElementById("candidateInput");
const limitInput = document.getElementById("limitInput");
const LABEL_WINDOW_SIZE = 120;
const TRADE_WINDOW_SIZE = 120;

function setStatus(message) {
  statusBox.textContent = message;
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  return response.json();
}

function renderReports() {
  reportList.innerHTML = "";
  if (!state.reports.length) {
    reportList.textContent = "저장된 리포트가 없습니다.";
    return;
  }

  for (const report of state.reports) {
    const button = document.createElement("button");
    button.className = `report-item ${state.selectedReport === report.name ? "active" : ""}`;
    button.type = "button";
    button.innerHTML = `<strong>${report.name}</strong><br><small>${new Date(report.modified_at * 1000).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })} (KST)</small>`;
    button.addEventListener("click", () => loadReport(report.name));
    reportList.appendChild(button);
  }
}

function renderSummary(summary) {
  summaryGrid.innerHTML = "";
  const selected = summary?.ON || summary?.OFF;
  if (!selected) {
    return;
  }

  const metrics = [
    ["Entries", selected.entries],
    ["TP Count", selected.tp],
    ["SL Count", selected.sl],
    ["Timeout", selected.timeout],
    ["TP Rate", `${selected.tp_rate.toFixed(2)}%`],
    ["Final", selected.final_balance.toFixed(2)],
    ["Return", `${selected.return_pct.toFixed(2)}%`],
    ["MDD", `${selected.mdd.toFixed(2)}%`],
  ];

  for (const [label, value] of metrics) {
    const item = document.createElement("div");
    item.className = "metric";
    item.innerHTML = `<b>${label}</b><span>${value}</span>`;
    summaryGrid.appendChild(item);
  }
}

async function loadReports() {
  state.reports = await fetchJson("/api/scalping-1m/reports");
  renderReports();
  if (state.reports.length && !state.selectedReport) {
    await loadReport(state.reports[0].name);
  }
}

async function loadScalpingDefaults() {
  const defaults = await fetchJson("/api/scalping-1m/defaults");
  if (!modelInput.value && defaults.model) {
    modelInput.value = defaults.model;
  }
  if (!thresholdInput.value && defaults.threshold !== null && defaults.threshold !== undefined) {
    thresholdInput.value = String(defaults.threshold);
  }
  if (!candidateInput.value && defaults.candidate_top_n !== null && defaults.candidate_top_n !== undefined) {
    candidateInput.value = String(defaults.candidate_top_n);
  }
}

async function loadReport(name) {
  const report = await fetchJson(`/api/scalping-1m/reports/${encodeURIComponent(name)}`);
  state.selectedReport = report.name;
  state.selectedTrade = null;
  state.tradeReplay = null;
  state.tradeWindowStart = 0;
  state.trades = report.trades || [];
  state.diagnosticEntries = report.diagnostic_entries || [];
  reportTitle.textContent = report.name;
  reportContent.textContent = "거래를 선택하면 진입 당시 피처와 판단 근거가 표시됩니다.";
  renderSummary(report.summary);
  renderTrades();
  renderReports();
  if (state.trades.length) {
    await loadTradeReplay(state.trades[0].trade_index);
  } else {
    clearChart("최근 거래를 선택하세요.");
  }
}

function renderTrades() {
  tradeList.innerHTML = "";
  if (!state.trades.length) {
    tradeList.textContent = "리포트에 표시된 최근 거래가 없습니다.";
    return;
  }

  for (const trade of state.trades) {
    const button = document.createElement("button");
    const reason = String(trade.reason || "").toLowerCase();
    button.className = `trade-item ${reason} ${state.selectedTrade === trade.trade_index ? "active" : ""}`;
    button.type = "button";
    button.innerHTML = `<strong>${trade.symbol}</strong> ${trade.reason}<br><small>${trade.entry_time_kst} → ${trade.exit_time_kst} (KST)</small>`;
    button.addEventListener("click", () => loadTradeReplay(trade.trade_index));
    tradeList.appendChild(button);
  }
}

async function loadTradeReplay(tradeIndex) {
  if (!state.selectedReport) {
    return;
  }
  setStatus("캔들 데이터 조회 중...");
  const replay = await fetchJson(
    `/api/scalping-1m/reports/${encodeURIComponent(state.selectedReport)}/trades/${tradeIndex}/replay?padding_minutes=180`,
  );
  state.selectedTrade = tradeIndex;
  state.tradeReplay = replay;
  state.tradeWindowStart = initialTradeWindowStart(replay);
  renderTrades();
  renderCandleChart(replay);
  renderTradeDetails(replay.trade);
  setStatus("차트 로드 완료");
}

function renderTradeDetails(trade) {
  const lines = [];
  lines.push(`[거래 요약]`);
  lines.push(`심볼: ${trade.symbol}`);
  lines.push(`결과: ${trade.reason}`);
  lines.push(`진입(KST): ${trade.entry_time_kst} @ ${formatValue(trade.entry_price)}`);
  lines.push(`청산(KST): ${trade.exit_time_kst} @ ${formatValue(trade.exit_price)}`);
  lines.push(`PnL ROE: ${formatValue(trade.pnl_roe)}`);
  lines.push(`Balance: ${formatValue(trade.balance)}`);
  lines.push(`Drawdown: ${formatValue(trade.drawdown)}`);
  lines.push("");
  lines.push(`[모델 판단]`);
  lines.push(`p_tp: ${formatValue(trade.p_tp ?? trade.pred_proba)}`);
  lines.push(`p_sl: ${formatValue(trade.p_sl)}`);
  lines.push(`p_timeout: ${formatValue(trade.p_timeout)}`);
  lines.push(`entry_score: ${formatValue(trade.entry_score)}`);
  lines.push(`entry_rank_score: ${formatValue(trade.entry_rank_score)}`);
  lines.push(`rank_at_entry: ${formatValue(trade.rank_at_entry)}`);
  lines.push("");
  lines.push(`[진입 시점 전체 피처]`);

  const features = normalizeEntryFeatures(trade.entry_features);
  const featureEntries = Object.entries(features)
    .filter(([key]) => !["timestamp", "symbol"].includes(key))
    .sort(([left], [right]) => left.localeCompare(right));

  if (!featureEntries.length) {
    lines.push("이 리포트에는 피처 스냅샷이 없습니다. 새 백테스트를 실행하면 전체 피처가 저장됩니다.");
  } else {
    for (const [key, value] of featureEntries) {
      lines.push(`${key}: ${formatValue(value)}`);
    }
  }

  reportContent.textContent = lines.join("\n");
}

function normalizeEntryFeatures(value) {
  if (!value) {
    return {};
  }
  if (typeof value === "object" && !Array.isArray(value)) {
    return value;
  }
  if (typeof value !== "string") {
    return { raw: value };
  }

  const trimmed = value.trim();
  if (!trimmed) {
    return {};
  }
  try {
    const parsed = JSON.parse(trimmed);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed;
    }
  } catch (_) {
    // 구형 리포트는 Python dict 문자열로 저장돼 API에서 복구되는 것이 정상입니다.
  }
  return { raw: trimmed };
}

function formatValue(value) {
  if (value === undefined || value === null || value === "") {
    return "-";
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      return "0";
    }
    if (Math.abs(value) >= 1000) {
      return value.toFixed(2);
    }
    if (Math.abs(value) >= 1) {
      return value.toFixed(6);
    }
    return value.toPrecision(6);
  }
  return String(value);
}

function clearChart(message) {
  const context = candleChart.getContext("2d");
  const { width, height } = resizeCanvas();
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#64748b";
  context.font = "15px Segoe UI";
  context.fillText(message, 24, 40);
  chartTitle.textContent = "거래 차트";
  chartMeta.textContent = message;
  if (tradeWindowSlider) {
    tradeWindowSlider.max = "0";
    tradeWindowSlider.value = "0";
    tradeWindowSlider.disabled = true;
  }
  if (tradeWindowText) {
    tradeWindowText.textContent = "0 / 0";
  }
  if (tradeCandleTooltip) {
    tradeCandleTooltip.hidden = true;
  }
  candleChart.onmousemove = null;
  candleChart.onmouseleave = null;
}

function resizeCanvas() {
  const ratio = window.devicePixelRatio || 1;
  const rect = candleChart.getBoundingClientRect();
  const fallbackWidth = candleChart.parentElement?.clientWidth || 900;
  const cssWidth = rect.width || fallbackWidth;
  candleChart.width = Math.max(600, Math.floor(cssWidth * ratio));
  candleChart.height = Math.floor(520 * ratio);
  const context = candleChart.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { width: Math.max(600, cssWidth), height: 520 };
}

function renderCandleChart(replay) {
  const candles = replay.candles || [];
  const trade = replay.trade;
  const context = candleChart.getContext("2d");
  const { width, height } = resizeCanvas();
  context.clearRect(0, 0, width, height);

  chartTitle.textContent = `${trade.symbol} ${trade.reason}`;
  chartMeta.textContent = `${trade.entry_time_kst} → ${trade.exit_time_kst} (KST)`;

  if (!candles.length) {
    clearChart("조회된 1분봉 캔들이 없습니다.");
    return;
  }

  const maxStart = Math.max(0, candles.length - TRADE_WINDOW_SIZE);
  state.tradeWindowStart = Math.min(Math.max(0, state.tradeWindowStart), maxStart);
  if (tradeWindowSlider) {
    tradeWindowSlider.max = String(maxStart);
    tradeWindowSlider.value = String(state.tradeWindowStart);
    tradeWindowSlider.disabled = maxStart === 0;
  }
  updateLabelWindowText(tradeWindowText, candles, state.tradeWindowStart, TRADE_WINDOW_SIZE);

  const visibleCandles = candles.slice(state.tradeWindowStart, state.tradeWindowStart + TRADE_WINDOW_SIZE);
  const padding = { left: 58, right: 18, top: 18, bottom: 38 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const highs = visibleCandles.map((item) => item.high);
  const lows = visibleCandles.map((item) => item.low);
  const visibleTimeKeys = new Set(visibleCandles.map((item) => minuteKey(item.timestamp)));
  const markerPrices = [];
  if (visibleTimeKeys.has(minuteKey(trade.entry_time_kst))) {
    markerPrices.push(Number(trade.entry_price || 0));
  }
  if (visibleTimeKeys.has(minuteKey(trade.exit_time_kst))) {
    markerPrices.push(Number(trade.exit_price || 0));
  }
  const maxPrice = Math.max(...highs, ...markerPrices);
  const minPrice = Math.min(...lows, ...markerPrices);
  const pricePad = (maxPrice - minPrice) * 0.08 || maxPrice * 0.001;
  const topPrice = maxPrice + pricePad;
  const bottomPrice = minPrice - pricePad;
  const candleStep = plotWidth / visibleCandles.length;
  const candleWidth = Math.max(2, Math.min(7, candleStep * 0.65));

  const xForIndex = (index) => padding.left + index * candleStep + candleStep / 2;
  const yForPrice = (price) => padding.top + ((topPrice - price) / (topPrice - bottomPrice)) * plotHeight;

  context.strokeStyle = "#e2e8f0";
  context.lineWidth = 1;
  context.fillStyle = "#64748b";
  context.font = "12px Segoe UI";
  for (let grid = 0; grid <= 4; grid += 1) {
    const y = padding.top + (plotHeight / 4) * grid;
    const price = topPrice - ((topPrice - bottomPrice) / 4) * grid;
    context.beginPath();
    context.moveTo(padding.left, y);
    context.lineTo(width - padding.right, y);
    context.stroke();
    context.fillText(price.toFixed(6), 8, y + 4);
  }

  visibleCandles.forEach((candle, index) => {
    const x = xForIndex(index);
    const openY = yForPrice(candle.open);
    const closeY = yForPrice(candle.close);
    const highY = yForPrice(candle.high);
    const lowY = yForPrice(candle.low);
    const up = candle.close >= candle.open;
    context.strokeStyle = up ? "#16a34a" : "#dc2626";
    context.fillStyle = context.strokeStyle;
    context.beginPath();
    context.moveTo(x, highY);
    context.lineTo(x, lowY);
    context.stroke();
    context.fillRect(x - candleWidth / 2, Math.min(openY, closeY), candleWidth, Math.max(1, Math.abs(closeY - openY)));
  });

  drawTradeMarker(context, visibleCandles, trade, "entry", xForIndex, yForPrice, "#2563eb");
  drawTradeMarker(context, visibleCandles, trade, "exit", xForIndex, yForPrice, trade.reason === "TP" ? "#16a34a" : "#dc2626");
  attachTradeCandleHover(visibleCandles, trade, state.tradeWindowStart);
}

function drawTradeMarker(context, candles, trade, type, xForIndex, yForPrice, color) {
  const timeKey = type === "entry" ? "entry_time_kst" : "exit_time_kst";
  const priceKey = type === "entry" ? "entry_price" : "exit_price";
  const targetMs = Date.parse(trade[timeKey]);
  let nearestIndex = 0;
  let nearestDiff = Infinity;
  candles.forEach((candle, index) => {
    const diff = Math.abs(Date.parse(candle.timestamp) - targetMs);
    if (diff < nearestDiff) {
      nearestDiff = diff;
      nearestIndex = index;
    }
  });
  if (nearestDiff > 60_000) {
    return;
  }

  const x = xForIndex(nearestIndex);
  const y = yForPrice(Number(trade[priceKey]));
  context.strokeStyle = color;
  context.fillStyle = color;
  context.lineWidth = 2;
  context.beginPath();
  context.moveTo(58, y);
  context.lineTo(candleChart.getBoundingClientRect().width - 18, y);
  context.stroke();
  context.beginPath();
  context.arc(x, y, 5, 0, Math.PI * 2);
  context.fill();
  context.font = "12px Segoe UI";
  context.fillText(type === "entry" ? "ENTRY" : `EXIT ${trade.reason}`, x + 8, y - 8);
}

function initialTradeWindowStart(replay) {
  const candles = replay.candles || [];
  if (!candles.length) {
    return 0;
  }
  const maxStart = Math.max(0, candles.length - TRADE_WINDOW_SIZE);
  const entryMs = Date.parse(replay.trade?.entry_time_kst);
  if (Number.isNaN(entryMs)) {
    return Math.max(0, Math.min(maxStart, candles.length - TRADE_WINDOW_SIZE));
  }
  let nearestIndex = 0;
  let nearestDiff = Infinity;
  candles.forEach((candle, index) => {
    const diff = Math.abs(Date.parse(candle.timestamp) - entryMs);
    if (diff < nearestDiff) {
      nearestDiff = diff;
      nearestIndex = index;
    }
  });
  return Math.min(Math.max(0, nearestIndex - Math.floor(TRADE_WINDOW_SIZE / 2)), maxStart);
}

function attachTradeCandleHover(visibleCandles, trade, windowStart) {
  if (!tradeCandleTooltip) {
    return;
  }
  tradeCandleTooltip.hidden = true;
  candleChart.onmousemove = (event) => {
    if (!visibleCandles.length) {
      tradeCandleTooltip.hidden = true;
      return;
    }
    const rect = candleChart.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const paddingLeft = 58;
    const paddingRight = 18;
    const plotWidth = rect.width - paddingLeft - paddingRight;
    const candleStep = plotWidth / visibleCandles.length;
    const rawIndex = Math.floor((x - paddingLeft) / candleStep);
    const index = Math.min(Math.max(rawIndex, 0), visibleCandles.length - 1);
    const candle = visibleCandles[index];
    if (!candle || x < paddingLeft || x > rect.width - paddingRight) {
      tradeCandleTooltip.hidden = true;
      return;
    }

    tradeCandleTooltip.innerHTML = buildTradeCandleTooltipHtml(trade, candle, windowStart + index);
    tradeCandleTooltip.hidden = false;
    const tooltipWidth = tradeCandleTooltip.offsetWidth || 320;
    const tooltipHeight = tradeCandleTooltip.offsetHeight || 180;
    const left = Math.min(Math.max(event.clientX - rect.left + 14, 8), rect.width - tooltipWidth - 8);
    const top = Math.min(Math.max(event.clientY - rect.top + 14, 8), rect.height - tooltipHeight - 8);
    tradeCandleTooltip.style.left = `${left}px`;
    tradeCandleTooltip.style.top = `${top}px`;
  };
  candleChart.onmouseleave = () => {
    tradeCandleTooltip.hidden = true;
  };
}

function buildTradeCandleTooltipHtml(trade, candle, absoluteIndex) {
  const open = Number(candle.open);
  const high = Number(candle.high);
  const low = Number(candle.low);
  const close = Number(candle.close);
  const range = Math.max(high - low, 0);
  const body = Math.abs(close - open);
  const upperWick = high - Math.max(open, close);
  const lowerWick = Math.min(open, close) - low;
  const changePct = open ? ((close - open) / open) * 100 : 0;
  const rangePct = close ? (range / close) * 100 : 0;
  const bodyRatio = range ? body / range : 0;
  const upperRatio = range ? upperWick / range : 0;
  const lowerRatio = range ? lowerWick / range : 0;
  const isEntry = minuteKey(candle.timestamp) === minuteKey(trade.entry_time_kst);
  const isExit = minuteKey(candle.timestamp) === minuteKey(trade.exit_time_kst);
  const markerText = isEntry ? "ENTRY 캔들" : isExit ? `EXIT ${trade.reason} 캔들` : "일반 캔들";
  const features = normalizeEntryFeatures(trade.entry_features);
  const featureHtml = buildTradeFeatureSummaryHtml(features);

  return `
    <div class="tooltip-title">${trade.symbol} #${absoluteIndex + 1}</div>
    <div>${formatShortTime(candle.timestamp)} (KST)</div>
    <div class="tooltip-label ${isEntry ? "tp" : isExit ? "sl" : "none"}">${markerText}</div>
    ${isEntry ? buildModelEntryTooltipHtml(trade) : ""}
    ${isEntry && featureHtml ? `<div class="tooltip-entry">${featureHtml}</div>` : ""}
    <hr />
    <div>OHLC: ${formatValue(open)} / ${formatValue(high)} / ${formatValue(low)} / ${formatValue(close)}</div>
    <div>캔들 수익률: ${changePct.toFixed(3)}%</div>
    <div>변동폭: ${rangePct.toFixed(3)}%</div>
    <div>몸통/윗꼬리/아랫꼬리: ${bodyRatio.toFixed(2)} / ${upperRatio.toFixed(2)} / ${lowerRatio.toFixed(2)}</div>
    <div>거래량: ${formatValue(Number(candle.volume || 0))}</div>
  `;
}

function buildTradeFeatureSummaryHtml(features) {
  if (!features || !Object.keys(features).length) {
    return "";
  }
  const keys = [
    "change_24h",
    "scalping_rank_score",
    "ret_1m",
    "ret_5m",
    "quote_vol_ratio_5m",
    "range_pct_1m",
    "taker_buy_ratio_3m",
    "pump_base_condition",
    "support_hold_120m",
    "near_support_entry_120m",
    "wedge_support_reclaim_120m",
    "wedge_resistance_breakout_120m",
    "support_breakdown_120m",
    "wedge_support_break_120m",
  ];
  return keys
    .filter((key) => features[key] !== undefined && features[key] !== null)
    .map((key) => `${key}: ${formatValue(features[key])}`)
    .join("<br />");
}

async function loadTop15LabelMap() {
  loadLabelMapButton.disabled = true;
  labelMapMeta.textContent = "Top 15 1분봉 라벨 데이터 조회 중...";
  labelMapGrid.innerHTML = "";
  try {
    const payload = await fetchJson("/api/scalping-1m/top15-label-candles?hours=24");
    renderLabelMap(payload);
    setStatus("Top 15 라벨 맵 로드 완료");
  } catch (error) {
    labelMapMeta.textContent = `라벨 맵 조회 실패: ${error.message}`;
    setStatus(`라벨 맵 조회 실패: ${error.message}`);
  } finally {
    loadLabelMapButton.disabled = false;
  }
}

function renderLabelMap(payload) {
  const charts = payload.charts || [];
  const symbols = payload.symbols || [];
  state.labelCharts = charts;
  state.selectedLabelSymbol = charts[0]?.symbol || null;
  state.labelWindowStart = 0;
  state.labelTargetTp = Number(payload.target_tp || 0);
  state.labelStopLoss = Number(payload.stop_loss || 0);
  state.labelHorizonMinutes = Number(payload.horizon_minutes || 0);
  labelMapMeta.textContent = `${payload.start} → ${payload.end} | symbols=${symbols.length} | TP ${formatPercent(payload.target_tp)} / SL ${formatPercent(payload.stop_loss)} / horizon ${payload.horizon_minutes}분`;
  labelMapGrid.innerHTML = "";
  if (!charts.length) {
    labelMapGrid.textContent = "표시할 라벨 캔들이 없습니다.";
    return;
  }

  const shell = document.createElement("div");
  shell.className = "label-map-viewer";
  shell.innerHTML = `
    <div class="label-symbol-buttons"></div>
      <div class="label-chart-card large">
        <div class="label-chart-title">
          <strong id="largeLabelSymbol">-</strong>
          <span id="largeLabelCounts">-</span>
        </div>
      <div class="label-canvas-wrap">
        <canvas class="label-chart-canvas large" width="1200" height="520"></canvas>
        <div class="label-candle-tooltip" hidden></div>
      </div>
      <div class="label-window-control">
        <input class="label-window-slider" type="range" min="0" max="0" value="0" />
        <span class="label-window-text"></span>
      </div>
    </div>
  `;
  labelMapGrid.appendChild(shell);

  const buttons = shell.querySelector(".label-symbol-buttons");
  for (const chart of charts) {
    const counts = countLabels(chart.candles || []);
    const button = document.createElement("button");
    button.type = "button";
    button.className = `label-symbol-button ${chart.symbol === state.selectedLabelSymbol ? "active" : ""}`;
    button.innerHTML = `<strong>${chart.symbol}</strong><span>TP ${counts.TP} / SL ${counts.SL}</span>`;
    button.addEventListener("click", () => {
      state.selectedLabelSymbol = chart.symbol;
      const candles = chart.candles || [];
      state.labelWindowStart = Math.max(0, candles.length - LABEL_WINDOW_SIZE);
      renderSelectedLabelChart();
    });
    buttons.appendChild(button);
  }

  const slider = shell.querySelector(".label-window-slider");
  slider.addEventListener("input", () => {
    state.labelWindowStart = Number(slider.value || 0);
    renderSelectedLabelChart();
  });
  const selectedChart = charts.find((chart) => chart.symbol === state.selectedLabelSymbol) || charts[0];
  state.labelWindowStart = Math.max(0, (selectedChart.candles || []).length - LABEL_WINDOW_SIZE);
  renderSelectedLabelChart();
}

function renderSelectedLabelChart() {
  const chart = state.labelCharts.find((item) => item.symbol === state.selectedLabelSymbol) || state.labelCharts[0];
  if (!chart) {
    return;
  }

  const shell = labelMapGrid.querySelector(".label-map-viewer");
  const canvas = shell?.querySelector(".label-chart-canvas.large");
  const tooltip = shell?.querySelector(".label-candle-tooltip");
  const slider = shell?.querySelector(".label-window-slider");
  const windowText = shell?.querySelector(".label-window-text");
  const symbolTitle = shell?.querySelector("#largeLabelSymbol");
  const countsText = shell?.querySelector("#largeLabelCounts");
  if (!canvas || !slider || !windowText || !symbolTitle || !countsText || !tooltip) {
    return;
  }

  const candles = chart.candles || [];
  const counts = countLabels(candles);
  const maxStart = Math.max(0, candles.length - LABEL_WINDOW_SIZE);
  state.labelWindowStart = Math.min(Math.max(0, state.labelWindowStart), maxStart);
  slider.max = String(maxStart);
  slider.value = String(state.labelWindowStart);
  slider.disabled = maxStart === 0;
  symbolTitle.textContent = chart.symbol;
  countsText.textContent = `TP ${counts.TP} / SL ${counts.SL} / NONE ${counts.NONE}`;

  for (const button of labelMapGrid.querySelectorAll(".label-symbol-button")) {
    const isActive = button.querySelector("strong")?.textContent === chart.symbol;
    button.classList.toggle("active", Boolean(isActive));
  }

  const modelEntries = getModelEntriesForSymbol(chart.symbol);
  drawLabelCandles(canvas, candles, state.labelWindowStart, LABEL_WINDOW_SIZE, modelEntries);
  attachLabelCandleHover(canvas, tooltip, chart.symbol, candles, state.labelWindowStart, LABEL_WINDOW_SIZE, modelEntries);
  updateLabelWindowText(windowText, candles, state.labelWindowStart, LABEL_WINDOW_SIZE);
}

function countLabels(candles) {
  return candles.reduce(
    (acc, candle) => {
      const label = candle.label || "NONE";
      acc[label] = (acc[label] || 0) + 1;
      return acc;
    },
    { TP: 0, SL: 0, NONE: 0 },
  );
}

function formatPercent(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function updateLabelWindowText(element, candles, start, size) {
  if (!element) {
    return;
  }
  if (!candles.length) {
    element.textContent = "0 / 0";
    return;
  }
  const end = Math.min(candles.length, start + size);
  const startTime = candles[start]?.timestamp || "";
  const endTime = candles[end - 1]?.timestamp || "";
  element.textContent = `${start + 1}-${end} / ${candles.length} | ${formatShortTime(startTime)} → ${formatShortTime(endTime)}`;
}

function formatShortTime(value) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function drawLabelCandles(canvas, candles, start = 0, size = LABEL_WINDOW_SIZE, modelEntries = []) {
  const visibleCandles = candles.slice(start, start + size);
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const cssWidth = rect.width || 520;
  const cssHeight = canvas.classList.contains("large") ? 520 : 220;
  canvas.width = Math.floor(cssWidth * ratio);
  canvas.height = Math.floor(cssHeight * ratio);
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, cssWidth, cssHeight);

  if (!visibleCandles.length) {
    context.fillStyle = "#64748b";
    context.font = "13px Segoe UI";
    context.fillText("캔들 없음", 14, 28);
    return;
  }

  const padding = { left: 52, right: 10, top: 10, bottom: 24 };
  const plotWidth = cssWidth - padding.left - padding.right;
  const plotHeight = cssHeight - padding.top - padding.bottom;
  const maxPrice = Math.max(...visibleCandles.map((item) => Number(item.high)));
  const minPrice = Math.min(...visibleCandles.map((item) => Number(item.low)));
  const pricePad = (maxPrice - minPrice) * 0.06 || maxPrice * 0.001;
  const topPrice = maxPrice + pricePad;
  const bottomPrice = minPrice - pricePad;
  const candleStep = plotWidth / visibleCandles.length;
  const candleWidth = Math.max(1, Math.min(5, candleStep * 0.7));

  const xForIndex = (index) => padding.left + index * candleStep + candleStep / 2;
  const yForPrice = (price) => padding.top + ((topPrice - price) / (topPrice - bottomPrice)) * plotHeight;
  const entryByTime = buildModelEntryMap(modelEntries);

  context.strokeStyle = "#e2e8f0";
  context.fillStyle = "#64748b";
  context.lineWidth = 1;
  context.font = "11px Segoe UI";
  for (let grid = 0; grid <= 3; grid += 1) {
    const y = padding.top + (plotHeight / 3) * grid;
    const price = topPrice - ((topPrice - bottomPrice) / 3) * grid;
    context.beginPath();
    context.moveTo(padding.left, y);
    context.lineTo(cssWidth - padding.right, y);
    context.stroke();
    context.fillText(price.toFixed(6), 6, y + 4);
  }

  visibleCandles.forEach((candle, index) => {
    const x = xForIndex(index);
    const openY = yForPrice(Number(candle.open));
    const closeY = yForPrice(Number(candle.close));
    const highY = yForPrice(Number(candle.high));
    const lowY = yForPrice(Number(candle.low));
    const bodyTop = Math.min(openY, closeY);
    const bodyHeight = Math.max(1, Math.abs(closeY - openY));
    const label = candle.label || "NONE";
    const color = label === "TP" ? "#2563eb" : label === "SL" ? "#dc2626" : "#111827";

    context.strokeStyle = color;
    context.fillStyle = color;
    context.lineWidth = label === "NONE" ? 1 : 1.4;
    context.beginPath();
    context.moveTo(x, highY);
    context.lineTo(x, lowY);
    context.stroke();

    if (label === "NONE") {
      context.strokeRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
    } else {
      context.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
    }

    const modelEntry = entryByTime.get(minuteKey(candle.timestamp));
    if (modelEntry) {
      drawModelEntryArrow(context, x, highY, modelEntry);
    }
  });
}

function drawModelEntryArrow(context, x, highY, modelEntry) {
  const isDiagnostic = modelEntry.entry_type === "DIAGNOSTIC";
  const color = modelEntry.reason === "TP" ? "#2563eb" : modelEntry.reason === "SL" ? "#dc2626" : "#111827";
  const y = Math.max(16, highY - 18);
  context.save();
  context.fillStyle = color;
  context.strokeStyle = isDiagnostic ? "#facc15" : "#ffffff";
  context.lineWidth = isDiagnostic ? 3 : 2;
  context.beginPath();
  context.moveTo(x, y);
  context.lineTo(x - 7, y - 12);
  context.lineTo(x + 7, y - 12);
  context.closePath();
  context.stroke();
  context.fill();
  context.fillStyle = "#020617";
  context.font = "700 11px Segoe UI";
  context.fillText(isDiagnostic ? "CANDIDATE" : "ENTRY", x + 8, y - 8);
  context.restore();
}

function attachLabelCandleHover(canvas, tooltip, symbol, candles, start, size, modelEntries = []) {
  const visibleCandles = candles.slice(start, start + size);
  const entryByTime = buildModelEntryMap(modelEntries);
  canvas.onmousemove = (event) => {
    if (!visibleCandles.length) {
      tooltip.hidden = true;
      return;
    }
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const paddingLeft = 52;
    const paddingRight = 10;
    const plotWidth = rect.width - paddingLeft - paddingRight;
    const candleStep = plotWidth / visibleCandles.length;
    const rawIndex = Math.floor((x - paddingLeft) / candleStep);
    const index = Math.min(Math.max(rawIndex, 0), visibleCandles.length - 1);
    const candle = visibleCandles[index];
    if (!candle || x < paddingLeft || x > rect.width - paddingRight) {
      tooltip.hidden = true;
      return;
    }

    const modelEntry = entryByTime.get(minuteKey(candle.timestamp));
    tooltip.innerHTML = buildLabelTooltipHtml(symbol, candle, start + index, modelEntry);
    tooltip.hidden = false;
    const tooltipWidth = tooltip.offsetWidth || 260;
    const tooltipHeight = tooltip.offsetHeight || 150;
    const left = Math.min(Math.max(event.clientX - rect.left + 14, 8), rect.width - tooltipWidth - 8);
    const top = Math.min(Math.max(event.clientY - rect.top + 14, 8), rect.height - tooltipHeight - 8);
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  };
  canvas.onmouseleave = () => {
    tooltip.hidden = true;
  };
}

function buildLabelTooltipHtml(symbol, candle, absoluteIndex, modelEntry = null) {
  const open = Number(candle.open);
  const high = Number(candle.high);
  const low = Number(candle.low);
  const close = Number(candle.close);
  const range = Math.max(high - low, 0);
  const body = Math.abs(close - open);
  const upperWick = high - Math.max(open, close);
  const lowerWick = Math.min(open, close) - low;
  const changePct = open ? ((close - open) / open) * 100 : 0;
  const rangePct = close ? (range / close) * 100 : 0;
  const bodyRatio = range ? body / range : 0;
  const upperRatio = range ? upperWick / range : 0;
  const lowerRatio = range ? lowerWick / range : 0;
  const label = candle.label || "NONE";
  const tpText = formatPercent(state.labelTargetTp);
  const slText = formatPercent(state.labelStopLoss);
  const horizonText = state.labelHorizonMinutes ? `${state.labelHorizonMinutes}분` : "horizon";
  const candleType = close >= open ? "양봉" : "음봉";
  const bodySignal = bodyRatio >= 0.65 ? "몸통이 강함" : bodyRatio <= 0.25 ? "몸통이 약하고 꼬리가 큼" : "중간 강도";
  const wickSignal = upperRatio >= 0.45
    ? "윗꼬리 큼: 위에서 밀림"
    : lowerRatio >= 0.45
      ? "아랫꼬리 큼: 아래에서 받침"
      : "꼬리 부담 낮음";
  const volatilitySignal = rangePct >= 2.5 ? "초고변동" : rangePct >= 1 ? "활성 변동" : "낮은 변동";
  const labelText =
    label === "TP"
      ? `이 close에서 매수하면 ${horizonText} 안에 +${tpText}가 먼저 닿음`
      : label === "SL"
        ? `이 close에서 매수하면 ${horizonText} 안에 -${slText}가 먼저 닿음`
        : `${horizonText} 안에 +${tpText}/-${slText} 모두 미도달`;

  return `
    <div class="tooltip-title">${symbol} #${absoluteIndex + 1}</div>
    <div>${formatShortTime(candle.timestamp)}</div>
    <div class="tooltip-label ${label.toLowerCase()}">${label}: ${labelText}</div>
    ${modelEntry ? buildModelEntryTooltipHtml(modelEntry) : ""}
    <hr />
    <div>캔들 성격: ${candleType}, ${bodySignal}</div>
    <div>꼬리 해석: ${wickSignal}</div>
    <div>변동성: ${volatilitySignal} (${rangePct.toFixed(3)}%)</div>
    <div>캔들 수익률: ${changePct.toFixed(3)}%</div>
    <div>몸통/윗꼬리/아랫꼬리: ${bodyRatio.toFixed(2)} / ${upperRatio.toFixed(2)} / ${lowerRatio.toFixed(2)}</div>
    <div>거래량: ${formatValue(Number(candle.volume || 0))}</div>
  `;
}

function buildModelEntryTooltipHtml(modelEntry) {
  const title = modelEntry.entry_type === "DIAGNOSTIC" ? "진단 후보" : "실제 모델 진입";
  const missReason = modelEntry.miss_reason ? `<br />미진입 사유: ${modelEntry.miss_reason}` : "";
  const blockedBy = modelEntry.blocked_by ? `<br />차단 근거: ${modelEntry.blocked_by}` : "";
  const stage = modelEntry.candidate_stage ? `<br />판단 단계: ${modelEntry.candidate_stage}` : "";
  const activeSlots = modelEntry.active_slots !== undefined
    ? `<br />슬롯: ${formatValue(modelEntry.active_slots)} / ${formatValue(modelEntry.max_slots)}`
    : "";
  return `
    <div class="tooltip-entry">
      ${title}: ${modelEntry.reason || "-"} |
      pTP ${formatValue(modelEntry.p_tp)} /
      pSL ${formatValue(modelEntry.p_sl)} /
      pTimeout ${formatValue(modelEntry.p_timeout)} /
      rank ${formatValue(modelEntry.entry_rank_score)}
      ${missReason}${blockedBy}${stage}${activeSlots}
    </div>
  `;
}

function getModelEntriesForSymbol(symbol) {
  return [...(state.trades || []), ...(state.diagnosticEntries || [])].filter(
    (trade) => trade.symbol === symbol && trade.entry_time_kst,
  );
}

function buildModelEntryMap(entries) {
  const map = new Map();
  for (const entry of entries) {
    const key = minuteKey(entry.entry_time_kst);
    const existing = map.get(key);
    const existingIsEntry = existing && existing.entry_type !== "DIAGNOSTIC";
    const newIsDiagnostic = entry.entry_type === "DIAGNOSTIC";
    if (existingIsEntry && newIsDiagnostic) {
      continue;
    }
    map.set(key, entry);
  }
  return map;
}

function minuteKey(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value).slice(0, 16);
  }
  date.setSeconds(0, 0);
  return date.toISOString().slice(0, 16);
}

async function runBacktest() {
  const payload = {};
  const modelValue = modelInput.value.trim();
  const thresholdValue = thresholdInput.value;
  const candidateValue = candidateInput.value;
  const limitValue = limitInput.value;
  if (modelValue) {
    payload.model = modelValue;
  }
  if (thresholdValue) {
    payload.threshold = Number(thresholdValue);
  }
  if (candidateValue) {
    payload.candidate_top_n = Number(candidateValue);
  }
  if (limitValue) {
    payload.limit = Number(limitValue);
  }

  runButton.disabled = true;
  setStatus("백테스트 실행 중...");
  try {
    const result = await fetchJson("/api/scalping-1m/backtests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    setStatus(`완료: return_code=${result.return_code}`);
    await loadReports();
    if (result.latest_report?.name) {
      await loadReport(result.latest_report.name);
    }
  } catch (error) {
    setStatus(`실패: ${error.message}`);
  } finally {
    runButton.disabled = false;
  }
}

refreshButton.addEventListener("click", () => {
  setStatus("리포트 갱신 중...");
  loadReports()
    .then(() => setStatus("갱신 완료"))
    .catch((error) => setStatus(`갱신 실패: ${error.message}`));
});
runButton.addEventListener("click", runBacktest);
loadLabelMapButton.addEventListener("click", loadTop15LabelMap);
tradeWindowSlider.addEventListener("input", () => {
  state.tradeWindowStart = Number(tradeWindowSlider.value || 0);
  if (state.tradeReplay) {
    renderCandleChart(state.tradeReplay);
  }
});

loadScalpingDefaults()
  .catch((error) => setStatus(`기본값 로드 실패: ${error.message}`))
  .then(loadReports)
  .then(() => setStatus("준비 완료"))
  .catch((error) => setStatus(`초기화 실패: ${error.message}`));

window.addEventListener("resize", () => {
  if (state.tradeReplay) {
    renderCandleChart(state.tradeReplay);
  } else {
    clearChart("최근 거래를 선택하세요.");
  }
});
