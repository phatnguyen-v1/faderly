"""Draw the Audio Hub icon: three mixer faders on a blue rounded square.

Writes extension/icons/{16,32,48,128}.png and tools/AudioHub.ico (for the exe).
Run from the repo root: .venv\\Scripts\\python tools\\make_icons.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SS = 8  # supersampling, so edges stay smooth after the downscale

TOP, BOTTOM = (0x7B, 0x9B, 0xFF), (0x3A, 0x55, 0xD9)  # matches the slider blue
KNOBS = (0.34, 0.66, 0.46)  # fader positions, 0 = top


def draw(size):
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # Background: vertical gradient clipped to a rounded square.
    grad = Image.new("RGBA", (s, s))
    gd = ImageDraw.Draw(grad)
    for y in range(s):
        t = y / (s - 1)
        gd.line([(0, y), (s, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)) + (255,))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=s * 0.23, fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    small = size <= 32
    # Small sizes get chunkier parts so the faders survive at 16 px.
    track_w = s * (0.085 if small else 0.05)
    knob_w, knob_h = s * (0.22 if small else 0.2), s * (0.15 if small else 0.11)
    top, bottom = s * 0.2, s * 0.8
    for i, k in enumerate(KNOBS):
        x = s * (0.27 + 0.23 * i)
        d.rounded_rectangle([x - track_w / 2, top, x + track_w / 2, bottom],
                            radius=track_w / 2, fill=(255, 255, 255, 110))
        y = top + (bottom - top) * k
        d.rounded_rectangle([x - knob_w / 2, y - knob_h / 2, x + knob_w / 2, y + knob_h / 2],
                            radius=knob_h * 0.45, fill=(255, 255, 255, 255))
    return img.resize((size, size), Image.LANCZOS)


def main():
    out = ROOT / "extension" / "icons"
    for size in (16, 32, 48, 128):
        draw(size).save(out / f"{size}.png")
    big = draw(256)
    big.save(ROOT / "tools" / "AudioHub.ico",
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
             append_images=[draw(n) for n in (16, 24, 32, 48, 64, 128)])
    print("icons written")


if __name__ == "__main__":
    main()
