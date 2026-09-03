// **미리 받아 둔 봉을 읽어 온다.** 이 앱이 도는 주된 길이 여기다.
//
// 왜 업비트를 직접 안 부르나
// -------------------------
// 브라우저에서 부르면 **분당 6번**이다. 업비트가 브라우저 요청(Origin 헤더가
// 붙은 요청)을 `origin`이라는 별도 묶음에 넣기 때문이고, 헤더에 대놓고 적혀
// 있다 — 서버에서 부르면 같은 주소가 분당 600번이다.
//
//     Origin 없이   → remaining-req: group=candles; min=600; sec=9
//     Origin 붙여서 → remaining-req: group=origin;  min=6;   sec=0
//
// 분당 6번이면 7일치(51번)에 8분, 4년치(10,512번)에 **29시간**이다.
// 브라우저에서 받는 건 애초에 될 일이 아니었다.
//
// 그래서 깃허브 액션이 서버에서 미리 받아 파일로 적어 둔다. 앱은 그 파일을
// 읽는다. 업비트는 **마지막 몇 분**을 채우는, 되면 좋은 자리로 내려갔다.
//
// 왜 조각으로 나눴나
// ----------------
// 4년치는 한 종목에 2,102,400봉이다. 한 파일로 만들면 143MB이고 깃허브
// 파일 상한(100MB)을 넘는다. 아이패드에 한 번에 내려받게 할 크기도 아니다.
//
// 그래서 바뀌는 것과 안 바뀌는 것을 나눈다.
//
//     tail/<종목>.json      최근 2일   10분마다 새로       ~90KB
//     recent/<종목>.json    최근 31일  한 시간에 한 번쯤   ~1.2MB
//     <종목>/<YYYY-MM>.json 지나간 달  한 번 적고 그대로   ~1.2MB
//
// 가운데를 '이번 달'이 아니라 **최근 31일**로 잡았다. 달로 자르면 1일 0시에
// 그 파일이 몇 줄로 줄어드는데 지난달 조각은 아직 없어서, 그 사이에 30일치를
// 고른 사람이 이틀치밖에 못 받는다. 31일 창은 지난달을 늘 덮으므로 그
// 구멍이 안 생긴다.
//
// 앞의 둘은 `data` 브랜치에 덮어쓰므로 저장소가 안 불어나고, 지나간 달은
// `history` 브랜치에 쌓인다 — 안 바뀌므로 각 파일이 딱 한 번 올라간다.
//
// **앱은 고른 기간만큼만 내려받는다.** 7일이면 꼬리 하나, 1년이면 열두
// 조각, 4년이면 마흔여덟. 한 번 받은 조각은 이 기기에 남으므로 다시 안 받는다.

/** 꼬리 파일이 담는 날 수. 서버(tools/candles.py)와 맞춰야 한다. */
export const TAIL_DAYS = 2;

const DAY_BARS = 1440;
const STEP = 60;

/**
 * 파일이 있을 만한 자리들. 순서대로 물어보고 **먼저 되는 것**을 쓴다.
 *
 * 1. 같은 주소 아래 `./<브랜치>/…`
 *    같은 곳에서 온 파일이라 CORS라는 개념 자체가 없다 — 막힐 구석이 없다.
 *    시험이 여기에 가짜 파일을 놓고 돈다.
 *
 * 2. raw.githubusercontent.com
 *    실제로 파일이 사는 곳. 여기는 아무 데서나 읽으라고 허용 표시를 붙여
 *    주므로 브라우저가 그대로 읽는다 — 업비트와 달리.
 *
 * 주소는 **지금 열려 있는 주소에서 뽑는다.** 저장소 이름을 코드에 박아 두면
 * 남이 갈라 갔을 때(fork) 남의 데이터를 읽게 된다.
 */
export function fileUrls(branch, path, where) {
  const urls = [new URL(`./${branch}/${path}`, where.href).toString()];
  const host = String(where.hostname || '');
  if (host.endsWith('.github.io')) {
    const owner = host.slice(0, -'.github.io'.length);
    // `/-/worker.js` → 저장소 이름은 `-`. 사용자 페이지(owner.github.io)면
    // 저장소 이름이 곧 호스트 이름이다.
    const first = String(where.pathname || '/').split('/').filter(Boolean)[0];
    const repo = first && !first.includes('.') ? first : host;
    urls.push(`https://raw.githubusercontent.com/${owner}/${repo}/${branch}/${path}`);
  }
  return urls;
}

/**
 * 작게 적어 둔 것을 봉으로 되돌린다.
 *
 * 형식은 tools/pack.py에 적어 뒀다. 요약하면 **열 단위 + 차이값**이다 —
 * 봉마다 배열을 만들면 껍데기가 봉 수만큼 늘고, 1분 사이 가격은 거의 안
 * 변해서 차이만 적으면 자릿수가 여섯에서 두셋으로 준다. 정확도를 잃지
 * 않으려고 정수(원 × 100)로 더해 나간다.
 */
export function unpackSeed(payload) {
  if (!payload || typeof payload !== 'object') return null;

  // 옛 형식(봉마다 배열 하나)도 읽는다. 갈아타는 동안 섞여 있을 수 있다.
  if (Array.isArray(payload.rows)) {
    const out = [];
    for (const row of payload.rows) {
      if (!Array.isArray(row) || row.length < 6) continue;
      const [ts, open, high, low, close, volume] = row.map(Number);
      if (Number.isFinite(ts) && Number.isFinite(close)) {
        out.push({ ts, open, high, low, close, volume });
      }
    }
    return out;
  }

  const gaps = payload.t;
  if (!Array.isArray(gaps)) return null;
  // **칸이 하나라도 짧으면 안 쓴다.**
  //
  // 중간에 끊긴 파일을 그대로 읽으면 `undefined`가 NaN이 되어 조용히
  // 흘러든다. 가격에 NaN이 섞이면 계산은 그대로 돌고 화면에도 숫자가
  // 찍히는데, 그게 무엇을 뜻하는지는 아무도 모른다.
  for (const key of ['c', 'o', 'h', 'l', 'v']) {
    if (!Array.isArray(payload[key]) || payload[key].length < gaps.length) return null;
  }
  const step = Number(payload.step) || STEP;
  // 배수는 파일이 알려 준다. 정수로 딱 떨어지는 종목은 1이고, 잘게
  // 쪼개지는 종목만 10이나 100이다 (tools/pack.py의 pick_scale).
  const scale = Number(payload.scale) || 1;
  const out = new Array(gaps.length);
  let ts = Number(payload.from) || 0;
  let close = 0;
  for (let i = 0; i < gaps.length; i += 1) {
    ts = i === 0 ? ts : ts + Number(gaps[i]) * step;
    close = i === 0 ? Number(payload.c[i]) : close + Number(payload.c[i]);
    out[i] = {
      ts,
      open: (close + Number(payload.o[i])) / scale,
      high: (close + Number(payload.h[i])) / scale,
      low: (close + Number(payload.l[i])) / scale,
      close: close / scale,
      volume: Number(payload.v[i]),
    };
  }
  return out;
}

/**
 * 파일 하나를 읽는다. 못 읽으면 `null` — **던지지 않는다.**
 *
 * `io.from`이 **어느 자리가 통했는지 기억한다.** 이게 없으면 4년치를 받을 때
 * 조각마다 안 되는 자리를 먼저 물어보게 된다 — 깃허브 페이지에는 `./data/`가
 * 없으므로 조각 48개마다 404가 한 번씩, 쓸데없는 왕복 48번이다.
 */
async function readFile(branch, path, io) {
  const urls = fileUrls(branch, path, io.where);
  // 통했던 자리를 앞으로 당긴다. 아직 모르면 적힌 순서 그대로.
  const order = io.from ? [...urls].sort((a, b) => (b.startsWith(io.from) ? 1 : 0) - (a.startsWith(io.from) ? 1 : 0)) : urls;
  for (const url of order) {
    try {
      // eslint-disable-next-line no-await-in-loop
      const response = await io.fetcher(url, { cache: 'no-store' });
      if (!response || response.status >= 400) { io.onTry?.(url, `${response?.status}`); continue; }
      // eslint-disable-next-line no-await-in-loop
      const payload = await response.json();
      io.onTry?.(url, 'ok');
      io.from = url.slice(0, url.length - `${branch}/${path}`.length);
      return payload;
    } catch (error) {
      io.onTry?.(url, error?.name === 'SyntaxError' ? 'JSON 아님' : '못 읽음');
    }
  }
  return null;
}

/** 그 조각이 담고 있는 봉이 맞는 종목인지. 다른 종목으로 계산하면 큰일 난다. */
const isFor = (payload, market) => (payload?.m ?? payload?.market) === market;

/**
 * **미리 받아 둔 봉을 필요한 만큼 읽어 온다.**
 *
 * 조각을 받을 때마다 `onChunk`로 넘긴다 — 다 모아 뒀다가 한 번에 주면
 * 4년치(210만 봉)가 통째로 메모리에 쌓여 아이패드에서 브라우저가 죽는다.
 *
 * `wanted`
 *     몇 봉이 필요한가. 이만큼 채워지면 더 안 받는다. 7일이면 꼬리 하나로
 *     끝나고, 4년이면 마흔여덟 조각까지 내려간다.
 *
 * `oldest`
 *     이미 이 기기에 있는 가장 오래된 봉의 시각. 그보다 새 조각은 이미
 *     가진 것이므로 건너뛴다. **이게 "한 번 받은 건 다시 안 받는다"를
 *     지키는 자리다.**
 *
 * 실패해도 던지지 않는다. 지름길이 막혔다고 판 전체가 죽으면 안 된다.
 */
export async function loadSeed(market, {
  wanted = 10080,
  oldest = null,
  fetcher = (...args) => globalThis.fetch(...args),
  where = globalThis.location,
  onTry = null,
  onChunk = null,
  shouldStop = null,
} = {}) {
  // `from`은 통한 자리를 기억하는 칸이다. readFile이 채운다.
  const io = { fetcher, where, onTry, from: null };
  let got = 0;
  let made = 0;
  let reached = null;   // 지금까지 받아 내려간 가장 오래된 시각

  const take = async (payload) => {
    if (!isFor(payload, market)) return false;
    const candles = unpackSeed(payload);
    if (!candles?.length) return false;
    candles.sort((a, b) => a.ts - b.ts);
    got += candles.length;
    reached = reached === null ? candles[0].ts : Math.min(reached, candles[0].ts);
    made = Math.max(made, Number(payload.made) || candles[candles.length - 1].ts);
    if (onChunk) await onChunk(candles);
    return true;
  };

  // 1) 꼬리. 제일 작고 제일 새것이라 언제나 먼저다.
  const tail = await readFile('data', `tail/${market}.json`, io);
  if (tail) await take(tail);

  // 2) 최근 31일. 이틀치보다 더 필요할 때만.
  if (wanted > TAIL_DAYS * DAY_BARS && !(shouldStop?.())) {
    const recent = await readFile('data', `recent/${market}.json`, io);
    if (recent) await take(recent);
  }

  if (got >= wanted || shouldStop?.()) return { got, made, reached };

  // 3) 지나간 달을 **새것부터** 내려간다. 목록을 먼저 받아서 404를 더듬지 않는다.
  const manifest = await readFile('history', 'manifest.json', io);
  const months = manifest?.months?.[market];
  if (!Array.isArray(months)) return { got, made, reached };

  for (const name of [...months].reverse()) {
    if (got >= wanted || shouldStop?.()) break;
    // 이미 이 기기에 있는 구간이면 건너뛴다. 한 번 받은 과거는 안 변한다.
    const start = Date.parse(`${name}-01T00:00:00Z`) / 1000;
    if (oldest !== null && Number.isFinite(start) && start >= oldest) continue;
    // eslint-disable-next-line no-await-in-loop
    const chunk = await readFile('history', `${market}/${name}.json`, io);
    // eslint-disable-next-line no-await-in-loop
    if (chunk) await take(chunk);
  }

  return { got, made, reached };
}
