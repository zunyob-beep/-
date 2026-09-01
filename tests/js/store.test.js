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
import { MAX_BARS, PERIODS, withinLimit } from '../../web/core/analysis.js';
import { UpbitClient, parseCandle, toCursor } from '../../web/core/upbit.js';

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
//
// 가짜를 **HTTP 자리에** 세운다.
//
// 처음에는 client.collect를 통째로 가짜로 바꿨는데, 그러면 정작 확인하고
// 싶은 것(쪽수 넘기기, 어디서 멈추는지, 받는 족족 저장하는지)이 전부
// 가짜 안에 있어서 아무것도 검증하지 못한다. 실제로 collect를 스트리밍
// 방식으로 고쳤을 때 가짜가 옛 방식 그대로라 시험이 통과해 버렸다.
//
// 그래서 진짜 UpbitClient를 쓰고 fetch만 바꾼다. 업비트가 주는 모양
// 그대로 답하므로 파싱·정렬·커서까지 다 지나간다.

/** 업비트가 주는 모양으로 답하는 가짜 서버. 요청 횟수를 센다. */
function fakeUpbit(total) {
  const seen = [];
  const priceAt = (i) => 100 + i;
  const row = (i) => ({
    market: 'KRW-BTC',
    candle_date_time_utc: new Date((START + i * STEP) * 1000).toISOString().slice(0, 19),
    opening_price: priceAt(i),
    high_price: priceAt(i) + 1,
    low_price: priceAt(i) - 1,
    trade_price: priceAt(i),
    candle_acc_trade_volume: 1 + (i % 5),
  });

  const fetcher = async (url) => {
    const parsed = new URL(url);
    const count = Number(parsed.searchParams.get('count') ?? 200);
    const to = parsed.searchParams.get('to');
    // 업비트는 `to`보다 **이전** 봉을 최신순으로 준다.
    const newest = to
      ? Math.min(total - 1, Math.floor((Date.parse(to) / 1000 - START) / STEP))
      : total - 1;
    seen.push({ count, newest });
    const rows = [];
    for (let k = 0; k < count; k += 1) {
      const i = newest - k;
      if (i < 0) break;
      rows.push(row(i));
    }
    return { status: 200, async json() { return rows; }, async text() { return ''; } };
  };

  const client = new UpbitClient({ retries: 0, perSecond: 100000, fetcher });
  client.seen = seen;
  return client;
}

test('캐시가 비었으면 요청한 만큼 받는다', async () => {
  const store = freshStore();
  const client = fakeUpbit(500);
  const got = await update(store, 'KRW-BTC', 'minute1', 300, { client });
  assert.equal(got, 300, '받은 개수');
  assert.equal(await store.count('KRW-BTC', 'minute1'), 300);
  // 200개씩 주므로 300개면 두 번이다. 한 번에 다 달라고 하면 안 된다.
  assert.equal(client.seen.length, 2, `요청 ${client.seen.length}번`);
});

test('이미 가진 과거는 다시 받지 않는다', async () => {
  const store = freshStore();
  // 캐시가 요청한 만큼 이미 차 있다
  await store.put('KRW-BTC', 'minute1', STEP, range(0, 300));
  const client = fakeUpbit(300);
  await update(store, 'KRW-BTC', 'minute1', 300, { client });

  // 이게 이 앱의 핵심 약속이다. 300개를 이미 갖고 있으면 요청은 한 번,
  // 그것도 꼬리를 다시 받기 위한 것뿐이어야 한다.
  assert.equal(client.seen.length, 1, `요청을 ${client.seen.length}번 했습니다`);
  assert.equal(await store.count('KRW-BTC', 'minute1'), 300, '개수가 변하면 안 됩니다');
});

test('새로 생긴 봉만 이어 붙인다', async () => {
  const store = freshStore();
  await store.put('KRW-BTC', 'minute1', STEP, range(0, 300));
  // 과거 300개 뒤로 200개가 더 생긴 상황
  const client = fakeUpbit(500);
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
  const client = fakeUpbit(500);
  await update(store, 'KRW-BTC', 'minute1', 500, { client });

  // 가진 구간(400~499)보다 위를 다시 받으러 간 요청이 있으면 안 된다.
  const backward = client.seen.filter((c) => c.newest < 400);
  assert.ok(backward.length >= 1, '과거를 받으러 가지 않았습니다');
  assert.ok(await store.count('KRW-BTC', 'minute1') >= 500);
});

test('마지막 몇 봉은 일부러 다시 받는다', async () => {
  // 그 분이 끝나기 전에 받은 봉은 확정된 값이 아니다.
  const store = freshStore();
  await store.put('KRW-BTC', 'minute1', STEP, range(0, 300));
  const client = fakeUpbit(300);
  await update(store, 'KRW-BTC', 'minute1', 300, { client });
  // 꼬리를 다시 받았으면 마지막 봉들이 새 값으로 덮여 있어야 한다.
  assert.equal(client.seen.length, 1, '꼬리를 다시 받지 않았습니다');
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

// ------------------------------------------------------------ 한도
test('브라우저가 감당할 크기를 넘지 않는다', () => {
  // 8년치(420만 봉)는 배열만 270MB가 넘어 아이패드에서 탭이 죽는 쪽에
  // 가깝다. 죽는 단추를 화면에 두느니 없는 게 낫다. 4년까지는 실제로
  // 재 봤다 — 세 간격 합쳐 2.8초, 배열 135MB.
  for (const period of PERIODS) {
    assert.ok(period.count <= MAX_BARS, `${period.label}이 상한을 넘습니다`);
  }
  assert.ok(PERIODS.length >= 2, '고를 수 있는 기간이 남아 있어야 합니다');
  // 4년은 실제로 고를 수 있어야 한다.
  assert.ok(PERIODS.some((p) => p.label === '4년'), '4년 선택지가 없습니다');
});

test('상한은 화면이 아니라 계산 쪽에서 건다', () => {
  // 화면만 막으면 낡은 화면이나 손으로 보낸 메시지가 그대로 통과한다.
  assert.equal(withinLimit(4204800), MAX_BARS, '상한을 넘는 값을 그대로 받아들였습니다');
  assert.equal(withinLimit(43200), 43200, '상한 안의 값은 그대로여야 합니다');
  assert.equal(withinLimit(0), PERIODS[0].count);
  assert.equal(withinLimit(-5), PERIODS[0].count);
  assert.equal(withinLimit('abc'), PERIODS[0].count);
  assert.equal(withinLimit(undefined), PERIODS[0].count);
});

// ------------------------------------------- 빠른 읽기와 느린 읽기가 같은가
test('배열로 읽는 것과 봉으로 읽는 것이 같은 답을 낸다', async () => {
  // loadTailColumns는 8년치를 견디려고 객체를 안 만드는 대신 손으로 배열을
  // 채운다. 손으로 채우는 코드는 어긋나기 쉬우므로, 읽기 쉬운 쪽(loadTail)과
  // 늘 같은 답이 나오는지 묶어 둔다.
  const store = freshStore();
  await store.put('KRW-BTC', 'minute1', STEP, range(0, CHUNK * 2 + 500));

  for (const wanted of [1, 7, 100, CHUNK - 1, CHUNK, CHUNK + 1, CHUNK * 3]) {
    // eslint-disable-next-line no-await-in-loop
    const slow = await store.loadTail('KRW-BTC', 'minute1', wanted);
    // eslint-disable-next-line no-await-in-loop
    const fast = await store.loadTailColumns('KRW-BTC', 'minute1', wanted);
    assert.equal(fast.length, slow.length, `${wanted}개를 달라고 했을 때 개수`);
    assert.deepEqual(Array.from(fast.ts), slow.map((c) => c.ts), `${wanted}개: 시각`);
    assert.deepEqual(Array.from(fast.close), slow.map((c) => c.close), `${wanted}개: 종가`);
    assert.deepEqual(Array.from(fast.high), slow.map((c) => c.high), `${wanted}개: 고가`);
    assert.deepEqual(Array.from(fast.volume), slow.map((c) => c.volume), `${wanted}개: 거래량`);
  }
  const none = await store.loadTailColumns('KRW-ETH', 'minute1', 100);
  assert.equal(none.length, 0, '없는 종목은 빈 배열이어야 합니다');
});
