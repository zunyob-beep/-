// **미리 받아 둔 봉을 읽어 오는 자리.** 이제 앱은 여기로 돈다.
//
// 업비트가 아니라 이 파일이 주된 길이므로, 여기가 조용히 깨지면 앱이
// 통째로 못 돈다. 그래서 시험이 다른 어느 곳보다 촘촘하다.

import assert from 'node:assert/strict';
import test from 'node:test';

import { loadSeed, seedName, seedUrls } from '../../web/core/seed.js';

const PAGES = { href: 'https://zunyob-beep.github.io/-/worker.js', hostname: 'zunyob-beep.github.io', pathname: '/-/worker.js' };
const LOCAL = { href: 'http://127.0.0.1:8080/-/worker.js', hostname: '127.0.0.1', pathname: '/-/worker.js' };

const rows = (n, from = 1700000000) => Array.from(
  { length: n }, (_, i) => [from + i * 60, 100 + i, 102 + i, 98 + i, 101 + i, 1.5],
);
const file = (market, n = 5, extra = {}) => ({
  market, timeframe: 'minute1', step: 60, made: 1700000600, rows: rows(n), ...extra,
});
const ok = (body) => ({ status: 200, json: async () => body });

test('같은 주소 아래를 먼저 물어본다', () => {
  const urls = seedUrls('KRW-BTC', PAGES);
  assert.equal(urls[0], 'https://zunyob-beep.github.io/-/data/KRW-BTC.min1.json');
});

test('깃허브 페이지면 raw 주소도 안다', () => {
  // 여기가 파일이 실제로 사는 곳이다. 저장소 이름이 `-` 하나여도 된다.
  const urls = seedUrls('KRW-BTC', PAGES);
  assert.ok(
    urls.includes('https://raw.githubusercontent.com/zunyob-beep/-/data/KRW-BTC.min1.json'),
    `raw 주소가 없습니다: ${urls.join(', ')}`,
  );
});

test('주소를 코드에 박지 않는다 — 갈라 가도 자기 것을 읽는다', () => {
  const forked = { href: 'https://someone.github.io/patternscan/worker.js', hostname: 'someone.github.io', pathname: '/patternscan/worker.js' };
  const urls = seedUrls('KRW-ETH', forked);
  assert.ok(urls.some((u) => u.includes('/someone/patternscan/data/')), urls.join(', '));
  assert.ok(!urls.some((u) => u.includes('zunyob')), '남의 저장소를 읽습니다');
});

test('사용자 페이지(owner.github.io)도 맞게 짚는다', () => {
  const user = { href: 'https://someone.github.io/worker.js', hostname: 'someone.github.io', pathname: '/worker.js' };
  assert.ok(
    seedUrls('KRW-BTC', user).includes('https://raw.githubusercontent.com/someone/someone.github.io/data/KRW-BTC.min1.json'),
  );
});

test('깃허브가 아니면 같은 주소만 본다', () => {
  // 개발 중이거나 다른 데 올렸을 때 남의 저장소를 부르면 안 된다.
  const urls = seedUrls('KRW-BTC', LOCAL);
  assert.equal(urls.length, 1);
  assert.ok(!urls[0].includes('githubusercontent'));
});

test('파일 이름은 서버가 적는 것과 같다', () => {
  // tools/candles.py가 `<종목>.min1.json`으로 적는다. 어긋나면 조용히 안 된다.
  assert.equal(seedName('KRW-SOL'), 'KRW-SOL.min1.json');
});

test('읽어 오면 봉이 된다', async () => {
  const got = await loadSeed('KRW-BTC', {
    where: LOCAL, fetcher: async () => ok(file('KRW-BTC', 3)),
  });
  assert.equal(got.candles.length, 3);
  assert.deepEqual(got.candles[0], {
    ts: 1700000000, open: 100, high: 102, low: 98, close: 101, volume: 1.5,
  });
  assert.equal(got.made, 1700000600);
});

test('오래된 것부터 정렬해서 준다', async () => {
  const shuffled = { ...file('KRW-BTC', 0), rows: [...rows(4)].reverse() };
  const got = await loadSeed('KRW-BTC', { where: LOCAL, fetcher: async () => ok(shuffled) });
  const stamps = got.candles.map((c) => c.ts);
  assert.deepEqual(stamps, [...stamps].sort((a, b) => a - b));
});

test('첫 자리가 없으면 다음 자리로 간다', async () => {
  // 깃허브 페이지에는 ./data/가 없다 — 파일은 raw 쪽에 산다.
  const asked = [];
  const got = await loadSeed('KRW-BTC', {
    where: PAGES,
    fetcher: async (url) => {
      asked.push(url);
      if (url.includes('github.io')) return { status: 404, json: async () => ({}) };
      return ok(file('KRW-BTC', 2));
    },
  });
  assert.equal(got.candles.length, 2);
  assert.equal(asked.length, 2, '두 자리를 다 물어봐야 합니다');
});

test('아무 데서도 못 읽으면 null — 던지지 않는다', async () => {
  // **여기서 던지면 지름길이 막혔다는 이유로 판 전체가 죽는다.**
  const got = await loadSeed('KRW-BTC', {
    where: PAGES, fetcher: async () => { throw new TypeError('Load failed'); },
  });
  assert.equal(got, null);
});

test('JSON이 아니어도 던지지 않는다', async () => {
  // 깃허브 페이지의 404는 HTML을 돌려준다. 그걸 json()으로 읽으면 터진다.
  const got = await loadSeed('KRW-BTC', {
    where: LOCAL,
    fetcher: async () => ({ status: 200, json: async () => { throw new SyntaxError('Unexpected token <'); } }),
  });
  assert.equal(got, null);
});

test('다른 종목 파일이 오면 안 쓴다', async () => {
  // 캐시가 엉키거나 주소가 잘못 짜이면 실제로 일어날 수 있다. 이더리움
  // 봉으로 비트코인을 계산하면 화면에는 그럴듯한 숫자가 그대로 찍힌다.
  const got = await loadSeed('KRW-BTC', {
    where: LOCAL, fetcher: async () => ok(file('KRW-ETH', 3)),
  });
  assert.equal(got, null);
});

test('비어 있으면 안 쓴다', async () => {
  const got = await loadSeed('KRW-BTC', {
    where: LOCAL, fetcher: async () => ok(file('KRW-BTC', 0)),
  });
  assert.equal(got, null);
});

test('망가진 줄은 버리고 나머지는 쓴다', async () => {
  const broken = {
    ...file('KRW-BTC', 0),
    rows: [[1700000000, 1, 2, 3, 4, 5], 'nope', [1, 2], [1700000060, 1, 2, 3, 4, 5]],
  };
  const got = await loadSeed('KRW-BTC', { where: LOCAL, fetcher: async () => ok(broken) });
  assert.equal(got.candles.length, 2);
});

test('무엇을 어디서 읽었는지 알려 준다', async () => {
  // 진단 화면이 이걸 업비트 요청과 같은 자리에 편다. 한 장으로 끝나야 한다.
  const seen = [];
  await loadSeed('KRW-BTC', {
    where: PAGES,
    onTry: (url, how) => seen.push([url, how]),
    fetcher: async (url) => (url.includes('github.io')
      ? { status: 404, json: async () => ({}) }
      : ok(file('KRW-BTC', 7))),
  });
  assert.equal(seen.length, 2);
  assert.equal(seen[0][1], '404');
  assert.equal(seen[1][1], '7개');
});

test('made가 없으면 마지막 봉 시각으로 친다', async () => {
  const got = await loadSeed('KRW-BTC', {
    where: LOCAL, fetcher: async () => ok({ ...file('KRW-BTC', 3), made: undefined }),
  });
  assert.equal(got.made, got.candles[got.candles.length - 1].ts);
});
