from datetime import datetime, timezone
from pathlib import Path
import mimetypes

from spicy_detector import infer_is_spicy
from image_quality import analyze_image_quality


def build_final_result(
    image_path: str,
    menus,
    scan_status: str = "completed",
    source: str = "upload",
    storage_key: str | None = None,
    image_url: str | None = None,
    raw_lines=None,
    enable_gpt_post_process: bool = True,
    enable_gpt_judgment: bool = True,
):
    """
    최종 결과 생성 (GPT 후처리 포함)

    Args:
        image_path: 이미지 경로
        menus: 파싱된 메뉴 목록
        scan_status: 스캔 상태
        source: 이미지 소스
        storage_key: 저장소 키
        image_url: 이미지 URL
        raw_lines: OCR 원본 라인
        enable_gpt_post_process: GPT 후처리 활성화 여부
        enable_gpt_judgment: GPT 품질 판단 활성화 여부
    """
    image = Path(image_path)
    scan_quality = build_scan_quality(image, menus, raw_lines)

    menu_analyses = [
        build_menu_analysis(menu, index)
        for index, menu in enumerate(menus, start=1)
    ]

    if enable_gpt_post_process:
        from post_process import post_process_menu_analyses

        menu_analyses = post_process_menu_analyses(menu_analyses)

    apply_spicy_flags(menu_analyses, menus, raw_lines or [])

    result = {
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
        "menu_analyses": menu_analyses,
    }

    if enable_gpt_judgment:
        from post_process import judge_ocr_quality

        gpt_judgment = judge_ocr_quality(menu_analyses, raw_lines or [])
        if gpt_judgment:
            result["gpt_quality_judgment"] = gpt_judgment

    return result


def build_menu_analysis(menu, display_order: int):
    menu_name = menu.get("matchedMenu") or menu.get("normalizedCandidate") or menu.get("rawName")
    description = menu.get("description") or ""
    price_text = menu.get("priceRaw") or stringify_price(menu.get("price"))

    return {
        "menu_name_ko": menu_name,
        "menu_name_en": None,
        "description_ko": description,
        "description_en": None,
        "price_text": price_text,
        "risk_level": None,
        "is_spicy": infer_is_spicy(
            {
                "menu_name_ko": menu_name,
                "description_ko": description,
                "price_text": price_text,
            },
            menu,
        ),
        "image_url": None,
        "display_order": display_order,
    }


def apply_spicy_flags(menu_analyses, raw_menus, raw_lines):
    for index, menu_analysis in enumerate(menu_analyses):
        raw_menu = raw_menus[index] if index < len(raw_menus) else None
        menu_analysis["is_spicy"] = infer_is_spicy(menu_analysis, raw_menu, raw_lines)


def build_scan_quality(image: Path, menus, raw_lines=None):
    raw_line_count = len(raw_lines) if raw_lines is not None else None
    menu_count = len(menus)
    price_match_count = count_price_matches(menus)
    price_match_ratio = round(price_match_count / menu_count, 2) if menu_count else 0.0
    image_width, image_height = infer_image_size(image)
    image_quality = analyze_image_quality(image)

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

    if image_quality.get("available"):
        reasons.extend(image_quality.get("reasons", []))
        if image_quality.get("score") is not None and image_quality["score"] < 45:
            hard_retake = True

    score = calculate_scan_quality_score(
        menu_count=menu_count,
        raw_line_count=raw_line_count,
        price_match_ratio=price_match_ratio,
        image_width=image_width,
        image_height=image_height,
        image_quality_score=image_quality.get("score"),
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
        "image_quality": image_quality,
        "retake_suggestions": image_quality.get("suggestions", []),
        "reasons": dedupe(reasons),
    }


def count_price_matches(menus):
    return sum(1 for menu in menus if menu.get("priceRaw") or menu.get("price") is not None)


def calculate_scan_quality_score(
    menu_count: int,
    raw_line_count: int | None,
    price_match_ratio: float,
    image_width: int | None,
    image_height: int | None,
    image_quality_score: int | None = None,
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

    if image_quality_score is not None:
        score = min(score, int(round((score * 0.7) + (image_quality_score * 0.3))))

    return max(0, min(100, score))


def dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


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
        pass

    try:
        import cv2

        cv_image = cv2.imread(str(image))
        if cv_image is None:
            return None, None
        height, width = cv_image.shape[:2]
        return width, height
    except Exception:
        return None, None


def stringify_price(price):
    if price is None:
        return None
    return str(price)
