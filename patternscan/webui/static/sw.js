'use strict';

// 홈 화면에서 아이콘을 눌렀는데 흰 화면만 나오는 걸 막는 게 이 파일의 전부다.
//
// 이 도구는 계산을 파이썬이 한다. 서버가 꺼져 있으면 확률도 사례도 나올 수
// 없고, 그건 캐시로 어떻게 해볼 수 있는 일이 아니다. 그러니 여기서 노리는
// 것은 **"왜 안 되는지 말해 주는 화면"**까지는 뜨게 하는 것뿐이다.
//
// 그래서 규칙이 둘이다.
//
//   1. /api/* 는 절대 캐시하지 않는다. 시세를 캐시했다가 나중에 꺼내 주면
//      3시간 전 가격을 지금 가격이라고 보여주게 된다. 틀린 답을 주느니
//      실패하는 편이 낫다.
//   2. 나머지는 **항상 네트워크를 먼저** 본다. 캐시는 네트워크가 죽었을
//      때만 쓴다. 화면 코드가 바뀌었는데 옛날 것이 계속 뜨는 사고를
//      아예 만들지 않기 위해서다.

const CACHE = 'patternscan-shell-v1';

const SHELL = [
  '/',
  '/static/app.js',
  '/static/style.css',
  '/static/manifest.webmanifest',
  '/static/icon-192.png',
  '/static/apple-touch-icon.png',
];

const OFFLINE_PAGE = `<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>모양 찾기 — 서버가 꺼져 있습니다</title>
<style>
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:#0f1116; color:#e6e9ef; font:16px/1.7 -apple-system,BlinkMacSystemFont,
         'Apple SD Gothic Neo','Malgun Gothic',sans-serif; padding:2rem; }
  main { max-width:34rem; }
  h1 { font-size:1.35rem; margin:0 0 1rem; }
  code { background:#1a1d25; padding:.15rem .4rem; border-radius:4px; font-size:.9em; }
  ol { padding-left:1.2rem; } li { margin:.5rem 0; }
  p.dim { color:#8b93a7; font-size:.9rem; }
  button { margin-top:1.5rem; background:#3d7eff; color:#fff; border:0; border-radius:8px;
           padding:.7rem 1.4rem; font-size:1rem; cursor:pointer; }
</style></head>
<body><main>
  <h1>서버가 꺼져 있습니다</h1>
  <p>계산은 컴퓨터에서 도는 파이썬이 합니다. 아이콘은 홈 화면에 남아 있지만,
     그 계산을 해 줄 쪽이 지금 응답하지 않습니다.</p>
  <ol>
    <li>시세를 받아 오던 컴퓨터가 켜져 있는지 확인하세요.</li>
    <li>그 컴퓨터에서 <code>./start.sh --lan</code> 을 다시 실행하세요.</li>
    <li>같은 와이파이에 붙어 있는지 확인하세요.</li>
  </ol>
  <p class="dim">막힌 곳을 모르겠으면 그 컴퓨터에서
     <code>python -m patternscan doctor</code> 를 실행해 보세요.</p>
  <button onclick="location.reload()">다시 시도</button>
</main></body></html>`;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .catch(() => undefined)   // 하나 못 받았다고 설치를 접을 이유는 없다
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(
        names.filter((n) => n !== CACHE).map((n) => caches.delete(n))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // 규칙 1 — 시세와 계산 결과는 손대지 않는다.
  if (url.pathname.startsWith('/api/')) return;

  event.respondWith(freshFirst(request));
});

async function freshFirst(request) {
  try {
    const response = await fetch(request);
    if (response && response.ok) {
      const copy = response.clone();
      caches.open(CACHE).then((cache) => cache.put(request, copy)).catch(() => undefined);
    }
    return response;
  } catch (err) {
    const cached = await caches.match(request);
    if (cached) return cached;
    if (request.mode === 'navigate') {
      const shell = await caches.match('/');
      if (shell) return shell;
      return new Response(OFFLINE_PAGE, {
        status: 503,
        headers: { 'Content-Type': 'text/html; charset=utf-8' },
      });
    }
    throw err;
  }
}
