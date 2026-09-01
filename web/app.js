// 화면. 계산은 워커가 하고, 여기서는 그리기만 한다.
//
// 그림은 전부 SVG로 그린다. 이전 판(canvas)에서는 화면 배율이 2 이상인
// 기기에서 다시 그릴 때마다 캔버스가 배율만큼 계속 커지는 버그가 있었다.
// SVG는 viewBox가 좌표계를 고정하고 크기는 CSS가 정하므로 그 문제가 아예
// 생기지 않는다.

import { MARKETS, marketLabel } from './core/models.js';
import { VERSION } from './version.js';
import { DEFAULT_PERIOD, MAX_BARS, PERIODS } from './core/analysis.js';
import {
  API_BASE, ENDPOINTS, PAGE, PER_SECOND, TO_FORMATS,
} from './core/upbit.js';

const $ = (id) => document.getElementById(id);
const pct = (x, d = 0) => `${(x * 100).toFixed(d)}%`;
const signed = (x, d = 2) => `${x >= 0 ? '+' : ''}${(x * 100).toFixed(d)}%`;

let selected = null;      // {timeframe, horizon}
let market = 'KRW-BTC';
let busy = false;
let lastAnalysis = null;
let aheadPick = null;
let theoryPick = null;

// ---------------------------------------------------------------- 워커
//
// 멈추기는 **워커를 통째로 끝내는 것**으로 한다. 메시지로 "그만"을 보내도,
// 계산이 도는 동안에는 워커가 그 메시지를 읽을 틈이 없어서 다 끝난 뒤에야
// 멈춘다 — 그건 멈추기가 아니다. 받다가 끊겨도 받은 만큼은 이미 저장돼
// 있으므로(core/data.js의 onBatch) 잃는 게 없다.
let worker = null;

/** 워커가 지금 결과를 들고 있는가. 멈추기를 누르면 워커째 사라진다. */
let workerHasResult = false;

function spawn() {
  // 모듈 워커를 못 만드는 브라우저가 있다(사파리 15 미만). 그때 그냥
  // 터지면 화면이 아무 말도 없이 멈춘 것처럼 보이므로, 무엇 때문인지
  // 적어 준다 — 고칠 수 있는 문제이기 때문이다(브라우저를 올리면 된다).
  try {
    worker = new Worker(new URL('./worker.js', import.meta.url), { type: 'module' });
  } catch {
    worker = null;
    finish();
    showError('이 브라우저에서는 계산을 시작할 수 없습니다. '
      + '브라우저를 최신으로 올리거나 다른 브라우저에서 열어 주세요.');
    return null;
  }
  worker.onmessage = (event) => handle(event.data ?? {});
  worker.onerror = (event) => {
    event.preventDefault();
    workerHasResult = false;
    finish();
    showError(`계산이 실패했습니다: ${event.message || '알 수 없는 오류'}`);
  };
  return worker;
}

function send(message) {
  if (!worker) spawn();
  if (!worker) return;
  worker.postMessage(message);
}

function handle(message) {
  switch (message.type) {
    case 'progress':
      $('job').textContent = message.message || '';
      showProgress(message.done, message.total);
      break;
    case 'warn':
      showError(message.message);
      break;
    case 'summary':
      if (message.market === market) renderCached(message.cached);
      break;
    case 'ticker':
      if (message.market === market && message.rows.length) showTicker(message.rows);
      break;
    case 'blocked':
      // 새 시세를 못 받았다. 결과는 나올 수 있지만(받아둔 것으로) 그건
      // '지금'이 아니다. 그 사실이 결과보다 먼저 보여야 한다.
      setBlocked(message.kind);
      if (!message.stale) finish();
      // 예전에는 여기서 진단을 자동으로 돌렸다. 무엇 때문에 막히는지 몰랐을
      // 때는 그게 유일한 단서였다. 지금은 클라이언트가 이미 정확히 가르므로
      // (막힘 / 못 닿음 / 끊김), 여덟 번을 더 두드릴 이유가 없다 —
      // 하필 두드리면 안 되는 때가 바로 막혀 있을 때다. 단추는 남는다.
      break;
    case 'done':
      finish();
      $('job').textContent = message.stale
        ? '받아둔 시세로 계산했습니다 (새 시세는 못 받았습니다)'
        : '계산을 마쳤습니다';
      lastAnalysis = message.analysis;
      workerHasResult = true;
      selected = null;
      render(message.analysis);
      break;
    case 'examples':
      drawExamples(message.examples, lastAnalysis);
      break;
    case 'error':
      workerHasResult = false;
      finish();
      reportWorkerError(message);
      break;
    default:
      break;
  }
}

function start(text) {
  busy = true;
  showError('');
  setBlocked(null);
  $('job').textContent = text;
  for (const id of ['btn-scan', 'btn-live']) $(id).disabled = true;
  $('btn-stop').hidden = false;
  setStale(lastAnalysis !== null);
}

/** 새 판으로 갈아탈 준비가 됐지만 받는 중이라 미뤄 둔 것. */
let pendingSwap = null;

function finish() {
  busy = false;
  for (const id of ['btn-scan', 'btn-live']) $(id).disabled = false;
  $('btn-stop').hidden = true;
  $('progress').hidden = true;
  setStale(false);
  // 미뤄 둔 새 판 갈아타기가 있으면 **정리를 다 마친 뒤에** 한다.
  if (pendingSwap) {
    const swap = pendingSwap;
    pendingSwap = null;
    swap();
  }
}

function showProgress(done, total) {
  const bar = $('progress');
  if (total > 0) {
    bar.hidden = false;
    $('progress-bar').style.width = `${Math.min(100, (done / total) * 100)}%`;
  } else {
    bar.hidden = true;
  }
}

function showError(message) {
  const box = $('error');
  box.hidden = !message;
  box.textContent = message || '';
}

function setStale(stale) {
  for (const id of ['odds-panel', 'examples-panel']) $(id).classList.toggle('stale', stale);
  $('stale-note').hidden = !stale;
}

/**
 * 업비트에 닿지 못했을 때.
 *
 * 이 앱에는 서버가 없다. 그래서 잘못될 수 있는 곳도 하나뿐이다 —
 * **브라우저에서 업비트로 가는 길**. 그 길이 막혔을 때 "실패"라고만 하면
 * 사용자는 인터넷이 끊긴 건지, 업비트가 막은 건지, 잠깐 점검 중인지
 * 알 수가 없고, 그러면 다시 시도할지 포기할지도 정할 수 없다.
 */
function setBlocked(kind) {
  const box = $('blocked');
  box.hidden = kind === null;
  if (kind === null) return;
  const said = {
    offline: `<b>인터넷이 끊겨 있습니다.</b>
      연결을 확인하고 다시 눌러 주세요.
      <span class="dim">이미 받아둔 시세로는 <b>받아둔 시세로 다시 계산</b>이 그대로 됩니다.</span>`,
    blocked: `<b>업비트에 닿지 못했습니다.</b>
      인터넷은 되는데 업비트 쪽으로만 못 가고 있습니다.
      <span class="dim">업비트가 점검 중이거나, 쓰고 계신 망(회사·학교 와이파이, 일부 VPN)이
      막고 있을 수 있습니다. 다른 망에서 한 번 시도해 보세요.
      이미 받아둔 시세로는 <b>받아둔 시세로 다시 계산</b>이 그대로 됩니다.</span>`,
    stalled: `<b>받다가 중간에 막혔습니다.</b>
      업비트에서 <b>받기는 받았는데</b> 그 뒤로 더 못 받고 있습니다 — 길이 막힌 게
      아니라 받는 도중에 걸린 것입니다.
      <span class="dim">받은 만큼은 이미 저장돼 있습니다. <b>지금 시세로 판단받기</b>를
      다시 누르면 <b>이어서</b> 받습니다 (처음부터 다시 받지 않습니다).
      몇 번 나눠 누르셔도 됩니다 — 누를 때마다 그만큼씩 쌓입니다.</span>`,
    rate: `<b>업비트 요청 한도를 넘었습니다.</b>
      잠시 뒤에 다시 눌러 주세요.
      <span class="dim">아주 긴 과거를 한꺼번에 받을 때 생깁니다.
      <b>얼마나 과거까지</b>를 줄이면 덜 생깁니다.</span>`,
    server: `<b>업비트 쪽에서 오류가 왔습니다.</b>
      우리가 고칠 수 있는 문제가 아닙니다. 잠시 뒤에 다시 눌러 주세요.`,
    throttled: `<b>업비트가 지금 우리 요청을 막고 있습니다.</b>
      업비트까지는 <b>갔고 답도 왔습니다</b>. 다만 그 답에 브라우저가 요구하는
      허용 표시가 없어서 읽을 수가 없습니다 — 거절당했을 때 그렇습니다.
      <span class="dim">요청이 잦아서 잠시 막힌 것일 가능성이 큽니다.
      <b>10분쯤 뒤에 다시</b> 눌러 주세요. 받아둔 만큼은 그대로 있고, 다시 누르면
      이어서 받습니다. 휴대폰 데이터(5G)는 여러 사람이 한 주소를 나눠 쓰기 때문에
      더 자주 걸립니다 — <b>와이파이에서 해 보시면</b> 달라질 수 있습니다.</span>`,
    empty: `<b>업비트가 답은 했는데 과거 봉을 주지 않았습니다.</b>
      길은 뚫려 있고 지금 시세도 받아집니다. 과거를 달라는 요청에만
      빈 답이 옵니다 — 보내는 방법을 아홉 가지로 바꿔 가며 다 시도했습니다.
      <span class="dim">아래 <b>업비트 연결 진단</b>을 눌러 결과를 보내 주시면
      원인을 정확히 짚을 수 있습니다. 받아둔 시세로는
      <b>받아둔 시세로 다시 계산</b>이 그대로 됩니다.</span>`,
  }[kind];
  box.innerHTML = said ?? '';
  box.hidden = !said;
}

function reportWorkerError(message) {
  if (['offline', 'blocked', 'stalled', 'rate', 'server', 'empty', 'throttled']
    .includes(message.kind)) {
    setBlocked(message.kind);
    showError('');
    return;
  }
  setBlocked(null);
  showError(message.message);
}

// ------------------------------------------------------------ 종목 고르기
function renderCoins() {
  const box = $('coins');
  if (!box.children.length) {
    box.innerHTML = Object.entries(MARKETS)
      .map(([code, label]) => `<button type="button" class="coin" data-code="${code}">
                                 <b>${label}</b><span>${code.replace('KRW-', '')}</span>
                               </button>`)
      .join('');
    for (const button of box.querySelectorAll('.coin')) {
      button.addEventListener('click', () => pickCoin(button.dataset.code));
    }
  }
  for (const button of box.querySelectorAll('.coin')) {
    button.classList.toggle('on', button.dataset.code === market);
  }
}

async function pickCoin(code) {
  if (code === market || busy) return;
  market = code;
  // 종목마다 시세도 결과도 따로다. 남아 있는 표를 그대로 두면
  // 비트코인 확률을 솔라나 것으로 읽게 된다.
  lastAnalysis = null;
  workerHasResult = false;
  selected = null;
  aheadPick = null;
  theoryPick = null;
  for (const id of ['verdict', 'odds-panel', 'examples-panel', 'ahead-panel',
    'levels-panel', 'theory-panel']) $(id).hidden = true;
  $('ticker-price').textContent = '—';
  $('ticker-change').textContent = '';
  $('ticker-label').textContent = marketLabel(code);
  $('ticker-code').textContent = code;
  renderCoins();
  send({ type: 'summary', market });
  refreshTicker();
  runLive();
}

// -------------------------------------------------------- 얼마나 과거까지
function renderPeriods() {
  const box = $('in-period');
  box.innerHTML = PERIODS
    .map((p) => `<option value="${p.count}">${p.label}</option>`).join('');
  // 첫 번째(가장 짧은 것)가 아니라 정해 둔 기본값을 고른다. 이유는
  // DEFAULT_PERIOD 옆에 적어 뒀다.
  box.value = String(DEFAULT_PERIOD);
  box.addEventListener('change', showPeriodNote);
  showPeriodNote();
}

function showPeriodNote() {
  // 처음 받을 때 얼마나 걸릴지 미리 말해 준다. 4년치는 1만 6천 번을
  // 받아야 해서 한 시간 가까이 걸린다 — 눌러 놓고 기다리다 포기하지 않도록.
  const count = parseInt($('in-period').value, 10) || 0;
  // **1분봉만 받는다.** 3·5분봉은 그걸 묶어서 만드므로 요청이 안 든다.
  const requests = Math.ceil(count / PAGE);
  const minutes = requests / PER_SECOND / 60;
  const guess = minutes < 1
    ? '1분 안'
    : minutes < 60
      ? `약 ${Math.round(minutes)}분`
      : `약 ${(minutes / 60).toFixed(1)}시간`;
  $('period-note').textContent = `(처음 받을 때 소요시간 ${guess})`;
}

// ------------------------------------------------------------ 돈으로 보기
//
// "+0.02%"는 아무 느낌이 없다. "100만원에 +200원"은 바로 온다. 그리고
// 수수료가 왜 문제인지도 그제서야 보인다 — 0.14%면 100만원에 1,400원이고,
// 그건 20분 동안 벌 수 있는 돈보다 크다.
function amount() {
  const value = parseFloat($('in-amount').value);
  return Number.isFinite(value) && value > 0 ? value : 0;
}

/** 손익 금액. 부호를 꼭 붙인다 — 0원 근처에서 방향이 헷갈리면 안 된다. */
function cash(value) {
  const rounded = Math.round(value);
  const sign = rounded > 0 ? '+' : rounded < 0 ? '−' : '';
  return `${sign}${Math.abs(rounded).toLocaleString('ko-KR')}원`;
}

// ------------------------------------------------------------ 지금 시세
const won = (x) => (x >= 1000
  ? Math.round(x).toLocaleString('ko-KR')
  : x.toLocaleString('ko-KR', { maximumFractionDigits: 2 }));

/**
 * 맨 위 시세. **워커를 거쳐서 받는다.**
 *
 * 예전에는 여기 UpbitClient가 따로 있었다. 그러면 속도 제한기가 둘이 되어
 * 서로를 모르고, 5초마다 부르는 이 요청이 내려받기 위에 얹혔다. 아무것도
 * 안 하고 앱만 켜 둬도 시간당 720번이 나갔고, 막혀 있는 동안에도 계속
 * 두드려서 회복을 방해했다. 업비트로 나가는 길은 하나여야 한다.
 */
function refreshTicker() {
  if (document.hidden) return;   // 안 보고 있으면 묻지 않는다
  // 내려받는 중에도 묻는다. 같은 예산 안에서 줄을 서므로 초당 회수를
  // 넘기지 않고, 20초에 한 번이라 받는 속도에 사실상 영향이 없다.
  send({ type: 'ticker', market });
}

function showTicker(rows) {
  const data = rows.find((r) => r.market === market);
  if (!data) return;
  $('ticker-label').textContent = marketLabel(data.market);
  $('ticker-code').textContent = data.market;
  $('ticker-price').textContent = `${won(data.price)}원`;
  const rate = data.changeRate;
  const arrow = rate > 0 ? '▲' : rate < 0 ? '▼' : '·';
  $('ticker-change').textContent =
    `${arrow} ${won(Math.abs(data.changePrice))}  ${signed(rate)}`;
  // 업비트와 같은 색: 오르면 빨강, 내리면 파랑.
  $('ticker').className = `ticker ${rate > 0 ? 'up' : rate < 0 ? 'down' : 'flat'}`;
}

// ---------------------------------------------------------------- 렌더
/** ISO 시각을 "2026-08-04 15:32"로. 초와 T는 읽는 데 방해만 된다. */
function when(iso) {
  return iso ? iso.replace('T', ' ').slice(0, 16) : '';
}

/** 며칠치인지. 개수만 보면 그게 긴 건지 짧은 건지 감이 안 온다. */
function howLong(fromIso, toIso) {
  if (!fromIso || !toIso) return '';
  const days = (Date.parse(toIso) - Date.parse(fromIso)) / 86400000;
  if (days < 1) return `${Math.round(days * 24)}시간치`;
  if (days < 400) return `${Math.round(days)}일치`;
  return `${(days / 365.25).toFixed(1)}년치`;
}

/**
 * 받아둔 시세를 지우는 단추.
 *
 * **두 화면에 다 있어야 한다.** 예전에는 받기 전 화면에만 있었는데, 계산이
 * 끝나면 같은 자리를 '무엇으로 계산했나'가 덮어써서 단추가 사라졌다.
 * 정작 무엇을 갖고 있는지 보고 나서 지우고 싶어지는데, 그때 없어진다.
 */
const FORGET_BUTTON = '<button type="button" id="btn-forget" class="linky">받아둔 시세 지우기</button>';

function wireForget() {
  const button = $('btn-forget');
  if (!button) return;
  button.addEventListener('click', () => {
    if (busy) return;
    send({ type: 'forget', market });
  });
}

function renderCached(cached) {
  const box = $('coverage');
  box.hidden = false;
  const any = cached.some((c) => c.count > 0);
  if (!any) {
    box.innerHTML = `<div class="section-title">받아둔 시세</div>
      <p class="note-line">아직 받아둔 시세가 없습니다. <b>지금 시세로 판단받기</b>를 누르면
      업비트에서 받아옵니다.</p>`;
    return;
  }
  const rows = cached.map((c) => {
    if (!c.count) {
      return `<tr><th>${c.label}</th><td class="num dim">없음</td>
              <td class="dim" colspan="2">아직 안 받았습니다</td></tr>`;
    }
    // **언제부터 언제까지인지**를 반드시 보여준다. 개수만 적으면 그게
    // 어제 하루치인지 4년치인지 알 수가 없다.
    return `<tr>
      <th>${c.label}</th>
      <td class="num">${c.count.toLocaleString()}개</td>
      <td class="num dim">${howLong(c.from, c.to)}</td>
      <td class="num span">${when(c.from)} <span class="dim">~</span> ${when(c.to)}</td>
    </tr>`;
  }).join('');
  box.innerHTML = `<div class="section-title">받아둔 시세</div>
    <p class="keep"><b>한 번 받은 과거는 다시 받지 않습니다.</b>
      지나간 봉은 변하지 않으니 받아 둘 필요도 한 번뿐입니다. 이 기기에 저장돼 있어
      앱을 껐다 켜도, 중간에 끊겨도 그대로 남습니다 —
      <b>다시 누르면 멈춘 자리에서 이어서</b> 받습니다.
      <span class="dim">받는 건 1분봉 하나뿐입니다. 3분봉·5분봉은 그걸 묶어서
      만들기 때문에 따로 받지 않습니다.</span></p>
    <div class="table-wrap"><table class="cached">
      <thead><tr><th>봉</th><th>개수</th><th>기간</th><th>언제부터 언제까지 (UTC)</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="note-line">더 긴 과거를 보려면 위에서 <b>얼마나 과거의 데이터와 비교하나요</b>를
      늘리고 <b>지금 시세로 판단받기</b>를 누르세요.
      ${FORGET_BUTTON}</p>`;
  wireForget();
}

function render(analysis) {
  renderVerdict(analysis);
  renderCoverage(analysis);
  renderAhead(analysis);
  renderLevels(analysis);
  renderTheories(analysis);
  renderOdds(analysis);
  if (analysis.odds && analysis.odds.length) {
    const first = analysis.odds.find((o) => o.samples >= analysis.minSamples) || analysis.odds[0];
    select(first.timeframe, first.horizon);
  } else {
    $('examples-panel').hidden = true;
  }
}

// 문장에서 **강조**만 굵게 만든다. 이스케이프를 **먼저** 하므로 문장에
// 태그가 섞여 있어도 태그로 살아나지 않는다.
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
  const spans = analysis.series.map((s) => `<tr>
      <th>${s.label}</th>
      <td class="num">${s.count.toLocaleString()}개</td>
      <td class="num dim">${howLong(s.from, s.to)}</td>
      <td class="num span">${when(s.from)} <span class="dim">~</span> ${when(s.to)}</td>
      <td class="dim">${s.gaps ? `끊긴 곳 ${s.gaps}` : ''}</td>
    </tr>`).join('');
  const missing = (analysis.missing || []).length
    ? `<p class="note-line warn">${analysis.missing.map((m) => m.label).join(', ')} 시세가 없어 빠졌습니다.</p>`
    : '';
  box.innerHTML = `<div class="section-title">무엇으로 계산했나</div>
    <p class="keep"><b>여기 있는 과거는 다시 받지 않습니다.</b>
      지나간 봉은 변하지 않으니 받아 둘 필요도 한 번뿐입니다. 이 기기에 저장돼 있어
      앱을 껐다 켜도, 중간에 끊겨도 그대로 남습니다 —
      <b>다시 누르면 멈춘 자리에서 이어서</b> 받습니다.</p>
    <div class="table-wrap"><table class="cached">
      <thead><tr><th>봉</th><th>개수</th><th>기간</th>
        <th>언제부터 언제까지 (UTC)</th><th></th></tr></thead>
      <tbody>${spans}</tbody></table></div>${missing}
    <p class="note-line">왕복 비용 <b>${pct(analysis.cost, 2)}</b> ·
      직전 <b>${analysis.oddsLength}개</b> 봉 기준 ·
      닮았다고 볼 기준 상관계수 <b>${analysis.similarity.toFixed(2)}</b>
      ${FORGET_BUTTON}</p>`;
  wireForget();
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

  for (const group of Object.values(groups)) {
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
          <th class="l">시간</th><th>올라 있을 확률</th><th>차이</th>
          <th>불확실 범위</th><th>수수료까지 넘길 확률</th>
          <th>넣었다면</th>
        </tr></thead><tbody></tbody>
      </table></div>`;

    const tbody = section.querySelector('tbody');
    for (const row of group.sort((a, b) => a.horizon - b.horizon)) {
      const tr = document.createElement('tr');
      tr.dataset.key = `${row.timeframe}|${row.horizon}`;
      tr.className = row.tellsUsAnything ? 'informative' : '';
      tr.innerHTML = `
        <td class="l">${row.minutes}분 뒤</td>
        <td class="big">${pct(row.upRate)}<span class="sub2">${row.samples}개 중 ${row.up}개 ·
          평소 ${pct(row.baseUp)}</span></td>
        <td class="${row.upEdge >= 0 ? 'pos' : 'neg'}">${signed(row.upEdge, 0)}</td>
        <td class="dim">${pct(row.ciLow)}~${pct(row.ciHigh)}${
  row.tellsUsAnything ? '' : '<span class="sub2">평소와 구분 안 됨</span>'}</td>
        <td class="big">${pct(row.beatRate)}<span class="sub2">평소 ${pct(row.baseBeat)}</span></td>
        ${moneyCell(row, analysis)}`;
      tr.addEventListener('click', () => select(row.timeframe, row.horizon));
      tbody.appendChild(tr);
    }
    body.appendChild(section);
  }
  markSelected();
}

/** 중앙 수익을 금액으로. **수수료를 뺀 뒤**의 숫자여야 의미가 있다. */
function moneyCell(row, analysis) {
  const stake = amount();
  if (!stake) return '<td class="dim money">—</td>';
  const net = row.medianReturn - (analysis.cost || 0);
  // 값이 오르내린 결과이므로 **업비트와 같은 색**(오르면 빨강, 내리면 파랑)을
  // 쓴다. 초록·빨강은 '좋다/나쁘다'를 뜻하는 자리(초과 승률 같은)에만 남긴다 —
  // --up과 --bad가 같은 빨강이라, 두 뜻을 같은 화면에서 섞으면 빨간 숫자가
  // 무슨 뜻인지 원리적으로 알 수 없게 된다.
  return `<td class="big money ${net >= 0 ? 'up' : 'down'}">${cash(stake * net)}
    <span class="sub2">중앙 ${signed(row.medianReturn, 3)}</span></td>`;
}

function markSelected() {
  const key = selected ? `${selected.timeframe}|${selected.horizon}` : null;
  for (const tr of document.querySelectorAll('#odds-body tbody tr')) {
    tr.classList.toggle('selected', tr.dataset.key === key);
  }
}

// ---------------------------------------------------------------- 실제 사례
function select(timeframe, horizon) {
  selected = { timeframe, horizon };
  markSelected();
  // 멈추기를 누르면 워커를 통째로 끝낸다. 그때 표는 그대로 남아 있지만
  // 사례를 그릴 재료(찾아둔 과거 구간)는 워커와 함께 사라졌다. 그냥
  // 아무 일도 안 일어나면 사용자는 눌러도 반응이 없다고 여긴다.
  if (!workerHasResult) {
    $('examples-panel').hidden = false;
    $('examples-title').textContent = '실제 사례';
    $('examples-note').textContent = '';
    for (const id of ['rose-list', 'fell-list']) {
      $(id).innerHTML = '<p class="footnote">멈춘 뒤라 사례를 다시 그리려면 '
        + '<b>받아둔 시세로 다시 계산</b>을 눌러 주세요.</p>';
    }
    return;
  }
  send({ type: 'examples', timeframe, horizon });
}

function drawExamples(data, analysis) {
  if (!analysis) return;
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
      box.innerHTML = '<p class="footnote">해당하는 사례가 없습니다.</p>';
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
      <!-- 머리말('올랐던 사례')이 업비트 색인데 이 숫자만 초록·빨강이면,
           같은 사건에 색이 둘이 된다. 실제로 빨간 머리말 아래 초록 숫자가
           떠 있었다. 값의 움직임이므로 머리말과 같은 색을 쓴다. -->
      <span class="${example.outcome >= 0 ? 'up' : 'down'} result">${signed(example.outcome)}</span>
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
  let low = Math.min(...values);
  let high = Math.max(...values);
  if (!(high > low)) { high = low + 1; low -= 1; }
  // 위아래 여백. 넉넉히 두면 선이 상자 가운데 작게 눌려 보인다 —
  // 안 그래도 1분봉 움직임은 0.03% 남짓이라 눌릴 여유가 없다.
  const margin = (high - low) * 0.06;
  low -= margin;
  high += margin;
  return (v) => height - pad - ((v - low) / (high - low)) * (height - pad * 2);
}

function polyline(values, y, width, pad, color, stroke, alpha) {
  const x = (i) => pad + (i / Math.max(1, values.length - 1)) * (width - pad * 2);
  const points = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  return `<polyline points="${points}" fill="none" stroke="${color}" stroke-width="${stroke}"`
       + ` stroke-opacity="${alpha}" vector-effect="non-scaling-stroke"`
       + ' stroke-linejoin="round" stroke-linecap="round"/>';
}

function overlay(shape, query) {
  // 그 당시 모양(흐린 초록)과 지금 모양(밝은 초록)을 같은 자에 겹친다
  const y = scaleTo(shape.concat(query), 120, 8);
  return polyline(shape, y, 400, 8, '#3f7a5c', 2.2, 0.95)
       + polyline(query, y, 400, 8, '#00ff66', 1.6, 0.9);
}

function afterPath(after, cost, good) {
  const y = scaleTo(after.concat([0, cost, -cost]), 120, 8);
  const rule = (v, color) => `<line x1="8" y1="${y(v).toFixed(1)}" x2="112" y2="${y(v).toFixed(1)}"`
    + ` stroke="${color}" stroke-width="1" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>`;
  return rule(0, '#1b3a2a') + rule(cost, '#00ff9c')
       + polyline(after, y, 120, 8, good ? '#00ff9c' : '#ff4d5e', 2, 1);
}

// ---------------------------------------------------------------- 조작
function settings(fresh) {
  return {
    type: 'run',
    market,
    fresh,
    count: Math.min(parseInt($('in-period').value, 10) || PERIODS[0].count, MAX_BARS),
    length: parseInt($('in-length').value, 10),
    similarity: parseFloat($('in-similarity').value),
    fee: parseFloat($('in-fee').value),
    slippage: parseFloat($('in-slippage').value),
    stake: amount(),
  };
}

function run(fresh) {
  if (busy) return;
  start(fresh ? '업비트에서 받는 중…' : '다시 계산하는 중…');
  send(settings(fresh));
}

const runLive = () => run(true);

$('btn-scan').addEventListener('click', () => run(false));
$('btn-live').addEventListener('click', () => runLive());
$('btn-diag').addEventListener('click', () => { runDiagnosis(); });

// 넣을 금액은 **바꾸는 즉시** 반영돼야 한다.
//
// 지금까지 이 칸에는 듣는 사람이 아무도 없었다. 금액을 고쳐도 표의 돈은
// 그대로였고, 판정 문구는 아예 100만원이 박혀 있었다. 얼마를 넣을지 물어
// 놓고 답에는 안 쓴 셈이다.
//
// 표와 그림의 돈은 계산이 필요 없다(확률은 금액과 무관하다) — 곧바로 다시
// 그린다. 판정 문구만 계산을 거쳐야 하므로, 타이핑이 멎은 뒤에 한 번만
// 다시 센다. 받아둔 시세로만 하니 업비트에 가지 않는다.
let amountTimer = null;
$('in-amount').addEventListener('input', () => {
  if (lastAnalysis) {
    renderOdds(lastAnalysis);
    renderAhead(lastAnalysis);
    markSelected();
  }
  clearTimeout(amountTimer);
  amountTimer = setTimeout(() => {
    if (!busy && lastAnalysis) run(false);
  }, 800);
});

$('btn-stop').addEventListener('click', () => {
  // 워커를 끝낸다. 받던 중이었다면 그때까지 받은 것은 이미 저장돼 있다.
  if (worker) worker.terminate();
  worker = null;
  workerHasResult = false;
  finish();
  $('job').textContent = '멈췄습니다';
  send({ type: 'summary', market });
});

// 1분마다 자동 갱신. 새 봉이 생기는 주기가 1분이므로 그보다 자주 물어도 의미가 없다.
let auto = null;
$('in-auto').addEventListener('change', (e) => {
  if (auto !== null) { clearInterval(auto); auto = null; }
  if (e.target.checked) {
    auto = setInterval(() => {
      // 안 보고 있는데 1분마다 시세를 받고 계산까지 할 이유가 없다.
      if (!document.hidden && !busy) runLive();
    }, 60000);
  }
});

// ------------------------------------------------------ 앱처럼 설치하기
//
// 서비스 워커는 **보안 컨텍스트에서만** 등록된다. https와 localhost는 되고,
// 같은 와이파이의 http://192.168.x.x 는 안 된다. 안 되는 자리에서 굳이
// 시도하면 콘솔만 빨개지고 얻는 게 없으므로 아예 건너뛴다.
if ('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register(new URL('./sw.js', import.meta.url)).catch(() => undefined);

  // **새 판이 올라오면 알아서 갈아탄다.**
  //
  // 이게 없어서 오래 헤맸다. 서비스 워커는 빨리 뜨라고 캐시부터 주므로,
  // 새 판을 밀어 넣어도 **이미 열려 있던 화면은 옛 파일 그대로**다. 그래서
  // "고쳤는데 왜 그대로냐"와 "안 고쳐졌다"를 구분할 수가 없었다.
  //
  // 새 워커가 넘겨받으면(controllerchange) 지금 화면은 이미 낡은 것이다.
  // 받는 중이 아니면 곧장 새로고침하고, 받는 중이면 끝난 뒤에 한다 —
  // 한창 받고 있는데 새로고침하면 그건 그것대로 황당하다.
  // **처음 심는 중인지, 갈아타는 중인지 갈라야 한다.**
  //
  // 처음 들어오면 워커가 없다가 심기는 순간에도 controllerchange가 난다.
  // 그걸 갈아타기로 오해하면 **처음 온 사람이 이유 없이 새로고침을 당한다.**
  // 화면이 한 번 깜빡이고 다시 뜨는데, 무슨 일인지 알 수가 없다.
  // 원래 맡고 있던 워커가 있었을 때만 갈아타는 것이다.
  const hadController = Boolean(navigator.serviceWorker.controller);
  let swapping = false;
  const swapNow = () => {
    if (swapping) return;
    swapping = true;
    window.location.reload();
  };
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (!hadController) return;
    if (!busy) { swapNow(); return; }
    $('job').textContent = '새 판이 준비됐습니다 — 지금 받는 것만 끝내고 넘어갑니다';
    pendingSwap = swapNow;
  });
}

// ============================================================ 앞으로의 모양
//
// 선 하나로 그리면 거짓말이 된다. 실제로 일어날 일은 하나지만 우리가 아는
// 건 "비슷했던 과거들이 제각각 흩어졌다"뿐이다. 그래서 띠로 그린다.
// 띠가 넓으면 그건 모른다는 뜻이고, 그게 눈에 보여야 한다.
function renderAhead(analysis) {
  const all = analysis.projection || {};
  const codes = Object.keys(all);
  const panel = $('ahead-panel');
  if (!codes.length) { panel.hidden = true; return; }
  panel.hidden = false;
  if (!aheadPick || !all[aheadPick]) [aheadPick] = codes;
  drawAhead(all[aheadPick], analysis);
}

function drawAhead(p, analysis) {
  const W = 640;
  const H = 260;
  const PAD = 8;
  const past = p.recent || [];
  const ahead = p.median.length;
  // 지나온 길과 앞으로를 같은 자 위에 놓는다. 그래야 이어져 보인다.
  const total = past.length + ahead - 1;
  const x = (i) => PAD + (i / Math.max(1, total - 1)) * (W - PAD * 2);
  const split = past.length - 1;

  const every = [
    ...past.flatMap((k) => [k.h, k.l]),
    ...p.worst, ...p.best, ...p.walks.flat(),
  ];
  const y = scaleTo(every, H, PAD);

  // ── 지나온 길: 진짜 봉으로 그린다. 종가 선만 그으면 꼬리가 사라져
  //    밋밋해지고, 실제 차트로 안 보인다.
  const width = Math.max(1.4, ((W - PAD * 2) / Math.max(1, total)) * 0.62);
  const bars = past.map((k, i) => {
    const up = k.c >= k.o;
    const colour = up ? '#ff5566' : '#4aa3ff';   // 업비트와 같은 색
    const cx = x(i);
    const top = y(Math.max(k.o, k.c));
    const bottom = y(Math.min(k.o, k.c));
    const body = Math.max(0.8, bottom - top);
    return `<line x1="${cx.toFixed(1)}" y1="${y(k.h).toFixed(1)}"
                  x2="${cx.toFixed(1)}" y2="${y(k.l).toFixed(1)}"
                  stroke="${colour}" stroke-width="1" stroke-opacity="0.75"
                  vector-effect="non-scaling-stroke"/>
            <rect x="${(cx - width / 2).toFixed(1)}" y="${top.toFixed(1)}"
                  width="${width.toFixed(1)}" height="${body.toFixed(1)}"
                  fill="${colour}" fill-opacity="0.85"/>`;
  }).join('');

  // ── 앞으로: 띠 + 실제로 갔던 길 몇 개 + 중앙값
  const at = (i) => x(split + i);
  const band = (lo, hi, fill) => {
    const up = lo.map((v, i) => `${at(i).toFixed(1)},${y(v).toFixed(1)}`);
    const down = hi.map((v, i) => `${at(i).toFixed(1)},${y(v).toFixed(1)}`).reverse();
    return `<polygon points="${up.concat(down).join(' ')}" fill="${fill}" stroke="none"/>`;
  };
  const path = (values, colour, stroke, alpha) => {
    const pts = values.map((v, i) => `${at(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    return `<polyline points="${pts}" fill="none" stroke="${colour}"
             stroke-width="${stroke}" stroke-opacity="${alpha}"
             stroke-linejoin="round" stroke-linecap="round"
             vector-effect="non-scaling-stroke"/>`;
  };

  const zero = y(0).toFixed(1);
  const cost = analysis.cost || 0;
  const edge = x(split).toFixed(1);

  $('ahead-chart').innerHTML = band(p.worst, p.best, 'rgba(0,255,102,0.06)')
    + band(p.low, p.high, 'rgba(0,255,102,0.14)')
    // 실제로 갔던 길. 톱니처럼 꺾이는 게 진짜 모습이다 — 중앙값은
    // 100개의 중앙값이라 매끄러울 수밖에 없고, 그것만 보면
    // "앞으로 미끄러지듯 간다"로 읽힌다.
    + p.walks.map((w) => path(w, '#00ff66', 1, 0.28)).join('')
    + `<line x1="${PAD}" y1="${zero}" x2="${W - PAD}" y2="${zero}"
             stroke="#3f7a5c" stroke-width="1" stroke-dasharray="4 4"
             vector-effect="non-scaling-stroke"/>`
    + `<line x1="${edge}" y1="${y(cost).toFixed(1)}" x2="${W - PAD}" y2="${y(cost).toFixed(1)}"
             stroke="#d8ff6b" stroke-width="1" stroke-dasharray="2 5"
             stroke-opacity="0.85" vector-effect="non-scaling-stroke"/>`
    + bars
    // 지금 자리. 왼쪽은 일어난 일, 오른쪽은 아직 아닌 일이다.
    + `<line x1="${edge}" y1="${PAD}" x2="${edge}" y2="${H - PAD}"
             stroke="#00ff66" stroke-width="1" stroke-opacity="0.5"
             stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>`
    + path(p.median, '#00ff66', 2.4, 1);

  const end = p.median[p.median.length - 1];
  const lo = p.low[p.low.length - 1];
  const hi = p.high[p.high.length - 1];
  const money = (r) => `${Math.round(p.priceNow * (1 + r)).toLocaleString('ko-KR')}원`;
  const wide = (hi - lo) > Math.abs(end) * 4;
  const stake = amount();

  $('ahead-legend').innerHTML = `
    <div class="ahead-row"><b>${p.label}</b> · 닮았던 과거 ${p.samples}개 · ${p.minutes}분 앞</div>
    <div class="ahead-row big">가운뎃값 <b>${signed(end)}</b></div>
    <div class="ahead-row dim">${money(end)}</div>
    <div class="ahead-row">절반은 <b>${signed(lo)} ~ ${signed(hi)}</b></div>
    <div class="ahead-row dim">${money(lo)} ~ ${money(hi)}</div>
    ${stake ? `<div class="ahead-money">
        ${won(stake)}원 넣었다면<br>
        <b class="${end - cost >= 0 ? 'up' : 'down'}">${cash(stake * (end - cost))}</b>
        <span class="dim">수수료 빼고</span><br>
        <span class="dim">절반은 ${cash(stake * (lo - cost))} ~ ${cash(stake * (hi - cost))}</span>
      </div>` : ''}
    <div class="ahead-row"><span class="rule-cost">┈</span> 수수료선 ${pct(cost, 2)}</div>
    ${wide ? `<div class="ahead-warn">띠가 가운뎃값보다 훨씬 넓습니다 —
      방향을 말할 수 있는 상태가 아닙니다.</div>` : ''}`;
}

// ============================================================ 지지·저항
function renderLevels(analysis) {
  // 예상 그림이 없으면 aheadPick이 안 정해진다. 그때도 뭔가는 보여줘야 한다.
  if (!aheadPick || !(analysis.levels || {})[aheadPick]) {
    [aheadPick] = Object.keys(analysis.levels || {});
    aheadPick = aheadPick ?? null;
  }
  const found = (analysis.levels || {})[aheadPick] || [];
  const fibs = (analysis.fibonacci || {})[aheadPick] || [];
  const panel = $('levels-panel');
  if (!found.length && !fibs.length) { panel.hidden = true; return; }
  panel.hidden = false;

  // 지금 값은 예상 그림이 아니라 시세에서 읽는다. 닮은 과거를 못 찾아
  // 그림이 없을 때도 위아래를 갈라야 한다.
  const now = (analysis.series || []).find((s) => s.timeframe === aheadPick)?.priceNow;
  const money = (v) => Math.round(v).toLocaleString('ko-KR');
  const strongest = Math.max(...found.map((l) => l.strength), 1);

  const line = (l, kind) => {
    const away = now ? (l.price - now) / now : 0;
    const weight = kind === 'fib' ? 0.35 : 0.25 + 0.75 * (l.strength / strongest);
    return `<div class="level ${l.kind === '저항' ? 'res' : 'sup'} ${kind}">
      <span class="level-bar" style="opacity:${weight.toFixed(2)}"></span>
      <span class="level-kind">${kind === 'fib' ? '피보' : l.kind}</span>
      <span class="level-price">${money(l.price)}원</span>
      <span class="level-away">${signed(away)}</span>
      <span class="level-touch dim">${kind === 'fib' ? '되돌림 자리'
    : `${l.touches}번 닿음 · ${l.lastTouch}봉 전`}</span>
    </div>`;
  };

  const mixed = [...found.map((l) => [l, 'real']), ...fibs.map((l) => [l, 'fib'])]
    .sort((a, b) => b[0].price - a[0].price);
  const here = now
    ? `<div class="level now"><span class="level-bar"></span>
       <span class="level-kind">지금</span>
       <span class="level-price">${money(now)}원</span><span></span><span></span></div>`
    : '';
  const above = mixed.filter(([l]) => !now || l.price > now).map(([l, k]) => line(l, k));
  const below = mixed.filter(([l]) => now && l.price <= now).map(([l, k]) => line(l, k));
  $('levels-body').innerHTML = above.join('') + here + below.join('');
}

// ============================================================ 연결 진단
//
// 세 번 고쳤는데 세 번 다 같은 자리에서 멈췄다. 받은 개수가 201 → 263 → 297로
// 늘긴 했지만, 늘어난 만큼이 정확히 '그 사이 흐른 시간'이었다. 즉 `to`가 붙은
// 과거 요청은 **한 번도** 성공한 적이 없다.
//
// 문제는 브라우저가 실패 이유를 안 알려준다는 것이다. CORS로 막힌 것, 서버가
// 400을 준 것, 인터넷이 끊긴 것이 전부 똑같이 `TypeError`로 온다. 그래서
// 여기까지는 추측으로 고쳤고, 세 번 다 틀렸다.
//
// **추측을 그만두고 실제로 물어본다.** 아래는 사용자 기기에서 도는 작은
// 실험이다. 한 번에 하나씩, 넉넉히 벌려서(속도를 원인에서 지운다) 보내고
// 무엇이 되고 무엇이 안 되는지 그대로 적는다.
//
// 결정적인 것은 마지막의 `no-cors`다.
//   · no-cors가 되는데 보통 요청이 안 된다 → 업비트는 **답했고**, 그 답에
//     CORS 헤더가 없었던 것이다 (십중팔구 오류 응답)
//   · no-cors도 안 된다 → 요청이 업비트에 **닿지도 못한** 것이다
// 이 둘은 고치는 방법이 완전히 다르다.

const DIAG_GAP = 1200;   // 넉넉히 벌린다. 속도를 원인 후보에서 지우기 위해서다.

/** 한 번 물어보고, 무슨 일이 있었는지 그대로 적는다. */
async function probe(label, url, init = {}) {
  const started = Date.now();
  try {
    const response = await fetch(url, { cache: 'no-store', ...init });
    const took = Date.now() - started;
    if (init.mode === 'no-cors') {
      // 내용은 못 읽는다(불투명 응답). 하지만 **닿았다는 것**은 알 수 있다.
      return { label, ok: true, note: `닿았습니다 (내용은 못 읽음, ${took}ms)` };
    }
    if (!response.ok) {
      const body = await response.text().catch(() => '');
      return { label, ok: false, note: `HTTP ${response.status} — ${body.slice(0, 120)}` };
    }
    const rows = await response.json();
    return {
      label,
      ok: true,
      note: `${Array.isArray(rows) ? rows.length : '?'}개 받음 (${took}ms)`,
    };
  } catch (error) {
    // 여기가 아무것도 안 알려주는 자리다. 그래도 이름과 문구는 브라우저마다
    // 달라서(크롬 "Failed to fetch", 사파리 "Load failed") 단서가 된다.
    return {
      label,
      ok: false,
      note: `${error?.name ?? '오류'}: ${error?.message ?? error}`,
    };
  }
}

/**
 * 결과를 읽고 **무엇이 문제인지 한국어로 말한다.**
 *
 * 표를 보여주는 것만으로는 부족하다. 여덟 줄을 보고 무슨 뜻인지 읽어내는 건
 * 내 일이지 사용자 일이 아니다. 그리고 사용자가 나에게 결과를 옮겨 적는
 * 왕복 자체가 비용이다 — 여기서 결론까지 내 준다.
 */
function conclude(done) {
  const find = (part) => done.filter((r) => r.label.includes(part));
  const okOf = (rows) => rows.filter((r) => r.ok);
  const plain = find('to 없음');
  const withTo = done.filter((r) => r.label.includes('+ to'));
  const nocors = done.find((r) => r.label.includes('no-cors'));

  // **no-cors 결과를 먼저 본다.** 이게 가장 강한 증거다.
  //
  // 처음에는 이 갈래를 맨 아래에 뒀는데, 그래서 실제로 틀린 말을 했다 —
  // no-cors가 124ms 만에 성공했는데도 화면에는 "업비트에 아예 못 닿고
  // 있습니다"라고 떴다. 닿았는데 못 닿았다고 한 것이다.
  //
  // no-cors가 되면 요청은 업비트까지 갔고 답도 왔다. 그런데 보통 요청이
  // 전부 실패한다면, 그 답에 브라우저가 요구하는 허용 표시(CORS)가 없다는
  // 뜻이다. 정상 응답에는 붙고 **거절 응답에는 안 붙는다** — 즉 지금
  // 업비트가 우리 요청을 거절하고 있다.
  if (nocors?.ok && !okOf(plain).length) {
    return ['업비트가 지금 우리 요청을 막고 있습니다.',
      '업비트까지는 갔고 답도 왔습니다(no-cors로는 됨). 다만 그 답에 브라우저가 '
      + '요구하는 허용 표시가 없습니다 — 거절 응답일 때 그렇습니다. '
      + '<b>요청이 잦아서 잠시 막힌 것</b>일 가능성이 큽니다. 10분쯤 뒤에 다시 눌러 주세요. '
      + '휴대폰 데이터(5G)는 여러 사람이 한 주소를 나눠 쓰기 때문에 더 자주 걸립니다 — '
      + '<b>와이파이에서 해 보시면</b> 달라질 수 있습니다.'];
  }
  if (!okOf(plain).length) {
    return ['업비트에 아예 못 닿고 있습니다.', '과거뿐 아니라 지금 시세도 못 받습니다. '
      + '요청이 업비트까지 가지도 못했습니다(no-cors도 실패). '
      + '망(회사·학교 와이파이, 일부 VPN)이 막고 있을 수 있습니다.'];
  }
  if (okOf(withTo).length === withTo.length) {
    return ['지금은 과거 요청도 다 됩니다.', '아까 멈춘 건 일시적이었을 수 있습니다. '
      + '<b>지금 시세로 판단받기</b>를 다시 눌러 이어서 받아 보세요.'];
  }
  const small = withTo.find((r) => r.label.includes('봉 1개'));
  const big = withTo.filter((r) => r.label.includes('200개'));
  if (small?.ok && !okOf(big).length) {
    return ['한 번에 많이 달라고 할 때만 거절당합니다.',
      '개수를 줄여서 받도록 앱이 스스로 바꿉니다. 다시 눌러 주세요.'];
  }
  if (okOf(withTo).length) {
    const works = okOf(withTo).map((r) => r.label).join(', ');
    return ['일부 방식만 통합니다.', `이건 됩니다: ${works}. 앱이 통하는 쪽으로 맞춥니다.`];
  }
  if (nocors?.ok) {
    return ['업비트는 답했지만 브라우저가 그 답을 못 읽습니다.',
      '요청 자체는 업비트까지 갔고 답도 돌아왔는데, 그 답에 브라우저가 요구하는 '
      + '허용 표시(CORS)가 없습니다. 오류 응답일 때 그런 경우가 많습니다 — '
      + '즉 <b>업비트가 이 요청을 거절하고 있고, 그 거절 이유를 우리가 볼 수 없는</b> 상태입니다.'];
  }
  return ['과거를 달라는 요청만 업비트에 닿지 못합니다.',
    '지금 시세는 되는데 과거 요청만 안 됩니다. 같은 주소를 no-cors로 불러도 '
    + '안 되는 것으로 보아, 중간에서 그 요청만 끊고 있을 수 있습니다.'];
}

async function runDiagnosis() {
  const box = $('diag');
  const button = $('btn-diag');
  button.disabled = true;
  box.hidden = false;

  const path = ENDPOINTS.minute1;
  const at = Math.floor(Date.now() / 1000 / 60) * 60 - 3 * 3600;   // 3시간 전, 분 단위
  const candles = (extra) => {
    const url = new URL(API_BASE + path);
    url.searchParams.set('market', market);
    for (const [k, v] of Object.entries(extra)) url.searchParams.set(k, v);
    return url.toString();
  };
  const withTo = candles({ count: 200, to: TO_FORMATS[0](at) });

  const plan = [
    ['현재가 (to 없음)', `${API_BASE}/v1/ticker?markets=${market}`, {}],
    ['봉 1개 (to 없음)', candles({ count: 1 }), {}],
    ['봉 200개 (to 없음)', candles({ count: 200 }), {}],
    ['봉 200개 + to (…00Z)', withTo, {}],
    ['봉 200개 + to (빈칸)', candles({ count: 200, to: TO_FORMATS[1](at) }), {}],
    ['봉 200개 + to (+00:00)', candles({ count: 200, to: TO_FORMATS[2](at) }), {}],
    ['봉 1개 + to', candles({ count: 1, to: TO_FORMATS[0](at) }), {}],
    ['같은 주소를 no-cors로', withTo, { mode: 'no-cors' }],
  ];

  const done = [];
  const draw = (running) => {
    const rows = done.map((r) => `<tr>
      <th>${r.label}</th>
      <!-- 여기서 색은 상승·하락이 아니라 잘됨·안됨이다. up(빨강)을 쓰면
           다 됐는데도 온통 빨개서 실패한 것처럼 읽힌다. -->
      <td class="${r.ok ? 'pos' : 'neg'}">${r.ok ? '됨' : '안 됨'}</td>
      <td class="dim">${r.note}</td></tr>`).join('');
    const [headline, detail] = running ? [] : conclude(done);
    box.innerHTML = `<div class="section-title">업비트 연결 진단
        <span class="hint">한 번에 하나씩, ${DIAG_GAP}ms씩 벌려서 물어봅니다</span></div>
      ${running ? '' : `<p class="diag-said"><b>${headline}</b> ${detail}</p>`}
      <div class="table-wrap"><table class="cached"><tbody>${rows}</tbody></table></div>
      ${running
    ? '<p class="note-line dim">물어보는 중…</p>'
    : `<p class="note-line"><button type="button" id="btn-diag-copy" class="linky">결과
         복사하기</button> <span class="dim">복사해서 그대로 보내 주시면 됩니다.</span></p>`}`;
  };
  draw(true);

  for (const [label, url, init] of plan) {
    // eslint-disable-next-line no-await-in-loop
    done.push(await probe(label, url, init));
    draw(true);
    // eslint-disable-next-line no-await-in-loop
    await new Promise((r) => { setTimeout(r, DIAG_GAP); });
  }
  draw(false);
  button.disabled = false;

  $('btn-diag-copy').addEventListener('click', async () => {
    const text = [
      `기시감 연결 진단 (${new Date().toISOString()})`,
      navigator.userAgent,
      ...done.map((r) => `${r.ok ? '됨  ' : '안 됨'} | ${r.label} | ${r.note}`),
    ].join('\n');
    try {
      await navigator.clipboard.writeText(text);
      $('btn-diag-copy').textContent = '복사했습니다';
    } catch {
      // 클립보드를 막아둔 경우가 있다. 그때는 골라서 복사하실 수 있게 편다.
      box.insertAdjacentHTML('beforeend', `<pre class="diag-text">${text}</pre>`);
    }
  });
}

// ============================================================ 차트 이론
function renderTheories(analysis) {
  const all = analysis.theories || {};
  const codes = Object.keys(all).filter((k) => k !== 'confirmation');
  const panel = $('theory-panel');
  if (!codes.length) { panel.hidden = true; return; }
  panel.hidden = false;
  if (!theoryPick || !all[theoryPick]) [theoryPick] = codes;

  $('theory-tabs').innerHTML = codes
    .map((c) => `<button type="button" class="tab ${c === theoryPick ? 'on' : ''}"
                  data-tf="${c}">${all[c].label}</button>`).join('');
  for (const b of $('theory-tabs').querySelectorAll('.tab')) {
    b.addEventListener('click', () => {
      theoryPick = b.dataset.tf;
      aheadPick = b.dataset.tf;
      renderTheories(analysis);
      renderAhead(analysis);
      renderLevels(analysis);
    });
  }

  const agreed = all.confirmation || {};
  $('theory-confirm').innerHTML = `<b>다우의 상호 확인:</b> ${agreed.detail || ''}`;
  $('theory-confirm').className = `theory-confirm ${arrowClass(agreed.says)}`;

  const group = all[theoryPick];
  $('theory-body').innerHTML = `
    <div class="tally">이 봉 간격에서 <b class="up">상승 ${group.up}</b> ·
      <b class="down">하락 ${group.down}</b> · <span class="dim">중립 ${group.flat}</span></div>
    <div class="table-wrap"><table class="theories">
      <thead><tr>
        <th class="l">이론</th><th class="l">지금</th>
        <th>예측</th><th>적중</th><th>평소</th><th>초과</th><th class="l">믿을 만한가</th>
      </tr></thead>
      <tbody>${group.readings.map((r) => theoryRow(r, group.scoring)).join('')}</tbody>
    </table></div>`;
}

const arrowClass = (says) => (says === '상승' ? 'up' : says === '하락' ? 'down' : 'flat');

function theoryRow(r, scoring) {
  const mark = r.says === '상승' ? '▲' : r.says === '하락' ? '▼' : '·';
  const p = r.past;
  if (!p) {
    // 채점을 아예 못 한 것과 '이 이론이 방향을 말한 적이 없다'는 다른
    // 이야기다. 봉이 모자라면 열한 줄 전부가 같은 문구로 나오는데,
    // 그러면 이론들이 침묵한 것처럼 읽힌다 — 사실은 우리가 못 센 것이다.
    const why = scoring && !scoring.ran
      ? `과거 성적을 내기에 봉이 모자랍니다 (${scoring.need.toLocaleString()}개 필요, `
        + `지금 ${scoring.have.toLocaleString()}개)`
      : '이 데이터에서 방향을 말한 적이 없습니다';
    return `<tr class="quiet">
      <td class="l"><b>${r.theory}</b></td>
      <td class="l now-cell ${arrowClass(r.says)}">${mark} ${r.detail}</td>
      <td colspan="5" class="l dim">${why}</td></tr>`;
  }
  // 칸이 좁다. 긴 문장은 잘려서 오히려 안 읽히므로 짧게 쓰고
  // 자세한 설명은 표 아래 각주에 한 번만 둔다.
  let believe = '<span class="dim">구분 안 됨</span>';
  if (p.worthBelieving) believe = '<b class="up">우연 아님</b>';
  else if (!p.enough) believe = '<span class="dim">표본 부족</span>';
  return `<tr class="${p.worthBelieving ? 'real' : ''}">
    <td class="l"><b>${r.theory}</b></td>
    <td class="l now-cell ${arrowClass(r.says)}">${mark} ${r.detail}</td>
    <td>${p.calls}</td>
    <td>${pct(p.rate, 1)}</td>
    <td class="dim">${pct(p.base, 1)}</td>
    <td class="${p.edge > 0 ? 'pos' : 'neg'}">${signed(p.edge, 1)}</td>
    <td class="l">${believe}</td></tr>`;
}

// ------------------------------------------------------------ 떨어지는 글자
//
// 화면 뒤에 아주 옅게 깔리는 장식이다. 장식이 본업을 방해하면 안 되므로
// 세 가지를 지킨다.
//
//   1. 탭이 안 보이면 멈춘다. 아이패드에서 배터리를 계속 먹으면 곤란하다.
//   2. 움직임을 불편해하는 사람에게는 아예 안 그린다.
//   3. 캔버스 하나에 열별로만 그린다.
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
    ctx.font = `${SIZE}px ui-monospace, monospace`;
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
  const startRain = () => { if (ticking === null) ticking = setInterval(draw, 70); };
  const stopRain = () => { if (ticking !== null) { clearInterval(ticking); ticking = null; } };

  resize();
  startRain();
  window.addEventListener('resize', resize);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stopRain(); else startRain();
  });
}());

// ================================================================ 시작
//
// 이 블록이 없으면 화면을 열어도 아무 일도 안 일어난다. 단추를 누르기
// 전까지는 시세도, 받아둔 양도 나오지 않는다. 실제로 한동안 그런 채로
// 있었다 — 안내 문구 하나를 걷어내면서 이 줄들이 같이 지워졌는데, 그
// 뒤로도 계속 단추를 눌러 확인했기 때문에 못 봤다.
//
// 맨 아래에 둔다. 위에서 쓰는 함수들이 모두 정의된 뒤여야 한다.

$('version').textContent = VERSION;
renderCoins();
renderPeriods();
spawn();
send({ type: 'summary', market });

refreshTicker();
// 20초마다. 예전엔 5초였는데, 그것만으로 시간당 720번이 나갔다. 맨 위
// 숫자에 5초 해상도가 필요하지도 않다.
setInterval(refreshTicker, 20000);

// 탭을 다시 보면 곧바로 따라잡는다. 안 보는 동안 아무것도 안 물어봤으므로
// 맨 위 시세가 그만큼 뒤처져 있다.
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) refreshTicker();
});
