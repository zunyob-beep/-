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

import { TO_FORMATS, UpbitClient, UpbitError } from '../../web/core/upbit.js';

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
