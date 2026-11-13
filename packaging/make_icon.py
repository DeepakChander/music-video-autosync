"""Writes packaging/autosync.ico (multi-size) for the executables and installer."""
import os

from PIL import Image, ImageDraw


def draw(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=max(2, size // 6), fill=(30, 30, 36, 255))
    bars = [0.35, 0.7, 1.0, 0.55, 0.85, 0.45]
    w = size / (len(bars) * 2 + 1)
    for i, h in enumerate(bars):
        x0 = w * (1 + 2 * i)
        hh = h * size * 0.7
        d.rectangle((x0, size / 2 - hh / 2, x0 + w, size / 2 + hh / 2), fill=(95, 200, 160, 255))
    return img


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "autosync.ico")
    draw(256).save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(out)
