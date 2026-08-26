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
let market = 'KRW-BTC';
let coinsDrawn = '';      // 이미 그린 종목 목록 (매번 다시 그리면 누르는 중에 사라진다)

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

  // 이 서버는 오류에 **반드시 이유를 붙여** JSON으로 답한다. 그러니 이유
  // 없는 오류가 왔다면 답한 쪽이 이 서버가 아니다 — 코드스페이스가 잠들어
  // 깃허브 프록시가 대신 답했거나, 로그인이 풀렸거나 하는 경우다.
  // 그때 "오류 404"라고만 띄우면 사용자는 뭘 해야 할지 알 수가 없다.
  const said = payload && payload.error;
  if (!response.ok && !said) {
    throw new Unreachable(`서버가 아닌 곳에서 ${response.status}가 왔습니다`);
  }
  if (response.status >= 500) {
    throw new Unreachable(`서버가 응답하지 못했습니다 (${response.status})`);
  }
  if (!response.ok) throw new Error(said);
  if (!payload) throw new Error(`${path} 응답을 읽지 못했습니다`);
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
//
// **다음 폴링은 무슨 일이 있어도 잡는다.**
//
// 예전에는 오류 종류를 보고 어떤 경우에만 다시 잡았다. 그래서 예상 못 한
// 오류가 한 번 나면 그 자리에서 폴링이 죽었고, 마지막 응답에서 잠갔던
// 단추가 영영 잠긴 채로 남았다. 화면이 통째로 굳어서, 새로고침 말고는
// 되살릴 방법이 없었다. 실제로 404 한 번에 그렇게 됐다.
async function refreshState() {
  let next = HEARTBEAT;
  try {
    next = applyState(await api('/api/state'));
  } catch (err) {
    report(err);
    // 서버가 답을 못 하는 동안 단추까지 잠겨 있으면 손쓸 방법이 없어진다.
    unlock();
    next = 5000;   // 다시 켜는 순간 알아서 살아나도록 계속 두드린다
  } finally {
    stopPolling();
    timer = setTimeout(refreshState, next);
  }
}

function unlock() {
  for (const id of ['btn-scan', 'btn-live']) $(id).disabled = false;
  $('btn-stop').hidden = true;
  $('progress').hidden = true;
}

function applyState(state) {
  setOffline(false);

  const job = state.job || {};
  if (state.markets) renderCoins(state.markets, state.market);
  if (state.periods) renderPeriods(state.periods);
  market = state.market;
  $('ticker-code').textContent = state.market;
  $('ticker-label').textContent = state.marketLabel || state.market;
  $('btn-scan').disabled = job.running;
  $('btn-live').disabled = job.running;
  $('btn-stop').hidden = !job.running;
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
  // 멈춰서, 컴퓨터가 꺼져도 화면은 멀쩡해 보였다.
  return job.running ? 500 : HEARTBEAT;
}

//: 놀 때 서버가 살아 있는지 확인하는 주기. 로컬이라 부담이 없다.
const HEARTBEAT = 15000;

function stopPolling() { if (timer !== null) { clearTimeout(timer); timer = null; } }

function setStale(stale) {
  for (const id of ['odds-panel', 'examples-panel']) $(id).classList.toggle('stale', stale);
  $('stale-note').hidden = !stale;
}

// ------------------------------------------------------------ 종목 고르기
function renderCoins(markets, current) {
  const box = $('coins');
  const signature = markets.map((m) => m.code).join(',');
  if (signature !== coinsDrawn) {
    coinsDrawn = signature;
    box.innerHTML = markets
      .map((m) => `<button type="button" class="coin" data-code="${m.code}">
                     <b>${m.label}</b><span>${m.code.replace('KRW-', '')}</span>
                   </button>`)
      .join('');
    for (const button of box.querySelectorAll('.coin')) {
      button.addEventListener('click', () => pickCoin(button.dataset.code));
    }
  }
  for (const button of box.querySelectorAll('.coin')) {
    button.classList.toggle('on', button.dataset.code === current);
  }
}

async function pickCoin(code) {
  if (code === market || $('btn-live').disabled) return;
  market = code;
  // 종목마다 시세도 결과도 따로다. 남아 있는 표를 그대로 두면
  // 비트코인 확률을 솔라나 것으로 읽게 된다.
  shownAnalysis = -1;
  selected = null;
  for (const id of ['verdict', 'odds-panel', 'examples-panel']) $(id).hidden = true;
  $('ticker-price').textContent = '—';
  $('ticker-change').textContent = '';
  renderCoins([...$('coins').querySelectorAll('.coin')]
    .map((b) => ({ code: b.dataset.code, label: b.querySelector('b').textContent })), code);
  refreshTicker();
  await runLive();
}

// -------------------------------------------------------- 얼마나 과거까지
//
// 예전에는 이 선택이 아예 없어서 화면 버튼이 **늘 30일치만** 요청했다.
// 명령줄로 8년치를 받아둔 사람이 화면에서 버튼을 누르면 그때부터 30일만
// 갱신됐고, 왜 숫자가 그것밖에 안 되는지 알 방법이 없었다.
let periodsDrawn = '';

function renderPeriods(periods) {
  const signature = periods.map((p) => p.count).join(',');
  if (signature === periodsDrawn) return;
  periodsDrawn = signature;
  const box = $('in-period');
  box.innerHTML = periods
    .map((p) => `<option value="${p.count}" data-note="${p.note}">${p.label}</option>`)
    .join('');
  box.addEventListener('change', showPeriodNote);
  showPeriodNote();
}

function showPeriodNote() {
  const picked = $('in-period').selectedOptions[0];
  $('period-note').textContent = picked ? picked.dataset.note : '';
}

// ------------------------------------------------------------ 지금 시세
const won = (x) => x >= 1000
  ? Math.round(x).toLocaleString('ko-KR')
  : x.toLocaleString('ko-KR', { maximumFractionDigits: 2 });

async function refreshTicker() {
  let data;
  try { data = await api(`/api/ticker?market=${encodeURIComponent(market)}`); }
  catch (err) { return; }   // 맨 위 숫자는 장식이다. 안 나온다고 화면을 빨갛게 만들지 않는다
  if (!data.ok || data.market !== market) {
    $('ticker-price').textContent = '—';
    $('ticker-change').textContent = '';
    $('ticker').className = 'ticker';
    return;
  }
  $('ticker-label').textContent = data.label;
  $('ticker-code').textContent = data.market;
  $('ticker-price').textContent = `${won(data.price)}원`;
  const rate = data.changeRate;
  const arrow = rate > 0 ? '▲' : rate < 0 ? '▼' : '·';
  $('ticker-change').textContent =
    `${arrow} ${won(Math.abs(data.changePrice))}  ${signed(Math.abs(rate) * (rate < 0 ? -1 : 1))}`;
  // 업비트와 같은 색: 오르면 빨강, 내리면 파랑.
  $('ticker').className = `ticker ${data.direction}`;
}

// ---------------------------------------------------------------- 렌더
function renderCached(cached) {
  const box = $('coverage');
  box.hidden = false;
  const any = cached.some((c) => c.count > 0);
  if (!any) {
    box.innerHTML = `아직 받아둔 시세가 없습니다. <b>지금 시세로 판단받기</b>를 누르면
                     업비트에서 받아옵니다 (처음 한 번은 몇 분 걸립니다).`;
    return;
  }
  // 개수만 보여주면 그게 많은 건지 적은 건지 알 수가 없다. 기간을 같이 적는다.
  const rows = cached.map((c) => {
    const when = c.from && c.to ? `<span class="dim">${c.from} ~ ${c.to}</span>` : '';
    return `<div class="cached-row"><b>${c.label}</b>
            <span class="cached-count">${c.count.toLocaleString()}개</span> ${when}</div>`;
  }).join('');
  box.innerHTML = `<div class="cached-head">받아둔 시세</div>${rows}
    <div class="dim cached-foot">더 긴 과거를 보려면 위에서 <b>얼마나 과거까지</b>를 늘리고
      <b>지금 시세로 판단받기</b>를 누르세요.</div>`;
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

// 서버가 보낸 문장에서 **강조**만 굵게 만든다. 이스케이프를 **먼저** 하므로
// 서버 문장에 태그가 섞여 있어도 태그로 살아나지 않는다.
const emphasise = (text) => text
  .replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))
  .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');

function renderVerdict(analysis) {
  const v = analysis.verdict;
  if (!v) return;
  const panel = $('verdict');
  panel.hidden = false;
  panel.className = `panel verdict ${v.buy ? 'buy' : 'hold'}`;
  $('verdict-headline').textContent = v.headline;
  $('verdict-time').textContent = analysis.updatedAt ? `${analysis.updatedAt} 기준` : '';
  $('verdict-reasons').innerHTML = v.reasons.map((r) => `<li>${emphasise(r)}</li>`).join('');
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
  // 그 당시 모양(흐린 초록)과 지금 모양(밝은 초록)을 같은 자에 겹친다
  const y = scaleTo(shape.concat(query), 120, 8);
  return polyline(shape, y, 400, 8, '#3f7a5c', 2.2, 0.95)
       + polyline(query, y, 400, 8, '#00ff66', 1.6, 0.9);
}

function afterPath(after, cost, good) {
  const y = scaleTo(after.concat([0, cost, -cost]), 120, 8);
  const rule = (v, color) =>
    `<line x1="8" y1="${y(v).toFixed(1)}" x2="112" y2="${y(v).toFixed(1)}" stroke="${color}"` +
    ` stroke-width="1" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>`;
  return rule(0, '#1b3a2a') + rule(cost, '#00ff9c')
       + polyline(after, y, 120, 8, good ? '#00ff9c' : '#ff4d5e', 2, 1);
}

// ---------------------------------------------------------------- 조작
function settings() {
  return {
    market,
    count: parseInt($('in-period').value, 10) || undefined,
    oddsLength: parseInt($('in-length').value, 10),
    similarity: parseFloat($('in-similarity').value),
    fee: parseFloat($('in-fee').value),
    slippage: parseFloat($('in-slippage').value),
  };
}

$('btn-scan').addEventListener('click', () => run('/api/scan'));
$('btn-live').addEventListener('click', () => runLive());

$('btn-stop').addEventListener('click', async () => {
  $('btn-stop').disabled = true;
  try { await post('/api/stop', {}); } catch (err) { report(err); }
  $('btn-stop').disabled = false;
  refreshState();
});

async function run(path) {
  showError('');
  try {
    const answer = await post(path, settings());
    // 시작 안 됐는데 아무 말도 안 하면, 눌러도 반응이 없는 것처럼 보인다.
    if (answer && answer.started === false) {
      showError('이미 하고 있습니다. 끝나기를 기다리거나 멈추기를 누르세요.');
    }
  } catch (err) { report(err); }
  refreshState();
}

const runLive = () => run('/api/live');

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
  // 할 말이 없으면 띄우지 않는다. "앱처럼 쓰기:"만 덩그러니 뜬 적이 있다.
  if (!how) return;
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
//
// 그런데 그 버튼의 자리가 기기마다 다르다. 아이패드는 **위쪽 주소창 옆**,
// 아이폰은 **아래쪽 가운데**다. "아래 공유 버튼"이라고만 적어두면
// 아이패드 사용자는 없는 곳을 쳐다보게 된다.
const iphone = /iPhone|iPod/.test(navigator.userAgent);
const ipad = /iPad/.test(navigator.userAgent)
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

if (iphone || ipad) {
  const where = iphone ? '화면 아래 가운데' : '오른쪽 위 주소창 옆';
  showInstallHint(
    `${where}의 공유 버튼(□↑)을 누르고, 목록을 내려 "홈 화면에 추가"를 고르세요.`
  );
}

// 맨 위 시세는 상태 폴링과 따로 돈다. 계산이 몇십 초 걸려도 시세는 계속
// 살아 있어야 하고, 반대로 시세를 못 받았다고 계산이 멈추면 안 된다.
refreshTicker();
setInterval(refreshTicker, 5000);
// 탭을 다시 보면 곧바로 맞춘다 — 안 그러면 5초 동안 옛 숫자를 보게 된다.
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) refreshTicker();
});

refreshState();

// ------------------------------------------------------------ 떨어지는 글자
//
// 화면 뒤에 아주 옅게 깔리는 장식이다. 장식이 본업을 방해하면 안 되므로
// 세 가지를 지킨다.
//
//   1. 탭이 안 보이면 멈춘다. 아이패드에서 배터리를 계속 먹으면 곤란하다.
//   2. 움직임을 불편해하는 사람에게는 아예 안 그린다.
//   3. 캔버스 하나에 열별로만 그린다 — 폴링이 0.5초마다 도는 화면이라
//      여기서 프레임을 잡아먹으면 진행 막대가 끊긴다.
(function rain() {
  const canvas = $('rain');
  if (!canvas || !canvas.getContext) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  const ctx = canvas.getContext('2d', { alpha: true });
  const GLYPHS = 'ｦｱｳｴｵｶｷｹｺｻｼｽｾｿﾀﾂﾃﾅﾆﾇﾈﾊﾋﾎﾏﾐﾑﾒﾓﾔﾕﾗﾘﾜ0123456789';
  const SIZE = 16;
  let columns = [];
  let width = 0;
  let height = 0;

  function resize() {
    // 화면 배율만큼만 키운다. 예전에 캔버스가 다시 그릴 때마다 계속
    // 커지던 버그가 있었으므로, 매번 CSS 크기에서 새로 계산한다.
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    width = canvas.clientWidth;
    height = canvas.clientHeight;
    canvas.width = Math.floor(width * ratio);
    canvas.height = Math.floor(height * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.font = `${SIZE}px ${'ui-monospace, monospace'}`;
    const count = Math.ceil(width / SIZE);
    columns = Array.from({ length: count }, () => Math.random() * -height);
  }

  function draw() {
    // 지우지 않고 옅은 검정을 덮는다 — 지나간 글자가 서서히 사라진다.
    ctx.fillStyle = 'rgba(5, 8, 7, 0.09)';
    ctx.fillRect(0, 0, width, height);
    for (let i = 0; i < columns.length; i += 1) {
      const y = columns[i];
      const glyph = GLYPHS[(Math.random() * GLYPHS.length) | 0];
      ctx.fillStyle = y < SIZE * 2 ? '#c8ffdd' : '#00ff66';   // 맨 앞 글자만 밝게
      ctx.fillText(glyph, i * SIZE, y);
      columns[i] = y > height + Math.random() * 400 ? 0 : y + SIZE;
    }
  }

  let ticking = null;
  function start() {
    if (ticking === null) ticking = setInterval(draw, 70);
  }
  function stop() {
    if (ticking !== null) { clearInterval(ticking); ticking = null; }
  }

  resize();
  start();
  window.addEventListener('resize', () => { resize(); });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stop(); else start();
  });
})();
