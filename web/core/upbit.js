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

/**
 * 초당 요청 수.
 *
 * 4년치는 1만 6천 번을 넘게 부른다. 너무 빠르면 업비트가 막는다 — 실제로
 * 8회일 때 첫 쪽만 받고 그 뒤가 전부 막히는 일이 있었다. 화면이 "얼마나
 * 걸리는지"를 계산할 때도 이 값을 쓰므로, 여기 하나만 고치면 된다.
 */
export const PER_SECOND = 5;

/**
 * `to`(어느 시점 이전을 달라)를 적는 방법. 업비트가 여럿을 받아 준다.
 *
 * 왜 여러 개를 두는가 — 아이패드에서 실제로 돌려 보니 **`to`가 붙은 요청만**
 * 막혔다. 맨 위 시세도, 첫 쪽(200개)도 잘 받았는데 둘째 쪽부터 전부
 * 실패했다. 둘의 유일한 차이가 `to`였다. 어느 표기를 받아 주는지는 여기서
 * 알 수 없으므로(이 환경에서는 업비트가 막혀 있다) **차례로 넣어 보고
 * 통하는 것에 고정한다.**
 */
export const TO_FORMATS = [
  (seconds) => `${new Date(seconds * 1000).toISOString().slice(0, 19)}Z`,
  (seconds) => new Date(seconds * 1000).toISOString().slice(0, 19).replace('T', ' '),
  (seconds) => `${new Date(seconds * 1000).toISOString().slice(0, 19)}+00:00`,
];

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

const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

/** `to` 표기를 처음부터 다시 훑어보는 횟수. */
export const TO_SWEEPS = 1;

/** 표기를 다시 훑기 전에 쉬는 시간. 한도에 걸린 것이라면 이 사이에 풀린다. */
export const SWEEP_PAUSE = 2000;

/** 아무리 느려져도 이보다 느려지지는 않는다 (초당 회수). */
export const SLOWEST = 0.5;

/**
 * 요청을 **고르게 벌려서** 내보낸다.
 *
 * 처음에는 '지난 1초에 N번 미만이면 통과'로 만들었다. 그건 초당 회수는
 * 지키지만 **간격은 안 지킨다** — 창이 비어 있으면 5개가 한꺼번에 나가고
 * 남은 시간을 쉰다. 평균은 초당 5회지만 순간 속도는 초당 100회다.
 *
 * 아이패드에서 실제로 이것 때문에 막혔다. 맨 위 시세와 첫 쪽은 받아지는데
 * 그 뒤가 전부 실패했다. 한꺼번에 나간 쪽이 통째로 걸린 것이다. 그리고
 * 브라우저는 그걸 `to` 표기 문제와 구별할 수 없게 알려준다(아래 참고).
 *
 * 이제 **다음 요청 시각을 미리 잡아 둔다.** 기다리기 전에 잡으므로, 여러
 * 곳에서 동시에 불러도 서로 겹치지 않고 줄을 선다.
 */
export class RateLimiter {
  constructor(perSecond = PER_SECOND) {
    this.perSecond = Math.max(SLOWEST, perSecond);
    this.next = 0;
  }

  /** 요청 사이 최소 간격(밀리초). */
  get gap() {
    return 1000 / this.perSecond;
  }

  async acquire() {
    const now = Date.now();
    const at = Math.max(now, this.next);
    // **기다리기 전에** 자리를 잡는다. 기다린 뒤에 잡으면 동시에 들어온
    // 요청들이 같은 자리를 잡고 함께 나간다 — 고치려던 그 문제가 된다.
    this.next = at + this.gap;
    if (at > now) await sleep(at - now);
  }

  /**
   * 스스로 느려진다.
   *
   * 막힌 이유를 브라우저가 안 알려주므로, 막혔으면 일단 느려지고 본다.
   * 한 번 느려지면 그 상태로 남는다 — 다시 빨라져 봐야 또 막힌다.
   */
  slowDown() {
    this.perSecond = Math.max(SLOWEST, this.perSecond / 2);
    return this.perSecond;
  }
}

export class UpbitClient {
  constructor({
    base = API_BASE, retries = 4, perSecond = PER_SECOND, fetcher = null,
    sweepPause = SWEEP_PAUSE,
  } = {}) {
    this.base = base.replace(/\/$/, '');
    this.retries = retries;
    this.sweepPause = sweepPause;
    this.limiter = new RateLimiter(perSecond);
    // 테스트에서 갈아끼울 수 있게 둔다. 진짜 업비트를 부르는 테스트는
    // 만들 수 없다 — 값이 매번 달라서 무엇과도 대조할 수 없다.
    this.fetch = fetcher ?? ((...args) => globalThis.fetch(...args));
    /** 지금까지 업비트에서 실제로 받아 온 횟수. 진단이 이걸 본다. */
    this.succeeded = 0;
    /** 쓰고 있는 `to` 표기. 통하는 걸 찾으면 거기서 고정한다. */
    this.toFormat = 0;
    this.toProven = false;
  }

  /**
   * `toSeconds`를 따로 받는 이유 — 표기를 바꿔 가며 다시 시도해야 하기
   * 때문이다. URL을 미리 만들어 두면 표기를 못 바꾼다.
   */
  async get(path, params = {}, toSeconds = null) {
    const wantsTo = toSeconds !== null;
    let last = null;
    let attempt = 0;   // 지연을 넣고 다시 해 본 횟수
    let sweeps = 0;    // to 표기를 처음부터 다시 훑은 횟수

    for (;;) {
      const url = new URL(this.base + path);
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
      }
      if (wantsTo) url.searchParams.set('to', TO_FORMATS[this.toFormat](toSeconds));

      await this.limiter.acquire();
      let response = null;
      let threw = false;
      try {
        // 헤더를 하나도 붙이지 않는다. 붙이면 브라우저가 먼저 OPTIONS를
        // 보내는데(사전 요청), 그건 실패할 구멍을 하나 더 만드는 것이다.
        response = await this.fetch(url.toString(), { cache: 'no-store' });
      } catch {
        threw = true;
      }

      if (!threw && response.status < 400) {
        try {
          const payload = await response.json();
          this.succeeded += 1;
          // 이 표기로 실제로 받아 봤다. 이제부터는 이것만 쓴다.
          if (wantsTo) this.toProven = true;
          return payload;
        } catch {
          throw new UpbitError('업비트 응답을 읽지 못했습니다 (JSON 아님)', 'parse');
        }
      }

      if (threw) {
        last = await this.diagnose();
        if (last.kind === 'offline') throw last;   // 재시도해도 소용없다
        // **여기가 핵심이다.** 브라우저는 한도 초과 응답(429)에 CORS 헤더가
        // 없으면 상태 코드를 안 보여주고 그냥 예외를 던진다. 그래서 '막혔다'와
        // '너무 빨랐다'가 이 자리에서 똑같이 생겼다. 구분할 수 없으니 **일단
        // 느려지고 본다.** 느려져서 손해 보는 경우는 조금 오래 걸리는 것뿐이고,
        // 안 느려져서 손해 보는 경우는 아예 못 받는 것이다.
        this.limiter.slowDown();
      } else if (response.status === 429) {
        last = new UpbitError('업비트 요청 한도를 넘었습니다. 속도를 낮춰 다시 받습니다.', 'rate');
        this.limiter.slowDown();
      } else if (response.status >= 500) {
        last = new UpbitError(`업비트 서버 오류 ${response.status}`, 'server');
      } else {
        const body = await response.text().catch(() => '');
        last = new UpbitError(
          `업비트가 요청을 거부했습니다 (${response.status}): ${body.slice(0, 200)}`, 'refused',
        );
      }

      // to 표기를 아직 못 정했으면 다음 표기를 넣어 본다. 서버가 죽은
      // 것(5xx)은 표기와 무관하므로 그때는 훑지 않는다.
      if (wantsTo && !this.toProven && last.kind !== 'server') {
        if (this.toFormat < TO_FORMATS.length - 1) {
          this.toFormat += 1;
          continue;
        }
        // 세 표기가 다 안 통했다. 여기서 '표기 문제'라고 결론 내리면 안 된다 —
        // 너무 빨라서 셋 다 막힌 것일 수도 있다. 느려진 채로 한 번 더 훑어
        // 본다. 이걸로 통하면 표기가 아니라 속도가 문제였던 것이다.
        if (sweeps < TO_SWEEPS) {
          sweeps += 1;
          this.toFormat = 0;
          this.limiter.slowDown();
          await sleep(this.sweepPause);
          continue;
        }
      }

      if (attempt >= this.retries) throw last;
      await sleep(Math.min(2 ** attempt, 16) * 1000);
      attempt += 1;
    }
  }

  /**
   * 무엇 때문에 막혔는지 가른다.
   *
   * 브라우저는 CORS로 막힌 요청과 인터넷이 끊긴 요청을 **똑같이** "Failed to
   * fetch"로 알려준다. 그래서 우리 쪽에서 갈라야 한다.
   *
   * **이미 한 번이라도 받아 본 적이 있으면** 이야기가 완전히 다르다.
   * 업비트까지 가는 길은 뚫려 있다는 뜻이므로 "닿지 못했습니다"는 거짓말이
   * 된다. 실제로 그렇게 말해서, 시세가 멀쩡히 뜨는 화면에 "업비트에 닿지
   * 못했습니다"가 같이 떠 있었다.
   */
  async diagnose() {
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      return new UpbitError(
        '인터넷이 끊겨 있습니다. 연결을 확인하고 다시 눌러 주세요.', 'offline',
      );
    }
    if (this.succeeded > 0) {
      return new UpbitError(
        `업비트에서 ${this.succeeded}번은 받았는데 그 뒤로 막혔습니다. `
        + '너무 자주 부른 것으로 보고 속도를 낮췄습니다. '
        + '받은 만큼은 저장돼 있으니 다시 누르면 이어서 받습니다.',
        'stalled',
      );
    }
    try {
      // 같은 출처라 CORS와 무관하다. 캐시를 피해야 진짜로 나갔다 온 게 된다.
      // ?ping= 이 붙은 요청은 서비스 워커가 캐시로 답하지 않는다(web/sw.js).
      await this.fetch(`./manifest.webmanifest?ping=${Date.now()}`, { cache: 'no-store' });
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

  /** 오래된 것부터 정렬해 돌려준다 (업비트는 최신순으로 준다). */
  async getCandles(market, timeframe, count = PAGE, to = null) {
    const path = ENDPOINTS[timeframe];
    if (!path) {
      throw new Error(`모르는 봉 간격 '${timeframe}'. 사용 가능: ${Object.keys(ENDPOINTS).join(', ')}`);
    }
    const params = { market, count: Math.min(Math.max(count, 1), PAGE) };
    const rows = (await this.get(path, params, to)) ?? [];
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

/** 유닉스 초 -> 업비트가 받는 `to` 문자열 (기본 표기). */
export function toCursor(seconds) {
  return TO_FORMATS[0](seconds);
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
