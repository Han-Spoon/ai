import re
from difflib import SequenceMatcher

from menu_dictionary import MENU_NAMES, MENU_TO_CATEGORY


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


def normalize_price_detail(text: str):
    price = normalize_price(text)
    raw = extract_price_raw(text)
    warnings = []

    if raw and price is None:
        warnings.append("invalid_price_range")
    if price is not None and price < 3000:
        warnings.append("possible_missing_digit")

    return {
        "price": price,
        "priceRaw": raw,
        "priceCorrected": has_price_ocr_correction(raw),
        "priceWarnings": warnings,
    }


def extract_price_raw(text: str):
    if not text:
        return None

    match = re.search(r"[₩]?\s*(?:\d{1,2}|[OoO])\s*[,\.]?\s*[\dOoO]{3}\s*원?", text)
    if match:
        return match.group().strip()

    return None


def normalize_price_text(text: str):
    if not text:
        return ""

    cleaned = text.replace("O", "0").replace("o", "0")
    cleaned = cleaned.replace("₩", "").replace("원", "")
    cleaned = cleaned.replace(",", "").replace(".", "")
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned


def has_price_ocr_correction(text: str | None):
    if not text:
        return False

    return bool(re.search(r"[OoO₩원\.]", text))


def remove_price(text: str):
    if not text:
        return ""

    without_price = re.sub(r"[₩]?\s*[\d,\.OoO\s]{3,}\s*원?", " ", text)
    return normalize_name_text(without_price)


def normalize_menu_name(text: str):
    text = normalize_name_text(text)
    text = clean_menu_name_artifacts(text)
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

    matched = match_known_menu_name(text)
    return matched["name"] if matched else text


def match_known_menu_name(text: str):
    if not text:
        return None

    candidate = normalize_name_text(text)
    candidate = clean_menu_name_artifacts(candidate)
    candidate = re.sub(r"\s+", "", candidate)
    candidate = candidate.replace("찌게", "찌개")

    if candidate in MENU_TO_CATEGORY:
        return {"name": candidate, "category": MENU_TO_CATEGORY[candidate], "score": 1.0}

    best_name = None
    best_score = 0.0
    for menu_name in MENU_NAMES:
        score = SequenceMatcher(None, candidate, menu_name).ratio()
        if score > best_score:
            best_name = menu_name
            best_score = score

    if best_name and best_score >= 0.86 and len(candidate) == len(best_name):
        return {"name": best_name, "category": MENU_TO_CATEGORY[best_name], "score": round(best_score, 3)}

    return None


def normalize_name_text(text: str):
    if not text:
        return ""

    text = text.strip()
    text = re.sub(r"[·•▶▷■□◆◇★☆▪▫_\-\u2010-\u2015\|\[\]\(\):]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_menu_name_artifacts(text: str):
    if not text:
        return ""

    cleaned = normalize_name_text(text)
    cleaned = remove_serving_amount(cleaned)
    cleaned = re.sub(r"^[^가-힣A-Za-z0-9]+", "", cleaned)
    cleaned = re.sub(r"[^가-힣A-Za-z0-9\s]+$", "", cleaned)
    cleaned = re.sub(r"[^\w\s가-힣]+", " ", cleaned)
    cleaned = re.sub(r"\b[a-zA-Z]\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def remove_serving_amount(text: str):
    text = re.sub(r"\s*\d+(?:\.\d+)?\s*(?:kg|g|그램|인분)(?=$|[^A-Za-z가-힣])", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\d+\s*(?:인|人)\b", " ", text)
    return text


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
