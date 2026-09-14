"""
Draws the Golden Finds desktop icon - a gold square with a shopping bag -
so the shortcut is easy to spot on a busy desktop.
"""

import sys

from PIL import Image, ImageDraw

SIZE = 256


def draw():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    # Gold-to-coral diagonal gradient, clipped to a rounded square.
    gradient = Image.new("RGBA", (SIZE, SIZE))
    top, bottom = (246, 201, 92), (224, 114, 90)
    pixels = gradient.load()
    for y in range(SIZE):
        for x in range(SIZE):
            t = (x + y) / (2 * (SIZE - 1))
            pixels[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,)
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((8, 8, SIZE - 8, SIZE - 8), radius=64, fill=255)
    img.paste(gradient, (0, 0), mask)

    d = ImageDraw.Draw(img)
    # Bag body and handle.
    d.polygon([(76, 104), (180, 104), (168, 210), (88, 210)], fill=(255, 255, 255, 245))
    d.arc((96, 52, 160, 132), start=180, end=360, fill=(255, 255, 255, 255), width=14)
    # A small gem on the bag.
    d.polygon([(128, 136), (144, 158), (128, 182), (112, 158)], fill=(224, 114, 90, 255))
    return img


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "golden-finds.ico"
    draw().save(target, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
