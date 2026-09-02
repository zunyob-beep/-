// **미리 받아 둔 봉을 읽어 온다.**
//
// 이 파일이 이 앱에서 제일 중요한 자리다. 여기가 되면 앱은 업비트가
// 막혀 있어도 돈다.
//
// 무슨 일이 있었나
// ---------------
// 브라우저에서 업비트를 직접 부르는 것은 믿을 수 없다. 업비트 REST API는
// 서버끼리 쓰라고 만든 것이라, 거절할 때 돌려주는 응답에 브라우저가 요구하는
// 허용 표시(CORS 헤더)가 없다. 그러면 브라우저는 그 답을 **읽지도 못하고**
// 실패로 처리한다 — 인터넷이 끊긴 것과 구분조차 안 된다. 그 위에 한도가
// 주소(IP) 단위로 걸리므로, 휴대폰 데이터에서는 내가 아무것도 안 해도
// 남이 쓴 몫 때문에 막힌다.
//
// 그래서 공개 우회 서버로 돌아가게 만들었는데, 그건 문제를 옮긴 것뿐이었다.
// 우회 서버 주소도 수천 명이 같이 쓰니 똑같이 막히고, 느리고, 어느 날
// 유료로 바뀌면 그날부터 앱이 죽는다 (corsproxy.io가 실제로 그랬다).
//
// 그러니 **브라우저가 업비트를 안 부르게 한다.** 깃허브 액션이 20분마다
// 서버에서 — CORS도 없고 한도도 넉넉한 자리에서 — 14일치를 받아 파일로
// 적어 둔다. 앱은 그 파일 하나만 내려받는다. 요청 한 번, 몇백 KB,
// CORS 걱정 없음, 한도 없음.
//
// 그 뒤에 업비트로 최근 몇 분만 더 받아 보되, 그건 **되면 좋은 것**이지
// 없으면 안 되는 것이 아니다. 이 뒤바뀜이 이 판의 전부다.

/** 파일 이름. 서버 쪽(tools/candles.py)과 맞춰야 한다. */
export const seedName = (market) => `${market}.min1.json`;

/**
 * 파일이 있을 만한 자리들. 순서대로 물어보고 **먼저 되는 것**을 쓴다.
 *
 * 1. 같은 주소 아래 `./data/…`
 *    같은 곳에서 온 파일이라 CORS라는 개념 자체가 없다 — 막힐 구석이 없다.
 *    시험이 여기에 가짜 파일을 놓고 돈다.
 *
 * 2. raw.githubusercontent.com
 *    실제로 파일이 사는 곳(`data` 브랜치). 여기는 아무 데서나 읽으라고
 *    허용 표시를 붙여 주므로 브라우저가 그대로 읽는다 — 업비트와 달리.
 *
 * 주소는 **지금 열려 있는 주소에서 뽑는다.** 저장소 이름을 코드에 박아 두면
 * 남이 갈라 갔을 때(fork) 남의 데이터를 읽게 된다.
 */
export function seedUrls(market, where) {
  const name = seedName(market);
  const urls = [new URL(`./data/${name}`, where.href).toString()];

  const host = String(where.hostname || '');
  if (host.endsWith('.github.io')) {
    const owner = host.slice(0, -'.github.io'.length);
    // `/-/worker.js` → 저장소 이름은 `-`. 사용자 페이지(owner.github.io)면
    // 저장소 이름이 곧 호스트 이름이다.
    const first = String(where.pathname || '/').split('/').filter(Boolean)[0];
    const repo = first && !first.includes('.') ? first : host;
    urls.push(`https://raw.githubusercontent.com/${owner}/${repo}/data/${name}`);
  }
  return urls;
}

/** 파일 한 줄을 봉 하나로. 서버가 [시각, 시가, 고가, 저가, 종가, 거래량]로 적는다. */
function toCandle(row) {
  if (!Array.isArray(row) || row.length < 6) return null;
  const [ts, open, high, low, close, volume] = row.map(Number);
  if (!Number.isFinite(ts) || !Number.isFinite(close)) return null;
  return { ts, open, high, low, close, volume };
}

/**
 * 미리 받아 둔 봉을 읽는다. 못 읽으면 `null`을 돌려준다 — **던지지 않는다.**
 *
 * 이건 어디까지나 지름길이라, 없으면 없는 대로 업비트로 가면 된다.
 * 여기서 던지면 지름길이 막혔다는 이유로 판 전체가 죽는다.
 */
export async function loadSeed(market, {
  fetcher = (...args) => globalThis.fetch(...args),
  where = globalThis.location,
  onTry = null,
} = {}) {
  for (const url of seedUrls(market, where)) {
    try {
      // eslint-disable-next-line no-await-in-loop
      const response = await fetcher(url, { cache: 'no-store' });
      if (!response || response.status >= 400) { onTry?.(url, `${response?.status}`); continue; }
      // eslint-disable-next-line no-await-in-loop
      const payload = await response.json();
      if (payload?.market !== market || !Array.isArray(payload.rows)) {
        onTry?.(url, '모양이 다릅니다');
        continue;
      }
      const candles = payload.rows.map(toCandle).filter(Boolean);
      if (!candles.length) { onTry?.(url, '비어 있습니다'); continue; }
      candles.sort((a, b) => a.ts - b.ts);
      onTry?.(url, `${candles.length}개`);
      return { candles, made: Number(payload.made) || candles[candles.length - 1].ts, url };
    } catch (error) {
      onTry?.(url, error?.name === 'SyntaxError' ? 'JSON 아님' : '못 읽음');
    }
  }
  return null;
}
