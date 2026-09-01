// 캐시와 '무엇을 받을지' 판단을 검사한다.
//
// 여기서 지켜야 할 약속은 하나다 — **지나간 봉은 다시 받지 않는다.**
// 그게 지켜지는지 요청 횟수를 세서 확인한다. "빨라졌습니다"는 말로는
// 확인할 수 없고, 실제로 몇 번 불렀는지는 셀 수 있다.

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  CHUNK, CandleStore, MemoryBackend, chunkOf, groupByChunk, mergeCandles,
} from '../../web/core/store.js';
import { REFRESH_TAIL, cachedSummary, loadSeries, update } from '../../web/core/data.js';
import { parseCandle, toCursor } from '../../web/core/upbit.js';

const STEP = 60;
const START = 1700000000 - (1700000000 % STEP);

function candle(i, price = 100 + i) {
  return {
    ts: START + i * STEP,
    open: price, high: price + 1, low: price - 1, close: price, volume: 1 + (i % 5),
  };
}

const range = (from, to) => Array.from({ length: to - from }, (_, k) => candle(from + k));

function freshStore() {
  return new CandleStore(new MemoryBackend());
}

// ---------------------------------------------------------------- 담기
test('넣은 만큼 그대로 나온다', async () => {
  const store = freshStore();
  await store.put('KRW-BTC', 'minute1', STEP, range(0, 500));
  assert.equal(await store.count('KRW-BTC', 'minute1'), 500);
  const all = await store.loadAll('KRW-BTC', 'minute1');
  assert.equal(all.length, 500);
  assert.equal(all[0].ts, START);
  assert.equal(all[499].close, 100 + 499);
});

test('덩어리 경계를 넘어도 순서가 유지된다', async () => {
  const store = freshStore();
  // 한 덩어리보다 확실히 길게 — 경계를 두 번 넘는다
  await store.put('KRW-BTC', 'minute1', STEP, range(0, CHUNK * 2 + 37));
  const all = await store.loadAll('KRW-BTC', 'minute1');
  assert.equal(all.length, CHUNK * 2 + 37);
  for (let i = 1; i < all.length; i += 1) {
    assert.equal(all[i].ts - all[i - 1].ts, STEP, `${i}번째에서 순서가 깨졌습니다`);
  }
});

test('같은 시각을 다시 넣으면 새 값으로 덮인다', async () => {
  // 마지막 봉은 그 분이 끝나기 전에 받으면 확정된 값이 아니다.
  const store = freshStore();
  await store.put('KRW-BTC', 'minute1', STEP, range(0, 10));
  await store.put('KRW-BTC', 'minute1', STEP, [{ ...candle(9), close: 999 }]);
  const all = await store.loadAll('KRW-BTC', 'minute1');
  assert.equal(all.length, 10, '개수가 늘면 안 됩니다');
  assert.equal(all[9].close, 999);
});

test('앞에 과거를 덧붙여도 뒤쪽 덩어리는 다시 쓰지 않는다', async () => {
  // 위치가 아니라 시각으로 자르는 이유가 이것이다. 위치로 자르면 앞에
  // 한 봉만 붙여도 모든 경계가 밀려서 8년치를 전부 다시 써야 한다.
  const store = freshStore();
  await store.put('KRW-BTC', 'minute1', STEP, range(CHUNK * 2, CHUNK * 4));
  const before = await store.backend.listIndex('KRW-BTC', 'minute1');
  assert.ok(before.length >= 2, '덩어리 경계를 넘는 자료여야 의미가 있습니다');

  const written = [];
  const real = store.backend.writeChunks.bind(store.backend);
  store.backend.writeChunks = async (m, t, entries) => {
    written.push(...entries.keys());
    return real(m, t, entries);
  };
  await store.put('KRW-BTC', 'minute1', STEP, range(0, CHUNK * 2));

  assert.equal(await store.count('KRW-BTC', 'minute1'), CHUNK * 4);
  const last = before[before.length - 1].index;
  assert.ok(
    !written.includes(last),
    `과거를 덧붙였는데 마지막 덩어리(${last})까지 다시 썼습니다`,
  );
});

test('꼬리만 읽을 때 앞쪽 덩어리는 건드리지 않는다', async () => {
  const store = freshStore();
  await store.put('KRW-BTC', 'minute1', STEP, range(0, CHUNK * 3));

  let reads = 0;
  const real = store.backend.readChunks.bind(store.backend);
  store.backend.readChunks = async (m, t, ids) => {
    reads += ids.length;
    return real(m, t, ids);
  };

  const tail = await store.loadTail('KRW-BTC', 'minute1', 100);
  assert.equal(tail.length, 100);
  assert.equal(tail[99].ts, START + (CHUNK * 3 - 1) * STEP);
  assert.ok(reads <= 2, `덩어리를 ${reads}개나 읽었습니다 — 꼬리만 읽어야 합니다`);
});

test('기간과 개수는 데이터를 읽지 않고 답한다', async () => {
  const store = freshStore();
  await store.put('KRW-BTC', 'minute1', STEP, range(0, CHUNK * 2));
  store.backend.readChunks = async () => {
    throw new Error('개수를 세려고 데이터를 읽었습니다');
  };
  assert.equal(await store.count('KRW-BTC', 'minute1'), CHUNK * 2);
  assert.deepEqual(
    await store.span('KRW-BTC', 'minute1'),
    [START, START + (CHUNK * 2 - 1) * STEP],
  );
});

test('종목끼리 섞이지 않는다', async () => {
  const store = freshStore();
  await store.put('KRW-BTC', 'minute1', STEP, range(0, 50));
  await store.put('KRW-ETH', 'minute1', STEP, range(0, 20));
  await store.put('KRW-BTC', 'minute5', 300, range(0, 30));
  assert.equal(await store.count('KRW-BTC', 'minute1'), 50);
  assert.equal(await store.count('KRW-ETH', 'minute1'), 20);
  assert.equal(await store.count('KRW-BTC', 'minute5'), 30);
  await store.clear('KRW-BTC', 'minute1');
  assert.equal(await store.count('KRW-BTC', 'minute1'), 0);
  assert.equal(await store.count('KRW-ETH', 'minute1'), 20, '다른 종목까지 지워졌습니다');
});

test('덩어리 나누기가 시각으로만 정해진다', () => {
  // 같은 시각이면 언제 넣든 같은 덩어리다. 이게 앞에 과거를 덧붙여도
  // 뒤쪽을 다시 안 써도 되는 이유다.
  assert.equal(chunkOf(0, STEP), 0);
  assert.equal(chunkOf(STEP * (CHUNK - 1), STEP), 0);
  assert.equal(chunkOf(STEP * CHUNK, STEP), 1);
  const groups = groupByChunk([candle(0), candle(CHUNK), candle(CHUNK + 1)], STEP);
  assert.equal(groups.size, 2);
});

test('합칠 때 시각 순서가 유지된다', () => {
  const merged = mergeCandles([candle(5), candle(1)], [candle(3)]);
  assert.deepEqual(merged.map((c) => c.ts), [candle(1).ts, candle(3).ts, candle(5).ts]);
});

// ------------------------------------------------- 무엇을 받을지 판단
/** 부른 횟수를 세는 가짜 업비트. */
function fakeClient(total) {
  const all = range(0, total);
  const calls = [];
  return {
    calls,
    async collect(market, timeframe, count, { end = null, stopAt = null } = {}) {
      calls.push({ count, end, stopAt });
      let pool = all;
      if (end !== null) pool = pool.filter((c) => c.ts <= end);
      if (stopAt !== null) pool = pool.filter((c) => c.ts >= stopAt);
      return pool.slice(Math.max(0, pool.length - count));
    },
  };
}

test('캐시가 비었으면 요청한 만큼 받는다', async () => {
  const store = freshStore();
  const client = fakeClient(500);
  const got = await update(store, 'KRW-BTC', 'minute1', 300, { client });
  assert.equal(got, 300);
  assert.equal(client.calls.length, 1);
  assert.equal(await store.count('KRW-BTC', 'minute1'), 300);
});

test('이미 가진 과거는 다시 받지 않는다', async () => {
  const store = freshStore();
  // 캐시가 요청한 만큼 이미 차 있다
  await store.put('KRW-BTC', 'minute1', STEP, range(0, 300));
  const client = fakeClient(300);
  const got = await update(store, 'KRW-BTC', 'minute1', 300, { client });

  assert.equal(client.calls.length, 1, '과거를 다시 받으러 갔습니다');
  assert.ok(client.calls[0].stopAt !== null, '어디서 멈출지 안 알려주고 받으러 갔습니다');
  // 실제로 내려받은 개수. 꼬리 몇 개를 다시 받는 것 말고는 없어야 한다.
  assert.ok(
    got <= REFRESH_TAIL + 1,
    `이미 가진 봉을 ${got}개나 다시 받았습니다`,
  );
});

test('새로 생긴 봉만 이어 붙인다', async () => {
  const store = freshStore();
  await store.put('KRW-BTC', 'minute1', STEP, range(0, 300));
  // 과거 300개 뒤로 200개가 더 생긴 상황
  const client = fakeClient(500);
  await update(store, 'KRW-BTC', 'minute1', 300, { client });
  const all = await store.loadAll('KRW-BTC', 'minute1');
  assert.equal(all[all.length - 1].ts, candle(499).ts, '새 봉이 안 붙었습니다');
  for (let i = 1; i < all.length; i += 1) {
    assert.equal(all[i].ts - all[i - 1].ts, STEP, '이어 붙인 자리에 구멍이 생겼습니다');
  }
});

test('더 긴 과거가 필요하면 가진 것보다 아래로만 내려간다', async () => {
  const store = freshStore();
  await store.put('KRW-BTC', 'minute1', STEP, range(400, 500));
  const client = fakeClient(500);
  await update(store, 'KRW-BTC', 'minute1', 500, { client });

  const backward = client.calls.filter((c) => c.end !== null);
  assert.equal(backward.length, 1, '과거를 받으러 정확히 한 번 가야 합니다');
  assert.ok(
    backward[0].end < candle(400).ts,
    '가진 구간을 다시 받으러 갔습니다',
  );
  assert.ok(await store.count('KRW-BTC', 'minute1') >= 500);
});

test('마지막 몇 봉은 일부러 다시 받는다', async () => {
  // 그 분이 끝나기 전에 받은 봉은 확정된 값이 아니다.
  const store = freshStore();
  await store.put('KRW-BTC', 'minute1', STEP, range(0, 300));
  const client = fakeClient(300);
  await update(store, 'KRW-BTC', 'minute1', 300, { client });
  const [call] = client.calls;
  assert.ok(
    call.stopAt <= candle(299).ts - STEP * (REFRESH_TAIL - 1),
    '꼬리를 다시 받지 않고 있습니다',
  );
});

test('캐시에서 계산에 쓸 모양으로 읽어 온다', async () => {
  const store = freshStore();
  await store.put('KRW-BTC', 'minute1', STEP, range(0, 200));
  const series = await loadSeries(store, 'KRW-BTC', 'minute1', 50);
  assert.equal(series.length, 50);
  assert.equal(series.timeframe, 'minute1');
  assert.equal(series.close[49], 100 + 199);
  assert.equal(series.gaps(), 0);
});

test('요약은 가진 기간을 그대로 말한다', async () => {
  const store = freshStore();
  await store.put('KRW-BTC', 'minute1', STEP, range(0, 120));
  const summary = await cachedSummary(store, 'KRW-BTC', ['minute1', 'minute5']);
  assert.deepEqual(summary[0], {
    timeframe: 'minute1', count: 120, from: candle(0).ts, to: candle(119).ts,
  });
  assert.deepEqual(summary[1], {
    timeframe: 'minute5', count: 0, from: null, to: null,
  });
});

// ---------------------------------------------------------- 업비트 응답
test('업비트 시각을 UTC로 읽는다', () => {
  // Z를 안 붙이면 브라우저가 현지 시각으로 읽어서 한국에서는 9시간이 어긋난다.
  const row = {
    candle_date_time_utc: '2024-03-01T00:00:00',
    opening_price: 1, high_price: 2, low_price: 0.5, trade_price: 1.5,
    candle_acc_trade_volume: 10,
  };
  assert.equal(parseCandle(row).ts, Date.UTC(2024, 2, 1) / 1000);
  assert.equal(parseCandle(row).close, 1.5, '종가는 trade_price입니다');
});

test('to 커서가 업비트가 받는 모양이다', () => {
  assert.equal(toCursor(Date.UTC(2024, 2, 1, 12, 34, 56) / 1000), '2024-03-01T12:34:56Z');
});
