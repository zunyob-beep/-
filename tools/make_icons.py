"""홈 화면 아이콘을 그린다.

    python tools/make_icons.py

바이너리를 저장소에 그냥 넣어두면 나중에 색 하나 바꾸려 해도 손댈 방법이
없다. 그래서 그리는 코드를 남기고, PNG는 그 결과물로 둔다.

의존성은 numpy와 표준 라이브러리(zlib)뿐이다 — 아이콘 하나 만들자고
이미지 라이브러리를 새로 깔게 하고 싶지 않았다.
"""

from __future__ import annotations

import struct
import zlib
from itertools import pairwise
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent.parent / "patternscan" / "webui" / "static"

GROUND = (0x0F, 0x11, 0x16)
LINE = (0x3D, 0x7E, 0xFF)

#: 파비콘과 같은 꺾은선. 32칸 좌표계 기준.
POINTS = [(5, 21), (11, 13), (16, 17), (21, 7), (27, 12)]

#: 4배로 그린 뒤 줄인다. 계단이 안 보이는 가장 싼 방법이다.
SUPER = 4


def _segment_distance(
    xs: np.ndarray, ys: np.ndarray, a: tuple[float, float], b: tuple[float, float]
) -> np.ndarray:
    """모든 점에서 선분 ab까지의 거리."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return np.hypot(xs - ax, ys - ay)
    t = np.clip(((xs - ax) * dx + (ys - ay) * dy) / length2, 0.0, 1.0)
    return np.hypot(xs - (ax + t * dx), ys - (ay + t * dy))


def render(size: int, *, bleed: bool = False) -> np.ndarray:
    """RGBA 배열 하나. `bleed`면 모서리를 둥글리지 않고 꽉 채운다.

    iOS는 홈 화면 아이콘을 **자기가** 둥글립니다. 이미 둥근 아이콘을 주면
    모서리가 두 번 깎여 안쪽에 검은 띠가 생긴다. 그래서 애플용은 bleed다.
    """
    big = size * SUPER
    ys, xs = np.mgrid[0:big, 0:big].astype(np.float64)
    # 픽셀 중심으로 옮긴 뒤 32칸 좌표계로
    unit = (xs + 0.5) / big * 32.0, (ys + 0.5) / big * 32.0

    if bleed:
        inside = np.ones((big, big))
        # 꽉 채우면 선이 가장자리에 닿는다. 안드로이드 마스크가 바깥 20%를
        # 잘라낼 수 있으므로 그림을 안쪽으로 당겨 둔다.
        scale = 0.74
    else:
        radius = 7.0
        cx = np.clip(unit[0], radius, 32.0 - radius)
        cy = np.clip(unit[1], radius, 32.0 - radius)
        corner = np.hypot(unit[0] - cx, unit[1] - cy)
        inside = np.clip((radius - corner) * big / 32.0 + 0.5, 0.0, 1.0)
        scale = 1.0

    px = (unit[0] - 16.0) * scale + 16.0
    py = (unit[1] - 16.0) * scale + 16.0

    width = 2.5 * scale
    near = np.full((big, big), np.inf)
    for a, b in pairwise(POINTS):
        near = np.minimum(near, _segment_distance(px, py, a, b))
    # 끝을 둥글게 (stroke-linecap="round")
    stroke = np.clip((width / 2.0 - near) * big / 32.0 + 0.5, 0.0, 1.0)

    rgb = np.empty((big, big, 3), dtype=np.float64)
    for channel in range(3):
        rgb[..., channel] = GROUND[channel] * (1 - stroke) + LINE[channel] * stroke

    rgba = np.concatenate([rgb, (inside * 255.0)[..., None]], axis=2)
    # 4×4 묶어 평균 — 이게 안티에일리어싱이다
    shrunk = rgba.reshape(size, SUPER, size, SUPER, 4).mean(axis=(1, 3))
    return np.clip(np.rint(shrunk), 0, 255).astype(np.uint8)


def write_png(path: Path, image: np.ndarray) -> Path:
    """RGBA 배열을 PNG로. 필터는 안 쓴다(전부 0) — 아이콘 크기라 충분하다."""
    height, width = image.shape[:2]
    rows = b"".join(b"\x00" + image[y].tobytes() for y in range(height))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )
    return path


def main() -> None:
    made = [
        write_png(OUT / "icon-192.png", render(192)),
        write_png(OUT / "icon-512.png", render(512)),
        write_png(OUT / "icon-maskable.png", render(512, bleed=True)),
        write_png(OUT / "apple-touch-icon.png", render(180, bleed=True)),
    ]
    for path in made:
        print(f"  {path.relative_to(OUT.parent.parent.parent)}  {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
