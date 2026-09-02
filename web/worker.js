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

import {
  DEFAULT_COUNT, RATIO, analyse, analysisJson, examplesJson, withinLimit,
} from './core/analysis.js';
import { loadSeries, update } from './core/data.js';
import { CandleStore, IndexedDbBackend } from './core/store.js';
import { UpbitClient, UpbitError } from './core/upbit.js';
import { aggregate, timeframeLabel } from './core/models.js';

/** 업비트로 가는 길 자체가 막힌 경우. 화면이 안내를 달리 띄운다. */
const NETWORK = ['offline', 'blocked', 'stalled', 'rate', 'server', 'empty', 'throttled', 'candles'];

let store = null;
let analysis = null;   // 마지막으로 끝낸 계산. 사례를 볼 때마다 다시 찾지 않으려고 들고 있는다.

/**
 * 업비트로 가는 길은 **하나만 둔다.**
 *
 * 누를 때마다 새로 만들면 그때까지 배운 것을 전부 버린다 — 통하는 `to` 표기도,
 * 막혀서 낮춰 둔 속도도. 그러면 막혔을 때 다시 눌러도 똑같은 속도로 똑같이
 * 막힌다. 실제로 두 번 연속 같은 자리에서 멈췄다.
 *
 * 하나를 계속 쓰면 배운 것이 남아, 다시 누를 때마다 조금씩 더 잘 받는다.
 */
let client = null;

/**
 * 사용자가 적어 둔 우회 주소. 화면이 보내 준다.
 *
 * 워커에서는 localStorage를 못 읽으므로 화면이 알려 줘야 한다. 바뀌면
 * 클라이언트를 새로 만든다 — 길 목록이 달라지기 때문이다.
 */
let myProxy = null;

async function ready() {
  if (!store) store = new CandleStore(await IndexedDbBackend.open());
  if (!client) client = new UpbitClient({ myProxy });
  return store;
}

function setProxy(url) {
  const next = url || null;
  if (next === myProxy) return;
  myProxy = next;
  client = null;   // 길 목록이 바뀌었으니 새로 만든다
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
  // 저장하는 건 1분봉뿐이다. 3·5분봉은 그걸 묶어서 만든다 — 없는 게 아니라
  // 받을 필요가 없는 것이다. 여기서 셋을 다 보여주면 두 개가 0으로 떠서
  // '못 받았다'로 읽힌다.
  for (const timeframe of ['minute1']) {
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
/**
 * 받아둔 1분봉을 읽어 계산에 쓸 세 간격을 만든다.
 *
 * 3·5분봉은 1분봉을 묶어서 만든다 — 업비트가 주는 것과 같은 값이고 요청은
 * 하나도 더 안 쓴다.
 */
async function buildSeries(db, market, bars) {
  const series = {};
  const base = await loadSeries(db, market, 'minute1', bars);
  if (!base.length) return series;
  series.minute1 = base;
  for (const [timeframe, factor] of [['minute3', 3], ['minute5', 5]]) {
    const made = aggregate(base, factor);
    if (made.length) series[timeframe] = made;
  }
  return series;
}

/**
 * **가진 것으로 먼저 답하기에 충분한 봉 수.**
 *
 * 왜 이게 필요한가 — 30일치(43,200봉)를 고르고 누르면, 이미 12,696개를
 * 받아 뒀는데도 나머지 3만 개를 다 채울 때까지 아무 답도 안 줬다. 그 사이
 * 화면에는 "다시 해 보는 중"만 몇 분씩 떴고, 사용자 눈에는 작동을 안 하는
 * 것과 구분이 안 됐다. 실제로 그런 화면을 받았다.
 *
 * 이미 8.8일치가 있는데 답을 못 줄 이유가 없다. 먼저 답하고, 더 받으면
 * 다시 답한다.
 *
 * 2,000봉(약 1.4일)을 문턱으로 둔다. 그보다 적으면 닮은 과거가 스무 개도
 * 안 나와서 "표본이 모자랍니다"만 보여 주게 되고, 그건 답이 아니다.
 */
const FIRST_ANSWER = 2000;

/** 업비트가 알려 준 남은 한도를 화면에 적을 문구로. 못 읽으면 빈 문자열. */
function budget() {
  const left = client?.limiter?.remaining;
  if (!left) return '';
  const bits = [];
  if (Number.isFinite(left.sec)) bits.push(`초 ${left.sec}`);
  if (Number.isFinite(left.min)) bits.push(`분 ${left.min}`);
  return bits.length ? ` · 업비트가 남았다는 한도 ${bits.join('/')}` : '';
}

async function run({ market, count, fresh, similarity, fee, slippage, length, stake }) {
  const db = await ready();
  // 상한은 **여기서** 건다. 화면 쪽만 막으면 낡은 화면이나 손으로 보낸
  // 메시지가 그대로 통과해, 브라우저가 감당 못 할 크기를 받으러 간다.
  const bars = withinLimit(count);

  // 업비트로 가는 길이 막혔는지. 막혔으면 나머지 봉 간격도 똑같이 막혀
  // 있으므로 더 시도하지 않는다 — 간격마다 재시도를 다 기다리면 아무
  // 소득 없이 45초가 그냥 간다.
  let blocked = null;

  // **1분봉만 받는다.**
  //
  // 3분봉은 1분봉 셋을 묶은 것과 정확히 같고, 5분봉은 다섯을 묶은 것과 같다.
  // 그런데 지금까지 셋을 각각 따로 받았다 — 30일치면 216 + 72 + 43 = 331번.
  // 이미 받은 걸로 만들 수 있는 것을 다시 받고 있었던 셈이다.
  //
  // 업비트가 우리를 막는 이유가 요청이 잦아서이므로(아이패드 진단이 그걸
  // 보여줬다), 요청 수를 줄이는 게 가장 큰 지렛대다. 216번으로 끝난다.
  // **가진 것으로 먼저 답한다.**
  //
  // 다 받을 때까지 기다리게 하지 않는다. 받아둔 것이 쓸 만하면 그걸로 바로
  // 계산해서 보여 주고, 받기는 뒤에서 계속한다. 다 받으면 다시 계산해서
  // 덮어쓴다 — 숫자는 그때 정확해진다.
  if (fresh) {
    const have = await db.count(market, 'minute1');
    if (have >= FIRST_ANSWER) {
      progress(`받아둔 ${have.toLocaleString()}개로 먼저 계산합니다…`);
      const early = await buildSeries(db, market, bars);
      if (Object.keys(early).length) {
        analysis = analyse(market, early, { similarity, fee, slippage, length, stake });
        say({
          type: 'partial',
          analysis: analysisJson(analysis),
          have,
          want: Math.floor(bars / RATIO.minute1),
        });
      }
    }
  }

  if (fresh) {
    for (const timeframe of ['minute1']) {
      if (blocked) break;
      const label = timeframeLabel(timeframe);
      const wanted = Math.floor(bars / RATIO[timeframe]);
      // **이미 가진 건 다시 안 받는다는 걸 화면에서 보이게 한다.**
      //
      // 지나간 봉은 변하지 않으므로 한 번 받아 두면 끝이다. 그런데 화면에는
      // 그냥 "받는 중…"만 떠서, 중간에 끊기면 처음부터 다시 받는 것처럼
      // 보인다. 그러면 다시 누르기가 겁난다 — 사실은 누를수록 쌓이는데도.
      // eslint-disable-next-line no-await-in-loop
      const already = await db.count(market, timeframe);
      const kept = already ? ` (이미 ${already.toLocaleString()}개 있음 — 다시 안 받습니다)` : '';
      progress(`${label} 받는 중…${kept}`, 0, Math.max(0, wanted - already));
      try {
        // eslint-disable-next-line no-await-in-loop
        await update(db, market, timeframe, wanted, {
          client,
          onProgress: (done, total, info) => {
            if (info?.banned) {
              // 몇 분을 기다려야 한다. 남은 시간을 초 단위로 보여주지 않으면
              // 멈춘 것으로 보이고, 사용자는 앱을 닫는다.
              progress(
                `업비트가 막고 있습니다 — ${info.waitLeft}초 뒤에 이어서 받습니다`
                + ` (받아둔 ${(already + done).toLocaleString()}개는 그대로입니다)`,
                done, total,
              );
              return;
            }
            if (info?.retrying) {
              // **거절당해서 다시 해 보는 중이라는 걸 말한다.**
              //
              // 이게 없을 때 화면에는 "받는 중…"만 몇 분씩 떠 있었다.
              // 앱이 죽은 것과 구분이 안 되고, 실제로 그렇게 보였다.
              progress(
                `업비트가 거절해서 다시 해 보는 중입니다 (${info.retrying}/${info.of})`
                + ` — 받아둔 ${(already + done).toLocaleString()}개는 그대로입니다`,
                done, total,
              );
              return;
            }
            progress(
              info?.stalled
                ? `${label} 잠시 걸렸습니다 — ${info.waitLeft}초 뒤 이어서 받습니다${kept}`
                // 지금 속도를 같이 적는다. 느릴 때 왜 느린지 보이지 않으면
                // 멈춘 건지 기다리는 건지 알 수가 없다.
                // **업비트가 알려 준 남은 한도를 그대로 적는다.**
                // 이 숫자가 보이면 "왜 거절당하나"를 추측할 필요가 없다.
                : `${label} 받는 중… 초당 ${client.limiter.perSecond.toFixed(1)}회${budget()}${kept}`,
              done, total,
            );
          },
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
  const series = await buildSeries(db, market, bars);

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
    similarity, fee, slippage, length, stake, onStep: progress,
  });
  say({ type: 'done', analysis: analysisJson(analysis), stale: blocked !== null });
}

onmessage = async (event) => {
  const message = event.data ?? {};
  try {
    // 우회 주소는 어떤 일을 하기 전에 먼저 반영한다. 화면이 모든 메시지에
    // 실어 보내므로, 사용자가 고치면 그다음 요청부터 바로 그 길로 간다.
    if ('myProxy' in message) setProxy(message.myProxy);
    if (message.type === 'proxy') return;   // 알려 주기만 하는 메시지
    // 맨 위 시세도 **여기를 거친다.**
    //
    // 예전에는 화면 쪽에 UpbitClient가 따로 있었다. 그러면 속도 제한기가
    // 둘이 되어 서로를 모른다 — "초당 3회"가 사실이 아니게 된다. 게다가
    // 5초마다 부르고 있어서, 아무것도 안 하고 앱만 켜 둬도 시간당 720번이
    // 나갔다. **막혀 있는 동안에도 계속 두드려서 회복을 방해했다.**
    //
    // 업비트로 나가는 길을 하나로 모으면, 내려받기와 시세가 같은 줄을 서고
    // 막힌 것도 한 곳에서 안다.
    if (message.type === 'ticker') {
      await ready();
      // 막혀 있는 걸 이미 안다면 두드리지 않는다. 맨 위 숫자 하나 때문에
      // 회복을 늦출 이유가 없다.
      if (client.knownBlocked()) {
        say({ type: 'ticker', market: message.market, rows: [] });
        return;
      }
      try {
        const rows = await client.getTicker(message.market);
        say({ type: 'ticker', market: message.market, rows });
      } catch {
        // 맨 위 숫자는 장식이다. 안 나온다고 화면을 빨갛게 만들지 않는다.
        say({ type: 'ticker', market: message.market, rows: [] });
      }
      return;
    }
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
