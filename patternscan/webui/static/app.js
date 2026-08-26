'use strict';

// 그림은 전부 SVG로 그린다.
// 이전 판(canvas)에서는 화면 배율이 2 이상인 기기에서 다시 그릴 때마다
// 캔버스가 배율만큼 계속 커지는 버그가 있었다. SVG는 viewBox가 좌표계를
// 고정하고 크기는 CSS가 정하므로 그 문제가 아예 생기지 않는다.

const VIEW = { w: 400, h: 200, pad: 12 };

const $ = (id) => document.getElementById(id);
const pct = (x, digits = 1) => `${(x * 100).toFixed(digits)}%`;
const signed = (x, digits = 1) => `${x >= 0 ? '+' : ''}${(x * 100).toFixed(digits)}%`;

let selected = null;      // {timeframe, length, horizon}
let shownAnalysis = -1;   // 화면에 그려 둔 분석 번호
let timer = null;

// ---------------------------------------------------------------- 통신
async function api(path, options) {
  const response = await fetch(path, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch (err) {
    throw new Error(`서버 응답을 읽지 못했습니다 (${response.status})`);
  }
  if (!response.ok) throw new Error(payload && payload.error ? payload.error : `오류 ${response.status}`);
  return payload;
}

function post(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}

function showError(message) {
  const box = $('error');
  if (!message) { box.hidden = true; box.textContent = ''; return; }
  box.hidden = false;
  box.textContent = message;
}

// ---------------------------------------------------------------- 상태 폴링
async function refreshState() {
  let state;
  try {
    state = await api('/api/state');
  } catch (err) {
    showError(err.message);
    return;
  }

  const job = state.job || {};
  $('market').textContent = state.market;

  $('btn-scan').disabled = job.running;
  $('btn-fetch').disabled = job.running;
  $('job').textContent = job.running ? (job.message || '작업 중…') : (job.message || '');

  const bar = $('progress');
  if (job.running && job.total > 0) {
    bar.hidden = false;
    $('progress-bar').style.width = `${Math.min(100, (job.done / job.total) * 100)}%`;
  } else {
    bar.hidden = true;
  }

  if (job.error) showError(job.error); else showError('');

  // 표를 매번 다시 그리면 사용자가 누르던 행이 클릭 도중에 사라진다.
  // 분석이 실제로 바뀌었을 때만 다시 그린다.
  if (state.analysis && state.analysisId !== shownAnalysis) {
    shownAnalysis = state.analysisId;
    selected = null;
    render(state.analysis);
  } else if (!state.analysis && state.cached) {
    renderCached(state.cached);
  }

  // 다시 계산하는 동안 옛날 판정이 그대로 떠 있으면 지금 결과로 읽힌다.
  // 화면에 뭔가 떠 있는 채로 새 분석이 도는 경우가 정확히 그 상황이다.
  setStale(job.running && job.kind === 'scan' && state.analysis !== null);

  // 작업이 돌 때만 폴링한다. 끝나면 멈춘다 — 계속 물어볼 이유가 없다.
  stopPolling();
  if (job.running) timer = setTimeout(refreshState, 500);
}

function stopPolling() {
  if (timer !== null) { clearTimeout(timer); timer = null; }
}

function setStale(stale) {
  for (const id of ['verdict', 'table-panel', 'chart-panel']) {
    $(id).classList.toggle('stale', stale);
  }
  $('stale-note').hidden = !stale;
}

// ---------------------------------------------------------------- 렌더
function renderCached(cached) {
  const box = $('coverage');
  const any = cached.some((c) => c.count > 0);
  box.hidden = false;
  box.innerHTML = any
    ? `받아둔 시세: ${cached.map((c) => `<b>${c.label}</b> ${c.count.toLocaleString()}개`).join(' · ')}
       — <b>지금 판정하기</b>를 누르세요.`
    : `아직 받아둔 시세가 없습니다. <b>시세 받기</b>를 먼저 눌러 주세요 (몇 분 걸립니다).`;
}

function render(analysis) {
  renderVerdict(analysis);
  renderCoverage(analysis);
  renderTable(analysis);

  // 판정 근거가 된 조합을 기본으로 열어 준다.
  if (analysis.findings.length) {
    const first = analysis.findings.find((f) => f.qualifies) || analysis.findings[0];
    select(first);
  }
}

function renderVerdict(analysis) {
  const v = analysis.verdict;
  const panel = $('verdict');
  panel.hidden = false;
  panel.className = `panel verdict ${v.enter ? 'enter' : 'stay'}`;
  $('verdict-headline').textContent = v.headline;
  $('verdict-reasons').innerHTML = v.reasons.map((r) => `<li>${escapeHtml(r)}</li>`).join('');
  $('verdict-meta').textContent =
    `상승 기준: 왕복 비용 ${pct(analysis.cost, 3)}를 넘긴 경우만. ` +
    `'같은 모양' 기준: 상관계수 ${analysis.similarity.toFixed(2)} 이상. ` +
    `검정한 조합 ${v.tested}개 중 보정 후 유의한 것 ${v.significant}개.`;
}

function renderCoverage(analysis) {
  const box = $('coverage');
  box.hidden = false;
  const spans = analysis.series
    .map((s) => `<b>${s.label}</b> ${s.count.toLocaleString()}개` + (s.gaps ? ` (끊긴 곳 ${s.gaps})` : ''))
    .join(' · ');
  const c = analysis.coverage;
  // 시세가 없어 아예 못 본 봉 간격은 반드시 알려야 한다 — 조용히 빼면
  // 사용자는 1·3·5분봉을 다 본 줄 안다.
  const missing = (analysis.missing || []).length
    ? `<br><b class="warn">${analysis.missing.map((m) => m.label).join(', ')} 시세가 없어` +
      ` 이 간격은 판정에서 빠졌습니다.</b>`
    : '';
  box.innerHTML =
    `${spans}${missing}<br>` +
    `조합 ${c.total}개 중 데이터가 모자라 건너뛴 것 ${c.skipped}개, ` +
    `같은 모양을 ${analysis.minSamples}개 미만으로 찾은 것 ${c.thin}개 ` +
    `— 모양이 길수록 똑같은 게 잘 없습니다.`;
}

function renderTable(analysis) {
  $('table-panel').hidden = false;
  const body = $('findings').querySelector('tbody');
  body.innerHTML = '';

  if (!analysis.findings.length) {
    body.innerHTML = `<tr><td class="l" colspan="9">표본 ${analysis.minSamples}개를 넘긴 조합이 없습니다.</td></tr>`;
    return;
  }

  for (const f of analysis.findings.slice(0, 40)) {
    const tr = document.createElement('tr');
    tr.className = [f.significant ? 'sig' : '', f.qualifies ? 'pass' : ''].join(' ').trim();
    tr.dataset.key = `${f.timeframe}|${f.length}|${f.horizon}`;
    tr.innerHTML = `
      <td class="l">${f.timeframeLabel} ${f.length}개 → ${f.horizon}봉 뒤</td>
      <td>${f.samples}</td>
      <td>${pct(f.upRate)}</td>
      <td>${pct(f.baseUpRate)}</td>
      <td class="${f.edge >= 0 ? 'pos' : 'neg'}">${signed(f.edge)}</td>
      <td class="${f.meanReturn >= 0 ? 'pos' : 'neg'}">${signed(f.meanReturn, 3)}</td>
      <td>${f.minSimilarity === null ? '—' : f.minSimilarity.toFixed(2)}</td>
      <td class="${f.mostlyATrend ? 'trend' : ''}" title="${f.mostlyATrend
        ? '직선에 가깝습니다 — 특이한 모양이 아니라 추세 중인 구간을 센 것에 가깝습니다'
        : ''}">${f.linearity.toFixed(2)}</td>
      <td>${f.qValue.toFixed(3)}</td>
      <td>${pct(f.firstHalfRate, 0)} / ${pct(f.secondHalfRate, 0)}</td>`;
    tr.addEventListener('click', () => select(f));
    body.appendChild(tr);
  }
  markSelected();
}

function markSelected() {
  const key = selected ? `${selected.timeframe}|${selected.length}|${selected.horizon}` : null;
  for (const tr of document.querySelectorAll('#findings tbody tr')) {
    tr.classList.toggle('selected', tr.dataset.key === key);
  }
}

// ---------------------------------------------------------------- 모양 그리기
async function select(finding) {
  selected = { timeframe: finding.timeframe, length: finding.length, horizon: finding.horizon };
  markSelected();
  try {
    const data = await api(
      `/api/shape?timeframe=${encodeURIComponent(finding.timeframe)}` +
      `&length=${finding.length}&horizon=${finding.horizon}`
    );
    drawShapes(data);
  } catch (err) {
    showError(err.message);
  }
}

function scaler(values, box) {
  let low = Math.min(...values);
  let high = Math.max(...values);
  if (!(high > low)) { high = low + 1; low -= 1; }
  const pad = (high - low) * 0.08;
  low -= pad; high += pad;
  return (v) => box.h - VIEW.pad - ((v - low) / (high - low)) * (box.h - VIEW.pad * 2);
}

function polyline(values, xs) {
  return values.map((v, i) => `${xs(i).toFixed(1)},${v.toFixed(1)}`).join(' ');
}

function drawShapes(data) {
  $('chart-panel').hidden = false;
  $('chart-title').textContent =
    `${data.timeframeLabel} 직전 ${data.length}개 → ${data.horizon}봉 뒤`;

  // --- 왼쪽: 모양 겹쳐보기
  const all = data.query.concat(...data.shapes.map((s) => s.values));
  const y = scaler(all, VIEW);
  const xs = (i) => VIEW.pad + (i / Math.max(1, data.length - 1)) * (VIEW.w - VIEW.pad * 2);

  let svg = '';
  for (const shape of data.shapes) {
    // 덜 닮은 것일수록 흐리게 — 표본이 얼마나 고른지 눈으로 보인다.
    const alpha = 0.12 + 0.35 * Math.max(0, (shape.similarity - 0.5) / 0.5);
    svg += line(polyline(shape.values.map(y), xs), '#8b93a3', 1, alpha);
  }
  svg += line(polyline(data.query.map(y), xs), '#3d7eff', 2.4, 1);
  $('chart-shape').innerHTML = svg;

  // --- 오른쪽: 직후 경로
  const afterValues = [0, data.cost, -data.cost].concat(...data.paths.map((p) => p.values));
  const y2 = scaler(afterValues, VIEW);
  const xs2 = (i) => VIEW.pad + (i / Math.max(1, data.horizon)) * (VIEW.w - VIEW.pad * 2);

  let svg2 = '';
  // 손익분기선(왕복 비용) — 이 선 위로 끝나야 '올랐다'로 센다.
  svg2 += rule(y2(data.cost), '#26a17b', '비용');
  svg2 += rule(y2(0), '#3a4150', null);
  for (const path of data.paths) {
    svg2 += line(polyline(path.values.map(y2), xs2), path.won ? '#26a17b' : '#d0424f', 1.2, 0.45);
  }
  $('chart-after').innerHTML = svg2;

  const won = data.paths.filter((p) => p.won).length;
  $('chart-note').textContent =
    `같은 모양 ${data.matches}건 중 가장 닮은 ${data.shown}건을 그렸습니다. ` +
    `그중 ${won}건이 왕복 비용 ${pct(data.cost, 3)}를 넘겼습니다. ` +
    `지금 모양의 끝: ${data.queryAt} KST.`;
}

function line(points, color, width, alpha) {
  // vector-effect가 있어야 viewBox를 늘려도 선 굵기가 일정하다.
  return `<polyline points="${points}" fill="none" stroke="${color}" stroke-width="${width}"` +
         ` stroke-opacity="${alpha}" vector-effect="non-scaling-stroke"` +
         ` stroke-linejoin="round" stroke-linecap="round"/>`;
}

function rule(y, color, label) {
  const text = label
    ? `<text x="${VIEW.pad + 2}" y="${(y - 4).toFixed(1)}" fill="${color}" font-size="9">${label}</text>`
    : '';
  return `<line x1="${VIEW.pad}" y1="${y.toFixed(1)}" x2="${VIEW.w - VIEW.pad}" y2="${y.toFixed(1)}"` +
         ` stroke="${color}" stroke-width="1" stroke-dasharray="3 3"` +
         ` vector-effect="non-scaling-stroke"/>${text}`;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// ---------------------------------------------------------------- 조작
function settings() {
  return {
    market: $('in-market').value.trim().toUpperCase() || 'KRW-BTC',
    similarity: parseFloat($('in-similarity').value),
    fee: parseFloat($('in-fee').value),
    slippage: parseFloat($('in-slippage').value),
    scale: $('in-scale').value,
  };
}

$('btn-scan').addEventListener('click', async () => {
  showError('');
  try {
    await post('/api/scan', settings());
    refreshState();
  } catch (err) {
    showError(err.message);
  }
});

$('btn-fetch').addEventListener('click', async () => {
  showError('');
  try {
    await post('/api/fetch', { market: settings().market, refresh: $('in-refresh').checked });
    refreshState();
  } catch (err) {
    showError(err.message);
  }
});

refreshState();
