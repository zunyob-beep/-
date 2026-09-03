// **진짜 데이터로 앱을 끝까지 돌려 본다.**
//
// 다른 시험은 전부 가짜 업비트와 가짜 파일 위에서 돈다 — 그래야 값이
// 매번 같아서 무엇과도 대조할 수 있다. 그런데 그것만으로는 답할 수 없는
// 물음이 하나 남는다.
//
//     **서버가 실제로 적어 둔 파일을, 앱이 실제로 읽어서, 판정까지 내는가?**
//
// 이 시험은 그것만 본다. `data`/`history` 브랜치의 진짜 파일을 그대로
// 가져다 앱에 물리고, 업비트로 가는 길은 전부 끊는다 (실제 사용자가 겪는
// 상태다 — 브라우저에서 업비트는 분당 6번이라 사실상 안 된다).
//
// 값이 매번 다르므로 **무엇과도 대조하지 않는다.** 대신 '말이 되는가'만
// 본다: 봉이 들어왔는가, 가격이 사람이 아는 범위인가, 판정이 떴는가.
//
// 바깥 세상을 부르므로 CI에서는 안 돈다. 손으로만 돌린다.
//
//     node tests/js/live.mjs

import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, extname, join, normalize } from 'node:path';
import { chromium } from 'playwright';
import { launchOptions } from './launch.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const ROOT = join(here, '..', '..', 'web');
const BASE = '/-';
const REPO = process.env.GISIGAM_REPO ?? 'zunyob-beep/-';

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.png': 'image/png',
  '.webmanifest': 'application/manifest+json; charset=utf-8',
};

const fetched = [];

/**
 * `data`/`history`를 **진짜 저장소에서** 가져다 그대로 넘긴다.
 *
 * 앱이 raw 주소를 스스로 짚으려면 호스트가 `*.github.io`여야 하는데
 * 여기서는 127.0.0.1이다. 그래서 같은 자리에서 대신 받아다 준다 — 앱이
 * 읽는 파일은 배포된 것과 **한 글자도 다르지 않다.**
 */
async function relay(rest, res) {
  const url = `https://raw.githubusercontent.com/${REPO}${rest}`;
  try {
    const answer = await fetch(url, { headers: { 'accept-encoding': 'gzip' } });
    const body = Buffer.from(await answer.arrayBuffer());
    fetched.push({ rest, status: answer.status, bytes: body.length });
    res.writeHead(answer.status, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(body);
  } catch (error) {
    fetched.push({ rest, status: 0, bytes: 0, why: error.message });
    res.writeHead(502).end();
  }
}

function serve() {
  const server = createServer(async (req, res) => {
    const asked = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    const rest = asked.startsWith(BASE) ? asked.slice(BASE.length) || '/' : null;
    if (rest === null) { res.writeHead(404).end(); return; }
    if (rest.startsWith('/data/') || rest.startsWith('/history/')) {
      await relay(rest, res);
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

// **업비트로 가는 길을 전부 끊는다.** 실제 사용자가 겪는 상태다 — 브라우저
// 요청은 분당 6번이라 사실상 안 된다. 미리 받아 둔 파일만으로 되는지 본다.
for (const pattern of [
  'https://api.upbit.com/**',
  'https://api.allorigins.win/**',
  'https://api.codetabs.com/**',
]) {
  await context.route(pattern, (route) => route.abort('failed'));
}

const page = await context.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
page.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });

console.log(`\n  ${REPO}의 진짜 파일로 돌립니다. 업비트는 끊었습니다.\n`);

await page.goto(url, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.coin', { timeout: 20000 });
await page.selectOption('#in-period', '10080');   // 7일
await page.click('#btn-live');

const ended = await page.waitForFunction(
  () => !document.getElementById('btn-live').disabled,
  null, { timeout: 180000 },
).then(() => true, () => false);
note('판이 끝난다', ended, (await page.locator('#job').innerText()).slice(0, 60));

// 실제로 저장된 개수를 IndexedDB에서 직접 센다. 화면이 스스로 계산한
// 숫자끼리 비교하면 틀린 채로 일관될 수 있다.
const kept = await page.evaluate(async () => {
  const db = await new Promise((resolve, reject) => {
    const open = indexedDB.open('gisigam');
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
  return new Promise((resolve) => {
    const tx = db.transaction('index', 'readonly');
    const all = tx.objectStore('index').getAll();
    all.onsuccess = () => resolve(all.result
      .filter((r) => r.market === 'KRW-BTC' && r.timeframe === 'minute1')
      .reduce((sum, r) => sum + r.n, 0));
    all.onerror = () => resolve(-1);
  });
});
note('진짜 파일에서 봉이 들어온다', kept > 2000, `${kept.toLocaleString()}개`);

note('판정이 떴다', !(await page.locator('#verdict').isHidden()),
  (await page.locator('#verdict-headline').innerText().catch(() => '')).slice(0, 40));

// **가격이 사람이 아는 범위인가.** 값을 대조할 수는 없지만, 자릿수가
// 어긋나면(배수를 잘못 읽으면) 여기서 바로 드러난다.
const price = await page.locator('#ticker-price').innerText().catch(() => '');
const won = Number(price.replace(/[^\d]/g, ''));
note('맨 위 시세가 말이 되는 값이다',
  won > 1_000_000 && won < 10_000_000_000, price);

const coverage = await page.locator('#coverage').innerText().catch(() => '');
note('받아둔 시세 범위가 적힌다', /\d[\d,]*개/.test(coverage),
  coverage.split('\n').slice(0, 2).join(' ').slice(0, 60));

note('콘솔에 오류가 없다', errors.length === 0, errors.slice(0, 2).join(' | '));

console.log('\n  내려받은 파일');
for (const f of fetched) {
  console.log(`    ${String(f.status).padStart(3)}  ${(f.bytes / 1024).toFixed(0).padStart(6)}KB  ${f.rest}${f.why ? `  ${f.why}` : ''}`);
}

console.log(`\n${ok.length}개 통과, ${found.length}개 문제`);
if (found.length) {
  console.log('\n문제:');
  for (const line of found) console.log(`  · ${line}`);
}

await browser.close();
server.close();
process.exit(found.length ? 1 : 0);
