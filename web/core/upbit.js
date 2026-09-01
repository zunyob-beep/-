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
 * 너무 빠르면 업비트가 막는다. 8회일 때 첫 쪽만 받고 그 뒤가 전부 막혔고,
 * 5회로 낮추고 간격을 고르게 벌리자 4,812개까지 갔다. 그런데 그 뒤에 또
 * 통째로 막혔다 — 진단이 그걸 보여줬다(보통 요청은 다 실패, no-cors는 성공).
 *
 * 그래서 3회로 더 낮춘다. 대신 1분봉만 받고 3·5분봉은 묶어서 만들므로
 * 요청 수 자체가 35% 줄었다. 결과적으로 30일치는 예전보다 오래 걸리지 않는다.
 *
 * 화면이 "얼마나 걸리는지"를 계산할 때도 이 값을 쓰므로 여기만 고치면 된다.
 */
export const PER_SECOND = 3;

/**
 * 잘 되고 있을 때 **올라갈 수 있는 상한.**
 *
 * 한때 이걸 8로 올렸다가 되돌렸다. 상한이 시작 속도와 같은 것을 버그로 보고
 * "잘 되면 더 빨라져야 한다"고 고쳤는데, 그게 **버그가 아니라 안전장치**였다.
 *
 * 실제로 겪은 순서가 이렇다.
 *
 *   초당 8회, 몰아 쏨      201개에서 막힘
 *   초당 5회, 고르게       4,812개에서 막힘
 *   초당 3회, 고르게       끝까지 잘 받음
 *   초당 3→8회로 올림      다시 막힘
 *
 * 그리고 막히면 초당 한도가 아니라 **한동안 통째로** 막힌다. 진단이 요청을
 * 1.2초씩 벌려 8번 물어봤는데 전부 실패했다 — 초당 한도였다면 그 간격의 단발
 * 요청은 통과해야 한다. 즉 한 번 넘기면 대가가 몇 분이다.
 *
 * 그래서 상한을 시작 속도와 같게 둔다. 올리는 기능은 남는다 — 막혀서 절반으로
 * 내려갔을 때 **원래 속도까지 되돌아오기 위한** 것이고, 그게 원래 목적이었다.
 */
export const CEILING = 3;

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

/**
 * `to`와 함께 쓸 때 한 번에 몇 개까지 달라고 할지.
 *
 * `null`은 '요청한 개수 그대로'다. 나머지는 그보다 적게 달라고 해 보는 것이다.
 *
 * 왜 이것까지 더듬는가 — 표기만 세 가지 넣어 봤는데도 세 번 다 같은 자리에서
 * 막혔다. 그러면 원인이 표기가 아닐 수 있다는 뜻이고, 남는 후보 중 하나가
 * '`to`와 큰 `count`를 같이 주면 거절한다'는 것이다. 나는 여기서 업비트에
 * 닿을 수 없어 어느 쪽인지 확인할 방법이 없다. **그래서 맞히지 않고,
 * 앱이 돌면서 직접 찾게 한다.**
 */
export const TO_CAPS = [null, 100, 10];

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
 * 이만큼 연속으로 성공하면 빨라져 본다.
 *
 * 20이었는데 너무 느긋했다. 30일치가 231번이라 올릴 기회가 11번뿐이고,
 * 한 번 삐끗해 절반으로 내려가면 그 안에 회복이 안 된다.
 */
export const SPEEDUP_AFTER = 10;

/**
 * 되돌아온 뒤 이만큼(× SPEEDUP_AFTER)을 더 잘 받으면 **한 번 더 올려 본다.**
 *
 * 예전에는 한 번 되돌아오면 영영 안 올렸다. 그런데 개수 조합이 10개짜리로
 * 내려가 있으면 요청이 **스무 배**가 된다 (30일치 216번 → 4,320번). 딸꾹질
 * 한 번의 대가가 그것이면 너무 크다. 오래 잘 되고 있으면 다시 올려 본다.
 */
export const CLIMB_RETRY = 10;

/**
 * 받는 도중에 걸렸을 때 **같은 자리에서 몇 번까지 다시 이어 받을지.**
 *
 * 받은 쪽은 그때그때 저장된다(collect의 onBatch). 그러니 중간에 한 번
 * 걸렸다고 통째로 포기할 이유가 없다. 실제로 4,812개까지 잘 받다가 한 번
 * 걸려서 거기서 끝났는데, 그건 4,812개를 버린 게 아니라 **남은 걸 안 받은**
 * 것이다. 쉬었다 이어 받으면 된다.
 */
export const STALL_RETRIES = 6;

/** 걸렸을 때 쉬는 시간. 다시 걸릴수록 더 오래 쉰다. */
export const STALL_PAUSE = 3000;

/**
 * **막혔을 때** 쉬는 시간. 삐끗한 것과는 차원이 다르다.
 *
 * 업비트는 한도를 넘긴 주소를 초 단위가 아니라 몇 분씩 막는다. 그 사이에는
 * 무엇을 해도 안 되고, 자꾸 두드리면 더 길어질 뿐이다. 1분부터 시작해
 * 늘려 가며 네 번 기다린다 — 합쳐서 10분이다.
 *
 * 사람이 10분 뒤에 다시 누르는 대신 앱이 스스로 기다리는 것이다. 받은
 * 만큼은 저장돼 있으므로 기다렸다 이어 받으면 잃는 것이 없다.
 */
export const THROTTLE_PAUSE = 60000;
export const THROTTLE_RETRIES = 4;

/** 진단 결과를 이만큼은 그대로 쓴다 (밀리초). */
export const DIAGNOSIS_TTL = 5000;

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
  constructor(perSecond = PER_SECOND, ceiling = CEILING) {
    this.perSecond = Math.max(SLOWEST, perSecond);
    /**
     * 올라갈 수 있는 상한. **시작 속도와 다른 값이어야 한다.**
     * 같게 두면 잘 되고 있어도 절대 빨라지지 못한다 — 그게 느렸던 이유다.
     */
    this.top = Math.max(this.perSecond, ceiling);
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

  /**
   * 잘 되고 있으면 도로 빨라진다.
   *
   * 이게 없으면 **한 번의 딸꾹질이 영구적인 벌이 된다.** 4년치를 받는 동안
   * 중간에 한 번 실패하면 그때부터 끝까지 절반 속도로 기어간다. 실제로
   * 화면이 많이 느려졌다는 말을 들었고, 원인이 이것이었다.
   *
   * 내릴 때는 절반씩(빠르게), 올릴 때는 조금씩(천천히). 다시 막히면 곧장
   * 절반으로 내려가므로, 올리다 과해져도 금방 제자리를 찾는다.
   */
  speedUp() {
    this.perSecond = Math.min(this.top, this.perSecond * 1.5);
    return this.perSecond;
  }
}

export class UpbitClient {
  constructor({
    base = API_BASE, retries = 4, perSecond = PER_SECOND, fetcher = null,
    sweepPause = SWEEP_PAUSE, stallPause = STALL_PAUSE, throttlePause = THROTTLE_PAUSE,
  } = {}) {
    this.base = base.replace(/\/$/, '');
    this.retries = retries;
    this.sweepPause = sweepPause;
    this.stallPause = stallPause;
    this.throttlePause = throttlePause;
    this.limiter = new RateLimiter(perSecond);
    // 테스트에서 갈아끼울 수 있게 둔다. 진짜 업비트를 부르는 테스트는
    // 만들 수 없다 — 값이 매번 달라서 무엇과도 대조할 수 없다.
    this.fetch = fetcher ?? ((...args) => globalThis.fetch(...args));
    /** 지금까지 업비트에서 실제로 받아 온 횟수. 진단이 이걸 본다. */
    this.succeeded = 0;
    /**
     * `to`를 어떻게 보낼지 — 표기와 개수의 조합을 차례로 더듬는다.
     * 한 번 통하면 거기서 고정한다(`toProven`).
     */
    this.planAt = 0;
    this.toProven = false;
    /** 연속 성공 횟수. 잘 되고 있으면 도로 빨라지려고 센다. */
    this.streak = 0;
    /** 더 빠른 조합을 시도해 봤다가 안 돼서 되돌아온 상태. 그러면 그만 올린다. */
    this.settled = false;
    /** 지금 더 빠른 조합을 시험해 보는 중이면, 안 될 때 돌아갈 자리. */
    this.fallback = null;
    /** 되돌아온 뒤 얼마나 잘 되고 있는지. 충분하면 다시 올려 본다. */
    this.climbWait = 0;
    /** 마지막 진단 결과. 실패마다 다시 물어보지 않으려고 잠깐 들고 있는다. */
    this.lastDiagnosis = null;
    /** 막혀 있다고 확인된 시각. 이 뒤 얼마 동안은 굳이 두드리지 않는다. */
    this.blockedAt = 0;
  }

  /** 더듬어 볼 조합. 표기 3가지 × 개수 3가지 = 9가지. */
  // eslint-disable-next-line class-methods-use-this
  get toPlan() {
    const plan = [];
    // 개수를 바깥에 둔다 — 먼저 세 표기를 원래 개수로 다 해 보고,
    // 그래도 안 되면 그때 개수를 줄인다. 개수를 줄이면 그만큼 느려지므로
    // 마지막 수단이어야 한다.
    for (const cap of TO_CAPS) {
      for (let format = 0; format < TO_FORMATS.length; format += 1) plan.push({ format, cap });
    }
    return plan;
  }

  /** 지금 쓰는 `to` 표기. */
  get toFormat() {
    return this.toPlan[this.planAt].format;
  }

  /** 지금 쓰는 개수 상한. `null`이면 요청한 개수 그대로. */
  get toCap() {
    return this.toPlan[this.planAt].cap;
  }

  /** 무엇으로 정착했는지. 화면과 진단이 사람에게 보여줄 말. */
  get toStrategy() {
    const { format, cap } = this.toPlan[this.planAt];
    return `표기 ${format + 1}번${cap === null ? '' : `, 한 번에 ${cap}개씩`}`;
  }

  /**
   * `toSeconds`를 따로 받는 이유 — 표기를 바꿔 가며 다시 시도해야 하기
   * 때문이다. URL을 미리 만들어 두면 표기를 못 바꾼다.
   */
  async get(path, params = {}, toSeconds = null, { retries = this.retries } = {}) {
    const wantsTo = toSeconds !== null;
    let last = null;
    let attempt = 0;   // 지연을 넣고 다시 해 본 횟수
    let sweeps = 0;    // to 표기를 처음부터 다시 훑은 횟수

    for (;;) {
      const url = new URL(this.base + path);
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
      }
      if (wantsTo) {
        url.searchParams.set('to', TO_FORMATS[this.toFormat](toSeconds));
        // 상한은 **여기서** 건다. 미리 걸어 두면 조합을 바꿔 다시 해 볼 때
        // 개수가 예전 것 그대로 남는다.
        if (this.toCap !== null && params.count !== undefined) {
          url.searchParams.set('count', String(Math.min(Number(params.count), this.toCap)));
        }
      }

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
        let payload;
        try {
          payload = await response.json();
        } catch {
          throw new UpbitError('업비트 응답을 읽지 못했습니다 (JSON 아님)', 'parse');
        }
        this.succeeded += 1;

        // **아직 통하는 조합을 못 찾았는데 빈 배열이 왔다면 그것도 실패다.**
        //
        // 200 OK에 `[]`는 '더 줄 게 없다'는 뜻이기도 하지만, 첫 `to` 요청부터
        // 그렇다면 그럴 리가 없다 — 비트코인 1분봉은 몇 년치가 있다. 이걸
        // 성공으로 받으면 collect가 조용히 멈추고, 화면에는 아무 설명 없이
        // '받다가 멈췄다'만 남는다. 실제로 그렇게 보였을 수 있다.
        //
        // 한 번이라도 받아 본 뒤(`toProven`)의 빈 배열은 진짜로 끝에 닿은
        // 것이므로 그때는 그대로 받아들인다.
        if (wantsTo && !this.toProven && Array.isArray(payload) && payload.length === 0) {
          last = new UpbitError(
            `업비트가 답은 했는데 봉을 하나도 주지 않았습니다 (${this.toStrategy}).`, 'empty',
          );
          if (this.planAt < this.toPlan.length - 1) { this.planAt += 1; continue; }
          if (attempt >= retries) throw last;
          await sleep(Math.min(2 ** attempt, 16) * 1000);
          attempt += 1;
          continue;
        }

        // 이 조합으로 실제로 받아 봤다. 이제부터는 이것만 쓴다.
        if (wantsTo) this.toProven = true;
        this.streak += 1;
        // 시험 삼아 올려 본 조합이 통했다. 그럼 그게 새 기준이다.
        if (this.fallback !== null) this.fallback = null;
        if (this.streak >= SPEEDUP_AFTER) {
          this.streak = 0;
          this.limiter.speedUp();
          // **개수도 도로 올려 본다.** 더듬는 중에 딸꾹질 한 번으로 10개짜리
          // 조합에 눌러앉으면, 그 뒤로 계속 스무 배 많은 요청을 보내게 된다.
          // 4,812개에서 멈추고 "많이 느리다"는 말이 나온 이유가 여기다.
          if (wantsTo && this.planAt > 0) {
            if (!this.settled) {
              this.fallback = this.planAt;
              this.planAt -= 1;
            } else {
              // 되돌아온 뒤에도 한참 잘 되고 있으면 한 번 더 올려 본다.
              // 영영 안 올리면 딸꾹질 한 번에 스무 배 느린 채로 끝까지 간다.
              this.climbWait += 1;
              if (this.climbWait >= CLIMB_RETRY) {
                this.climbWait = 0;
                this.settled = false;
                this.fallback = this.planAt;
                this.planAt -= 1;
              }
            }
          }
        }
        return payload;
      }

      if (threw) {
        last = await this.diagnose();
        // 재시도해도 소용없는 둘은 곧장 알린다. 특히 'throttled'은 지금
        // 업비트가 우리를 막고 있는 상태라, 조합을 더듬거나 재시도하는 것이
        // **상황을 더 나쁘게** 만든다.
        if (last.kind === 'offline' || last.kind === 'throttled') throw last;
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

      this.streak = 0;

      // 더 빠른 조합을 시험해 보다가 실패했다면, 원래 자리로 돌아가고
      // 다시는 올리지 않는다. 통하던 것을 잃으면 안 된다.
      if (this.fallback !== null) {
        this.planAt = this.fallback;
        this.fallback = null;
        this.settled = true;
        continue;
      }

      // `to` 보내는 법을 아직 못 정했으면 다음 조합을 넣어 본다. 서버가
      // 죽은 것(5xx)은 조합과 무관하므로 그때는 훑지 않는다.
      if (wantsTo && !this.toProven && last.kind !== 'server') {
        if (this.planAt < this.toPlan.length - 1) {
          this.planAt += 1;
          continue;
        }
        // 아홉 조합이 다 안 통했다. 여기서 '조합 문제'라고 결론 내리면 안 된다 —
        // 너무 빨라서 전부 막힌 것일 수도 있다. 느려진 채로 한 번 더 훑어
        // 본다. 이걸로 통하면 조합이 아니라 속도가 문제였던 것이다.
        if (sweeps < TO_SWEEPS) {
          sweeps += 1;
          this.planAt = 0;
          this.limiter.slowDown();
          await sleep(this.sweepPause);
          continue;
        }
      }

      if (attempt >= retries) throw last;
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
    // 실패할 때마다 확인 요청(우리 쪽 + no-cors)을 새로 보내면, 막혀 있는
    // 상황을 오히려 더 악화시킨다. 잠깐은 지난 결과를 그대로 쓴다.
    const now = Date.now();
    if (this.lastDiagnosis && now - this.lastDiagnosis.at < DIAGNOSIS_TTL) {
      return this.lastDiagnosis.error;
    }
    const remember = (error, blocked = false) => {
      this.lastDiagnosis = { at: Date.now(), error };
      // 막혀 있다고 확인되면 그 시각을 남긴다. 그동안은 급하지 않은 요청
      // (맨 위 시세 같은 것)을 아예 보내지 않는다 — 두드릴수록 나빠진다.
      if (blocked) this.blockedAt = Date.now();
      return error;
    };
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      return remember(new UpbitError(
        '인터넷이 끊겨 있습니다. 연결을 확인하고 다시 눌러 주세요.', 'offline',
      ));
    }
    try {
      // 같은 출처라 CORS와 무관하다. 캐시를 피해야 진짜로 나갔다 온 게 된다.
      // ?ping= 이 붙은 요청은 서비스 워커가 캐시로 답하지 않는다(web/sw.js).
      await this.fetch(`./manifest.webmanifest?ping=${Date.now()}`, { cache: 'no-store' });
    } catch {
      return remember(new UpbitError(
        '인터넷이 끊겨 있습니다. 연결을 확인하고 다시 눌러 주세요.', 'offline',
      ));
    }

    // **지금 통째로 막힌 것인가, 이 요청만 거절당한 것인가.**
    //
    // 증거 두 개로 가른다.
    //
    //   1. 평범한 요청(to 없는 현재가)도 실패하는가
    //   2. no-cors로는 닿는가 — 내용은 못 읽어도 답이 왔는지는 알 수 있다
    //
    // 둘 다 그렇다면 업비트까지 갔고 답도 왔는데 그 답을 읽을 수 없다는 뜻이다.
    // 허용 표시(CORS)는 정상 응답에는 붙고 **거절 응답에는 안 붙으므로**,
    // 지금 업비트가 우리를 통째로 거절하고 있는 것이다.
    //
    // 실제로 아이패드 화면이 이랬다 — 현재가까지 전부 실패하는데 no-cors는
    // 124ms 만에 성공했다. 그동안 이걸 "닿지 못했습니다"라고 잘못 말해 왔다.
    //
    // 1번이 성공하면 통째로 막힌 게 아니다. 그때는 이 요청 모양만 문제이므로
    // 조합을 계속 더듬어야 한다 — 여기서 포기하면 찾을 수 있는 것도 못 찾는다.
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
        + '너무 자주 부른 것으로 보고 속도를 낮췄습니다. '
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
   * 급하지 않은 요청은 이 동안 아예 보내지 않는다. 맨 위 시세 하나 때문에
   * 막힌 업비트를 20초마다 두드리면 회복만 늦어진다.
   */
  knownBlocked(within = THROTTLE_PAUSE) {
    return this.blockedAt > 0 && Date.now() - this.blockedAt < within;
  }

  /** 가장 평범한 요청(to도 없고 개수도 1개)이 되는가. */
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
        // 막힌 게 아니라 삐끗한 것이면 속도를 낮춰 본다. 막힌 상태에서는
        // 속도를 낮춰도 소용없다 — 이미 통째로 거절당하는 중이다.
        if (!banned) this.limiter.slowDown();

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
