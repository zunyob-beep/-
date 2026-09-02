// 진짜 브라우저에서 앱을 한 번 돌려 본다.
//
// 왜 필요한가
// -----------
// 계산이 맞는지는 정답지 대조(golden.test.js)가 본다. 하지만 그건 노드에서
// 함수를 직접 부르는 것이고, **실제로 화면이 뜨는지**는 아무것도 말해 주지
// 않는다. 워커가 뜨는지, IndexedDB가 열리는지, 모듈 경로가 맞는지, 그려진
// 표에 숫자가 들어 있는지는 브라우저에서만 알 수 있다.
//
// 업비트는 가짜로 세운다. 진짜를 부르면 (1) 이 환경에서는 막혀 있고
// (2) 값이 매번 달라 무엇과도 대조할 수 없다. 가짜라도 **경로가 다 이어져
// 있는지**는 그대로 확인된다.
//
//     node tests/js/browser.mjs

import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, extname, join, normalize } from 'node:path';
import { chromium } from 'playwright';
import { launchOptions } from './launch.mjs';
import { DEFAULT_PERIOD } from '../../web/core/analysis.js';

const here = dirname(fileURLToPath(import.meta.url));
const ROOT = join(here, '..', '..', 'web');

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.png': 'image/png',
  '.webmanifest': 'application/manifest+json; charset=utf-8',
};

/**
 * **하위 경로에 얹어서** 띄운다.
 *
 * GitHub Pages는 저장소 이름이 붙은 경로에 얹는다 — `/-/index.html`.
 * 루트(`/`)에서만 시험하면 절대경로가 하나라도 섞여 있을 때 못 잡고,
 * 정작 배포한 자리에서 흰 화면이 된다. 실제 배포와 같은 모양으로 띄우고,
 * 하위 경로 **밖을** 찾는 요청이 하나라도 있으면 실패로 본다.
 */
const BASE = '/-';
const outside = [];

function serve() {
  const server = createServer(async (req, res) => {
    const asked = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    if (asked !== BASE && !asked.startsWith(`${BASE}/`)) {
      outside.push(asked);
      res.writeHead(404).end('하위 경로 밖입니다');
      return;
    }
    const rest = asked.slice(BASE.length) || '/';
    const path = join(ROOT, normalize(rest === '/' ? '/index.html' : rest));
    if (!path.startsWith(ROOT)) { res.writeHead(403).end(); return; }
    try {
      const body = await readFile(path);
      res.writeHead(200, { 'Content-Type': TYPES[extname(path)] ?? 'application/octet-stream' });
      res.end(body);
    } catch {
      res.writeHead(404).end('없음');
    }
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port }));
  });
}

// ------------------------------------------------------------ 가짜 업비트
//
// 실제 업비트처럼 최신순으로 주고, `to` 커서로 과거를 준다. 값은 시각만으로
// 정해지는 함수라 몇 번을 물어도 같은 답이 온다 — 그래야 캐시가 제대로
// 이어 붙는지 확인할 수 있다.
const STEP = { 1: 60, 3: 180, 5: 300 };

function priceAt(ts) {
  const t = ts / 600;
  return 50000000 * (1 + 0.02 * Math.sin(t) + 0.008 * Math.sin(t * 3.7)
    + 0.003 * Math.sin(t * 11.3));
}

function fakeCandle(ts) {
  const close = priceAt(ts);
  const open = priceAt(ts - 60);
  return {
    market: 'KRW-BTC',
    candle_date_time_utc: new Date(ts * 1000).toISOString().slice(0, 19),
    opening_price: open,
    high_price: Math.max(open, close) * 1.0004,
    low_price: Math.min(open, close) * 0.9996,
    trade_price: close,
    candle_acc_trade_volume: 2 + (ts % 7) / 3,
  };
}

let requests = 0;
let tickerCalls = 0;

// **시험은 바깥 세상을 부르지 않는다.**
//
// 앱은 직접 가는 길이 막히면 공개 우회 서버로 돌아선다. api.upbit.com만
// 막아 두면 그 순간부터 진짜 남의 서버로 요청이 나간다 — 느리고, 남에게
// 폐가 되고, 그 서버가 죽은 날 CI가 같이 죽는다. 통째로 끊는다.
async function blockDetours(context) {
  for (const pattern of ['https://api.allorigins.win/**', 'https://api.codetabs.com/**']) {
    // eslint-disable-next-line no-await-in-loop
    await context.route(pattern, (route) => route.abort('failed'));
  }
}

async function stubUpbit(context) {
  await context.route('https://api.upbit.com/**', async (route) => {
    requests += 1;
    const url = new URL(route.request().url());
    if (url.pathname === '/v1/ticker') {
      tickerCalls += 1;
      const now = Math.floor(Date.now() / 1000);
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify([{
          market: url.searchParams.get('markets').split(',')[0],
          trade_price: priceAt(now),
          signed_change_rate: 0.0123,
          signed_change_price: 610000,
          high_price: priceAt(now) * 1.01,
          low_price: priceAt(now) * 0.99,
        }]),
      });
      return;
    }
    const unit = Number(url.pathname.split('/').pop());
    const step = STEP[unit] ?? 60;
    const count = Number(url.searchParams.get('count') ?? 200);
    const to = url.searchParams.get('to');
    const end = to
      ? Math.floor(Date.parse(to) / 1000)
      : Math.floor(Date.now() / 1000);
    const last = end - (end % step);
    // 업비트는 최신순으로 준다
    const rows = Array.from({ length: count }, (_, i) => fakeCandle(last - i * step));
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(rows) });
  });
}

// ---------------------------------------------------------------- 확인
const checks = [];
const check = (name, ok, detail = '') => {
  checks.push({ name, ok, detail });
  console.log(`${ok ? '  ok  ' : ' 실패 '} ${name}${detail ? `  ${detail}` : ''}`);
};

const { server, port } = await serve();
const browser = await chromium.launch(launchOptions);
const context = await browser.newContext({ ...chromium.devices?.['iPad (gen 7)'] });
await stubUpbit(context);
await blockDetours(context);
const page = await context.newPage();

// 마지막 단계에서 업비트를 일부러 끊으므로 그때의 네트워크 오류는
// 기대한 결과다. 그것까지 실패로 세면 진짜 오류가 묻힌다.
let expectFailures = false;
const problems = [];
page.on('pageerror', (e) => problems.push(`PAGEERROR ${e}`));
page.on('console', (m) => {
  if (m.type() !== 'error') return;
  if (expectFailures && /ERR_FAILED|Failed to fetch|net::/.test(m.text())) return;
  problems.push(`CONSOLE ${m.text()}`);
});

await page.goto(`http://127.0.0.1:${port}${BASE}/`, { waitUntil: 'domcontentloaded' });

// ── 화면이 스스로 시작하는가 (예전에 이게 통째로 빠져 있었다)
await page.waitForFunction(
  () => document.getElementById('coins').children.length > 0, null, { timeout: 10000 },
);
check('종목 단추가 그려진다', (await page.locator('.coin').count()) === 4);

await page.waitForFunction(
  () => !document.getElementById('coverage').hidden, null, { timeout: 10000 },
);
check(
  '받아둔 시세 요약이 스스로 뜬다',
  (await page.locator('#coverage').innerText()).includes('아직 받아둔 시세가 없습니다'),
);

await page.waitForFunction(
  () => document.getElementById('ticker-price').textContent !== '—', null, { timeout: 10000 },
);
check('맨 위 시세가 뜬다', (await page.locator('#ticker-price').innerText()).includes('원'));

// **가격도 분봉에서 온다.** 주소를 하나로 줄였으니 실제로 그런지 본다 —
// /v1/ticker를 부르고 있으면 20초마다 다른 주소가 하나 더 나가는 것이다.
check('맨 위 가격도 분봉에서 가져온다', tickerCalls === 0,
  `/v1/ticker를 ${tickerCalls}번 불렀습니다`);

// ── 실제로 받아서 계산한다
// **index로 고르지 않는다.** 예전에는 index 0이 30일치였는데, 1일 선택지를
// 앞에 넣으면서 1,440봉이 됐다. 그 정도로는 닮은 과거가 스무 개를 못 넘길
// 때가 있어서 확률 표가 비고, 아래 '줄이 있다'가 가끔 실패한다. 실제로
// CI에서 그렇게 한 번 죽었다. 사용자가 실제로 받는 기본값을 그대로 쓴다.
await page.selectOption('#in-period', String(DEFAULT_PERIOD));
await page.fill('#in-length', '20');
await page.fill('#in-similarity', '0.6');
await page.click('#btn-live');

// **받는 동안 몇 개를 받았는지 보이는가.**
//
// 예전에는 잘 받고 있을 때 개수를 안 적었다 — 걸렸을 때만 나왔다. 그래서
// 순조로울 때가 오히려 깜깜했고, 화면만 봐서는 쌓이는 중인지 멈춘 건지
// 알 수가 없었다.
// 개수가 **올라가는지**를 본다. 0이 떠 있는 것만으로는 실시간이 아니다.
const growing = await page.waitForFunction(
  () => {
    const seen = document.getElementById('progress-count').textContent.match(/([\d,]+) \//);
    return seen ? Number(seen[1].replace(/,/g, '')) > 0 : false;
  },
  null, { timeout: 60000 },
).then(() => true, () => false);
check('받는 동안 개수가 실시간으로 올라간다', growing,
  (await page.locator('#progress-count').innerText()));

// 막대도 실제로 차야 한다. 예전에는 2픽셀짜리라 있으나 마나였고, 분모가
// 이번에 받을 조각 수여서 다 차도 뜻이 없었다.
// 막대에는 0.25초짜리 전환이 걸려 있다. 자라는 도중에 재면 0이 나온다 —
// 실제로 그렇게 한 번 헛짚었다. 다 자란 뒤에 잰다.
await page.waitForTimeout(600);
const bar = await page.evaluate(() => {
  const box = document.getElementById('progress');
  const fill = document.getElementById('progress-bar');
  return {
    shown: !box.hidden,
    tall: document.querySelector('.progress-track').getBoundingClientRect().height,
    wide: fill.getBoundingClientRect().width,
    track: document.querySelector('.progress-track').getBoundingClientRect().width,
    pct: document.getElementById('progress-pct').textContent,
    // 실제로 칠해진 너비와 **적어 넣은 너비**를 같이 본다. 둘 다 봐야
    // '전환 중이라 0'과 '진짜로 0'을 구분할 수 있다.
    said: fill.style.width,
  };
});
check('진행 막대가 눈에 보인다', bar.shown && bar.tall >= 4, `높이 ${bar.tall}px`);
check('진행 막대가 실제로 찬다', bar.wide > 0 && bar.wide < bar.track,
  `${Math.round(bar.wide)} / ${Math.round(bar.track)}px · 적힌 너비 ${bar.said} · ${bar.pct}`);
check('몇 퍼센트인지 적혀 있다', /%$/.test(bar.pct), bar.pct);

const startedAt = Date.now();
await page.waitForFunction(
  () => !document.getElementById('verdict').hidden, null, { timeout: 300000 },
);
const took = ((Date.now() - startedAt) / 1000).toFixed(1);
check('받아서 계산까지 끝난다', true, `${took}초, 업비트 요청 ${requests}번`);

for (const id of ['verdict', 'coverage', 'odds-panel', 'theory-panel']) {
  // eslint-disable-next-line no-await-in-loop
  check(`${id}이(가) 보인다`, !(await page.locator(`#${id}`).isHidden()));
}

const headline = await page.locator('#verdict-headline').innerText();
check('판정에 문장이 들어 있다', headline.length > 3, headline);

const rows = await page.locator('#odds-body tbody tr').count();
check('확률 표에 줄이 있다', rows > 0, `${rows}줄`);

const theoryRows = await page.locator('table.theories tbody tr').count();
check('이론 표에 11줄이 있다', theoryRows === 11, `${theoryRows}줄`);

// 이론 표의 방향 색(빨강·파랑)이 실제로 칠해지는가.
//
// 줄 수만 세고 있었더니, 스타일시트를 다시 쓰면서 색이 통째로 사라진 걸
// 시험이 하나도 못 잡았다. 명시도 싸움에서 진 것이라 눈으로만 보였다.
// **과거 성적을 못 낸 줄(quiet)에서도** 방향 색은 남아야 한다 — 봉이
// 모자라면 열한 줄이 전부 그 줄이 되기 때문이다.
const colours = await page.evaluate(() => {
  const seen = { up: null, down: null, quietUp: null, quietDown: null };
  for (const td of document.querySelectorAll('table.theories td.now-cell')) {
    const quiet = td.closest('tr').classList.contains('quiet');
    const way = td.classList.contains('up') ? 'up' : td.classList.contains('down') ? 'down' : null;
    if (!way) continue;
    const key = quiet ? (way === 'up' ? 'quietUp' : 'quietDown') : way;
    seen[key] ??= getComputedStyle(td).color;
  }
  return seen;
});
// 업비트와 같은 색: 오르면 빨강, 내리면 파랑. 회색으로 덮이면 안 된다.
const reddish = (c) => /^rgba?\((\d+), (\d+), (\d+)/.test(c)
  && Number(RegExp.$1) > Number(RegExp.$3) + 40;
const bluish = (c) => /^rgba?\((\d+), (\d+), (\d+)/.test(c)
  && Number(RegExp.$3) > Number(RegExp.$1) + 40;
const painted = Object.entries(colours).filter(([, c]) => c !== null);
check(
  '이론 표의 방향 색이 살아 있다',
  painted.length > 0 && painted.every(([key, c]) => (key.toLowerCase().includes('up')
    ? reddish(c) : bluish(c))),
  painted.map(([k, c]) => `${k}=${c}`).join(' · ') || '방향이 있는 칸이 없었습니다',
);

// 여기서는 봉이 넉넉해서 quiet 줄이 안 나온다. 하지만 **깨진 자리가 정확히
// 거기였다** — 아이패드에서는 봉이 모자라 열한 줄이 전부 quiet였다. 데이터가
// 그 상태가 되기를 기다리지 말고, 줄에 직접 quiet를 걸어 색이 버티는지 본다.
const quietKeeps = await page.evaluate(() => {
  const td = document.querySelector('table.theories td.now-cell.up')
    ?? document.querySelector('table.theories td.now-cell.down');
  if (!td) return null;
  const row = td.closest('tr');
  const had = row.classList.contains('quiet');
  const before = getComputedStyle(td).color;
  row.classList.add('quiet');
  const after = getComputedStyle(td).color;
  if (!had) row.classList.remove('quiet');
  return { before, after };
});
check(
  '과거 성적을 못 낸 줄에서도 방향 색이 남는다',
  quietKeeps !== null && quietKeeps.before === quietKeeps.after,
  quietKeeps ? `${quietKeeps.before} → ${quietKeeps.after}` : '방향이 있는 칸이 없었습니다',
);

// ── 넣을 금액이 실제로 반영되는가
//
// 이 칸에는 듣는 사람이 아무도 없었다. 금액을 고쳐도 표의 돈은 그대로였고,
// 판정 문구에는 100만원이 아예 박혀 있었다 — 얼마를 넣을지 물어 놓고 답에는
// 안 쓴 셈이다.
const moneyOf = () => page.locator('#odds-body tbody tr td:last-child').first().innerText();
const moneyBefore = await moneyOf();
await page.fill('#in-amount', '2000000');
await page.waitForFunction(
  (was) => document.querySelector('#odds-body tbody tr td:last-child')?.innerText !== was,
  moneyBefore, { timeout: 15000 },
).catch(() => {});
const moneyAfter = await moneyOf();
check(
  '금액을 바꾸면 표의 돈이 곧바로 바뀐다',
  moneyBefore !== moneyAfter,
  `${moneyBefore.replace(/\s+/g, ' ')} → ${moneyAfter.replace(/\s+/g, ' ')}`,
);

// 판정 문구도 그 금액으로 말해야 한다 (다시 세고 나면).
const saidAmount = await page.waitForFunction(
  () => document.getElementById('verdict-reasons')?.innerText.includes('2,000,000원'),
  null, { timeout: 30000 },
).then(() => true, () => false);
check(
  '판정 문구도 그 금액으로 말한다',
  saidAmount,
  (await page.locator('#verdict-reasons').innerText()).split('\n').pop() ?? '',
);
await page.fill('#in-amount', '1000000');

check('예상 그림이 그려졌다', (await page.locator('#ahead-chart polyline').count()) > 0);
check('사례가 그려졌다', (await page.locator('.example').count()) > 0);

// ── 다시 눌렀을 때 과거를 다시 받지 않는가 (이 앱의 핵심 약속)
const before = requests;
await page.click('#btn-live');
await page.waitForFunction(
  () => document.getElementById('job').textContent === '계산을 마쳤습니다',
  null, { timeout: 120000 },
);
const again = requests - before;
check(
  '두 번째는 새 봉만 받는다',
  again <= 12,
  `${again}번 (처음 ${before}번)`,
);

// ── 받아둔 시세가 이 기기에 남아 있는가
const cached = await page.locator('#coverage').innerText();
check('받아둔 개수가 표시된다', /\d[\d,]*개/.test(cached), cached.split('\n')[1] ?? '');

// ── 새로고침해도 캐시가 살아 있는가 (IndexedDB에 정말 들어갔는지)
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForFunction(
  () => document.getElementById('coverage').innerText.includes('개'),
  null, { timeout: 15000 },
);
check(
  '새로고침해도 받아둔 시세가 남아 있다',
  (await page.locator('#coverage').innerText()).includes('개'),
);
// 남아 있다는 사실을 **화면이 말해 줘야** 한다. 그걸 못 믿으면 중간에
// 끊겼을 때 다시 누르기가 겁난다 — 사실은 누를수록 쌓이는데도.
check(
  '다시 받지 않는다는 걸 화면이 말해 준다',
  (await page.locator('#coverage').innerText()).includes('다시 받지 않습니다'),
  (await page.locator('.keep').innerText().catch(() => '없음')).replace(/\s+/g, ' ').slice(0, 60),
);

// ── **가진 것으로 먼저 답하는가**
//
// 실제로 받은 화면이 이랬다: 30일치를 고르고 눌렀는데 이미 12,696개를
// 받아 뒀는데도 나머지 3만 개를 다 채울 때까지 아무 답도 안 줬다. 화면에는
// "다시 해 보는 중"만 몇 분씩 떴고, 사용자 눈에는 **작동을 안 하는 것과
// 구분이 안 됐다.**
//
// 여기서는 훨씬 큰 기간을 골라 두고, 다 받기 전에 답이 나오는지 본다.
// 가짜 업비트라 실패가 한 번도 없는데도 90일치는 648번이라 3분이 넘는다 —
// 그 안에 답이 나와야 한다.
const big = await page.locator('#in-period option').evaluateAll(
  (options) => options.map((o) => Number(o.value)).filter((v) => v >= 129600)[0],
);
await page.selectOption('#in-period', String(big));
await page.click('#btn-live');
const early = await page.waitForFunction(
  () => {
    const note = document.getElementById('stale-note');
    return !note.hidden && note.textContent.includes('먼저 계산한');
  },
  null, { timeout: 30000 },
).then(() => true, () => false);
check('다 받기 전에 가진 것으로 먼저 답한다', early,
  (await page.locator('#stale-note').innerText()).replace(/\s+/g, ' ').slice(0, 50));
check('그때 판정도 실제로 그려져 있다', !(await page.locator('#verdict').isHidden()));
// 멈추기가 살아 있어야 한다 — 아직 받는 중이므로.
check('먼저 답한 뒤에도 멈출 수 있다', !(await page.locator('#btn-stop').isHidden()));
await page.click('#btn-stop');
await page.waitForFunction(
  () => !document.getElementById('btn-live').disabled, null, { timeout: 60000 },
);

// ── 업비트가 막혔을 때 무슨 말을 하는가
expectFailures = true;
await context.route('https://api.upbit.com/**', (route) => route.abort('failed'));
await page.click('#btn-live');
// **말없이 있으면 안 된다.**
//
// 예전에는 여기서 곧장 오류 칸이 뜨기를 기다렸다. 그런데 이제 앱은 한 번
// 실패했다고 포기하지 않고 다시 해 본다 — 7번 중 1번은 통과하는 망이 실제로
// 있었기 때문이다. 그건 옳은 동작이다.
//
// 다만 **다시 해 보는 중이라는 걸 화면이 말해야** 한다. 실제로 그걸 안 하고
// 있었고, 화면에는 "받는 중…"만 몇 분씩 떠서 앱이 죽은 것처럼 보였다.
// 그러니 여기서 볼 것은 '오류가 떴는가'가 아니라 **'무슨 일이 벌어지고
// 있는지 사용자가 알 수 있는가'**다.
await page.waitForFunction(
  () => !document.getElementById('blocked').hidden
    || /거절|막고|걸렸/.test(document.getElementById('job').textContent),
  null, { timeout: 60000 },
);
check(
  '거절당하는 동안 말없이 있지 않는다',
  /거절|막고|걸렸/.test(await page.locator('#job').innerText())
    || !(await page.locator('#blocked').isHidden()),
  (await page.locator('#job').innerText()).slice(0, 44),
);
// 끝내 포기하고 오류 칸을 띄우는 것까지는 여기서 재지 않는다. 길이 열려
// 있을 가능성이 남아 있는 동안 계속 해 보는 게 맞고, 그건 몇 분이 걸릴 수
// 있다. 시간을 재는 시험으로 만들면 상수를 조금만 건드려도 깨진다.
// **한 번도 못 받은 채 막혀 있을 때** 이유를 말하는지는 rough.mjs가 본다.
// 그리고 **끝내는** 오류 칸까지 떠야 한다. 다시 해 보는 것과 붙잡고 있는
// 것은 다르다. 다만 길이 열려 있을 가능성이 남아 있는 동안은 계속 해 보는
// 게 맞으므로, 여기서는 2분 남짓이 걸린다 — 그동안 화면은 위에서 확인한
// 대로 무슨 일이 벌어지는지 계속 말하고 있다.
await page.waitForFunction(
  () => !document.getElementById('blocked').hidden, null, { timeout: 200000 },
);
const blocked = await page.locator('#blocked').innerText().catch(() => '');
check(
  '업비트가 막히면 무엇 때문인지 말해 준다',
  blocked.includes('업비트') || blocked.includes('인터넷'),
  blocked.split('\n')[0],
);
// 막힌 뒤에도 화면이 반드시 되살아나야 한다. 단추가 잠긴 채로 남으면
// 새로고침 말고는 손쓸 방법이 없어진다 — 예전 판에서 실제로 그랬다.
let recovered = true;
try {
  await page.waitForFunction(
    () => !document.getElementById('btn-live').disabled, null, { timeout: 120000 },
  );
} catch { recovered = false; }
check('막혔을 때도 단추가 다시 살아난다', recovered);
check(
  '못 받았다는 걸 결과 옆에도 적는다',
  (await page.locator('#job').innerText()).includes('못 받았습니다')
    || !(await page.locator('#blocked').isHidden()),
  await page.locator('#job').innerText(),
);

// ── 연결 진단이 실제로 돌아가는가
//
// 이건 결과를 맞히는 시험이 아니다. **버튼을 눌렀을 때 화면에 무언가가
// 제대로 나오는지**를 본다. 진단은 내가 여기서 못 보는 것을 사용자 기기에서
// 대신 봐 주는 물건이라, 이게 조용히 깨져 있으면 다음 단서를 통째로 잃는다.
await page.click('#btn-diag');
await page.waitForSelector('#btn-diag-copy', { timeout: 60000 });
const diagRows = await page.locator('#diag table tbody tr').count();
check('연결 진단이 여덟 가지를 다 물어본다', diagRows === 8, `${diagRows}줄`);
check(
  '진단 결과에 되고 안 된 것이 적힌다',
  /됨|안 됨/.test(await page.locator('#diag').innerText()),
  (await page.locator('#diag table tbody tr').first().innerText()).replace(/\s+/g, ' '),
);
// 표만 보여주고 마는 건 부족하다. 여덟 줄을 읽어 무슨 뜻인지 알아내는 건
// 내 일이지 사용자 일이 아니다. 진단이 **스스로 결론을 말해야** 한다.
const said = (await page.locator('.diag-said').innerText().catch(() => '')).trim();
check('진단이 스스로 결론을 말한다', said.length > 10, said.replace(/\s+/g, ' ').slice(0, 90));

// ── 하위 경로에서 제대로 얹혔는가 (진짜 배포와 같은 모양)
check(
  '절대경로가 섞여 있지 않다',
  outside.length === 0,
  outside.length ? `밖을 찾았습니다: ${[...new Set(outside)].slice(0, 5)}` : '',
);
const scope = await page.evaluate(async () => {
  const reg = await navigator.serviceWorker.getRegistration();
  return reg ? reg.scope : null;
});
check(
  '서비스 워커가 하위 경로를 관할한다',
  Boolean(scope && scope.endsWith(`${BASE}/`)),
  scope ?? '(등록 안 됨)',
);

console.log('');
if (problems.length) {
  console.log('브라우저 오류:');
  for (const p of problems) console.log(`  ${p}`);
}
const failed = checks.filter((c) => !c.ok);
console.log(`${checks.length - failed.length}/${checks.length} 통과`);

await browser.close();
server.close();
process.exit(failed.length || problems.length ? 1 : 0);
