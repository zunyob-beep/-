// 차트 이론들을 실제로 계산해 본다. patternscan/theories.py를 옮긴 것이다.
//
// 다우 이론, 엘리어트 파동, 캔들 패턴, 이동평균, RSI, MACD, 볼린저 밴드,
// 머리어깨형, 이중천장/바닥, 삼각수렴, 거래량 확인.
//
// **여기서 지키는 태도.** 이 이론들은 대부분 실증 근거가 약하다. 특히
// 엘리어트 파동은 같은 차트를 놓고 사람마다 다르게 세고, 나중에 보면 언제나
// 맞아떨어지게 다시 셀 수 있다 — 그건 맞히는 게 아니라 설명하는 것이다.
//
// 그래서 이 파일은 **판단을 팔지 않는다.** 각 이론이 지금 무엇을 가리키는지
// 계산해 주고, 끝이다. 그 신호가 실제로 맞았는지는 score()가 **사용자의
// 데이터로 직접 세어** 답한다. "엘리어트가 3파라고 합니다"가 아니라
// "엘리어트식 셈이 상승을 가리킨 47번 중 23번(49%) 올랐습니다"가 이 도구의
// 말투다. 후자만이 검증 가능하다.

import { SWING, atr, recent, swings } from './levels.js';
import { mean } from './shape.js';

/** 신호가 가리키는 방향. */
export const UP = '상승';
export const DOWN = '하락';
export const FLAT = '중립';

/** 이론 하나가 지금 시점에 내놓은 읽기. */
export class Reading {
  constructor(theory, says, detail, clarity = 1) {
    this.theory = theory;
    this.says = says;
    this.detail = detail;
    /** 규칙이 얼마나 또렷하게 맞았는지 (0~1). 확률이 아니라 '선명도'다. */
    this.clarity = clarity;
  }

  get directional() {
    return this.says === UP || this.says === DOWN;
  }
}

const won = (value) => Math.round(value).toLocaleString('ko-KR');

// ------------------------------------------------------------------ 다우 이론
/**
 * 고점과 저점이 함께 높아지면 상승 추세, 함께 낮아지면 하락 추세.
 *
 * 다우가 실제로 말한 것 중 계산할 수 있는 건 이게 거의 전부다. 나머지
 * (시장은 모든 것을 반영한다 같은 말)는 셀 수가 없다.
 */
export function dow(series, reach = SWING) {
  const { highs, lows } = swings(series, reach);
  if (highs.length < 2 || lows.length < 2) {
    return new Reading('다우 이론', FLAT, '꼭짓점이 모자라 추세를 못 봅니다', 0);
  }
  const highUp = series.high[highs[highs.length - 1]] > series.high[highs[highs.length - 2]];
  const lowUp = series.low[lows[lows.length - 1]] > series.low[lows[lows.length - 2]];

  if (highUp && lowUp) {
    return new Reading('다우 이론', UP, '고점과 저점이 함께 높아지고 있습니다 (상승 추세)');
  }
  if (!highUp && !lowUp) {
    return new Reading('다우 이론', DOWN, '고점과 저점이 함께 낮아지고 있습니다 (하락 추세)');
  }
  return new Reading(
    '다우 이론', FLAT,
    '고점과 저점이 서로 다른 방향입니다 — 추세가 아니라 눌림/반등 구간', 0.5,
  );
}
dow.pyName = 'dow';

/**
 * 다우의 '상호 확인'. 여러 지표가 같은 말을 해야 믿는다.
 *
 * 원래는 산업평균과 운송평균이 서로를 확인해야 한다는 이야기였다.
 * 여기서는 1·3·5분봉이 서로를 확인하는지로 옮겼다.
 */
export function dowConfirmation(byTimeframe) {
  const readings = Object.values(byTimeframe);
  const votes = readings.filter((r) => r.directional).map((r) => r.says);
  if (votes.length === 0) {
    return new Reading('상호 확인', FLAT, '방향을 말하는 봉 간격이 없습니다', 0);
  }
  const counts = new Map();
  for (const vote of votes) counts.set(vote, (counts.get(vote) ?? 0) + 1);
  // 파이썬의 max(set(votes), key=votes.count)와 같게: 동수면 먼저 나온 쪽.
  let agree = votes[0];
  let same = counts.get(agree);
  for (const [vote, count] of counts) {
    if (count > same) {
      agree = vote;
      same = count;
    }
  }
  if (same === readings.length && same > 1) {
    return new Reading('상호 확인', agree, `${same}개 봉 간격이 모두 ${agree}을 가리킵니다`);
  }
  return new Reading(
    '상호 확인', FLAT,
    `봉 간격끼리 엇갈립니다 (${same}/${readings.length}만 ${agree})`
    + ' — 다우는 이때 믿지 말라고 합니다',
    same / Math.max(1, readings.length),
  );
}

// --------------------------------------------------------------- 엘리어트 파동
/**
 * 마지막 다섯 꼭짓점이 충격파의 **어길 수 없는 규칙**에 맞는지만 본다.
 *
 * 엘리어트 셈은 사람마다 다르고, 지나고 나면 언제나 맞게 다시 셀 수
 * 있다. 그러니 '몇 파인지'를 단정하지 않는다. 대신 반박 가능한 세
 * 규칙만 확인한다.
 *
 *   · 2파는 1파의 시작을 되돌리지 않는다
 *   · 3파는 1·3·5파 중 가장 짧지 않다
 *   · 4파는 1파의 영역을 침범하지 않는다
 *
 * 셋 다 맞으면 "충격파 모양과 어긋나지 않는다"까지만 말한다. 그게
 * 정직하게 말할 수 있는 전부다.
 */
export function elliott(series, reach = SWING) {
  const { highs, lows } = swings(series, reach);
  const highSet = new Set(highs);
  const points = [...highs, ...lows].sort((a, b) => a - b);
  if (points.length < 5) {
    return new Reading('엘리어트 파동', FLAT, '꼭짓점이 다섯 개가 안 됩니다', 0);
  }

  const idx = points.slice(-5);
  const price = idx.map((i) => (highSet.has(i) ? series.high[i] : series.low[i]));
  const rising = price[1] > price[0];
  const direction = rising ? 1 : -1;
  // 방향을 위로 뒤집어 놓고 한 벌의 규칙으로 검사한다
  const p = price.map((v) => v * direction);

  if (!(p[1] > p[0] && p[2] < p[1] && p[3] > p[2] && p[4] < p[3])) {
    return new Reading('엘리어트 파동', FLAT, '꼭짓점이 지그재그 모양이 아닙니다', 0);
  }

  const wave1 = p[1] - p[0];
  const wave3 = p[3] - p[2];
  const broke = [];
  if (p[2] <= p[0]) broke.push('2파가 1파 시작을 되돌렸습니다');
  if (wave3 < wave1) broke.push('3파가 1파보다 짧습니다');
  if (p[4] <= p[1]) broke.push('4파가 1파 영역을 침범했습니다');

  const where = rising ? '상승' : '하락';
  if (broke.length) {
    return new Reading(
      '엘리어트 파동', FLAT,
      `${where} 충격파로 보기 어렵습니다 — ${broke[0]}`,
      1 - broke.length / 3,
    );
  }
  return new Reading(
    '엘리어트 파동', rising ? UP : DOWN,
    `${where} 충격파 규칙에 어긋나지 않습니다 (4파로 볼 수 있는 자리, 5파가 남았다면)`,
    0.7, // 규칙을 안 어겼을 뿐 맞다는 뜻이 아니다
  );
}
elliott.pyName = 'elliott';

// ------------------------------------------------------------------ 캔들 패턴
/** 마지막 한두 개 봉의 생김새. 널리 쓰이는 정의를 그대로 옮겼다. */
export function candles(series) {
  const n = series.length;
  if (n < 3) return new Reading('캔들 패턴', FLAT, '봉이 모자랍니다', 0);

  const o = [series.open[n - 3], series.open[n - 2], series.open[n - 1]];
  const h = [series.high[n - 3], series.high[n - 2], series.high[n - 1]];
  const l = [series.low[n - 3], series.low[n - 2], series.low[n - 1]];
  const c = [series.close[n - 3], series.close[n - 2], series.close[n - 1]];

  const body = Math.abs(c[2] - o[2]);
  const span = h[2] - l[2];
  if (span <= 0) return new Reading('캔들 패턴', FLAT, '움직임이 없는 봉입니다', 0);

  const upper = h[2] - Math.max(o[2], c[2]);
  const lower = Math.min(o[2], c[2]) - l[2];
  const prevBody = Math.abs(c[1] - o[1]);
  const upNow = c[2] > o[2];
  const upBefore = c[1] > o[1];

  // 장악형: 앞 봉 몸통을 통째로 덮는다
  if (prevBody > 0 && body > prevBody) {
    if (upNow && !upBefore && c[2] >= o[1] && o[2] <= c[1]) {
      return new Reading('캔들 패턴', UP, '상승 장악형 — 앞의 음봉을 통째로 덮었습니다');
    }
    if (!upNow && upBefore && o[2] >= c[1] && c[2] <= o[1]) {
      return new Reading('캔들 패턴', DOWN, '하락 장악형 — 앞의 양봉을 통째로 덮었습니다');
    }
  }

  // 망치형/교수형: 아래꼬리가 몸통의 두 배 넘고 위꼬리는 짧다
  if (body > 0 && lower > body * 2 && upper < body * 0.5) {
    return new Reading('캔들 패턴', UP, '망치형 — 아래로 밀렸다가 되돌아왔습니다', 0.7);
  }
  if (body > 0 && upper > body * 2 && lower < body * 0.5) {
    return new Reading('캔들 패턴', DOWN, '역망치·유성형 — 위로 밀었다가 되밀렸습니다', 0.7);
  }

  // 도지: 몸통이 전체 폭의 10% 미만
  if (body < span * 0.1) {
    return new Reading('캔들 패턴', FLAT, '도지 — 사려는 쪽과 팔려는 쪽이 팽팽합니다', 0.5);
  }

  // 샛별/저녁별: 큰 봉 → 작은 봉 → 반대쪽 큰 봉
  const small = Math.abs(c[1] - o[1]) < Math.abs(c[0] - o[0]) * 0.4;
  if (small && c[0] < o[0] && upNow && c[2] > (o[0] + c[0]) / 2) {
    return new Reading('캔들 패턴', UP, '샛별형 — 바닥에서 방향이 바뀌는 모양');
  }
  if (small && c[0] > o[0] && !upNow && c[2] < (o[0] + c[0]) / 2) {
    return new Reading('캔들 패턴', DOWN, '저녁별형 — 꼭대기에서 방향이 바뀌는 모양');
  }

  return new Reading('캔들 패턴', FLAT, `특별한 모양 없는 ${upNow ? '양봉' : '음봉'}입니다`, 0);
}
candles.pyName = 'candles';

// ----------------------------------------------------------------- 이동평균
/** 정배열이면 상승, 역배열이면 하락. 짧은 선이 위에 있는지로 본다. */
export function movingAverages(series, spans = [5, 20, 60]) {
  const n = series.length;
  if (n < Math.max(...spans) + 1) {
    return new Reading('이동평균 배열', FLAT, '봉이 모자랍니다', 0);
  }
  const lines = spans.map((span) => mean(series.close, n - span, span));
  if (lines.every((v, i) => i === lines.length - 1 || v > lines[i + 1])) {
    return new Reading('이동평균 배열', UP, `정배열 (${spans.join('>')})`);
  }
  if (lines.every((v, i) => i === lines.length - 1 || v < lines[i + 1])) {
    return new Reading('이동평균 배열', DOWN, `역배열 (${spans.join('<')})`);
  }
  return new Reading('이동평균 배열', FLAT, '이동평균들이 얽혀 있습니다 — 방향이 없습니다', 0.3);
}
movingAverages.pyName = 'moving_averages';

/**
 * 상대강도. 70 위는 과매수, 30 아래는 과매도로 보는 게 관례다.
 *
 * 주의: 과매수는 '곧 떨어진다'가 아니다. 강한 추세에서는 과매수인 채로
 * 한참 더 오른다. 그래서 방향을 **되돌림 쪽**으로 말하되 선명도를 낮게 둔다.
 */
export function rsi(series, window = 14) {
  const n = series.length;
  if (n < window + 1) return new Reading('RSI', FLAT, '봉이 모자랍니다', 0);
  let gain = 0;
  let loss = 0;
  for (let i = n - window; i < n; i += 1) {
    const change = series.close[i] - series.close[i - 1];
    if (change > 0) gain += change;
    else loss += -change;
  }
  gain /= window;
  loss /= window;
  if (gain + loss === 0) return new Reading('RSI', FLAT, '움직임이 없습니다', 0);
  const value = (100 * gain) / (gain + loss);

  if (value >= 70) {
    return new Reading('RSI', DOWN, `RSI ${value.toFixed(0)} — 과매수 구간 (되돌림이 잦은 자리)`, 0.5);
  }
  if (value <= 30) {
    return new Reading('RSI', UP, `RSI ${value.toFixed(0)} — 과매도 구간 (반등이 잦은 자리)`, 0.5);
  }
  return new Reading('RSI', FLAT, `RSI ${value.toFixed(0)} — 치우치지 않았습니다`, 0.2);
}
rsi.pyName = 'rsi';

/** 지수이동평균. 가장 최근 값의 가중치가 1이 되도록 정규화한다. */
function ema(values, from, length, span) {
  const alpha = 2 / (span + 1);
  let weighted = 0;
  let total = 0;
  for (let i = 0; i < length; i += 1) {
    const weight = (1 - alpha) ** (length - 1 - i);
    weighted += values[from + i] * weight;
    total += weight;
  }
  return weighted / total;
}

/** 빠른 이평과 느린 이평의 차이가 신호선 위인지 아래인지. */
export function macd(series, fast = 12, slow = 26, signal = 9) {
  const n = series.length;
  if (n < slow + signal + 1) return new Reading('MACD', FLAT, '봉이 모자랍니다', 0);
  const closes = series.close;
  const line = [];
  for (let k = signal - 1; k >= 0; k -= 1) {
    const end = n - k;                       // closes[:len-k]
    const take = Math.min(slow * 3, end);    // 그중 마지막 slow*3개
    const from = end - take;
    line.push(ema(closes, from, take, fast) - ema(closes, from, take, slow));
  }
  const now = line[line.length - 1];
  const mark = line.reduce((a, b) => a + b, 0) / line.length;
  if (now > mark && now > 0) {
    return new Reading('MACD', UP, 'MACD가 신호선 위에 있습니다 (상승 탄력)');
  }
  if (now < mark && now < 0) {
    return new Reading('MACD', DOWN, 'MACD가 신호선 아래에 있습니다 (하락 탄력)');
  }
  return new Reading('MACD', FLAT, 'MACD가 신호선 근처입니다 — 방향이 갈리는 자리', 0.3);
}
macd.pyName = 'macd';

/** 20봉 평균에서 표준편차 2배 밖으로 나갔는지. */
export function bollinger(series, window = 20, width = 2) {
  const n = series.length;
  if (n < window) return new Reading('볼린저 밴드', FLAT, '봉이 모자랍니다', 0);
  const mid = mean(series.close, n - window, window);
  let sum = 0;
  for (let i = n - window; i < n; i += 1) sum += (series.close[i] - mid) ** 2;
  const spread = Math.sqrt(sum / window);
  if (spread <= 0) return new Reading('볼린저 밴드', FLAT, '변동이 없습니다', 0);
  const z = (series.close[n - 1] - mid) / spread;
  if (z >= width) {
    return new Reading('볼린저 밴드', DOWN, `위 밴드를 벗어났습니다 (${z.toFixed(1)}σ) — 되돌림이 잦습니다`, 0.5);
  }
  if (z <= -width) {
    return new Reading('볼린저 밴드', UP, `아래 밴드를 벗어났습니다 (${z.toFixed(1)}σ) — 반등이 잦습니다`, 0.5);
  }
  const sign = z >= 0 ? '+' : '';
  return new Reading('볼린저 밴드', FLAT, `밴드 안입니다 (${sign}${z.toFixed(1)}σ)`, 0.2);
}
bollinger.pyName = 'bollinger';

// --------------------------------------------------------------- 모양 패턴
/** 머리어깨형: 가운데 봉우리가 양옆보다 높고, 양 어깨는 비슷한 높이. */
export function headAndShoulders(series, reach = SWING) {
  const { highs, lows } = swings(series, reach);
  if (highs.length < 3 || lows.length < 2) {
    return new Reading('머리어깨형', FLAT, '꼭짓점이 모자랍니다', 0);
  }

  const span = atr(series);
  if (span <= 0) return new Reading('머리어깨형', FLAT, '변동폭을 못 잽니다', 0);

  const top = highs.slice(-3).map((i) => series.high[i]);
  if (top[1] > top[0] && top[1] > top[2] && Math.abs(top[0] - top[2]) < span * 1.5) {
    const neck = (series.low[lows[lows.length - 2]] + series.low[lows[lows.length - 1]]) / 2;
    return new Reading(
      '머리어깨형', DOWN,
      `머리어깨형 — 목선 ${won(neck)}원을 깨면 하락으로 보는 모양`, 0.7,
    );
  }

  if (lows.length >= 3) {
    const bottom = lows.slice(-3).map((i) => series.low[i]);
    if (bottom[1] < bottom[0] && bottom[1] < bottom[2]
        && Math.abs(bottom[0] - bottom[2]) < span * 1.5) {
      const neck = (series.high[highs[highs.length - 2]] + series.high[highs[highs.length - 1]]) / 2;
      return new Reading(
        '머리어깨형', UP,
        `역머리어깨형 — 목선 ${won(neck)}원을 넘으면 상승으로 보는 모양`, 0.7,
      );
    }
  }
  return new Reading('머리어깨형', FLAT, '머리어깨 모양이 아닙니다', 0);
}
headAndShoulders.pyName = 'head_and_shoulders';

/** 이중천장/이중바닥: 비슷한 높이를 두 번 찍고 못 넘어선 자리. */
export function doubleTopBottom(series, reach = SWING) {
  const { highs, lows } = swings(series, reach);
  const span = atr(series);
  if (span <= 0) return new Reading('이중천장·바닥', FLAT, '변동폭을 못 잽니다', 0);

  if (highs.length >= 2) {
    const a = series.high[highs[highs.length - 1]];
    const b = series.high[highs[highs.length - 2]];
    if (Math.abs(a - b) < span * 0.8) {
      return new Reading('이중천장·바닥', DOWN, `이중천장 — ${won((a + b) / 2)}원을 두 번 못 넘었습니다`, 0.7);
    }
  }
  if (lows.length >= 2) {
    const a = series.low[lows[lows.length - 1]];
    const b = series.low[lows[lows.length - 2]];
    if (Math.abs(a - b) < span * 0.8) {
      return new Reading('이중천장·바닥', UP, `이중바닥 — ${won((a + b) / 2)}원에서 두 번 버텼습니다`, 0.7);
    }
  }
  return new Reading('이중천장·바닥', FLAT, '두 번 같은 자리를 찍은 모양이 아닙니다', 0);
}
doubleTopBottom.pyName = 'double_top_bottom';

/**
 * 삼각수렴: 고점은 낮아지고 저점은 높아지며 폭이 좁아지는 모양.
 *
 * 방향은 말하지 않는다. 삼각수렴은 어느 쪽으로든 터지는 모양이고,
 * 방향을 아는 척하면 그건 지어내는 것이다.
 */
export function squeeze(series, reach = SWING) {
  const { highs, lows } = swings(series, reach);
  if (highs.length < 2 || lows.length < 2) {
    return new Reading('삼각수렴', FLAT, '꼭짓점이 모자랍니다', 0);
  }
  const narrowing = series.high[highs[highs.length - 1]] < series.high[highs[highs.length - 2]]
    && series.low[lows[lows.length - 1]] > series.low[lows[lows.length - 2]];
  if (narrowing) {
    return new Reading(
      '삼각수렴', FLAT,
      '폭이 좁아지고 있습니다 — 곧 크게 움직이되 방향은 이 모양이 말해주지 않습니다',
      0.6,
    );
  }
  return new Reading('삼각수렴', FLAT, '수렴하는 모양이 아닙니다', 0);
}
squeeze.pyName = 'squeeze';

/** 다우의 거래량 원칙: 추세는 거래량이 받쳐줘야 한다. */
export function volumeConfirms(series, window = 20) {
  const n = series.length;
  if (n < window + 1) return new Reading('거래량 확인', FLAT, '봉이 모자랍니다', 0);
  const fresh = mean(series.volume, n - 3, 3);
  const usual = mean(series.volume, n - window, window);
  if (usual <= 0) return new Reading('거래량 확인', FLAT, '거래량이 없습니다', 0);
  const ratio = fresh / usual;
  const rising = series.close[n - 1] > series.close[n - 4];
  if (ratio > 1.5) {
    return new Reading(
      '거래량 확인', rising ? UP : DOWN,
      `거래량이 평소의 ${ratio.toFixed(1)}배입니다 — 움직임에 힘이 실렸습니다`, 0.6,
    );
  }
  if (ratio < 0.6) {
    return new Reading(
      '거래량 확인', FLAT,
      `거래량이 평소의 ${ratio.toFixed(1)}배뿐입니다 — 지금 움직임은 힘이 약합니다`, 0.5,
    );
  }
  return new Reading('거래량 확인', FLAT, `거래량은 평소 수준입니다 (${ratio.toFixed(1)}배)`, 0.2);
}
volumeConfirms.pyName = 'volume_confirms';

// ------------------------------------------------------------------ 전부 읽기
/** 순서대로 화면에 나온다. 계산할 수 있는 것만 넣었다. */
export const THEORIES = [
  dow,
  elliott,
  candles,
  movingAverages,
  macd,
  rsi,
  bollinger,
  headAndShoulders,
  doubleTopBottom,
  squeeze,
  volumeConfirms,
];

/**
 * 모든 이론을 한 번씩 돌린다.
 *
 * 최근 구간만 넘긴다. 이론들은 어차피 마지막 꼭짓점 몇 개와 짧은 창만
 * 보므로 답이 달라지지 않는데, 8년치를 통째로 넘기면 봉 간격마다 몇 초씩
 * 잡아먹는다.
 */
export function readAll(series) {
  const view = recent(series);
  const out = [];
  for (const theory of THEORIES) {
    try {
      out.push(theory(view));
    } catch {
      // 이론 하나가 이상한 데이터에 걸려도 나머지는 나와야 한다.
      out.push(new Reading(theory.pyName, FLAT, '계산하지 못했습니다', 0));
    }
  }
  return out;
}

/**
 * [상승, 하락, 중립] 개수. **다수결이 곧 답은 아니다.**
 *
 * 이론끼리 독립이 아니다 — 이동평균·MACD·다우는 결국 같은 추세를 세
 * 번 세는 것에 가깝다. 그래서 이 숫자는 '얼마나 한목소리인가'를 보는
 * 용도지, 확률로 바꿔 읽으면 안 된다.
 */
export function tally(readings) {
  const ups = readings.filter((r) => r.says === UP).length;
  const downs = readings.filter((r) => r.says === DOWN).length;
  return [ups, downs, readings.length - ups - downs];
}

// ------------------------------------------------------- 그래서 맞기는 하나
//
// 여기가 이 파일의 핵심이다. 위의 이론들은 "지금 무엇처럼 보이는가"를
// 말할 뿐이고, 그건 검증할 수 없는 말이다. 검증할 수 있는 말은 하나뿐이다 —
// **이 신호가 나왔던 과거에 실제로 무슨 일이 있었나.**
//
// 그걸 사용자의 데이터로 직접 센다. 남의 백테스트를 인용하지 않는다.

/**
 * 한 시점을 판단할 때 볼 과거 길이. 이론들이 보는 최대 창(이동평균 60,
 * MACD 26+9, 꼭짓점 몇 개)을 넉넉히 덮는다. 더 길게 잡아도 답이 안 변한다.
 */
export const LOOKBACK = 400;

/** 이론 하나가 과거에 얼마나 맞았는지. */
export class Score {
  constructor(theory, calls, hits, base, beat = 0) {
    this.theory = theory;
    this.calls = calls;   // 방향을 말한 횟수
    this.hits = hits;     // 그중 맞은 횟수
    this.base = base;     // 아무 때나 찍었으면 맞았을 비율
    /** 수수료까지 넘긴 경우만 센 적중률. 진짜 중요한 건 이쪽이다. */
    this.beat = beat;
  }

  get rate() { return this.calls ? this.hits / this.calls : 0; }

  get beatRate() { return this.calls ? this.beat / this.calls : 0; }

  /** 평소보다 얼마나 나은가. 0 근처면 이 이론은 여기서 아무 말도 안 한 것이다. */
  get edge() { return this.rate - this.base; }

  /** 표본이 이보다 적으면 적중률은 우연과 구분되지 않는다. */
  get enough() { return this.calls >= 30; }

  /** 우연으로 보기 어려운가. 표준오차 두 배를 넘어야 한다. */
  get worthBelieving() {
    if (!this.enough) return false;
    return this.edge > 2 * Math.sqrt(0.25 / this.calls);
  }
}

/**
 * 훑어볼 시점 고르기.
 *
 * 파이썬은 numpy의 난수로 뽑는다. 브라우저에는 같은 난수기가 없으므로
 * **구간을 points개로 나눠 칸마다 하나씩** 고른다. 결과는 더 고르게
 * 흩어지고, 실행할 때마다 같으며, 시드도 필요 없다. 칸 안에서의 자리는
 * 고정 난수(LCG)로 흔들어 준다 — 정확히 등간격으로 찍으면 데이터에
 * 그 주기가 있을 때 엉뚱하게 맞아떨어질 수 있다.
 */
export function samplePoints(from, to, count) {
  const total = to - from;
  if (total <= 0) return [];
  if (total <= count) {
    return Array.from({ length: total }, (_, i) => from + i);
  }
  const out = [];
  // xorshift32. Math.imul을 쓰는 이유는 곱셈이 2^53을 넘으면 자릿수를 잃어
  // 난수가 아니라 그냥 규칙적인 값이 되기 때문이다.
  let seed = 0x9e3779b9;
  for (let k = 0; k < count; k += 1) {
    const lo = from + Math.floor((total * k) / count);
    const hi = from + Math.floor((total * (k + 1)) / count);
    seed ^= seed << 13; seed |= 0;
    seed ^= seed >>> 17;
    seed ^= seed << 5; seed |= 0;
    out.push(lo + ((seed >>> 0) % Math.max(1, hi - lo)));
  }
  return out;
}

/**
 * 과거 여러 시점으로 돌아가 이론별 적중률을 잰다.
 *
 * 각 시점에서 **그 이전 데이터만** 보고 읽은 뒤, 실제로 어떻게 됐는지
 * 맞춰본다. 미래를 보지 않으므로 여기서 나온 숫자는 실제로 그때 얻을
 * 수 있었던 성적이다.
 */
export function score(series, { horizon = 10, points = 300, cost = 0.0014 } = {}) {
  const usable = series.length - horizon;
  if (usable <= LOOKBACK + 1) return [];

  const spots = samplePoints(LOOKBACK, usable, points);
  const closes = series.close;

  let rose = 0;
  const after = spots.map((spot) => closes[spot + horizon] / closes[spot] - 1);
  for (const value of after) if (value > 0) rose += 1;
  const baseUp = after.length ? rose / after.length : 0;

  const tallyBy = new Map();
  for (let offset = 0; offset < spots.length; offset += 1) {
    const spot = spots[offset];
    const window = series.slice(spot - LOOKBACK, spot + 1);
    const wentUp = after[offset] > 0;
    for (const reading of readAll(window)) {
      if (!reading.directional) continue;
      const saidUp = reading.says === UP;
      const correct = saidUp === wentUp;
      const beat = saidUp ? after[offset] > cost : after[offset] < -cost;
      const got = tallyBy.get(reading.theory) ?? [0, 0, 0];
      got[0] += 1;
      got[1] += correct ? 1 : 0;
      got[2] += beat ? 1 : 0;
      tallyBy.set(reading.theory, got);
    }
  }

  const out = [];
  for (const [name, [calls, hits, beat]] of tallyBy) {
    // 비교 대상은 '그 이론이 말한 방향으로 늘 찍었을 때'가 아니라
    // '아무 때나 그 방향으로 찍었을 때'다.
    out.push(new Score(name, calls, hits, Math.max(baseUp, 1 - baseUp), beat));
  }
  return out.sort((a, b) => b.edge - a.edge);
}
