// 업비트 공개 시세 API — 브라우저에서 직접 부른다.
//
// 인증이 없다. 이 프로그램은 주문을 내지 않으므로 API 키도, 서명도, 계좌
// 조회도 필요 없다. 시세만 읽는다.
//
// **규칙은 하나다: 초당 요청 수를 하나의 예산으로 관리한다.**
//
// 예전에는 그러지 못했다. 무엇이 요청을 막는지 몰라서 추측으로 장치를 계속
// 붙였다 — `to` 표기 3가지 × 개수 3가지를 더듬고, 실패하면 절반으로 감속하고,
// 성공하면 1.5배 증속하고, 되돌아가고, 다시 올라가고. 그 장치들이 저마다
// 요청을 더 만들어서, 막히지 않으려던 것이 오히려 막히는 원인이 됐다.
//
// 원인을 알고 나니(업비트가 주소를 통째로 막는 것) 전부 필요 없어졌다.
// 남은 것은 셋뿐이다.
//
//   1. 요청을 고르게 벌려서, 정해진 예산 안에서만 내보낸다
//   2. 막히면 기다렸다 이어 받는다 (받은 것은 그때그때 저장돼 있다)
//   3. 실패하면 무엇 때문인지 가려서 말해 준다

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
 * **초당 요청 수. 이 앱이 업비트에 보내는 전부가 여기에 들어간다.**
 *
 * 업비트 시세 API의 공개 한도는 초당 10회다. 5로 두면 절반을 여유로 남긴다.
 * 남의 몫까지 다 쓸 이유가 없다 — 휴대폰 데이터에서는 한 주소를 여러 사람이
 * 나눠 쓰기 때문에, 우리가 아낀 만큼이 실제로 덜 막히는 쪽으로 돌아온다.
 * 봉을 받는 것도, 맨 위 시세도, 확인 요청도 전부 이 예산을 나눠 쓴다 —
 * 시세는 20초에 한 번이라 사실상 봉 받기가 거의 다 쓴다.
 *
 * 예전에는 이게 지켜지지 않았다. 화면과 워커에 클라이언트가 따로 있어
 * 제한기가 둘이었고, 서로를 몰랐다. 그래서 "초당 3회"라고 적어 놓고 실제로는
 * 그보다 많이 나갔다. 이제 나가는 길이 하나뿐이라 이 숫자가 곧 사실이다.
 *
 * **막히면 여기만 낮추면 된다.** 다른 곳을 고칠 필요가 없다.
 */
export const PER_SECOND = 5;

/**
 * `to`(어느 시점 이전을 달라)를 적는 방법.
 *
 * 실제로 쓰는 건 첫 번째 하나다 — 업비트 문서의 표기이고, 실제로 이걸로
 * 받아 왔다. 나머지 둘은 **연결 진단에서만** 쓴다. 혹시 표기가 문제인
 * 경우를 사람이 한 번에 확인할 수 있게.
 *
 * 예전에는 클라이언트가 세 표기를 차례로 더듬었다. 표기가 문제인 줄 알았기
 * 때문인데, 사실은 막혀 있던 것이었다. 막힌 상태에서 표기를 바꿔 가며
 * 다시 보내는 건 상황을 나쁘게만 만든다.
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
    /** offline | throttled | blocked | stalled | rate | server | refused | parse | unknown */
    this.kind = kind;
    Object.assign(this, extra);
  }
}

const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

/** 잠깐 걸렸을 때 쉬는 시간과 횟수. */
export const STALL_PAUSE = 3000;
export const STALL_RETRIES = 4;

/**
 * **막혔을 때** 쉬는 시간. 잠깐 걸린 것과는 차원이 다르다.
 *
 * 업비트는 한도를 넘긴 주소를 초 단위가 아니라 몇 분씩 막는다. 진단이 요청을
 * 1.2초씩 벌려 8번 물어봤는데 전부 실패한 적이 있다 — 초당 한도였다면 그
 * 간격의 단발 요청은 통과해야 한다.
 *
 * 그 사이에는 무엇을 해도 안 되고 자꾸 두드리면 더 길어진다. 1분부터
 * 늘려 가며 네 번 기다린다(합쳐서 10분). 사람이 10분 뒤에 다시 누르는 대신
 * 앱이 스스로 기다리는 것이고, 받은 만큼은 저장돼 있으니 잃는 게 없다.
 */
export const THROTTLE_PAUSE = 60000;
export const THROTTLE_RETRIES = 4;

/** 진단 결과를 이만큼은 그대로 쓴다 (밀리초). */
export const DIAGNOSIS_TTL = 5000;

/**
 * 요청을 **고르게 벌려서** 내보낸다.
 *
 * 처음에는 '지난 1초에 N번 미만이면 통과'로 만들었다. 그건 초당 회수는
 * 지키지만 **간격은 안 지킨다** — 창이 비어 있으면 여러 개가 한꺼번에 나가고
 * 남은 시간을 쉰다. 평균은 맞지만 순간 속도가 수십 배다. 실제로 그것 때문에
 * 막혔다.
 *
 * 이제 다음 요청 시각을 미리 잡아 둔다. **기다리기 전에** 잡으므로, 여러
 * 곳에서 동시에 불러도 서로 겹치지 않고 줄을 선다.
 */
export class RateLimiter {
  constructor(perSecond = PER_SECOND) {
    this.perSecond = Math.max(0.5, perSecond);
    this.next = 0;
  }

  /** 요청 사이 최소 간격(밀리초). */
  get gap() {
    return 1000 / this.perSecond;
  }

  async acquire() {
    const now = Date.now();
    const at = Math.max(now, this.next);
    this.next = at + this.gap;
    if (at > now) await sleep(at - now);
  }
}

export class UpbitClient {
  constructor({
    base = API_BASE, retries = 2, perSecond = PER_SECOND, fetcher = null,
    stallPause = STALL_PAUSE, throttlePause = THROTTLE_PAUSE,
  } = {}) {
    this.base = base.replace(/\/$/, '');
    this.retries = retries;
    this.stallPause = stallPause;
    this.throttlePause = throttlePause;
    this.limiter = new RateLimiter(perSecond);
    // 테스트에서 갈아끼울 수 있게 둔다. 진짜 업비트를 부르는 테스트는
    // 만들 수 없다 — 값이 매번 달라서 무엇과도 대조할 수 없다.
    this.fetch = fetcher ?? ((...args) => globalThis.fetch(...args));
    /** 지금까지 업비트에서 실제로 받아 온 횟수. 진단이 이걸 본다. */
    this.succeeded = 0;
    /** 마지막 진단 결과. 실패마다 다시 물어보지 않으려고 잠깐 들고 있는다. */
    this.lastDiagnosis = null;
    /** 막혀 있다고 확인된 시각. 그동안은 급하지 않은 요청을 아예 안 보낸다. */
    this.blockedAt = 0;
  }

  /**
   * 한 번 물어본다. 실패하면 무엇 때문인지 가려서 던진다.
   *
   * `toSeconds`가 있으면 `to`를 붙인다. 표기는 문서에 있는 하나만 쓴다.
   */
  async get(path, params = {}, toSeconds = null, { retries = this.retries } = {}) {
    let last = null;

    for (let attempt = 0; ; attempt += 1) {
      const url = new URL(this.base + path);
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
      }
      if (toSeconds !== null) url.searchParams.set('to', TO_FORMATS[0](toSeconds));

      await this.limiter.acquire();
      let response = null;
      try {
        // 헤더를 하나도 붙이지 않는다. 붙이면 브라우저가 먼저 OPTIONS를
        // 보내는데(사전 요청), 그건 실패할 구멍을 하나 더 만드는 것이다.
        response = await this.fetch(url.toString(), { cache: 'no-store' });
      } catch {
        // 브라우저는 CORS로 막힌 것과 인터넷이 끊긴 것을 똑같이 알려준다.
        // 무엇 때문인지는 diagnose가 가른다.
        last = await this.diagnose();
        // 이 둘은 다시 해 봐야 소용없다. 특히 막힌 상태에서 재시도하는 건
        // 상황을 나쁘게만 만든다.
        if (last.kind === 'offline' || last.kind === 'throttled') throw last;
        if (attempt >= retries) throw last;
        await sleep(Math.min(2 ** attempt, 8) * 1000);
        continue;
      }

      if (response.status < 400) {
        let payload;
        try {
          payload = await response.json();
        } catch {
          throw new UpbitError('업비트 응답을 읽지 못했습니다 (JSON 아님)', 'parse');
        }
        this.succeeded += 1;
        return payload;
      }

      if (response.status === 429) {
        last = new UpbitError('업비트 요청 한도를 넘었습니다.', 'rate');
        throw last;   // 기다리는 건 부르는 쪽(collect)이 한다
      }
      if (response.status >= 500) {
        last = new UpbitError(`업비트 서버 오류 ${response.status}`, 'server');
        if (attempt >= retries) throw last;
        await sleep(Math.min(2 ** attempt, 8) * 1000);
        continue;
      }
      const body = await response.text().catch(() => '');
      throw new UpbitError(
        `업비트가 요청을 거부했습니다 (${response.status}): ${body.slice(0, 200)}`, 'refused',
      );
    }
  }

  /**
   * 무엇 때문에 막혔는지 가른다.
   *
   * 브라우저는 CORS로 막힌 요청과 인터넷이 끊긴 요청을 **똑같이** 알려준다.
   * 그래서 우리 쪽에서 갈라야 한다. 증거는 셋이다.
   *
   *   1. 우리 쪽 파일이 받아지는가        → 아니면 인터넷이 끊긴 것
   *   2. 평범한 요청(현재가)이 되는가     → 되면 통째로 막힌 건 아니다
   *   3. no-cors로는 닿는가               → 닿으면 답은 온 것이다
   *
   * 2번이 안 되는데 3번이 되면, 업비트까지 갔고 답도 왔는데 그 답을 읽을 수
   * 없다는 뜻이다. 허용 표시(CORS)는 정상 응답에는 붙고 **거절 응답에는 안
   * 붙으므로**, 지금 업비트가 우리를 통째로 거절하고 있는 것이다.
   *
   * 이걸 구분 못 해서 오랫동안 "닿지 못했습니다"라고 잘못 말해 왔다.
   */
  async diagnose() {
    const now = Date.now();
    if (this.lastDiagnosis && now - this.lastDiagnosis.at < DIAGNOSIS_TTL) {
      return this.lastDiagnosis.error;
    }
    // **막힌 걸 이미 알면 다시 물어보지 않는다.**
    //
    // 진단 한 번에 업비트로 두 번 나간다(평범한 요청 + no-cors). 몇 분짜리
    // 차단 동안 5초마다 그걸 반복하면, 확인하려다 풀릴 틈을 없앤다.
    if (this.knownBlocked() && this.lastDiagnosis) return this.lastDiagnosis.error;
    const remember = (error, blocked = false) => {
      this.lastDiagnosis = { at: Date.now(), error };
      if (blocked) this.blockedAt = Date.now();
      return error;
    };
    const offline = () => new UpbitError(
      '인터넷이 끊겨 있습니다. 연결을 확인하고 다시 눌러 주세요.', 'offline',
    );

    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      return remember(offline());
    }
    try {
      // 같은 출처라 CORS와 무관하다. ?ping= 이 붙은 요청은 서비스 워커가
      // 캐시로 답하지 않는다(web/sw.js) — 그래야 진짜로 나갔다 온 게 된다.
      await this.fetch(`./manifest.webmanifest?ping=${Date.now()}`, { cache: 'no-store' });
    } catch {
      return remember(offline());
    }

    if (!(await this.plainWorks()) && await this.reaches()) {
      return remember(new UpbitError(
        '업비트가 지금 우리 요청을 막고 있습니다. 업비트까지는 갔고 답도 왔지만 '
        + '브라우저가 그 답을 읽을 수 없습니다 — 거절당했을 때 그렇습니다. '
        + '요청이 잦아서 잠시 막힌 것일 수 있으니 10분쯤 뒤에 다시 눌러 주세요.',
        'throttled',
      ), true);
    }
    if (this.succeeded > 0) {
      return remember(new UpbitError(
        `업비트에서 ${this.succeeded}번은 받았는데 그 뒤로 막혔습니다. `
        + '받은 만큼은 저장돼 있으니 다시 누르면 이어서 받습니다.',
        'stalled',
      ));
    }
    return remember(new UpbitError(
      '인터넷은 되는데 업비트에 닿지 못했습니다. 업비트가 잠깐 막혔거나 점검 중일 수 있습니다.',
      'blocked',
    ));
  }

  /**
   * 지금 막혀 있다고 알고 있는가.
   *
   * 급하지 않은 요청(맨 위 시세)은 이 동안 아예 보내지 않는다. 숫자 하나
   * 때문에 막힌 업비트를 계속 두드리면 풀릴 틈만 없어진다.
   */
  knownBlocked(within = THROTTLE_PAUSE) {
    return this.blockedAt > 0 && Date.now() - this.blockedAt < within;
  }

  /** 가장 평범한 요청이 되는가. */
  async plainWorks() {
    try {
      const response = await this.fetch(`${this.base}/v1/ticker?markets=KRW-BTC`, {
        cache: 'no-store',
      });
      return response.status < 400;
    } catch {
      return false;
    }
  }

  /** 업비트에 닿기는 하는가. 내용은 못 읽어도 답이 왔는지는 알 수 있다. */
  async reaches() {
    try {
      await this.fetch(`${this.base}/v1/ticker?markets=KRW-BTC`, {
        mode: 'no-cors', cache: 'no-store',
      });
      return true;
    } catch {
      return false;
    }
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
    // **재시도하지 않는다.** 맨 위 숫자는 장식이라, 그것 하나 때문에 막혀
    // 있는 업비트를 다섯 번 더 두드릴 이유가 없다. 그건 회복을 늦출 뿐이다.
    const rows = (await this.get('/v1/ticker', { markets: list.join(',') }, null, { retries: 0 }))
      ?? [];
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
    let stalls = 0;
    const step = { minute1: 60, minute3: 180, minute5: 300 }[timeframe];

    while (got < count) {
      if (shouldStop && shouldStop()) break;
      // 마지막 쪽에서는 **남은 만큼만** 달라고 한다. 늘 200개를 달라고 하면
      // 300개를 원했는데 400개를 받아 저장하게 된다. 남는 봉이 해롭지는
      // 않지만, 몇 개를 받을지 말해 놓고 다른 개수를 받는 것은 뒤에서
      // 개수를 세는 쪽(진행률·시험)을 전부 어긋나게 만든다.
      const asking = Math.min(PAGE, count - got);

      // **중간에 걸렸다고 통째로 포기하지 않는다.**
      //
      // 받은 쪽은 그때그때 저장된다(onBatch). 4,812개까지 잘 받다가 한 번
      // 걸려서 거기서 끝난 적이 있는데, 그건 4,812개를 잃은 게 아니라 **남은
      // 걸 안 받은** 것이다. 쉬었다 같은 자리에서 이어 받으면 된다.
      //
      // 인터넷이 끊긴 것만 예외다. 그건 쉬어도 안 되고, 기다리게 하면 안 된다.
      let batch;
      try {
        // eslint-disable-next-line no-await-in-loop
        batch = await this.getCandles(market, timeframe, asking, cursor);
        stalls = 0;
      } catch (error) {
        // **받던 중에** 걸린 것만 기다린다.
        //
        // 첫 요청부터 실패했다면 이어 받을 것도 없고, 길이 아예 막힌
        // 경우다. 그때까지 1분씩 쥐고 있으면 "막혔습니다"라는 말조차 늦게
        // 나온다 — 사용자는 그동안 무슨 일인지 알 수가 없다.
        const kind = error instanceof UpbitError ? error.kind : null;

        // **막힌 것은 기다리면 풀린다.** 그러니 포기하지 않는다.
        //
        // 업비트는 한도를 넘긴 주소를 초 단위가 아니라 **몇 분씩** 막는다
        // (진단이 1.2초 간격으로 8번 물어봐도 전부 실패했다). 그동안은
        // 무슨 짓을 해도 안 되고, 자꾸 두드리면 더 길어질 뿐이다.
        //
        // 그런데 받은 만큼은 이미 저장돼 있으므로 기다렸다 이어 받으면 된다.
        // 사람이 10분 뒤에 다시 누르는 대신 **앱이 스스로 기다린다.**
        const banned = kind === 'throttled' || kind === 'rate';
        const canWait = kind !== null && kind !== 'offline' && kind !== 'blocked'
          && (banned ? stalls < THROTTLE_RETRIES : got > 0 && stalls < STALL_RETRIES);
        if (!canWait) throw error;
        stalls += 1;
        // 몇 분을 아무 표시 없이 쉬면 멈춘 것으로 보인다. 1초마다 남은
        // 시간을 알려서, 기다리는 중이라는 걸 보이게 한다.
        const until = Date.now() + (banned ? this.throttlePause : this.stallPause) * stalls;
        while (Date.now() < until) {
          if (shouldStop && shouldStop()) break;
          if (onProgress) {
            onProgress(got, count, {
              stalled: stalls, banned, waitLeft: Math.ceil((until - Date.now()) / 1000),
            });
          }
          // eslint-disable-next-line no-await-in-loop
          await sleep(Math.min(1000, Math.max(0, until - Date.now())));
        }
        // 다음 시도는 새로 진단한다. 기다린 뒤에도 옛 판단을 쓰면 안 된다.
        this.lastDiagnosis = null;
        // eslint-disable-next-line no-continue
        continue;
      }
      if (!batch.length) {
        // 첫 쪽부터 빈손이면 조용히 끝내지 않는다.
        //
        // 200 OK에 빈 배열은 '더 줄 게 없다'는 뜻이기도 하지만, **첫 요청부터**
        // 그렇다면 그럴 리가 없다 — 비트코인 1분봉은 몇 년치가 있다. 이걸
        // 성공으로 받으면 화면에는 오류도 없이 그냥 멈춘 것으로 보인다.
        if (got === 0) {
          throw new UpbitError(
            '업비트가 답은 했는데 봉을 하나도 주지 않았습니다.', 'empty',
          );
        }
        break;   // 받다가 끝에 닿은 것 — 정상이다
      }

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
