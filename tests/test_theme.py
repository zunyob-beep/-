"""화면 색이 지켜야 할 것.

분위기는 취향이지만 두 가지는 취향이 아니다.

1. **빨강과 파랑은 뜻이 있다.** 업비트에서 상승은 빨강, 하락은 파랑이다.
   화면을 초록으로 칠하다가 이걸 같이 덮으면, 같은 화면 안에서 색의 뜻이
   두 개가 된다.
2. **읽혀야 한다.** 인광 초록은 예쁘지만 대비를 잃기 쉽다. 특히 밝은
   초록 위의 흰 글씨는 거의 안 보인다(실제로 그렇게 만들었다가 고쳤다).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = (
    Path(__file__).resolve().parent.parent
    / "patternscan" / "webui" / "static" / "style.css"
).read_text(encoding="utf-8")


def tokens() -> dict[str, str]:
    block = CSS[CSS.index(":root {") : CSS.index("}", CSS.index(":root {"))]
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", block))


def _channel(value: int) -> float:
    x = value / 255.0
    return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4


def luminance(hex_colour: str) -> float:
    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(one: str, two: str) -> float:
    a, b = sorted((luminance(one), luminance(two)), reverse=True)
    return (a + 0.05) / (b + 0.05)


# ------------------------------------------------------------- 뜻이 있는 색
def test_up_stays_red_and_down_stays_blue():
    """업비트와 같은 색이어야 한다. 여기서만 뒤집으면 안 된다."""
    palette = tokens()
    up = palette["up"]
    down = palette["down"]
    red, green, blue = (int(up[i : i + 2], 16) for i in (1, 3, 5))
    assert red > green and red > blue, f"상승색 {up}이 빨강 계열이 아닙니다"
    red, green, blue = (int(down[i : i + 2], 16) for i in (1, 3, 5))
    assert blue > red, f"하락색 {down}이 파랑 계열이 아닙니다"


def test_up_and_down_stand_out_from_the_green_around_them():
    """화면 전체가 초록이라, 뜻이 있는 색이 초록빛을 띠면 묻힌다.

    (밝기로 재면 안 된다. 빨강과 파랑은 밝기가 비슷해도 색상이 달라서
    눈으로는 잘 구분된다 — 여기서 봐야 할 건 '초록에 섞이는가'다.)
    """
    palette = tokens()
    for name in ("up", "down"):
        colour = palette[name]
        red, green, blue = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
        assert green < max(red, blue), f"--{name} {colour}이 초록에 가깝습니다"


# ------------------------------------------------------------------- 읽힘
@pytest.mark.parametrize(
    ("front", "back", "least"),
    [
        ("text", "bg", 7.0),        # 큰 글씨·강조
        ("text-2", "panel", 7.0),   # 본문
        ("dim", "panel", 4.5),      # 작은 설명 — 여기가 가장 아슬아슬하다
        ("up", "panel", 4.5),
        ("down", "panel", 4.5),
        ("good", "panel", 4.5),
        ("bad", "panel", 4.5),
        ("warn", "panel", 4.5),
        ("accent", "panel", 4.5),
    ],
)
def test_text_is_readable_on_its_ground(front, back, least):
    palette = tokens()
    ratio = contrast(palette[front], palette[back])
    assert ratio >= least, f"--{front}가 --{back} 위에서 {ratio:.1f}:1 밖에 안 됩니다"


def test_the_primary_button_is_not_white_on_bright_green():
    """대비 1.3:1이었다. 글씨가 있다는 것만 알 수 있는 수준이었다."""
    match = re.search(r"button\.primary\s*\{[^}]*\}", CSS, re.S)
    assert match, "primary 단추 규칙을 못 찾았습니다"
    rule = match.group(0)
    front = re.search(r"color:\s*(#[0-9a-fA-F]{6})", rule)
    assert front, "primary 단추에 글자색이 없습니다"
    assert contrast(front.group(1), tokens()["accent"]) >= 4.5


# ------------------------------------------------------------------ 장식
def test_the_falling_glyphs_never_cover_the_text():
    """장식이 본문 위로 올라오면 읽을 수가 없다."""
    rain = CSS[CSS.index("#rain {") : CSS.index("}", CSS.index("#rain {"))]
    assert "z-index: -2" in rain
    assert "pointer-events: none" in rain


def test_motion_can_be_turned_off():
    """화면이 계속 움직이는 게 불편한 사람이 있다."""
    assert "prefers-reduced-motion" in CSS
    guard = CSS[CSS.index("@media (prefers-reduced-motion"):]
    assert "#rain { display: none; }" in guard[:200]
