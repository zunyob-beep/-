// **미리 받아 둔 봉을 읽어 오는 자리.** 이제 앱은 여기로 돈다.
//
// 업비트가 아니라 이 파일이 주된 길이므로, 여기가 조용히 깨지면 앱이
// 통째로 못 돈다. 그래서 시험이 다른 어느 곳보다 촘촘하다.

import assert from 'node:assert/strict';
import test from 'node:test';

import { fileUrls, loadSeed, unpackSeed } from '../../web/core/seed.js';

const PAGES = { href: 'https://zunyob-beep.github.io/-/worker.js', hostname: 'zunyob-beep.github.io', pathname: '/-/worker.js' };
const LOCAL = { href: 'http://127.0.0.1:8080/-/worker.js', hostname: '127.0.0.1', pathname: '/-/worker.js' };

const MINUTE = 60;

/** tools/pack.py가 적는 것과 같은 모양으로 만든다. */
function packed(market, { from = 1700000000, n = 5, price = 150000000, made = null } = {}) {
  const t = []; const c = []; const o = []; const h = []; const l = []; const v = [];
  let previous = 0;
  for (let i = 0; i < n; i += 1) {
    const close = price + i * 1000;
    t.push(i === 0 ? 0 : 1);
    c.push(i === 0 ? close : close - previous);
    previous = close;
    o.push(-500); h.push(2000); l.push(-2000); v.push(1.5);
  }
  return { m: market, step: MINUTE, from, n, scale: 1, made: made ?? from + n * MINUTE, t, c, o, h, l, v };
}

const ok = (body) => ({ status: 200, json: async () => body });
const missing = { status: 404, json: async () => ({}) };

// --------------------------------------------------------------- 주소 짚기
test('같은 주소 아래를 먼저 물어본다', () => {
  assert.equal(
    fileUrls('data', 'tail/KRW-BTC.json', PAGES)[0],
    'https://zunyob-beep.github.io/-/data/tail/KRW-BTC.json',
  );
});

test('깃허브 페이지면 raw 주소도 안다', () => {
  // 여기가 파일이 실제로 사는 곳이다. 저장소 이름이 `-` 하나여도 된다.
  assert.ok(
    fileUrls('history', 'manifest.json', PAGES)
      .includes('https://raw.githubusercontent.com/zunyob-beep/-/history/manifest.json'),
  );
});

test('주소를 코드에 박지 않는다 — 갈라 가도 자기 것을 읽는다', () => {
  const forked = { href: 'https://someone.github.io/patternscan/worker.js', hostname: 'someone.github.io', pathname: '/patternscan/worker.js' };
  const urls = fileUrls('data', 'tail/KRW-ETH.json', forked);
  assert.ok(urls.some((u) => u.includes('/someone/patternscan/data/')), urls.join(', '));
  assert.ok(!urls.some((u) => u.includes('zunyob')), '남의 저장소를 읽습니다');
});

test('사용자 페이지(owner.github.io)도 맞게 짚는다', () => {
  const user = { href: 'https://someone.github.io/worker.js', hostname: 'someone.github.io', pathname: '/worker.js' };
  assert.ok(
    fileUrls('data', 'tail/KRW-BTC.json', user)
      .includes('https://raw.githubusercontent.com/someone/someone.github.io/data/tail/KRW-BTC.json'),
  );
});

test('깃허브가 아니면 같은 주소만 본다', () => {
  // 개발 중이거나 다른 데 올렸을 때 남의 저장소를 부르면 안 된다.
  const urls = fileUrls('data', 'tail/KRW-BTC.json', LOCAL);
  assert.equal(urls.length, 1);
  assert.ok(!urls[0].includes('githubusercontent'));
});

// --------------------------------------------------------------- 되돌려 읽기
test('작게 적은 것을 봉으로 되돌린다', () => {
  const candles = unpackSeed(packed('KRW-BTC', { n: 3 }));
  assert.equal(candles.length, 3);
  assert.deepEqual(candles[0], {
    ts: 1700000000, open: 149999500, high: 150002000, low: 149998000, close: 150000000, volume: 1.5,
  });
  assert.equal(candles[2].ts, 1700000120);
  assert.equal(candles[2].close, 150002000);
});

test('차이가 쌓여도 안 어긋난다', () => {
  // 이 형식의 유일한 위험이다. 만 봉을 더해도 마지막 값이 정확해야 한다.
  const candles = unpackSeed(packed('KRW-BTC', { n: 10000 }));
  assert.equal(candles[9999].close, 150000000 + 9999 * 1000);
  assert.equal(candles[9999].ts, 1700000000 + 9999 * MINUTE);
});

test('소수가 있는 종목도 정확하다', () => {
  // scale=100이면 원 × 100으로 적혀 있다. 나눠서 되돌려야 한다.
  const file = {
    m: 'KRW-DOGE', step: 60, from: 1700000000, n: 2, scale: 100, made: 0,
    t: [0, 1], c: [50, 1], o: [1], h: [3], l: [-1], v: [1, 1],
  };
  file.o = [1, 1]; file.h = [3, 3]; file.l = [-1, -1];
  const candles = unpackSeed(file);
  assert.equal(candles[0].close, 0.5);
  assert.equal(candles[1].close, 0.51);
});

test('빠진 봉이 있어도 시각이 맞다', () => {
  // 업비트는 거래가 한 건도 없던 분에는 봉을 안 준다.
  const file = {
    m: 'KRW-BTC', step: 60, from: 1700000000, n: 3, scale: 1, made: 0,
    t: [0, 1, 9], c: [100, 1, 1], o: [0, 0, 0], h: [0, 0, 0], l: [0, 0, 0], v: [1, 1, 1],
  };
  assert.deepEqual(unpackSeed(file).map((c) => c.ts),
    [1700000000, 1700000060, 1700000600]);
});

test('옛 형식(봉마다 배열 하나)도 읽는다', () => {
  // 갈아타는 동안 두 형식이 섞여 있을 수 있다.
  const old = { market: 'KRW-BTC', rows: [[1700000000, 1, 2, 0, 1.5, 3]] };
  assert.deepEqual(unpackSeed(old), [{
    ts: 1700000000, open: 1, high: 2, low: 0, close: 1.5, volume: 3,
  }]);
});

test('모양이 아니면 null', () => {
  assert.equal(unpackSeed(null), null);
  assert.equal(unpackSeed({ nope: 1 }), null);
});

// ------------------------------------------------------------------ 읽어 오기
test('짧은 기간이면 꼬리 하나로 끝난다', async () => {
  // **이게 이 설계의 핵심이다.** 7일을 고른 사람이 4년치를 내려받으면 안 된다.
  const asked = [];
  const got = await loadSeed('KRW-BTC', {
    where: LOCAL,
    wanted: 2000,
    fetcher: async (url) => {
      asked.push(url);
      return ok(packed('KRW-BTC', { n: 2880 }));
    },
  });
  assert.equal(got.got, 2880);
  assert.equal(asked.length, 1, `${asked.length}번 받았습니다: ${asked.join(', ')}`);
  assert.ok(asked[0].includes('tail/'), asked[0]);
});

test('모자라면 최근 31일과 지나간 달까지 내려간다', async () => {
  const asked = [];
  const chunks = [];
  const got = await loadSeed('KRW-BTC', {
    where: LOCAL,
    wanted: 9000,
    onChunk: (candles) => { chunks.push(candles.length); },
    fetcher: async (url) => {
      asked.push(url);
      if (url.includes('manifest')) {
        return ok({ months: { 'KRW-BTC': ['2025-11', '2025-12', '2026-01'] } });
      }
      return ok(packed('KRW-BTC', { n: 3000 }));
    },
  });
  assert.equal(got.got, 9000);
  assert.deepEqual(chunks, [3000, 3000, 3000]);
  assert.ok(asked.some((u) => u.includes('tail/')), '꼬리를 안 봤습니다');
  assert.ok(asked.some((u) => u.includes('recent/')), '최근 31일을 안 봤습니다');
  assert.ok(asked.some((u) => u.includes('2026-01')), '지나간 달을 안 봤습니다');
});

test('새것부터 내려간다', async () => {
  const months = [];
  await loadSeed('KRW-BTC', {
    where: LOCAL,
    wanted: 100000,
    fetcher: async (url) => {
      if (url.includes('manifest')) {
        return ok({ months: { 'KRW-BTC': ['2025-10', '2025-11', '2025-12'] } });
      }
      const hit = url.match(/(\d{4}-\d{2})\.json/);
      if (hit) months.push(hit[1]);
      return ok(packed('KRW-BTC', { n: 100 }));
    },
  });
  assert.deepEqual(months, ['2025-12', '2025-11', '2025-10'], '오래된 것부터 받았습니다');
});

test('이미 가진 달은 다시 안 받는다', async () => {
  // **이 앱의 핵심 약속이다.** 지나간 봉은 안 변하므로 한 번 받으면 끝이다.
  const months = [];
  await loadSeed('KRW-BTC', {
    where: LOCAL,
    wanted: 100000,
    // 2025-12-01부터는 이미 이 기기에 있다
    oldest: Date.parse('2025-12-01T00:00:00Z') / 1000,
    fetcher: async (url) => {
      if (url.includes('manifest')) {
        return ok({ months: { 'KRW-BTC': ['2025-10', '2025-11', '2025-12'] } });
      }
      const hit = url.match(/(\d{4}-\d{2})\.json/);
      if (hit) months.push(hit[1]);
      return ok(packed('KRW-BTC', { n: 100 }));
    },
  });
  assert.ok(!months.includes('2025-12'), '이미 가진 달을 또 받았습니다');
  assert.deepEqual(months, ['2025-11', '2025-10']);
});

test('멈추라고 하면 멈춘다', async () => {
  let seen = 0;
  await loadSeed('KRW-BTC', {
    where: LOCAL,
    wanted: 100000,
    shouldStop: () => seen >= 3,
    fetcher: async (url) => {
      seen += 1;
      if (url.includes('manifest')) {
        return ok({ months: { 'KRW-BTC': ['2025-09', '2025-10', '2025-11', '2025-12'] } });
      }
      return ok(packed('KRW-BTC', { n: 100 }));
    },
  });
  assert.ok(seen <= 5, `멈추라고 했는데 ${seen}번 받았습니다`);
});

test('조각마다 그때그때 넘긴다 — 다 모아 두지 않는다', async () => {
  // 4년치 210만 봉을 한 배열에 쌓으면 아이패드에서 브라우저가 죽는다.
  let biggest = 0;
  await loadSeed('KRW-BTC', {
    where: LOCAL,
    wanted: 5000,
    onChunk: (candles) => { biggest = Math.max(biggest, candles.length); },
    fetcher: async (url) => (url.includes('manifest')
      ? ok({ months: { 'KRW-BTC': ['2026-01'] } })
      : ok(packed('KRW-BTC', { n: 2000 }))),
  });
  assert.equal(biggest, 2000, '조각 크기보다 큰 덩어리를 넘겼습니다');
});

// ------------------------------------------------------------------ 못 읽을 때
test('아무 데서도 못 읽으면 던지지 않는다', async () => {
  // **여기서 던지면 지름길이 막혔다는 이유로 판 전체가 죽는다.**
  const got = await loadSeed('KRW-BTC', {
    where: PAGES, fetcher: async () => { throw new TypeError('Load failed'); },
  });
  assert.equal(got.got, 0);
});

test('JSON이 아니어도 던지지 않는다', async () => {
  // 깃허브 페이지의 404는 HTML을 돌려준다. 그걸 json()으로 읽으면 터진다.
  const got = await loadSeed('KRW-BTC', {
    where: LOCAL,
    fetcher: async () => ({ status: 200, json: async () => { throw new SyntaxError('Unexpected token <'); } }),
  });
  assert.equal(got.got, 0);
});

test('첫 자리가 없으면 다음 자리로 간다', async () => {
  // 깃허브 페이지에는 ./data/가 없다 — 파일은 raw 쪽에 산다.
  const asked = [];
  const got = await loadSeed('KRW-BTC', {
    where: PAGES,
    wanted: 100,
    fetcher: async (url) => {
      asked.push(url);
      if (url.includes('github.io')) return missing;
      return ok(packed('KRW-BTC', { n: 200 }));
    },
  });
  assert.equal(got.got, 200);
  assert.equal(asked.length, 2, '두 자리를 다 물어봐야 합니다');
});

test('다른 종목 파일이 오면 안 쓴다', async () => {
  // 이더리움 봉으로 비트코인을 계산하면 화면에는 그럴듯한 숫자가 그대로 찍힌다.
  const got = await loadSeed('KRW-BTC', {
    where: LOCAL, wanted: 100, fetcher: async () => ok(packed('KRW-ETH', { n: 500 })),
  });
  assert.equal(got.got, 0);
});

test('무엇을 어디서 읽었는지 알려 준다', async () => {
  // 진단 화면이 이걸 업비트 요청과 같은 자리에 편다. 한 장으로 끝나야 한다.
  const seen = [];
  await loadSeed('KRW-BTC', {
    where: PAGES,
    wanted: 100,
    onTry: (url, how) => seen.push([url, how]),
    fetcher: async (url) => (url.includes('github.io') ? missing : ok(packed('KRW-BTC', { n: 200 }))),
  });
  assert.equal(seen.length, 2);
  assert.equal(seen[0][1], '404');
  assert.equal(seen[1][1], 'ok');
});

test('만들어진 시각을 알려 준다', async () => {
  const got = await loadSeed('KRW-BTC', {
    where: LOCAL, wanted: 10, fetcher: async () => ok(packed('KRW-BTC', { n: 5, made: 1700009999 })),
  });
  assert.equal(got.made, 1700009999);
});


test('통했던 자리를 기억해서 안 되는 데를 다시 안 물어본다', async () => {
  // **4년치를 받을 때 이게 없으면 조각 48개마다 404가 한 번씩 더 난다.**
  //
  // 깃허브 페이지에는 `./data/`가 없다 — 파일은 raw 쪽에 산다. 그런데
  // 순서가 늘 같으면 조각마다 없는 자리를 먼저 물어보게 된다.
  const asked = [];
  await loadSeed('KRW-BTC', {
    where: PAGES,
    wanted: 100000,
    fetcher: async (url) => {
      asked.push(url);
      if (url.includes('github.io')) return missing;
      if (url.includes('manifest')) {
        return ok({ months: { 'KRW-BTC': ['2025-10', '2025-11', '2025-12'] } });
      }
      return ok(packed('KRW-BTC', { n: 200 }));
    },
  });
  const wasted = asked.filter((u) => u.includes('github.io')).length;
  assert.ok(wasted <= 1, `안 되는 자리를 ${wasted}번이나 물어봤습니다`);
  assert.ok(asked.length >= 5, `자리를 아예 안 물어봤습니다 (${asked.length}번)`);
});

test('중간에 끊긴 파일은 안 쓴다', async () => {
  // 칸이 짧으면 undefined가 NaN이 되어 조용히 흘러든다. 가격에 NaN이
  // 섞이면 계산은 그대로 돌고 화면에도 숫자가 찍히는데, 그게 무엇을
  // 뜻하는지는 아무도 모른다.
  const whole = packed('KRW-BTC', { n: 10 });
  for (const key of ['c', 'o', 'h', 'l', 'v']) {
    const cut = { ...whole, [key]: whole[key].slice(0, 5) };
    assert.equal(unpackSeed(cut), null, `${key} 칸이 짧은데 읽었습니다`);
  }
  assert.equal(unpackSeed(whole).length, 10, '멀쩡한 것까지 버렸습니다');
});
