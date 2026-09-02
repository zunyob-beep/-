// 업비트에 어떻게 요청하고, 못 받았을 때 무엇 때문인지 제대로 가르는가.
//
// 여기 있는 시험은 대부분 **실제로 겪은 화면**에서 나왔다. 하나씩 적어 둔다.
// 원인을 몰라서 추측으로 붙였던 장치들(표기·개수 9조합 더듬기, 감속·증속,
// 되돌아가기)은 원인을 알고 나서 전부 걷어냈고, 그 시험들도 같이 지웠다.
// 남은 것은 지금도 참인 것들뿐이다.

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  PER_SECOND, RateLimiter, UpbitClient, UpbitError,
} from '../../web/core/upbit.js';

const OK = (rows) => ({ status: 200, async json() { return rows; }, async text() { return ''; } });

function candleRows(count = 2, at = 1700000000) {
  return Array.from({ length: count }, (_, i) => ({
    market: 'KRW-BTC',
    candle_date_time_utc: new Date((at - i * 60) * 1000).toISOString().slice(0, 19),
    opening_price: 100, high_price: 101, low_price: 99, trade_price: 100,
    candle_acc_trade_volume: 1,
  }));
}

/**
 * 연결 확인용 요청(./manifest.webmanifest?ping=)은 상대 주소다. 그것까지
 * 실패로 만들면 '업비트만 막힘'과 '인터넷이 끊김'을 가르는 진단이 망가진다.
 */
const isPing = (url) => url.includes('manifest.webmanifest');
const sentTo = (url) => new URL(url, 'http://test.local/').searchParams.get('to');

// ------------------------------------------------------------ 예산
//
// 이 앱이 업비트에 보내는 전부가 하나의 예산 안에 있어야 한다. 예전에는
// 화면과 워커에 클라이언트가 따로 있어 제한기가 둘이었고 서로를 몰랐다.
// "초당 3회"라고 적어 놓고 실제로는 그보다 많이 나갔다.

test('업비트 한도 안에서 부른다', () => {
  // 업비트 시세 API의 공개 한도는 초당 10회다.
  assert.ok(PER_SECOND <= 10, `초당 ${PER_SECOND}번은 업비트 한도를 넘습니다`);
  // **한도 안이라고 안전한 게 아니다.** 5로 두고도 막혔다는 말을 들었다.
  // 휴대폰 데이터는 한 주소를 여러 사람이 나눠 쓰므로 우리 몫은 한도보다
  // 훨씬 작아야 한다. 3은 4년치 받기를 끝까지 마친 유일한 속도다.
  // 올리고 싶으면 실제로 끝까지 받아 보고 이 숫자부터 고쳐라.
  assert.ok(PER_SECOND <= 3, `초당 ${PER_SECOND}번은 실제로 막혔던 속도입니다`);
  const client = new UpbitClient();
  assert.equal(client.limiter.perSecond, PER_SECOND);
});

test('요청을 한꺼번에 쏘지 않고 고르게 벌린다', async () => {
  // 이게 진짜 버그였다. '지난 1초에 N번 미만이면 통과'는 창이 비어 있을 때
  // 여러 개를 **동시에** 내보낸다. 평균은 맞지만 순간 속도가 수십 배다.
  const limiter = new RateLimiter(20);   // 간격 50ms
  const at = [];
  for (let i = 0; i < 4; i += 1) {
    at.push(Date.now());
    // eslint-disable-next-line no-await-in-loop
    await limiter.acquire();
  }
  at.push(Date.now());
  const gaps = at.slice(1).map((t, i) => t - at[i]);
  assert.ok(gaps.slice(1).every((g) => g >= 35), `요청이 붙어서 나갔습니다: ${gaps}ms`);
});

test('동시에 불러도 서로 겹치지 않고 줄을 선다', async () => {
  // 기다린 뒤에 자리를 잡으면 동시에 들어온 요청들이 같은 자리를 잡고
  // 함께 나간다 — 고치려던 그 문제가 된다.
  const limiter = new RateLimiter(20);
  const at = [];
  await Promise.all([0, 1, 2, 3].map(async () => {
    await limiter.acquire();
    at.push(Date.now());
  }));
  at.sort((a, b) => a - b);
  const gaps = at.slice(1).map((t, i) => t - at[i]);
  assert.ok(gaps.every((g) => g >= 35), `동시 요청이 붙어서 나갔습니다: ${gaps}ms`);
});

test('업비트로 나가는 길은 워커 하나뿐이다', async () => {
  const { readFile } = await import('node:fs/promises');
  const app = await readFile(new URL('../../web/app.js', import.meta.url), 'utf8');
  assert.ok(
    !/new UpbitClient/.test(app),
    '화면 쪽에 UpbitClient가 또 있습니다 — 제한기가 둘이 되면 초당 회수를 못 지킵니다',
  );
  const worker = await readFile(new URL('../../web/worker.js', import.meta.url), 'utf8');
  const made = worker.match(/new UpbitClient/g) ?? [];
  assert.equal(made.length, 1, `워커가 UpbitClient를 ${made.length}개 만듭니다`);
});

test('맨 위 시세를 너무 자주 묻지 않는다', async () => {
  const { readFile } = await import('node:fs/promises');
  const app = await readFile(new URL('../../web/app.js', import.meta.url), 'utf8');
  const every = app.match(/setInterval\(refreshTicker,\s*(\d+)\)/);
  assert.ok(every, 'refreshTicker 주기를 못 찾았습니다');
  const ms = Number(every[1]);
  // 5초였을 때 시간당 720번이 나갔다. 맨 위 숫자에 그 해상도는 필요 없다.
  assert.ok(ms >= 15000, `${ms / 1000}초마다 묻습니다 — 시간당 ${3600000 / ms}번입니다`);
});

// ------------------------------------------------------- 못 받았을 때
//
// 브라우저는 CORS로 막힌 것, 서버가 거절한 것, 인터넷이 끊긴 것을 **똑같이**
// TypeError로 알려준다. 그래서 우리 쪽에서 갈라야 하고, 이걸 못 갈라서
// 오랫동안 "닿지 못했습니다"라는 틀린 말을 해 왔다.

test('한 번이라도 받은 뒤 막히면 "닿지 못했다"고 하지 않는다', async () => {
  // 시세가 멀쩡히 나오는데 "업비트에 닿지 못했습니다"가 같이 떠 있었다.
  let calls = 0;
  const client = new UpbitClient({
    retries: 0,
    perSecond: 100000,
    fetcher: async (url) => {
      if (isPing(url)) return OK([]);
      calls += 1;
      if (calls === 1) return OK(candleRows());
      throw new TypeError('Failed to fetch');
    },
  });

  await client.getCandles('KRW-BTC', 'minute1', 200);   // 첫 쪽은 성공
  assert.equal(client.succeeded, 1);

  const failure = await client.getCandles('KRW-BTC', 'minute1', 200)
    .then(() => null, (error) => error);
  assert.equal(failure.kind, 'stalled', `종류가 ${failure.kind}입니다`);
  assert.ok(
    !failure.message.includes('닿지 못했'),
    `받아 놓고도 "닿지 못했다"고 합니다: ${failure.message}`,
  );
});

test('한 번도 못 받았으면 그때는 "닿지 못했다"가 맞다', async () => {
  const client = new UpbitClient({
    retries: 0,
    perSecond: 100000,
    fetcher: async (url) => {
      if (isPing(url)) return OK([]);   // 인터넷은 된다
      throw new TypeError('Failed to fetch');
    },
  });
  const failure = await client.getCandles('KRW-BTC', 'minute1', 200)
    .then(() => null, (error) => error);
  assert.equal(failure.kind, 'blocked');
});

// 아이패드 진단 화면이 실제로 이랬다.
//
//   현재가 (to 없음)        안 됨   TypeError: Load failed
//   봉 200개 (to 없음)      안 됨   TypeError: Load failed
//   같은 주소를 no-cors로   됨      닿았습니다 (124ms)
//
// no-cors가 124ms에 성공했다는 건 업비트까지 갔고 답도 왔다는 뜻이다. 못
// 닿은 게 아니라 온 답을 못 읽는 것이다 — 거절 응답에는 허용 표시(CORS)가
// 안 붙기 때문이다. 그런데 화면에는 "아예 못 닿고 있습니다"라고 떴다.
test('닿기는 하는데 전부 거절당하면 "못 닿았다"고 하지 않는다', async () => {
  const client = new UpbitClient({
    retries: 0,
    perSecond: 100000,
    fetcher: async (url, init) => {
      if (isPing(url)) return OK([]);              // 인터넷은 된다
      if (init?.mode === 'no-cors') return OK([]); // 업비트까지 닿는다
      throw new TypeError('Load failed');          // 그런데 답을 읽을 수 없다
    },
  });
  const failure = await client.getCandles('KRW-BTC', 'minute1', 200)
    .then(() => null, (error) => error);
  assert.equal(failure.kind, 'throttled', `종류가 ${failure.kind}입니다`);
  assert.ok(failure.message.includes('막고 있'), '막힌 상태라고 말해야 합니다');
  assert.ok(client.knownBlocked(), '막힌 걸 기억해야 급하지 않은 요청을 멈춥니다');
});

test('한 번도 못 받은 상태에서는 조심스럽게만 다시 해 본다', async () => {
  // 예전 이 시험은 "막혔으면 아예 재시도하지 마라"였다. 진단표를 받고 나서
  // 그 믿음이 틀렸다는 걸 알았다 — 이 망은 **7번 중 1번은 통과한다.** 한 번
  // 실패했다고 포기하면 통과했을 요청을 스스로 안 보내는 것이고, 실제로
  // 7일치 받는 데 15시간이 걸렸다.
  //
  // 그렇다고 무한정 두드릴 수는 없다. 정말 막힌 주소일 수도 있고, 그때
  // 두드리면 차단만 길어진다. **한 번도 못 받았으면 조금만, 한 쪽이라도
  // 받아 봤으면 끈질기게** — 증거가 있는 만큼만 한다.
  //
  // 길이 여럿이 되고 나서는 **길마다** 세야 한다. 다른 길을 가 보는 것은
  // 같은 길을 또 두드리는 것과 다르다.
  let calls = 0;
  const perRoute = {};
  const client = new UpbitClient({
    retries: 4,
    perSecond: 100000,
    retryPause: 1,
    fetcher: async (url, init) => {
      if (isPing(url)) return OK([]);
      if (init?.mode === 'no-cors') return OK([]);
      calls += 1;
      const where = String(url).startsWith('https://api.upbit.com') ? '직접' : '우회';
      perRoute[where] = (perRoute[where] ?? 0) + 1;
      throw new TypeError('Load failed');
    },
  });
  await client.getCandles('KRW-BTC', 'minute1', 200, 1700000000).catch(() => {});
  assert.ok(calls >= 2, `한 번도 다시 안 해 봤습니다 (${calls}번)`);
  assert.ok(
    perRoute['직접'] <= 4,
    `막힌 길 하나를 ${perRoute['직접']}번이나 두드렸습니다`,
  );
  assert.ok(perRoute['우회'] > 0, '다른 길은 가 보지도 않았습니다');

  // **그리고 멈춘다.** 여기가 핵심이다 — 몇 번 해 보고 안 되면 입을 다문다.
  assert.ok(client.knownBlocked(), '몇 번 실패하고도 안 멈췄습니다');
  calls = 0;
  await client.getCandles('KRW-BTC', 'minute1', 200, 1700000000).catch(() => {});
  assert.equal(calls, 0, `멈춘 뒤에도 ${calls}번을 보냈습니다`);
});

test('한 쪽이라도 받아 봤으면 끈질기게 다시 한다', async () => {
  // 7번 중 1번만 통과하는 망. 예전에는 한 번 실패하면 몇 분씩 쉬어서
  // 7일치가 15시간이었다. 길이 열려 있다는 증거가 있으면 쉬지 말아야 한다.
  let calls = 0;
  const client = new UpbitClient({
    perSecond: 100000,
    retryPause: 1,
    fetcher: async (url, init) => {
      if (isPing(url)) return OK([]);
      if (init?.mode === 'no-cors') return OK([]);
      calls += 1;
      if (calls % 7 !== 0) throw new TypeError('Load failed');
      return OK(candleRows(200, 1700000000 - calls * 60));
    },
  });

  const started = Date.now();
  const saved = [];
  await client.collect('KRW-BTC', 'minute1', 600, {
    retain: false,
    onBatch: (batch) => { saved.push(...batch); },
  });
  assert.ok(saved.length >= 600, `${saved.length}개에서 포기했습니다`);
  // 몇 분씩 쉬었다면 여기서 몇 분이 걸린다. 쉬지 않으면 순식간이다.
  assert.ok(Date.now() - started < 5000, '통과가 섞인 망인데 입을 다물었습니다');
  assert.equal(client.knownBlocked(), false, '받고 있는데 막혔다고 봅니다');
});

test('인터넷 자체가 끊겼으면 업비트 탓을 하지 않는다', async () => {
  const client = new UpbitClient({
    retries: 4,
    perSecond: 100000,
    fetcher: async () => { throw new TypeError('Failed to fetch'); },
  });
  const started = Date.now();
  const failure = await client.getCandles('KRW-BTC', 'minute1', 200)
    .then(() => null, (error) => error);
  assert.equal(failure.kind, 'offline');
  // 끊겨 있으면 재시도해도 소용없다. 기다리게 하면 안 된다.
  assert.ok(Date.now() - started < 2000, '끊긴 걸 알고도 재시도로 시간을 썼습니다');
});

test('요청 한도(429)는 따로 구분한다', async () => {
  const client = new UpbitClient({
    retries: 0,
    perSecond: 100000,
    fetcher: async () => ({ status: 429, async json() { return {}; }, async text() { return ''; } }),
  });
  const failure = await client.getCandles('KRW-BTC', 'minute1', 200)
    .then(() => null, (error) => error);
  assert.equal(failure.kind, 'rate');
});

// --------------------------------------------------- 끊겨도 이어 받는다
//
// 받은 쪽은 그때그때 저장된다. 4,812개까지 잘 받다가 한 번 걸려서 거기서
// 끝난 적이 있는데, 그건 4,812개를 잃은 게 아니라 남은 걸 안 받은 것이다.

test('받다가 한 번 걸려도 이어서 받는다', async () => {
  let calls = 0;
  const client = new UpbitClient({
    retries: 0, perSecond: 100000, stallPause: 5,
    fetcher: async (url) => {
      if (isPing(url)) return OK([]);
      calls += 1;
      if (calls === 3) throw new TypeError('Failed to fetch');   // 딱 한 번 걸린다
      return OK(candleRows(2, 1700000000 - calls * 600));
    },
  });

  const saved = [];
  await client.collect('KRW-BTC', 'minute1', 8, {
    retain: false, onBatch: (batch) => { saved.push(...batch); },
  });
  assert.ok(saved.length >= 8, `${saved.length}개에서 멈췄습니다 — 이어 받았어야 합니다`);
});

test('막혀 있으면 기다렸다가 스스로 이어 받는다', async () => {
  // 사람이 10분 뒤에 다시 누르는 대신 앱이 스스로 기다린다.
  // **막힌 상태를 개수가 아니라 상태로 흉내 낸다.**
  //
  // 예전에는 '몇 번째 요청부터 풀린다'로 썼는데, 그건 막힘이 아니라 세는
  // 것이라 진단이 주소를 하나 더 물어보자마자 어긋났다. 막혔을 때는 봉이든
  // 현재가든 **다 막힌다** — 그게 막힘의 정의다.
  let tries = 0;
  const UNBLOCKS_AT = 3;
  const client = new UpbitClient({
    retries: 0, perSecond: 100000, quietSteps: [20],
    fetcher: async (url, init) => {
      if (isPing(url)) return OK([]);
      if (init?.mode === 'no-cors') return OK([]);   // 닿기는 한다 = 막힌 상태
      const blocked = tries < UNBLOCKS_AT;
      if (String(url).includes('/v1/candles/')) tries += 1;
      if (blocked) throw new TypeError('Load failed');
      return OK(candleRows(2, 1700000000 - tries * 600));
    },
  });

  const waits = [];
  const saved = [];
  await client.collect('KRW-BTC', 'minute1', 4, {
    retain: false,
    onBatch: (batch) => { saved.push(...batch); },
    onProgress: (done, total, info) => { if (info?.banned) waits.push(info.waitLeft); },
  });
  assert.ok(saved.length >= 4, `${saved.length}개에서 포기했습니다 — 기다렸어야 합니다`);
  assert.ok(waits.length > 0, '기다리는 동안 남은 시간을 알려줘야 합니다');
});

test('막힌 채로 영원히 매달리지는 않는다', async () => {
  // 기다리는 것과 붙잡고 있는 것은 다르다. 끝내 안 풀리면 말해 줘야 한다.
  const client = new UpbitClient({
    retries: 0, perSecond: 100000, quietSteps: [5],
    fetcher: async (url, init) => {
      if (isPing(url)) return OK([]);
      if (init?.mode === 'no-cors') return OK([]);
      throw new TypeError('Load failed');
    },
  });
  const failure = await client.collect('KRW-BTC', 'minute1', 500, { retain: false })
    .then(() => null, (error) => error);
  assert.ok(failure instanceof UpbitError, '조용히 빈손으로 끝내면 안 됩니다');
  assert.equal(failure.kind, 'throttled');
});

test('첫 쪽부터 빈손이면 조용히 끝내지 않는다', async () => {
  // 200 OK에 빈 배열이 오면 예전에는 아무 말 없이 멈췄다. 화면에는 오류도
  // 없이 그냥 멈춘 것으로 보인다. 비트코인 1분봉에 과거가 없을 리 없다.
  const client = new UpbitClient({
    retries: 0,
    perSecond: 100000,
    fetcher: async (url) => (isPing(url) ? OK([]) : OK([])),
  });
  const failure = await client.collect('KRW-BTC', 'minute1', 200, { retain: false })
    .then(() => null, (error) => error);
  assert.ok(failure instanceof UpbitError, '빈손으로 조용히 끝냈습니다');
  assert.equal(failure.kind, 'empty');
});

test('to는 문서에 있는 표기 하나만 쓴다', async () => {
  // 예전에는 세 표기를 차례로 더듬었다. 표기가 문제인 줄 알았는데 사실은
  // 막혀 있던 것이었고, 막힌 상태에서 표기를 바꿔 가며 다시 보내는 건
  // 상황을 나쁘게만 만든다.
  const sent = [];
  const client = new UpbitClient({
    retries: 0,
    perSecond: 100000,
    fetcher: async (url) => {
      if (isPing(url)) return OK([]);
      const to = sentTo(url);
      if (to !== null) sent.push(to);
      return OK(candleRows());
    },
  });
  await client.getCandles('KRW-BTC', 'minute1', 200, 1700000000);
  assert.equal(sent.length, 1, `한 번에 보냈어야 합니다: ${sent}`);
  assert.match(sent[0], /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/, `표기가 ${sent[0]}입니다`);
});

test('판 번호와 캐시 이름이 어긋나지 않는다', async () => {
  // 캐시 이름에 판 번호가 들어간다. 번호가 바뀌면 이름이 바뀌고, 그래야
  // 서비스 워커가 예전 파일을 버린다. 둘이 어긋나면 화면에는 새 번호가
  // 뜨는데 실제로는 옛 파일이 도는 — 제일 헷갈리는 상태가 된다.
  const { readFile } = await import('node:fs/promises');
  const { VERSION } = await import('../../web/version.js');
  const sw = await readFile(new URL('../../web/sw.js', import.meta.url), 'utf8');
  const cache = sw.match(/const CACHE = '([^']+)'/);
  assert.ok(cache, '캐시 이름을 못 찾았습니다');
  assert.ok(
    cache[1].endsWith(VERSION),
    `판 번호는 ${VERSION}인데 캐시 이름은 ${cache[1]}입니다`,
  );

  // 서비스 워커가 담는 목록에 version.js가 있어야 오프라인에서도 뜬다.
  assert.ok(sw.includes("'./version.js'"), '서비스 워커가 version.js를 안 담습니다');
});

// ------------------------------------------- 막혀 있는 동안 얼마나 두드리나
//
// **"전혀 데이터를 불러오고 있지 않아"의 원인이 여기 있었다.**
//
// 업비트는 한도를 넘긴 주소를 몇 분씩 막고, 막혀 있는 동안 들어오는 요청은
// 대개 차단을 연장시킨다. 그런데 우리가 정확히 그러고 있었다 — 실패할 때마다
// 업비트로 3번(요청 1 + 진단 2)이 나갔고, 막힌 걸 아는 상태에서도 일단
// 보내고 실패했다. 맨 위 시세가 20초마다 도니까 밤새 천 번이 넘게 나갔다.
//
// 두드리지 않는 것 말고 우리가 할 수 있는 일이 없다. 그래서 이 셋을 묶는다.

test('막힌 걸 알면 업비트로 한 번도 안 보낸다', async () => {
  // 예전에는 여기서 1번이 나갔다. 보내고 실패하는 게 아니라 안 보내야 한다.
  let calls = 0;
  const client = new UpbitClient({
    retries: 0,
    perSecond: 100000,
    fetcher: async (url, init) => {
      if (isPing(url)) return OK([]);
      if (init?.mode === 'no-cors') { calls += 1; return OK([]); }
      calls += 1;
      throw new TypeError('Load failed');
    },
  });

  await client.getTicker('KRW-BTC').catch(() => {});
  assert.ok(client.knownBlocked(), '막힌 걸 기억해야 합니다');

  calls = 0;
  const failure = await client.getTicker('KRW-BTC').then(() => null, (error) => error);
  assert.equal(calls, 0, `막힌 걸 아는데 ${calls}번을 보냈습니다`);
  assert.equal(failure.kind, 'throttled');
  assert.ok(failure.message.includes('막고'), '왜 안 보냈는지 말해야 합니다');
});

test('막힐수록 더 길게 입을 다물고, 한 번 통하면 처음으로 돌아간다', () => {
  const client = new UpbitClient({ quietSteps: [1000, 5000, 20000] });
  const windows = [];
  for (let i = 0; i < 4; i += 1) {
    client.markBlocked();
    windows.push(client.blockedUntil - Date.now());
  }
  // 늘어나야 한다. 같은 간격으로 계속 두드리면 풀릴 틈이 안 생긴다.
  assert.ok(windows[1] > windows[0], `안 늘어났습니다: ${windows}`);
  assert.ok(windows[2] > windows[1], `안 늘어났습니다: ${windows}`);
  // 맨 끝 칸에서 멈춘다 — 한없이 늘어나면 풀린 뒤에도 하루를 쉰다.
  assert.ok(windows[3] <= windows[2] + 5, `상한이 없습니다: ${windows}`);

  // 한 번이라도 받아 오면 되돌린다. 안 그러면 새벽에 한 번 막힌 것 때문에
  // 아침 내내 20초씩 쉰다.
  client.markWorking();
  assert.equal(client.knownBlocked(), false, '통했는데도 막힌 줄 압니다');
  client.markBlocked();
  assert.ok(client.blockedUntil - Date.now() <= 1000, '처음으로 안 돌아갔습니다');
});

test('밤새 막혀 있어도 시간당 몇 번밖에 안 두드린다', async () => {
  // 실제 상황 그대로 8시간을 돌린다 — 앱을 켜 두면 맨 위 시세가 20초마다
  // 돌고, 업비트는 계속 거절한다. 예전 코드는 1,443번이었다(시간당 180번).
  // 이미 거절당하고 있는 주소로 그만큼 보내면 풀릴 리가 없다.
  const realNow = Date.now;
  let clock = realNow();
  Date.now = () => clock;
  let calls = 0;
  try {
    const client = new UpbitClient({
      retries: 0,
      fetcher: async (url, init) => {
        if (isPing(url)) return OK([]);
        calls += 1;
        if (init?.mode === 'no-cors') return OK([]);
        throw new TypeError('Load failed');
      },
    });
    const start = clock;
    const HOURS = 8;
    while (clock - start < HOURS * 3600 * 1000) {
      // 워커가 하는 것과 같다: 막힌 걸 알면 아예 묻지 않는다.
      // eslint-disable-next-line no-await-in-loop
      if (!client.knownBlocked()) await client.getTicker('KRW-BTC').catch(() => {});
      clock += 20000;
    }
    assert.ok(calls <= 40, `8시간에 ${calls}번을 두드렸습니다 (예전 1,443번)`);
  } finally {
    Date.now = realNow;
  }
});

// --------------------------------------- 현재가는 되는데 봉만 안 되는 상태
//
// 아이패드가 아니라 맥 사파리에서 나온 진단표다.
//
//   됨    현재가 (to 없음)        1개 받음 (49ms)
//   안 됨 봉 1개 (to 없음)        TypeError: Load failed
//   안 됨 봉 200개 (to 없음)      TypeError: Load failed
//   ...
//   됨    같은 주소를 no-cors로   닿았습니다 (58ms)
//
// **차단이라면 현재가도 막혀야 한다.** 현재가가 49ms에 되는데 봉만 전부
// 안 되는 것은 다른 종류의 문제다. 그런데 앱은 이걸 "업비트에 닿지
// 못했습니다"라고 말했다 — 사실이 아니다. 원인은 진단이 **되는 것을 물어보고
// 안 되는 것을 판단**했기 때문이다. plainWorks()가 현재가를 물어봤다.

test('현재가만 되고 봉이 다 막히면 "못 닿았다"고 하지 않는다', async () => {
  const client = new UpbitClient({
    retries: 0,
    perSecond: 100000,
    fetcher: async (url, init) => {
      if (isPing(url)) return OK([]);
      if (init?.mode === 'no-cors') return OK([]);
      if (String(url).includes('/v1/ticker')) return OK([{ market: 'KRW-BTC' }]);
      throw new TypeError('Load failed');       // 봉만 안 된다
    },
  });
  const failure = await client.getCandles('KRW-BTC', 'minute1', 200)
    .then(() => null, (error) => error);
  assert.equal(failure.kind, 'candles', `종류가 ${failure.kind}입니다`);
  assert.ok(failure.message.includes('현재가는'), '현재가는 된다는 걸 말해야 합니다');
  assert.ok(failure.message.includes('기다려도'), '기다려서 될 일이 아니라고 말해야 합니다');
  // **조용히 기다리는 상태로 들어가면 안 된다.** 기다려도 안 풀리는 종류라,
  // 30분씩 입을 다물어 봐야 아무 소득 없이 앱만 멈춘다.
  assert.equal(client.knownBlocked(), false, '기다려도 소용없는 걸 기다리고 있습니다');
});

test('진단은 우리가 실제로 필요한 것을 물어본다', async () => {
  // plainWorks()가 현재가를 물어보던 시절에는, 봉이 안 되는데도 "잘 된다"고
  // 답해서 판단이 통째로 어긋났다. 물어보는 주소를 시험으로 묶는다.
  const asked = [];
  const client = new UpbitClient({
    retries: 0,
    perSecond: 100000,
    fetcher: async (url, init) => {
      if (isPing(url)) return OK([]);
      asked.push(`${init?.mode === 'no-cors' ? 'no-cors ' : ''}${String(url)}`);
      throw new TypeError('Load failed');
    },
  });
  await client.getCandles('KRW-BTC', 'minute1', 200).catch(() => {});
  // 첫 요청 자체가 봉이므로 '봉을 한 번 불렀다'로는 아무것도 확인 못 한다.
  // **진단이 따로 봉을 물어봤는지**를 봐야 하고, 그러면 두 번이어야 한다.
  const bars = asked.filter((u) => u.includes('/v1/candles/') && !u.startsWith('no-cors'));
  assert.ok(
    bars.length >= 2,
    `진단이 봉을 안 물어봤습니다 (봉 요청 ${bars.length}번): ${asked.join(' | ')}`,
  );
});

// ------------------------------------------- 직접 가는 길이 막혀 있을 때
//
// 진단표 세 장이 같은 말을 했다. 마지막 표(와이파이)에서는 **전부** 실패했다.
//
//   안 됨 | 봉 1개 (to 없음)      | TypeError: Load failed
//   안 됨 | 현재가 ①②③          | TypeError: Load failed
//   됨    | 같은 주소를 no-cors로 | 닿았습니다 (128ms)
//
// no-cors가 128ms에 닿는데 읽을 수 있는 요청은 다 실패한다. 그리고 5G에서
// 와이파이로 바꿔도 똑같았다 — 우리 주소가 차단당한 게 아니다. 브라우저에서
// **직접 부르는 길 자체가** 막힌 것이고, 그건 몇 번을 다시 해도 안 뚫린다.
//
// 그러면 다른 길로 가야 한다. 그게 이 시험이 지키는 것이다.

test('직접 가는 길이 막히면 우회로 돌아서 받아 온다', async () => {
  const tried = [];
  const client = new UpbitClient({
    perSecond: 100000,
    retryPause: 1,
    fetcher: async (url, init) => {
      if (isPing(url)) return OK([]);
      if (init?.mode === 'no-cors') return OK([]);
      const direct = String(url).startsWith('https://api.upbit.com');
      tried.push(direct ? '직접' : '우회');
      // 직접은 무슨 짓을 해도 안 된다. 우회는 된다.
      if (direct) throw new TypeError('Load failed');
      return OK(candleRows(200, 1700000000));
    },
  });

  const candles = await client.getCandles('KRW-BTC', 'minute1', 200);
  assert.ok(candles.length > 0, '우회로도 못 받았습니다');
  assert.ok(tried.includes('직접'), '직접부터 해 봐야 합니다');
  assert.ok(tried.includes('우회'), `우회를 안 해 봤습니다: ${tried.join(', ')}`);
  // 직접을 끝없이 두드리지 않는다. 몇 번 해 보고 길을 바꾼다.
  const straight = tried.filter((t) => t === '직접').length;
  assert.ok(straight <= 4, `직접만 ${straight}번 두드렸습니다`);
});

test('직접이 되면 아무 데도 안 거친다', async () => {
  // 우회는 남의 서버를 거치는 것이라, 필요할 때만 써야 한다.
  const tried = [];
  const client = new UpbitClient({
    perSecond: 100000,
    fetcher: async (url) => {
      if (isPing(url)) return OK([]);
      tried.push(String(url));
      return OK(candleRows(200, 1700000000));
    },
  });
  await client.getCandles('KRW-BTC', 'minute1', 200);
  assert.ok(
    tried.every((u) => u.startsWith('https://api.upbit.com')),
    `잘 되는데도 우회를 거쳤습니다: ${tried.join(', ')}`,
  );
  assert.equal(client.routeLabel, '직접');
});

test('내 우회 주소를 적어 두면 그게 먼저다', async () => {
  // 남의 서버보다 자기 것이 낫다 — 한도도 자기 몫이고, 남이 안 본다.
  const tried = [];
  const client = new UpbitClient({
    perSecond: 100000,
    retryPause: 1,
    myProxy: 'https://my-worker.example.dev/?u={url}',
    fetcher: async (url) => {
      if (isPing(url)) return OK([]);
      tried.push(String(url));
      return OK(candleRows(200, 1700000000));
    },
  });
  await client.getCandles('KRW-BTC', 'minute1', 200);
  assert.ok(
    tried[0].startsWith('https://my-worker.example.dev/'),
    `내 주소를 안 썼습니다: ${tried[0]}`,
  );
  // 업비트 주소가 통째로 실려 가야 한다. 안 그러면 우회 서버가 뭘 물어볼지 모른다.
  assert.ok(tried[0].includes(encodeURIComponent('https://api.upbit.com')));
});

// --------------------------------- 우회 서버가 자기 사정으로 거절할 때
//
// 화면에 이 오류가 떴다.
//
//   1분봉을 갱신하지 못했습니다: 업비트가 요청을 거부했습니다 (401):
//   {"error":"A valid API key is required. Get one at https://console.corsproxy.io/"}
//
// 우회로 넘어간 것까지는 성공이다 — 요청이 거기까지 갔고 **읽을 수 있는**
// 답이 왔다. 문제는 그 우회 서버가 유료로 바뀌었다는 것, 그리고 앱이
// **다음 우회로 안 넘어갔다**는 것이다.
//
// 길 바꾸기가 네트워크 실패(catch)에서만 일어나고 있었다. 401은 성공한 HTTP
// 응답이라 그 갈래를 안 타고 곧장 '거부'로 끝났다. 업비트 잘못도 아닌데
// "업비트가 요청을 거부했습니다"라고 적힌 것도 그래서다.

test('우회 서버가 키를 요구하면 다음 우회로 넘어간다', async () => {
  const tried = [];
  const client = new UpbitClient({
    perSecond: 100000,
    retryPause: 1,
    fetcher: async (url, init) => {
      if (isPing(url)) return OK([]);
      if (init?.mode === 'no-cors') return OK([]);
      const at = String(url);
      if (at.startsWith('https://api.upbit.com')) {
        tried.push('직접');
        throw new TypeError('Load failed');
      }
      if (at.includes('allorigins')) {
        tried.push('첫 우회');
        // 실제로 겪은 그대로: 읽을 수 있는 401 응답
        return {
          status: 401,
          async json() { return {}; },
          async text() { return '{"error":"A valid API key is required."}'; },
        };
      }
      tried.push('다음 우회');
      return OK(candleRows(200, 1700000000));
    },
  });

  const candles = await client.getCandles('KRW-BTC', 'minute1', 200);
  assert.ok(candles.length > 0, '다음 우회로도 못 받았습니다');
  assert.ok(tried.includes('다음 우회'), `다음 우회를 안 가 봤습니다: ${tried.join(', ')}`);
  // 키를 달라는 서버를 계속 두드릴 이유가 없다. 한 번 보고 넘어가야 한다.
  const stuck = tried.filter((t) => t === '첫 우회').length;
  assert.ok(stuck <= 2, `키를 요구하는 서버를 ${stuck}번 두드렸습니다`);
});

test('공개 우회 서버는 키가 필요 없는 것만 쓴다', async () => {
  const { ROUTES } = await import('../../web/core/upbit.js');
  // corsproxy.io는 유료로 바뀌어 401을 준다. 목록에 있으면 안 된다.
  assert.ok(
    !ROUTES.some((r) => r.wrap('https://api.upbit.com/x').includes('corsproxy.io')),
    '키를 요구하는 우회 서버가 목록에 남아 있습니다',
  );
  assert.equal(ROUTES[0].id, 'direct', '직접이 첫 번째여야 합니다');
});
