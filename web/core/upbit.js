// 업비트 공개 시세 API — 브라우저에서 직접 부른다.
//
// 인증이 없다. 이 프로그램은 주문을 내지 않으므로 API 키도, 서명도, 계좌
// 조회도 필요 없다. 시세만 읽는다. 키가 없다는 것이 브라우저에서 직접
// 부를 수 있는 이유이기도 하다 — 숨길 게 없으니 숨길 곳도 필요 없다.
//
// **여기가 이 앱에서 유일하게 불확실한 자리다.**
// 브라우저가 다른 도메인을 부르려면 상대 서버가 CORS 헤더로 허락해 줘야
// 한다. 업비트가 허락하는지는 실제로 브라우저에서 눌러 봐야 안다. 그래서
// 실패했을 때 **왜 실패했는지 구분해서** 알려주는 데 공을 들였다. "실패"
// 한 줄만 띄우면 인터넷이 끊긴 건지, 업비트가 막은 건지, 업비트가 죽은
// 건지 알 수가 없고, 그러면 사용자는 고칠 방법도 없다.

/** 봉 간격 -> 엔드포인트 */
export const ENDPOINTS = {
  minute1: '/v1/candles/minutes/1',
  minute3: '/v1/candles/minutes/3',
  minute5: '/v1/candles/minutes/5',
};

export const API_BASE = 'https://api.upbit.com';

/** 한 번에 받을 수 있는 최대 봉 수 */
export const PAGE = 200;

/** 시세 조회 실패. `kind`로 무엇 때문인지 구분한다. */
export class UpbitError extends Error {
  constructor(message, kind = 'unknown', extra = {}) {
    super(message);
    this.name = 'UpbitError';
    /** offline | blocked | refused | server | rate | parse | unknown */
    this.kind = kind;
    Object.assign(this, extra);
  }
}

/**
 * 초당 N회 토큰 버킷.
 *
 * 1분봉 한 달치를 받으려면 200개씩 200번 넘게 요청해야 한다. 한도를
 * 넘기면 429가 오고, 그러면 수집이 중간에 끊긴다.
 */
export class RateLimiter {
  constructor(perSecond = 8) {
    this.perSecond = Math.max(1, perSecond);
    this.hits = [];
  }

  async acquire() {
    for (;;) {
      const now = Date.now();
      while (this.hits.length && now - this.hits[0] >= 1000) this.hits.shift();
      if (this.hits.length < this.perSecond) {
        this.hits.push(now);
        return;
      }
      const wait = 1000 - (now - this.hits[0]);
      await new Promise((resolve) => { setTimeout(resolve, Math.max(wait, 10)); });
    }
  }
}

const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

/**
 * 요청이 왜 실패했는지 알아본다.
 *
 * 브라우저는 CORS로 막힌 요청과 인터넷이 끊긴 요청을 **똑같이** "Failed to
 * fetch"로 알려준다. 일부러 그렇게 만들어 뒀다 — 다른 사이트가 남의 서버
 * 상태를 캐낼 수 없게 하려고. 그래서 우리 쪽에서 갈라야 한다. 우리 서버
 * (이 페이지가 올라간 곳)가 답하는지 먼저 물어보면 갈린다.
 */
async function diagnose() {
  if (typeof navigator !== 'undefined' && navigator.onLine === false) {
    return new UpbitError(
      '인터넷이 끊겨 있습니다. 연결을 확인하고 다시 눌러 주세요.', 'offline',
    );
  }
  try {
    // 같은 출처라 CORS와 무관하다. 캐시를 피해야 진짜로 나갔다 온 게 된다.
    await fetch(`./manifest.webmanifest?ping=${Date.now()}`, { cache: 'no-store' });
  } catch {
    return new UpbitError(
      '인터넷이 끊겨 있습니다. 연결을 확인하고 다시 눌러 주세요.', 'offline',
    );
  }
  return new UpbitError(
    '인터넷은 되는데 업비트에 닿지 못했습니다. 업비트가 잠깐 막혔거나 점검 중일 수 있습니다.',
    'blocked',
  );
}

export class UpbitClient {
  constructor({ base = API_BASE, retries = 4, perSecond = 8, fetcher = null } = {}) {
    this.base = base.replace(/\/$/, '');
    this.retries = retries;
    this.limiter = new RateLimiter(perSecond);
    // 테스트에서 갈아끼울 수 있게 둔다. 진짜 업비트를 부르는 테스트는
    // 만들 수 없다 — 값이 매번 달라서 무엇과도 대조할 수 없다.
    this.fetch = fetcher ?? ((...args) => globalThis.fetch(...args));
  }

  async get(path, params = {}) {
    const url = new URL(this.base + path);
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
    }

    let last = null;
    for (let attempt = 0; attempt <= this.retries; attempt += 1) {
      await this.limiter.acquire();
      let response;
      try {
        // 헤더를 하나도 붙이지 않는다. 붙이면 브라우저가 먼저 OPTIONS를
        // 보내는데(사전 요청), 그건 실패할 구멍을 하나 더 만드는 것이다.
        response = await this.fetch(url.toString(), { cache: 'no-store' });
      } catch {
        last = await diagnose();
        if (last.kind === 'offline') throw last;   // 재시도해도 소용없다
        if (attempt >= this.retries) throw last;
        await sleep(Math.min(2 ** attempt, 16) * 1000);
        continue;
      }

      if (response.status === 429) {
        last = new UpbitError('업비트 요청 한도를 넘었습니다. 잠시 뒤 다시 시도합니다.', 'rate');
        if (attempt >= this.retries) throw last;
        await sleep(Math.min(2 ** attempt, 16) * 1000);
        continue;
      }
      if (response.status >= 500) {
        last = new UpbitError(`업비트 서버 오류 ${response.status}`, 'server');
        if (attempt >= this.retries) throw last;
        await sleep(Math.min(2 ** attempt, 16) * 1000);
        continue;
      }
      if (response.status >= 400) {
        const body = await response.text().catch(() => '');
        throw new UpbitError(
          `업비트가 요청을 거부했습니다 (${response.status}): ${body.slice(0, 200)}`, 'refused',
        );
      }
      try {
        return await response.json();
      } catch {
        throw new UpbitError('업비트 응답을 읽지 못했습니다 (JSON 아님)', 'parse');
      }
    }
    throw last ?? new UpbitError(`GET ${path} 실패`, 'unknown');
  }

  /** 오래된 것부터 정렬해 돌려준다 (업비트는 최신순으로 준다). */
  async getCandles(market, timeframe, count = PAGE, to = null) {
    const path = ENDPOINTS[timeframe];
    if (!path) {
      throw new Error(`모르는 봉 간격 '${timeframe}'. 사용 가능: ${Object.keys(ENDPOINTS).join(', ')}`);
    }
    const params = { market, count: Math.min(Math.max(count, 1), PAGE) };
    if (to !== null) params.to = toCursor(to);

    const rows = (await this.get(path, params)) ?? [];
    return rows.map(parseCandle).sort((a, b) => a.ts - b.ts);
  }

  /**
   * 지금 얼마인지. 봉이 아니라 현재가다.
   *
   * 봉은 그 분이 끝나야 확정되므로, '지금 시세'로 쓰면 최대 1분 늦은 값을
   * 보여주게 된다. 화면 맨 위에 큰 글씨로 띄울 숫자는 그러면 안 된다.
   */
  async getTicker(markets) {
    const list = Array.isArray(markets) ? markets : [markets];
    const rows = (await this.get('/v1/ticker', { markets: list.join(',') })) ?? [];
    if (!rows.length) throw new UpbitError('현재가를 받지 못했습니다', 'unknown');
    return rows.map((row) => ({
      market: String(row.market),
      price: Number(row.trade_price),
      changeRate: Number(row.signed_change_rate ?? 0),
      changePrice: Number(row.signed_change_price ?? 0),
      high: Number(row.high_price ?? 0),
      low: Number(row.low_price ?? 0),
    }));
  }

  /**
   * `count`개가 모일 때까지 과거로 거슬러 올라가며 받는다.
   *
   * 1분봉은 하루가 1,440개다. 8년치(420만 개)면 200개씩 21,000번 요청해야
   * 하고 아주 오래 걸린다. 그래서 둘을 지원한다.
   *
   * `stopAt`
   *     이 시각(유닉스 초)까지 내려가면 멈춘다. 이미 가진 구간을 다시 받지
   *     않기 위한 것이다. 이게 없으면 8년치를 받아둔 사람이 한 봉을 더
   *     받으려 해도 처음부터 8년을 다시 받게 된다.
   *
   * `onBatch`
   *     페이지를 받을 때마다 **그 페이지만** 넘긴다. 중간에 끊겨도 받은
   *     만큼은 남기기 위한 것이다. 오래 걸리는 수집이 끝에서 끊겨 전부
   *     날아가면 사용자는 다시 시도하지 않는다.
   *
   *     처음에는 여기에 '지금까지 모은 것 전부'를 넘겼는데, 그러면 k번째
   *     페이지에서 200k개를 다시 저장한다. 8년치(21,000페이지)면 저장량이
   *     제곱으로 늘어 사실상 끝나지 않는다.
   *
   * `retain`
   *     거짓이면 받은 봉을 **들고 있지 않는다.** 8년치 420만 개를 메모리에
   *     쌓으면 그것만으로 수백 MB라 아이패드에서 브라우저가 죽는다.
   *     onBatch로 그때그때 저장하고 개수만 세면 되는 경우에 쓴다.
   */
  async collect(market, timeframe, count, {
    end = null, onProgress = null, stopAt = null, onBatch = null, shouldStop = null,
    retain = true,
  } = {}) {
    const collected = retain ? new Map() : null;
    let cursor = end;
    let got = 0;
    let previousOldest = null;
    const step = { minute1: 60, minute3: 180, minute5: 300 }[timeframe];

    while (got < count) {
      if (shouldStop && shouldStop()) break;
      // 마지막 쪽에서는 **남은 만큼만** 달라고 한다. 늘 200개를 달라고 하면
      // 300개를 원했는데 400개를 받아 저장하게 된다. 남는 봉이 해롭지는
      // 않지만, 몇 개를 받을지 말해 놓고 다른 개수를 받는 것은 뒤에서
      // 개수를 세는 쪽(진행률·시험)을 전부 어긋나게 만든다.
      const asking = Math.min(PAGE, count - got);
      // eslint-disable-next-line no-await-in-loop
      const batch = await this.getCandles(market, timeframe, asking, cursor);
      if (!batch.length) break;

      const oldest = batch[0].ts;
      // 더 과거로 못 내려갔으면 업비트에 더 줄 게 없는 것이다. 들고 있지
      // 않을 때는 개수로 알 수 없으므로 커서가 움직였는지로 본다.
      if (previousOldest !== null && oldest >= previousOldest) break;
      previousOldest = oldest;

      if (collected) {
        const before = collected.size;
        for (const candle of batch) collected.set(candle.ts, candle);
        if (collected.size === before) break;
        got = collected.size;
      } else {
        got += batch.length;
      }

      if (onProgress) onProgress(got, count);
      // eslint-disable-next-line no-await-in-loop
      if (onBatch) await onBatch(batch);
      if (stopAt !== null && oldest <= stopAt) break; // 이미 가진 구간에 닿았다
      cursor = oldest - step;
    }

    if (!collected) return [];
    const candles = [...collected.values()].sort((a, b) => a.ts - b.ts);
    return candles.length > count ? candles.slice(candles.length - count) : candles;
  }
}

/** 유닉스 초 -> 업비트가 받는 `to` 문자열. */
export function toCursor(seconds) {
  return `${new Date(seconds * 1000).toISOString().slice(0, 19)}Z`;
}

/** 업비트 응답 한 줄 -> 우리 봉. `ts`는 봉이 열린 시각(유닉스 초, UTC). */
export function parseCandle(row) {
  // candle_date_time_utc는 타임존 표시가 없다. Z를 붙이지 않으면 브라우저가
  // **현지 시각**으로 읽어서 한국에서는 9시간이 어긋난다.
  const ts = Math.floor(Date.parse(`${row.candle_date_time_utc}Z`) / 1000);
  return {
    ts,
    open: Number(row.opening_price),
    high: Number(row.high_price),
    low: Number(row.low_price),
    close: Number(row.trade_price),
    volume: Number(row.candle_acc_trade_volume),
  };
}
