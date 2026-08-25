'use strict';

/* btcbot 웹 UI.
 *
 * 프레임워크 없이 순수 JS로 쓴다. 사용자가 npm 같은 걸 몰라도 되게 하려면
 * 빌드 단계가 없어야 한다.
 */

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

const state = {
  meta: null,
  market: 'KRW-BTC',
  marketName: '비트코인',
  interval: 'day',
  candles: [],
  backtest: null,
  polling: null,
  tickerTimer: null,
};

/* ─────────────────────────────── 통신 ─────────────────────────────── */
async function api(path, body) {
  const options = body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : {};
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({ error: '서버 응답을 읽지 못했습니다' }));
  if (!res.ok || data.error) throw new Error(data.error || `요청 실패 (${res.status})`);
  return data;
}

function toast(message, isError) {
  const node = $('toast');
  node.textContent = message;
  node.className = 'toast show' + (isError ? ' error' : '');
  clearTimeout(node._timer);
  node._timer = setTimeout(() => { node.className = 'toast'; }, 3600);
}

/* ─────────────────────────────── 포맷 ─────────────────────────────── */
const won = (v) => (v == null ? '—' : Math.round(v).toLocaleString('ko-KR') + '원');
const pct = (v) => (v == null ? '—' : (v * 100).toFixed(2) + '%');
const signedPct = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + (v * 100).toFixed(2) + '%');
const signedWon = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + Math.round(v).toLocaleString('ko-KR'));
const price = (v) => (v == null ? '—' : v >= 100 ? Math.round(v).toLocaleString('ko-KR') : v.toFixed(2));

function bigMoney(v) {
  if (!v) return '—';
  if (v >= 1e12) return (v / 1e12).toFixed(1) + '조';
  if (v >= 1e8) return Math.round(v / 1e8).toLocaleString('ko-KR') + '억';
  if (v >= 1e4) return Math.round(v / 1e4).toLocaleString('ko-KR') + '만';
  return Math.round(v).toLocaleString('ko-KR');
}

/* ─────────────────────────────── 시작 ─────────────────────────────── */
async function boot() {
  const saved = localStorage.getItem('btcbot-theme');
  if (saved) document.documentElement.dataset.theme = saved;

  try {
    state.meta = await api('/api/meta');
  } catch (err) {
    toast('서버에 연결하지 못했습니다: ' + err.message, true);
    return;
  }

  state.market = state.meta.defaults.market;
  state.interval = state.meta.defaults.interval;

  renderMarkets();
  renderIntervals();
  renderPresets();
  renderRiskFields('riskFields', 'bt');
  renderRiskFields('runRiskFields', 'run');
  renderSaved();
  refreshStrategyPickers();
  bindEvents();

  addCondition('entry');
  addCondition('exit');
  loadPreset(state.meta.presets.find((p) => p.default) || state.meta.presets[0]);

  $('btStart').value = isoDaysAgo(365 * 2);
  $('runCash').value = state.meta.defaults.cash;
  $('btCash').value = state.meta.defaults.cash;

  refreshTicker();
  refreshChart();
  state.tickerTimer = setInterval(refreshTicker, 10000);
  pollStatus();
  setInterval(pollStatus, 4000);
}

const isoDaysAgo = (days) => new Date(Date.now() - days * 864e5).toISOString().slice(0, 10);

/* ─────────────────────────────── 마켓 ─────────────────────────────── */
function renderMarkets(filter) {
  const list = $('marketList');
  list.innerHTML = '';
  const term = (filter || '').trim().toLowerCase();
  const items = state.meta.markets.filter(
    (m) => !term || m.name.toLowerCase().includes(term) || m.market.toLowerCase().includes(term)
  );

  items.slice(0, 200).forEach((m) => {
    const row = el('div', 'market-item' + (m.market === state.market ? ' active' : ''));
    row.appendChild(el('span', null, m.name));
    row.appendChild(el('span', 'code', m.market.replace('KRW-', '')));
    row.onclick = () => selectMarket(m);
    list.appendChild(row);
  });

  if (!items.length) list.appendChild(el('div', 'market-item', '검색 결과 없음'));
}

function selectMarket(m) {
  state.market = m.market;
  state.marketName = m.name;
  $('mhName').textContent = m.name;
  $('mhCode').textContent = m.market;
  renderMarkets($('marketSearch').value);
  refreshTicker();
  refreshChart();
}

function renderIntervals() {
  const box = $('intervalTabs');
  box.innerHTML = '';
  state.meta.intervals.forEach((iv) => {
    const btn = el('button', 'tab' + (iv.value === state.interval ? ' active' : ''), iv.label);
    btn.onclick = () => {
      state.interval = iv.value;
      renderIntervals();
      refreshChart();
    };
    box.appendChild(btn);
  });
}

async function refreshTicker() {
  try {
    const t = await api(`/api/ticker?market=${encodeURIComponent(state.market)}`);
    const up = t.change_rate >= 0;
    $('mhPrice').textContent = price(t.price);
    $('mhChange').textContent = `${up ? '▲' : '▼'} ${price(Math.abs(t.change_price))} (${signedPct(t.change_rate)})`;
    $('mhHigh').textContent = price(t.high);
    $('mhLow').textContent = price(t.low);
    $('mhValue').textContent = bigMoney(t.value);
  } catch {
    $('mhChange').textContent = '시세 연결 안 됨 (오프라인)';
  }
}

/* ─────────────────────────────── 차트 ─────────────────────────────── */
async function refreshChart() {
  try {
    const data = await api(`/api/candles?market=${encodeURIComponent(state.market)}&interval=${state.interval}`);
    state.candles = data.candles;
    $('chartEmpty').classList.add('hidden');
  } catch {
    state.candles = [];
    $('chartEmpty').classList.remove('hidden');
  }
  drawCandles();
}

function fitCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || canvas.parentElement.clientWidth;
  canvas.width = width * dpr;
  canvas.height = canvas.getAttribute('height') * dpr;
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, canvas.height / dpr);
  return { ctx, w: width, h: canvas.height / dpr };
}

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function drawCandles() {
  const canvas = $('priceChart');
  const { ctx, w, h } = fitCanvas(canvas);
  const candles = state.candles;
  if (!candles.length) return;

  const padL = 8, padR = 62, padT = 10, volH = 46;
  const plotH = h - padT - volH - 22;
  const plotW = w - padL - padR;

  const highs = candles.map((c) => c.h);
  const lows = candles.map((c) => c.l);
  let hi = Math.max(...highs), lo = Math.min(...lows);
  const pad = (hi - lo) * 0.06 || 1;
  hi += pad; lo -= pad;

  const x = (i) => padL + (i + 0.5) * (plotW / candles.length);
  const y = (v) => padT + plotH * (1 - (v - lo) / (hi - lo));

  // 가로 눈금 + 오른쪽 가격축 (업비트처럼 축은 오른쪽)
  ctx.strokeStyle = css('--border');
  ctx.fillStyle = css('--muted');
  ctx.font = '10px sans-serif';
  ctx.textAlign = 'left';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const value = lo + ((hi - lo) * i) / 4;
    const py = Math.round(y(value)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(padL, py);
    ctx.lineTo(padL + plotW, py);
    ctx.stroke();
    ctx.fillText(price(value), padL + plotW + 6, py + 3);
  }

  const bw = Math.max(1, (plotW / candles.length) * 0.62);
  const upColor = css('--up'), downColor = css('--down');
  const maxVol = Math.max(...candles.map((c) => c.v)) || 1;

  candles.forEach((c, i) => {
    const rising = c.c >= c.o;
    ctx.strokeStyle = ctx.fillStyle = rising ? upColor : downColor;
    const cx = x(i);

    ctx.beginPath();
    ctx.moveTo(cx, y(c.h));
    ctx.lineTo(cx, y(c.l));
    ctx.stroke();

    const top = y(Math.max(c.o, c.c));
    const height = Math.max(1, Math.abs(y(c.o) - y(c.c)));
    ctx.fillRect(cx - bw / 2, top, bw, height);

    const vh = (c.v / maxVol) * volH;
    ctx.globalAlpha = 0.42;
    ctx.fillRect(cx - bw / 2, h - 18 - vh, bw, vh);
    ctx.globalAlpha = 1;
  });

  if ($('ovMa').checked) {
    [[5, '#f0a500'], [20, '#2a9d8f'], [60, '#9b5de5']].forEach(([period, color]) => {
      drawMA(ctx, candles, period, color, x, y);
    });
  }

  if ($('ovTrades').checked && state.backtest) drawTradeMarkers(ctx, x, y);

  // 시간축
  ctx.fillStyle = css('--muted');
  ctx.textAlign = 'center';
  const step = Math.max(1, Math.floor(candles.length / 6));
  for (let i = 0; i < candles.length; i += step) {
    ctx.fillText(candles[i].ts.slice(5, 10), x(i), h - 4);
  }
}

function drawMA(ctx, candles, period, color, x, y) {
  if (candles.length < period) return;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  let sum = 0, started = false;
  for (let i = 0; i < candles.length; i++) {
    sum += candles[i].c;
    if (i >= period) sum -= candles[i - period].c;
    if (i < period - 1) continue;
    const py = y(sum / period);
    if (!started) { ctx.moveTo(x(i), py); started = true; } else ctx.lineTo(x(i), py);
  }
  ctx.stroke();
  ctx.lineWidth = 1;
}

function drawTradeMarkers(ctx, x, y) {
  const index = new Map(state.candles.map((c, i) => [c.ts.slice(0, 16), i]));
  state.backtest.fills.forEach((f) => {
    const i = index.get(f.ts.slice(0, 16));
    if (i === undefined) return;
    const c = state.candles[i];
    const buy = f.side === 'buy';
    ctx.fillStyle = buy ? css('--up') : css('--down');
    const py = buy ? y(c.l) + 11 : y(c.h) - 11;
    ctx.beginPath();
    ctx.moveTo(x(i), py + (buy ? -7 : 7));
    ctx.lineTo(x(i) - 4.5, py);
    ctx.lineTo(x(i) + 4.5, py);
    ctx.closePath();
    ctx.fill();
  });
}

function drawEquity(curve) {
  const canvas = $('equityChart');
  const { ctx, w, h } = fitCanvas(canvas);
  if (!curve || curve.length < 2) return;

  const padL = 8, padR = 62, padT = 12, padB = 20;
  const plotW = w - padL - padR, plotH = h - padT - padB;

  const base = curve[0].equity, basePrice = curve[0].price;
  const strategy = curve.map((p) => p.equity / base);
  const hold = curve.map((p) => p.price / basePrice);
  const hi = Math.max(...strategy, ...hold), lo = Math.min(...strategy, ...hold);
  const span = hi - lo || 1;

  const x = (i) => padL + (i / (curve.length - 1)) * plotW;
  const y = (v) => padT + plotH * (1 - (v - lo) / span);

  ctx.strokeStyle = css('--border');
  ctx.fillStyle = css('--muted');
  ctx.font = '10px sans-serif';
  ctx.textAlign = 'left';
  for (let i = 0; i <= 4; i++) {
    const value = lo + (span * i) / 4;
    const py = Math.round(y(value)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(padL, py);
    ctx.lineTo(padL + plotW, py);
    ctx.stroke();
    ctx.fillText(signedPct(value - 1), padL + plotW + 6, py + 3);
  }

  // 원금선
  ctx.strokeStyle = css('--muted');
  ctx.setLineDash([3, 3]);
  ctx.beginPath();
  ctx.moveTo(padL, y(1));
  ctx.lineTo(padL + plotW, y(1));
  ctx.stroke();
  ctx.setLineDash([]);

  const line = (values, color, width) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.beginPath();
    values.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
    ctx.stroke();
    ctx.lineWidth = 1;
  };

  line(hold, css('--muted'), 1.2);
  line(strategy, css('--accent'), 2);

  ctx.fillStyle = css('--muted');
  ctx.textAlign = 'center';
  const step = Math.max(1, Math.floor(curve.length / 6));
  for (let i = 0; i < curve.length; i += step) {
    ctx.fillText(curve[i].ts.slice(2, 10), x(i), h - 4);
  }
}

/* ───────────────────────── 전략 빌더 ───────────────────────── */
function renderPresets() {
  const row = $('presetRow');
  row.innerHTML = '';
  state.meta.presets.forEach((preset) => {
    const btn = el('button', 'preset');
    btn.appendChild(el('strong', null, preset.label));
    btn.appendChild(el('span', null, preset.note || ''));
    btn.onclick = () => { loadPreset(preset); toast(`'${preset.label}'을(를) 불러왔습니다`); };
    row.appendChild(btn);
  });
}

function operandSpec(type) {
  return state.meta.builder.operands.find((o) => o.type === type);
}

/** 조건 한 줄을 만든다. value가 있으면 그 내용으로 채운다. */
function addCondition(group, value) {
  const box = $(group === 'entry' ? 'entryConditions' : 'exitConditions');
  const row = el('div', 'cond');

  const left = buildSide(value && value.left);
  const op = el('select', 'op');
  state.meta.builder.operators.forEach((o) => {
    const option = el('option', null, o.label);
    option.value = o.op;
    op.appendChild(option);
  });
  if (value) op.value = value.op;
  op.onchange = updateSummary;

  const right = buildSide(value ? value.right : { type: 'number', value: 0 });

  const remove = el('button', 'remove', '×');
  remove.title = '이 조건 삭제';
  remove.onclick = () => { row.remove(); updateSummary(); };

  row.append(left.node, op, right.node, remove);
  row._read = () => ({ left: left.read(), op: op.value, right: right.read() });
  box.appendChild(row);
  updateSummary();
}

/** 조건의 한쪽(지표 + 파라미터 입력칸)을 만든다. */
function buildSide(value) {
  const wrap = el('div', 'side');
  const select = el('select');
  state.meta.builder.operands.forEach((o) => {
    const option = el('option', null, o.label);
    option.value = o.type;
    select.appendChild(option);
  });

  const params = el('span', 'side');
  const inputs = {};

  const rebuild = () => {
    params.innerHTML = '';
    Object.keys(inputs).forEach((k) => delete inputs[k]);
    const spec = operandSpec(select.value);
    if (!spec) return;

    if (spec.type === 'number') {
      const input = el('input', 'num');
      input.type = 'number';
      input.step = 'any';
      input.value = value && value.type === 'number' ? value.value : 0;
      input.oninput = updateSummary;
      inputs.value = input;
      params.appendChild(input);
      return;
    }
    spec.params.forEach((p) => {
      params.appendChild(el('span', 'plabel', p.label));
      const input = el('input', 'num');
      input.type = 'number';
      input.step = 'any';
      input.value = value && value[p.key] !== undefined ? value[p.key] : p.default;
      input.oninput = updateSummary;
      inputs[p.key] = input;
      params.appendChild(input);
    });
  };

  select.value = value && value.type ? value.type : 'close';
  select.onchange = () => { rebuild(); updateSummary(); };
  rebuild();

  wrap.append(select, params);
  return {
    node: wrap,
    read: () => {
      const out = { type: select.value };
      Object.entries(inputs).forEach(([key, input]) => { out[key] = Number(input.value); });
      return out;
    },
  };
}

function loadPreset(preset) {
  $('specLabel').value = preset.label || '';
  $('specNote').value = preset.note || '';
  $('specWeight').value = String(preset.target_weight || 1);

  ['entry', 'exit'].forEach((group) => {
    const box = $(group === 'entry' ? 'entryConditions' : 'exitConditions');
    box.innerHTML = '';
    const node = preset[group];
    if (!node) return;
    const join = node.all ? 'all' : 'any';
    const radio = document.querySelector(`input[name="${group}Join"][value="${join}"]`);
    if (radio) radio.checked = true;
    (node.all || node.any || []).forEach((cond) => {
      if (cond.left) addCondition(group, cond);
    });
  });
  updateSummary();
}

function collectSpec() {
  const spec = {
    label: $('specLabel').value.trim(),
    note: $('specNote').value.trim(),
    target_weight: Number($('specWeight').value),
  };
  ['entry', 'exit'].forEach((group) => {
    const box = $(group === 'entry' ? 'entryConditions' : 'exitConditions');
    const rows = [...box.children].map((r) => r._read()).filter(Boolean);
    if (!rows.length) return;
    const join = document.querySelector(`input[name="${group}Join"]:checked`).value;
    spec[group] = { [join]: rows };
  });
  return spec;
}

function describeOperand(operand) {
  if (operand.type === 'number') return String(operand.value);
  const spec = operandSpec(operand.type);
  if (!spec) return operand.type;
  const args = spec.params.map((p) => operand[p.key]).filter((v) => v !== undefined);
  return args.length ? `${spec.label}(${args.join(',')})` : spec.label;
}

function updateSummary() {
  const spec = collectSpec();
  const box = $('specSummary');
  box.innerHTML = '';

  const describe = (group, verb, cls) => {
    const node = spec[group];
    if (!node) return;
    const join = node.all ? ' 그리고 ' : ' 또는 ';
    const parts = (node.all || node.any).map((c) => {
      const opLabel = state.meta.builder.operators.find((o) => o.op === c.op).label;
      return `${describeOperand(c.left)} 이(가) ${describeOperand(c.right)} ${opLabel}`;
    });
    const line = el('div');
    line.append(document.createTextNode(parts.join(join) + ' → '));
    line.appendChild(el('strong', cls, verb));
    box.appendChild(line);
  };

  describe('entry', '매수', 'buy');
  describe('exit', '매도', 'sell');
  if (!box.children.length) {
    box.appendChild(el('div', 'empty', '조건을 하나 이상 추가하세요.'));
  }
}

async function saveSpec() {
  const spec = collectSpec();
  if (!spec.label) { toast('전략 이름을 입력하세요', true); return; }
  try {
    const data = await api('/api/strategies/save', { spec });
    state.meta.saved = data.saved;
    renderSaved();
    refreshStrategyPickers();
    toast(`'${spec.label}' 저장 완료`);
  } catch (err) {
    toast(err.message, true);
  }
}

function renderSaved() {
  const box = $('savedList');
  box.innerHTML = '';
  const items = state.meta.saved || [];
  if (!items.length) {
    box.appendChild(el('span', 'muted', '아직 없습니다'));
    return;
  }
  items.forEach((item) => {
    const chip = el('span', 'chip');
    const load = el('button', 'load', item.label);
    load.title = item.note || '불러오기';
    load.onclick = () => { loadPreset(item); toast(`'${item.label}' 불러옴`); };
    const remove = el('button', null, '×');
    remove.title = '삭제';
    remove.onclick = async () => {
      if (!confirm(`'${item.label}' 전략을 삭제할까요?`)) return;
      const data = await api('/api/strategies/delete', { label: item.label });
      state.meta.saved = data.saved;
      renderSaved();
      refreshStrategyPickers();
    };
    chip.append(load, remove);
    box.appendChild(chip);
  });
}

/** 백테스트/자동매매 탭의 전략 선택 목록을 다시 그린다. */
function refreshStrategyPickers() {
  ['btStrategy', 'runStrategy'].forEach((id) => {
    const select = $(id);
    const previous = select.value;
    select.innerHTML = '';

    const builder = el('option', null, '⭐ 지금 만들고 있는 전략');
    builder.value = '__current__';
    select.appendChild(builder);

    (state.meta.saved || []).forEach((item) => {
      const option = el('option', null, '💾 ' + item.label);
      option.value = 'saved:' + item.label;
      select.appendChild(option);
    });

    state.meta.strategies
      .filter((s) => s.name !== 'rule')
      .forEach((s) => {
        const option = el('option', null, `📦 ${s.name} — ${s.label}`);
        option.value = 'builtin:' + s.name;
        select.appendChild(option);
      });

    if ([...select.options].some((o) => o.value === previous)) select.value = previous;
  });
}

/** 선택된 전략을 요청 본문 형태로. */
function strategyPayload(selectId) {
  const value = $(selectId).value;
  if (value === '__current__') return { spec: collectSpec() };
  if (value.startsWith('saved:')) {
    const label = value.slice(6);
    const found = (state.meta.saved || []).find((s) => s.label === label);
    if (!found) throw new Error('저장된 전략을 찾지 못했습니다');
    return { spec: found };
  }
  return { strategy: value.slice(8), params: {} };
}

/* ───────────────────────── 리스크 입력칸 ───────────────────────── */
const RISK_FIELDS = [
  { key: 'max_position_weight', label: '최대 투자 비중', hint: '1 = 전액', step: 0.05 },
  { key: 'stop_loss_pct', label: '손절 (-%)', hint: '0.05 = -5%', step: 0.01 },
  { key: 'take_profit_pct', label: '익절 (+%)', hint: '0 = 사용 안 함', step: 0.01 },
  { key: 'trailing_stop_pct', label: '트레일링 스탑', hint: '고점 대비 하락', step: 0.01 },
  { key: 'daily_loss_limit_pct', label: '하루 손실 한도', hint: '넘으면 당일 중단', step: 0.01 },
  { key: 'max_drawdown_pct', label: '최대 낙폭 한도', hint: '넘으면 봇 정지', step: 0.01 },
  { key: 'cooldown_bars', label: '손절 후 쉬는 봉', hint: '0 = 바로 재진입', step: 1 },
];

function renderRiskFields(containerId, prefix) {
  const box = $(containerId);
  box.innerHTML = '';
  const defaults = state.meta.defaults.risk;
  RISK_FIELDS.forEach((f) => {
    const field = el('label', 'field');
    field.appendChild(el('span', null, f.label));
    const input = el('input');
    input.type = 'number';
    input.step = String(f.step);
    input.min = '0';
    input.id = `${prefix}_${f.key}`;
    input.value = defaults[f.key];
    field.appendChild(input);
    field.appendChild(el('span', 'muted', f.hint));
    box.appendChild(field);
  });
}

function collectRisk(prefix) {
  const out = {};
  RISK_FIELDS.forEach((f) => {
    const input = $(`${prefix}_${f.key}`);
    if (input) out[f.key] = Number(input.value);
  });
  return out;
}

/* ───────────────────────── 백테스트 ───────────────────────── */
async function fetchData() {
  const button = $('fetchData');
  button.disabled = true;
  setStatus('btStatus', '시세를 받는 중입니다. 처음 받을 때는 1~2분 걸릴 수 있습니다…');
  try {
    const data = await api('/api/data/fetch', {
      market: state.market,
      interval: state.interval,
      start: $('btStart').value,
    });
    setStatus('btStatus', `봉 ${data.count.toLocaleString('ko-KR')}개 준비 완료 (${data.first.slice(0, 10)} ~ ${data.last.slice(0, 10)})`, 'ok');
    refreshChart();
  } catch (err) {
    setStatus('btStatus', err.message, 'error');
  } finally {
    button.disabled = false;
  }
}

async function runBacktest() {
  const button = $('runBacktest');
  button.disabled = true;
  setStatus('btStatus', '백테스트를 돌리는 중…');
  try {
    const body = {
      market: state.market,
      interval: state.interval,
      start: $('btStart').value || null,
      end: $('btEnd').value || null,
      cash: Number($('btCash').value),
      fee_rate: Number($('btFee').value),
      slippage: Number($('btSlip').value),
      risk: collectRisk('bt'),
      ...strategyPayload('btStrategy'),
    };
    const result = await api('/api/backtest', body);
    state.backtest = result;
    renderBacktest(result);
    setStatus('btStatus', '완료', 'ok');
  } catch (err) {
    setStatus('btStatus', err.message, 'error');
    $('btResult').classList.add('hidden');
  } finally {
    button.disabled = false;
  }
}

function setStatus(id, message, kind) {
  const node = $(id);
  node.textContent = message;
  node.className = 'status-line' + (kind ? ' ' + kind : '');
}

function renderBacktest(result) {
  $('btResult').classList.remove('hidden');
  const p = result.performance;

  const cards = [
    ['총 수익률', signedPct(p.total_return), 'hero', p.total_return],
    ['그냥 들고 있었다면', signedPct(p.buy_and_hold_return), '', p.buy_and_hold_return],
    ['최대 낙폭(MDD)', pct(p.max_drawdown), 'hero', -p.max_drawdown],
    ['최종 자산', won(p.final_equity), '', null],
    ['연환산 수익률', signedPct(p.cagr), '', p.cagr],
    ['승률', pct(p.win_rate), '', null],
    ['거래 횟수', (p.trades || 0) + '회', '', null],
    ['손익비', p.profit_factor != null ? p.profit_factor.toFixed(2) : '—', '', null],
    ['샤프 지수', p.sharpe != null ? p.sharpe.toFixed(2) : '—', '', null],
    ['총 수수료', won(p.total_fees), '', null],
  ];

  const box = $('btMetrics');
  box.innerHTML = '';
  cards.forEach(([label, value, cls, tone]) => {
    const card = el('dl', 'metric ' + cls);
    card.appendChild(el('dt', null, label));
    const dd = el('dd', tone == null ? '' : tone >= 0 ? 'up' : 'down', value);
    card.appendChild(dd);
    box.appendChild(card);
  });

  drawEquity(result.equity_curve);
  drawCandles();

  $('btTradeCount').textContent = `— ${result.trades.length}건`;
  renderTable(
    'btTrades',
    ['진입', '청산', '진입가', '청산가', '수익률', '손익', { text: '사유', cls: 'reason' }],
    result.trades.map((t) => [
      t.entry_ts, t.exit_ts, price(t.entry_price), price(t.exit_price),
      { text: signedPct(t.pnl_pct), cls: t.pnl >= 0 ? 'up' : 'down' },
      { text: signedWon(t.pnl), cls: t.pnl >= 0 ? 'up' : 'down' },
      { text: t.reason, cls: 'reason' },
    ]),
    '거래가 한 건도 없었습니다. 조건이 너무 까다롭지 않은지 확인해보세요.'
  );
}

function renderTable(id, headers, rows, emptyText) {
  const table = $(id);
  table.innerHTML = '';
  const thead = el('thead');
  const hr = el('tr');
  headers.forEach((h) => {
    const cell = typeof h === 'object' && h !== null ? h : { text: h, cls: '' };
    hr.appendChild(el('th', cell.cls, cell.text));
  });
  thead.appendChild(hr);
  table.appendChild(thead);

  const tbody = el('tbody');
  if (!rows.length) {
    const tr = el('tr', 'empty-row');
    const td = el('td', null, emptyText || '내역이 없습니다');
    td.colSpan = headers.length;
    tr.appendChild(td);
    tbody.appendChild(tr);
  } else {
    rows.forEach((cells) => {
      const tr = el('tr');
      cells.forEach((cell) => {
        const value = typeof cell === 'object' && cell !== null ? cell : { text: cell, cls: '' };
        tr.appendChild(el('td', value.cls, String(value.text ?? '')));
      });
      tbody.appendChild(tr);
    });
  }
  table.appendChild(tbody);
}

/* ───────────────────────── 자동매매 ───────────────────────── */
function isLiveMode() {
  return document.querySelector('input[name="runMode"]:checked').value === 'live';
}

/* 실전 투자에 권하는 보호장치. 처음 켜는 사람이 아무것도 없이
 * 시작하는 것을 막는다. 값은 언제든 바꿀 수 있다. */
const SAFE_DEFAULTS = {
  max_position_weight: 0.3,
  stop_loss_pct: 0.05,
  daily_loss_limit_pct: 0.03,
  max_drawdown_pct: 0.15,
  cooldown_bars: 3,
};

function hasNoProtection() {
  const risk = collectRisk('run');
  return (
    !risk.stop_loss_pct &&
    !risk.trailing_stop_pct &&
    !risk.daily_loss_limit_pct &&
    !risk.max_drawdown_pct
  );
}

function onModeChange() {
  const live = isLiveMode();
  $('liveWarning').classList.toggle('hidden', !live);
  $('runCashField').classList.toggle('hidden', live);
  $('startBot').textContent = live ? '실전 투자 시작' : '모의 투자 시작';
  $('startBot').className = live ? 'danger-btn' : 'primary';

  if (live && hasNoProtection()) {
    Object.entries(SAFE_DEFAULTS).forEach(([key, value]) => {
      const input = $('run_' + key);
      if (input) input.value = value;
    });
    $('runRiskFields').closest('details').open = true;
    toast('보호장치가 없어 안전 기본값을 채웠습니다. 값은 바꿔도 됩니다.');
  }
}

async function startBot() {
  const live = isLiveMode();
  if (live) {
    if (!state.meta.has_api_keys) {
      toast('API 키가 없습니다. .env 파일에 키를 넣고 서버를 다시 시작하세요.', true);
      return;
    }
    if (!$('liveConfirm').checked) {
      toast('실전 투자 확인란에 체크해야 시작할 수 있습니다', true);
      return;
    }
    if (hasNoProtection() && !confirm(
      '손절·일일한도·최대낙폭이 모두 꺼져 있습니다.\n' +
      '손실을 막아줄 장치가 하나도 없는 상태로 실전 매매를 시작합니다.\n\n그래도 계속할까요?'
    )) return;
  }

  const button = $('startBot');
  button.disabled = true;
  setStatus('runStatus', '봇을 시작하는 중…');
  try {
    const body = {
      market: state.market,
      interval: state.interval,
      live,
      dry_run: live && $('dryRun').checked,
      cash: Number($('runCash').value),
      risk: collectRisk('run'),
      run_name: $('runName').value.trim() || null,
      ...strategyPayload('runStrategy'),
    };
    await api('/api/bot/start', body);
    setStatus('runStatus', live ? '실전 투자를 시작했습니다.' : '모의 투자를 시작했습니다.', 'ok');
    pollStatus();
  } catch (err) {
    setStatus('runStatus', err.message, 'error');
  } finally {
    button.disabled = false;
  }
}

async function stopBot() {
  setStatus('runStatus', '중지 요청 — 현재 봉 처리를 마치면 멈춥니다…');
  try {
    await api('/api/bot/stop', {});
    setStatus('runStatus', '봇을 멈췄습니다. 보유 중이던 포지션은 그대로 있습니다.', 'ok');
  } catch (err) {
    setStatus('runStatus', err.message, 'error');
  }
  pollStatus();
}

async function pollStatus() {
  let status;
  try {
    status = await api('/api/status');
  } catch {
    return;
  }

  const badge = $('botBadge');
  if (status.running) {
    badge.dataset.state = status.mode === '실거래' ? 'live' : 'paper';
    $('botBadgeText').textContent = `${status.mode} 실행 중${status.dry_run ? ' (연습)' : ''}`;
  } else {
    badge.dataset.state = 'idle';
    $('botBadgeText').textContent = '봇 정지';
  }
  $('startBot').classList.toggle('hidden', status.running);
  $('stopBot').classList.toggle('hidden', !status.running);

  if (status.error) setStatus('runStatus', status.error, 'error');
  renderLiveStats(status);

  try {
    const { logs } = await api('/api/logs?limit=120');
    const box = $('botLogs');
    if (logs.length) {
      box.innerHTML = '';
      logs.forEach((line) => {
        const row = el('div');
        row.appendChild(el('span', 't', line.ts));
        row.appendChild(el('span', line.level, line.message));
        box.appendChild(row);
      });
      box.scrollTop = box.scrollHeight;
    }
  } catch { /* 로그는 실패해도 무시 */ }
}

function renderLiveStats(status) {
  const box = $('liveStats');
  box.innerHTML = '';
  if (!status.running && status.equity == null) {
    box.appendChild(el('span', 'muted', '봇을 시작하면 상태가 표시됩니다.'));
    return;
  }

  const cards = [
    ['평가 자산', won(status.equity), status.return_pct],
    ['수익률', signedPct(status.return_pct), status.return_pct],
    ['현금', won(status.cash), null],
    ['코인 비중', pct(status.weight), null],
    ['현재가', price(status.price), null],
    ['체결', (status.fills || 0) + '건', null],
    ['청산', (status.trades || 0) + '건', null],
    ['실현손익', signedWon(status.realized_pnl), status.realized_pnl],
  ];
  cards.forEach(([label, value, tone]) => {
    const card = el('dl', 'metric');
    card.appendChild(el('dt', null, label));
    card.appendChild(el('dd', tone == null ? '' : tone >= 0 ? 'up' : 'down', value));
    box.appendChild(card);
  });

  if (status.risk && status.risk.halted) {
    setStatus('runStatus', '⛔ 리스크 한도로 봇이 정지했습니다: ' + status.risk.halt_reason, 'error');
  }

  renderTable(
    'liveFills',
    ['시각', '구분', '가격', '수량', { text: '사유', cls: 'reason' }],
    (status.recent_fills || []).reverse().map((f) => [
      f.ts,
      { text: f.side, cls: f.side === '매수' ? 'up' : 'down' },
      price(f.price),
      f.volume.toFixed(8),
      { text: f.reason, cls: 'reason' },
    ]),
    '아직 체결이 없습니다.'
  );
}

/* ───────────────────────── 기록 ───────────────────────── */
async function loadHistory() {
  const run = $('histRun').value.trim() || `paper-${state.market}`;
  try {
    const data = await api(`/api/journal?run=${encodeURIComponent(run)}`);
    setStatus(
      'histSummary',
      `총 ${data.count}건 · 누적 실현손익 ${signedWon(data.total_pnl)}원`,
      data.total_pnl >= 0 ? 'ok' : 'error'
    );
    renderTable(
      'histTable',
      ['청산 시각', '진입가', '청산가', '수익률', '손익', { text: '사유', cls: 'reason' }],
      data.trades.reverse().map((t) => [
        String(t.exit_ts).slice(0, 16).replace('T', ' '),
        price(t.entry_price),
        price(t.exit_price),
        { text: signedPct(t.pnl_pct), cls: t.pnl >= 0 ? 'up' : 'down' },
        { text: signedWon(t.pnl), cls: t.pnl >= 0 ? 'up' : 'down' },
        { text: t.reason || '', cls: 'reason' },
      ]),
      '이 이름으로 저장된 기록이 없습니다.'
    );
  } catch (err) {
    setStatus('histSummary', err.message, 'error');
  }
}

/* ───────────────────────── 이벤트 ───────────────────────── */
function switchTab(name) {
  document.querySelectorAll('#mainTabs .tab').forEach((t) => {
    t.classList.toggle('active', t.dataset.tab === name);
  });
  document.querySelectorAll('.tab-body').forEach((b) => {
    b.classList.toggle('active', b.id === 'tab-' + name);
  });
  if (name === 'backtest' && state.backtest) drawEquity(state.backtest.equity_curve);
}

function bindEvents() {
  $('marketSearch').oninput = (e) => renderMarkets(e.target.value);
  document.querySelectorAll('#mainTabs .tab').forEach((tab) => {
    tab.onclick = () => switchTab(tab.dataset.tab);
  });
  document.querySelectorAll('[data-goto]').forEach((node) => {
    node.onclick = () => switchTab(node.dataset.goto);
  });

  $('addEntry').onclick = () => addCondition('entry');
  $('addExit').onclick = () => addCondition('exit');
  $('saveSpec').onclick = saveSpec;
  $('testSpec').onclick = () => switchTab('backtest');
  document.querySelectorAll('input[name="entryJoin"], input[name="exitJoin"]').forEach((r) => {
    r.onchange = updateSummary;
  });

  $('fetchData').onclick = fetchData;
  $('runBacktest').onclick = runBacktest;
  $('startBot').onclick = startBot;
  $('stopBot').onclick = stopBot;
  $('loadHistory').onclick = loadHistory;
  document.querySelectorAll('input[name="runMode"]').forEach((r) => { r.onchange = onModeChange; });

  $('ovMa').onchange = drawCandles;
  $('ovTrades').onchange = drawCandles;

  $('themeToggle').onclick = () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('btcbot-theme', next); } catch { /* 저장 실패는 무시 */ }
    drawCandles();
    if (state.backtest) drawEquity(state.backtest.equity_curve);
  };

  window.addEventListener('resize', () => {
    drawCandles();
    if (state.backtest) drawEquity(state.backtest.equity_curve);
  });

  onModeChange();
}

boot();
