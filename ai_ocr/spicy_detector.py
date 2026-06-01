import re


SPICY_KEYWORDS = (
    "매운",
    "매움",
    "맵기",
    "매콤",
    "얼큰",
    "칼칼",
    "화끈",
    "고추장",
    "청양",
    "땡초",
    "캡사이신",
    "불닭",
    "매운탕",
    "hot",
    "spicy",
    "chili",
    "chilli",
    "chile",
)

SPICY_ICON_TOKENS = ("🌶", "🌶️", "고추그림", "고추 그림")

NON_SPICY_EXCEPTIONS = (
    "불고기",
    "고추튀김",
    "오이고추",
    "풋고추",
    "고추전",
)


def infer_is_spicy(menu_analysis: dict, raw_menu: dict | None = None, raw_lines=None) -> bool:
    """Return a binary spicy flag from menu text and nearby OCR spicy markers."""
    text_parts = [
        menu_analysis.get("menu_name_ko", ""),
        menu_analysis.get("description_ko", ""),
        menu_analysis.get("price_text", ""),
    ]

    if raw_menu:
        text_parts.extend(
            [
                raw_menu.get("rawName", ""),
                raw_menu.get("normalizedCandidate", ""),
                raw_menu.get("matchedMenu", ""),
                raw_menu.get("description", ""),
            ]
        )
        text_parts.extend(raw_menu.get("source", {}).get("lineTexts", []))

    combined = " ".join(part for part in text_parts if part)
    if has_spicy_signal(combined):
        return True

    if raw_menu and raw_lines:
        return any(has_spicy_signal(line.get("text", "")) for line in nearby_lines(raw_menu, raw_lines))

    return False


def has_spicy_signal(text: str) -> bool:
    if not text:
        return False

    compact = re.sub(r"\s+", "", text).lower()
    if not compact:
        return False

    if any(exception in compact for exception in NON_SPICY_EXCEPTIONS):
        exception_removed = compact
        for exception in NON_SPICY_EXCEPTIONS:
            exception_removed = exception_removed.replace(exception, "")
        if not any(keyword in exception_removed for keyword in SPICY_KEYWORDS):
            return False

    if any(token in compact for token in SPICY_ICON_TOKENS):
        return True

    if re.search(r"🌶+", text):
        return True

    return any(keyword in compact for keyword in SPICY_KEYWORDS)


def nearby_lines(raw_menu: dict, raw_lines: list) -> list:
    source = raw_menu.get("source", {})
    bbox = source.get("bbox")
    source_texts = set(source.get("lineTexts", []))
    if not bbox:
        return []

    nearby = []
    for line in raw_lines:
        text = line.get("text", "")
        if not text or text in source_texts:
            continue
        if line.get("page") != source.get("page"):
            continue
        if is_near_menu_bbox(line, bbox):
            nearby.append(line)

    return nearby


def is_near_menu_bbox(line: dict, bbox: list) -> bool:
    line_center_y = (line.get("y1", 0) + line.get("y2", 0)) / 2
    menu_center_y = (bbox[1] + bbox[3]) / 2
    menu_height = max(bbox[3] - bbox[1], 1)
    vertical_limit = max(menu_height * 0.8, 18)

    if abs(line_center_y - menu_center_y) > vertical_limit:
        return False

    horizontal_gap = max(bbox[0] - line.get("x2", 0), line.get("x1", 0) - bbox[2], 0)
    menu_width = max(bbox[2] - bbox[0], 1)
    return horizontal_gap <= max(menu_width * 0.35, 60)
