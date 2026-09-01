"""요청을 얼마나 빨리 내보내는가.

아이패드에서 두 번 연속 같은 자리에서 막혔다. 맨 위 시세와 첫 쪽(200개)은
받아지는데 그 뒤가 전부 실패했다. 원인은 속도 제한기였다 — **초당 회수만
지키고 간격은 안 지켰다.** 창이 비어 있으면 여덟 개가 한꺼번에 나가고 남은
시간을 쉰다. 평균은 초당 8회지만 순간 속도는 그보다 훨씬 빠르다.

브라우저 판에서 먼저 잡았고, 파이썬 판에도 같은 결함이 있었다. 두 판이
같은 실수를 하지 않도록 양쪽에 같은 시험을 둔다.
"""

from __future__ import annotations

import threading
import time
from itertools import pairwise

from patternscan.upbit import RateLimiter


def gaps(times: list[float]) -> list[float]:
    return [b - a for a, b in pairwise(times)]


def test_요청을_한꺼번에_쏘지_않고_고르게_벌린다() -> None:
    limiter = RateLimiter(per_second=20)  # 간격 50ms
    at = []
    for _ in range(4):
        limiter.acquire()
        at.append(time.monotonic())

    # 첫 번째는 기다릴 이유가 없으니 그 다음부터 본다. 타이머가 정확하지
    # 않으므로 넉넉히 보되, 요지는 **0이 아니어야** 한다는 것이다.
    assert all(g >= 0.035 for g in gaps(at)), f"요청이 붙어서 나갔습니다: {gaps(at)}"


def test_여러_스레드가_동시에_불러도_줄을_선다() -> None:
    # 봉 간격 세 종을 동시에 받으면 acquire가 겹쳐 불린다. 기다린 뒤에
    # 자리를 잡으면 셋이 같은 자리를 잡고 함께 나간다.
    limiter = RateLimiter(per_second=20)
    at: list[float] = []
    lock = threading.Lock()

    def one() -> None:
        limiter.acquire()
        with lock:
            at.append(time.monotonic())

    threads = [threading.Thread(target=one) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    at.sort()
    assert all(g >= 0.035 for g in gaps(at)), f"동시 요청이 붙어서 나갔습니다: {gaps(at)}"


def test_간격은_초당_회수에서_나온다() -> None:
    assert RateLimiter(per_second=5).gap == 0.2
    assert RateLimiter(per_second=10).gap == 0.1
    # 0이나 음수를 줘도 나눗셈이 터지지 않아야 한다.
    assert RateLimiter(per_second=0).gap == 1.0
