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

test('막힌 상태에서는 더 두드리지 않는다', async () => {
  // 막혀 있는데 재시도하는 건 풀릴 틈만 없앤다.
  let calls = 0;
  const client = new UpbitClient({
    retries: 4,
    perSecond: 100000,
    fetcher: async (url, init) => {
      if (isPing(url)) return OK([]);
      if (init?.mode === 'no-cors') return OK([]);
      calls += 1;
      throw new TypeError('Load failed');
    },
  });
  await client.getCandles('KRW-BTC', 'minute1', 200, 1700000000).catch(() => {});
  assert.ok(calls <= 3, `막혀 있는데 ${calls}번이나 더 두드렸습니다`);
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
  let calls = 0;
  const UNBLOCKS_AT = 3;
  const client = new UpbitClient({
    retries: 0, perSecond: 100000, throttlePause: 20,
    fetcher: async (url, init) => {
      if (isPing(url)) return OK([]);
      if (init?.mode === 'no-cors') return OK([]);   // 닿기는 한다 = 막힌 상태
      calls += 1;
      if (calls < UNBLOCKS_AT) throw new TypeError('Load failed');
      return OK(candleRows(2, 1700000000 - calls * 600));
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
    retries: 0, perSecond: 100000, throttlePause: 5,
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
