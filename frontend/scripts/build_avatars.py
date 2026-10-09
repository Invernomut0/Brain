"""Turn the artist sprite sheets in assets/ into small, evenly aligned avatar sheets for the dashboard.

Each assets/<Role>.png is a 3 columns x 4 rows sheet on a transparent background:
row 1 = waiting, row 2 = work in progress, row 3 = reasoning, row 4 = queued; the 3 columns are animation frames.
The characters are full-body and touch their neighbours (boots of one row meet the props of the next), so neither
straight cuts nor plain connected shapes can separate them. Instead the 12 bodies are found as the largest shapes left
after eroding the drawing (thin contacts break), and every remaining pixel joins its nearest body. All frames are then
drawn on one common square canvas, feet on the bottom edge and body mass centred, downscaled and written to
frontend/public/avatars/<role>.png (3 x 4 cells of CELL px). Run from the repo root after changing the sources:

    .venv/bin/pip install pillow scipy
    .venv/bin/python frontend/scripts/build_avatars.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[2]
SRC, OUT = ROOT / "assets", ROOT / "frontend" / "public" / "avatars"
COLS, ROWS, CELL, PAD = 3, 4, 192, 8
ERODES = (9, 7, 5, 3, 2)  # px: contacts thinner than twice this stop joining neighbouring frames; the first value giving 12 clean bodies wins


def frames(im: Image.Image) -> list[list[tuple[Image.Image, float]]]:
    """ROWS x COLS list of (RGBA frame cropped to its bbox, x of its centre of mass inside the crop)."""
    rgba = np.array(im)
    solid = rgba[..., 3] > 10
    count = ROWS * COLS
    for erode in ERODES:
        eroded, n = ndimage.label(ndimage.binary_erosion(solid, iterations=erode))
        sizes = ndimage.sum(np.ones_like(eroded), eroded, index=range(1, n + 1)) if n else np.array([])
        ranked = np.sort(sizes)[::-1]
        if n >= count and (n == count or ranked[count] <= 0.25 * ranked[count - 1]):
            break  # exactly 12 bodies stand out from the debris
    else:
        raise ValueError(f"cannot isolate {count} bodies (found {n} shapes at the lowest erosion)")
    top = np.argsort(sizes)[::-1][:count] + 1
    seeds = np.zeros_like(eroded)
    centres = {}
    floor = {}  # lowest row of each body: drawing found well below it is debris, not part of the character
    for rank, lab in enumerate(top, start=1):
        seeds[eroded == lab] = rank
        ys, xs = np.where(eroded == lab)
        centres[rank] = (ys.mean(), xs.mean())
        floor[rank] = ys.max()
    # every drawn pixel belongs to the nearest body
    _, (iy, ix) = ndimage.distance_transform_edt(seeds == 0, return_indices=True)
    owner = np.where(solid, seeds[iy, ix], 0)

    by_y = sorted(centres, key=lambda k: centres[k][0])
    grid = [sorted(by_y[r * COLS:(r + 1) * COLS], key=lambda k: centres[k][1]) for r in range(ROWS)]
    out: list[list[tuple[Image.Image, float]]] = []
    for row in grid:
        cells = []
        for k in row:
            mask = owner == k
            parts, np_ = ndimage.label(ndimage.binary_dilation(mask, iterations=3))
            for p in range(1, np_ + 1):  # detached bits entirely below the feet (stray boots, crumbs) are dropped
                py = np.where(parts == p)[0]
                if py.min() > floor[k] + 2 * ERODES[0] and (parts == p).sum() < 0.1 * mask.sum():
                    mask &= parts != p
            ys, xs = np.where(mask)
            y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
            frame = np.zeros_like(rgba[y0:y1, x0:x1])
            sub = mask[y0:y1, x0:x1]
            frame[sub] = rgba[y0:y1, x0:x1][sub]
            cells.append((Image.fromarray(frame), float(xs.mean() - x0)))
        out.append(cells)
    return out


def build(path: Path) -> None:
    cells = frames(Image.open(path).convert("RGBA"))
    flat = [f for row in cells for f in row]
    height = max(f.height for f, _ in flat)
    half = max(max(cx, f.width - cx) for f, cx in flat)
    side = int(max(height, 2 * half)) + 2 * PAD
    sheet = Image.new("RGBA", (COLS * CELL, ROWS * CELL))
    for r, row in enumerate(cells):
        for c, (f, cx) in enumerate(row):
            canvas = Image.new("RGBA", (side, side))
            canvas.paste(f, (round(side / 2 - cx), side - PAD - f.height))  # feet on the bottom edge, body mass centred
            sheet.paste(canvas.resize((CELL, CELL), Image.LANCZOS), (c * CELL, r * CELL))
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{path.stem.lower()}.png"
    sheet.quantize(colors=255, method=Image.FASTOCTREE, dither=Image.NONE).save(target, optimize=True)
    print(f"{path.name}: side={side} heights={[f.height for f, _ in flat]} -> {target.relative_to(ROOT)} ({target.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    for p in sorted(SRC.glob("*.png")):
        build(p)
