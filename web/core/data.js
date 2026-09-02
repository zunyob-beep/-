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

  // 받는 족족 저장한다. 받은 것을 다 모아 뒀다가 마지막에 한 번에 넣으면
  // (1) 8년치 420만 개가 메모리에 쌓여 아이패드에서 브라우저가 죽고
  // (2) 중간에 끊기면 그때까지 받은 게 전부 날아간다.
  //
  // **새로 늘어난 개수만 센다.** 받은 개수를 세면 꼬리를 다시 받은 것까지
  // 세어서, "300개 받았다"고 해 놓고 캐시는 그대로인 일이 생긴다.
  let saved = 0;
  const save = async (candles) => {
    if (!candles.length) return;
    saved += await store.put(market, timeframe, step, candles);
    report();
  };

  /**
   * **진행은 여기서만 센다.** 늘 같은 뜻이다 — `(지금 가진 개수, 목표 개수)`.
   *
   * 예전에는 세 곳이 제각각이었다. 캐시가 빌 때는 상대값, 새 봉을 채울 때는
   * 분모가 `missing`(3 같은 수), 과거를 채울 때는 절대값. 그런데 부르는 쪽
   * (워커)은 셋 다 상대값인 줄 알고 이미 가진 개수를 **또** 더했다.
   *
   * 그래서 화면에 "12,699 / 3개"나 두 배로 부푼 숫자가 떴다. 총 개수가
   * 틀렸던 게 이것이다. 세 곳이 각자 세는 대신, 한 곳에서만 센다.
   */
  const report = (info) => {
    if (onProgress) onProgress(have + saved, wanted, info);
  };
  const relay = (done, total, info) => report(info);

  // 1) 아무것도 없다 — 처음부터
  if (!span || have === 0) {
    await client.collect(market, timeframe, wanted, {
      onProgress: relay, shouldStop, onBatch: save, retain: false,
    });
    return saved;
  }

  const [, lastTs] = span;

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
  if (missing > 0) {
    await client.collect(market, timeframe, missing, {
      stopAt: lastTs - step * REFRESH_TAIL,
      onProgress: relay,
      shouldStop,
      onBatch: save,
      retain: false,
    });
  }

  // 3) 더 과거가 필요하면 가진 것보다 **아래로만** 내려간다.
  //
  // 여기서 기준을 **다시 읽어야 한다.** 2번이 방금 앞쪽으로 봉을 붙였으므로
  // 처음에 읽어둔 firstTs는 이미 낡았다. 낡은 값으로 내려가면 2번이 방금
  // 받은 구간을 한 쪽 더 받고, 그 중복까지 개수로 세는 바람에 목표치를
  // 못 채우고 멈춘다 — 실제로 500개를 요청했는데 400개에서 끝났다.
  const filled = await store.span(market, timeframe);
  const total = await store.count(market, timeframe);
  if (filled && total < wanted) {
    await client.collect(market, timeframe, wanted - total, {
      end: filled[0] - step,
      onProgress: relay,
      shouldStop,
      onBatch: save,
      retain: false,
    });
  }
  return saved;
}

/**
 * 캐시에서 읽어 계산에 쓸 모양으로 만든다.
 *
 * 봉 하나마다 객체를 만들지 않는다 — 8년치면 그것만으로 브라우저가 죽는다.
 * 저장된 배열을 곧장 잘라 붙인다.
 */
export async function loadSeries(store, market, timeframe, wanted) {
  const columns = await store.loadTailColumns(market, timeframe, wanted);
  return new Series(
    market, timeframe,
    columns.ts, columns.open, columns.high, columns.low, columns.close, columns.volume,
  );
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
