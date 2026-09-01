// 핵심 자료구조. 파이썬 patternscan/models.py를 그대로 옮긴 것이다.
//
// 주문도 계좌도 없다. 시세를 읽고 통계를 내기만 한다.
//
// 파이썬은 numpy 배열을, 여기서는 Float64Array를 쓴다. 둘 다 슬라이스가
// **복사가 아니라 뷰**라서(numpy의 basic slicing, TypedArray.subarray)
// 400만 봉을 잘라 넘겨도 메모리가 늘지 않는다. 이 성질에 기대는 자리가
// 여럿 있으니(theories.score가 시점마다 400봉씩 잘라 쓴다) 복사로 바꾸지 말 것.

export const KST_OFFSET_MINUTES = 9 * 60;

/** 봉 간격 -> 초. 단타만 보므로 여기서 더 늘리지 않는다. */
export const TIMEFRAMES = {
  minute1: 60,
  minute3: 180,
  minute5: 300,
};

export const TIMEFRAME_LABELS = {
  minute1: '1분봉',
  minute3: '3분봉',
  minute5: '5분봉',
};

/**
 * 다루는 종목. 아무거나 칠 수 있게 두면 오타 하나로 "없는 종목입니다"를
 * 만나게 되고, 그게 오타 때문인지 업비트가 막힌 건지 알 수가 없다.
 * 거래량이 많은 넷으로 좁힌다 — 얇은 종목은 빈 봉이 많아 모양이 왜곡된다.
 */
export const MARKETS = {
  'KRW-BTC': '비트코인',
  'KRW-ETH': '이더리움',
  'KRW-XRP': '엑스알피',
  'KRW-SOL': '솔라나',
};

/** 진입 후 몇 봉 뒤를 볼지. 봉 간격 기준이므로 1분봉이면 1/3/5/10/20분 뒤다. */
export const HORIZONS = [1, 3, 5, 10, 20];

export function marketLabel(market) {
  return MARKETS[market] ?? market;
}

export function timeframeLabel(timeframe) {
  return TIMEFRAME_LABELS[timeframe] ?? timeframe;
}

export function timeframeSeconds(timeframe) {
  const seconds = TIMEFRAMES[timeframe];
  if (seconds === undefined) {
    throw new Error(
      `모르는 봉 간격 '${timeframe}'. 사용 가능: ${Object.keys(TIMEFRAMES).join(', ')}`,
    );
  }
  return seconds;
}

/**
 * 한 종목·한 봉 간격의 연속된 봉들.
 *
 * `ts`는 봉이 **열린** 시각(유닉스 초)이다. 초 단위 정수는 2^53 안에 한참
 * 못 미치므로 double에 정확히 담긴다 — 밀리초로 바꾸지 말 것. 밀리초여도
 * 아직은 정확하지만, 굳이 여유를 깎을 이유가 없다.
 */
export class Series {
  constructor(market, timeframe, ts, open, high, low, close, volume) {
    this.market = market;
    this.timeframe = timeframe;
    this.ts = ts;
    this.open = open;
    this.high = high;
    this.low = low;
    this.close = close;
    this.volume = volume;
  }

  get length() {
    return this.close.length;
  }

  /**
   * `{ts, open, high, low, close, volume}` 객체 배열에서 만든다.
   * 시각 순으로 정렬해 둔다 — 뒤의 모든 계산이 순서를 전제한다.
   */
  static fromCandles(market, timeframe, candles) {
    const sorted = [...candles].sort((a, b) => a.ts - b.ts);
    const n = sorted.length;
    const cols = {
      ts: new Float64Array(n),
      open: new Float64Array(n),
      high: new Float64Array(n),
      low: new Float64Array(n),
      close: new Float64Array(n),
      volume: new Float64Array(n),
    };
    for (let i = 0; i < n; i += 1) {
      const c = sorted[i];
      cols.ts[i] = c.ts;
      cols.open[i] = c.open;
      cols.high[i] = c.high;
      cols.low[i] = c.low;
      cols.close[i] = c.close;
      cols.volume[i] = c.volume;
    }
    return new Series(
      market, timeframe, cols.ts, cols.open, cols.high, cols.low, cols.close, cols.volume,
    );
  }

  static empty(market, timeframe) {
    return Series.fromCandles(market, timeframe, []);
  }

  /** 뷰를 돌려준다. 복사하지 않는다. */
  slice(start, stop) {
    return new Series(
      this.market,
      this.timeframe,
      this.ts.subarray(start, stop),
      this.open.subarray(start, stop),
      this.high.subarray(start, stop),
      this.low.subarray(start, stop),
      this.close.subarray(start, stop),
      this.volume.subarray(start, stop),
    );
  }

  /** 그 봉이 열린 시각(밀리초, UTC 기준 Date). */
  timeAt(index) {
    return new Date(this.ts[index] * 1000);
  }

  /**
   * 파이썬 `datetime.isoformat()`과 같은 모양: `2024-03-01T00:00:00+00:00`.
   *
   * 자바스크립트 `toISOString()`은 `...T00:00:00.000Z`로 쓴다. 둘 다
   * 유효한 ISO 8601이고 브라우저는 어느 쪽이든 읽지만, 표기가 갈리면
   * 파이썬 결과와 통짜로 대조할 수가 없다.
   */
  isoAt(index) {
    const at = this.timeAt(index);
    const pad = (v) => String(v).padStart(2, '0');
    return (
      `${at.getUTCFullYear()}-${pad(at.getUTCMonth() + 1)}-${pad(at.getUTCDate())}`
      + `T${pad(at.getUTCHours())}:${pad(at.getUTCMinutes())}:${pad(at.getUTCSeconds())}+00:00`
    );
  }

  /** KST로 "YYYY-MM-DD HH:MM". */
  kstAt(index) {
    const shifted = new Date((this.ts[index] + KST_OFFSET_MINUTES * 60) * 1000);
    const pad = (v) => String(v).padStart(2, '0');
    return (
      `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())}`
      + ` ${pad(shifted.getUTCHours())}:${pad(shifted.getUTCMinutes())}`
    );
  }

  /**
   * 봉이 비어 있는 구간 수.
   *
   * 업비트는 거래가 없는 분의 봉을 아예 주지 않는다. 그 자리를 이어붙여
   * 비교하면 실제로는 떨어져 있는 두 시점을 연속된 모양으로 착각한다.
   */
  gaps() {
    if (this.length < 2) return 0;
    const step = timeframeSeconds(this.timeframe);
    let found = 0;
    for (let i = 1; i < this.ts.length; i += 1) {
      if (this.ts[i] - this.ts[i - 1] !== step) found += 1;
    }
    return found;
  }
}

/**
 * 1분봉을 묶어 3분봉·5분봉을 만든다.
 *
 * **왜 이걸 하는가 — 요청 수를 줄이려고.**
 *
 * 지금까지는 1·3·5분봉을 각각 따로 받았다. 30일치면 216 + 72 + 43 = 331번을
 * 부른다. 그런데 업비트의 3분봉은 1분봉 세 개를 묶은 것과 **정확히 같다.**
 * 이미 받은 걸로 만들 수 있는 것을 다시 받고 있었던 셈이다.
 *
 * 1분봉만 받고 나머지를 여기서 만들면 216번으로 끝난다 — 35% 적다. 그리고
 * 업비트가 우리를 막는 이유가 요청이 잦아서이므로, 이게 가장 큰 지렛대다.
 *
 * 묶는 방법은 봉의 정의 그대로다. 시가는 첫 봉의 시가, 종가는 마지막 봉의
 * 종가, 고가·저가는 그 구간의 최고·최저, 거래량은 합.
 *
 * 경계는 시각으로 자른다(업비트와 같다 — 3분봉은 :00, :03, :06…에서 시작).
 * **맨 앞의 잘린 묶음은 버린다.** 1분봉이 구간 중간부터 시작했다면 그
 * 묶음에는 앞 몇 분이 빠져 있어서 시가와 고·저가가 틀리기 때문이다.
 * 맨 뒤는 남긴다 — 지금 만들어지는 중인 봉이고, 업비트가 주는 것도 그렇다.
 */
export function aggregate(series, factor) {
  const span = 60 * factor;
  const n = series.length;
  if (!n || factor <= 1) return series;

  // 앞의 잘린 묶음을 건너뛴다.
  let from = 0;
  while (from < n && series.ts[from] % span !== 0) from += 1;
  const usable = n - from;
  if (usable <= 0) {
    return new Series(series.market, `minute${factor}`, ...Array.from(
      { length: 6 }, () => new Float64Array(0),
    ));
  }

  const groups = Math.ceil(usable / factor);
  const cols = {
    ts: new Float64Array(groups),
    open: new Float64Array(groups),
    high: new Float64Array(groups),
    low: new Float64Array(groups),
    close: new Float64Array(groups),
    volume: new Float64Array(groups),
  };

  let g = -1;
  let bucket = null;
  for (let i = from; i < n; i += 1) {
    const at = Math.floor(series.ts[i] / span) * span;
    if (at !== bucket) {
      g += 1;
      bucket = at;
      cols.ts[g] = at;
      cols.open[g] = series.open[i];
      cols.high[g] = series.high[i];
      cols.low[g] = series.low[i];
      cols.volume[g] = 0;
    }
    if (series.high[i] > cols.high[g]) cols.high[g] = series.high[i];
    if (series.low[i] < cols.low[g]) cols.low[g] = series.low[i];
    cols.close[g] = series.close[i];
    cols.volume[g] += series.volume[i];
  }

  // 봉이 끊긴 구간이 있으면 묶음 수가 예상보다 적다. 남는 자리를 잘라낸다.
  const made = g + 1;
  const cut = (a) => (made === groups ? a : a.subarray(0, made));
  return new Series(
    series.market, `minute${factor}`,
    cut(cols.ts), cut(cols.open), cut(cols.high), cut(cols.low),
    cut(cols.close), cut(cols.volume),
  );
}
