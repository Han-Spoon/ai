import argparse
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps


def preprocess_image(
    input_path: str,
    output_path: str | None = None,
    crop: tuple[int, int, int, int] | None = None,
    scale: float = 2.0,
    grayscale: bool = True,
    contrast: float = 1.4,
    sharpness: float = 1.6,
    perspective: bool = True,
    deskew: bool = True,
    max_deskew_angle: float = 25.0,
):
    source = Path(input_path)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {input_path}")

    image = Image.open(source)
    image = ImageOps.exif_transpose(image)

    if crop:
        image = image.crop(crop)

    if perspective:
        image = auto_perspective_correct(image)

    if deskew:
        image = auto_deskew(image, max_angle=max_deskew_angle)

    if scale != 1:
        width = int(image.width * scale)
        height = int(image.height * scale)
        image = image.resize((width, height), Image.Resampling.LANCZOS)

    if grayscale:
        image = ImageOps.grayscale(image)

    image = ImageEnhance.Contrast(image).enhance(contrast)
    image = ImageEnhance.Sharpness(image).enhance(sharpness)
    image = image.filter(ImageFilter.UnsharpMask(radius=1.5, percent=140, threshold=3))

    target = Path(output_path) if output_path else build_output_path(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, quality=95)

    return target


def require_cv_tools():
    try:
        import cv2
        import numpy as np
    except ImportError as error:
        raise RuntimeError(
            "기울기/원근 보정에는 opencv-python-headless와 numpy가 필요합니다. "
            "pip install -r requirements.txt를 실행해주세요."
        ) from error

    return cv2, np


def auto_perspective_correct(image: Image.Image):
    cv2, np = require_cv_tools()
    cv_image = pil_to_cv(image)
    original = cv_image.copy()
    height, width = cv_image.shape[:2]

    gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 50, 150)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)
    min_area = width * height * 0.18

    for contour in contours[:8]:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue

        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approx) != 4:
            continue

        corrected = four_point_transform(original, approx.reshape(4, 2))
        if corrected.shape[0] < 100 or corrected.shape[1] < 100:
            continue

        return cv_to_pil(corrected)

    return image


def auto_deskew(image: Image.Image, max_angle: float = 25.0):
    cv2, np = require_cv_tools()
    cv_image = pil_to_cv(image)
    gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    threshold = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 3))
    connected = cv2.dilate(threshold, kernel, iterations=1)

    coords = np.column_stack(np.where(connected > 0))
    if len(coords) < 100:
        return image

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    if abs(angle) < 0.25 or abs(angle) > max_angle:
        return image

    height, width = cv_image.shape[:2]
    center = (width // 2, height // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    cos = abs(matrix[0, 0])
    sin = abs(matrix[0, 1])
    new_width = int((height * sin) + (width * cos))
    new_height = int((height * cos) + (width * sin))
    matrix[0, 2] += (new_width / 2) - center[0]
    matrix[1, 2] += (new_height / 2) - center[1]

    rotated = cv2.warpAffine(
        cv_image,
        matrix,
        (new_width, new_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return cv_to_pil(rotated)


def pil_to_cv(image: Image.Image):
    _, np = require_cv_tools()
    rgb_image = image.convert("RGB")
    return np.array(rgb_image)[:, :, ::-1].copy()


def cv_to_pil(cv_image):
    rgb_image = cv_image[:, :, ::-1]
    return Image.fromarray(rgb_image)


def order_points(points):
    _, np = require_cv_tools()
    points = points.astype("float32")
    ordered = np.zeros((4, 2), dtype="float32")

    point_sums = points.sum(axis=1)
    ordered[0] = points[np.argmin(point_sums)]
    ordered[2] = points[np.argmax(point_sums)]

    point_diffs = np.diff(points, axis=1)
    ordered[1] = points[np.argmin(point_diffs)]
    ordered[3] = points[np.argmax(point_diffs)]

    return ordered


def four_point_transform(image, points):
    cv2, np = require_cv_tools()
    top_left, top_right, bottom_right, bottom_left = order_points(points)

    top_width = np.linalg.norm(top_right - top_left)
    bottom_width = np.linalg.norm(bottom_right - bottom_left)
    max_width = int(max(top_width, bottom_width))

    right_height = np.linalg.norm(top_right - bottom_right)
    left_height = np.linalg.norm(top_left - bottom_left)
    max_height = int(max(right_height, left_height))

    destination = np.array(
        [
            [0, 0],
            [max_width - 1, 0],
            [max_width - 1, max_height - 1],
            [0, max_height - 1],
        ],
        dtype="float32",
    )

    matrix = cv2.getPerspectiveTransform(order_points(points), destination)
    return cv2.warpPerspective(image, matrix, (max_width, max_height))


def build_output_path(source: Path):
    return Path("images/preprocessed") / f"{source.stem}_preprocessed.jpg"


def parse_crop(crop_text: str | None):
    if not crop_text:
        return None

    values = [int(value.strip()) for value in crop_text.split(",")]
    if len(values) != 4:
        raise ValueError("--crop은 left,top,right,bottom 형식이어야 합니다. 예: 90,120,1030,590")

    left, top, right, bottom = values
    if right <= left or bottom <= top:
        raise ValueError("--crop 좌표가 올바르지 않습니다. right/bottom은 left/top보다 커야 합니다.")

    return left, top, right, bottom


def parse_args():
    parser = argparse.ArgumentParser(description="Azure OCR 전에 메뉴판 이미지를 로컬 전처리")
    parser.add_argument("--image", required=True, help="전처리할 원본 이미지 경로")
    parser.add_argument("--output", help="저장할 전처리 이미지 경로")
    parser.add_argument("--crop", help="잘라낼 영역: left,top,right,bottom")
    parser.add_argument("--scale", type=float, default=2.0, help="확대 배율")
    parser.add_argument("--contrast", type=float, default=1.4, help="대비 보정 강도")
    parser.add_argument("--sharpness", type=float, default=1.6, help="선명도 보정 강도")
    parser.add_argument("--color", action="store_true", help="흑백 변환하지 않고 컬러 유지")
    parser.add_argument("--no-perspective", action="store_true", help="자동 원근 보정을 끔")
    parser.add_argument("--no-deskew", action="store_true", help="자동 기울기 보정을 끔")
    parser.add_argument("--max-deskew-angle", type=float, default=25.0, help="자동 회전 보정 최대 각도")
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        output_path = preprocess_image(
            input_path=args.image,
            output_path=args.output,
            crop=parse_crop(args.crop),
            scale=args.scale,
            grayscale=not args.color,
            contrast=args.contrast,
            sharpness=args.sharpness,
            perspective=not args.no_perspective,
            deskew=not args.no_deskew,
            max_deskew_angle=args.max_deskew_angle,
        )
        print(f"전처리 이미지 저장 완료: {output_path}")
    except Exception as error:
        print(f"[전처리 오류] {error}")


if __name__ == "__main__":
    main()
