"""Draw the app's icons (static/icons/) from the logo: a white hex nut on a purple tile.

Run it again only if the logo changes:

    .venv\\Scripts\\python docs\\make_icons.py

It draws at 4x and scales down, so the edges are smooth. The "maskable" icon has
the nut in the middle 60% and purple all the way to the edges, because Android cuts
icons into circles or rounded squares.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent.parent / "shiromoto" / "static" / "icons"
PURPLE = (124, 58, 237, 255)  # --accent
WHITE = (255, 255, 255, 255)
SCALE = 4


def nut(draw, centre, radius, width):
    """A hexagon (pointy top) and its hole, as in mark() in _macros.html."""
    cx, cy = centre
    points = [(cx + radius * math.cos(math.radians(a)), cy + radius * math.sin(math.radians(a)))
              for a in range(-90, 270, 60)]
    draw.line(points + [points[0], points[1]], fill=WHITE, width=width, joint="curve")
    hole = radius * 0.4
    draw.ellipse((cx - hole, cy - hole, cx + hole, cy + hole), outline=WHITE, width=width)


def icon(size, maskable=False):
    big = size * SCALE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if maskable:
        draw.rectangle((0, 0, big, big), fill=PURPLE)
        radius = big * 0.3 * 0.94
    else:
        draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=big * 0.22, fill=PURPLE)
        radius = big * 0.29
    nut(draw, (big / 2, big / 2), radius, max(2, round(big * 0.08)))
    return image.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size, maskable in (("icon-192.png", 192, False), ("icon-512.png", 512, False),
                                 ("maskable-512.png", 512, True), ("apple-touch-icon.png", 180, True)):
        icon(size, maskable).save(OUT / name)
        print("saved", name)
    icon(64).save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print("saved favicon.ico")


if __name__ == "__main__":
    main()
