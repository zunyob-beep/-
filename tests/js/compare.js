// 파이썬 결과와 자바스크립트 결과를 대조하는 도구.
//
// 왜 '완전히 같음'이 아니라 허용 오차를 두는가
// -------------------------------------------
// 두 언어 모두 IEEE 754 배정도를 쓰므로 사칙연산 하나하나는 똑같다. 다른
// 것은 **더하는 순서**다. numpy는 긴 합을 쌍으로 나눠 더하고(pairwise),
// 자바스크립트 쪽은 보정 덧셈(Neumaier)을 쓴다. 둘 다 순진한 순차 덧셈보다
// 정확하지만 마지막 몇 비트가 다를 수 있다.
//
// 그래서 상대오차 1e-9을 넘으면 실패로 본다. 알고리즘이 실제로 갈렸다면
// 그보다 훨씬 크게 벌어진다 — 덧셈 순서 차이로는 이 선을 못 넘는다.
//
// 파이썬이 6자리에서 반올림해 내보내는 값(예상 경로 등)은 그 반올림 폭의
// 절반보다 크게 벌어질 수 없으므로 절대오차 5.1e-7로 본다.

import assert from 'node:assert/strict';

export const TIGHT = 1e-9;
export const ROUNDED_6 = 5.1e-7;

/**
 * 두 값을 재귀로 훑으며 비교한다. 어디서 갈렸는지 경로를 붙여 알려준다 —
 * "숫자가 다릅니다"만으로는 5만 줄짜리 JSON에서 아무것도 못 찾는다.
 *
 * `absolute(path)`가 숫자를 돌려주면 그 자리는 **절대오차**로 본다.
 * 파이썬이 반올림해서 내보내는 자리에 쓴다.
 * `skip(path)`가 참이면 그 자리는 건너뛴다 (시각처럼 매번 달라지는 값).
 */
export function same(actual, expected, options = {}) {
  const {
    path = '', tolerance = TIGHT, absolute = () => null, skip = () => false,
  } = options;
  if (skip(path)) return;

  if (typeof expected === 'number') {
    assert.equal(typeof actual, 'number', `${path}: 숫자가 아닙니다 (${typeof actual})`);
    if (Number.isNaN(expected)) {
      assert.ok(Number.isNaN(actual), `${path}: ${actual} ≠ NaN`);
      return;
    }
    const limit = absolute(path);
    const allowed = limit !== null && limit !== undefined
      ? limit
      : tolerance * Math.max(1, Math.abs(actual), Math.abs(expected));
    assert.ok(
      Math.abs(actual - expected) <= allowed,
      `${path}: ${actual} ≠ ${expected} (차이 ${Math.abs(actual - expected)}, 허용 ${allowed})`,
    );
    return;
  }
  if (expected === null || typeof expected !== 'object') {
    assert.equal(
      actual, expected,
      `${path}: ${JSON.stringify(actual)} ≠ ${JSON.stringify(expected)}`,
    );
    return;
  }
  if (Array.isArray(expected)) {
    assert.ok(Array.isArray(actual) || ArrayBuffer.isView(actual), `${path}: 배열이 아닙니다`);
    assert.equal(
      actual.length, expected.length,
      `${path}: 길이 ${actual.length} ≠ ${expected.length}`,
    );
    for (let i = 0; i < expected.length; i += 1) {
      same(actual[i], expected[i], { ...options, path: `${path}[${i}]` });
    }
    return;
  }
  assert.ok(actual && typeof actual === 'object', `${path}: 객체가 아닙니다`);
  assert.deepEqual(
    Object.keys(actual).sort(), Object.keys(expected).sort(), `${path}: 키가 다릅니다`,
  );
  for (const key of Object.keys(expected)) {
    same(actual[key], expected[key], { ...options, path: path ? `${path}.${key}` : key });
  }
}
