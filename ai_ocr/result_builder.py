from datetime import datetime, timezone
from pathlib import Path
import mimetypes
import re

from spicy_detector import infer_is_spicy
from image_quality import analyze_image_quality
from normalizer import match_known_menu_name, normalize_menu_name
from price_metrics import count_matched_price_anchors


_FOREIGN_TEXT_RE = re.compile(
    r"[A-Za-z]+(?:[\s_-]+[A-Za-z]+)*|[\u3040-\u30ff]+|[\u3400-\u4dbf\u4e00-\u9fff]+"
)


def build_final_result(
    image_path: str,
    menus,
    scan_status: str = "completed",
    source: str = "upload",
    storage_key: str | None = None,
    image_url: str | None = None,
    mime_type: str | None = None,
    file_size: int | None = None,
    image_title: str | None = None,
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
        mime_type: 이미지 MIME 타입
        file_size: 이미지 파일 크기
        image_title: 결과 제목에 사용할 이미지 이름
        raw_lines: OCR 원본 라인
        enable_gpt_post_process: GPT 후처리 활성화 여부
        enable_gpt_judgment: GPT 품질 판단 활성화 여부
    """
    image = Path(image_path)
    scan_quality = build_scan_quality(image, menus, raw_lines)
    expanded_menus = expand_slash_menu_choices(menus)

    menu_analyses = [
        build_menu_analysis(menu, index)
        for index, menu in enumerate(expanded_menus, start=1)
    ]

    if enable_gpt_post_process:
        from post_process import post_process_menu_analyses

        menu_analyses = post_process_menu_analyses(menu_analyses)

    apply_spicy_flags(menu_analyses, expanded_menus, raw_lines or [])

    result = {
        "scan_session": {
            "title": image_title or image.name,
            "menu_count": len(expanded_menus),
            "risky_menu_count": None,
            "scan_status": scan_status,
            "scanned_at": current_timestamp(),
        },
        "menu_image": {
            "source": source,
            "storage_key": storage_key,
            "image_url": image_url,
            "mime_type": mime_type or infer_mime_type(image),
            "file_size": file_size if file_size is not None else infer_file_size(image),
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
    menu_name = menu.get("normalizedCandidate") or menu.get("rawName") or menu.get("matchedMenu")
    raw_menu_name = menu.get("rawName") or menu_name
    description = menu.get("description") or ""
    price_text = stringify_price(menu.get("price")) or menu.get("priceRaw")
    price_options = [
        {
            "label": option.get("name"),
            "price": option.get("price"),
            **({"inferred": True} if option.get("inferred") else {}),
        }
        for option in menu.get("options", [])
        if option.get("name") and option.get("price") is not None
    ]

    return {
        # Supervisor/②는 이 필드를 우선 사용한다. 기존 /v1/ocr 표시 계약인
        # menu_name_ko는 하위 호환을 위해 정규화 후보를 계속 반환한다.
        "raw_menu_name": raw_menu_name,
        "origin_text": menu.get("originText"),
        "foreign_text_segments": extract_foreign_text_segments(raw_menu_name),
        "menu_name_ko": menu_name,
        "menu_name_en": None,
        "description_ko": description,
        "description_en": None,
        "ocr_confidence": float(menu.get("confidence") or 0.0),
        "price_text": price_text,
        "price_options": price_options,
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


def expand_slash_menu_choices(menus):
    """Expand slash-delimited choices while preserving plus-delimited combinations."""

    expanded = []
    for menu in menus:
        raw_name = str(menu.get("rawName") or "").strip()
        choices = split_slash_choices(raw_name)
        if len(choices) == 1:
            expanded.append(menu)
            continue

        for choice in choices:
            copied = dict(menu)
            copied["rawName"] = choice
            copied["normalizedCandidate"] = normalize_menu_name(choice)
            matched = match_known_menu_name(choice)
            copied["matchedMenu"] = matched["name"] if matched else None
            copied["category"] = matched["category"] if matched else None
            copied["matchScore"] = matched["score"] if matched else None
            copied["choiceSourceRawName"] = raw_name
            expanded.append(copied)
    return expanded


def split_slash_choices(raw_name: str) -> list[str]:
    if "/" not in raw_name or "://" in raw_name:
        return [raw_name]
    if re.search(r"\d\s*/\s*\d", raw_name):
        return [raw_name]

    choices = [part.strip() for part in raw_name.split("/")]
    if not 2 <= len(choices) <= 5:
        return [raw_name]
    if any(
        not choice or not any(character.isalpha() for character in choice)
        for choice in choices
    ):
        return [raw_name]
    return choices


def extract_foreign_text_segments(raw_name: str) -> list[dict]:
    return [
        {"text": match.group().strip(), "role": "unknown", "confidence": 0.0}
        for match in _FOREIGN_TEXT_RE.finditer(raw_name)
        if match.group().strip()
    ]


def apply_spicy_flags(menu_analyses, raw_menus, raw_lines):
    for index, menu_analysis in enumerate(menu_analyses):
        raw_menu = raw_menus[index] if index < len(raw_menus) else None
        menu_analysis["is_spicy"] = infer_is_spicy(menu_analysis, raw_menu, raw_lines)


def build_scan_quality(image: Path, menus, raw_lines=None):
    raw_line_count = len(raw_lines) if raw_lines is not None else None
    menu_count = len(menus)
    price_match_count = count_price_matches(menus)
    price_anchor_count = count_detected_price_anchors(raw_lines or [])
    pair_coverage = (
        round(min(price_match_count / price_anchor_count, 1.0), 2)
        if price_anchor_count
        else 0.0
    )
    price_match_ratio = pair_coverage if price_anchor_count else (
        round(min(price_match_count / menu_count, 1.0), 2)
        if menu_count
        else 0.0
    )
    ocr_confidences = [
        float(line.get("confidence"))
        for line in (raw_lines or [])
        if line.get("confidence") is not None
    ]
    mean_ocr_confidence = (
        round(sum(ocr_confidences) / len(ocr_confidences), 3)
        if ocr_confidences
        else None
    )
    pair_confidences = [
        float(menu.get("confidence"))
        for menu in menus
        if menu.get("confidence") is not None
    ]
    mean_pair_confidence = (
        round(sum(pair_confidences) / len(pair_confidences), 3)
        if pair_confidences
        else None
    )
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

    if price_anchor_count and pair_coverage < 0.5:
        reasons.append("가격과 매칭된 메뉴 비율이 낮습니다.")

    if mean_pair_confidence is not None and mean_pair_confidence < 0.65:
        reasons.append("메뉴명과 가격의 OCR/위치 신뢰도가 낮습니다.")
        if mean_pair_confidence < 0.45:
            hard_retake = True

    ocr_structure_ok = (
        not reasons
        and raw_line_count is not None
        and mean_pair_confidence is not None
    )

    if image_width and image_height and min(image_width, image_height) < 600:
        reasons.append("이미지 해상도가 낮아 메뉴판 판독이 어렵습니다.")
        # 낮은 해상도만으로 결과를 폐기하지 않는다. OCR 구조가 함께 나쁠 때만
        # 재촬영을 강제하고, 충분히 매칭되면 사용자 확인 가능한 결과를 제공한다.
        if menu_count < 3 or (price_anchor_count and pair_coverage < 0.5):
            hard_retake = True

    if image_quality.get("available") and not ocr_structure_ok:
        reasons.extend(image_quality.get("reasons", []))
        if image_quality.get("score") is not None and image_quality["score"] < 45:
            hard_retake = True

    score = calculate_scan_quality_score(
        menu_count=menu_count,
        raw_line_count=raw_line_count,
        price_match_ratio=price_match_ratio,
        image_width=image_width,
        image_height=image_height,
        image_quality_score=None if ocr_structure_ok else image_quality.get("score"),
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
        "price_anchor_count": price_anchor_count,
        "pair_coverage": pair_coverage,
        "mean_ocr_confidence": mean_ocr_confidence,
        "mean_pair_confidence": mean_pair_confidence,
        "image_width": image_width,
        "image_height": image_height,
        "image_quality": image_quality,
        "retake_suggestions": (
            [] if status == "usable" else image_quality.get("suggestions", [])
        ),
        "reasons": dedupe(reasons),
    }


def count_price_matches(menus):
    return count_matched_price_anchors(menus)


def count_detected_price_anchors(raw_lines):
    if not raw_lines or not any(line.get("source") == "clova_field" for line in raw_lines):
        return 0

    from clova_layout import count_price_anchors

    return count_price_anchors(raw_lines)


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
