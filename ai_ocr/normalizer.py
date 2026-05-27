import re

def normalize_price(text: str):
    if not text:
        return None

    text = text.replace("O", "0").replace("o", "0")
    text = text.replace("원", "").replace("₩", "")
    text = text.replace(",", "").replace(".", "")
    text = text.replace(" ", "")

    numbers = re.findall(r"\d+", text)

    for n in numbers:
        if len(n) >= 3:
            price = int(n)

            if 1000 <= price <= 50000:
                return price

    return None


def remove_price(text: str):
    if not text:
        return ""

    text = re.sub(r"[₩]?\s*[\d,\.OoO\s]{3,}\s*원?", "", text)
    return text.strip()


def normalize_menu_name(text: str):
    if not text:
        return ""

    text = text.strip()
    text = re.sub(r"\s+", "", text)

    replacements = {
        "찌게": "찌개",
        "김치찌게": "김치찌개",
        "된장찌게": "된장찌개",
        "순두부찌게": "순두부찌개",
        "수육국 밥": "수육국밥",
    }

    for wrong, correct in replacements.items():
        text = text.replace(wrong, correct)

    return text


def looks_like_menu_name(text: str):
    if not text:
        return False

    cleaned = re.sub(r"\s+", "", text)

    if len(cleaned) < 2:
        return False

    if re.search(r"\d{3,}", cleaned):
        return False

    return bool(re.search(r"[가-힣]", cleaned))