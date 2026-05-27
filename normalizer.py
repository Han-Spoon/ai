import re


PRICE_MIN = 1000
PRICE_MAX = 50000


def normalize_price(text: str):
    if not text:
        return None

    cleaned = text.replace("O", "0").replace("o", "0")
    cleaned = cleaned.replace("₩", "").replace("원", "")
    cleaned = cleaned.replace(",", "").replace(".", "")
    cleaned = re.sub(r"\s+", "", cleaned)

    for number in re.findall(r"\d+", cleaned):
        if len(number) < 4 and int(number) < PRICE_MIN:
            continue

        price = int(number)
        if PRICE_MIN <= price <= PRICE_MAX:
            return price

    return None


def remove_price(text: str):
    if not text:
        return ""

    without_price = re.sub(r"[₩]?\s*[\d,\.OoO\s]{3,}\s*원?", " ", text)
    return normalize_name_text(without_price)


def normalize_menu_name(text: str):
    text = normalize_name_text(text)
    text = re.sub(r"\s+", "", text)

    replacements = {
        "찌게": "찌개",
        "김치찌게": "김치찌개",
        "된장찌게": "된장찌개",
        "순두부찌게": "순두부찌개",
        "수육국밥": "수육국밥",
    }

    for wrong, correct in replacements.items():
        text = text.replace(wrong, correct)

    return text


def normalize_name_text(text: str):
    if not text:
        return ""

    text = text.strip()
    text = re.sub(r"[·•▶▷_\-\|\[\]\(\):]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def looks_like_menu_name(text: str):
    if not text:
        return False

    cleaned = normalize_name_text(text)
    compact = re.sub(r"\s+", "", cleaned)

    if len(compact) < 2:
        return False
    if not re.search(r"[가-힣]", compact):
        return False
    if mostly_numeric(compact):
        return False
    if looks_like_description(cleaned):
        return False

    return True


def mostly_numeric(text: str):
    if not text:
        return False

    numeric_count = len(re.findall(r"\d", text))
    return numeric_count > 0 and numeric_count / len(text) >= 0.5


def looks_like_description(text: str):
    if not text:
        return False

    compact = re.sub(r"\s+", "", text)
    sentence_markers = ("입니다", "드립니다", "가능", "사용", "포함", "제공", "추가", "선택")

    if len(compact) >= 18 and normalize_price(text) is None:
        return True
    if len(compact) >= 12 and any(marker in compact for marker in sentence_markers):
        return True

    return False
