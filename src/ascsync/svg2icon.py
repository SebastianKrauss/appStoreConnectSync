#!/usr/bin/env python3
"""An SF Symbols SVG -> a Game Center icon (512x512, RGB without alpha).

Game Center wants square PNGs for achievements and leaderboards (512² or
1024²) WITHOUT an alpha channel. An SF Symbols export is transparent and not
square, so this script places it on a solid background and centres the glyph.

    ascsync-svg2icon crown.fill.svg assets/gamecenter/achievements/event.champion.png

The target name has to be given explicitly on purpose: it must match the
vendor id without its prefix (`challenge.turns.png`), not the name of the SF
symbol.

The path itself is rendered — Bezier flattening and polygon filling in PIL,
supersampled eight times. This avoids depending on an SVG renderer (cairosvg,
rsvg-convert, ImageMagick) being installed. It expects an SF Symbols export
with EXACTLY ONE <path>; if the file has several it is not a symbol export but
a composed graphic, and that belongs in a drawing program, not here.

Limitation: subpaths are filled individually, so a hole in the symbol
(nonzero winding) gets painted over. A glance at the result shows it
immediately when that happens.
"""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from typing import List, Tuple

try:
    from PIL import Image, ImageChops, ImageDraw
except ImportError:                                            # pragma: no cover
    sys.exit("Pillow is missing — run 'pip install pillow' in the active venv")

SUPERSAMPLE = 8
CANVAS = 512
BACKGROUND = (249, 246, 239)   # a warm off-white; match it to your other icons
FOREGROUND = (69, 69, 69)      # #454545, the SF Symbols export colour
WIDTH_FRACTION = 0.52          # glyph width relative to the edge length

SVG_NS = "{http://www.w3.org/2000/svg}"
TOKEN = re.compile(r"([MmLlHhVvCcSsZz])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)")

Point = Tuple[float, float]


def _tokenize(d: str):
    for command, number in TOKEN.findall(d):
        yield ("cmd", command) if command else ("num", float(number))


def _flatten_cubic(p0: Point, p1: Point, p2: Point, p3: Point,
                   steps: int = 24) -> List[Point]:
    points = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1 - t
        points.append((u * u * u * p0[0] + 3 * u * u * t * p1[0]
                       + 3 * u * t * t * p2[0] + t * t * t * p3[0],
                       u * u * u * p0[1] + 3 * u * u * t * p1[1]
                       + 3 * u * t * t * p2[1] + t * t * t * p3[1]))
    return points


def parse_path(d: str) -> List[List[Point]]:
    """SVG path -> subpaths as point lists. Supports M L H V C S Z."""
    tokens = list(_tokenize(d))
    index = 0
    subpaths: List[List[Point]] = []
    current: List[Point] = []
    cursor = start = (0.0, 0.0)
    last_control = None
    command = None

    def take(count: int) -> List[float]:
        nonlocal index
        values = []
        while len(values) < count:
            if index >= len(tokens):
                raise ValueError("path ends in the middle of a command")
            kind, value = tokens[index]
            if kind != "num":
                raise ValueError(f"expected a number, found {value!r}")
            values.append(value)
            index += 1
        return values

    while index < len(tokens):
        kind, value = tokens[index]
        if kind == "cmd":
            command = value
            index += 1
            if command in "Zz":
                if current:
                    subpaths.append(current)
                    current = []
                cursor = start
                last_control = None
                continue
        if command is None:
            raise ValueError("path starts without a command")
        relative = command.islower()
        op = command.upper()

        if op == "M":
            x, y = take(2)
            if relative:
                x, y = cursor[0] + x, cursor[1] + y
            if current:
                subpaths.append(current)
            cursor = start = (x, y)
            current = [cursor]
            # Per the SVG spec, further coordinate pairs after an M are lines.
            command = "l" if relative else "L"
            last_control = None
        elif op in ("L", "H", "V"):
            if op == "L":
                x, y = take(2)
                if relative:
                    x, y = cursor[0] + x, cursor[1] + y
            elif op == "H":
                (x,) = take(1)
                x = cursor[0] + x if relative else x
                y = cursor[1]
            else:
                (y,) = take(1)
                y = cursor[1] + y if relative else y
                x = cursor[0]
            cursor = (x, y)
            current.append(cursor)
            last_control = None
        elif op in ("C", "S"):
            if op == "C":
                x1, y1, x2, y2, x, y = take(6)
                if relative:
                    x1, y1 = cursor[0] + x1, cursor[1] + y1
                    x2, y2 = cursor[0] + x2, cursor[1] + y2
                    x, y = cursor[0] + x, cursor[1] + y
            else:
                x2, y2, x, y = take(4)
                if relative:
                    x2, y2 = cursor[0] + x2, cursor[1] + y2
                    x, y = cursor[0] + x, cursor[1] + y
                x1, y1 = ((2 * cursor[0] - last_control[0],
                           2 * cursor[1] - last_control[1])
                          if last_control else cursor)
            current.extend(_flatten_cubic(cursor, (x1, y1), (x2, y2), (x, y)))
            last_control = (x2, y2)
            cursor = (x, y)
        else:
            raise ValueError(f"path command {op} is not supported "
                             "(arc or quadratic curve) — re-export the symbol")

    if current:
        subpaths.append(current)
    return subpaths


def render(svg_path: str, size: int, width_fraction: float) -> Image.Image:
    root = ET.parse(svg_path).getroot()
    paths = [p.get("d") for p in root.iter(f"{SVG_NS}path") if p.get("d")]
    if len(paths) != 1:
        raise SystemExit(f"{svg_path}: found {len(paths)} paths — expected "
                         "exactly one (an SF Symbols export of a glyph)")
    subpaths = parse_path(paths[0])

    xs = [x for sub in subpaths for x, _ in sub]
    ys = [y for sub in subpaths for _, y in sub]
    min_x, min_y = min(xs), min(ys)
    glyph_w, glyph_h = max(xs) - min_x, max(ys) - min_y

    scale = (size * width_fraction) / glyph_w * SUPERSAMPLE
    canvas = size * SUPERSAMPLE
    offset_x = (canvas - glyph_w * scale) / 2 - min_x * scale
    offset_y = (canvas - glyph_h * scale) / 2 - min_y * scale

    mask = Image.new("L", (canvas, canvas), 0)
    draw = ImageDraw.Draw(mask)
    for sub in subpaths:
        draw.polygon([(x * scale + offset_x, y * scale + offset_y)
                      for x, y in sub], fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)

    icon = Image.new("RGB", (size, size), BACKGROUND)
    icon.paste(Image.new("RGB", (size, size), FOREGROUND), mask=mask)
    return icon


def coverage(icon: Image.Image) -> str:
    """How much of the edge length the glyph covers — for comparing icons."""
    reference = Image.new("RGB", icon.size, BACKGROUND)
    diff = ImageChops.difference(icon, reference).convert("L")
    box = diff.point(lambda p: 255 if p > 12 else 0).getbbox()
    if not box:
        return "empty"
    width, height = icon.size
    return (f"{100 * (box[2] - box[0]) / width:.0f}% wide, "
            f"{100 * (box[3] - box[1]) / height:.0f}% tall")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert an SF Symbols SVG into a Game Center icon.")
    parser.add_argument("svg", help="source file (SF Symbols export)")
    parser.add_argument("png", help="target file, named after the vendor id "
                                    "without its prefix")
    parser.add_argument("--size", type=int, default=CANVAS, choices=(512, 1024),
                        help="edge length (default: 512)")
    parser.add_argument("--width", type=float, default=WIDTH_FRACTION,
                        help="glyph width as a fraction of the edge length "
                             f"(default: {WIDTH_FRACTION})")
    args = parser.parse_args()

    icon = render(args.svg, args.size, args.width)
    icon.save(args.png, "PNG", optimize=True)
    print(f"{args.png}  {icon.size[0]}x{icon.size[1]} RGB, {coverage(icon)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
