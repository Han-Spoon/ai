"""CLOVA General OCR field를 메뉴-가격 으로 조립하는 공간 파서.

CLOVA의 fields는 문장이 아니라 단어/낱글자 단위로 반환될 수 있음.
따라서 polygon 기준선과 가격 위치를 사용해 메뉴명을 복원.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from statistics import mean, median

from normalizer import normalize_name_text, normalize_price, remove_serving_amount
from price_metrics import count_matched_price_anchors


_FORMATTED_PRICE_RE = re.compile(
    r"(?<![\d])(?:₩\s*)?"
    r"(?P<price>(?:[1-9]\d{0,2}(?:\s*[,\.]\s*\d{3})+|[1-9]\d{2,5}))"
    r"\s*원?(?![\d-])"
)
_KOREAN_RE = re.compile(r"[가-힣]")
_BULLET_RE = re.compile(r"^[·•▶▷■□◆◇★☆▪▫/|\-]+$")
_SERVING_ONLY_RE = re.compile(
    r"^\s*\d+(?:\.\d+)?\s*(?:kg|g|그램|인분|인|개|人)\s*$", re.IGNORECASE
)
_NOISE_PARTS = (
    "http",
    "www",
    "원산지",
    "영업",
    "전화",
    "예약",
    "청소년",
    "미만",
    "포장됩니다",
)
_EXCLUDED_MENU_PARTS = ("사리추가",)
_SIZE_LABELS = {
    "소": "소",
    "중": "중",
    "대": "대",
    "小": "소",
    "中": "중",
    "大": "대",
}
_SIZE_ORDER = ("소", "중", "대")
# menu_005에서 실제로 확인된 CLOVA 오인식이다.
# 단독 문자열 교정에는 사용하지 않는다.
_OBSERVED_SIZE_LABEL_MISREADS = {
    "★": "대",
    "ㅊ": "대",
}


@dataclass(frozen=True)
class SpatialOption:
    label: str
    price: int
    price_raw: str
    confidence: float
    label_token: dict | None
    price_token: dict
    inferred: bool = False


@dataclass(frozen=True)
class SpatialPair:
    name: str
    price: int
    price_raw: str
    confidence: float
    source_tokens: tuple[dict, ...]
    options: tuple[SpatialOption, ...] = ()


def extract_clova_fields(payload: dict) -> list[dict]:
    """CLOVA V2 응답을 공급자 중립 token dict 목록으로 변환한다."""
    images = payload.get("images") or []
    if not images:
        raise ValueError("CLOVA OCR 응답에 images가 없습니다.")

    tokens: list[dict] = []
    for page_index, image in enumerate(images, start=1):
        result = image.get("inferResult")
        if result != "SUCCESS":
            message = image.get("message") or result or "UNKNOWN"
            raise ValueError(f"CLOVA OCR 이미지 분석 실패: {message}")

        converted = image.get("convertedImageInfo") or {}
        image_width = float(converted.get("width") or 0)
        image_height = float(converted.get("height") or 0)

        for field in image.get("fields") or []:
            text = str(field.get("inferText") or "").strip()
            vertices = (field.get("boundingPoly") or {}).get("vertices") or []
            if not text or len(vertices) < 4:
                continue

            polygon = tuple(
                (float(vertex.get("x", 0)), float(vertex.get("y", 0)))
                for vertex in vertices[:4]
            )
            xs = [point[0] for point in polygon]
            ys = [point[1] for point in polygon]
            tokens.append(
                {
                    "text": text,
                    "page": page_index,
                    "x1": min(xs),
                    "y1": min(ys),
                    "x2": max(xs),
                    "y2": max(ys),
                    "polygon": polygon,
                    "confidence": float(field.get("inferConfidence") or 0.0),
                    "lineBreak": bool(field.get("lineBreak")),
                    "source": "clova_field",
                    "imageWidth": image_width,
                    "imageHeight": image_height,
                }
            )

    return tokens


def parse_spatial_pairs(tokens: list[dict]) -> list[SpatialPair]:
    """가격 field를 anchor로 왼쪽의 메뉴명 token을 결합한다."""
    expanded = []
    for token in tokens:
        expanded.extend(_split_price_token(token))

    price_tokens = [token for token in expanded if token.get("price") is not None]
    text_tokens = [token for token in expanded if token.get("price") is None]
    pairs: list[SpatialPair] = []

    consumed_price_ids: set[int] = set()
    for option_group in _find_size_option_groups(price_tokens, text_tokens):
        pair = _pair_option_group_with_name(option_group, price_tokens, text_tokens)
        if pair is None:
            continue
        pairs.append(pair)
        consumed_price_ids.update(id(option.price_token) for option in pair.options)

    for price_token in price_tokens:
        if id(price_token) in consumed_price_ids:
            continue
        pair = _pair_price_with_name(price_token, price_tokens, text_tokens)
        if pair is not None:
            pairs.append(pair)

    pairs.sort(key=lambda pair: _reading_order(pair.source_tokens))
    return _deduplicate_pairs(pairs)


def _find_size_option_groups(
    prices: list[dict], texts: list[dict]
) -> list[list[SpatialOption]]:
    """같은 열에 반복되는 크기 라벨과 가격을 옵션 그룹으로 묶는다."""
    used_price_ids: set[int] = set()
    options: list[SpatialOption] = []

    size_tokens = sorted(
        (
            token
            for token in texts
            if _normalize_size_label(token.get("text", "")) is not None
        ),
        key=lambda token: (int(token.get("page") or 1), _centerline_y_at(token, _center_x(token))),
    )

    for label_token in size_tokens:
        candidates = []
        for price_token in prices:
            if id(price_token) in used_price_ids:
                continue
            match_score = _label_price_match_score(label_token, price_token)
            if match_score is None:
                continue
            candidates.append((*match_score, price_token))

        if not candidates:
            continue

        _, _, price_token = min(candidates, key=lambda item: (item[0], item[1]))
        used_price_ids.add(id(price_token))
        options.append(
            SpatialOption(
                label=_normalize_size_label(label_token["text"]),
                price=int(price_token["price"]),
                price_raw=str(price_token["priceRaw"]),
                confidence=round(
                    mean(
                        (
                            float(label_token.get("confidence") or 0.0),
                            float(price_token.get("confidence") or 0.0),
                        )
                    ),
                    3,
                ),
                label_token=label_token,
                price_token=price_token,
            )
        )

    groups: list[list[SpatialOption]] = []
    current: list[SpatialOption] = []
    for option in options:
        if current and (
            option.label in {item.label for item in current}
            or not _is_same_option_column(current[-1], option)
        ):
            if len(current) >= 2:
                groups.append(current)
            current = []
        current.append(option)

    if len(current) >= 2:
        groups.append(current)
    return groups


def _normalize_size_label(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text)
    return _SIZE_LABELS.get(compact) or _OBSERVED_SIZE_LABEL_MISREADS.get(compact)


def _label_price_match_score(
    label_token: dict, price_token: dict
) -> tuple[float, float] | None:
    if price_token.get("page") != label_token.get("page"):
        return None

    horizontal_gap = float(price_token["x1"]) - float(label_token["x2"])
    if horizontal_gap < -2:
        return None
    max_gap = max(_height(label_token), _height(price_token)) * 3.0
    if horizontal_gap > max_gap:
        return None

    baseline_error = _baseline_error(label_token, price_token)
    tolerance = max(_height(label_token), _height(price_token)) * 0.7 + 4.0
    if baseline_error > tolerance:
        return None
    return baseline_error, max(horizontal_gap, 0.0)


def _is_same_option_column(first: SpatialOption, second: SpatialOption) -> bool:
    if first.label_token.get("page") != second.label_token.get("page"):
        return False

    row_gap = _centerline_y_at(second.label_token, _center_x(second.label_token)) - _centerline_y_at(
        first.label_token, _center_x(first.label_token)
    )
    typical_height = max(
        _height(first.label_token),
        _height(second.label_token),
        _height(first.price_token),
        _height(second.price_token),
    )
    if row_gap <= 0 or row_gap > typical_height * 3.0:
        return False

    label_x_gap = abs(_center_x(first.label_token) - _center_x(second.label_token))
    price_x_gap = abs(float(first.price_token["x1"]) - float(second.price_token["x1"]))
    return label_x_gap <= typical_height and price_x_gap <= typical_height * 1.5


def _pair_option_group_with_name(
    options: list[SpatialOption], prices: list[dict], texts: list[dict]
) -> SpatialPair | None:
    option_label_ids = {id(option.label_token) for option in options}
    name_tokens = [token for token in texts if id(token) not in option_label_ids]

    anchor = _find_option_name_anchor(options, prices, name_tokens)
    options = _infer_missing_size_option(
        options,
        prices,
        texts,
        menu_name=anchor.name if anchor is not None else None,
    )
    if anchor is None:
        anchor = _find_option_name_anchor(options, prices, name_tokens)
    if anchor is None:
        return None

    source_tokens = list(anchor.source_tokens[:-1])
    for option in options:
        if option.label_token is not None:
            source_tokens.append(option.label_token)
        source_tokens.append(option.price_token)

    unique_tokens: list[dict] = []
    seen_ids = set()
    for token in source_tokens:
        if id(token) in seen_ids:
            continue
        seen_ids.add(id(token))
        unique_tokens.append(token)

    option_confidence = mean(option.confidence for option in options)
    return SpatialPair(
        name=anchor.name,
        price=options[0].price,
        price_raw=options[0].price_raw,
        confidence=round((anchor.confidence * 0.75) + (option_confidence * 0.25), 3),
        source_tokens=tuple(unique_tokens),
        options=tuple(options),
    )


def _find_option_name_anchor(
    options: list[SpatialOption], prices: list[dict], texts: list[dict]
) -> SpatialPair | None:
    for option in options:
        anchor = _pair_price_with_name(option.price_token, prices, texts)
        if anchor is not None:
            return anchor
    return None


def _infer_missing_size_option(
    options: list[SpatialOption],
    prices: list[dict],
    texts: list[dict],
    menu_name: str | None,
) -> list[SpatialOption]:
    """두 확정 옵션 사이 또는 끝의 단 하나의 누락 라벨만 추론."""
    if len(options) != 2 or len({option.label for option in options}) != 2:
        return options

    ordered = sorted(options, key=lambda option: _centerline_y_at(
        option.price_token, _center_x(option.price_token)
    ))
    first_rank = _SIZE_ORDER.index(ordered[0].label)
    second_rank = _SIZE_ORDER.index(ordered[1].label)
    direction = 1 if second_rank > first_rank else -1
    display_order = _SIZE_ORDER if direction > 0 else tuple(reversed(_SIZE_ORDER))
    missing_labels = set(_SIZE_ORDER) - {option.label for option in ordered}
    if len(missing_labels) != 1:
        return options

    missing_label = missing_labels.pop()
    first_index = display_order.index(ordered[0].label)
    second_index = display_order.index(ordered[1].label)
    missing_index = display_order.index(missing_label)
    if first_index == second_index:
        return options

    first_y = _centerline_y_at(
        ordered[0].price_token, _center_x(ordered[0].price_token)
    )
    second_y = _centerline_y_at(
        ordered[1].price_token, _center_x(ordered[1].price_token)
    )
    row_step = (second_y - first_y) / (second_index - first_index)
    typical_height = median(_height(option.price_token) for option in ordered)
    if row_step < typical_height * 0.45 or row_step > typical_height * 2.5:
        return options

    expected_y = first_y + (row_step * (missing_index - first_index))
    candidates = []
    used_price_ids = {id(option.price_token) for option in ordered}
    reference_x = mean(float(option.price_token["x2"]) for option in ordered)
    for price_token in prices:
        if id(price_token) in used_price_ids:
            continue
        if price_token.get("page") != ordered[0].price_token.get("page"):
            continue
        if abs(float(price_token["x2"]) - reference_x) > typical_height * 1.5:
            continue

        price_y = _centerline_y_at(price_token, _center_x(price_token))
        y_error = abs(price_y - expected_y)
        if y_error > max(4.0, typical_height * 0.55, row_step * 0.35):
            continue
        if not _is_monotonic_size_price(missing_label, price_token, ordered):
            continue
        if any(
            _normalize_size_label(token.get("text", "")) is not None
            and _label_price_match_score(token, price_token) is not None
            for token in texts
        ):
            continue

        candidate_name = _pair_price_with_name(price_token, prices, texts)
        if (
            menu_name
            and candidate_name is not None
            and re.sub(r"\s+", "", candidate_name.name)
            != re.sub(r"\s+", "", menu_name)
        ):
            continue
        candidates.append((y_error, price_token))

    if len(candidates) != 1:
        return options

    _, price_token = candidates[0]
    inferred = SpatialOption(
        label=missing_label,
        price=int(price_token["price"]),
        price_raw=str(price_token["priceRaw"]),
        confidence=round(
            min(
                float(price_token.get("confidence") or 0.0),
                mean(option.confidence for option in ordered) * 0.75,
            ),
            3,
        ),
        label_token=None,
        price_token=price_token,
        inferred=True,
    )
    return sorted(
        [*options, inferred],
        key=lambda option: _centerline_y_at(
            option.price_token, _center_x(option.price_token)
        ),
    )


def _is_monotonic_size_price(
    missing_label: str, price_token: dict, options: list[SpatialOption]
) -> bool:
    missing_rank = _SIZE_ORDER.index(missing_label)
    missing_price = int(price_token["price"])
    for option in options:
        option_rank = _SIZE_ORDER.index(option.label)
        if missing_rank > option_rank and missing_price <= option.price:
            return False
        if missing_rank < option_rank and missing_price >= option.price:
            return False
    return True


def count_price_anchors(tokens: list[dict]) -> int:
    return sum(
        1
        for token in tokens
        for part in _split_price_token(token)
        if part.get("price") is not None
    )


def parse_attempt_score(tokens: list[dict], menus: list[dict]) -> float:
    """원본/전처리 OCR 결과 중 더 구조적으로 일관된 결과를 고른다."""
    anchors = count_price_anchors(tokens)
    paired = len(menus)
    matched_prices = count_matched_price_anchors(menus)
    coverage = min(matched_prices / anchors, 1.0) if anchors else 0.0
    confidences = [float(menu.get("confidence") or 0.0) for menu in menus]
    confidence = mean(confidences) if confidences else 0.0
    single_char_ratio = (
        sum(len(re.sub(r"\s+", "", menu.get("rawName", ""))) == 1 for menu in menus)
        / paired
        if paired
        else 1.0
    )
    return round(
        (coverage * 55)
        + (confidence * 30)
        + min(paired, 15)
        - (single_char_ratio * 25),
        3,
    )


def should_retry_with_preprocessing(tokens: list[dict], menus: list[dict]) -> bool:
    anchors = count_price_anchors(tokens)
    if not tokens or not menus or len(menus) < 3:
        return True
    matched_prices = count_matched_price_anchors(menus)
    coverage = matched_prices / anchors if anchors else 0.0
    single_chars = sum(
        len(re.sub(r"\s+", "", menu.get("rawName", ""))) == 1 for menu in menus
    )
    return coverage < 0.72 or single_chars > max(1, len(menus) // 4)


def _split_price_token(token: dict) -> list[dict]:
    text = token.get("text", "").strip()
    if not text or any(
        marker in text.lower() for marker in ("http://", "https://", "www.")
    ):
        return [token]

    match = _FORMATTED_PRICE_RE.search(text)
    if not match:
        return [token]

    # 날짜/전화번호/범위 표기는 가격으로 취급하지 않는다.
    if "-" in match.group("price") or "/" in match.group("price"):
        return [token]

    price_raw = match.group().strip()
    digit_count = len(re.sub(r"\D", "", match.group("price")))
    if digit_count == 3 and "원" not in price_raw:
        return [token]
    price = normalize_price(price_raw)
    if price is None:
        return [token]

    prefix = text[: match.start()].strip(" ·•▶▷■□◆◇★☆▪▫_-/|")
    suffix = text[match.end() :].strip()
    if suffix and _KOREAN_RE.search(suffix):
        return [token]

    if not prefix:
        price_token = dict(token)
        price_token["text"] = price_raw
        price_token["price"] = price
        price_token["priceRaw"] = price_raw
        return [price_token]

    prefix_token, price_token = _split_bbox(token, match.start(), match.end())
    prefix_token["text"] = prefix
    price_token["text"] = price_raw
    price_token["price"] = price
    price_token["priceRaw"] = price_raw
    return [prefix_token, price_token]


def _split_bbox(token: dict, price_start: int, price_end: int) -> tuple[dict, dict]:
    text_length = max(len(token.get("text", "")), 1)
    start_ratio = max(0.05, min(price_start / text_length, 0.95))
    end_ratio = max(start_ratio, min(price_end / text_length, 1.0))
    x1 = float(token["x1"])
    x2 = float(token["x2"])
    width = max(x2 - x1, 1.0)

    prefix = dict(token)
    prefix["x2"] = x1 + (width * start_ratio)
    prefix["polygon"] = _rect_polygon(prefix)

    price = dict(token)
    price["x1"] = x1 + (width * start_ratio)
    price["x2"] = x1 + (width * end_ratio)
    price["polygon"] = _rect_polygon(price)
    return prefix, price


def _pair_price_with_name(
    price: dict, prices: list[dict], texts: list[dict]
) -> SpatialPair | None:
    left_boundary = _left_boundary(price, prices)
    aligned = []
    price_x = _center_x(price)

    for token in texts:
        if token.get("page") != price.get("page"):
            continue
        if _center_x(token) >= price_x:
            continue
        if float(token["x2"]) <= left_boundary:
            continue
        if _is_ignored_text(token.get("text", "")):
            continue

        error = _baseline_error(token, price)
        tolerance = max(_height(token), _height(price)) * 0.72 + 6.0
        if error <= tolerance:
            aligned.append((token, error))

    if aligned:
        name_tokens = _nearest_connected_tokens(price, aligned, left_boundary)
    else:
        # 작은 보조 메뉴판은 "메뉴명" 다음 줄에 "수량 가격"을 두기도 한다.
        name_tokens = _nearest_name_above_price(price, texts, left_boundary)
    if not name_tokens:
        return None

    raw_name = normalize_name_text(" ".join(token["text"] for token in name_tokens))
    raw_name = remove_serving_amount(raw_name)
    compact_name = re.sub(r"\s+", "", raw_name)
    if not _is_valid_menu_name(compact_name):
        return None

    all_tokens = tuple(name_tokens + [price])
    token_confidence = mean(
        float(token.get("confidence") or 0.0) for token in all_tokens
    )
    alignment_error = mean(_baseline_error(token, price) for token in name_tokens)
    tolerance = max(median(_height(token) for token in all_tokens), 1.0)
    geometry_score = max(0.0, 1.0 - (alignment_error / (tolerance * 1.5)))
    confidence = round((token_confidence * 0.7) + (geometry_score * 0.3), 3)

    return SpatialPair(
        name=raw_name,
        price=int(price["price"]),
        price_raw=str(price["priceRaw"]),
        confidence=confidence,
        source_tokens=all_tokens,
    )


def _nearest_name_above_price(
    price: dict, texts: list[dict], left_boundary: float
) -> list[dict]:
    price_center_y = (float(price["y1"]) + float(price["y2"])) / 2
    candidates = []
    for token in texts:
        if token.get("page") != price.get("page") or _is_ignored_text(
            token.get("text", "")
        ):
            continue
        token_center_y = (float(token["y1"]) + float(token["y2"])) / 2
        if token_center_y >= price_center_y or float(token["x2"]) <= left_boundary:
            continue
        horizontal_overlap = min(float(token["x2"]), float(price["x2"])) - max(
            float(token["x1"]), float(price["x1"])
        )
        horizontal_distance = abs(_center_x(token) - _center_x(price))
        if (
            horizontal_overlap <= 0
            and horizontal_distance > max(_height(token), _height(price)) * 2
        ):
            continue
        vertical_gap = float(price["y1"]) - float(token["y2"])
        if vertical_gap > max(_height(token), _height(price)) * 1.2:
            continue
        candidates.append((max(vertical_gap, 0.0), horizontal_distance, token))

    if not candidates:
        return []
    return [min(candidates, key=lambda item: (item[0], item[1]))[2]]


def _left_boundary(price: dict, prices: list[dict]) -> float:
    candidates = []
    for other in prices:
        if other is price or other.get("page") != price.get("page"):
            continue
        if _center_x(other) >= _center_x(price):
            continue
        # 같은 가격 열의 위/아래 가격은 왼쪽 열 경계가 아니다.
        if float(other["x2"]) >= float(price["x1"]):
            continue
        if (
            _baseline_error(other, price)
            > max(_height(other), _height(price)) * 0.8 + 8.0
        ):
            continue
        candidates.append(other)

    if not candidates:
        return -math.inf
    return max(float(other["x2"]) for other in candidates)


def _nearest_connected_tokens(
    price: dict, aligned: list[tuple[dict, float]], left_boundary: float
) -> list[dict]:
    best_error = min(item[1] for item in aligned)
    typical_height = median(_height(item[0]) for item in aligned)
    error_band = max(8.0, typical_height * 0.25)
    candidates = sorted(
        (item[0] for item in aligned if item[1] <= best_error + error_band),
        key=lambda token: float(token["x1"]),
    )
    if not candidates:
        return []

    # 가격에 가장 가까운 글자에서 왼쪽으로 확장한다. 다른 행의 큰 bbox가
    # 우연히 겹쳐도 local baseline 오차가 큰 후보는 먼저 제외된다.
    result = [candidates[-1]]
    median_height = median(_height(token) for token in candidates)
    max_gap = max(median_height * 3.2, 24.0)

    for token in reversed(candidates[:-1]):
        rightmost = result[0]
        gap = float(rightmost["x1"]) - float(token["x2"])
        if gap > max_gap:
            break
        if float(token["x2"]) <= left_boundary:
            break
        result.insert(0, token)

    return result


def _baseline_error(first: dict, second: dict) -> float:
    probe_x = (_center_x(first) + _center_x(second)) / 2
    return abs(_centerline_y_at(first, probe_x) - _centerline_y_at(second, probe_x))


def _centerline_y_at(token: dict, x: float) -> float:
    polygon = token.get("polygon")
    if polygon and len(polygon) >= 4:
        left_x = (polygon[0][0] + polygon[3][0]) / 2
        left_y = (polygon[0][1] + polygon[3][1]) / 2
        right_x = (polygon[1][0] + polygon[2][0]) / 2
        right_y = (polygon[1][1] + polygon[2][1]) / 2
        if abs(right_x - left_x) > 1e-6:
            ratio = (x - left_x) / (right_x - left_x)
            return left_y + ((right_y - left_y) * ratio)
    return (float(token["y1"]) + float(token["y2"])) / 2


def _is_ignored_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    lower = compact.lower()
    if not compact or _BULLET_RE.fullmatch(compact):
        return True
    if any(part.lower() in lower for part in _NOISE_PARTS):
        return True
    if _SERVING_ONLY_RE.fullmatch(compact):
        return True
    if compact.isdigit():
        return True
    return compact.endswith("류") or compact in {"주류", "차림표", "차림", "표"}


def _is_valid_menu_name(name: str) -> bool:
    if not name or not _KOREAN_RE.search(name):
        return False
    if any(part in name for part in _EXCLUDED_MENU_PARTS):
        return False
    if any(part.lower() in name.lower() for part in _NOISE_PARTS):
        return False
    return 2 <= len(name) <= 24


def _deduplicate_pairs(pairs: list[SpatialPair]) -> list[SpatialPair]:
    seen = set()
    result = []
    for pair in pairs:
        key = (re.sub(r"\s+", "", pair.name), pair.price)
        if key in seen:
            continue
        seen.add(key)
        result.append(pair)
    return result


def _reading_order(tokens: tuple[dict, ...]) -> tuple[int, float, float]:
    page = int(tokens[0].get("page") or 1)
    return (
        page,
        min(float(token["y1"]) for token in tokens),
        min(float(token["x1"]) for token in tokens),
    )


def _center_x(token: dict) -> float:
    return (float(token["x1"]) + float(token["x2"])) / 2


def _height(token: dict) -> float:
    polygon = token.get("polygon")
    if polygon and len(polygon) >= 4:
        left = math.dist(polygon[0], polygon[3])
        right = math.dist(polygon[1], polygon[2])
        return max((left + right) / 2, 1.0)
    return max(float(token["y2"]) - float(token["y1"]), 1.0)


def _rect_polygon(token: dict) -> tuple[tuple[float, float], ...]:
    return (
        (float(token["x1"]), float(token["y1"])),
        (float(token["x2"]), float(token["y1"])),
        (float(token["x2"]), float(token["y2"])),
        (float(token["x1"]), float(token["y2"])),
    )
