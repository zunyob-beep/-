// **화면을 실제로 찍어 본다.** 눈으로 봐야 아는 것이 있다.
//
// 그래프가 "너무 직선이고 대충"이라는 말은 숫자로 확인할 수 없다. 짐작으로
// 고치면 또 헛짚으므로, 진짜 데이터로 그린 것을 그대로 찍어서 본다.
//
//     node tests/js/shot.mjs [찍을 곳] [파일 이름]
//     node tests/js/shot.mjs '#ahead-panel' /tmp/ahead.png

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
const PICK = process.argv[2] ?? '#ahead-panel';
const OUT = process.argv[3] ?? '/tmp/shot.png';
const PERIOD = process.env.GISIGAM_PERIOD ?? '525600';

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
    if (rest.startsWith('/data/') || rest.startsWith('/history/')) {
      // 진짜 저장소에서 가져다 그대로 넘긴다.
      try {
        const answer = await fetch(`https://raw.githubusercontent.com/${REPO}${rest}`);
        const body = Buffer.from(await answer.arrayBuffer());
        res.writeHead(answer.status, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(body);
      } catch { res.writeHead(502).end(); }
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

const { server, port } = await serve();
const browser = await chromium.launch(launchOptions);
const context = await browser.newContext({
  viewport: { width: 1100, height: 900 }, deviceScaleFactor: 2,
});
for (const pattern of ['https://api.upbit.com/**', 'https://api.allorigins.win/**',
  'https://api.codetabs.com/**']) {
  await context.route(pattern, (route) => route.abort('failed'));
}
const page = await context.newPage();
await page.goto(`http://127.0.0.1:${port}${BASE}/`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.coin', { timeout: 20000 });
await page.selectOption('#in-period', PERIOD);
await page.click('#btn-live');
await page.waitForFunction(() => !document.getElementById('btn-live').disabled,
  null, { timeout: 300000 });
await page.waitForTimeout(800);

const box = page.locator(PICK);
if (await box.count() === 0 || await box.isHidden()) {
  console.log(`${PICK}가 안 보입니다.`);
} else {
  await box.screenshot({ path: OUT });
  console.log(`찍었습니다: ${OUT}`);
}
await browser.close();
server.close();
