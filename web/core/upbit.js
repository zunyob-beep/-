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
 * 업비트 시세 API의 공개 한도는 초당 10회다. 그런데 **한도 안이어도 막힌다.**
 * 5로 내리고도 막혔다는 말을 들었다. 휴대폰 데이터에서는 한 주소를 수백 명이
 * 나눠 쓰기 때문에, 우리가 한도의 절반만 써도 남들이 쓴 몫까지 합쳐 넘어간다.
 * 즉 우리가 지킬 수 있는 건 한도가 아니라 **우리 몫을 얼마나 작게 하느냐**다.
 *
 * 3으로 둔다. 지금까지 4년치 받기를 **끝까지 마친 유일한 속도**다. 그보다
 * 빠르게 두었을 때는 매번 중간에 막혔다. 200개씩 받으므로 3이면 초당 600봉,
 * 하루치(1,440봉)가 2.4초다 — 느려서 못 쓸 속도가 아니다.
 * 봉을 받는 것도, 맨 위 시세도, 확인 요청도 전부 이 예산을 나눠 쓴다 —
 * 시세는 20초에 한 번이라 사실상 봉 받기가 거의 다 쓴다.
 *
 * 예전에는 이게 지켜지지 않았다. 화면과 워커에 클라이언트가 따로 있어
 * 제한기가 둘이었고, 서로를 몰랐다. 그래서 "초당 3회"라고 적어 놓고 실제로는
 * 그보다 많이 나갔다. 이제 나가는 길이 하나뿐이라 이 숫자가 곧 사실이다.
 *
 * **막히면 여기만 낮추면 된다.** 다른 곳을 고칠 필요가 없다.
 */
export const PER_SECOND = 3;

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

/**
 * **업비트까지 가는 길.**
 *
 * 왜 이게 필요한가 — 진단표 세 장이 같은 말을 했다.
 *
 *     안 됨 | 봉 1개 (to 없음)      | TypeError: Load failed
 *     안 됨 | 현재가 ①②③          | TypeError: Load failed
 *     됨    | 같은 주소를 no-cors로 | 닿았습니다 (128ms)
 *
 * no-cors가 128ms에 닿는다는 건 **업비트 서버는 멀쩡히 답한다**는 뜻이다.
 * 그런데 브라우저가 읽을 수 있는 요청은 거의 다 실패한다. 그리고 5G에서
 * 와이파이로 바꿔도 똑같았다 — 우리 주소가 차단당한 게 아니라는 뜻이다.
 *
 * 즉 **브라우저에서 직접 부르는 길 자체가 막혀 있다.** 초당 회수를 낮추든
 * 재시도를 늘리든 고칠 수 없는 종류다. 이틀 동안 그 위에서 조절만 했다.
 *
 * 브라우저가 못 가져오면 **브라우저가 아닌 곳을 거쳐서** 가져오면 된다.
 * 우회 서버는 업비트에 서버끼리 물어보고, 그 답에 브라우저가 요구하는 허용
 * 표시를 붙여서 돌려준다. 우리가 보내는 건 '어느 종목, 어느 시각'뿐이고
 * 계정도 키도 없으므로, 남에게 보여서 곤란한 것이 없다.
 *
 * **직접이 먼저다.** 되면 아무 데도 안 거친다. 안 될 때만 돌아간다.
 */
export const ROUTES = [
  { id: 'direct', label: '직접', wrap: (url) => url },
  {
    id: 'allorigins',
    label: 'allorigins.win',
    wrap: (url) => `https://api.allorigins.win/raw?url=${encodeURIComponent(url)}`,
  },
  {
    id: 'codetabs',
    label: 'codetabs.com',
    wrap: (url) => `https://api.codetabs.com/v1/proxy?quest=${encodeURIComponent(url)}`,
  },
];

/**
 * 우회 서버가 **자기 사정으로** 거절할 때 돌려주는 것들.
 *
 * corsproxy.io를 목록에서 뺀 이유가 이것이다 — 유료로 바뀌어 401을 준다.
 * 업비트 공개 시세는 이 코드들을 쓰지 않으므로, 우회로 가는 중에 이게 오면
 * 업비트가 아니라 그 우회 서버가 우리를 막는 것이다.
 */
export const PROXY_REFUSED = new Set([401, 402, 403, 407, 429]);

/**
 * 직접 길이 이만큼 연달아 실패하면 우회로 넘어간다.
 *
 * 한 번 실패는 아무 정보가 아니다(통과가 섞인 망도 있었다). 세 번 연달아
 * 실패하면 그건 이 길이 막힌 것이다.
 */
export const SWITCH_AFTER = 3;

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
 * **한 쪽을 몇 번까지 다시 해 보는가.**
 *
 * 진단표가 말해 준 것: 이 망은 완전히 막힌 게 아니라 **7번 중 1번은
 * 통과**한다. 현재가든 봉이든, 순서가 앞이든 뒤든 상관없이 그랬다.
 *
 * 그렇다면 한 번 실패한 건 아무 정보가 아니다. 그냥 다시 하면 된다.
 * 12번이면 (6/7)^12 ≈ 16%만 남으므로 대부분의 쪽이 통과한다.
 */
export const RETRIES = 12;

/** 다시 하기 전에 쉬는 시간. 회를 거듭해도 늘리지 않는다. */
export const RETRY_PAUSE = 2000;

/**
 * **아직 한 번도 못 받았을 때는** 몇 번까지만 해 보는가.
 *
 * 12번은 '길이 열려 있다'는 증거가 있을 때 쓰는 숫자다. 한 번도 못 받은
 * 상태라면 정말 막혀 있는 것일 수도 있고, 그때 12번을 두드리는 건 차단을
 * 연장시킬 뿐이다. 그래서 증거가 생기기 전까지는 조심스럽게만 해 본다.
 *
 * 한 쪽만 통과하면 그때부터는 12번을 쓴다.
 */
export const FIRST_RETRIES = 6;

/**
 * **최근에 받아 온 적이 있으면 '막혔다'고 보지 않는다.**
 *
 * 이게 v27의 잘못을 막는 자물쇠다. 그때 나는 실패 한 번을 차단으로 보고
 * 몇 분씩 입을 다물게 만들었다. 완전히 막힌 주소라면 옳지만, 통과가 섞여
 * 있는 망에서는 **통과했을 요청까지 안 보내는** 것이라 정확히 반대로 작동한다.
 * 실제로 7일치 받기가 5시간에서 15시간으로 늘었다.
 *
 * 받아 온 게 있다면 길은 열려 있다. 그때는 조용히 있을 게 아니라 계속
 * 두드려야 한다. 입을 다무는 건 **한 번도 못 받고 있을 때**만이다.
 *
 * 2분으로 둔다. 통과가 섞인 망에서는 14초에 한 쪽씩 들어오므로 넉넉하고,
 * 정말 캄캄해졌을 때는 2분이면 알 수 있다. 이보다 길게 두면 이미 꺼진 길을
 * 붙들고 몇 분을 더 두드리게 되고, 사용자는 그동안 답을 못 듣는다.
 */
export const WORKED_RECENTLY = 120000;

/**
 * **막혔을 때 입을 다무는 시간.** 이 앱에서 가장 중요한 숫자다.
 *
 * 왜 이게 제일 중요한가
 * --------------------
 * 업비트는 한도를 넘긴 주소를 초 단위가 아니라 **몇 분씩** 막는다. 그리고
 * 막혀 있는 동안 들어오는 요청은 대개 **차단을 연장시킨다.** 즉 막힌 뒤에
 * 계속 두드리면 영영 안 풀린다.
 *
 * 그런데 우리가 정확히 그러고 있었다. 실제로 세어 봤다.
 *
 *     시세 한 번 부르기 → 업비트로 3번 (요청 1 + 진단 2)
 *     막힌 걸 아는 상태에서 또 부르기 → 그래도 1번이 나간다
 *
 * 맨 위 시세는 20초마다 돈다. '막힌 걸 안다'가 1분뿐이었으므로, 1분에 한 번씩
 * 3번이 나갔다 — **시간당 180번.** 아무것도 안 하고 앱만 켜 둔 밤새 천 번이
 * 넘는다. 그 주소를 업비트가 이미 거절하고 있는데도.
 *
 * 그래서 규칙을 바꾼다.
 *
 *   1. 막힌 걸 알면 **아예 안 보낸다.** 보내고 실패하는 게 아니라, 보내지
 *      않고 곧장 "아직 막혀 있습니다"라고 답한다. 나가는 요청은 0이다.
 *   2. 조용히 있는 시간을 **막힐 때마다 늘린다.** 1 → 2 → 5 → 10 → 20 → 30분.
 *      한 번이라도 성공하면 처음으로 되돌린다.
 *
 * 이건 사용자를 기다리게 하려는 게 아니라, **풀릴 틈을 주려는 것**이다.
 * 두드리지 않는 것 말고 우리가 할 수 있는 일이 없다.
 */
export const QUIET_STEPS = [60000, 120000, 300000, 600000, 1200000, 1800000];

/**
 * 받는 중에 막혔을 때 **몇 번까지 기다렸다 이어 받을지.**
 *
 * 위 표의 앞 네 칸을 쓰므로 합쳐서 18분이다. 그 뒤로도 안 풀리면 사람에게
 * 말한다 — 한 시간을 말없이 붙들고 있는 것보다 낫다. 받은 만큼은 저장돼
 * 있으므로 나중에 다시 눌러도 이어서 받는다.
 */
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
    base = API_BASE, retries = RETRIES, perSecond = PER_SECOND, fetcher = null,
    stallPause = STALL_PAUSE, quietSteps = QUIET_STEPS, retryPause = RETRY_PAUSE,
    routes = ROUTES, myProxy = null,
  } = {}) {
    this.base = base.replace(/\/$/, '');
    this.retries = retries;
    this.stallPause = stallPause;
    this.retryPause = retryPause;
    this.quietSteps = quietSteps;
    this.limiter = new RateLimiter(perSecond);
    // 테스트에서 갈아끼울 수 있게 둔다. 진짜 업비트를 부르는 테스트는
    // 만들 수 없다 — 값이 매번 달라서 무엇과도 대조할 수 없다.
    this.fetch = fetcher ?? ((...args) => globalThis.fetch(...args));
    /** 지금까지 업비트에서 실제로 받아 온 횟수. 진단이 이걸 본다. */
    this.succeeded = 0;
    /** 마지막 진단 결과. 실패마다 다시 물어보지 않으려고 잠깐 들고 있는다. */
    this.lastDiagnosis = null;
    /** 이 시각까지는 업비트에 **아무것도 안 보낸다** (유닉스 밀리초). */
    this.blockedUntil = 0;
    /** 연달아 몇 번 막혔는가. 조용히 있을 시간을 여기서 고른다. */
    this.banLevel = 0;
    /** 마지막으로 실제로 받아 온 시각. 이게 있으면 길이 열려 있는 것이다. */
    this.lastSuccessAt = 0;

    // **업비트까지 가는 길.** 직접이 먼저고, 안 되면 우회한다.
    //
    // 사용자가 자기 우회 주소를 적어 두면 그게 맨 앞에 선다 — 남의 서버를
    // 안 거치는 편이 언제나 낫고, 자기 것이면 한도도 자기 몫이다.
    this.routes = myProxy
      ? [{ id: 'mine', label: '내 우회 주소', wrap: (url) => myProxy.replace('{url}', encodeURIComponent(url)) },
        ...routes]
      : [...routes];
    /** 지금 쓰는 길. 통하는 길을 찾으면 거기 머문다. */
    this.route = 0;
    /** 이 길로 연달아 실패한 횟수. */
    this.routeMisses = 0;
  }

  /** 지금 어느 길로 가고 있는지. 화면이 이걸 보여준다. */
  get routeLabel() {
    return this.routes[this.route]?.label ?? '직접';
  }

  /**
   * 이 길이 안 통한다. **다음 길로 넘어간다.**
   *
   * 넘어가면 실패 횟수를 0으로 되돌린다 — 새 길은 새로 세야 한다.
   * 마지막 길까지 갔으면 더 갈 데가 없으므로 거기 머문다(거짓을 돌려준다).
   */
  nextRoute() {
    if (this.route >= this.routes.length - 1) return false;
    this.route += 1;
    this.routeMisses = 0;
    // 길이 바뀌었으니 옛 판단은 버린다. '막혔다'는 앞 길 이야기다.
    this.lastDiagnosis = null;
    this.blockedUntil = 0;
    this.banLevel = 0;
    return true;
  }

  /**
   * 막혔다고 확인했다. **조용히 있을 시간을 한 칸 늘린다.**
   *
   * 연달아 막힐수록 길게 쉰다. 짧게 쉬고 다시 두드리면 차단이 연장될 뿐이라,
   * 안 풀리는 상황에서 두드리는 횟수를 스스로 줄이는 것이 유일한 수단이다.
   */
  markBlocked() {
    const step = this.quietSteps[Math.min(this.banLevel, this.quietSteps.length - 1)];
    this.banLevel += 1;
    this.blockedUntil = Date.now() + step;
  }

  /**
   * 한 번이라도 받아 왔다. **처음으로 되돌린다.**
   *
   * 이게 없으면 새벽에 한 번 막힌 것 때문에 아침 내내 30분씩 쉬게 된다.
   */
  markWorking() {
    this.banLevel = 0;
    this.blockedUntil = 0;
    this.lastSuccessAt = Date.now();
  }

  /**
   * 최근에 실제로 받아 온 적이 있는가.
   *
   * 있으면 길은 열려 있다 — 지금 실패한 건 통과율이 낮아서지 막혀서가
   * 아니다. 그때 입을 다물면 통과했을 요청까지 안 보내게 된다.
   */
  recentlyWorked() {
    return this.lastSuccessAt > 0 && Date.now() - this.lastSuccessAt < WORKED_RECENTLY;
  }

  /** 지금 막혀 있다고 알고 있는가. 그동안은 한 번도 안 보낸다. */
  knownBlocked() {
    return Date.now() < this.blockedUntil;
  }

  /** 다시 말 걸어도 되기까지 남은 초. 화면이 이 숫자를 보여준다. */
  quietLeft() {
    return Math.max(0, Math.ceil((this.blockedUntil - Date.now()) / 1000));
  }

  /** 아직 조용히 있어야 할 때 던질 오류. 보내지 않고 만든다. */
  stillBlocked() {
    const left = this.quietLeft();
    const say = left >= 60 ? `${Math.ceil(left / 60)}분` : `${left}초`;
    return new UpbitError(
      `업비트가 아직 우리 요청을 막고 있습니다. ${say} 뒤에 다시 시도합니다 — `
      + '그때까지는 두드리지 않습니다. 자꾸 두드리면 차단이 길어집니다.',
      'throttled',
    );
  }

  /**
   * 한 번 물어본다. 실패하면 무엇 때문인지 가려서 던진다.
   *
   * `toSeconds`가 있으면 `to`를 붙인다. 표기는 문서에 있는 하나만 쓴다.
   */
  async get(path, params = {}, toSeconds = null, { retries = this.retries, onRetry = null } = {}) {
    let last = null;

    // **막힌 걸 알면 보내지 않는다.**
    //
    // 예전에는 여기가 없어서, 막힌 걸 뻔히 알면서도 일단 보내고 실패했다.
    // 그 한 번이 차단을 연장시킨다. 보내지 않는 것이 지금 할 수 있는 유일한
    // 일이므로, 확인을 요청보다 **앞**에 둔다.
    if (this.knownBlocked()) throw this.stillBlocked();

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
        response = await this.fetch(
          this.routes[this.route].wrap(url.toString()), { cache: 'no-store' },
        );
      } catch {
        // **인터넷이 끊긴 것은 다시 해 봐야 소용없다.**
        //
        // 그건 우리 쪽 파일 하나로 알 수 있고, 그 요청은 업비트로 나가지
        // 않는다. 이걸 먼저 안 거르면 비행기 모드에서 12번을 다시 하느라
        // 1분을 쓰고 나서야 "인터넷이 끊겼습니다"라고 말하게 된다.
        //
        // 쪽마다 첫 실패에서만 확인한다. 실패할 때마다 확인하면 같은 답을
        // 수천 번 받으려고 우리 서버를 두드리는 셈이다.
        if (attempt === 0 && !(await this.online())) throw await this.diagnose();

        // **이 길이 막힌 것이면 다른 길로 간다.**
        //
        // 같은 길을 열두 번 두드리는 것보다 다른 길을 한 번 가 보는 게 낫다.
        // 브라우저에서 직접 부르는 길이 통째로 막혀 있던 것이 지금까지의
        // 진짜 원인이었고, 그건 몇 번을 다시 해도 안 뚫린다.
        this.routeMisses += 1;
        if (this.routeMisses >= SWITCH_AFTER && this.nextRoute()) {
          if (onRetry) onRetry(0, retries, this.routeLabel);
          // 새 길에는 **재시도 횟수를 새로 준다.** 앞 길에서 쓴 횟수를 물려받으면
          // 길이 셋인데 예산이 모자라 마지막 길은 가 보지도 못한다.
          attempt = -1;
          // eslint-disable-next-line no-continue
          continue;
        }

        // **한 번 실패했다고 무슨 일인지 알아보러 가지 않는다.**
        //
        // 예전에는 여기서 곧장 diagnose()를 불렀다. 그게 두 가지를 망쳤다.
        //
        //   · 실패 한 번의 값이 3배가 된다 (요청 1 + 진단 2)
        //   · throttled로 판정되면 **재시도 없이 곧장 포기**한다
        //
        // 그런데 진단표를 보니 이 망은 완전히 막힌 게 아니라 **7번 중 1번은
        // 통과**한다. 그 상태에서 한 번 실패했다고 포기하면, 통과했을 요청을
        // 스스로 안 보내는 셈이다. 실제로 7일치 받는 데 15시간이 걸렸다.
        //
        // 그러니 먼저 **그냥 다시 해 본다.** 몇 번 해 보고도 안 되면 그때
        // 무슨 일인지 알아본다. 진단은 마지막에 한 번이면 된다.
        // 증거가 있는 만큼만 끈질기게 한다. **최근에** 받아 봤으면 길이
        // 열려 있는 것이니 12번, 아니면 6번까지만.
        //
        // 누적 성공 횟수가 아니라 '최근'을 본다. 누적은 줄어들지 않으므로,
        // 그걸 기준으로 삼으면 한 번 받아 본 뒤로는 길이 캄캄해져도 영영
        // 12번씩 두드리게 된다 — 증거가 사라져도 그 증거로 계속 행동하는 셈이다.
        const budget = this.recentlyWorked() ? retries : Math.min(retries, FIRST_RETRIES);
        if (attempt < budget) {
          // **쉬는 시간은 회를 거듭해도 늘리지 않는다.**
          //
          // 처음에는 2 → 3 → 4 → … → 10초로 늘렸다. 근거 없이 넣은 것이었고,
          // 실제로는 한 쪽에 84초가 걸려서 화면이 몇 분씩 멈춘 것처럼 보이는
          // 원인이 됐다. 통과율이 일정한 망이라면 늘릴 이유가 없다 — 고르게
          // 2초씩 두드리는 편이 더 빨리 끝나고 두드리는 횟수도 같다.
          //
          // **다시 해 보는 중이라는 걸 화면이 말해야 한다.**
          //
          // 이게 없어서 화면에 "받는 중…"만 몇 분씩 떠 있었다. 사용자 눈에는
          // 앱이 죽은 것과 구분이 안 된다 — 실제로 "아예 읽어오지를 못해"라는
          // 말을 들었는데, 그때 앱은 조용히 재시도하는 중이었다.
          if (onRetry) onRetry(attempt + 1, budget);
          await sleep(this.retryPause);
          // eslint-disable-next-line no-continue
          continue;
        }
        last = await this.diagnose();
        throw last;
      }

      if (response.status < 400) {
        let payload;
        try {
          payload = await response.json();
        } catch {
          throw new UpbitError('업비트 응답을 읽지 못했습니다 (JSON 아님)', 'parse');
        }
        this.succeeded += 1;
        this.routeMisses = 0;
        // 통했다. 쌓아 둔 차단 기억을 지운다 — 안 그러면 새벽에 한 번 막힌
        // 것 때문에 아침 내내 30분씩 쉰다.
        this.markWorking();
        return payload;
      }

      // **우회 서버 자신이 거절한 것이면 다른 길로 간다.**
      //
      // 실제로 이걸 놓쳐서 한 번 막혔다. corsproxy.io가 유료로 바뀌면서
      // 401 {"error":"A valid API key is required"} 를 돌려줬는데, 그건
      // **성공한 HTTP 응답**이라 아래 '거부' 갈래로 곧장 떨어졌다. 길 바꾸기는
      // 네트워크 실패(catch)에서만 일어나고 있었으므로, 다음 우회는 가 보지도
      // 못하고 끝났다.
      //
      // 업비트는 공개 시세에 401·402·403·407을 쓰지 않는다. 우회로 가는 중에
      // 이런 답이 오면 그건 업비트가 아니라 **그 우회 서버가** 우리를 막는
      // 것이고, 몇 번을 다시 해도 안 뚫린다. 곧장 다음 길로 간다.
      const mine = this.routes[this.route].id !== 'direct';
      if (mine && (PROXY_REFUSED.has(response.status) || response.status >= 500)) {
        if (this.nextRoute()) {
          if (onRetry) onRetry(0, retries, this.routeLabel);
          attempt = -1;
          // eslint-disable-next-line no-continue
          continue;
        }
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
    // 진단 한 번에 업비트로 두 번 나간다(평범한 요청 + no-cors). 차단 동안
    // 그걸 반복하면, 확인하려다 풀릴 틈을 없앤다. 확인도 두드리는 것이다.
    if (this.knownBlocked()) return this.lastDiagnosis?.error ?? this.stillBlocked();
    const remember = (error, blocked = false) => {
      this.lastDiagnosis = { at: Date.now(), error };
      // **받아 온 게 있으면 입을 다물지 않는다.** v27에서 이걸 빠뜨려서
      // 통과가 섞인 망인데도 몇 분씩 조용히 있었고, 받는 시간이 3배가 됐다.
      if (blocked && !this.recentlyWorked()) this.markBlocked();
      return error;
    };
    const offline = () => new UpbitError(
      '인터넷이 끊겨 있습니다. 연결을 확인하고 다시 눌러 주세요.', 'offline',
    );

    if (!(await this.online())) return remember(offline());

    // **한 번 막힌 걸 확인했으면, 다시 확인하는 데 두 번을 더 쓰지 않는다.**
    //
    // 조용히 있다가 시간이 되어 한 번 보냈는데 또 실패했다면, 답은 뻔하다 —
    // 아직 막혀 있는 것이다. 그걸 확인하겠다고 업비트로 두 번을 더 보내면
    // 재시도 한 번의 값이 3배가 되고, 그 3배가 차단을 연장시킨다.
    //
    // 인터넷이 끊긴 경우는 바로 위에서 이미 걸렀다(우리 쪽 파일 받아 보기).
    // 업비트로는 한 번도 안 나간다.
    if (this.banLevel > 0) {
      return remember(new UpbitError(
        '업비트가 아직 우리 요청을 막고 있습니다. 조금 더 기다렸다 다시 시도합니다.',
        'throttled',
      ), true);
    }

    if (!(await this.candlesWork())) {
      // **현재가는 되는데 봉만 안 되는가.**
      //
      // 그렇다면 차단이 아니다. 차단이라면 현재가도 같이 막힌다. 기다려서
      // 풀릴 문제가 아니므로 기다리라고 말하면 안 되고, 조용히 있는 시간을
      // 늘려도 소용없다 — 그래서 blocked 표시를 하지 않는다.
      if (await this.tickerWorks()) {
        return remember(new UpbitError(
          '업비트가 봉(과거 시세) 주소만 브라우저에 안 열어 주고 있습니다. '
          + '현재가는 받아지는데 봉만 거절당합니다 — 기다려도 풀리지 않는 종류입니다.',
          'candles',
        ));
      }
      if (await this.reaches()) {
        return remember(new UpbitError(
          '업비트가 지금 우리 요청을 막고 있습니다. 업비트까지는 갔고 답도 왔지만 '
          + '브라우저가 그 답을 읽을 수 없습니다 — 거절당했을 때 그렇습니다. '
          + '요청이 잦아서 잠시 막힌 것일 수 있습니다. 지금부터는 두드리지 않고 '
          + '기다립니다 — 막혀 있는 동안 계속 두드리면 차단이 길어지기 때문입니다.',
          'throttled',
        ), true);
      }
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
   * 인터넷 자체가 살아 있는가. **업비트로는 한 번도 안 나간다.**
   *
   * 우리 쪽 파일을 하나 불러 본다. 같은 출처라 CORS와 무관하고, `?ping=`이
   * 붙은 요청은 서비스 워커가 캐시로 답하지 않는다(web/sw.js) — 그래야
   * 진짜로 나갔다 온 것이 된다.
   */
  async online() {
    if (typeof navigator !== 'undefined' && navigator.onLine === false) return false;
    try {
      await this.fetch(`./manifest.webmanifest?ping=${Date.now()}`, { cache: 'no-store' });
      return true;
    } catch {
      return false;
    }
  }

  /** 그 주소가 되는가. 던지지 않고 참·거짓으로만 답한다. */
  async works(path, params) {
    try {
      const url = new URL(this.base + path);
      for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
      // **지금 쓰는 길로 물어본다.** 직접으로만 물어보면, 우회로 잘 받고
      // 있는 중에도 "안 됩니다"라고 답하게 된다 — 쓰지도 않는 길을 진단하는 셈이다.
      const response = await this.fetch(
        this.routes[this.route].wrap(url.toString()), { cache: 'no-store' },
      );
      return response.status < 400;
    } catch {
      return false;
    }
  }

  /**
   * **우리가 실제로 필요한 것**이 되는가 — 가장 단순한 봉 요청.
   *
   * 예전에는 여기서 현재가(`/v1/ticker`)를 물어봤다. 그게 틀렸다. 현재가는
   * 잘 되는데 봉만 안 되는 상태가 실제로 있었고(진단표가 그걸 보여줬다),
   * 그때 이 함수는 "잘 된다"고 답했다. 그래서 진단이 '막힘'이 아니라
   * '못 닿음'으로 흘러가 버렸고, 화면에는 "업비트에 닿지 못했습니다"라는
   * **사실이 아닌 말**이 떴다. 되는 것을 물어보고 안 되는 것을 판단할 수는 없다.
   */
  async candlesWork() {
    return this.works(ENDPOINTS.minute1, { market: 'KRW-BTC', count: 1 });
  }

  /** 현재가는 되는가. 봉만 거절당하는 상태를 가려내는 데 쓴다. */
  async tickerWorks() {
    return this.works('/v1/ticker', { markets: 'KRW-BTC' });
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
  async getCandles(market, timeframe, count = PAGE, to = null, { onRetry = null } = {}) {
    const path = ENDPOINTS[timeframe];
    if (!path) {
      throw new Error(`모르는 봉 간격 '${timeframe}'. 사용 가능: ${Object.keys(ENDPOINTS).join(', ')}`);
    }
    const params = { market, count: Math.min(Math.max(count, 1), PAGE) };
    const rows = (await this.get(path, params, to, { onRetry })) ?? [];
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
        batch = await this.getCandles(market, timeframe, asking, cursor, {
          onRetry: onProgress
            ? (tried, of) => onProgress(got, count, { retrying: tried, of })
            : null,
        });
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
        // **'막혔다'는 판정을 증거로 한 번 더 거른다.**
        //
        // 최근에 받아 온 게 있으면 길은 열려 있는 것이다. 통과율이 낮아서
        // 실패했을 뿐이니 몇 분씩 쉴 이유가 없다 — 짧게 쉬고 다시 한다.
        // 몇 분씩 쉬는 건 **한 번도 못 받고 있을 때**만이다.
        const banned = (kind === 'throttled' || kind === 'rate') && !this.recentlyWorked();
        const keepTrying = got > 0 || this.recentlyWorked();
        const canWait = kind !== null && kind !== 'offline' && kind !== 'blocked'
          && (banned ? stalls < THROTTLE_RETRIES : keepTrying && stalls < STALL_RETRIES);
        if (!canWait) throw error;
        stalls += 1;
        // **얼마나 쉴지는 클라이언트가 정한다.**
        //
        // 막힌 것은 blockedUntil이 이미 잡혀 있다. 여기서 따로 세면 두 시계가
        // 어긋나서, 짧은 쪽이 끝나자마자 보내려다 긴 쪽에 막혀 아무 일도
        // 안 하고 시도만 한 번 날린다. 조용히 있을 시간은 **한 곳에서만** 센다.
        const until = banned
          ? Math.max(this.blockedUntil, Date.now())
          : Date.now() + this.stallPause * stalls;
        // 몇 분을 아무 표시 없이 쉬면 멈춘 것으로 보인다. 1초마다 남은
        // 시간을 알려서, 기다리는 중이라는 걸 보이게 한다.
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
