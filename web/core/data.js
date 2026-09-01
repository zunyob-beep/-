// 무엇을 받고 무엇을 안 받을지 정한다.
//
// **지나간 봉은 변하지 않는다.** 이 한 줄이 이 파일의 전부다.
//
// 파이썬 판에서 이걸 안 지켜서, 8년치를 받아둔 사람이 새 봉 세 개를 얻으려고
// 버튼을 누르면 41초가 걸렸다. 캐시를 통째로 읽어 합치고 통째로 다시 썼기
// 때문이다. 새로 생긴 것만 이어 붙이도록 고치자 49밀리초가 됐다.
//
// 브라우저에서는 더 중요하다. 아이패드에서 8년치를 다시 받으면 몇십 분이
// 걸리고, 그 사이 화면을 잠그면 중간에 끊긴다.

import { Series, timeframeSeconds } from './models.js';
import { UpbitClient } from './upbit.js';

/**
 * 마지막 봉을 몇 개나 다시 받을지.
 *
 * 가장 최근 봉은 그 분이 끝나기 전에 받으면 아직 확정된 값이 아니다.
 * 그래서 꼬리 몇 개는 늘 다시 받아 덮어쓴다.
 */
export const REFRESH_TAIL = 3;

/**
 * 캐시를 원하는 만큼 채운다. 받은 봉 수를 돌려준다.
 *
 * 세 가지 경우로 갈린다.
 *
 *   1. 캐시가 비었다        → `wanted`개를 처음부터 받는다
 *   2. 캐시가 충분히 길다   → **새로 생긴 것만** 받는다 (거의 항상 이쪽)
 *   3. 더 과거가 필요하다   → 가진 것보다 과거로만 내려간다
 *
 * 2번과 3번 모두 이미 가진 구간은 다시 받지 않는다.
 */
export async function update(store, market, timeframe, wanted, options = {}) {
  const { client = new UpbitClient(), onProgress = null, shouldStop = null } = options;
  const step = timeframeSeconds(timeframe);
  const have = await store.count(market, timeframe);
  const span = await store.span(market, timeframe);

  const save = async (candles) => {
    if (candles.length) await store.put(market, timeframe, step, candles);
  };

  // 1) 아무것도 없다 — 처음부터
  if (!span || have === 0) {
    const fresh = await client.collect(market, timeframe, wanted, {
      onProgress, shouldStop, onBatch: null,
    });
    await save(fresh);
    return fresh.length;
  }

  const [firstTs, lastTs] = span;

  // 2) 뒤쪽(새로 생긴 봉)을 먼저 채운다. 이게 매번 일어나는 일이다.
  //
  // 몇 개나 새로 생겼는지는 시각으로 셀 수 있다. 몇 개 안 되면 한 번만
  // 부르면 끝난다 — 여기가 41초와 49밀리초를 가르는 자리다.
  const now = Math.floor(Date.now() / 1000);
  // `wanted`로 자른다. 캐시가 아주 오래됐으면 '빠진 봉'이 수백만 개로
  // 나오는데, 어차피 그만큼 필요하지도 않고 진행률이 엉뚱해진다.
  const missing = Math.min(
    wanted, Math.max(0, Math.ceil((now - lastTs) / step)) + REFRESH_TAIL,
  );
  let got = 0;
  if (missing > 0) {
    const tail = await client.collect(market, timeframe, missing, {
      stopAt: lastTs - step * REFRESH_TAIL, onProgress, shouldStop,
    });
    await save(tail);
    got += tail.length;
  }

  // 3) 더 과거가 필요하면 가진 것보다 **아래로만** 내려간다.
  const total = await store.count(market, timeframe);
  if (total < wanted) {
    const older = await client.collect(market, timeframe, wanted - total, {
      end: firstTs - step,
      onProgress: onProgress
        ? (done, want) => onProgress(total + done, wanted)
        : null,
      shouldStop,
      // 오래 걸리는 수집이다. 페이지마다 저장해 두면 중간에 끊겨도
      // 받은 만큼은 남는다.
      onBatch: async (batch) => { await save(batch); },
    });
    await save(older);
    got += older.length;
  }
  return got;
}

/** 캐시에서 읽어 계산에 쓸 모양으로 만든다. */
export async function loadSeries(store, market, timeframe, wanted) {
  const candles = await store.loadTail(market, timeframe, wanted);
  return Series.fromCandles(market, timeframe, candles);
}

/** 화면 맨 위 요약에 쓸 정보. 계산 없이 캐시 상태만. */
export async function cachedSummary(store, market, timeframes) {
  const out = [];
  for (const timeframe of timeframes) {
    // eslint-disable-next-line no-await-in-loop
    const count = await store.count(market, timeframe);
    // eslint-disable-next-line no-await-in-loop
    const span = await store.span(market, timeframe);
    out.push({
      timeframe,
      count,
      from: span ? span[0] : null,
      to: span ? span[1] : null,
    });
  }
  return out;
}
