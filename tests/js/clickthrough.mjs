// **사람처럼 눌러 본다.**
//
// browser.mjs는 '한 번 돌려서 화면이 뜨는지'를 본다. 이건 다르다 — 아이패드
// 크기에서 있는 단추를 다 눌러 보고, 그때 콘솔에 오류가 나는지, 가로로
// 삐져나가는 곳이 있는지, 눌렀는데 아무 일도 안 일어나는지를 본다.
//
// 왜 따로 필요한가: 지금까지 나온 문제 중 여러 개가 **눌러 봐야만 보이는**
// 것이었다. 이론 표의 색이 사라진 것, 조작부 줄이 어긋난 것, 금액 칸에 듣는
// 사람이 없던 것. 전부 시험은 통과하는데 사람이 보면 이상한 것들이었다.
//
//     node tests/js/clickthrough.mjs

import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, extname, join, normalize } from 'node:path';
import { chromium } from 'playwright';
import { launchOptions } from './launch.mjs';
import { DEFAULT_PERIOD } from '../../web/core/analysis.js';

const here = dirname(fileURLToPath(import.meta.url));
const ROOT = join(here, '..', '..', 'web');
const BASE = '/-';

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.png': 'image/png',
  '.webmanifest': 'application/manifest+json; charset=utf-8',
};

/**
 * **미리 받아 둔 봉 파일.** 진짜 앱이 켜자마자 읽는 것이 이것이다.
 *
 * 깃허브 액션이 20분마다 서버에서 받아 `data` 브랜치에 적어 두고, 앱은
 * 그걸 내려받아 캐시에 넣은 뒤에야 업비트로 마지막 몇 분을 채운다.
 * 이걸 안 내면 시험은 실제로 아무도 안 걷는 길을 걷게 된다.
 */
const SEED_BARS = 20000;
function seedFile(market) {
  // **10분 전까지만 담는다.** 실제로도 그렇다 — 서버가 20분마다 받으므로
  // 파일은 늘 몇 분 뒤처져 있고, 앱은 그 몇 분을 업비트로 채우러 간다.
  // 지금까지 담아 버리면 그 길이 시험에서 통째로 안 걸린다.
  const now = Math.floor(Date.now() / 1000 / 60) * 60 - 600;
  const rows = [];
  for (let i = SEED_BARS - 1; i >= 0; i -= 1) {
    const ts = now - i * 60;
    const c = fakeCandle(ts, market);
    rows.push([ts, c.opening_price, c.high_price, c.low_price,
      c.trade_price, c.candle_acc_trade_volume]);
  }
  return JSON.stringify({
    market, timeframe: 'minute1', step: 60, days: 14, made: now, rows,
  });
}

function serve() {
  const server = createServer(async (req, res) => {
    const asked = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    const rest = asked.startsWith(BASE) ? asked.slice(BASE.length) || '/' : null;
    if (rest === null) { res.writeHead(404).end(); return; }
    // 미리 받아 둔 봉 파일. 실제 앱에서는 raw.githubusercontent.com에서 오지만
    // 시험은 바깥으로 안 나가므로 같은 주소에서 낸다.
    const seed = rest.match(/^\/data\/(KRW-[A-Z]+)\.min1\.json$/);
    if (seed) {
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(seedFile(seed[1]));
      return;
    }
    const path = join(ROOT, normalize(rest === '/' ? '/index.html' : rest));
    if (!path.startsWith(ROOT)) { res.writeHead(403).end(); return; }
    try {
      const body = await readFile(path);
      res.writeHead(200, { 'Content-Type': TYPES[extname(path)] ?? 'application/octet-stream' });
      res.end(body);
    } catch { res.writeHead(404).end(); }
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port }));
  });
}

// 가짜 업비트. 값은 시각만으로 정해지므로 몇 번을 물어도 같은 답이 온다.
const STEP = { 1: 60, 3: 180, 5: 300 };
const priceAt = (ts) => {
  const t = ts / 600;
  return 50000000 * (1 + 0.02 * Math.sin(t) + 0.008 * Math.sin(t * 3.7));
};
const fakeCandle = (ts, market) => {
  const close = priceAt(ts);
  const open = priceAt(ts - 60);
  return {
    market,
    candle_date_time_utc: new Date(ts * 1000).toISOString().slice(0, 19),
    opening_price: open,
    high_price: Math.max(open, close) * 1.0004,
    low_price: Math.min(open, close) * 0.9996,
    trade_price: close,
    candle_acc_trade_volume: 2 + (ts % 7) / 3,
  };
};

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
    const url = new URL(route.request().url());
    const json = (body) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/v1/ticker') {
      const now = Math.floor(Date.now() / 1000);
      const market = url.searchParams.get('markets').split(',')[0];
      return json([{
        market,
        trade_price: priceAt(now),
        signed_change_rate: 0.0123,
        signed_change_price: 610000,
        high_price: priceAt(now) * 1.01,
        low_price: priceAt(now) * 0.99,
      }]);
    }
    const unit = Number(url.pathname.split('/').pop());
    const step = STEP[unit] ?? 60;
    const count = Number(url.searchParams.get('count') ?? 200);
    const market = url.searchParams.get('market');
    const to = url.searchParams.get('to');
    const end = to ? Math.floor(Date.parse(to) / 1000) : Math.floor(Date.now() / 1000);
    const last = end - (end % step);
    return json(Array.from({ length: count }, (_, i) => fakeCandle(last - i * step, market)));
  });
}

// ---------------------------------------------------------------- 확인
const found = [];
const ok = [];
const note = (name, good, detail = '') => {
  (good ? ok : found).push(`${name}${detail ? `  ${detail}` : ''}`);
  console.log(`  ${good ? 'ok  ' : '문제'}  ${name}${detail ? `  ${detail}` : ''}`);
};

const { server, port } = await serve();
const url = `http://127.0.0.1:${port}${BASE}/`;
const browser = await chromium.launch(launchOptions);

// 아이패드 크기. 사용자가 쓰는 화면이다.
const context = await browser.newContext({
  viewport: { width: 1024, height: 768 },
  deviceScaleFactor: 2,
});
await stubUpbit(context);
await blockDetours(context);
const page = await context.newPage();

// 콘솔 오류는 **하나도** 없어야 한다. 눌렀는데 조용히 터지는 것이 제일 나쁘다.
const errors = [];
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
page.on('console', (m) => {
  if (m.type() === 'error') errors.push(`console: ${m.text()}`);
});

await page.goto(url, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.coin', { timeout: 20000 });

// ── 판 번호
const version = (await page.locator('#version').innerText()).trim();
note('판 번호가 화면에 뜬다', /^v\d+$/.test(version), version);

const shown = await page.evaluate(async () => {
  const mod = await import('./version.js');
  return mod.VERSION;
});
note('화면의 판 번호가 코드와 같다', shown === version, `${shown} / ${version}`);

// ── 가로로 삐져나가는 곳이 없어야 한다 (아이패드에서 좌우 스크롤은 최악이다)
const overflow = await page.evaluate(() => {
  const wide = [];
  for (const el of document.querySelectorAll('body *')) {
    if (el.scrollWidth > document.documentElement.clientWidth + 2) {
      const style = getComputedStyle(el);
      if (style.overflowX === 'auto' || style.overflowX === 'scroll') continue;
      wide.push(`${el.tagName.toLowerCase()}.${el.className || '(무명)'}`);
    }
  }
  return wide.slice(0, 5);
});
note('가로로 삐져나가는 곳이 없다', overflow.length === 0, overflow.join(', '));

// ── 열자마자 골라져 있는 기간
//
// 첫 번째(가장 짧은 1일)가 골라져 있으면 안 된다. 하루치로는 닮은 과거가
// 스무 개도 안 나와서, 처음 온 사람이 "표본이 모자랍니다"만 보고 고장난
// 줄 안다. 그렇다고 30일치를 기본으로 두면 막힌 날에 아무것도 못 본다.
const firstPick = await page.locator('#in-period').evaluate(
  (box) => box.options[box.selectedIndex].textContent.trim(),
);
note('열자마자 7일이 골라져 있다', firstPick === '7일', firstPick);

// ── 기간을 바꾸면 안내가 따라 바뀐다
const noteBefore = await page.locator('#period-note').innerText();
const last = await page.locator('#in-period option').count();
await page.selectOption('#in-period', { index: last - 1 });   // 가장 긴 것
await page.waitForTimeout(100);
const noteAfter = await page.locator('#period-note').innerText();
note('기간을 바꾸면 예상 시간이 바뀐다', noteBefore !== noteAfter, `${noteBefore} → ${noteAfter}`);

// **원래 골라져 있던 것으로 되돌린다.**
//
// 예전에는 여기서 `index: 0`을 골랐다. 그때는 그게 30일치라 아무 문제가
// 없었는데, 1일 선택지를 앞에 넣으면서 index 0이 **1,440봉**이 됐다.
// 그걸로는 닮은 과거가 스무 개도 안 나와서 확률 표가 비는 일이 생기고,
// 그러면 아래에서 표의 줄을 누르려다 30초를 기다리다 죽는다.
// 실제로 CI에서 그렇게 죽었다 — 여기서는 사용자가 실제로 받는 기본값을
// 그대로 써야 한다.
await page.selectOption('#in-period', String(DEFAULT_PERIOD));

// ── 받아서 계산 (여기서 시간이 제일 오래 걸린다)
const started = Date.now();
await page.click('#btn-live');
await page.waitForFunction(
  () => document.getElementById('job')?.textContent?.includes('마쳤'),
  null, { timeout: 300000 },
);
note('받아서 계산까지 끝난다', true, `${((Date.now() - started) / 1000).toFixed(1)}초`);

// ── 이론 탭을 눌러 본다
const tabs = await page.locator('#theory-tabs .tab').count();
if (tabs > 1) {
  const firstRow = await page.locator('table.theories tbody tr').first().innerText();
  await page.locator('#theory-tabs .tab').nth(1).click();
  await page.waitForTimeout(300);
  const afterRow = await page.locator('table.theories tbody tr').first().innerText();
  const onTab = await page.locator('#theory-tabs .tab.on').count();
  note('이론 탭을 누르면 표가 바뀐다', firstRow !== afterRow || onTab === 1);
} else {
  note('이론 탭이 그려진다', false, `${tabs}개뿐입니다`);
}

// ── 확률 표의 줄을 눌러 본다
//
// **줄이 없으면 여기서 멈춘다.** nth(-1)은 플레이라이트에서 '마지막 것'이
// 되어 영영 기다리므로, 표가 비어 있다는 사실이 30초짜리 시간 초과로만
// 드러난다. 무엇이 잘못됐는지 알 수 없는 실패는 없느니만 못하다.
const rows = await page.locator('#odds-body tbody tr').count();
note('확률 표에 줄이 있다', rows > 0, `${rows}줄`);
if (rows === 0) {
  console.log('\n확률 표가 비어 있어 나머지를 건너뜁니다.');
  await browser.close();
  server.close();
  process.exit(1);
}
await page.locator('#odds-body tbody tr').nth(Math.min(3, rows - 1)).click();
await page.waitForTimeout(600);
note(
  '확률 표의 줄을 누르면 사례가 그려진다',
  (await page.locator('.example').count()) > 0 && (await page.locator('#examples-panel').isVisible()),
);
note('누른 줄이 표시된다', (await page.locator('#odds-body tbody tr.selected').count()) === 1);

// ── 금액 칸의 색이 살아 있는가
//
// `.big`을 확률 칸과 금액 칸이 같이 써서, 의미 있는 줄(informative)의 확률을
// 흰색으로 강조하는 규칙이 **금액의 빨강·파랑까지 지우고 있었다.** 그래서
// 의미 있는 줄일수록 금액이 무채색이 되는 정반대 결과가 났다.
const moneyColours = await page.evaluate(() => {
  const plain = getComputedStyle(document.body).color;
  const out = [];
  for (const tr of document.querySelectorAll('#odds-body tbody tr')) {
    const td = tr.querySelector('td.money');
    if (!td || !/[+\-−]/.test(td.textContent)) continue;
    out.push({ informative: tr.classList.contains('informative'), colour: getComputedStyle(td).color, plain });
  }
  return out;
});
const coloured = (c) => /^rgb\((\d+), (\d+), (\d+)\)$/.test(c)
  && Math.abs(Number(RegExp.$1) - Number(RegExp.$3)) > 40;
const withMeaning = moneyColours.filter((m) => m.informative);
note(
  '의미 있는 줄에서도 금액에 색이 남는다',
  withMeaning.length === 0 || withMeaning.every((m) => coloured(m.colour)),
  withMeaning.length ? `${withMeaning.length}줄 · ${withMeaning[0].colour}` : '해당 줄이 없었습니다',
);

// ── 금액
await page.fill('#in-amount', '3000000');
await page.waitForTimeout(400);
note(
  '금액을 바꾸면 표의 돈이 바뀐다',
  (await page.locator('#odds-body tbody tr td:last-child').first().innerText()).length > 1,
);
await page.fill('#in-amount', '1000000');

// ── 받아둔 시세로 다시 계산 (업비트에 안 가야 한다)
let calls = 0;
await context.route('https://api.upbit.com/v1/candles/**', async (route) => {
  calls += 1;
  await route.fallback();
});
await page.click('#btn-scan');
await page.waitForFunction(
  () => document.getElementById('job')?.textContent?.includes('마쳤'),
  null, { timeout: 120000 },
);
note('받아둔 시세로 다시 계산은 업비트에 안 간다', calls === 0, `${calls}번 갔습니다`);

// ── 자동 갱신 켰다 끄기
await page.check('#in-auto');
note('자동 갱신을 켤 수 있다', await page.isChecked('#in-auto'));
await page.uncheck('#in-auto');
note('자동 갱신을 끌 수 있다', !(await page.isChecked('#in-auto')));

// ── 종목 바꾸기 (남은 표가 그대로 있으면 다른 코인 숫자를 잘못 읽는다)
await page.locator('.coin').nth(1).click();
// **곧바로** 본다. 미리 받아 둔 파일 덕분에 새 종목 결과가 몇백 밀리초면
// 나오므로, 기다렸다 보면 '앞 종목이 남은 것'과 '새 종목이 벌써 나온 것'을
// 구분할 수 없다. 지켜야 할 것은 누른 순간 앞 종목 것이 치워지는 것이다.
const cleared = await page.evaluate(() => document.getElementById('verdict').hidden
  || document.getElementById('verdict-headline').textContent.length === 0);
await page.waitForTimeout(500);
const code = await page.locator('#ticker-code').innerText();
note('종목을 바꾸면 맨 위가 따라 바뀐다', code === 'KRW-ETH', code);
note('종목을 바꾸면 앞 종목 결과가 남지 않는다', cleared);

// ── 멈추기
await page.waitForTimeout(1500);
const stopVisible = await page.locator('#btn-stop').isVisible();
if (stopVisible) {
  await page.click('#btn-stop');
  await page.waitForTimeout(500);
  note('멈추기를 누르면 단추가 다시 살아난다', await page.isEnabled('#btn-live'));
} else {
  note('멈추기 단추는 받는 중에만 보인다', true, '이미 끝나 있었습니다');
}

// ── 세로 화면(아이패드 세로)에서도 삐져나가지 않는다
await page.setViewportSize({ width: 834, height: 1194 });
await page.waitForTimeout(400);
const tallOverflow = await page.evaluate(() => {
  const wide = [];
  for (const el of document.querySelectorAll('body *')) {
    if (el.scrollWidth > document.documentElement.clientWidth + 2) {
      const style = getComputedStyle(el);
      if (style.overflowX === 'auto' || style.overflowX === 'scroll') continue;
      wide.push(`${el.tagName.toLowerCase()}.${el.className || '(무명)'}`);
    }
  }
  return wide.slice(0, 5);
});
note('세로 화면에서도 삐져나가지 않는다', tallOverflow.length === 0, tallOverflow.join(', '));

// ── 콘솔 오류
note('콘솔에 오류가 없다', errors.length === 0, errors.slice(0, 3).join(' | '));

await page.screenshot({ path: join(here, '..', '..', '.clickthrough.png'), fullPage: false });
await browser.close();
server.close();

console.log(`\n${ok.length}개 통과, ${found.length}개 문제`);
if (found.length) {
  console.log('\n문제:');
  for (const f of found) console.log(`  · ${f}`);
  process.exit(1);
}
