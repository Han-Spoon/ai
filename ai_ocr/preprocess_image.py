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
):
    source = Path(input_path)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {input_path}")

    image = Image.open(source)
    image = ImageOps.exif_transpose(image)

    if crop:
        image = image.crop(crop)

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
        )
        print(f"전처리 이미지 저장 완료: {output_path}")
    except Exception as error:
        print(f"[전처리 오류] {error}")


if __name__ == "__main__":
    main()
