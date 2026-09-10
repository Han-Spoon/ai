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


@dataclass(frozen=True)
class SpatialPair:
    name: str
    price: int
    price_raw: str
    confidence: float
    source_tokens: tuple[dict, ...]


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

    for price_token in price_tokens:
        pair = _pair_price_with_name(price_token, price_tokens, text_tokens)
        if pair is not None:
            pairs.append(pair)

    pairs.sort(key=lambda pair: _reading_order(pair.source_tokens))
    return _deduplicate_pairs(pairs)


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
    coverage = min(paired / anchors, 1.0) if anchors else 0.0
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
    coverage = len(menus) / anchors if anchors else 0.0
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
