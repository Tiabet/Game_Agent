from __future__ import annotations

import argparse

from PIL import Image


CHARS = " .:-=+*#%@"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--crop", default="0,0,360,640", help="left,top,right,bottom")
    parser.add_argument("--cols", type=int, default=72)
    parser.add_argument("--rows", type=int, default=40)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    left, top, right, bottom = [int(part) for part in args.crop.split(",")]
    source = Image.open(args.image).convert("L")
    crop = source.crop((left, top, right, bottom)).resize((args.cols, args.rows))
    pix = crop.load()

    print(f"image={source.size} crop=({left},{top},{right},{bottom}) ascii=({args.cols},{args.rows})")
    print("    " + "".join(str((left + int(x * (right - left) / args.cols)) // 10 % 10) for x in range(args.cols)))
    print("    " + "".join(str((left + int(x * (right - left) / args.cols)) % 10) for x in range(args.cols)))
    for y in range(crop.height):
        original_y = top + int(y * (bottom - top) / crop.height)
        line = "".join(CHARS[pix[x, y] * len(CHARS) // 256] for x in range(crop.width))
        print(f"{original_y:03d} {line}")


if __name__ == "__main__":
    main()
