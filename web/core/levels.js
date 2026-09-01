// 지지선과 저항선, 그리고 그걸 찾는 데 쓰는 변곡점. patternscan/levels.py를 옮겼다.
//
// **무엇을 세는가.** 가격이 여러 번 되돌아선 자리는 다음에도 되돌아설 확률이
// 조금 높다 — 거기서 사고팔려던 사람들의 주문이 남아 있기 때문이라는 게
// 흔한 설명이다. 그게 맞든 아니든, "여러 번 되돌아섰던 자리"는 **셀 수 있는
// 사실**이다. 이 파일은 그 사실만 센다. 왜 그런지는 말하지 않는다.
//
// **두 가지를 조심한다.**
//
// 1. 변곡점을 너무 촘촘히 잡으면 잡음의 톱니 하나하나가 '지지선'이 된다.
//    좌우 SWING개보다 확실히 높거나 낮은 자리만 변곡점으로 본다.
// 2. 가까운 값끼리 묶지 않으면 같은 자리가 선 열 개로 나온다. 값이
//    **변동성 대비** 가까우면 한 자리로 묶는다 — 비트코인 1억에서 10만원과
//    엑스알피 3천원에서 10만원은 전혀 다른 거리다.

/** 변곡점: 좌우 이만큼보다 높아야(낮아야) 꼭짓점으로 인정한다. */
export const SWING = 5;

/** 같은 자리로 묶을 거리. 변동폭(ATR) 대비 비율이다. */
export const CLUSTER = 0.6;

/** 이 정도는 닿아야 '선'으로 쳐 준다. 한 번 스친 자리는 선이 아니다. */
export const MIN_TOUCHES = 2;

/** 지금 값에서 이보다 멀면 안 보여준다. 단타에 5% 밖의 선은 소용없다. */
export const FAR = 0.05;

/**
 * 얼마나 최근까지만 볼지. 8년치를 받아뒀다고 2018년의 지지선이 오늘
 * 20분짜리 거래에 쓸모 있을 리 없다. 게다가 420만 봉을 훑으면 봉 간격마다
 * 몇 초씩 잡아먹는다. 1분봉이면 사흘 반, 5분봉이면 보름 남짓이다.
 */
export const RECENT = 5000;

/** 가격이 여러 번 되돌아섰던 자리. */
export class Level {
  constructor(price, touches, lastTouch, kind) {
    this.price = price;
    this.touches = touches;
    /** 마지막으로 닿은 게 몇 봉 전인지. 오래된 선일수록 약하다고 본다. */
    this.lastTouch = lastTouch;
    this.kind = kind; // "지지" | "저항"
  }

  /** 닿은 횟수와 최근성을 함께 본 점수. 비교용이지 확률이 아니다. */
  get strength() {
    return this.touches * (1 / (1 + this.lastTouch / 500));
  }
}

/** 최근 구간만 잘라 준다. 오래된 자리는 지금 거래에 쓸모가 없다. */
export function recent(series, window = RECENT) {
  return series.length <= window ? series : series.slice(series.length - window, series.length);
}

/**
 * 평균 진폭. '가깝다'를 종목·시기와 무관하게 재기 위한 자다.
 *
 * 비트코인 1억에서의 10만원과 엑스알피 3천원에서의 10만원은 전혀 다른
 * 거리다. 퍼센트로 재도 되지만, 조용한 장과 요동치는 장의 1%도 다르다.
 */
export function atr(series, window = 100) {
  const n = series.length;
  if (n < 2) return 0;
  const from = Math.max(0, n - window);
  const { high, low, close } = series;
  let sum = 0;
  let count = 0;
  for (let i = from + 1; i < n; i += 1) {
    const span = Math.max(
      high[i] - low[i],
      Math.max(Math.abs(high[i] - close[i - 1]), Math.abs(low[i] - close[i - 1])),
    );
    sum += span;
    count += 1;
  }
  return count ? sum / count : 0;
}

/**
 * {highs, lows} — 좌우 `reach`개보다 확실히 높거나 낮은 자리.
 *
 * 양옆을 다 보므로 마지막 `reach`개에서는 변곡점이 안 나온다 — 아직
 * 오른쪽이 안 그려졌기 때문이다. 그게 맞다. 지금 값이 꼭짓점인지는
 * 나중에야 알 수 있고, 미리 아는 척하면 그게 미래를 보는 것이다.
 */
export function swings(series, reach = SWING) {
  const n = series.length;
  if (n < 2 * reach + 1) return { highs: [], lows: [] };

  const { high, low } = series;
  const highs = [];
  const lows = [];
  for (let i = reach; i < n - reach; i += 1) {
    let isHigh = true;
    let isLow = true;
    for (let step = 1; step <= reach; step += 1) {
      if (isHigh && !(high[i] >= high[i - step] && high[i] >= high[i + step])) isHigh = false;
      if (isLow && !(low[i] <= low[i - step] && low[i] <= low[i + step])) isLow = false;
      if (!isHigh && !isLow) break;
    }
    if (isHigh) highs.push(i);
    if (isLow) lows.push(i);
  }
  return { highs, lows };
}

/** 가까운 값끼리 한 자리로 묶는다. */
function cluster(prices, ages, tolerance, kind) {
  if (prices.length === 0 || tolerance <= 0) return [];
  const order = prices.map((price, i) => ({ price, age: ages[i] }));
  order.sort((a, b) => a.price - b.price);

  const out = [];
  let start = 0;
  for (let i = 1; i <= order.length; i += 1) {
    if (i < order.length && order[i].price - order[start].price <= tolerance) continue;
    let sum = 0;
    let youngest = Infinity;
    for (let j = start; j < i; j += 1) {
      sum += order[j].price;
      if (order[j].age < youngest) youngest = order[j].age;
    }
    out.push(new Level(sum / (i - start), i - start, youngest, kind));
    start = i;
  }
  return out;
}

/**
 * 지금 값 위아래로 가장 그럴듯한 선 몇 개씩.
 *
 * 위는 저항, 아래는 지지다. 지금 값을 이미 뚫은 선은 반대쪽이 되므로
 * (뚫린 저항은 지지가 된다는 흔한 이야기) 방향은 **지금 값 기준으로**
 * 다시 매긴다.
 */
export function levels(series, reach = SWING, most = 3) {
  const view = recent(series);
  if (view.length < 2 * reach + 2) return [];
  const span = atr(view);
  if (span <= 0) return [];

  const { highs, lows } = swings(view, reach);
  if (highs.length === 0 && lows.length === 0) return [];

  const n = view.length;
  const now = view.close[n - 1];
  const tolerance = span * CLUSTER;

  const found = [
    ...cluster(highs.map((i) => view.high[i]), highs.map((i) => n - 1 - i), tolerance, '저항'),
    ...cluster(lows.map((i) => view.low[i]), lows.map((i) => n - 1 - i), tolerance, '지지'),
  ];

  // 방향은 지금 값이 정한다. 뚫린 저항은 더 이상 저항이 아니다.
  const kept = [];
  for (const one of found) {
    if (one.touches < MIN_TOUCHES) continue;
    if (Math.abs(one.price - now) / now > FAR) continue;
    kept.push(new Level(one.price, one.touches, one.lastTouch, one.price > now ? '저항' : '지지'));
  }

  const byStrength = (a, b) => b.strength - a.strength;
  const above = kept.filter((x) => x.kind === '저항').sort((a, b) => a.price - b.price);
  const below = kept.filter((x) => x.kind === '지지').sort((a, b) => b.price - a.price);
  // 가까운 것부터 고르되, 그중 힘센 것을 남긴다.
  const pickedAbove = above.slice(0, most * 2).sort(byStrength).slice(0, most);
  const pickedBelow = below.slice(0, most * 2).sort(byStrength).slice(0, most);
  return [...pickedAbove, ...pickedBelow].sort((a, b) => b.price - a.price);
}

/**
 * 피보나치 되돌림 비율. 왜 하필 이 숫자냐는 근거는 약하지만, 많은 사람이
 * 이 자리를 보고 주문을 걸기 때문에 실제로 자주 멈춘다 — 자기실현적이다.
 */
export const FIBONACCI = [0.236, 0.382, 0.5, 0.618, 0.786];

/**
 * 마지막 큰 파동의 피보나치 되돌림 자리.
 *
 * 되돌림은 '닿은 횟수'가 없다. 그래서 touches를 0으로 두고, 지지·저항과
 * 섞이지 않게 화면에서 따로 표시한다.
 */
export function retracements(series, reach = SWING) {
  const view = recent(series);
  if (view.length < 2 * reach + 2) return [];
  const { highs, lows } = swings(view, reach);
  if (highs.length === 0 || lows.length === 0) return [];

  const topAt = highs[highs.length - 1];
  const bottomAt = lows[lows.length - 1];
  const top = view.high[topAt];
  const bottom = view.low[bottomAt];
  if (top <= bottom) return [];

  const now = view.close[view.length - 1];
  const rising = bottomAt < topAt; // 저점 뒤에 고점이면 올라온 파동
  const out = [];
  for (const ratio of FIBONACCI) {
    const price = rising ? top - (top - bottom) * ratio : bottom + (top - bottom) * ratio;
    if (Math.abs(price - now) / now > FAR) continue;
    out.push(new Level(price, 0, 0, price > now ? '저항' : '지지'));
  }
  return out;
}
