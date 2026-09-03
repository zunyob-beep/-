// **미리 받아 둔 봉 파일을 흉내 낸다.** 브라우저 시험 셋이 같이 쓴다.
//
// 진짜 앱에서는 깃허브 액션이 서버에서 받아 두 브랜치에 적어 두고, 앱은
// 그걸 내려받은 다음에야 업비트로 마지막 몇 분을 채운다. 시험이 이걸 안
// 내면 **실제로 아무도 안 걷는 길**을 걷게 된다 — 업비트만 두드리는 길은
// 이제 주된 길이 아니다.
//
// 형식은 tools/pack.py와 web/core/seed.js가 주고받는 것과 같다. 여기서
// 다시 한 번 적는 이유는, 시험이 앱 코드를 빌려 쓰면 **둘이 같이 틀려도
// 통과**하기 때문이다. 서로 다른 손으로 적어야 대조가 된다.

const STEP = 60;
const SCALE = 100;

function pack(market, rows) {
  const t = []; const c = []; const o = []; const h = []; const l = []; const v = [];
  let previousTs = rows[0][0];
  let previousClose = 0;
  rows.forEach((row, i) => {
    const [ts, open, high, low, close, volume] = row;
    t.push(i === 0 ? 0 : (ts - previousTs) / STEP);
    previousTs = ts;
    const packedClose = Math.round(close * SCALE);
    c.push(i === 0 ? packedClose : packedClose - previousClose);
    previousClose = packedClose;
    o.push(Math.round(open * SCALE) - packedClose);
    h.push(Math.round(high * SCALE) - packedClose);
    l.push(Math.round(low * SCALE) - packedClose);
    v.push(Number(volume.toFixed(3)));
  });
  return {
    m: market,
    step: STEP,
    from: rows[0][0],
    n: rows.length,
    scale: SCALE,
    made: rows[rows.length - 1][0],
    t, c, o, h, l, v,
  };
}

/** `endTs`에서 뒤로 `bars`개. 값은 시각만으로 정해지므로 몇 번을 만들어도 같다. */
export function fakeSeed({ market, endTs, bars, priceAt }) {
  const rows = [];
  for (let i = bars - 1; i >= 0; i -= 1) {
    const ts = endTs - i * STEP;
    const close = priceAt(ts);
    rows.push([ts, priceAt(ts - STEP), close * 1.0004, close * 0.9996, close, 2 + (ts % 7) / 3]);
  }
  return pack(market, rows);
}

/**
 * 그 주소로 낼 것. 우리가 맡을 주소가 아니면 `null`.
 *
 * `lag`
 *     지금이 아니라 **몇 초 전까지만** 담는다. 실제로도 파일은 늘 몇 분
 *     뒤처져 있고, 앱은 그 몇 분을 업비트로 채우러 간다. 지금까지 담아
 *     버리면 그 길이 시험에서 통째로 안 걸린다.
 */
export function seedBody(rest, {
  priceAt, lag = 600, tailBars = 2880, monthBars = 20000, months = {},
} = {}) {
  const now = Math.floor(Date.now() / 1000 / 60) * 60 - lag;

  let hit = rest.match(/^\/data\/tail\/(KRW-[A-Z]+)\.json$/);
  if (hit) return JSON.stringify(fakeSeed({ market: hit[1], endTs: now, bars: tailBars, priceAt }));

  hit = rest.match(/^\/data\/month\/(KRW-[A-Z]+)\.json$/);
  if (hit) return JSON.stringify(fakeSeed({ market: hit[1], endTs: now, bars: monthBars, priceAt }));

  if (rest === '/history/manifest.json') return JSON.stringify({ made: now, months });

  hit = rest.match(/^\/history\/(KRW-[A-Z]+)\/(\d{4}-\d{2})\.json$/);
  if (hit) {
    const [, market, name] = hit;
    if (!(months[market] ?? []).includes(name)) return null;
    const start = Date.parse(`${name}-01T00:00:00Z`) / 1000;
    const bars = 43200;
    return JSON.stringify(fakeSeed({ market, endTs: start + bars * STEP, bars, priceAt }));
  }
  return null;
}
