// **새 판을 밀어 넣으면 열려 있던 화면이 알아서 갈아타는가.**
//
// 이 세션 내내 가장 헷갈렸던 문제다. 고쳐서 배포했는데 아이패드에는 그대로다.
// 서비스 워커는 빨리 뜨라고 캐시부터 주므로 **이미 열려 있던 화면은 옛 파일
// 그대로**이기 때문인데, 화면만 봐서는 "안 고쳐졌다"와 "옛 판을 보고 있다"를
// 구분할 수가 없다. 그래서 매번 "됐나요?"를 되묻게 됐다.
//
// 여기서는 진짜로 그 상황을 만든다 — 앱을 띄워 서비스 워커를 심고, 파일을
// 바꿔 새 판을 낸 다음, 열려 있던 화면이 스스로 새 번호로 바뀌는지 본다.
//
//     node tests/js/upgrade.mjs

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

/**
 * 서버가 내주는 파일을 중간에 바꿔치기할 수 있게 해 둔다.
 * 새 판을 내는 것을 흉내 내려면 같은 주소가 다른 내용을 줘야 한다.
 */
const swap = new Map();

function serve() {
  const server = createServer(async (req, res) => {
    const asked = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    const rest = asked.startsWith(BASE) ? asked.slice(BASE.length) || '/' : null;
    if (rest === null) { res.writeHead(404).end(); return; }
    const name = rest === '/' ? '/index.html' : rest;
    const path = join(ROOT, normalize(name));
    if (!path.startsWith(ROOT)) { res.writeHead(403).end(); return; }
    try {
      const body = swap.has(name) ? swap.get(name) : await readFile(path);
      res.writeHead(200, {
        'Content-Type': TYPES[extname(path)] ?? 'application/octet-stream',
        'Cache-Control': 'no-store',
      });
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
// 업비트는 안 부른다. 여기서 보는 건 판 갈아타기뿐이다.
await context.route('https://api.upbit.com/**', (route) => route.abort('failed'));
// 직접 길이 막히면 앱이 공개 우회 서버로 돌아선다. 시험이 바깥 세상을
// 부르면 안 되므로 그 길도 끊는다.
for (const pattern of ['https://api.allorigins.win/**', 'https://api.codetabs.com/**']) {
  await context.route(pattern, (route) => route.abort('failed'));
}
const page = await context.newPage();

await page.goto(url, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.coin', { timeout: 20000 });
const first = (await page.locator('#version').innerText()).trim();
note('첫 판이 뜬다', /^v\d+$/.test(first), first);

// 서비스 워커가 자리를 잡을 때까지 기다린다. 안 심겼으면 갈아탈 것도 없다.
const controlled = await page.evaluate(async () => {
  if (!('serviceWorker' in navigator)) return false;
  await navigator.serviceWorker.ready;
  for (let i = 0; i < 40 && !navigator.serviceWorker.controller; i += 1) {
    await new Promise((r) => { setTimeout(r, 250); });
  }
  return Boolean(navigator.serviceWorker.controller);
});
note('서비스 워커가 화면을 맡는다', controlled);

// **한 번 다시 연다.**
//
// 처음 들어오면 워커가 없다가 심기는 것이라, 그때의 controllerchange는
// 갈아타기가 아니다. 실제로 갈아타는 상황은 '두 번째로 열었을 때' —
// 열자마자 이미 워커가 맡고 있는 상태다. 그 상황을 만든다.
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('.coin', { timeout: 20000 });
note(
  '다시 열면 처음부터 워커가 맡고 있다',
  await page.evaluate(() => Boolean(navigator.serviceWorker.controller)),
);

// ── 새 판을 낸다. version.js와 sw.js를 함께 바꿔야 실제 배포와 같다.
const NEXT = 'v99';
const version = await readFile(join(ROOT, 'version.js'), 'utf8');
const sw = await readFile(join(ROOT, 'sw.js'), 'utf8');
swap.set('/version.js', version.replace(/VERSION = '[^']+'/, `VERSION = '${NEXT}'`));
swap.set('/sw.js', sw.replace(/const CACHE = '[^']+'/, `const CACHE = 'gisigam-${NEXT}'`));

// 브라우저가 새 sw.js를 집어 오게 한다. 실제로는 다음에 열 때 일어난다.
await page.evaluate(async () => {
  const reg = await navigator.serviceWorker.getRegistration();
  await reg?.update();
});

// ── 열려 있던 화면이 스스로 새 번호로 바뀌어야 한다
const swapped = await page.waitForFunction(
  (want) => document.getElementById('version')?.textContent?.trim() === want,
  NEXT, { timeout: 30000 },
).then(() => true, () => false);
note(
  '새 판이 올라오면 열려 있던 화면이 알아서 갈아탄다',
  swapped,
  `${first} → ${(await page.locator('#version').innerText()).trim()}`,
);

// ── 옛 캐시가 남아 있으면 안 된다
const caches = await page.evaluate(() => window.caches.keys());
note('옛 판 캐시를 지운다', caches.length === 1 && caches[0].endsWith(NEXT), caches.join(', '));

await browser.close();
server.close();

console.log(`\n${ok.length}개 통과, ${found.length}개 문제`);
if (found.length) {
  console.log('\n문제:');
  for (const f of found) console.log(`  · ${f}`);
  process.exit(1);
}
