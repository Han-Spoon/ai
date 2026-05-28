from datetime import datetime, timezone
from pathlib import Path
import mimetypes


def build_final_result(
    image_path: str,
    menus,
    scan_status: str = "completed",
    source: str = "upload",
    storage_key: str | None = None,
    image_url: str | None = None,
    raw_lines=None,
):
    image = Path(image_path)
    scan_quality = build_scan_quality(image, menus, raw_lines)

    return {
        "scan_session": {
            "title": image.name,
            "menu_count": len(menus),
            "risky_menu_count": None,
            "scan_status": scan_status,
            "scanned_at": current_timestamp(),
        },
        "menu_image": {
            "source": source,
            "storage_key": storage_key,
            "image_url": image_url,
            "mime_type": infer_mime_type(image),
            "file_size": infer_file_size(image),
        },
        "scan_quality": scan_quality,
        "menu_analyses": [
            build_menu_analysis(menu, index)
            for index, menu in enumerate(menus, start=1)
        ],
    }


def build_menu_analysis(menu, display_order: int):
    return {
        "menu_name_ko": menu.get("matchedMenu") or menu.get("normalizedCandidate") or menu.get("rawName"),
        "menu_name_en": None,
        "description_ko": menu.get("description") or "",
        "description_en": None,
        "price_text": menu.get("priceRaw") or stringify_price(menu.get("price")),
        "risk_level": None,
        "is_spicy": None,
        "image_url": None,
        "display_order": display_order,
    }


def build_scan_quality(image: Path, menus, raw_lines=None):
    raw_line_count = len(raw_lines) if raw_lines is not None else None
    menu_count = len(menus)
    price_match_count = count_price_matches(menus)
    price_match_ratio = round(price_match_count / menu_count, 2) if menu_count else 0.0
    image_width, image_height = infer_image_size(image)

    reasons = []
    hard_retake = False

    if menu_count == 0:
        hard_retake = True
        reasons.append("추출된 메뉴 후보가 없습니다.")
    elif menu_count < 3:
        reasons.append("추출된 메뉴 후보가 너무 적습니다.")

    if raw_line_count is not None:
        if raw_line_count < 3:
            hard_retake = True
            reasons.append("OCR로 읽힌 텍스트 줄 수가 너무 적습니다.")
        elif raw_line_count < 8:
            reasons.append("OCR로 읽힌 텍스트 줄 수가 적습니다.")

    if menu_count and price_match_ratio < 0.5:
        reasons.append("가격과 매칭된 메뉴 비율이 낮습니다.")

    if image_width and image_height and min(image_width, image_height) < 600:
        hard_retake = True
        reasons.append("이미지 해상도가 낮아 메뉴판 판독이 어렵습니다.")

    score = calculate_scan_quality_score(
        menu_count=menu_count,
        raw_line_count=raw_line_count,
        price_match_ratio=price_match_ratio,
        image_width=image_width,
        image_height=image_height,
    )

    if hard_retake:
        status = "needs_retake"
    elif score < 70 or reasons:
        status = "low_confidence"
    else:
        status = "usable"

    return {
        "status": status,
        "score": score,
        "raw_line_count": raw_line_count,
        "price_match_count": price_match_count,
        "price_match_ratio": price_match_ratio,
        "image_width": image_width,
        "image_height": image_height,
        "reasons": reasons,
    }


def count_price_matches(menus):
    return sum(1 for menu in menus if menu.get("priceRaw") or menu.get("price") is not None)


def calculate_scan_quality_score(
    menu_count: int,
    raw_line_count: int | None,
    price_match_ratio: float,
    image_width: int | None,
    image_height: int | None,
):
    score = 100

    if menu_count == 0:
        score -= 70
    elif menu_count < 3:
        score -= 25

    if raw_line_count is not None:
        if raw_line_count < 3:
            score -= 45
        elif raw_line_count < 8:
            score -= 15

    if menu_count and price_match_ratio < 0.5:
        score -= 20

    if image_width and image_height and min(image_width, image_height) < 600:
        score -= 35

    return max(0, min(100, score))


def current_timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def infer_mime_type(image: Path):
    mime_type, _ = mimetypes.guess_type(image.name)
    return mime_type


def infer_file_size(image: Path):
    if image.exists() and image.is_file():
        return image.stat().st_size
    return None


def infer_image_size(image: Path):
    if not image.exists() or not image.is_file():
        return None, None

    try:
        from PIL import Image

        with Image.open(image) as image_file:
            return image_file.size
    except Exception:
        return None, None


def stringify_price(price):
    if price is None:
        return None
    return str(price)
