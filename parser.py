from normalizer import (
    looks_like_description,
    looks_like_menu_name,
    normalize_menu_name,
    normalize_name_text,
    normalize_price,
    remove_price,
)


NOISE_KEYWORDS = ("영업", "전화", "예약", "원산지", "포장", "배달", "OPEN", "CLOSE", "메뉴판")


def parse_menu_candidates(lines):
    clean_lines = [line for line in lines if should_keep_line(line)]
    rows = group_lines_by_row(clean_lines)
    menus = []

    for row in rows:
        row = sorted(row, key=lambda item: item["x1"])
        joined = " ".join(line["text"] for line in row)
        price = normalize_price(joined)

        if price is None:
            continue

        raw_name = extract_name_from_row(row)
        if not looks_like_menu_name(raw_name):
            continue

        menus.append(
            {
                "rawName": raw_name,
                "normalizedCandidate": normalize_menu_name(raw_name),
                "price": price,
                "confidence": 0.88,
                "source": {
                    "page": row[0]["page"],
                    "lineTexts": [line["text"] for line in row],
                    "bbox": [
                        min(line["x1"] for line in row),
                        min(line["y1"] for line in row),
                        max(line["x2"] for line in row),
                        max(line["y2"] for line in row),
                    ],
                },
            }
        )

    return menus


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
