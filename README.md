# btcbot — 업비트 비트코인 자동매매 봇

[![CI](https://github.com/zunyob-beep/-/actions/workflows/ci.yml/badge.svg)](https://github.com/zunyob-beep/-/actions/workflows/ci.yml)

백테스트 · 페이퍼 트레이딩 · 실거래가 **같은 엔진 위에서** 돌아가는 자동매매 봇입니다.
외부 의존성은 `requests` 하나뿐입니다.

```
봉 마감 → 전략 판단 → 리스크 심사 → 주문 → 기록
```

이 순서가 세 모드에서 완전히 동일합니다. 백테스트에만 있는 매매 로직이 따로 없기 때문에,
백테스트에서 본 숫자가 실거래에서 재현될 가능성이 그만큼 높습니다.

---

## ⚠️ 먼저 읽어주세요

- **자동매매는 원금을 잃을 수 있습니다.** 이 코드는 투자 조언이 아니며, 수익을 보장하지 않습니다.
- 반드시 **페이퍼 모드로 최소 몇 주** 돌려 본 뒤에 실거래를 켜세요.
- 실거래는 처음에 **잃어도 괜찮은 금액**으로 시작하세요. `--max-weight`로 비중을 제한할 수 있습니다.
- 업비트 API 키는 **출금 권한 없이** 발급하고, 허용 IP를 등록하세요. 키가 유출돼도 자산을 빼갈 수 없습니다.
- 백테스트 성적이 좋다고 미래가 같지 않습니다. 특히 `optimize`로 고른 파라미터는
  과거에만 맞춰진 것일 가능성이 큽니다 — `--walk-forward`로 반드시 검증하세요.

---

## 설치

```bash
git clone <이 저장소>
cd btcbot
pip install -e .            # 또는: pip install requests
```

Python 3.10 이상이 필요합니다.
설정 파일을 YAML로 쓰려면 `pip install -e ".[yaml]"`,
말로 전략을 설명하는 기능을 쓰려면 `pip install -e ".[ai]"`.

### API 키 설정 (실거래에만 필요)

[업비트 Open API 관리](https://upbit.com/mypage/open_api_management)에서 키를 발급받고,
`.env` 파일을 만드세요.

```bash
cp .env.example .env
# .env를 열어 키를 채웁니다
```

```
UPBIT_ACCESS_KEY=발급받은_액세스_키
UPBIT_SECRET_KEY=발급받은_시크릿_키
```

키는 환경변수로만 읽습니다. **설정 파일(config.json)에 키를 적어도 무시됩니다** — 실수로
커밋되는 사고를 구조적으로 막기 위해서입니다. `.env`는 `.gitignore`에 이미 들어 있습니다.

---

## 코딩을 모른다면 — 웹 화면으로 쓰기

터미널 명령어 없이 **마우스로만** 전략을 만들고 돌릴 수 있습니다.

```bash
python -m btcbot ui
```

브라우저가 자동으로 열립니다 (안 열리면 http://127.0.0.1:8765 를 직접 입력하세요).

화면은 세 단계로 되어 있습니다.

**💬 말로 설명하기** — 원하는 전략을 그냥 문장으로 씁니다.

> "RSI가 30 아래로 내려가면 사고 55를 넘으면 팔아. 단 200일선 위일 때만. 5% 빠지면 손절."

Claude가 이걸 조건으로 옮겨서 빌더에 채워줍니다. 손절·익절 같은 말은 조건이 아니라
**리스크 설정 칸**으로 들어갑니다(진입가 대비로 계산돼야 하니까요).

중요한 건 **바로 실행되지 않는다**는 점입니다. 무엇을 어떻게 이해했는지 한국어로 먼저
보여주고, 추측해서 채운 값이 있으면 알려줍니다("RSI 기간은 말씀이 없어 14로 가정했습니다").
확인하고 고친 뒤에 저장하는 건 사람의 몫입니다.

지원하지 않는 개념(뉴스 감성, 김치프리미엄, 호가창 등)을 요청하면 **비슷한 지표로 슬쩍
바꾸지 않고 거절합니다.** 요청한 것과 다른 전략이 돌아가는 게 가장 나쁜 결과이기 때문입니다.

이 기능에는 [Claude API 키](https://console.anthropic.com)가 필요합니다:

```bash
pip install "btcbot[ai]"           # 또는: pip install anthropic
echo 'ANTHROPIC_API_KEY=sk-...' >> .env
```

터미널에서도 씁니다:

```bash
python -m btcbot describe "20일선을 위로 뚫으면 사고 아래로 빠지면 판다" --save my.json
python -m btcbot backtest --strategy rule -p spec_file=my.json
```

**① 전략 만들기** — 예시를 하나 고르고 숫자만 바꾸면 됩니다.
"RSI(14)가 30보다 작다" 처럼 조건을 드롭다운으로 조합하고, 조건이 몇 개든
"모두 만족" 또는 "하나만 만족"으로 묶습니다. 만든 전략은 이름을 붙여 저장됩니다.

**② 과거로 검증** — 만든 전략을 과거 시세에 그대로 돌려봅니다. 수익률·최대낙폭·
승률과 자산 곡선, 개별 거래 내역이 나옵니다. **그냥 들고 있었을 때와 항상 비교해서
보여줍니다** — 그걸 못 이기면 전략을 쓸 이유가 없기 때문입니다.

**③ 자동매매 돌리기** — 모의 투자(주문 안 나감)와 실전 투자를 버튼으로 시작·중지합니다.
실전을 고르면 손절·일일 손실 한도·최대 낙폭 같은 보호장치가 자동으로 채워지고,
확인란에 체크해야만 시작됩니다.

화면에서 만든 전략은 터미널에서도 그대로 쓸 수 있습니다:

```bash
python -m btcbot backtest --strategy rule -p spec_file=strategies_saved.json
```

> 이 서버는 **내 컴퓨터에서만(127.0.0.1)** 열립니다. 인터넷에 노출되지 않으므로
> API 키가 밖으로 나가지 않습니다. 외부에 공개하지 마세요 — 남이 내 계좌로
> 주문을 낼 수 있게 됩니다.

---

## 5분 만에 해보기

```bash
# 1. 사용 가능한 전략 확인
python -m btcbot strategies

# 2. 과거 데이터 내려받기 (CSV로 캐시됨)
python -m btcbot fetch --interval day --start 2021-01-01

# 3. 백테스트
python -m btcbot backtest --strategy vb -p k=0.5 --interval day --start 2021-01-01

# 4. 실시간 모의매매 (주문은 나가지 않습니다)
python -m btcbot paper --strategy vb -p k=0.5 --interval minute60 --verbose

# 5. 실거래 (진짜 돈이 나갑니다)
python -m btcbot live --strategy vb -p k=0.5 --interval minute60 \
    --max-weight 0.3 --stop-loss 0.05 --daily-loss-limit 0.03 --max-drawdown 0.20
```

---

## 전략

`--strategy` 로 고르고 `-p 키=값` 으로 파라미터를 넘깁니다.

### `vb` — 변동성 돌파 (Larry Williams)

```
목표가 = 오늘 시가 + (어제 고가 − 어제 저가) × k
```

종가가 목표가를 넘으면 진입하고, 날짜(KST 기준)가 바뀌면 청산합니다.

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `k` | `0.5` | 돌파 계수. 작을수록 자주 진입 |
| `dynamic_k` | `false` | 최근 봉들의 노이즈 평균을 k로 사용 |
| `noise_period` | `20` | `dynamic_k`용 관측 구간 |
| `ma_period` | `0` | `>0`이면 이동평균 위일 때만 진입 (하락장 반등 회피) |

```bash
python -m btcbot backtest --strategy vb -p k=0.4 -p ma_period=20 --interval minute60
```

> 일봉으로 백테스트하면 장중 돌파를 놓쳐 실제보다 보수적으로 나옵니다.
> **60분봉 이하로 돌리는 편이 실제 봇 동작에 가깝습니다.**

### `ma_cross` — 이동평균 교차 (추세추종)

단기 이평이 장기 이평 위면 보유, 아래면 현금. "교차 이벤트"가 아니라 "현재 상태"로
판단하기 때문에 봇을 재시작해도 포지션이 어긋나지 않습니다.

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `fast` / `slow` | `10` / `30` | 단기 / 장기 기간 |
| `kind` | `ema` | `ema` 또는 `sma` |
| `atr_stop_mult` | `0.0` | `>0`이면 ATR 배수만큼 아래에 손절선 제시 |

### `rsi` — RSI 평균회귀

RSI가 과매도 구간에 들어가면 분할 매수하고, 회복하면 청산합니다.

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `period` | `14` | RSI 기간 |
| `oversold` / `exit_rsi` | `30` / `55` | 진입선 / 청산선 |
| `trend_ma` | `200` | 이 이평 아래면 매수 금지 (`0`이면 끔) |
| `scale_in` | `true` | RSI가 낮을수록 비중 확대 |

### `rule` — 조건 조합 전략 (코딩 불필요)

웹 화면의 전략 빌더가 만들어내는 전략입니다. JSON으로 조건을 표현합니다.

```json
{
  "label": "RSI 과매도 반등",
  "entry": {"all": [
    {"left": {"type": "rsi", "period": 14}, "op": "<", "right": 30},
    {"left": {"type": "close"}, "op": ">", "right": {"type": "sma", "period": 200}}
  ]},
  "exit": {"any": [
    {"left": {"type": "rsi", "period": 14}, "op": ">", "right": 55}
  ]}
}
```

쓸 수 있는 지표: 시가/고가/저가/종가/거래량, SMA, EMA, RSI, ATR, 볼린저 상·중·하단,
N봉 최고·최저가, 변동성 돌파 목표가, 숫자 상수.
비교: `>` `>=` `<` `<=` `cross_above`(골든크로스) `cross_below`(데드크로스).
묶음: `all`(그리고) · `any`(또는) · `not`. 중첩할 수 있습니다.

```bash
python -m btcbot backtest --strategy rule -p spec_file=my_strategy.json
```

### 전략 직접 만들기

`Strategy`를 상속하고 `@register`만 붙이면 CLI에 자동으로 나타납니다.

```python
# btcbot/strategies/my_strategy.py
from ..models import Action, Signal
from .base import Strategy, register

@register
class MyStrategy(Strategy):
    """내 전략 한 줄 설명."""

    name = "mine"

    @classmethod
    def defaults(cls):
        return {"threshold": 0.02}

    def __init__(self, **params):
        super().__init__(**params)
        self.warmup = 20          # 판단에 필요한 최소 봉 개수

    def decide(self, candles):
        # candles[-1]은 방금 '닫힌' 봉입니다. 다음 봉 시가에 체결됩니다.
        change = candles[-1].close / candles[-20].close - 1
        if change > self.params["threshold"]:
            return Signal(Action.BUY, target_weight=1.0, reason=f"20봉 +{change:.1%}")
        return Signal(Action.SELL, target_weight=0.0, reason="조건 미달")
```

마지막으로 `btcbot/strategies/__init__.py`에 import 한 줄을 추가하세요.

**규칙 두 가지:**

1. `decide()`는 **닫힌 봉만** 받습니다. 미래 봉은 애초에 넘어오지 않습니다.
2. `decide()`는 상태를 쌓지 마세요. 매번 같은 입력에 같은 답을 내야 재시작해도 안전합니다.

**`target_weight`의 의미:**

| 값 | 뜻 |
|---|---|
| `1.0` | 총자산의 100%를 코인으로 |
| `0.5` | 절반만 |
| `0.0` | **전량 청산** |
| `None` | **현재 비중 유지** (사지도 팔지도 않음) |

`0.0`과 `None`을 구분하는 게 중요합니다. RSI 중립 구간처럼 "새로 사지는 않지만 갖고 있는 건 유지"를
표현하려면 `None`을 써야 합니다.

---

## 리스크 관리

**전략보다 항상 우선합니다.** 전략이 사라고 해도 여기서 막으면 못 삽니다.
계좌를 살리는 건 좋은 진입이 아니라 나쁜 상황에서의 강제 이탈입니다.

| 옵션 | 설명 |
|---|---|
| `--max-weight 0.3` | 총자산의 30%까지만 코인으로 |
| `--stop-loss 0.05` | 평단 대비 −5%면 청산 |
| `--take-profit 0.15` | 평단 대비 +15%면 청산 |
| `--trailing-stop 0.07` | 진입 후 고점 대비 −7%면 청산 |
| `--daily-loss-limit 0.03` | 당일 −3% 도달 시 청산 후 **그날은 진입 금지** |
| `--max-drawdown 0.20` | 최고 자산 대비 −20%면 **봇 정지 (킬 스위치)** |
| `--cooldown 5` | 손절 후 5봉 쉬기 |

킬 스위치가 발동하면 자산이 회복돼도 자동으로 재개하지 않습니다. 사람이 상황을 확인하고
`runs/<이름>/state.json`을 지운 뒤 다시 켜야 합니다. 의도된 동작입니다 — 뭔가 잘못됐을 때
봇이 스스로 판단해서 다시 뛰어드는 것이 가장 위험합니다.

이 상태는 **디스크에 저장**되므로 봇이 죽었다 살아나도 일일 한도와 트레일링 고점이
초기화되지 않습니다.

---

## 명령어

### `fetch` — 데이터 수집

```bash
python -m btcbot fetch --interval minute60 --start 2023-01-01
```

`data/KRW-BTC_minute60.csv`에 캐시됩니다. 이후 백테스트는 캐시를 먼저 쓰고 모자란 구간만
API로 채웁니다. 네트워크가 끊기면 캐시만으로 진행합니다(경고 출력).

### `backtest` — 전략 검증

```bash
python -m btcbot backtest --strategy vb -p k=0.5 \
    --interval day --start 2021-01-01 \
    --fee 0.0005 --slippage 0.001 \
    --stop-loss 0.05 --show-trades --csv curve.csv
```

출력 지표: 총수익률 · 바이앤홀드 대비 초과수익 · CAGR · MDD · 낙폭 지속일 · 변동성 ·
샤프 · 거래횟수 · 승률 · 손익비 · 총수수료 · 시장노출.

**바이앤홀드를 못 이기면 그 전략은 쓸 이유가 없습니다.** 그래서 항상 같이 표시합니다.

### `optimize` — 파라미터 탐색

```bash
# 전수 탐색
python -m btcbot optimize --strategy vb -g k=0.3,0.4,0.5,0.6,0.7 -g ma_period=0,10,20

# 앞 70%로 고르고 뒤 30%로 검증 (권장)
python -m btcbot optimize --strategy ma_cross \
    -g fast=5,10,15 -g slow=20,30,50 --walk-forward
```

`--walk-forward`는 학습 구간과 검증 구간의 샤프 차이를 보여줍니다. 차이가 크면
과최적화입니다 — 그 파라미터는 버리세요.

### `paper` — 모의매매

실시간 시세를 받아 실제와 똑같이 돌지만 **주문은 나가지 않습니다.** API 키도 필요 없습니다.
체결과 손익은 `runs/<이름>/`에 기록되고, 껐다 켜도 이전 포지션을 이어받습니다.

```bash
python -m btcbot paper --strategy vb --interval minute60 --cash 1000000 --verbose
```

### `live` — 실거래

```bash
python -m btcbot live --strategy vb --interval minute60 \
    --max-weight 0.3 --stop-loss 0.05 --max-drawdown 0.20
```

`LIVE`를 직접 입력해야 시작합니다. `--dry-run`을 붙이면 주문 직전까지만 수행하고
전송은 생략하므로, 실제 시세로 로직을 점검할 수 있습니다.

### `describe` — 말로 설명한 전략을 조건으로

```bash
python -m btcbot describe "RSI가 30 아래면 사고 55 넘으면 팔아" --save my.json
```

`ANTHROPIC_API_KEY`가 필요합니다. 결과를 보여주기만 하고, `--save` 없이는 아무것도
저장하지 않습니다.

### `ui` — 웹 화면

```bash
python -m btcbot ui --port 8765          # 기본 8765
python -m btcbot ui --no-browser         # 브라우저 자동 실행 끄기
```

### `status` — 상태 확인

```bash
python -m btcbot status --run-name live-KRW-BTC-vb
python -m btcbot status --account          # 업비트 실계좌 잔고도 조회
```

---

## 설정 파일

매번 긴 옵션을 치기 싫다면:

```bash
cp config.example.json config.json
python -m btcbot --config config.json paper
```

CLI 인자가 설정 파일보다 우선합니다.

---

## 알림

체결이 일어날 때 슬랙/디스코드 웹훅으로 알림을 받을 수 있습니다.

```bash
export BTCBOT_WEBHOOK_URL="https://hooks.slack.com/services/..."
python -m btcbot live --strategy vb --interval minute60
```

알림 전송이 실패해도 매매는 그대로 진행됩니다.

---

## 구조

```
btcbot/
├── models.py        Candle, Signal, Fill, Position, AccountState — 공통 자료구조
├── indicators.py    SMA, EMA, RSI, ATR, 볼린저, 노이즈 (순수 파이썬)
├── strategies/      전략들 (base.py의 레지스트리에 자동 등록)
│   └── rule.py      조건 조합 전략 — 웹 빌더가 만드는 것
├── exchange/
│   ├── base.py      Broker 인터페이스 + 거래소 규칙(최소 주문금액, 호가 단위)
│   ├── simulated.py 모의 브로커 — 백테스트/페이퍼가 공유 (수수료·슬리피지 반영)
│   └── upbit.py     업비트 REST + JWT 인증 + 실거래 브로커
├── feed.py          Bar 공급 — BacktestFeed / LiveFeed (같은 인터페이스)
├── execution.py     목표 비중 → 실제 주문 (단 하나의 경로)
├── risk.py          손절·익절·트레일링·일일한도·킬스위치
├── engine.py        매매 루프 (세 모드 공용)
├── backtest.py      백테스트 러너 + 격자 탐색 + 워크포워드
├── metrics.py       성과 지표
├── storage.py       체결/거래 기록(JSONL) + 상태 저장(원자적 쓰기)
├── nlstrategy.py    말로 쓴 설명 -> 조건 (Claude API)
├── runner.py        페이퍼/실거래 배선
└── cli.py           명령줄 인터페이스
```

### 설계에서 중요한 세 가지

**1. 미래를 훔쳐볼 수 없는 구조**

`Feed`는 닫힌 봉만 넘기고, 체결은 항상 다음 봉의 시가에서 일어납니다. 백테스트에서
"오늘 종가를 보고 오늘 종가에 산다" 같은 일이 코드 구조상 불가능합니다.

**2. 주문 경로는 하나뿐**

"얼마를 살까"는 `execution.reconcile()`에만 있습니다. 백테스트와 실거래에 각각
사이징 로직을 두면 둘이 갈라지는데, 그 차이는 아주 늦게 — 보통 돈을 잃은 뒤에 —
발견됩니다.

**3. 재시작해도 이어짐**

체결마다 리스크 상태와 계좌를 디스크에 씁니다(임시 파일 → 원자적 교체). 봇이 죽어도
일일 손실 한도와 트레일링 고점이 초기화되지 않습니다.

---

## 개발

```bash
pip install -e ".[dev]"
python -m pytest            # 280개 테스트, 네트워크 불필요
python -m ruff check btcbot tests
```

CI는 Python 3.10~3.13에서 테스트와 린트를 돌립니다(`.github/workflows/ci.yml`).

테스트는 전부 오프라인입니다. 업비트 API는 가짜 세션으로 흉내내고, JWT 서명은 검증 로직을
따로 재현해 대조합니다 — 실거래 중 401을 받고 나서 디버깅하지 않기 위해서입니다.

`test_live_loop.py`는 페이퍼/실거래 루프를 실제로 끝까지 통과시킵니다. 백테스트만
검증하면 정작 돈이 오가는 코드는 한 번도 실행해보지 않은 채 배포하게 됩니다.

---

## 알아두면 좋은 것들

**수수료와 슬리피지를 반드시 넣으세요.** 기본값은 업비트 원화 마켓 기준 0.05%입니다.
`--fee 0` 으로 돌린 백테스트는 현실이 아닙니다. 특히 짧은 봉에서 자주 매매하는 전략은
수수료만으로 계좌가 녹습니다.

**재조정 밴드(`--band`)가 잔주문을 막습니다.** 기본 5%. 가격이 조금 움직일 때마다
비중을 정확히 맞추려 들면 수수료로 다 나갑니다. 단, 완전 청산과 신규 진입은
밴드와 무관하게 즉시 실행됩니다.

**최소 주문 금액은 5,000원입니다.** 그보다 작은 주문은 아예 전송하지 않습니다.

**시장가 주문만 씁니다.** 지정가는 체결 안 될 때의 처리(취소, 재주문, 부분체결)가
훨씬 복잡하고, 그 복잡함이 실거래에서 사고를 냅니다.

**호가 단위 표는 참고용입니다.** 시장가 주문에는 필요 없고, 업비트가 개정하면 조용히
틀린 값이 됩니다. 지정가로 확장한다면 `GET /v1/orders/chance`가 주는 실시간 제약을 쓰세요.

---

## 라이선스

MIT
