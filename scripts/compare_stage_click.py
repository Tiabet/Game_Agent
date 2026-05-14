from __future__ import annotations

from PIL import Image, ImageChops


BOXES = {
    "arrow_area": (70, 285, 130, 320),
    "stage_numbers": (90, 320, 270, 355),
    "cards": (80, 320, 280, 360),
    "top_stage_text": (90, 292, 270, 310),
}


def main() -> None:
    before = Image.open("screenshots/before_stage_click.png").convert("RGB")
    after = Image.open("screenshots/latest.png").convert("RGB")
    for name, box in BOXES.items():
        diff = ImageChops.difference(before.crop(box), after.crop(box))
        print(name, "diff", sum(sum(pixel) for pixel in diff.getdata()), "bbox", diff.getbbox())


if __name__ == "__main__":
    main()
