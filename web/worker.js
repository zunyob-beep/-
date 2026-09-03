// 무거운 일을 여기서 한다. 화면과 다른 실 위에서 돈다.
//
// 왜 나눴는가
// ----------
// 8년치 1분봉은 420만 개다. 닮은 과거를 찾는 계산은 그 사이에 잠깐도
// 멈추지 않는데, 이걸 화면과 같은 실에서 돌리면 그동안 **화면이 통째로
// 얼어붙는다**. 스크롤도 안 되고, 멈추기 단추도 안 눌린다. 아이패드에서는
// 브라우저가 "응답 없음"으로 판단해 페이지를 죽이기도 한다.
//
// 예전에는 파이썬 서버가 이 자리에 있었다. 서버가 없어진 지금, 이 워커가
// 그 역할을 그대로 한다 — 받아서, 계산하고, 결과만 넘긴다.

import {
  DEFAULT_COUNT, RATIO, analyse, analysisJson, examplesJson, withinLimit,
} from './core/analysis.js';
import { loadSeries, update } from './core/data.js';
import { loadSeed } from './core/seed.js';
import { CandleStore, IndexedDbBackend } from './core/store.js';
import { UpbitClient, UpbitError } from './core/upbit.js';
import { aggregate, timeframeLabel } from './core/models.js';

/** 업비트로 가는 길 자체가 막힌 경우. 화면이 안내를 달리 띄운다. */
const NETWORK = ['offline', 'blocked', 'stalled', 'rate', 'server', 'empty', 'throttled', 'candles'];

let store = null;
let analysis = null;   // 마지막으로 끝낸 계산. 사례를 볼 때마다 다시 찾지 않으려고 들고 있는다.

/**
 * 업비트로 가는 길은 **하나만 둔다.**
 *
 * 누를 때마다 새로 만들면 그때까지 배운 것을 전부 버린다 — 통하는 `to` 표기도,
 * 막혀서 낮춰 둔 속도도. 그러면 막혔을 때 다시 눌러도 똑같은 속도로 똑같이
 * 막힌다. 실제로 두 번 연속 같은 자리에서 멈췄다.
 *
 * 하나를 계속 쓰면 배운 것이 남아, 다시 누를 때마다 조금씩 더 잘 받는다.
 */
let client = null;

/**
 * 사용자가 적어 둔 우회 주소. 화면이 보내 준다.
 *
 * 워커에서는 localStorage를 못 읽으므로 화면이 알려 줘야 한다. 바뀌면
 * 클라이언트를 새로 만든다 — 길 목록이 달라지기 때문이다.
 */
let myProxy = null;

async function ready() {
  if (!store) store = new CandleStore(await IndexedDbBackend.open());
  if (!client) client = new UpbitClient({ myProxy });
  return store;
}

function setProxy(url) {
  const next = url || null;
  if (next === myProxy) return;
  myProxy = next;
  client = null;   // 길 목록이 바뀌었으니 새로 만든다
}

const say = (message) => postMessage(message);

/**
 * 진행 상황을 화면에 알린다.
 *
 * `done`과 `total`은 **절대값**이다 — 지금까지 받아둔 전체 개수와 목표 개수.
 * 예전에는 이번에 받을 조각의 개수를 넘겼다. 그러면 막대가 100%까지 차도
 * 실제로는 30%밖에 안 온 것일 수 있어서, 막대가 거짓말을 한다.
 */
const progress = (text, done = 0, total = 0) => say({
  type: 'progress', message: text, done, total,
});

/**
 * 실패를 화면이 쓸 수 있는 모양으로.
 *
 * 오류 문구 한 줄만 넘기면 사용자는 무엇을 해야 할지 알 수 없다. 무엇 때문에
 * 막혔는지(`kind`)를 같이 넘겨서, 화면이 그에 맞는 안내를 띄우게 한다.
 */
function asFailure(error) {
  if (error instanceof UpbitError) {
    return { type: 'error', kind: error.kind, message: error.message };
  }
  return { type: 'error', kind: 'app', message: error?.message ?? String(error) };
}

/** 받아둔 시세 요약. 계산 없이 캐시 상태만 — 화면을 열자마자 보여줄 것. */
async function summary(market) {
  const db = await ready();
  const rows = [];
  // 저장하는 건 1분봉뿐이다. 3·5분봉은 그걸 묶어서 만든다 — 없는 게 아니라
  // 받을 필요가 없는 것이다. 여기서 셋을 다 보여주면 두 개가 0으로 떠서
  // '못 받았다'로 읽힌다.
  for (const timeframe of ['minute1']) {
    // eslint-disable-next-line no-await-in-loop
    const count = await db.count(market, timeframe);
    // eslint-disable-next-line no-await-in-loop
    const span = await db.span(market, timeframe);
    rows.push({
      timeframe,
      label: timeframeLabel(timeframe),
      count,
      from: span ? new Date(span[0] * 1000).toISOString() : null,
      to: span ? new Date(span[1] * 1000).toISOString() : null,
    });
  }
  return rows;
}

/**
 * 받고 계산한다.
 *
 * `fresh`가 거짓이면 업비트에 가지 않고 가진 것만으로 다시 센다. 설정만
 * 바꿔 보는데 매번 받으러 가면 느리고, 받을 것도 없다.
 */
/**
 * 받아둔 1분봉을 읽어 계산에 쓸 세 간격을 만든다.
 *
 * 3·5분봉은 1분봉을 묶어서 만든다 — 업비트가 주는 것과 같은 값이고 요청은
 * 하나도 더 안 쓴다.
 */
async function buildSeries(db, market, bars) {
  const series = {};
  const base = await loadSeries(db, market, 'minute1', bars);
  if (!base.length) return series;
  series.minute1 = base;
  for (const [timeframe, factor] of [['minute3', 3], ['minute5', 5]]) {
    const made = aggregate(base, factor);
    if (made.length) series[timeframe] = made;
  }
  return series;
}

/**
 * **가진 것으로 먼저 답하기에 충분한 봉 수.**
 *
 * 왜 이게 필요한가 — 30일치(43,200봉)를 고르고 누르면, 이미 12,696개를
 * 받아 뒀는데도 나머지 3만 개를 다 채울 때까지 아무 답도 안 줬다. 그 사이
 * 화면에는 "다시 해 보는 중"만 몇 분씩 떴고, 사용자 눈에는 작동을 안 하는
 * 것과 구분이 안 됐다. 실제로 그런 화면을 받았다.
 *
 * 이미 8.8일치가 있는데 답을 못 줄 이유가 없다. 먼저 답하고, 더 받으면
 * 다시 답한다.
 *
 * 2,000봉(약 1.4일)을 문턱으로 둔다. 그보다 적으면 닮은 과거가 스무 개도
 * 안 나와서 "표본이 모자랍니다"만 보여 주게 되고, 그건 답이 아니다.
 */
const FIRST_ANSWER = 2000;

/**
 * **한 번 누르면 이만큼만 받고 끝낸다.**
 *
 * 예전에는 목표를 다 채울 때까지 끝나지 않았다. 30일치면 216번이고, 거절이
 * 섞이면 10분이 넘는다. 그동안 단추는 잠겨 있고 화면은 "받는 중"이다 —
 * 사용자 눈에는 그게 **작동 안 하는 것**이다. 실제로 그 말을 들었다.
 *
 * 그래서 한 판을 시간으로 끊는다. 받은 만큼으로 계산해서 답을 주고 끝낸다.
 * 지나간 봉은 다시 안 받으므로, 다시 누르면 **이어서** 받는다. 세 번 누르면
 * 세 배가 쌓인다. 누를 때마다 반드시 끝나는 것이 오래 붙들고 있는 것보다
 * 훨씬 낫다.
 *
 * 90초로 둔다 — 잘 풀리면 30일치(216번, 72초)가 한 번에 들어온다.
 */
const FETCH_BUDGET_MS = 90000;

/**
 * 미리 받아 둔 파일이 들어왔을 때 업비트에 매달릴 시간.
 *
 * 파일이 14일치를 통째로 가져다 주므로 업비트에서 받을 게 **마지막 몇 분**밖에
 * 없다. 그것 때문에 90초를 기다리게 하는 건 말이 안 된다 — 그 몇 분이
 * 없어도 결과는 나오고, 화면에는 언제 값인지가 적힌다.
 */
const SEED_TOPUP_MS = 20000;

/** 미리 받아 둔 파일이 이보다 새것이면 업비트에 물어보지 않는다 (초). */
const SEED_FRESH_SECONDS = 120;

/**
 * **하루 전과 견준 변화를 받아둔 분봉에서 계산한다.**
 *
 * 예전에는 맨 위 가격을 `/v1/ticker`로 따로 받았다. 그 주소가 전일 대비까지
 * 같이 주기 때문이었는데, 그러자고 20초마다 **다른 주소를 하나 더** 부르고
 * 있었다 — 시간당 180번, 거절당할 구멍도 하나 더.
 *
 * 가격은 어차피 받고 있는 1분봉의 마지막 값이 곧 지금 값이다. 변화율은
 * 받아둔 분봉에서 24시간 전 값을 찾아 직접 계산하면 된다. 부르는 주소가
 * 하나로 줄고, 없는 값은 안 보여 주면 그만이다(하루치가 아직 없을 때).
 */
async function withChange(now) {
  const row = { market: now.market, price: now.price, changeRate: 0, changePrice: 0 };
  try {
    const db = await ready();
    const span = await db.span(now.market, 'minute1');
    if (!span || now.ts - span[0] < DAY) return row;
    // 하루치 꼬리만 읽어서 24시간 전 값을 찾는다. 받아둔 것에서 꺼내므로
    // 요청이 안 들고, 배열로 읽으므로 봉 객체를 1,441개 만들지 않는다.
    const tail = await db.loadTailColumns(now.market, 'minute1', 1441);
    const want = now.ts - DAY;
    let at = -1;
    for (let i = 0; i < tail.ts.length; i += 1) {
      if (tail.ts[i] >= want) { at = i; break; }
    }
    if (at < 0) return row;
    const then = tail.close[at];
    if (!then) return row;
    row.changePrice = now.price - then;
    row.changeRate = row.changePrice / then;
  } catch { /* 못 구하면 변화율 없이 가격만 보여 준다. */ }
  return row;
}

const DAY = 86400;

/**
 * 받아둔 것 중 **가장 최근 봉**으로 시세 한 줄을 만든다.
 *
 * 업비트가 막혔을 때 쓴다. 지금 값은 아니지만 몇 분 전 값이고, 그걸
 * 언제 값인지 적어서 보여 주는 편이 `—`보다 낫다.
 */
async function lastKept(market) {
  try {
    const db = await ready();
    const tail = await db.loadTailColumns(market, 'minute1', 1);
    if (!tail?.ts?.length) return null;
    const at = tail.ts.length - 1;
    const row = await withChange({ market, price: tail.close[at], ts: tail.ts[at] });
    // 언제 값인지. 화면이 이걸 보고 "N분 전"을 적는다.
    row.at = tail.ts[at];
    row.late = true;
    return row;
  } catch {
    return null;
  }
}

/** 업비트가 알려 준 남은 한도를 화면에 적을 문구로. 못 읽으면 빈 문자열. */
function budget() {
  const left = client?.limiter?.remaining;
  if (!left) return '';
  const bits = [];
  if (Number.isFinite(left.sec)) bits.push(`초 ${left.sec}`);
  if (Number.isFinite(left.min)) bits.push(`분 ${left.min}`);
  return bits.length ? ` · 업비트가 남았다는 한도 ${bits.join('/')}` : '';
}

/**
 * 미리 받아 둔 파일을 마지막으로 읽은 시각. 종목마다 따로 센다.
 *
 * 한 판에 한 번이면 된다 — 파일은 20분마다 갱신되므로 그보다 자주 물어봐야
 * 같은 것을 또 받을 뿐이다. 그렇다고 앱을 켜 둔 채 몇 시간이 지나도 다시
 * 안 읽으면, 그때는 낡은 것을 붙들고 있게 된다.
 */
const seedRead = new Map();
const SEED_AGAIN = 10 * 60 * 1000;

/**
 * **미리 받아 둔 봉을 캐시에 넣는다.** 성공하면 그 파일이 만들어진 시각.
 *
 * 이게 이 앱이 도는 주된 길이다. 업비트가 아니라 여기가.
 * 실패해도 던지지 않는다 — 지름길이 막혔다고 판 전체가 죽으면 안 된다.
 */
async function seedFrom(db, market, wanted) {
  const last = seedRead.get(market) ?? 0;
  const before = seedRead.get(`${market}:wanted`) ?? 0;
  // 시간이 안 지났어도 **더 긴 기간을 골랐으면** 다시 읽는다. 7일을 보고
  // 나서 1년을 고르면 조각을 더 받아야 하는데, 시간만 보면 10분 동안
  // 아무것도 안 받고 "7일치뿐입니다"만 답하게 된다.
  if (Date.now() - last < SEED_AGAIN && before >= wanted) {
    return seedRead.get(`${market}:made`) ?? null;
  }

  progress('미리 받아 둔 시세를 읽는 중…');
  const started = Date.now();
  const span = await db.span(market, 'minute1');
  let stored = await db.count(market, 'minute1');

  // **가진 구간이 성긴지 본다.**
  //
  // `oldest`는 "여기부터는 이미 있다"는 뜻으로 쓰인다. 그런데 저장된 것이
  // 듬성듬성하면(예전에 업비트로 조금씩 받다 만 자국) 시작만 오래됐을 뿐
  // 속은 비어 있는데, 그걸 믿고 조각을 통째로 건너뛰면 **영영 안 채워진다.**
  // 화면에는 아무 말도 안 뜨고 개수만 안 는다.
  const should = span ? Math.floor((span[1] - span[0]) / 60) + 1 : 0;
  const solid = should > 0 && stored >= should * 0.9;

  const got = await loadSeed(market, {
    wanted,
    // 이미 가진 구간은 건너뛴다. 지나간 봉은 안 변하므로 다시 받을 이유가 없다.
    // 다만 성긴 경우에는 안 건너뛴다 — 위를 보라.
    oldest: solid ? span[0] : null,
    // 무엇을 어디서 읽었는지도 기록에 남긴다. 진단 화면이 업비트 요청과
    // 같은 자리에 펴 주므로, 한 장으로 어느 길이 됐는지 다 보인다.
    onTry: (url, how) => client?.log?.push({
      at: Date.now(), route: '미리 받아둔 파일', path: url.replace(/^https?:\/\//, ''),
      ms: Date.now() - started, how,
    }),
    // **조각마다 그때그때 저장한다.** 다 모아 뒀다가 한 번에 넣으면 4년치
    // 210만 봉이 통째로 메모리에 쌓여 아이패드에서 브라우저가 죽는다.
    onChunk: async (candles) => {
      stored += await db.put(market, 'minute1', 60, candles);
      progress('미리 받아 둔 시세를 읽는 중…', stored, wanted);
    },
  });

  seedRead.set(market, Date.now());
  seedRead.set(`${market}:wanted`, wanted);
  if (!got.got) {
    seedRead.set(`${market}:made`, null);
    return null;
  }
  seedRead.set(`${market}:made`, got.made);
  return got.made;
}

async function run({ market, count, fresh, similarity, fee, slippage, length, stake }) {
  const db = await ready();
  /** 미리 받아 둔 파일이 만들어진 시각. 막혔을 때 화면이 이걸 보여준다. */
  let seedAt = null;
  // 상한은 **여기서** 건다. 화면 쪽만 막으면 낡은 화면이나 손으로 보낸
  // 메시지가 그대로 통과해, 브라우저가 감당 못 할 크기를 받으러 간다.
  const bars = withinLimit(count);

  // 업비트로 가는 길이 막혔는지. 막혔으면 나머지 봉 간격도 똑같이 막혀
  // 있으므로 더 시도하지 않는다 — 간격마다 재시도를 다 기다리면 아무
  // 소득 없이 45초가 그냥 간다.
  let blocked = null;

  // **1분봉만 받는다.**
  //
  // 3분봉은 1분봉 셋을 묶은 것과 정확히 같고, 5분봉은 다섯을 묶은 것과 같다.
  // 그런데 지금까지 셋을 각각 따로 받았다 — 30일치면 216 + 72 + 43 = 331번.
  // 이미 받은 걸로 만들 수 있는 것을 다시 받고 있었던 셈이다.
  //
  // 업비트가 우리를 막는 이유가 요청이 잦아서이므로(아이패드 진단이 그걸
  // 보여줬다), 요청 수를 줄이는 게 가장 큰 지렛대다. 216번으로 끝난다.
  // **미리 받아 둔 파일부터 읽는다. 업비트보다 먼저다.**
  //
  // 이 한 줄 순서가 이 판의 전부다. 지금까지는 업비트가 첫째였고, 업비트가
  // 막히면 앱은 아무것도 못 했다. 이제는 파일이 첫째다 — 한 번 내려받으면
  // 14일치가 통째로 들어온다. 업비트는 그 뒤에 최근 몇 분만 채우는,
  // **되면 좋은** 자리로 내려간다.
  if (fresh) {
    seedAt = await seedFrom(db, market, Math.floor(bars / RATIO.minute1));
    if (seedAt) {
      // 막대가 여기서부터 보여야 한다. 파일 하나로 목표의 거의 전부가
      // 들어오므로, 이 한 줄이 없으면 사용자는 아무 일도 안 일어난 줄 안다.
      progress(
        '미리 받아 둔 시세를 읽었습니다',
        await db.count(market, 'minute1'),
        Math.floor(bars / RATIO.minute1),
      );
    }
  }

  // **가진 것으로 먼저 답한다.**
  //
  // 다 받을 때까지 기다리게 하지 않는다. 받아둔 것이 쓸 만하면 그걸로 바로
  // 계산해서 보여 주고, 받기는 뒤에서 계속한다. 다 받으면 다시 계산해서
  // 덮어쓴다 — 숫자는 그때 정확해진다.
  if (fresh) {
    const cached = await db.count(market, 'minute1');
    if (cached >= FIRST_ANSWER) {
      progress(`받아둔 ${cached.toLocaleString()}개로 먼저 계산합니다…`);
      const early = await buildSeries(db, market, bars);
      if (Object.keys(early).length) {
        analysis = analyse(market, early, { similarity, fee, slippage, length, stake });
        say({
          type: 'partial',
          analysis: analysisJson(analysis),
          have: cached,
          want: Math.floor(bars / RATIO.minute1),
        });
      }
    }
  }

  // 한 판이 끝날 시각. 이걸 넘기면 받은 만큼으로 계산하고 끝낸다.
  const deadline = Date.now() + (seedAt ? SEED_TOPUP_MS : FETCH_BUDGET_MS);
  let ranOut = false;

  // **파일이 방금 것이면 업비트에 아예 안 물어본다.**
  //
  // 직접 가는 길은 분당 6번이라 한 요청에 10초가 든다. 파일이 이미 마지막
  // 봉까지 담고 있는데 그 10초를 쓰는 건 아무것도 안 얻고 기다리는 것이고,
  // 한도만 축낸다. 몇 분이라도 벌어져 있을 때만 채우러 간다.
  const seedIsNow = seedAt && Date.now() / 1000 - seedAt < SEED_FRESH_SECONDS;
  if (fresh && !seedIsNow) {
    for (const timeframe of ['minute1']) {
      if (blocked) break;
      const label = timeframeLabel(timeframe);
      const wanted = Math.floor(bars / RATIO[timeframe]);
      // **이미 가진 건 다시 안 받는다는 걸 화면에서 보이게 한다.**
      //
      // 지나간 봉은 변하지 않으므로 한 번 받아 두면 끝이다. 그런데 화면에는
      // 그냥 "받는 중…"만 떠서, 중간에 끊기면 처음부터 다시 받는 것처럼
      // 보인다. 그러면 다시 누르기가 겁난다 — 사실은 누를수록 쌓이는데도.
      // eslint-disable-next-line no-await-in-loop
      const already = await db.count(market, timeframe);
      // 개수는 아래 진행 막대가 맡는다. 여기 문구는 **무슨 일이 벌어지는지**만
      // 말한다 — 같은 숫자를 두 군데 적으면 읽는 데 방해만 된다.
      //
      // **여기서 개수를 더하지 않는다.** update()가 이미 절대값으로 준다.
      // 예전에는 여기서 already를 또 더해서 두 배로 부푼 숫자가 떴다.
      progress(`${label} 받는 중…`, already, wanted);
      try {
        // eslint-disable-next-line no-await-in-loop
        await update(db, market, timeframe, wanted, {
          client,
          shouldStop: () => {
            if (Date.now() <= deadline) return false;
            ranOut = true;
            return true;
          },
          onProgress: (done, total, info) => {
            if (info?.banned) {
              // 몇 분을 기다려야 한다. 남은 시간을 초 단위로 보여주지 않으면
              // 멈춘 것으로 보이고, 사용자는 앱을 닫는다.
              progress(
                `업비트가 막고 있습니다 — ${info.waitLeft}초 뒤에 이어서 받습니다`,
                done, total,
              );
              return;
            }
            if (info?.retrying) {
              // **거절당해서 다시 해 보는 중이라는 걸 말한다.**
              //
              // 이게 없을 때 화면에는 "받는 중…"만 몇 분씩 떠 있었다.
              // 앱이 죽은 것과 구분이 안 되고, 실제로 그렇게 보였다.
              progress(
                `업비트가 거절해서 다시 해 보는 중입니다 (${info.retrying}/${info.of})`,
                done, total,
              );
              return;
            }
            // **몇 개를 받았는지 늘 적는다.**
            //
            // 예전에는 잘 받고 있을 때 개수를 안 적었다. 걸렸을 때만
            // "받아둔 N개는 그대로입니다"가 떴다. 그래서 순조로울 때가
            // 오히려 깜깜했다 — 화면만 봐서는 쌓이는 중인지 멈춘 건지
            // 알 수가 없다. 늘 보여야 한다.
            progress(
              info?.stalled
                ? `잠시 걸렸습니다 — ${info.waitLeft}초 뒤 이어서 받습니다`
                // 지금 속도와, 업비트가 알려 준 남은 한도를 같이 적는다.
                // 이 숫자들이 보이면 "왜 거절당하나"를 추측할 필요가 없다.
                : `${label} 받는 중… 초당 ${client.limiter.perSecond.toFixed(1)}회${budget()}`,
              done, total,
            );
          },
        });
      } catch (error) {
        if (error instanceof UpbitError && NETWORK.includes(error.kind)) {
          blocked = error.kind;
        } else {
          // 한 간격을 못 받아도 나머지로 계산은 된다. 다만 조용히 넘어가면
          // 사용자는 3종을 다 본 줄 안다 — 결과의 '빠진 봉 간격'에 나온다.
          say({ type: 'warn', message: `${label}을 갱신하지 못했습니다: ${error.message}` });
        }
      }
    }
  }

  progress('받아둔 시세를 읽는 중…');
  const series = await buildSeries(db, market, bars);

  // **못 받았다는 사실을 반드시 말한다.**
  //
  // 예전에는 이걸 빨간 글씨 한 줄로만 흘렸다. 그러면 받아둔 옛날 시세로
  // 계산한 결과가 '지금 시세로 판단받기'의 답인 것처럼 화면에 뜬다.
  // 몇 시간 전 데이터를 지금 것으로 읽게 만드는 셈이라, 이건 조용히
  // 넘어가면 안 되는 종류의 실패다.
  if (blocked) {
    say({
      type: 'blocked',
      kind: blocked,
      stale: Object.keys(series).length > 0,
      // **미리 받아 둔 파일이 언제 만들어졌는지도 같이 준다.**
      //
      // 업비트가 막혀도 이제 화면에는 결과가 뜬다. 그런데 그게 언제 것인지
      // 안 적으면, 사용자는 그걸 '지금'으로 읽는다. 20분 전 값을 지금 값으로
      // 읽게 만드는 건 조용히 넘어가면 안 되는 종류의 거짓말이다.
      seedAt,
    });
  }

  if (!Object.keys(series).length) {
    // 받지도 못했고 가진 것도 없다. 여기서 "먼저 시세 받기를 누르세요"라고
    // 하면 방금 누른 사람에게 하는 말이 되어 버린다.
    if (blocked) return;
    throw new Error("시세가 없습니다. '지금 시세로 판단받기'를 눌러 주세요.");
  }

  analysis = analyse(market, series, {
    similarity,
    fee,
    slippage,
    length,
    stake,
    // **계산 단계는 개수를 건드리지 않는다.**
    //
    // analyse()는 onStep(문구, 1, 3)처럼 '세 간격 중 몇 번째'를 알린다.
    // 그걸 그대로 넘겼더니 화면이 그 숫자를 **받은 개수로** 그렸다 —
    // 10,080개를 받아 놓고 "3 / 3개 받았습니다"가 떴다. 통로는 같아도
    // 뜻이 다르므로, 여기서는 문구만 바꾼다.
    onStep: (text) => progress(text),
  });
  // **한 판이 시간에 걸려 끝났으면 그렇다고 말한다.**
  //
  // 조용히 끝내면 사용자는 다 받은 줄 안다. 다시 누르면 이어서 받는다는
  // 것도 같이 말해야, 누를수록 쌓인다는 걸 알고 다시 누른다.
  // **모자라면 모자란다고 말한다 — 시간에 걸렸든 아니든.**
  //
  // 예전에는 `ranOut`(시간 초과)일 때만 알렸다. 그런데 미리 받아 둔 파일이
  // 목표에 못 미치는 경우가 있다(4년치를 골랐는데 과거가 1년까지만 채워진
  // 때). 그때는 시간에 걸리지도 않으므로 아무 말 없이 끝났고, 사용자는
  // 4년치로 계산한 줄 알았다. 그건 조용히 넘어가면 안 되는 종류의 일이다.
  const have = await db.count(market, 'minute1');
  const more = have < bars * 0.99 ? { have, want: bars, ranOut } : null;
  say({ type: 'done', analysis: analysisJson(analysis), stale: blocked !== null, more });
}

onmessage = async (event) => {
  const message = event.data ?? {};
  try {
    // 우회 주소는 어떤 일을 하기 전에 먼저 반영한다. 화면이 모든 메시지에
    // 실어 보내므로, 사용자가 고치면 그다음 요청부터 바로 그 길로 간다.
    if ('myProxy' in message) setProxy(message.myProxy);
    if (message.type === 'proxy') return;   // 알려 주기만 하는 메시지
    // 맨 위 시세도 **여기를 거친다.**
    //
    // 예전에는 화면 쪽에 UpbitClient가 따로 있었다. 그러면 속도 제한기가
    // 둘이 되어 서로를 모른다 — "초당 3회"가 사실이 아니게 된다. 게다가
    // 5초마다 부르고 있어서, 아무것도 안 하고 앱만 켜 둬도 시간당 720번이
    // 나갔다. **막혀 있는 동안에도 계속 두드려서 회복을 방해했다.**
    //
    // 업비트로 나가는 길을 하나로 모으면, 내려받기와 시세가 같은 줄을 서고
    // 막힌 것도 한 곳에서 안다.
    if (message.type === 'ticker') {
      await ready();
      // 막혀 있는 걸 이미 안다면 두드리지 않는다. 맨 위 숫자 하나 때문에
      // 회복을 늦출 이유가 없다.
      // `keptOnly`면 업비트에 안 물어본다. 판이 막 끝난 직후에 쓴다 —
      // 방금 저장한 마지막 봉이 우리가 가진 가장 새 값이라, 그걸 두고
      // 분당 6번짜리 한도를 또 쓸 이유가 없다.
      const live = (message.keptOnly || client.knownBlocked())
        ? null
        : await client.getPrice(message.market).catch(() => null);
      if (live) {
        say({ type: 'ticker', market: message.market, rows: [await withChange(live)] });
        return;
      }
      // **업비트가 안 되면 받아둔 마지막 봉을 보여 준다.**
      //
      // 예전에는 여기서 빈 것을 돌려줬고, 맨 위에는 `—`만 남았다. 그런데
      // 이제는 미리 받아 둔 파일이 있어서 몇 분 전 값이 손안에 있다.
      // 있는 것을 안 보여 줄 이유가 없다 — 언제 값인지만 같이 적으면 된다.
      const kept = await lastKept(message.market);
      say({ type: 'ticker', market: message.market, rows: kept ? [kept] : [] });
      return;
    }
    if (message.type === 'summary') {
      say({ type: 'summary', market: message.market, cached: await summary(message.market) });
      return;
    }
    // **앱이 실제로 보낸 요청 목록.** 진단 화면이 이걸 그대로 펴 준다.
    //
    // 여기까지 오는 데 오래 걸렸다. 그동안 화면에 남는 건 "거절당했습니다"
    // 한 줄뿐이었고, 무엇을 어느 길로 보냈고 무슨 답이 왔는지는 아무 데도
    // 안 남았다. 그래서 고칠 때마다 추측이었다. 이제 안 그런다.
    if (message.type === 'log') {
      say({ type: 'log', rows: client ? [...client.log] : [] });
      return;
    }
    if (message.type === 'run') {
      analysis = null;
      await run(message);
      return;
    }
    if (message.type === 'examples') {
      if (!analysis) return;
      say({
        type: 'examples',
        examples: examplesJson(analysis, message.timeframe, message.horizon),
      });
      return;
    }
    if (message.type === 'forget') {
      const db = await ready();
      for (const timeframe of Object.keys(DEFAULT_COUNT)) {
        // eslint-disable-next-line no-await-in-loop
        await db.clear(message.market, timeframe);
      }
      // 지웠으면 미리 받아 둔 파일도 다시 읽어야 한다. 안 그러면 지운
      // 다음에 눌러도 10분 동안은 빈 채로 업비트만 두드린다.
      seedRead.delete(message.market);
      seedRead.delete(`${message.market}:made`);
      analysis = null;
      say({ type: 'summary', market: message.market, cached: await summary(message.market) });
      return;
    }
    say(asFailure(new Error(`모르는 요청입니다: ${message.type}`)));
  } catch (error) {
    say(asFailure(error));
  }
};
