from normalizer import (
    looks_like_description,
    looks_like_menu_name,
    match_known_menu_name,
    normalize_menu_name,
    normalize_name_text,
    normalize_price,
    normalize_price_detail,
    remove_price,
)
import re


NOISE_KEYWORDS = ("영업", "전화", "예약", "원산지", "포장", "배달", "OPEN", "CLOSE", "메뉴판")
NON_FOOD_KEYWORDS = ("주류", "소주", "맥주", "막걸리", "사리추가")


def parse_menu_candidates(lines):
    clean_lines = [line for line in lines if should_keep_line(line)]
    menus = []
    consumed_line_ids = set()

    for line in clean_lines:
        pairs = extract_structured_pairs_from_text(line["text"])
        if not pairs:
            continue

        consumed_line_ids.add(id(line))
        for pair in pairs:
            raw_name = pair["rawName"]
            if not looks_like_menu_name(raw_name) or should_skip_menu_name(raw_name):
                continue

            menus.append(
                build_menu_item(
                    raw_name,
                    pair["price"],
                    [line],
                    price_raw=pair.get("priceRaw"),
                    options=pair.get("options"),
                )
            )

    rows = group_lines_by_row([line for line in clean_lines if id(line) not in consumed_line_ids])

    for row in rows:
        row = sorted(row, key=lambda item: item["x1"])
        row_items = extract_menu_items_from_row(row)
        if row_items:
            menus.extend(row_items)
            continue

        joined = " ".join(line["text"] for line in row)
        price = normalize_price(joined)

        if price is None:
            continue

        raw_name = extract_name_from_row(row)
        if not looks_like_menu_name(raw_name) or should_skip_menu_name(raw_name):
            continue

        menus.append(build_menu_item(raw_name, price, row))

    attach_descriptions(menus, lines, consumed_line_ids)
    return menus


def extract_menu_items_from_row(row):
    if len(row) < 2:
        return []

    items = []
    pending_name_lines = []

    for line in row:
        text = line.get("text", "").strip()
        price = normalize_price(text)

        if price is not None:
            if pending_name_lines:
                raw_name = normalize_name_text(" ".join(name_line["text"] for name_line in pending_name_lines))
                if looks_like_menu_name(raw_name) and not should_skip_menu_name(raw_name):
                    items.append(build_menu_item(raw_name, price, pending_name_lines + [line]))
                pending_name_lines = []
            continue

        if looks_like_menu_name(text) and not should_skip_menu_name(text):
            pending_name_lines.append(line)

    return items


def build_menu_item(raw_name, price, source_lines, price_raw=None, options=None):
    normalized_name = normalize_menu_name(raw_name)
    matched = match_known_menu_name(raw_name)
    price_detail = normalize_price_detail(" ".join(line["text"] for line in source_lines))
    if price_raw:
        price_detail = normalize_price_detail(price_raw)

    item = {
        "rawName": raw_name,
        "normalizedCandidate": normalized_name,
        "price": price,
        "priceRaw": price_raw or price_detail["priceRaw"],
        "priceCorrected": price_detail["priceCorrected"],
        "priceWarnings": price_detail["priceWarnings"],
        "options": options or [],
        "confidence": 0.88,
        "source": {
            "page": source_lines[0]["page"],
            "lineTexts": [line["text"] for line in source_lines],
            "bbox": [
                min(line["x1"] for line in source_lines),
                min(line["y1"] for line in source_lines),
                max(line["x2"] for line in source_lines),
                max(line["y2"] for line in source_lines),
            ],
        },
    }

    if matched:
        item["matchedMenu"] = matched["name"]
        item["category"] = matched["category"]
        item["matchScore"] = matched["score"]
        item["nameCorrected"] = matched["name"] != re.sub(r"\s+", "", raw_name)
    else:
        item["matchedMenu"] = None
        item["category"] = None
        item["matchScore"] = None
        item["nameCorrected"] = normalized_name != re.sub(r"\s+", "", raw_name)

    item["description"] = ""
    item["descriptionLines"] = []
    return item


def extract_structured_pairs_from_text(text):
    split_pairs = extract_mixed_plain_and_option_pairs(text)
    if split_pairs:
        return split_pairs

    option_menu = extract_option_menu_from_text(text)
    if option_menu:
        return [option_menu]

    return extract_menu_price_pairs_from_text(text)


def extract_mixed_plain_and_option_pairs(text):
    price_matches = list(find_price_matches(text))
    labeled_matches = list(find_labeled_price_matches(text))
    if len(price_matches) < 2 or not labeled_matches:
        return []

    first_labeled_start = labeled_matches[0].start()
    first_option_price_start = labeled_matches[0].start("price")

    prefix = text[:first_labeled_start]
    previous_prices = [match for match in price_matches if match.end() <= first_labeled_start]
    option_name_start = previous_prices[-1].end() if previous_prices else 0
    option_name_text = text[option_name_start:first_option_price_start]
    option_menu_name = clean_inline_menu_name(option_name_text)

    plain_pairs = extract_menu_price_pairs_from_text(prefix)
    if not plain_pairs:
        return []

    raw_name = remove_trailing_option_label(option_menu_name)
    if not raw_name or not looks_like_menu_name(raw_name):
        return []

    options = []
    for match in labeled_matches:
        option_name = normalize_name_text(match.group("label"))
        price_raw = match.group("price").strip()
        price = normalize_price(price_raw)
        if price is None:
            continue
        options.append({"name": option_name, "price": price, "priceRaw": price_raw})

    if not options:
        return []

    return plain_pairs + [
        {
            "rawName": raw_name,
            "price": options[0]["price"],
            "priceRaw": options[0]["priceRaw"],
            "options": options,
        }
    ]


def extract_option_menu_from_text(text):
    matches = list(find_labeled_price_matches(text))
    if len(matches) < 2:
        return None

    raw_name = clean_inline_menu_name(text[: matches[0].start()])
    if not raw_name or not looks_like_menu_name(raw_name):
        return None

    options = []
    for match in matches:
        option_name = normalize_name_text(match.group("label"))
        price_raw = match.group("price").strip()
        price = normalize_price(price_raw)
        if price is None:
            continue
        options.append(
            {
                "name": option_name,
                "price": price,
                "priceRaw": price_raw,
            }
        )

    if not options:
        return None

    return {
        "rawName": raw_name,
        "price": options[0]["price"],
        "priceRaw": options[0]["priceRaw"],
        "options": options,
    }


def remove_trailing_option_label(text):
    labels = ("대짜", "중짜", "소짜", "대", "중", "소", "특", "보통", "곱빼기", "곱배기", "1인", "2인", "3인", "4인")
    compact = normalize_name_text(text)
    for label in labels:
        compact = re.sub(rf"\s*{label}$", "", compact)
    return normalize_name_text(compact)


def find_labeled_price_matches(text):
    labels = r"대짜|중짜|소짜|대|중|소|특|보통|곱빼기|곱배기|1인|2인|3인|4인"
    price = r"[₩]?\s*(?:\d{1,2}|[OoO])\s*[,\.]?\s*[\dOoO]{3}\s*원?"
    pattern = rf"(?P<label>{labels})\s*(?P<price>{price})"
    return re.finditer(pattern, text)


def extract_menu_price_pairs_from_text(text):
    price_matches = list(find_price_matches(text))
    if not price_matches:
        return []

    pairs = []
    cursor = 0

    for match in price_matches:
        raw_name = text[cursor:match.start()]
        raw_name = clean_inline_menu_name(raw_name)
        price_raw = match.group().strip()
        price = normalize_price(price_raw)

        if price is not None and raw_name:
            pairs.append({"rawName": raw_name, "price": price, "priceRaw": price_raw})

        cursor = match.end()

    pairs = merge_option_pairs(pairs)

    return pairs


def merge_option_pairs(pairs):
    if len(pairs) < 2:
        return pairs

    merged = []
    for pair in pairs:
        option_name = extract_option_label(pair["rawName"])
        if option_name and merged:
            merged[-1].setdefault("options", [])
            if not merged[-1]["options"]:
                merged[-1]["options"].append(
                    {
                        "name": "기본",
                        "price": merged[-1]["price"],
                        "priceRaw": merged[-1]["priceRaw"],
                    }
                )
            merged[-1]["options"].append(
                {
                    "name": option_name,
                    "price": pair["price"],
                    "priceRaw": pair["priceRaw"],
                }
            )
            continue

        merged.append(pair)

    return merged


def extract_option_label(text):
    compact = re.sub(r"\s+", "", text)
    labels = ("대짜", "중짜", "소짜", "대", "중", "소", "특", "보통", "곱빼기", "곱배기", "1인", "2인", "3인", "4인")
    for label in labels:
        if compact == label:
            return label
    return None


def find_price_matches(text):
    # Handles 11,000 / 11000 / 7.000 / 8,O00 / ₩9,000 / 10,000원.
    pattern = r"[₩]?\s*(?:\d{1,2}|[OoO])\s*[,\.]?\s*[\dOoO]{3}\s*원?"
    return re.finditer(pattern, text)


def clean_inline_menu_name(text):
    text = remove_price(text)
    text = re.sub(r"^[\dOoO\s,\.]+", "", text)
    text = re.sub(r"^[\/\\\s]+", "", text)
    text = normalize_name_text(text)
    return text


def should_skip_menu_name(text):
    compact = re.sub(r"\s+", "", text)
    return any(keyword in compact for keyword in NON_FOOD_KEYWORDS)


def attach_descriptions(menus, lines, consumed_line_ids):
    if not menus:
        return

    menu_line_texts = {
        text
        for menu in menus
        for text in menu["source"]["lineTexts"]
    }

    description_lines = []
    for line in lines:
        text = line.get("text", "").strip()
        if not text or text in menu_line_texts or id(line) in consumed_line_ids:
            continue
        if normalize_price(text) is not None:
            continue
        if any(keyword in text.upper() for keyword in NOISE_KEYWORDS):
            continue
        if is_category_heading(text):
            continue
        if looks_like_menu_name(text) and len(re.sub(r"\s+", "", text)) <= 8:
            continue

        description_lines.append(line)

    for line in description_lines:
        target = find_nearest_menu_above(line, menus)
        if not target:
            continue

        description = normalize_name_text(line["text"])
        target["descriptionLines"].append(description)
        target["description"] = " ".join(target["descriptionLines"])


def find_nearest_menu_above(line, menus):
    best_menu = None
    best_distance = None
    line_y = (line["y1"] + line["y2"]) / 2

    for menu in menus:
        bbox = menu["source"]["bbox"]
        menu_bottom = bbox[3]
        if menu["source"]["page"] != line["page"]:
            continue
        if line_y < menu_bottom:
            continue
        if not horizontally_related(line, bbox):
            continue

        distance = line_y - menu_bottom
        if distance > infer_description_gap(line):
            continue
        if best_distance is None or distance < best_distance:
            best_menu = menu
            best_distance = distance

    return best_menu


def horizontally_related(line, bbox):
    overlap = min(line["x2"], bbox[2]) - max(line["x1"], bbox[0])
    line_width = max(line["x2"] - line["x1"], 1)
    return overlap > 0 or abs(line["x1"] - bbox[0]) <= line_width


def infer_description_gap(line):
    max_y = max(line.get("y1", 0), line.get("y2", 0))
    return 120.0 if max_y > 20 else 0.08


def is_category_heading(text):
    compact = re.sub(r"\s+", "", text)
    headings = ("구이", "국", "기타", "김치", "나물", "떡", "만두", "면", "무침", "밥", "볶음", "쌈", "장", "전", "전골", "조림", "죽", "찌개", "찜", "탕", "튀김", "한과", "해물", "회")
    return compact in headings or compact.endswith("류") or compact.endswith("메뉴")


def group_lines_by_row(lines, y_threshold=None):
    if y_threshold is None:
        y_threshold = infer_y_threshold(lines)

    sorted_lines = sorted(lines, key=lambda item: (item["page"], item["y1"], item["x1"]))
    rows = []

    for line in sorted_lines:
        center_y = (line["y1"] + line["y2"]) / 2
        matched_row = None

        for row in rows:
            if line["page"] != row[0]["page"]:
                continue

            row_center_y = sum((item["y1"] + item["y2"]) / 2 for item in row) / len(row)
            if abs(center_y - row_center_y) <= y_threshold:
                matched_row = row
                break

        if matched_row is None:
            rows.append([line])
        else:
            matched_row.append(line)

    for row in rows:
        row.sort(key=lambda item: item["x1"])

    return rows


def infer_y_threshold(lines):
    if not lines:
        return 0.03

    max_y = max(max(line.get("y1", 0), line.get("y2", 0)) for line in lines)
    if max_y > 20:
        return 12.0

    return 0.03


def should_keep_line(line):
    text = line.get("text", "").strip()
    if not text:
        return False

    has_price = normalize_price(text) is not None
    upper_text = text.upper()

    if not has_price and any(keyword in upper_text for keyword in NOISE_KEYWORDS):
        return False
    if not has_price and looks_like_description(text):
        return False

    return True


def extract_name_from_row(row):
    name_parts = []

    for line in row:
        text = line["text"]
        if normalize_price(text) is not None:
            text = remove_price(text)

        text = normalize_name_text(text)
        if text and normalize_price(text) is None:
            name_parts.append(text)

    if name_parts:
        return normalize_name_text(" ".join(name_parts))

    joined = " ".join(line["text"] for line in row)
    return remove_price(joined)
