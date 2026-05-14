from __future__ import annotations

from pathlib import Path

import imagehash
from PIL import Image


def perceptual_hash(screen_path: str | Path) -> str:
    with Image.open(screen_path) as image:
        return str(imagehash.phash(image))


def hash_distance(left: str, right: str) -> int:
    return imagehash.hex_to_hash(left) - imagehash.hex_to_hash(right)


def similar_hash(left: str, right: str, *, threshold: int) -> bool:
    if left == right:
        return True
    try:
        return hash_distance(left, right) <= threshold
    except ValueError:
        return False
