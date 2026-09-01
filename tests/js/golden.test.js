// 파이썬이 내는 숫자와 자바스크립트가 내는 숫자가 같은지 대조한다.
//
// 이 파일이 이 프로젝트에서 제일 중요한 테스트다. 같은 계산이 두 언어로
// 존재하게 됐고, 브라우저에서 도는 쪽은 실제 시세로 검증한 적이 없다.
// "옮겼습니다"의 유일한 증거가 여기 있다.
//
// 정답지는 tools/goldens.py가 만든다. 계산을 고쳤으면 그걸 다시 돌려야 한다.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import test from 'node:test';
import assert from 'node:assert/strict';

import { Series } from '../../web/core/models.js';
import {
  distancesTo, flatMask, isFlat, linearity, normalizeWindow, similarityToDistance,
} from '../../web/core/shape.js';
import { distancesWithin } from '../../web/core/search.js';
import { wilsonInterval } from '../../web/core/stats.js';
import { atr, swings } from '../../web/core/levels.js';
import { readAll, tally } from '../../web/core/theories.js';
import { Odds, findMatches } from '../../web/core/odds.js';
import { analyse, analysisJson, examplesJson, verdict } from '../../web/core/analysis.js';
import { ROUNDED_6, same } from './compare.js';

const here = dirname(fileURLToPath(import.meta.url));
const load = (name) => JSON.parse(readFileSync(join(here, 'goldens', name), 'utf-8'));

const full = load('full.json');
const series = Series.fromCandles(full.market, full.timeframe, full.candles);

test('정답지가 실제 계산을 거친 것인지', () => {
  // 표본이 모자라면 예상 그림도 판정도 계산되지 않는다. 그런 정답지와
  // 대조해 놓고 "통과했다"고 말하면 아무것도 검증하지 않은 것이다.
  assert.ok(full.matches.ends.length >= 20, '닮은 과거가 20개는 나와야 합니다');
  assert.ok(full.analysis.projection.minute1, '예상 그림이 있어야 합니다');
  assert.ok(full.analysis.odds.length > 0, '확률 표가 있어야 합니다');
  assert.ok(full.analysis.theories.minute1.readings.length === 11, '이론 11개가 나와야 합니다');
});

// ------------------------------------------------------------------ 모양
test('모양 정규화가 같다', () => {
  const window = series.close.subarray(100, 140);
  same(Array.from(normalizeWindow(window)), full.shape.normalizeShape, { path: 'shape' });
  same(
    Array.from(normalizeWindow(window, 'amplitude')), full.shape.normalizeAmplitude,
    { path: 'amplitude' },
  );
});

test('평평한 구간 판정이 같다', () => {
  assert.equal(isFlat(series.close.subarray(100, 140)), full.shape.isFlatWindow);
  assert.equal(isFlat(new Float64Array(30).fill(42000000)), full.shape.isFlatConstant);
  same(
    Array.from(flatMask(series.close.subarray(0, 400), 20), (v) => v === 1),
    full.shape.flatMask, { path: 'flatMask' },
  );
});

test('직선성이 같다', () => {
  same(linearity(series.close.subarray(100, 140)), full.shape.linearity, { path: 'linearity' });
  const rising = Float64Array.from({ length: 40 }, (_, i) => i + 1);
  same(linearity(rising), full.shape.linearityRising, { path: 'linearityRising' });
});

test('유사도와 거리의 변환이 같다', () => {
  const inputs = [1, 0.9, 0.8, 0.5, 0, -1, 2];
  same(inputs.map(similarityToDistance), full.shape.similarityToDistance, { path: '변환' });
});

test('전수 거리 계산이 같다', () => {
  const got = distancesTo(series.close.subarray(200, 220), series.close.subarray(0, 400), 20);
  same(Array.from(got), full.shape.distancesTo, { path: 'distancesTo' });
});

test('윌슨 신뢰구간이 같다', () => {
  const cases = [[0, 0], [5, 10], [1, 3], [97, 100]];
  same(cases.map(([k, n]) => wilsonInterval(k, n)), full.shape.wilson, { path: 'wilson' });
});

// ------------------------------------------------------------- 지지·저항
test('변동폭과 변곡점이 같다', () => {
  same(atr(series), full.levels.atr, { path: 'atr' });
  const { highs, lows } = swings(series);
  same(highs, full.levels.swingHighs, { path: 'swingHighs' });
  same(lows, full.levels.swingLows, { path: 'swingLows' });
});

// ---------------------------------------------------------------- 이론
test('이론들의 읽기가 글자까지 같다', () => {
  const readings = readAll(series).map((r) => ({
    theory: r.theory, says: r.says, detail: r.detail, clarity: r.clarity,
  }));
  same(
    readings, full.analysis.theories.minute1.readings.map((r) => ({
      theory: r.theory, says: r.says, detail: r.detail, clarity: r.clarity,
    })),
    { path: 'readings', absolute: (p) => (p.endsWith('.clarity') ? 5e-3 : null) },
  );
  same(tally(readAll(series)), full.theories.tally, { path: 'tally' });
});

// ------------------------------------------------------------- 닮은 과거
test('닮은 과거를 같은 자리에서 찾는다', () => {
  const found = findMatches(series, full.length, { similarity: full.similarity, topK: 100 });
  assert.ok(found !== null);
  same(found.ends, full.matches.ends, { path: 'ends' });
  same(found.distances, full.matches.distances, { path: 'distances' });
  assert.equal(found.limit, full.matches.limit);
});

test('하한으로 걸러도 전수 계산과 결과가 같다', () => {
  // 구간이 2만 개를 넘어야 하한 거르기 경로를 탄다.
  const paa = load('paa.json');
  const closes = Float64Array.from(paa.closes);
  for (const [name, wanted] of Object.entries(paa.within)) {
    const query = closes.subarray(closes.length - wanted.length);
    const values = closes.subarray(0, closes.length - wanted.length);
    assert.ok(values.length - wanted.length + 1 > 20000, `${name}: 하한 경로를 안 탑니다`);
    const got = distancesWithin(query, values, wanted.length, wanted.threshold);
    same(got.positions, wanted.positions, { path: `${name}.positions` });
    same(got.distances, wanted.distances, { path: `${name}.distances` });
  }
});

// ---------------------------------------------------------------- 판정
test('판정의 네 관문이 모두 같게 걸린다', () => {
  for (const one of full.verdictCases) {
    const rows = one.rows.map((r) => new Odds(r));
    same(verdict(rows, full.cost), one.verdict, { path: `판정.${one.name}` });
  }
});

// -------------------------------------------------------- 통짜 결과 대조
//
// 화면이 실제로 받는 것이 이 모양이다. 위의 테스트들은 갈렸을 때 어디서
// 갈렸는지 짚기 위한 이정표고, 진짜 검증은 이것이다.
test('화면에 넘기는 결과 전체가 같다', () => {
  const analysis = analyse(full.market, { minute1: series }, {
    similarity: full.similarity,
    fee: full.fee,
    slippage: full.slippage,
    length: full.length,
    points: full.points,
    updatedAt: '00:00:00',
  });
  same(analysisJson(analysis), full.analysis, {
    path: '',
    // 예상 경로와 최근 봉은 파이썬이 소수 6자리에서 반올림해 내보낸다.
    // 그 자리는 반올림 폭 안에서만 같으면 된다.
    //
    // spread만 두 배로 본다. 파이썬은 **이미 반올림한** 두 값을 빼서
    // 구하므로 반올림 오차가 두 번 실린다. 자바스크립트 쪽은 원래 값에서
    // 빼므로 이쪽이 더 정확한데, 정확한 쪽을 굳이 흐리게 만들 이유가 없다.
    absolute: (path) => {
      if (/^projection\.[^.]+\.spread$/.test(path)) return 2 * ROUNDED_6;
      return /^projection\./.test(path) ? ROUNDED_6 : null;
    },
  });
});

test('봉 간격 셋을 한꺼번에 봐도 같다', () => {
  // full.json은 1분봉 하나뿐이라 **봉 간격끼리 비교하는 코드가 한 번도
  // 안 돌아간다.** 다우의 '상호 확인'이 그렇다 — 1·3·5분봉이 서로 같은
  // 말을 하는지 세는 것이라, 간격이 하나면 셀 것이 없어 검증 밖에 있었다.
  const many = load('many.json');
  const bundle = {};
  for (const [timeframe, candles] of Object.entries(many.candles)) {
    bundle[timeframe] = Series.fromCandles(many.market, timeframe, candles);
  }
  assert.equal(Object.keys(bundle).length, 3, '봉 간격 셋이어야 의미가 있습니다');

  const analysis = analyse(many.market, bundle, {
    similarity: many.similarity,
    fee: many.fee,
    slippage: many.slippage,
    length: many.length,
    points: many.points,
    updatedAt: '00:00:00',
  });
  const got = analysisJson(analysis);

  // 이 정답지가 실제로 상호 확인을 거쳤는지부터 확인한다. 안 거쳤으면
  // 통과해도 아무것도 검증하지 않은 것이다.
  assert.ok(
    many.analysis.theories.confirmation.detail.length > 3,
    '상호 확인이 계산되지 않은 정답지입니다',
  );
  same(got, many.analysis, {
    path: '',
    absolute: (path) => {
      if (/^projection\.[^.]+\.spread$/.test(path)) return 2 * ROUNDED_6;
      return /^projection\./.test(path) ? ROUNDED_6 : null;
    },
  });
});

test('사례 목록도 같다', () => {
  const analysis = analyse(full.market, { minute1: series }, {
    similarity: full.similarity,
    fee: full.fee,
    slippage: full.slippage,
    length: full.length,
    points: full.points,
    updatedAt: '00:00:00',
  });
  same(examplesJson(analysis, 'minute1', 10), full.examples, {
    path: '사례',
    absolute: (path) => (/(shape|after|similarity)/.test(path) ? ROUNDED_6 : null),
  });
});
