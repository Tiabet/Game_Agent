from __future__ import annotations

import cv2


def main() -> None:
    img = cv2.imread("screenshots/latest.png", cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError("screenshots/latest.png")

    th = ((img > 90).astype("uint8")) * 255
    th[:250, :] = 0
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if area > 50 and w > 3 and h > 3:
            boxes.append((x, y, w, h, area))

    print("boxes", len(boxes))
    for box in sorted(boxes, key=lambda item: (item[1], item[0]))[:160]:
        x, y, w, h, area = box
        print(f"x={x:3d} y={y:3d} w={w:3d} h={h:3d} area={area:5d} center=({x + w // 2},{y + h // 2})")


if __name__ == "__main__":
    main()
