// 무거운 일을 여기서 한다. 화면과 다른 실 위에서 돈다.
//
// 왜 나눴는가
// ----------
// 8년치 1분봉은 420만 개다. 닮은 과거를 찾는 계산은 그 사이에 잠깐도
// 멈추지 않는데, 이걸 화면과 같은 실에서 돌리면 그동안 **화면이 통째로
// 얼어붙는다**. 스크롤도 안 되고, 멈추기 단추도 안 눌린다. 아이패드에서는
// 브라우저가 "응답 없음"으로 판단해 페이지를 죽이기도 한다.
//
// 예전에는 파이썬 서버가 이 자리에 있었다. 서버가 없어진 지금, 이 워커가
// 그 역할을 그대로 한다 — 받아서, 계산하고, 결과만 넘긴다.

import { DEFAULT_COUNT, RATIO, analyse, analysisJson, examplesJson } from './core/analysis.js';
import { loadSeries, update } from './core/data.js';
import { CandleStore, IndexedDbBackend } from './core/store.js';
import { UpbitClient, UpbitError } from './core/upbit.js';
import { timeframeLabel } from './core/models.js';

/** 업비트로 가는 길 자체가 막힌 경우. 화면이 안내를 달리 띄운다. */
const NETWORK = ['offline', 'blocked', 'rate', 'server'];

let store = null;
let analysis = null;   // 마지막으로 끝낸 계산. 사례를 볼 때마다 다시 찾지 않으려고 들고 있는다.

async function ready() {
  if (!store) store = new CandleStore(await IndexedDbBackend.open());
  return store;
}

const say = (message) => postMessage(message);

const progress = (text, done = 0, total = 0) => say({
  type: 'progress', message: text, done, total,
});

/**
 * 실패를 화면이 쓸 수 있는 모양으로.
 *
 * 오류 문구 한 줄만 넘기면 사용자는 무엇을 해야 할지 알 수 없다. 무엇 때문에
 * 막혔는지(`kind`)를 같이 넘겨서, 화면이 그에 맞는 안내를 띄우게 한다.
 */
function asFailure(error) {
  if (error instanceof UpbitError) {
    return { type: 'error', kind: error.kind, message: error.message };
  }
  return { type: 'error', kind: 'app', message: error?.message ?? String(error) };
}

/** 받아둔 시세 요약. 계산 없이 캐시 상태만 — 화면을 열자마자 보여줄 것. */
async function summary(market) {
  const db = await ready();
  const rows = [];
  for (const timeframe of Object.keys(DEFAULT_COUNT)) {
    // eslint-disable-next-line no-await-in-loop
    const count = await db.count(market, timeframe);
    // eslint-disable-next-line no-await-in-loop
    const span = await db.span(market, timeframe);
    rows.push({
      timeframe,
      label: timeframeLabel(timeframe),
      count,
      from: span ? new Date(span[0] * 1000).toISOString() : null,
      to: span ? new Date(span[1] * 1000).toISOString() : null,
    });
  }
  return rows;
}

/**
 * 받고 계산한다.
 *
 * `fresh`가 거짓이면 업비트에 가지 않고 가진 것만으로 다시 센다. 설정만
 * 바꿔 보는데 매번 받으러 가면 느리고, 받을 것도 없다.
 */
async function run({ market, count, fresh, similarity, fee, slippage, length }) {
  const db = await ready();
  const client = new UpbitClient();

  // 업비트로 가는 길이 막혔는지. 막혔으면 나머지 봉 간격도 똑같이 막혀
  // 있으므로 더 시도하지 않는다 — 간격마다 재시도를 다 기다리면 아무
  // 소득 없이 45초가 그냥 간다.
  let blocked = null;

  if (fresh) {
    for (const timeframe of Object.keys(DEFAULT_COUNT)) {
      if (blocked) break;
      const label = timeframeLabel(timeframe);
      const wanted = Math.floor(count / RATIO[timeframe]);
      progress(`${label} 받는 중…`, 0, wanted);
      try {
        // eslint-disable-next-line no-await-in-loop
        await update(db, market, timeframe, wanted, {
          client,
          onProgress: (done, total) => progress(`${label} 받는 중…`, done, total),
        });
      } catch (error) {
        if (error instanceof UpbitError && NETWORK.includes(error.kind)) {
          blocked = error.kind;
        } else {
          // 한 간격을 못 받아도 나머지로 계산은 된다. 다만 조용히 넘어가면
          // 사용자는 3종을 다 본 줄 안다 — 결과의 '빠진 봉 간격'에 나온다.
          say({ type: 'warn', message: `${label}을 갱신하지 못했습니다: ${error.message}` });
        }
      }
    }
  }

  progress('받아둔 시세를 읽는 중…');
  const series = {};
  for (const timeframe of Object.keys(DEFAULT_COUNT)) {
    const wanted = Math.floor(count / RATIO[timeframe]);
    // eslint-disable-next-line no-await-in-loop
    const loaded = await loadSeries(db, market, timeframe, wanted);
    if (loaded.length) series[timeframe] = loaded;
  }

  // **못 받았다는 사실을 반드시 말한다.**
  //
  // 예전에는 이걸 빨간 글씨 한 줄로만 흘렸다. 그러면 받아둔 옛날 시세로
  // 계산한 결과가 '지금 시세로 판단받기'의 답인 것처럼 화면에 뜬다.
  // 몇 시간 전 데이터를 지금 것으로 읽게 만드는 셈이라, 이건 조용히
  // 넘어가면 안 되는 종류의 실패다.
  if (blocked) say({ type: 'blocked', kind: blocked, stale: Object.keys(series).length > 0 });

  if (!Object.keys(series).length) {
    // 받지도 못했고 가진 것도 없다. 여기서 "먼저 시세 받기를 누르세요"라고
    // 하면 방금 누른 사람에게 하는 말이 되어 버린다.
    if (blocked) return;
    throw new Error("시세가 없습니다. '지금 시세로 판단받기'를 눌러 주세요.");
  }

  analysis = analyse(market, series, {
    similarity, fee, slippage, length, onStep: progress,
  });
  say({ type: 'done', analysis: analysisJson(analysis), stale: blocked !== null });
}

onmessage = async (event) => {
  const message = event.data ?? {};
  try {
    if (message.type === 'summary') {
      say({ type: 'summary', market: message.market, cached: await summary(message.market) });
      return;
    }
    if (message.type === 'run') {
      analysis = null;
      await run(message);
      return;
    }
    if (message.type === 'examples') {
      if (!analysis) return;
      say({
        type: 'examples',
        examples: examplesJson(analysis, message.timeframe, message.horizon),
      });
      return;
    }
    if (message.type === 'forget') {
      const db = await ready();
      for (const timeframe of Object.keys(DEFAULT_COUNT)) {
        // eslint-disable-next-line no-await-in-loop
        await db.clear(message.market, timeframe);
      }
      analysis = null;
      say({ type: 'summary', market: message.market, cached: await summary(message.market) });
      return;
    }
    say(asFailure(new Error(`모르는 요청입니다: ${message.type}`)));
  } catch (error) {
    say(asFailure(error));
  }
};
