// 1분봉을 묶어 3분봉·5분봉을 만드는 게 **정확한가.**
//
// 왜 이걸 하게 됐나
// ----------------
// 업비트가 우리 요청을 막고 있었다. 아이패드 진단이 그걸 보여줬다 — 보통
// 요청은 지금 시세까지 전부 실패하는데 no-cors로는 124ms 만에 닿았다. 답은
// 오는데 그 답을 읽을 수 없는 상태, 즉 거절당하고 있는 것이다.
//
// 그렇다면 가장 큰 지렛대는 **요청 수를 줄이는 것**이다. 그런데 우리는
// 1·3·5분봉을 각각 따로 받고 있었다 (30일치 = 216 + 72 + 43 = 331번).
// 업비트의 3분봉은 1분봉 세 개를 묶은 것과 정확히 같으므로, 이미 받은 걸로
// 만들 수 있는 것을 다시 받고 있었던 셈이다. 1분봉만 받으면 216번이다.
//
// 다만 이건 **계산의 재료를 바꾸는 일**이라 틀리면 전부 틀린다. 여기서
// 묶음이 봉의 정의와 정확히 맞는지 지킨다.

import test from 'node:test';
import assert from 'node:assert/strict';

import { Series, aggregate } from '../../web/core/models.js';

/** 시각이 span으로 딱 떨어지는 자리에서 시작하는 1분봉 만들기. */
function minutes(count, startAt) {
  return Array.from({ length: count }, (_, i) => ({
    ts: startAt + i * 60,
    open: 100 + i,
    high: 200 + i,
    low: 10 + i,
    close: 150 + i,
    volume: i + 1,
  }));
}

const ALIGNED = 1700000000 - (1700000000 % 900);   // 3으로도 5로도 나눠지는 자리

test('3분봉은 1분봉 셋을 묶은 것과 같다', () => {
  const one = Series.fromCandles('KRW-BTC', 'minute1', minutes(6, ALIGNED));
  const three = aggregate(one, 3);

  assert.equal(three.length, 2, `${three.length}개가 나왔습니다`);
  assert.equal(three.timeframe, 'minute3');

  // 첫 묶음: 1분봉 0·1·2
  assert.equal(three.ts[0], ALIGNED, '묶음은 시각 경계에서 시작해야 합니다');
  assert.equal(three.open[0], 100, '시가는 첫 봉의 시가');
  assert.equal(three.high[0], 202, '고가는 구간 최고');
  assert.equal(three.low[0], 10, '저가는 구간 최저');
  assert.equal(three.close[0], 152, '종가는 마지막 봉의 종가');
  assert.equal(three.volume[0], 1 + 2 + 3, '거래량은 합');

  // 둘째 묶음: 1분봉 3·4·5
  assert.equal(three.ts[1], ALIGNED + 180);
  assert.equal(three.open[1], 103);
  assert.equal(three.high[1], 205);
  assert.equal(three.low[1], 13);
  assert.equal(three.close[1], 155);
  assert.equal(three.volume[1], 4 + 5 + 6);
});

test('5분봉도 같은 방식으로 맞는다', () => {
  const one = Series.fromCandles('KRW-BTC', 'minute1', minutes(10, ALIGNED));
  const five = aggregate(one, 5);
  assert.equal(five.length, 2);
  assert.equal(five.timeframe, 'minute5');
  assert.equal(five.ts[0], ALIGNED);
  assert.equal(five.open[0], 100);
  assert.equal(five.high[0], 204);
  assert.equal(five.low[0], 10);
  assert.equal(five.close[0], 154);
  assert.equal(five.volume[0], 1 + 2 + 3 + 4 + 5);
});

test('앞이 잘린 묶음은 버린다', () => {
  // **이게 조용히 틀리기 제일 쉬운 자리다.** 1분봉이 구간 중간부터 시작하면
  // 그 묶음에는 앞 몇 분이 빠져 있다. 그대로 쓰면 시가와 고·저가가 틀린
  // 봉 하나가 맨 앞에 섞여 들어가고, 아무도 눈치채지 못한다.
  const one = Series.fromCandles('KRW-BTC', 'minute1', minutes(5, ALIGNED + 60));
  const three = aggregate(one, 3);

  // 첫 봉은 경계+60에서 시작하므로 그 묶음은 버려야 한다.
  assert.ok(three.length > 0, '전부 버리면 안 됩니다');
  assert.equal(three.ts[0] % 180, 0, '남은 묶음은 경계에서 시작해야 합니다');
  assert.equal(three.ts[0], ALIGNED + 180, '잘린 앞 묶음을 그대로 썼습니다');
  assert.equal(three.open[0], 100 + 2, '버려야 할 봉의 시가를 썼습니다');
});

test('마지막 묶음은 덜 찼어도 남긴다', () => {
  // 지금 만들어지는 중인 봉이다. 업비트가 주는 것도 그렇다.
  const one = Series.fromCandles('KRW-BTC', 'minute1', minutes(4, ALIGNED));
  const three = aggregate(one, 3);
  assert.equal(three.length, 2, '덜 찬 마지막 묶음을 버렸습니다');
  assert.equal(three.volume[1], 4, '한 봉만 들어간 묶음이어야 합니다');
});

test('중간에 봉이 빠져 있어도 묶음이 어긋나지 않는다', () => {
  // 업비트도 거래가 없던 분은 봉을 안 준다. 위치로 세면 그때부터 전부
  // 밀리므로, 반드시 **시각으로** 잘라야 한다.
  const rows = minutes(9, ALIGNED).filter((_, i) => i !== 4);   // 다섯째 봉이 없다
  const one = Series.fromCandles('KRW-BTC', 'minute1', rows);
  const three = aggregate(one, 3);

  assert.equal(three.length, 3, `${three.length}개가 나왔습니다`);
  for (let i = 0; i < three.length; i += 1) {
    assert.equal(three.ts[i], ALIGNED + i * 180, `${i}번 묶음의 시각이 어긋났습니다`);
  }
  // 빠진 봉이 있는 묶음은 둘만 들어간다.
  assert.equal(three.volume[1], 4 + 6, '빠진 봉을 메워 넣었습니다');
});

test('빈 시세를 넣어도 터지지 않는다', () => {
  const empty = Series.fromCandles('KRW-BTC', 'minute1', []);
  const three = aggregate(empty, 3);
  assert.equal(three.length, 0);
});

test('묶을 게 없으면(1분봉) 그대로 돌려준다', () => {
  const one = Series.fromCandles('KRW-BTC', 'minute1', minutes(3, ALIGNED));
  assert.equal(aggregate(one, 1), one);
});
