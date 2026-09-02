// 홈 화면에서 아이콘을 눌렀을 때 **인터넷이 없어도 앱이 뜨게** 하는 게
// 이 파일의 전부다.
//
// 예전 판과 달라진 점
// ------------------
// 서버가 없어졌다. 계산은 이 기기 안에서 하고, 받아둔 시세도 이 기기 안에
// 있다. 그러니 인터넷이 없어도 **앱은 온전히 돌아간다** — 새 시세만 못
// 받을 뿐, 받아둔 것으로 다시 계산하는 건 그대로 된다.
//
// 그래서 예전처럼 "서버가 꺼졌습니다" 안내 화면을 띄우는 게 아니라,
// 진짜 앱을 띄워야 한다.
//
// 규칙은 둘이다.
//
//   1. **업비트 요청은 절대 캐시하지 않는다.** 시세를 캐시했다가 나중에
//      꺼내 주면 세 시간 전 가격을 지금 가격이라고 보여주게 된다. 틀린
//      답을 주느니 실패하는 편이 낫다.
//   2. 앱 파일은 **캐시를 먼저 주고 뒤에서 새로 받아 둔다.** 아이패드에서
//      즉시 뜨는 게 중요하고, 바뀐 코드는 다음 번에 적용되면 된다.

// 판 번호를 여기 넣는다. 번호가 바뀌면 캐시 이름이 바뀌고, activate에서
// 예전 이름을 통째로 지운다. 그래야 고친 것이 실제로 화면까지 간다 —
// 서비스 워커가 옛 파일을 붙들고 있으면 아무리 밀어 넣어도 안 보인다.
const CACHE = 'gisigam-v38';

// 상대 경로로 적는다. GitHub Pages는 저장소 이름이 붙은 하위 경로에
// 얹히므로 /로 시작하면 엉뚱한 곳을 가리킨다.
const SHELL = [
  './',
  './index.html',
  './app.js',
  './version.js',
  './worker.js',
  './style.css',
  './manifest.webmanifest',
  './icon-192.png',
  './apple-touch-icon.png',
  './core/analysis.js',
  './core/data.js',
  './core/format.js',
  './core/levels.js',
  './core/models.js',
  './core/odds.js',
  './core/search.js',
  './core/shape.js',
  './core/stats.js',
  './core/store.js',
  './core/theories.js',
  './core/upbit.js',
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    // 하나가 실패해도 나머지는 담는다. 파일 하나 때문에 설치가 통째로
    // 실패하면 앱이 아예 오프라인에서 안 뜬다.
    await Promise.all(SHELL.map((url) => cache.add(url).catch(() => undefined)));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  // 업비트는 손대지 않는다. 캐시된 시세는 틀린 시세다.
  if (url.origin !== self.location.origin) return;

  // **연결 확인용 요청은 절대 캐시에서 답하지 않는다.**
  //
  // core/upbit.js는 업비트에 못 닿았을 때 '인터넷이 끊긴 것'과 '업비트만
  // 막힌 것'을 가르려고 우리 쪽 파일을 하나 불러 본다. 그런데 여기서
  // 캐시로 답해 버리면 인터넷이 끊겨 있어도 성공해서, 늘 '업비트만
  // 막혔다'고 잘못 말하게 된다. 진단하려고 만든 요청을 진단이 못 하게
  // 막는 셈이라, 이 갈래만 통째로 비켜 준다.
  if (url.searchParams.has('ping')) return;

  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const hit = await cache.match(request, { ignoreSearch: true });

    // 뒤에서 새 것을 받아 둔다. 실패해도 조용히 넘어간다 — 인터넷이 없는
    // 상황이 바로 이 캐시를 쓰는 상황이다.
    const fresh = fetch(request).then((response) => {
      if (response && response.ok) cache.put(request, response.clone());
      return response;
    }).catch(() => null);

    if (hit) return hit;
    const response = await fresh;
    if (response) return response;

    // 캐시에도 없고 네트워크도 안 되는 경우. 페이지 요청이면 첫 화면이라도
    // 준다 — 흰 화면보다는 낫다.
    if (request.mode === 'navigate') {
      const shell = await cache.match('./index.html');
      if (shell) return shell;
    }
    return new Response('오프라인입니다.', {
      status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    });
  })());
});
