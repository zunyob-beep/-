// 업비트에 못 닿았을 때 **무엇 때문인지 제대로 가르는가.**
//
// 왜 이게 따로 필요한가
// --------------------
// 아이패드에서 실제로 돌려 보니 이런 화면이 나왔다.
//
//   · 맨 위 시세는 잘 뜬다 (108,779,000원)
//   · 봉은 201개까지만 받고 멈췄다
//   · 그런데 화면에는 "업비트에 닿지 못했습니다"
//
// 시세가 뜨고 있는데 "닿지 못했다"는 **거짓말**이다. 그리고 201개라는
// 숫자가 무슨 일이 있었는지 정확히 말해 준다 — 첫 쪽(200개)은 받았고,
// `to`가 붙는 둘째 쪽부터 전부 실패했다. 맨 위 시세도 `to`가 없다.
//
// 그래서 두 가지를 고쳤고, 여기서 그 둘을 지킨다.
//
//   1. `to` 표기를 여러 개 준비해 두고 통하는 것을 찾는다
//   2. 한 번이라도 받아 본 뒤에 막혔으면 '닿지 못했다'고 하지 않는다

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  RateLimiter, TO_FORMATS, UpbitClient, UpbitError,
} from '../../web/core/upbit.js';

const OK = (rows) => ({ status: 200, async json() { return rows; }, async text() { return ''; } });

function candleRows(count = 2) {
  return Array.from({ length: count }, (_, i) => ({
    market: 'KRW-BTC',
    candle_date_time_utc: new Date((1700000000 - i * 60) * 1000).toISOString().slice(0, 19),
    opening_price: 100, high_price: 101, low_price: 99, trade_price: 100,
    candle_acc_trade_volume: 1,
  }));
}

/**
 * `to`를 어떤 표기로 보냈는지 뽑아낸다.
 *
 * 연결 확인용 요청(./manifest.webmanifest?ping=)은 상대 주소라 그냥
 * `new URL(url)`에 넣으면 터진다. 그것까지 실패로 만들면 '업비트만 막힘'과
 * '인터넷이 끊김'을 가르는 진단 자체가 망가진다.
 */
const sentTo = (url) => new URL(url, 'http://test.local/').searchParams.get('to');
const isPing = (url) => url.includes('manifest.webmanifest');

/** 어떤 표기로 적힌 `to`인지 알아낸다. 시각과 무관하게 모양만 본다. */
function formatIndexOf(to) {
  const at = 1700000000;
  return TO_FORMATS.findIndex((make) => {
    const sample = make(at);
    // 자릿수는 같고 구분 기호만 다르다. 기호 자리를 맞춰 본다.
    return sample.length === to.length
      && sample[10] === to[10]
      && sample.slice(19) === to.slice(19);
  });
}

test('to 표기가 안 통하면 다른 표기로 바꿔 본다', async () => {
  // 업비트가 **두 번째 표기만** 받아 주는 상황을 흉내 낸다.
  const ACCEPTS = 1;
  const tried = [];
  const client = new UpbitClient({
    retries: 0,
    perSecond: 100000,
    fetcher: async (url) => {
      if (isPing(url)) return OK([]);          // 우리 쪽은 늘 답한다
      const to = sentTo(url);
      if (to !== null) {
        tried.push(formatIndexOf(to));
        // 브라우저는 CORS로 막히면 예외를 던진다. 그걸 흉내 낸다.
        if (formatIndexOf(to) !== ACCEPTS) throw new TypeError('Failed to fetch');
      }
      return OK(candleRows());
    },
  });

  const got = await client.getCandles('KRW-BTC', 'minute1', 200, 1700000000);
  assert.equal(got.length, 2, '결국 받아냈어야 합니다');
  assert.deepEqual(tried, [0, ACCEPTS], `표기를 이렇게 더듬었습니다: ${tried}`);

  // 한 번 찾았으면 그 뒤로는 그것만 쓴다 — 매번 처음부터 더듬으면 느리다.
  tried.length = 0;
  await client.getCandles('KRW-BTC', 'minute1', 200, 1699999000);
  assert.deepEqual(tried, [ACCEPTS], `표기를 다시 더듬었습니다: ${tried}`);
});

test('어느 표기도 안 통하면 결국 실패한다', async () => {
  const client = new UpbitClient({
    retries: 0,
    perSecond: 100000,
    fetcher: async (url) => {
      if (isPing(url)) return OK([]);
      if (sentTo(url) !== null) throw new TypeError('Failed to fetch');
      return OK(candleRows());
    },
  });
  await assert.rejects(
    () => client.getCandles('KRW-BTC', 'minute1', 200, 1700000000),
    (error) => error instanceof UpbitError,
    '조용히 성공한 척하면 안 됩니다',
  );
});

test('한 번이라도 받은 뒤 막히면 "닿지 못했다"고 하지 않는다', async () => {
  // 이게 화면에 뜬 거짓말이었다. 시세가 멀쩡히 나오는데 "업비트에 닿지
  // 못했습니다"가 같이 떠 있었다.
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
  assert.ok(failure instanceof UpbitError);
  assert.equal(failure.kind, 'stalled', `종류가 ${failure.kind}입니다`);
  assert.ok(
    !failure.message.includes('닿지 못했'),
    `받아 놓고도 "닿지 못했다"고 합니다: ${failure.message}`,
  );
  assert.ok(failure.message.includes('1번'), '몇 번 받았는지 말해야 합니다');
});

test('한 번도 못 받았으면 그때는 "닿지 못했다"가 맞다', async () => {
  const client = new UpbitClient({
    retries: 0,
    perSecond: 100000,
    fetcher: async (url) => {
      // 우리 쪽(같은 출처) 확인 요청은 성공시킨다 → 인터넷은 되는 상황
      if (isPing(url)) return OK([]);
      throw new TypeError('Failed to fetch');
    },
  });
  const failure = await client.getCandles('KRW-BTC', 'minute1', 200)
    .then(() => null, (error) => error);
  assert.equal(failure.kind, 'blocked');
});

test('인터넷 자체가 끊겼으면 업비트 탓을 하지 않는다', async () => {
  const client = new UpbitClient({
    retries: 4,
    perSecond: 100000,
    // 우리 쪽 확인 요청까지 실패 → 인터넷이 끊긴 것
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

test('업비트를 몰아붙이지 않는다', () => {
  // 1년치는 2,600번 넘게 요청해야 한다. 너무 빠르면 막힌다.
  const client = new UpbitClient();
  assert.ok(client.limiter.perSecond <= 5, `초당 ${client.limiter.perSecond}번은 너무 잦습니다`);
});

// ------------------------------------------------------------- 속도
//
// 아이패드에서 두 번째로 막혔을 때 화면에 263개가 떠 있었다. 이전 판이
// 받아둔 201개 + 그 사이 흐른 62분 = 263. 즉 **`to`가 붙은 요청은 이번에도
// 한 번도 성공하지 못했다.**
//
// 그런데 CORS는 쿼리 파라미터를 구분하지 못한다. 같은 주소·같은 방식인데
// 첫 요청만 되고 그 다음이 안 된다면 남는 설명은 속도뿐이다. 그리고 실제로
// 속도 제한기에 결함이 있었다 — 초당 회수만 지키고 **간격은 안 지켰다.**

test('요청을 한꺼번에 쏘지 않고 고르게 벌린다', async () => {
  // 이게 진짜 버그였다. '지난 1초에 5번 미만이면 통과'는 창이 비어 있을 때
  // 5개를 **동시에** 내보낸다. 평균은 초당 5회지만 순간 속도는 초당 100회다.
  const limiter = new RateLimiter(20);   // 간격 50ms
  const at = [];
  for (let i = 0; i < 4; i += 1) {
    at.push(Date.now());
    // eslint-disable-next-line no-await-in-loop
    await limiter.acquire();
  }
  at.push(Date.now());
  const gaps = at.slice(1).map((t, i) => t - at[i]);
  // 타이머는 정확하지 않으므로 넉넉히 본다. 요지는 **0이 아니어야** 한다는 것.
  assert.ok(
    gaps.slice(1).every((g) => g >= 35),
    `요청이 붙어서 나갔습니다: ${gaps}ms`,
  );
});

test('동시에 불러도 서로 겹치지 않고 줄을 선다', async () => {
  // 세 봉 간격을 동시에 받으면 acquire가 겹쳐 불린다. 기다린 뒤에 자리를
  // 잡으면 셋이 같은 자리를 잡고 함께 나간다 — 고치려던 그 문제가 된다.
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

test('막히면 스스로 느려진다', async () => {
  const client = new UpbitClient({
    retries: 0,
    perSecond: 100000,
    fetcher: async (url) => {
      if (isPing(url)) return OK([]);
      throw new TypeError('Failed to fetch');
    },
  });
  const before = client.limiter.perSecond;
  await client.getCandles('KRW-BTC', 'minute1', 200).catch(() => {});
  assert.ok(
    client.limiter.perSecond < before,
    `막혔는데도 같은 속도로 계속 부릅니다 (초당 ${client.limiter.perSecond}번)`,
  );
});

// ------------------------------------------------- 스스로 찾아내기
//
// 세 번 고쳤는데 세 번 다 같은 자리에서 멈췄다(201 → 263 → 297, 늘어난 만큼이
// 정확히 그 사이 흐른 시간). 나는 이 환경에서 업비트에 닿을 수 없어
// (CONNECT 403) 무엇이 문제인지 확인할 방법이 없다.
//
// **그래서 맞히기를 그만두고, 앱이 돌면서 직접 찾게 했다.** 아래는 남아 있는
// 가설들을 하나씩 흉내 내고, 각각에서 앱이 스스로 빠져나오는지 본다. 어느
// 가설이 맞든 작동해야 한다.

const withTo = (fetcher) => new UpbitClient({
  retries: 0, perSecond: 100000, sweepPause: 5, fetcher,
});
const askedCount = (url) => Number(new URL(url, 'http://test.local/').searchParams.get('count'));

test('가설 A — to와 큰 count를 같이 주면 거절당한다', async () => {
  // 표기는 처음부터 맞았지만 개수가 문제인 경우. 표기만 더듬으면 영영 못 찾는다.
  const CAP = 100;
  const client = withTo(async (url) => {
    if (isPing(url)) return OK([]);
    if (sentTo(url) !== null && askedCount(url) > CAP) throw new TypeError('Failed to fetch');
    return OK(candleRows(2));
  });

  const got = await client.getCandles('KRW-BTC', 'minute1', 200, 1700000000);
  assert.equal(got.length, 2, '개수를 줄여서라도 받아냈어야 합니다');
  assert.equal(client.toCap, CAP, `개수 상한이 ${client.toCap}입니다`);
  assert.ok(client.toProven, '찾은 조합을 기억해야 합니다');
});

test('가설 B — 답은 하는데 봉을 하나도 안 준다', async () => {
  // 200 OK에 빈 배열. 이걸 성공으로 받으면 **아무 설명 없이 조용히 멈춘다** —
  // 화면에 보이던 모습이 정확히 그랬다.
  let empties = 0;
  const client = withTo(async (url) => {
    if (isPing(url)) return OK([]);
    if (sentTo(url) === null) return OK(candleRows(2));
    // 첫 표기로는 빈 배열, 두 번째 표기부터 제대로 준다.
    if (formatIndexOf(sentTo(url)) === 0) { empties += 1; return OK([]); }
    return OK(candleRows(2));
  });

  const got = await client.getCandles('KRW-BTC', 'minute1', 200, 1700000000);
  assert.ok(empties > 0, '빈 배열을 실제로 겪었어야 합니다');
  assert.equal(got.length, 2, '빈 배열에서 멈추지 말고 다음 조합을 봤어야 합니다');
});

test('가설 B — 어느 조합으로도 봉을 안 주면 조용히 멈추지 않는다', async () => {
  const client = withTo(async (url) => {
    if (isPing(url)) return OK([]);
    return OK(sentTo(url) === null ? candleRows(2) : []);
  });
  const failure = await client.getCandles('KRW-BTC', 'minute1', 200, 1700000000)
    .then(() => null, (error) => error);
  assert.ok(failure instanceof UpbitError, '조용히 빈손으로 끝내면 안 됩니다');
  assert.equal(failure.kind, 'empty', `종류가 ${failure.kind}입니다`);
});

test('아홉 조합을 다 훑는다 (표기 3 × 개수 3)', async () => {
  const seen = new Set();
  const client = withTo(async (url) => {
    if (isPing(url)) return OK([]);
    const to = sentTo(url);
    if (to !== null) {
      seen.add(`${formatIndexOf(to)}/${askedCount(url)}`);
      throw new TypeError('Failed to fetch');
    }
    return OK(candleRows());
  });
  await client.getCandles('KRW-BTC', 'minute1', 200, 1700000000).catch(() => {});
  assert.equal(seen.size, 9, `${seen.size}가지만 해 봤습니다: ${[...seen].join(' ')}`);
});

test('느려진 뒤에 통하면 표기가 아니라 속도가 문제였던 것이다', async () => {
  // 세 표기가 전부 안 통했다고 해서 '표기 문제'라고 결론 내리면 안 된다.
  // 너무 빨라서 셋 다 막힌 것일 수도 있다. 그래서 느려진 채로 한 번 더
  // 훑어 본다. **여기서 통하면 원인은 표기가 아니라 속도다.**
  const FAIL_UNTIL = 3;    // 첫 훑기(표기 0·1·2)는 전부 막힌다
  let tries = 0;
  const client = new UpbitClient({
    retries: 0,
    perSecond: 100000,
    sweepPause: 5,
    fetcher: async (url) => {
      if (isPing(url)) return OK([]);
      if (sentTo(url) !== null) {
        tries += 1;
        if (tries <= FAIL_UNTIL) throw new TypeError('Failed to fetch');
      }
      return OK(candleRows());
    },
  });

  const got = await client.getCandles('KRW-BTC', 'minute1', 200, 1700000000);
  assert.equal(got.length, 2, '두 번째 훑기에서 받아냈어야 합니다');
  // 첫 표기로 돌아와서 성공했다 — 표기는 처음부터 맞았다는 뜻이다.
  assert.equal(client.toFormat, 0, `표기 ${client.toFormat}에 정착했습니다`);
  assert.ok(client.toProven, '통하는 표기를 찾았다고 기록해야 합니다');
});
