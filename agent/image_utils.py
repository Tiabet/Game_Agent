from __future__ import annotations

from pathlib import Path

from PIL import Image


def save_resized_image(
    input_path: str | Path,
    output_path: str | Path,
    *,
    scale: float = 0.5,
) -> Path:
    if scale <= 0:
        raise ValueError("scale must be greater than 0")

    source = Path(input_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(source) as image:
        width, height = image.size
        resized_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        resized = image.resize(resized_size, Image.Resampling.LANCZOS)
        resized.save(output)

    return output
