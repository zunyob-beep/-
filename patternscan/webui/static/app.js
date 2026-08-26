'use strict';

// 그림은 전부 SVG로 그린다.
// 이전 판(canvas)에서는 화면 배율이 2 이상인 기기에서 다시 그릴 때마다
// 캔버스가 배율만큼 계속 커지는 버그가 있었다. SVG는 viewBox가 좌표계를
// 고정하고 크기는 CSS가 정하므로 그 문제가 아예 생기지 않는다.

const $ = (id) => document.getElementById(id);
const pct = (x, d = 0) => `${(x * 100).toFixed(d)}%`;
const signed = (x, d = 2) => `${x >= 0 ? '+' : ''}${(x * 100).toFixed(d)}%`;

let selected = null;      // {timeframe, horizon}
let shownAnalysis = -1;
let timer = null;

// ---------------------------------------------------------------- 통신
// 서버가 답을 안 준 것과, 서버가 "안 된다"고 답한 것은 완전히 다른 사건이다.
// 앞의 것은 컴퓨터를 켜야 하고, 뒤의 것은 화면에서 조건을 바꿔야 한다.
// 둘을 같은 빨간 줄로 보여주면 사용자는 어느 쪽인지 알 수가 없다.
class Unreachable extends Error {}

async function api(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch (err) {
    throw new Unreachable('서버에 연결되지 않았습니다');
  }
  let payload = null;
  try { payload = await response.json(); } catch (err) { /* 아래에서 처리 */ }
  if (response.status >= 500 || response.status === 503) {
    throw new Unreachable(`서버가 응답하지 못했습니다 (${response.status})`);
  }
  if (!response.ok) throw new Error(payload && payload.error ? payload.error : `오류 ${response.status}`);
  if (!payload) throw new Error('서버 응답을 읽지 못했습니다');
  return payload;
}

const post = (path, body) => api(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body || {}),
});

function showError(message) {
  const box = $('error');
  box.hidden = !message;
  box.textContent = message || '';
}

function setOffline(down) {
  $('offline').hidden = !down;
  if (down) showError('');
}

function report(err) {
  if (err instanceof Unreachable) { setOffline(true); return; }
  setOffline(false);
  showError(err.message);
}

// ---------------------------------------------------------------- 상태
async function refreshState() {
  let state;
  try { state = await api('/api/state'); }
  catch (err) {
    report(err);
    // 서버가 꺼진 거라면 계속 두드린다. 다시 켜는 순간 알아서 살아나야 한다.
    if (err instanceof Unreachable) { stopPolling(); timer = setTimeout(refreshState, 5000); }
    return;
  }
  setOffline(false);

  const job = state.job || {};
  $('market').textContent = state.market;
  $('btn-scan').disabled = job.running;
  $('btn-live').disabled = job.running;
  $('job').textContent = job.running ? (job.message || '작업 중…') : (job.message || '');

  const bar = $('progress');
  if (job.running && job.total > 0) {
    bar.hidden = false;
    $('progress-bar').style.width = `${Math.min(100, (job.done / job.total) * 100)}%`;
  } else { bar.hidden = true; }

  showError(job.error || '');

  // 표를 매번 다시 그리면 사용자가 누르던 행이 클릭 도중에 사라진다.
  if (state.analysis && state.analysisId !== shownAnalysis) {
    shownAnalysis = state.analysisId;
    selected = null;
    render(state.analysis);
  } else if (!state.analysis && state.cached) {
    renderCached(state.cached);
  }

  setStale(job.running && state.analysis !== null);

  // 작업 중이면 자주, 아니면 가끔. 놀 때도 계속 물어보는 이유는 하나다 —
  // **서버가 꺼진 걸 눈치채기 위해서**다. 예전에는 놀 때 폴링을 아예
  // 멈춰서, 컴퓨터가 꺼져도 화면은 멀쩡해 보였다. 버튼을 눌러야만
  // 그제서야 안 된다는 걸 알았다.
  stopPolling();
  timer = setTimeout(refreshState, job.running ? 500 : HEARTBEAT);
}

//: 놀 때 서버가 살아 있는지 확인하는 주기. 로컬이라 부담이 없다.
const HEARTBEAT = 15000;

function stopPolling() { if (timer !== null) { clearTimeout(timer); timer = null; } }

function setStale(stale) {
  for (const id of ['odds-panel', 'examples-panel']) $(id).classList.toggle('stale', stale);
  $('stale-note').hidden = !stale;
}

// ---------------------------------------------------------------- 렌더
function renderCached(cached) {
  const box = $('coverage');
  box.hidden = false;
  const any = cached.some((c) => c.count > 0);
  box.innerHTML = any
    ? `받아둔 시세: ${cached.map((c) => `<b>${c.label}</b> ${c.count.toLocaleString()}개`).join(' · ')}
       — <b>지금 시세로 판단받기</b>를 누르세요.`
    : `아직 받아둔 시세가 없습니다. <b>지금 시세로 판단받기</b>를 누르면 업비트에서 받아옵니다 (처음 한 번은 몇 분 걸립니다).`;
}

function render(analysis) {
  renderVerdict(analysis);
  renderCoverage(analysis);
  renderOdds(analysis);
  if (analysis.odds && analysis.odds.length) {
    const first = analysis.odds.find((o) => o.samples >= analysis.minSamples) || analysis.odds[0];
    select(first.timeframe, first.horizon, analysis);
  } else {
    $('examples-panel').hidden = true;
  }
}

function renderVerdict(analysis) {
  const v = analysis.verdict;
  if (!v) return;
  const panel = $('verdict');
  panel.hidden = false;
  panel.className = `panel verdict ${v.buy ? 'buy' : 'hold'}`;
  $('verdict-headline').textContent = v.headline;
  $('verdict-time').textContent = analysis.updatedAt ? `${analysis.updatedAt} 기준` : '';
  $('verdict-reasons').innerHTML = v.reasons
    .map((r) => `<li>${r.replace(/[&<>]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}</li>`)
    .join('');
}

function renderCoverage(analysis) {
  const box = $('coverage');
  box.hidden = false;
  const spans = analysis.series
    .map((s) => `<b>${s.label}</b> ${s.count.toLocaleString()}개` + (s.gaps ? ` (끊긴 곳 ${s.gaps})` : ''))
    .join(' · ');
  const missing = (analysis.missing || []).length
    ? `<br><b class="warn">${analysis.missing.map((m) => m.label).join(', ')} 시세가 없어 빠졌습니다.</b>`
    : '';
  box.innerHTML = `${spans}${missing}<br>왕복 비용 <b>${pct(analysis.cost, 2)}</b> ·
    직전 <b>${analysis.oddsLength}개</b> 봉 기준 · 닮았다고 볼 기준 상관계수 <b>${analysis.similarity.toFixed(2)}</b>`;
}

function renderOdds(analysis) {
  const panel = $('odds-panel');
  const body = $('odds-body');
  panel.hidden = false;
  body.innerHTML = '';

  const rows = analysis.odds || [];
  if (!rows.length) {
    body.innerHTML = `<p class="footnote">닮은 과거 구간을 찾지 못했습니다.
      기준을 낮추거나(예: 0.7) 데이터를 더 모으세요.</p>`;
    return;
  }

  const groups = {};
  for (const row of rows) (groups[row.timeframe] ||= []).push(row);

  for (const [timeframe, group] of Object.entries(groups)) {
    const first = group[0];
    const section = document.createElement('div');
    section.className = 'odds-group';

    if (first.samples < analysis.minSamples) {
      section.innerHTML = `<h4>${first.timeframeLabel}</h4>
        <p class="footnote">닮은 구간이 ${first.samples}개뿐이라 확률을 말할 수 없습니다
        (최소 ${analysis.minSamples}개 필요).</p>`;
      body.appendChild(section);
      continue;
    }

    const warn = first.linearity >= 0.75
      ? `<p class="trend-warn">⚠ 지금 모양은 직선에 가깝습니다 (직선성 ${first.linearity.toFixed(2)}) —
         특이한 모양이 아니라 '추세 중'인 구간을 센 것에 가깝습니다.</p>` : '';

    section.innerHTML = `
      <h4>${first.timeframeLabel}
        <span class="hint">닮은 과거 구간 ${first.samples}개 · 유사도 ${first.minSimilarity.toFixed(2)} 이상</span>
      </h4>${warn}
      <div class="table-wrap"><table class="odds">
        <thead><tr>
          <th class="l">시간</th><th>올라 있을 확률</th><th>평소</th><th>차이</th>
          <th>불확실 범위</th><th>수수료까지 넘길 확률</th><th>평소</th>
        </tr></thead><tbody></tbody>
      </table></div>`;

    const tbody = section.querySelector('tbody');
    for (const row of group.sort((a, b) => a.horizon - b.horizon)) {
      const tr = document.createElement('tr');
      tr.dataset.key = `${row.timeframe}|${row.horizon}`;
      tr.className = row.tellsUsAnything ? 'informative' : '';
      tr.innerHTML = `
        <td class="l">${row.minutes}분 뒤</td>
        <td class="big">${pct(row.upRate)}<span class="sub2">${row.samples}개 중 ${row.up}개</span></td>
        <td class="dim">${pct(row.baseUp)}</td>
        <td class="${row.upEdge >= 0 ? 'pos' : 'neg'}">${signed(row.upEdge, 0)}</td>
        <td class="dim">${pct(row.ciLow)}~${pct(row.ciHigh)}${
          row.tellsUsAnything ? '' : '<span class="sub2">평소와 구분 안 됨</span>'}</td>
        <td class="big">${pct(row.beatRate)}</td>
        <td class="dim">${pct(row.baseBeat)}</td>`;
      tr.addEventListener('click', () => select(row.timeframe, row.horizon, analysis));
      tbody.appendChild(tr);
    }
    body.appendChild(section);
  }
  markSelected();
}

function markSelected() {
  const key = selected ? `${selected.timeframe}|${selected.horizon}` : null;
  for (const tr of document.querySelectorAll('#odds-body tbody tr')) {
    tr.classList.toggle('selected', tr.dataset.key === key);
  }
}

// ---------------------------------------------------------------- 실제 사례
async function select(timeframe, horizon, analysis) {
  selected = { timeframe, horizon };
  markSelected();
  try {
    const data = await api(
      `/api/examples?timeframe=${encodeURIComponent(timeframe)}&horizon=${horizon}`);
    drawExamples(data, analysis);
  } catch (err) { report(err); }
}

function drawExamples(data, analysis) {
  const panel = $('examples-panel');
  panel.hidden = false;
  const minutes = data.horizon * (parseInt(data.timeframe.replace('minute', ''), 10) || 1);
  $('examples-title').textContent =
    `${data.timeframeLabel} · 직전 ${data.length}개와 가장 닮았던 과거 — ${minutes}분 뒤 결과별`;
  $('examples-note').textContent =
    `지금 모양의 끝: ${data.queryAt} KST · 왕복 비용 ${pct(data.cost, 2)}`;

  for (const [id, list] of [['rose-list', data.rose], ['fell-list', data.fell]]) {
    const box = $(id);
    box.innerHTML = '';
    if (!list.length) {
      box.innerHTML = `<p class="footnote">해당하는 사례가 없습니다.</p>`;
      continue;
    }
    for (const example of list) box.appendChild(exampleCard(example, data));
  }
}

function exampleCard(example, data) {
  const card = document.createElement('div');
  card.className = 'example';
  const good = example.outcome > data.cost;
  card.innerHTML = `
    <div class="example-head">
      <span class="when">${example.at}</span>
      <span class="sim">유사도 ${example.similarity.toFixed(3)}</span>
      <span class="${good ? 'pos' : 'neg'} result">${signed(example.outcome)}</span>
    </div>
    <div class="example-charts">
      <div class="svg-box wide"><svg viewBox="0 0 400 120" preserveAspectRatio="none">
        ${overlay(example.shape, data.query)}
      </svg></div>
      <div class="svg-box narrow"><svg viewBox="0 0 120 120" preserveAspectRatio="none">
        ${afterPath(example.after, data.cost, good)}
      </svg></div>
    </div>`;
  return card;
}

function scaleTo(values, height, pad) {
  let low = Math.min(...values), high = Math.max(...values);
  if (!(high > low)) { high = low + 1; low -= 1; }
  const margin = (high - low) * 0.1;
  low -= margin; high += margin;
  return (v) => height - pad - ((v - low) / (high - low)) * (height - pad * 2);
}

function polyline(values, y, width, pad, color, stroke, alpha) {
  const x = (i) => pad + (i / Math.max(1, values.length - 1)) * (width - pad * 2);
  const points = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  return `<polyline points="${points}" fill="none" stroke="${color}" stroke-width="${stroke}"` +
         ` stroke-opacity="${alpha}" vector-effect="non-scaling-stroke"` +
         ` stroke-linejoin="round" stroke-linecap="round"/>`;
}

function overlay(shape, query) {
  // 그 당시 모양(회색)과 지금 모양(파랑)을 같은 자에 겹친다
  const y = scaleTo(shape.concat(query), 120, 8);
  return polyline(shape, y, 400, 8, '#8b93a3', 2.2, 0.95)
       + polyline(query, y, 400, 8, '#3d7eff', 1.6, 0.85);
}

function afterPath(after, cost, good) {
  const y = scaleTo(after.concat([0, cost, -cost]), 120, 8);
  const rule = (v, color) =>
    `<line x1="8" y1="${y(v).toFixed(1)}" x2="112" y2="${y(v).toFixed(1)}" stroke="${color}"` +
    ` stroke-width="1" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>`;
  return rule(0, '#3a4150') + rule(cost, '#26a17b')
       + polyline(after, y, 120, 8, good ? '#26a17b' : '#d0424f', 2, 1);
}

// ---------------------------------------------------------------- 조작
function settings() {
  return {
    market: $('in-market').value.trim().toUpperCase() || 'KRW-BTC',
    oddsLength: parseInt($('in-length').value, 10),
    similarity: parseFloat($('in-similarity').value),
    fee: parseFloat($('in-fee').value),
    slippage: parseFloat($('in-slippage').value),
  };
}

$('btn-scan').addEventListener('click', async () => {
  showError('');
  try { await post('/api/scan', settings()); refreshState(); }
  catch (err) { report(err); }
});

$('btn-live').addEventListener('click', () => runLive());

async function runLive() {
  showError('');
  try { await post('/api/live', settings()); refreshState(); }
  catch (err) { report(err); }
}

// 1분마다 자동 갱신. 새 봉이 생기는 주기가 1분이므로 그보다 자주 물어도 의미가 없다.
let auto = null;
$('in-auto').addEventListener('change', (e) => {
  if (auto !== null) { clearInterval(auto); auto = null; }
  if (e.target.checked) auto = setInterval(() => {
    if (!$('btn-live').disabled) runLive();
  }, 60000);
});

// ------------------------------------------------------ 앱처럼 설치하기
//
// 서비스 워커는 **보안 컨텍스트에서만** 등록된다. localhost와 https는 되고,
// 같은 와이파이의 http://192.168.x.x 는 안 된다. 안 되는 자리에서 굳이
// 시도하면 콘솔만 빨개지고 얻는 게 없으므로 아예 건너뛴다.
//
// 다행히 홈 화면 추가 자체는 서비스 워커가 없어도 된다. iOS는 index.html의
// meta 태그만 보고 주소창 없는 전체 화면으로 띄운다. 즉 LAN 주소에서도
// '앱처럼'은 되고, 다만 서버가 꺼졌을 때 대신 띄워 줄 화면이 없을 뿐이다.
if ('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register('/sw.js').catch(() => undefined);
}

const standalone = window.matchMedia('(display-mode: standalone)').matches
  || window.navigator.standalone === true;

let installPrompt = null;

function showInstallHint(how) {
  let dismissed = false;
  try { dismissed = localStorage.getItem('installHintDismissed') === '1'; }
  catch (err) { /* 읽을 수 없으면 그냥 보여준다 */ }
  if (standalone || dismissed) return;
  $('install-how').textContent = how;
  $('install-hint').hidden = false;
}

window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault();
  installPrompt = event;
  $('btn-install').hidden = false;
  showInstallHint('이 버튼을 누르면 홈 화면에 아이콘이 생깁니다.');
});

$('btn-install').addEventListener('click', async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice;
  installPrompt = null;
  $('install-hint').hidden = true;
});

$('btn-install-close').addEventListener('click', () => {
  $('install-hint').hidden = true;
  try { localStorage.setItem('installHintDismissed', '1'); } catch (err) { /* 비공개 모드 */ }
});

// iOS 사파리에는 설치 버튼이 없다(beforeinstallprompt를 안 쏜다).
// 공유 버튼을 눌러야 한다는 걸 아는 사람만 아는데, 모르면 영영 못 찾는다.
if (/iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)) {
  showInstallHint('아래 공유 버튼(□↑) → "홈 화면에 추가"를 누르면 앱처럼 열립니다.');
}

refreshState();
