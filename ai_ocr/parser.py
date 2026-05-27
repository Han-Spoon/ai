from normalizer import normalize_price, remove_price, normalize_menu_name, looks_like_menu_name

def group_lines_by_row(lines, y_threshold=0.03):
    lines = sorted(lines, key=lambda x: (x["page"], x["y1"], x["x1"]))

    rows = []

    for line in lines:
        added = False
        cy = (line["y1"] + line["y2"]) / 2

        for row in rows:
            row_cy = sum((l["y1"] + l["y2"]) / 2 for l in row) / len(row)

            if line["page"] == row[0]["page"] and abs(cy - row_cy) <= y_threshold:
                row.append(line)
                added = True
                break

        if not added:
            rows.append([line])

    for row in rows:
        row.sort(key=lambda x: x["x1"])

    return rows


def is_noise(text: str):
    noise_keywords = ["영업", "전화", "예약", "원산지", "포장", "배달", "OPEN", "CLOSE", "메뉴판"]

    return any(keyword in text for keyword in noise_keywords)


def parse_menu_candidates(lines):
    clean_lines = [
        line for line in lines
        if line["text"].strip() and not is_noise(line["text"])
    ]

    rows = group_lines_by_row(clean_lines)
    menus = []

    for row in rows:
        joined = " ".join(line["text"] for line in row)
        price = normalize_price(joined)

        if price is not None:
            raw_name = remove_price(joined)
            raw_name = raw_name.strip("·•▶▷-_ ")

            if looks_like_menu_name(raw_name):
                normalized = normalize_menu_name(raw_name)

                menus.append({
                    "rawName": raw_name,
                    "normalizedCandidate": normalized,
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
                        ]
                    }
                })

    return menus