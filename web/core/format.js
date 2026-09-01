// 파이썬 f-string 서식을 자바스크립트에서 같은 모양으로.
//
// 화면 문구를 파이썬 쪽과 한 글자도 다르지 않게 맞추려고 따로 뺐다.
// 문구가 갈리면 두 구현이 정말 같은 계산을 하는지 대조할 수가 없다.

/** 파이썬 `f"{x:.N%}"` — 100을 곱하고 소수 N자리. */
export function pct(value, digits = 0) {
  return `${(value * 100).toFixed(digits)}%`;
}

/** 파이썬 `f"{x:+.N%}"` — 부호를 항상 붙인다. */
export function pctSigned(value, digits = 0) {
  const body = (value * 100).toFixed(digits);
  return `${body.startsWith('-') ? '' : '+'}${body}%`;
}

/** 파이썬 `f"{x:,.0f}"` — 천 단위 쉼표, 소수 없음. */
export function won(value) {
  return Math.round(value).toLocaleString('en-US', { maximumFractionDigits: 0 });
}

/** 파이썬 `f"{x:+,.0f}"`. */
export function wonSigned(value) {
  const rounded = Math.round(value);
  const sign = rounded < 0 ? '-' : '+';
  return sign + Math.abs(rounded).toLocaleString('en-US', { maximumFractionDigits: 0 });
}

/** 파이썬 `f"{x:.Nf}"`. */
export function fixed(value, digits = 2) {
  return value.toFixed(digits);
}
