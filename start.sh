#!/usr/bin/env bash
# 한 번에 실행하기 (맥·리눅스)
#
#   ./start.sh              이 컴퓨터에서만 열기
#   ./start.sh --lan        같은 와이파이의 아이패드·폰에서도 열기
#
# 처음 실행하면 파이썬 환경을 만들고 업비트 시세를 받습니다.
# 두 번째부터는 받아둔 것에 새 봉만 이어 붙이므로 금방 뜹니다.

set -euo pipefail
cd "$(dirname "$0")"

BOLD=$'\033[1m'; DIM=$'\033[2m'; WARN=$'\033[33m'; OFF=$'\033[0m'
say() { printf '%s\n' "$*"; }

# ---------------------------------------------------------------- 파이썬 찾기
PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PY="$candidate"; break
    fi
  fi
done

if [ -z "$PY" ]; then
  say "${WARN}파이썬 3.10 이상이 필요합니다.${OFF}"
  say ""
  say "  맥:      brew install python3"
  say "  또는:    https://www.python.org/downloads/ 에서 내려받기"
  say ""
  say "설치한 뒤 이 파일을 다시 실행하세요."
  exit 1
fi

# ---------------------------------------------------------------- 환경 준비
if [ ! -d .venv ]; then
  say "${BOLD}처음 실행이라 준비를 합니다. 1~2분 걸립니다.${OFF}"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if ! python -c 'import patternscan' 2>/dev/null; then
  say "${DIM}필요한 것을 설치하는 중…${OFF}"
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -e .
fi

# ---------------------------------------------------------------- 시세 확보
MARKET="${MARKET:-KRW-BTC}"
COUNT="${COUNT:-129600}"   # 1분봉 90일치. 8년치를 원하면 COUNT=4204800

if [ ! -f "data/${MARKET}_minute1.csv" ]; then
  say ""
  say "${BOLD}업비트에서 시세를 받습니다.${OFF} 처음 한 번만 오래 걸립니다."
  say "${DIM}중간에 끊겨도 받은 만큼 저장되니, 다시 실행하면 이어서 받습니다.${OFF}"
  say ""
fi
# 시세를 못 받아도 **화면은 연다.** 예전에는 여기서 죽어서, 잠깐 인터넷이
# 안 되거나 업비트가 막힌 것뿐인데 빨간 글씨만 잔뜩 보고 끝났다. 화면은
# 받아둔 시세로도 돌아가고, 아무것도 없으면 무엇을 하면 되는지 안내한다.
if ! python -m patternscan fetch --market "$MARKET" --count "$COUNT"; then
  say ""
  say "${BOLD}시세를 받지 못했습니다.${OFF} 그래도 화면은 엽니다."
  say "${DIM}인터넷이 안 되거나 업비트가 막혀 있을 수 있습니다."
  say "어디가 막혔는지 보려면 다른 창에서:  python -m patternscan doctor${OFF}"
  say ""
fi

# ---------------------------------------------------------------- 화면 열기
HOST_ARG=()
if [ "${1:-}" = "--lan" ]; then
  HOST_ARG=(--host 0.0.0.0)
fi

say ""
say "${BOLD}화면을 엽니다.${OFF} 끝내려면 이 창에서 Ctrl+C 를 누르세요."
say "${DIM}브라우저에서 '홈 화면에 추가'를 하면 앱 아이콘처럼 쓸 수 있습니다.${OFF}"
say ""
exec python -m patternscan ui --market "$MARKET" "${HOST_ARG[@]}"
