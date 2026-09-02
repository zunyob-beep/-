// **험한 상황에서도 버티는가.**
//
// clickthrough.mjs는 잘 되는 길을 걸어 본다. 이건 반대다 — 잘못된 값을 넣고,
// 캐시를 지우고, 업비트를 처음부터 막고, 인터넷을 끊는다.
//
// 이런 자리가 조용히 터지기 쉽다. 화면은 멀쩡해 보이는데 단추가 안 살아나거나,
// 숫자 칸에 NaN이 찍히거나, 오프라인에서 흰 화면이 뜨는 식이다. 사용자는
// 그걸 "고장났다"로만 인식하고 다시 안 연다.
//
//     node tests/js/rough.mjs

import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, extname, join, normalize } from 'node:path';
import { chromium } from 'playwright';
import { launchOptions } from './launch.mjs';

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

function serve() {
  const server = createServer(async (req, res) => {
    const asked = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    const rest = asked.startsWith(BASE) ? asked.slice(BASE.length) || '/' : null;
    if (rest === null) { res.writeHead(404).end(); return; }
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

const STEP = { 1: 60, 3: 180, 5: 300 };
const priceAt = (ts) => 50000000 * (1 + 0.02 * Math.sin(ts / 600));
const fakeCandle = (ts, market) => ({
  market,
  candle_date_time_utc: new Date(ts * 1000).toISOString().slice(0, 19),
  opening_price: priceAt(ts - 60),
  high_price: priceAt(ts) * 1.0004,
  low_price: priceAt(ts) * 0.9996,
  trade_price: priceAt(ts),
  candle_acc_trade_volume: 2 + (ts % 7) / 3,
});

/**
 * **우회 주소도 가로챈다.**
 *
 * 앱은 직접 가는 길이 막히면 공개 우회 서버로 돌아선다. 그런데 시험이
 * api.upbit.com만 막아 두면, 그 순간부터 **진짜 남의 서버로 요청이 나간다.**
 * 시험이 바깥 세상을 부르면 안 된다 — 느리고, 남에게 폐가 되고, 그 서버가
 * 죽은 날 CI가 같이 죽는다.
 *
 * 우회 주소는 업비트 주소를 통째로 싣고 있으므로, 그걸 꺼내서 같은 가짜
 * 업비트에게 물어보면 된다. 그러면 우회로 넘어가는 길목까지 그대로 시험된다.
 */
const DETOURS = ['https://api.allorigins.win/**', 'https://api.codetabs.com/**'];
const innerUrl = (url) => {
  const asked = new URL(url);
  for (const [, value] of asked.searchParams) {
    if (value.startsWith('https://api.upbit.com')) return value;
  }
  return null;
};

/** 업비트를 흉내 낸다. `mode`로 어떤 험한 상황인지 고른다. */
async function stubUpbit(context, getMode) {
  const handler = async (route) => {
    const mode = getMode();
    if (mode === 'dead') return route.abort('failed');
    if (mode === 'walled') {
      // **실제로 겪은 그 상태를 그대로 흉내 낸다.**
      //
      // 업비트가 거절 응답을 주는데 거기에 허용 표시(CORS 헤더)가 없다.
      // 그러면 보통 요청은 브라우저가 막아서 실패하고, no-cors 요청은
      // 내용을 못 읽을 뿐 성공한다. 헤더를 안 붙이면 브라우저가 알아서
      // 그렇게 갈라 주므로, 요청 종류를 우리가 알아낼 필요가 없다.
      return route.fulfill({ status: 429, body: '', headers: {} });
    }
    const url = new URL(route.request().url());
    if (mode === 'bars-walled' && url.pathname.startsWith('/v1/candles/')) {
      // **맥 사파리에서 실제로 나온 상태.** 현재가는 49ms에 되는데 봉만
      // 전부 안 된다. 차단이라면 현재가도 막히므로 이건 다른 종류다 —
      // 앱이 이 둘을 섞어 말하면 사용자는 기다리면 될 줄 알고 밤을 새운다.
      return route.fulfill({ status: 429, body: '', headers: {} });
    }
    const json = (body) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/v1/ticker') {
      const now = Math.floor(Date.now() / 1000);
      return json([{
        market: url.searchParams.get('markets').split(',')[0],
        trade_price: priceAt(now),
        signed_change_rate: 0.0123,
        signed_change_price: 610000,
        high_price: priceAt(now) * 1.01,
        low_price: priceAt(now) * 0.99,
      }]);
    }
    const step = STEP[Number(url.pathname.split('/').pop())] ?? 60;
    const count = Number(url.searchParams.get('count') ?? 200);
    const to = url.searchParams.get('to');
    const end = to ? Math.floor(Date.parse(to) / 1000) : Math.floor(Date.now() / 1000);
    const last = end - (end % step);
    return json(Array.from(
      { length: count },
      (_, i) => fakeCandle(last - i * step, url.searchParams.get('market')),
    ));
  };
  await context.route('https://api.upbit.com/**', handler);
  // 우회로 넘어가도 바깥으로 나가지 않게, 안에 실린 업비트 주소를 꺼내
  // 같은 손으로 받는다.
  for (const pattern of DETOURS) {
    // eslint-disable-next-line no-await-in-loop
    await context.route(pattern, async (route) => {
      const inner = innerUrl(route.request().url());
      if (!inner) return route.abort('failed');
      return handler({
        ...route,
        request: () => ({ url: () => inner }),
        abort: (...a) => route.abort(...a),
        fulfill: (...a) => route.fulfill(...a),
      });
    });
  }
}

const found = [];
const ok = [];
const note = (name, good, detail = '') => {
  (good ? ok : found).push(`${name}${detail ? `  ${detail}` : ''}`);
  console.log(`  ${good ? 'ok  ' : '문제'}  ${name}${detail ? `  ${detail}` : ''}`);
};

const { server, port } = await serve();
const url = `http://127.0.0.1:${port}${BASE}/`;
const browser = await chromium.launch(launchOptions);
const context = await browser.newContext({ viewport: { width: 1024, height: 768 } });

let mode = 'dead';
await stubUpbit(context, () => mode);
const page = await context.newPage();

const errors = [];
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
page.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });

// ── 1. 처음부터 업비트가 막혀 있다 (받아둔 것도 없다)
//
// 제일 나쁜 첫인상이다. 아무것도 없는데 아무 말도 없으면 사용자는 고장난
// 줄 안다. 단추가 다시 살아나야 하고, 왜 안 되는지 말해 줘야 한다.
await page.goto(url, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.coin', { timeout: 20000 });
await page.click('#btn-live');
await page.waitForFunction(
  () => !document.getElementById('blocked').hidden
    || !document.getElementById('error').hidden,
  null, { timeout: 120000 },
);
note('처음부터 막혀 있으면 이유를 말해 준다',
  (await page.locator('#blocked').innerText()).length > 10,
  (await page.locator('#blocked').innerText()).split('\n')[0].slice(0, 40));
await page.waitForFunction(() => !document.getElementById('btn-live').disabled,
  null, { timeout: 30000 });
note('막혀도 단추가 다시 살아난다', await page.isEnabled('#btn-live'));

// ── 2. 잘못된 값을 넣는다
//
// 숫자 칸은 사람이 무엇이든 넣을 수 있다. 0개 봉으로 비교하라거나 상관계수
// 2를 넣었을 때 NaN이 표에 찍히면 안 된다.
mode = 'ok';
await page.fill('#in-length', '0');
await page.fill('#in-similarity', '2');
await page.fill('#in-amount', '0');
await page.click('#btn-live');
await page.waitForFunction(
  () => {
    const job = document.getElementById('job').textContent;
    return job.includes('마쳤') || job.includes('못 받') || !document.getElementById('error').hidden;
  },
  null, { timeout: 300000 },
);
const body = await page.locator('main').innerText();
note('잘못된 값을 넣어도 NaN이 화면에 안 찍힌다',
  !body.includes('NaN') && !body.includes('undefined'),
  body.match(/NaN|undefined/g)?.slice(0, 3).join(', ') ?? '');
note('잘못된 값을 넣어도 단추가 살아 있다', await page.isEnabled('#btn-live'));

// 되돌린다
await page.fill('#in-length', '20');
await page.fill('#in-similarity', '0.85');
await page.fill('#in-amount', '1000000');

// ── 3. 제대로 한 번 받는다 (다음 시험들의 재료)
await page.click('#btn-live');
await page.waitForFunction(
  () => document.getElementById('job')?.textContent?.includes('마쳤'),
  null, { timeout: 300000 },
);
const before = await page.locator('#coverage').innerText();
note('제대로 받으면 받아둔 시세가 표시된다', /\d[\d,]*개/.test(before));

// ── 4. 받아둔 시세 지우기
const forget = page.locator('#btn-forget');
if (await forget.count()) {
  await forget.click();
  await page.waitForTimeout(1500);
  const after = await page.locator('#coverage').innerText();
  note('받아둔 시세를 지울 수 있다', after.includes('아직 받아둔 시세가 없습니다'),
    after.split('\n').slice(0, 2).join(' ').slice(0, 50));
} else {
  note('받아둔 시세 지우기 단추가 있다', false, '못 찾았습니다');
}

// ── 5. 진단 단추와 결과 복사
mode = 'walled';
await page.click('#btn-diag');
await page.waitForSelector('#btn-diag-copy', { timeout: 60000 });
const said = await page.locator('.diag-said').innerText();
note('막힌 상태를 진단이 제대로 읽는다', said.includes('막고 있'), said.slice(0, 46));
// ── 5-2. 현재가는 되는데 봉만 막히는 상태
//
// 이 표를 사용자가 보내 왔다 — 현재가 49ms 성공, 봉은 to 없이 1개짜리도 실패.
// 예전 진단은 현재가가 됐다는 이유로 이 상태를 그냥 지나쳤고, 화면에는
// "업비트에 닿지 못했습니다"라는 **사실이 아닌 말**이 떴다. 그러면 사용자는
// 기다리면 되는 줄 알고 계속 누른다.
mode = 'bars-walled';
await page.click('#btn-diag');
await page.waitForSelector('#btn-diag-copy', { timeout: 60000 });
const barsSaid = await page.locator('.diag-said').innerText();
note('현재가는 되고 봉만 막힌 것을 가려낸다', barsSaid.includes('봉'), barsSaid.slice(0, 46));
note('기다리면 된다고 하지 않는다', !barsSaid.includes('닿고 있'), barsSaid.slice(0, 46));

await page.click('#btn-diag-copy');
await page.waitForTimeout(500);
const copyLabel = await page.locator('#btn-diag-copy').innerText().catch(() => '');
note('결과 복사가 눌린다', copyLabel.length > 0 || (await page.locator('.diag-text').count()) > 0,
  copyLabel);

// ── 6. 오프라인에서도 앱이 뜬다 (서비스 워커)
//
// 홈 화면 아이콘을 눌렀는데 흰 화면이 뜨면 그걸로 끝이다. 인터넷이 없어도
// 받아둔 시세로 계산하는 건 그대로 돼야 한다.
await page.evaluate(async () => {
  if ('serviceWorker' in navigator) await navigator.serviceWorker.ready;
});
await context.setOffline(true);
await page.reload({ waitUntil: 'domcontentloaded' }).catch(() => {});
const alive = await page.locator('.coin').count().catch(() => 0);
note('인터넷이 끊겨도 앱이 뜬다', alive === 4, `종목 단추 ${alive}개`);
await context.setOffline(false);

// ── 콘솔 오류. 험한 길에서도 조용히 터지면 안 된다.
//
// 업비트를 일부러 막았으므로 그쪽 실패는 정상이다. 그건 빼고 센다.
const real = errors.filter((e) => !/Failed to (load resource|fetch)|net::ERR|api\.upbit/i.test(e));
note('험한 길에서도 콘솔 오류가 없다', real.length === 0, real.slice(0, 3).join(' | '));

await browser.close();
server.close();

console.log(`\n${ok.length}개 통과, ${found.length}개 문제`);
if (found.length) {
  console.log('\n문제:');
  for (const f of found) console.log(`  · ${f}`);
  process.exit(1);
}
