"""Turn the artist sprite sheets in assets/ into small, evenly aligned avatar sheets for the dashboard.

Each assets/<Role>.png is a 3 columns x 4 rows sheet on a transparent background:
row 1 = waiting, row 2 = work in progress, row 3 = reasoning, row 4 = queued; the 3 columns are animation frames.
The frames are located from the empty gaps between them (not from a fixed grid), cropped to one common size so the
character does not jitter when the animation steps, downscaled and written to frontend/public/avatars/<role>.png
(3 x 4 cells of CELL px). Run from the repo root after changing the sources:

    .venv/bin/pip install pillow
    .venv/bin/python frontend/scripts/build_avatars.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SRC, OUT = ROOT / "assets", ROOT / "frontend" / "public" / "avatars"
COLS, ROWS, CELL, PAD = 3, 4, 160, 8


def segments(profile: np.ndarray, expected: int) -> list[tuple[int, int]]:
    """Runs of non-empty pixels along one axis; the gap tolerance grows until `expected` runs remain."""
    on = profile > 0
    runs: list[tuple[int, int]] = []
    start = None
    for i, v in enumerate(on):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i)); start = None
    if start is not None:
        runs.append((start, len(on)))
    for gap in range(2, 120, 2):
        merged: list[list[int]] = []
        for a, b in runs:
            if merged and a - merged[-1][1] < gap:
                merged[-1][1] = b
            else:
                merged.append([a, b])
        big = [(a, b) for a, b in merged if b - a > 20]  # specks (stars, notes) are not frames
        if len(big) == expected:
            return big
    raise ValueError(f"cannot find {expected} segments")


def uniform(lo: int, hi: int, n: int) -> list[tuple[int, int]]:
    step = (hi - lo) / n
    return [(round(lo + i * step), round(lo + (i + 1) * step)) for i in range(n)]


def build(path: Path) -> None:
    im = Image.open(path).convert("RGBA")
    alpha = np.array(im)[..., 3] > 10
    ys, xs = np.where(alpha)
    try:
        cols = segments(alpha.sum(axis=0), COLS)
        rows = segments(alpha.sum(axis=1), ROWS)
    except ValueError:
        cols, rows = uniform(xs.min(), xs.max(), COLS), uniform(ys.min(), ys.max(), ROWS)
    w = max(b - a for a, b in cols) + 2 * PAD
    h = max(b - a for a, b in rows) + 2 * PAD
    side = max(w, h)  # square cells keep the sprite proportions
    sheet = Image.new("RGBA", (COLS * CELL, ROWS * CELL))
    for r, (y0, y1) in enumerate(rows):
        for c, (x0, x1) in enumerate(cols):
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            # characters stand on the row's bottom edge: anchor there so feet stay at the same height
            top = y1 + PAD - side
            box = (round(cx - side / 2), round(top), round(cx + side / 2), round(top + side))
            frame = im.crop(box).resize((CELL, CELL), Image.LANCZOS)
            sheet.paste(frame, (c * CELL, r * CELL))
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{path.stem.lower()}.png"
    sheet.quantize(colors=255, method=Image.FASTOCTREE, dither=Image.NONE).save(target, optimize=True)
    print(f"{path.name}: cols={cols} rows={rows} -> {target.relative_to(ROOT)} ({target.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    for p in sorted(SRC.glob("*.png")):
        build(p)
