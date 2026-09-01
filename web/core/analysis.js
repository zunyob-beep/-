// 계산을 한 번 돌려 화면이 쓰는 모양으로 묶어 낸다.
//
// 예전에는 이 일을 파이썬 웹서버(patternscan/webui/server.py)가 했다. 서버가
// 없어졌으니 같은 일을 브라우저 안에서 한다 — 내보내는 JSON의 모양은
// 그대로다. 화면 코드가 그 모양에 맞춰 쓰여 있고, 두 구현이 같은 답을
// 내는지 대조할 때도 이 모양이 기준이 된다.

import { HORIZONS, timeframeLabel } from './models.js';
import { pct, pctSigned, won, wonSigned } from './format.js';
import { levels, retracements } from './levels.js';
import {
  MIN_SAMPLES, examplesFor, findMatches, oddsFor, project, roundTripCost,
} from './odds.js';
import { LOOKBACK, dow, dowConfirmation, readAll, score, tally } from './theories.js';
import { normalizeWindow } from './shape.js';

/** 기본으로 받아둘 봉 개수 (1분봉 30일 기준). */
export const DEFAULT_COUNT = { minute1: 43200, minute3: 14400, minute5: 8640 };

/** 1분봉 개수를 기준으로 각 간격이 몇 분의 1인지. */
export const RATIO = { minute1: 1, minute3: 3, minute5: 5 };

/**
 * 브라우저에서 한 번에 다룰 1분봉 개수의 **상한**.
 *
 * 4년(210만 봉)까지 둔다. 감으로 정하지 않고 실제로 재 봤다.
 *
 *   1년(53만 봉)   닮은 과거 찾기 0.4초 · 세 간격 합쳐 0.7초
 *   4년(210만 봉)  닮은 과거 찾기 1.7초 · 세 간격 합쳐 2.8초
 *                  숫자 배열 약 135MB
 *
 * 계산은 워커에서 도니 그동안에도 화면은 안 멈춘다. 8년(420만 봉)은 배열만
 * 270MB가 넘어 아이패드 사파리가 탭을 죽이는 쪽에 가까워서 여기까지로 둔다.
 * 그보다 긴 과거가 필요하면 파이썬 판에 8년이 그대로 있다.
 */
/**
 * 판정 문구에서 '얼마를 넣었다면'의 기본값.
 *
 * 예전에는 이 숫자가 문장 안에 100만원으로 박혀 있었다. 그래서 화면에서
 * 금액을 바꿔도 판정은 늘 100만원 기준으로 말했다 — 넣을 금액을 물어 놓고
 * 답에는 반영하지 않은 셈이다.
 */
export const DEFAULT_STAKE = 1000000;

export const MAX_BARS = 2102400;

/**
 * 얼마나 과거까지 볼지 — 화면의 선택지. MAX_BARS를 넘는 것은 두지 않는다.
 *
 * **짧은 쪽을 뒤늦게 넣었다.** 예전에는 30일이 가장 짧은 선택지였는데,
 * 30일치는 첫 요청이 216번이다. 업비트가 우리 주소를 막고 있는 날에는 그
 * 216번이 한 번도 안 끝나서 **아무것도 못 보고** 끝났다. 1일치는 8번,
 * 7일치는 51번이라 막힌 와중에도 통과할 확률이 훨씬 높다.
 *
 * 한 번 받은 과거는 다시 받지 않으므로, 7일 → 30일 → 90일로 올려 가며
 * 눌러도 받는 총량은 같다. 한꺼번에 몰아 받지 않을 뿐이다.
 */
export const PERIODS = [
  { label: '1일', count: 1440 },
  { label: '7일', count: 10080 },
  { label: '30일', count: 43200 },
  { label: '90일', count: 129600 },
  { label: '1년', count: 525600 },
  { label: '2년', count: 1051200 },
  { label: '4년', count: 2102400 },
].filter((p) => p.count <= MAX_BARS);

/**
 * 화면을 열었을 때 골라져 있는 기간.
 *
 * 가장 짧은 1일이 아니다. 1일치(1,440봉)로는 닮은 과거가 20개도 안 나와서
 * "표본이 모자랍니다"만 보게 되는 일이 잦다 — 처음 온 사람이 그 화면을 보면
 * 고장난 줄 안다. 7일치(51번, 약 17초)는 통계가 나오면서도 빨리 끝난다.
 */
export const DEFAULT_PERIOD = 10080;

/** 화면이 보낸 값이 무엇이든 상한 안으로 눌러 준다. */
export function withinLimit(count) {
  const asked = Number(count);
  if (!Number.isFinite(asked) || asked <= 0) return PERIODS[0].count;
  return Math.min(Math.floor(asked), MAX_BARS);
}

/**
 * 예상 앞에 붙일 **실제 봉** 개수. 지나온 길이 없으면 그림이 허공에서
 * 시작해 진짜 차트로 안 보인다.
 */
export const RECENT_BARS = 40;

/** 이론 성적을 낼 때 몇 봉 뒤를 보는지. analyse()와 아래 안내가 같이 쓴다. */
export const SCORE_HORIZON = 10;

/**
 * 과거 성적을 내려면 최소 몇 봉이 있어야 하는지.
 *
 * theories.score는 시점마다 그 이전 LOOKBACK봉을 보고, 결과를 보려고
 * horizon봉을 더 쓴다. 그보다 짧으면 **한 번도 채점할 수 없다.**
 */
export const SCORE_NEEDS = LOOKBACK + SCORE_HORIZON + 2;

/**
 * 왜 표본이 안 모였는지, 그리고 **무엇을 바꾸면 되는지**.
 *
 * "데이터를 더 받거나 기준을 낮춰 보세요" 한 줄은 이미 기준을 낮춘
 * 사람에게 아무 말도 안 한 것과 같다. 몇 개가 모였는지 숫자로 말해 준다.
 *
 * 그리고 진짜 효과가 큰 손잡이를 먼저 말한다. **직전 봉 개수**다.
 * 180개짜리 모양이 과거에 그대로 반복될 일은 드물다 — 유사도를 아무리
 * 낮춰도 잘 안 늘어난다. 20~40개로 줄이면 표본이 확 는다.
 */
export function whyNothingMatched(rows) {
  const best = rows.reduce((acc, r) => Math.max(acc, r.samples), 0);
  const length = rows.length ? rows[0].length : 0;
  const found = best
    ? `가장 많이 모인 조합도 ${best}개뿐입니다 (최소 ${MIN_SAMPLES}개 필요).`
    : '닮은 과거 구간이 하나도 없습니다.';
  const advice = ['**직전 몇 개 봉**을 20~40으로 줄여 보세요 — 가장 효과가 큽니다.'];
  if (length >= 60) {
    advice[0] = `**직전 몇 개 봉**이 ${length}개입니다. 20~40으로 줄여 보세요 — `
      + '긴 모양이 과거에 그대로 반복될 일은 드물어서, 유사도를 낮추는 것보다 '
      + '이쪽이 훨씬 크게 듣습니다.';
  }
  advice.push('그래도 모자라면 **얼마나 과거까지**를 늘려 더 긴 과거에서 찾으세요.');
  return [found, ...advice];
}

/**
 * 지금 살지 말지.
 *
 * 확률만 보여주기로 했지만, 사용자는 결국 "그래서 사?"를 묻는다.
 * 답하되 근거를 함께 낸다. 사려면 **넷을 모두** 넘겨야 한다.
 *
 *   1. 표본이 충분할 것
 *   2. 불확실 범위가 '평소'를 넘을 것 (우연과 구분될 것)
 *   3. 수수료까지 넘길 확률이 평소보다 높을 것
 *   4. **중앙값 수익이 왕복 비용보다 클 것**
 *
 * 4번이 없어서 실제로 사고가 났다. 확률 셋을 다 통과한 조합이
 * "살 만합니다"로 나갔는데, 그 조합의 중앙값 수익은 +0.036%였고 왕복
 * 비용은 0.140%였다. 화면 오른쪽 '넣었다면' 칸에는 **−1,036원**이 찍혀
 * 있었다. 매수를 권하면서 그 옆에 손실을 적어 둔 셈이다.
 *
 * 확률이 높은 것과 돈이 되는 것은 다른 문제다. 오를 확률 60%여도 오를
 * 때 조금 오르고 내릴 때 많이 내리면 잃는다. 그래서 마지막 관문은
 * 확률이 아니라 **금액**이어야 한다.
 */
export function verdict(rows, cost, stake = DEFAULT_STAKE) {
  const usable = rows.filter((r) => r.samples >= MIN_SAMPLES);
  if (usable.length === 0) {
    return { buy: false, headline: '판단할 수 없습니다', reasons: whyNothingMatched(rows) };
  }

  const winners = usable.filter(
    (r) => r.tellsUsAnything && r.upEdge > 0 && r.beatRate > r.baseBeat
      && r.medianReturn > cost,
  );
  if (winners.length === 0) {
    // 확률 관문을 넘고 돈에서만 걸린 조합이 있으면 그걸 보여준다.
    // 사용자가 표에서 가장 좋아 보인다고 느낄 줄이 바로 그 줄이다.
    const close = usable.filter(
      (r) => r.tellsUsAnything && r.upEdge > 0 && r.beatRate > r.baseBeat,
    );
    const pool = close.length ? close : usable;
    const best = pool.reduce((a, b) => (b.medianReturn - cost > a.medianReturn - cost ? b : a));
    const [low, high] = best.interval;
    const reasons = [
      `가장 나은 조합은 ${timeframeLabel(best.timeframe)} ${best.minutes}분 뒤로, `
      + `올라 있을 확률 ${pct(best.upRate)}입니다 (평소 ${pct(best.baseUp)}, `
      + `닮은 과거 ${best.samples}개 기준).`,
    ];
    // 실제로 걸린 관문만 말한다. 통과한 조건까지 실패로 적으면 거짓말이 된다.
    if (!best.tellsUsAnything) {
      reasons.push(
        `불확실 범위가 ${pct(low)}~${pct(high)}로 평소(${pct(best.baseUp)})를 품고 있습니다 — `
        + `표본 ${best.samples}개로는 이 차이가 우연인지 알 수 없습니다.`,
      );
    }
    if (best.upEdge <= 0) reasons.push('평소보다 높지도 않습니다.');
    if (best.beatRate <= best.baseBeat) {
      reasons.push(
        `수수료까지 넘긴 경우가 ${pct(best.beatRate)}로 평소 ${pct(best.baseBeat)}보다 낮습니다.`,
      );
    }
    if (best.medianReturn <= cost) {
      // 이게 대개 마지막까지 남는 이유다. 금액으로 적어야 와닿는다.
      const loss = (best.medianReturn - cost) * stake;
      reasons.push(
        '확률이 평소보다 높아도 **돈이 되지는 않습니다** — 중앙값 수익 '
        + `${pctSigned(best.medianReturn, 3)}가 왕복 비용 ${pct(cost, 3)}를 못 넘깁니다. `
        + `${won(stake)}원이면 ${wonSigned(loss)}원입니다.`,
      );
    } else if (best.tellsUsAnything && best.beatRate > best.baseBeat) {
      reasons.push('다른 조합들이 기준을 넘지 못했습니다.');
    }
    reasons.push('근거가 기준을 넘길 때까지는 들어가지 않는 것이 기본값입니다.');
    return { buy: false, headline: '사지 마세요 — 근거가 없습니다', reasons };
  }

  const top = winners.reduce((a, b) => (b.beatRate - b.baseBeat > a.beatRate - a.baseBeat ? b : a));
  const [low, high] = top.interval;
  return {
    buy: true,
    headline: `살 만합니다 — ${timeframeLabel(top.timeframe)} ${top.minutes}분 뒤 기준`,
    reasons: [
      `닮은 과거 ${top.samples}개 중 ${top.up}개가 올랐습니다 `
      + `(${pct(top.upRate)}, 평소 ${pct(top.baseUp)}).`,
      `불확실 범위 ${pct(low)}~${pct(high)}가 평소를 넘습니다 — 우연으로 보기 어렵습니다.`,
      `왕복 비용 ${pct(cost, 2)}까지 넘긴 경우가 ${pct(top.beatRate)}로, `
      + `평소 ${pct(top.baseBeat)}보다 높습니다.`,
      `중앙값 수익 ${pctSigned(top.medianReturn, 3)}가 왕복 비용을 넘습니다 — `
      + `${won(stake)}원이면 ${wonSigned((top.medianReturn - cost) * stake)}원입니다.`,
      `같은 기준을 통과한 조합이 ${winners.length}개입니다.`,
    ],
  };
}

// ---------------------------------------------------------------- 직렬화
const finite = (value) => (Number.isFinite(value) ? value : null);
const round = (value, digits) => {
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
};

export function oddsJson(row) {
  const [low, high] = row.interval;
  return {
    timeframe: row.timeframe,
    timeframeLabel: timeframeLabel(row.timeframe),
    length: row.length,
    horizon: row.horizon,
    minutes: row.minutes,
    samples: row.samples,
    up: row.up,
    beatCost: row.beatCost,
    upRate: row.upRate,
    beatRate: row.beatRate,
    baseUp: row.baseUp,
    baseBeat: row.baseBeat,
    upEdge: row.upEdge,
    beatEdge: row.beatEdge,
    ciLow: low,
    ciHigh: high,
    tellsUsAnything: row.tellsUsAnything,
    minSimilarity: finite(row.minSimilarity),
    linearity: row.queryLinearity,
    medianReturn: row.medianReturn,
    best: row.best,
    worst: row.worst,
  };
}

function scoreJson(mark) {
  if (!mark || mark.calls === 0) return null;
  return {
    calls: mark.calls,
    rate: mark.rate,
    base: mark.base,
    edge: mark.edge,
    beatRate: mark.beatRate,
    enough: mark.enough,
    worthBelieving: mark.worthBelieving,
  };
}

function levelJson(one) {
  return {
    price: one.price,
    touches: one.touches,
    lastTouch: one.lastTouch,
    kind: one.kind,
    strength: round(one.strength, 3),
  };
}

/** 마지막 봉 몇 개를 그대로. 종가만 주면 꼬리가 사라져 밋밋해진다. */
function recentCandles(series, count = RECENT_BARS) {
  const take = Math.min(count, series.length);
  if (take <= 0) return [];
  const base = series.close[series.length - 1];
  if (base <= 0) return [];
  const out = [];
  for (let i = series.length - take; i < series.length; i += 1) {
    out.push({
      o: round(series.open[i] / base - 1, 6),
      h: round(series.high[i] / base - 1, 6),
      l: round(series.low[i] / base - 1, 6),
      c: round(series.close[i] / base - 1, 6),
    });
  }
  return out;
}

function projectionJson(forward, series) {
  const six = (values) => values.map((v) => round(v, 6));
  return {
    recent: series ? recentCandles(series) : [],
    walks: forward.walks.map(six),
    timeframe: forward.timeframe,
    label: forward.label,
    samples: forward.samples,
    minutes: forward.minutes,
    priceNow: forward.priceNow,
    median: six(forward.median),
    low: six(forward.low),
    high: six(forward.high),
    worst: six(forward.worst),
    best: six(forward.best),
    spread: forward.spread,
  };
}

/**
 * 이론들이 지금 뭐라고 하는지, 그리고 **과거에 맞았는지**.
 *
 * 둘을 반드시 함께 내보낸다. 앞의 것만 보내면 이 도구는 점집이 된다.
 */
function theoryJson(analysis) {
  const out = {};
  for (const [timeframe, readings] of Object.entries(analysis.readings)) {
    const [ups, downs, flats] = tally(readings);
    const marks = new Map((analysis.scores[timeframe] ?? []).map((s) => [s.theory, s]));
    const bars = analysis.series[timeframe]?.length ?? 0;
    out[timeframe] = {
      label: timeframeLabel(timeframe),
      up: ups,
      down: downs,
      flat: flats,
      // 채점을 **아예 못 한** 것과 '이 이론이 방향을 말한 적이 없다'는
      // 전혀 다른 이야기다. 둘을 같은 문구로 보여주면, 봉이 201개뿐이라
      // 채점이 통째로 불가능한 상황에서도 열한 줄 모두가 "이 이론은
      // 방향을 말한 적이 없습니다"로 나온다 — 이론 탓처럼 읽힌다.
      scoring: { ran: bars >= SCORE_NEEDS, have: bars, need: SCORE_NEEDS },
      readings: readings.map((r) => ({
        theory: r.theory,
        says: r.says,
        detail: r.detail,
        clarity: round(r.clarity, 2),
        past: scoreJson(marks.get(r.theory)),
      })),
    };
  }
  // 다우의 상호 확인 — 봉 간격끼리 같은 말을 하는지
  const dows = {};
  for (const [timeframe, one] of Object.entries(analysis.series)) dows[timeframe] = dow(one);
  const agreed = dowConfirmation(dows);
  out.confirmation = { says: agreed.says, detail: agreed.detail };
  return out;
}

export function analysisJson(analysis) {
  const spans = [];
  for (const [timeframe, series] of Object.entries(analysis.series)) {
    const n = series.length;
    spans.push({
      timeframe,
      label: timeframeLabel(timeframe),
      count: n,
      gaps: series.gaps(),
      // 지금 값. 예전에는 화면이 이걸 '예상 그림'에서 꺼내 썼는데, 닮은
      // 과거를 못 찾으면 그림이 없어서 지지·저항선을 위아래로 가를 수가
      // 없었다. 값은 그림과 상관없이 늘 있다.
      priceNow: n ? series.close[n - 1] : null,
      from: n ? series.isoAt(0) : null,
      to: n ? series.isoAt(n - 1) : null,
    });
  }
  const mapValues = (source, fn) => Object.fromEntries(
    Object.entries(source).map(([key, value]) => [key, fn(value, key)]),
  );
  return {
    market: analysis.market,
    cost: analysis.cost,
    similarity: analysis.similarity,
    oddsLength: analysis.length,
    minSamples: MIN_SAMPLES,
    series: spans,
    missing: analysis.missing.map((tf) => ({ timeframe: tf, label: timeframeLabel(tf) })),
    odds: analysis.odds.map(oddsJson),
    theories: theoryJson(analysis),
    levels: mapValues(analysis.levels, (found) => found.map(levelJson)),
    fibonacci: mapValues(analysis.fibonacci, (found) => found.map(levelJson)),
    projection: mapValues(
      analysis.projection, (p, tf) => projectionJson(p, analysis.series[tf]),
    ),
    verdict: verdict(analysis.odds, analysis.cost, analysis.stake ?? DEFAULT_STAKE),
    updatedAt: analysis.updatedAt,
  };
}

/**
 * 가장 닮은 과거 사례를, 올랐던 쪽과 떨어졌던 쪽에서 각각 몇 개.
 *
 * 확률 숫자만 보면 '정말 닮았나'를 확인할 방법이 없다. 실제 사례를
 * 겹쳐 보여줘서 사용자가 직접 판단하게 한다.
 */
export function examplesJson(analysis, timeframe, horizon, count = 3) {
  const series = analysis.series[timeframe];
  if (!series) throw new Error(`${timeframe} 시세가 없습니다`);

  const matches = analysis.matches[timeframe];
  if (!matches) {
    // 칸을 다 채워 돌려준다. 예전 파이썬 판은 여기서 절반만 채웠는데,
    // 그러면 화면이 "undefined · 직전 undefined개 · 왕복 비용 NaN%"를
    // 그대로 찍는다. 지금은 닿을 수 없는 갈래지만, 닿았을 때 화면이
    // 깨져 보이면 어디가 문제인지 알아볼 수가 없다.
    return {
      timeframe,
      timeframeLabel: timeframeLabel(timeframe),
      length: analysis.length,
      horizon,
      cost: analysis.cost,
      query: [],
      queryAt: series.length ? series.kstAt(series.length - 1) : '',
      rose: [],
      fell: [],
    };
  }
  const { rose, fell } = examplesFor(series, matches, horizon, { cost: analysis.cost, count });
  const pack = (example) => ({
    at: example.at,
    similarity: round(example.similarity, 4),
    outcome: example.outcome,
    shape: example.shape.map((v) => round(v, 4)),
    after: example.after.map((v) => round(v, 6)),
  });
  return {
    timeframe,
    timeframeLabel: timeframeLabel(timeframe),
    length: analysis.length,
    horizon,
    cost: analysis.cost,
    query: Array.from(normalizeWindow(matches.query), (v) => round(v, 4)),
    queryAt: series.kstAt(series.length - 1),
    rose: rose.map(pack),
    fell: fell.map(pack),
  };
}

/**
 * 가진 시세로 한 번 계산한다.
 *
 * `onStep(message, done, total)`으로 진행 상황을 알린다 — 8년치는 몇 초가
 * 걸리고, 아무 말이 없으면 멈춘 것과 구분이 안 된다.
 */
export function analyse(market, seriesByTimeframe, options = {}) {
  const {
    similarity = 0.85, fee = 0.0005, slippage = 0.0002, length = 20, onStep = () => {},
    // 이론 성적을 몇 시점에서 재는지. 정답지 대조에서만 크게 준다 —
    // 그래야 파이썬 쪽이 시점을 무작위로 추려내지 않아 같은 것을 본다.
    points = 300,
    updatedAt = undefined,
    // 넣을 금액. 계산에는 안 쓰이고 **판정 문구에만** 들어간다 —
    // 확률은 금액과 무관하지만, "얼마 번다"는 말은 금액이 있어야 한다.
    stake: rawStake = DEFAULT_STAKE,
  } = options;
  // 0이나 빈 칸이 들어와도 "0원이면 +0원입니다"라고 말하지 않게 한다.
  const stake = Number.isFinite(rawStake) && rawStake > 0 ? rawStake : DEFAULT_STAKE;

  const series = {};
  for (const [timeframe, one] of Object.entries(seriesByTimeframe)) {
    if (one.length) series[timeframe] = one;
  }
  if (Object.keys(series).length === 0) {
    throw new Error("시세가 없습니다. 먼저 '시세 받기'를 눌러 주세요.");
  }

  const cost = roundTripCost(fee, slippage);
  const total = Object.keys(series).length;
  const odds = [];
  const matches = {};
  const readings = {};
  const scores = {};
  const lines = {};
  const fibs = {};
  const projection = {};

  let done = 0;
  for (const one of Object.values(series)) {
    const label = timeframeLabel(one.timeframe);
    onStep('닮은 과거를 찾는 중…', done, total);
    // 찾기를 한 번만 한다. 파이썬 서버는 oddsFor와 findMatches에서 두 번
    // 찾고 있었는데, 브라우저에서는 그 한 번이 그대로 체감 시간이 된다.
    const found = findMatches(one, length, {
      maxHorizon: Math.max(...HORIZONS), similarity, topK: 100,
    });
    odds.push(...oddsFor(one, length, {
      horizons: HORIZONS, similarity, topK: 100, fee, slippage, matches: found,
    }));
    if (found !== null) {
      matches[one.timeframe] = found;
      const forward = project(one, found, Math.max(...HORIZONS));
      if (forward !== null) projection[one.timeframe] = forward;
    }

    onStep(`${label} 차트 이론 보는 중…`, done, total);
    readings[one.timeframe] = readAll(one);
    lines[one.timeframe] = levels(one);
    fibs[one.timeframe] = retracements(one);
    // 이론이 과거에 맞았는지도 **사용자 데이터로 직접** 센다.
    scores[one.timeframe] = score(one, { horizon: SCORE_HORIZON, points, cost });
    done += 1;
    onStep(`${label} 완료`, done, total);
  }

  const now = new Date(Date.now() + 9 * 3600 * 1000);
  const pad = (v) => String(v).padStart(2, '0');
  return {
    market,
    cost,
    stake,
    similarity,
    length,
    series,
    odds,
    matches,
    readings,
    scores,
    levels: lines,
    fibonacci: fibs,
    projection,
    missing: Object.keys(DEFAULT_COUNT).filter((tf) => !(tf in series)),
    updatedAt: updatedAt
      ?? `${pad(now.getUTCHours())}:${pad(now.getUTCMinutes())}:${pad(now.getUTCSeconds())}`,
  };
}
