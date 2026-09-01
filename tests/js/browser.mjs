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

async function stubUpbit(context) {
  await context.route('https://api.upbit.com/**', async (route) => {
    requests += 1;
    const url = new URL(route.request().url());
    if (url.pathname === '/v1/ticker') {
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
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
const context = await browser.newContext({ ...chromium.devices?.['iPad (gen 7)'] });
await stubUpbit(context);
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

// ── 실제로 받아서 계산한다
await page.selectOption('#in-period', { index: 0 });
await page.fill('#in-length', '20');
await page.fill('#in-similarity', '0.6');
await page.click('#btn-live');

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

// ── 업비트가 막혔을 때 무슨 말을 하는가
expectFailures = true;
await context.route('https://api.upbit.com/**', (route) => route.abort('failed'));
await page.click('#btn-live');
await page.waitForFunction(
  () => !document.getElementById('blocked').hidden, null, { timeout: 60000 },
);
const blocked = await page.locator('#blocked').innerText();
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
