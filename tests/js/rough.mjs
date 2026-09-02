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
  const handler = async (route, viaDetour = false) => {
    const mode = getMode();
    // **직접만 막힌 상태.** 실제로 겪은 그것이다 — 브라우저에서 직접 부르는
    // 길은 막혔는데 우회로는 받아진다. 앱이 스스로 돌아서는지 보는 자리다.
    if (mode === 'direct-walled' && !viaDetour) {
      return route.fulfill({ status: 429, body: '', headers: {} });
    }
    if (mode === 'dead') return route.abort('failed');
    // **실제 조건.** 업비트가 가끔 거절한다 — 세 번에 한 번. 거절 응답에는
    // 허용 표시가 없으므로 브라우저가 막고, 앱은 TypeError만 본다.
    // 이 상태에서 **끝까지 받아지는지**가 진짜 물음이다.
    if (mode === 'flaky') {
      flaky += 1;
      if (flaky % 3 === 0) return route.fulfill({ status: 429, body: '', headers: {} });
    }
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
  // 플레이라이트는 처리기를 (route, request)로 부른다. handler를 그대로
  // 넘기면 두 번째 인자(Request)가 viaDetour 자리에 들어가 **늘 참**이 된다 —
  // '직접만 막힘' 흉내가 통째로 안 먹었다. 인자를 우리가 정해서 넘긴다.
  await context.route('https://api.upbit.com/**', (route) => handler(route, false));
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
      }, true);
    });
  }
}

let flaky = 0;

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
// 어느 길로도 못 받으면 그렇다고 말하고, **무엇을 하면 되는지**까지 말해야
// 한다. "막혔습니다"로 끝나면 사용자는 할 수 있는 게 없다.
note('어느 길로도 못 받으면 그렇다고 말한다', said.includes('못 받고'), said.slice(0, 40));
note('그때 무엇을 하면 되는지 알려준다', said.includes('우회 주소'), said.slice(0, 40));
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

// ── 5-3. **직접만 막히고 우회는 되는 상태** — 실제로 겪은 그것
//
// 여기가 이 앱의 마지막 방어선이다. 직접 길이 막혀도 우회로 받아지면
// 사용자는 아무것도 안 해도 된다. 진단이 그걸 말해 줘야 하고, 무엇보다
// **실제로 받아져야** 한다.
mode = 'direct-walled';
await page.click('#btn-diag');
await page.waitForSelector('#btn-diag-copy', { timeout: 90000 });
const detourSaid = await page.locator('.diag-said').innerText();
note('우회가 되면 "받을 수 있다"고 말한다',
  detourSaid.includes('받을 수 있습니다'), detourSaid.slice(0, 40));


// 그리고 진짜로 받아 온다. 말만 하고 안 받아지면 아무 소용이 없다.
await page.evaluate(() => { document.getElementById('btn-forget')?.click(); });
await page.waitForTimeout(1000);
await page.click('#btn-live');
await page.waitForFunction(
  () => document.getElementById('job')?.textContent?.includes('마쳤'),
  null, { timeout: 300000 },
);
const viaDetourText = await page.locator('#coverage').innerText();
note('직접이 막혀도 우회로 실제로 받아 온다', /\d[\d,]*개/.test(viaDetourText),
  viaDetourText.split('\n').slice(0, 2).join(' ').slice(0, 46));

mode = 'walled';
await page.click('#btn-diag-copy');
await page.waitForTimeout(500);
const copyLabel = await page.locator('#btn-diag-copy').innerText().catch(() => '');
note('결과 복사가 눌린다', copyLabel.length > 0 || (await page.locator('.diag-text').count()) > 0,
  copyLabel);

// ── 5-4. **거절이 섞인 실제 조건에서 끝까지 받아지는가**
//
// "전혀 작동을 안 한다"는 말을 듣고 만든 것이다. 여태 시험은 업비트가
// 완벽하거나 완벽히 막힌 두 극단만 봤다. 진짜 조건은 그 사이다 — 가끔
// 거절당하면서 조금씩 쌓이는 것.
//
// 그리고 **화면에 뜬 개수가 실제로 저장된 개수와 같은지**까지 본다.
// "총 개수도 틀리다"는 지적이 정확히 이 자리였다.
mode = 'flaky';
await page.evaluate(() => { document.getElementById('btn-forget')?.click(); });
await page.waitForTimeout(1000);
await page.click('#btn-live');
await page.waitForFunction(
  () => document.getElementById('job')?.textContent?.includes('마쳤'),
  null, { timeout: 300000 },
);
const shown = await page.evaluate(() => {
  const seen = document.getElementById('progress-count')?.textContent?.match(/([\d,]+) \/ ([\d,]+)/);
  return seen ? [Number(seen[1].replace(/,/g, '')), Number(seen[2].replace(/,/g, ''))] : null;
});
const stored = await page.evaluate(async () => {
  const db = await new Promise((resolve, reject) => {
    const open = indexedDB.open('gisigam');
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
  return new Promise((resolve) => {
    const tx = db.transaction('index', 'readonly');
    const all = tx.objectStore('index').getAll();
    all.onsuccess = () => resolve(
      all.result
        .filter((r) => r.market === 'KRW-BTC' && r.timeframe === 'minute1')
        .reduce((sum, r) => sum + r.n, 0),   // 색인 칸 이름은 n이다
    );
    all.onerror = () => resolve(-1);
  });
});
const jobText = (await page.locator('#job').innerText()).slice(0, 40);
note('거절이 섞여도 끝까지 받아 온다', stored > 1000,
  `실제로 저장된 봉 ${stored}개 · ${jobText}`);
note('화면에 뜬 개수가 실제 저장된 개수와 같다',
  shown !== null && Math.abs(shown[0] - stored) <= 5,
  shown ? `화면 ${shown[0].toLocaleString()} / ${shown[1].toLocaleString()} · 실제 ${stored.toLocaleString()}` : '못 읽음');

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
