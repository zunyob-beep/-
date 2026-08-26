"""아이콘이 정말 그리는 코드의 결과물인지 확인한다.

PNG는 diff가 안 된다. 누가 손으로 바꿔 넣어도 리뷰에서 안 보이고, 그러면
`tools/make_icons.py`는 있으나 마나 한 장식이 된다. 여기서 묶어 둔다.
"""

from __future__ import annotations

import importlib.util
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "patternscan" / "webui" / "static"


def _load_tool():
    spec = importlib.util.spec_from_file_location("make_icons", ROOT / "tools" / "make_icons.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_icons = _load_tool()


def test_the_committed_icon_is_what_the_code_draws(tmp_path):
    """손으로 갈아 끼운 PNG를 잡아낸다."""
    fresh = make_icons.write_png(
        tmp_path / "apple-touch-icon.png", make_icons.render(180, bleed=True)
    )
    assert fresh.read_bytes() == (STATIC / "apple-touch-icon.png").read_bytes()


@pytest.mark.parametrize(
    ("name", "size"),
    [("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)],
)
def test_each_icon_is_a_png_of_the_size_it_claims(name, size):
    body = (STATIC / name).read_bytes()
    assert body[:8] == b"\x89PNG\r\n\x1a\n"
    width, height, depth, colortype = struct.unpack(">IIBB", body[16:26])
    assert (width, height) == (size, size)
    assert (depth, colortype) == (8, 6)  # 8비트 RGBA


def test_the_apple_icon_is_not_pre_rounded():
    """iOS가 다시 둥글리므로, 이미 둥근 걸 주면 모서리에 검은 띠가 남는다."""
    image = make_icons.render(64, bleed=True)
    assert image[0, 0, 3] == 255, "모서리가 투명합니다 — 이미 둥글려져 있습니다"
    assert image[0, -1, 3] == 255


def test_the_normal_icon_is_rounded():
    image = make_icons.render(64)
    assert image[0, 0, 3] == 0, "모서리가 안 둥글려졌습니다"
    assert image[32, 32, 3] == 255


def test_the_line_is_actually_drawn():
    """배경만 있는 사각형이 나오면 아이콘이 아니라 그냥 검은 칸이다."""
    image = make_icons.render(64)
    blue = image[..., 2].astype(int) - image[..., 0].astype(int)
    assert (blue > 60).sum() > 200, "파란 선이 안 보입니다"
