import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
OCR_DIR = ROOT / "ai_ocr"
if str(OCR_DIR) not in sys.path:
    sys.path.insert(0, str(OCR_DIR))

import image_quality  # noqa: E402
import ocr_client  # noqa: E402
from clova_layout import (  # noqa: E402
    count_price_anchors,
    extract_clova_fields,
    parse_spatial_pairs,
    should_retry_with_preprocessing,
)
from ocr_client import ClovaOCRClient, OCRConfigError  # noqa: E402
from normalizer import normalize_menu_name, split_menu_name_and_origin  # noqa: E402
from parser import build_menu_item, parse_menu_candidates  # noqa: E402
from result_builder import (  # noqa: E402
    build_final_result,
    build_menu_analysis,
    build_scan_quality,
    expand_slash_menu_choices,
)

_OCR_MAIN_SPEC = importlib.util.spec_from_file_location(
    "ocr_main_under_test", OCR_DIR / "main.py"
)
ocr_main = importlib.util.module_from_spec(_OCR_MAIN_SPEC)
_OCR_MAIN_SPEC.loader.exec_module(ocr_main)


@pytest.mark.parametrize(
    "endpoint",
    ["PLACEHOLDER", "clova.example.test/ocr", "http://example.test/ocr"],
)
def test_clova_client_rejects_placeholder_or_non_https_endpoint(
    endpoint, monkeypatch
):
    monkeypatch.setenv("CLOVA_OCR_URL", endpoint)
    monkeypatch.setenv("CLOVA_OCR_SECRET", "test-secret")

    with pytest.raises(OCRConfigError):
        ClovaOCRClient()


# CLOVA 원문을 공간 파싱하고, 확정적 메뉴명 교정까지 적용한 계약.
# rawName은 아래 별도 assertion으로 보존 여부를 검증한다.
EXPECTED_NORMALIZED_MENUS = {
    "menu_001": {
        ("돼지불백", 11000),
        ("불백비빔", 11000),
        ("제육볶음", 11000),
        ("닭도리탕", 11000),
        ("반계탕", 11000),
        ("동태찌개", 11000),
        ("김치찌개", 11000),
        ("짜글이찌개", 11000),
        ("된장찌개", 9000),
        ("순두부찌개", 9000),
        ("부대찌개", 11000),
        ("떡국", 9000),
        ("오징어볶음", 11000),
        ("고등어구이", 11000),
        ("소주", 5000),
        ("맥주", 5000),
        ("막걸리", 5000),
    },
    "menu_002": {
        ("자율식백반", 5500),
        ("소머리국밥", 9000),
        ("김치찌개", 7000),
        ("된장찌개백반", 7000),
        ("순두부찌개", 7000),
        ("공기밥", 1000),
        ("삼겹살", 13000),
        ("두루치기", 13000),
        ("막걸리", 3000),
        ("소주", 4000),
        ("맥주", 4000),
        ("음료수", 2000),
        ("계란후라이", 500),
    },
    "menu_003": {
        ("김치찌개", 6500),
        ("낙지덮밥", 6500),
        ("떡갈비", 6500),
        ("알탕", 6500),
        ("아구지리탕", 8000),
        ("갈치조림", 6500),
        ("된장찌개", 6500),
        ("참치회덮밥", 6500),
        ("제육덮밥", 6500),
        ("가자미탕", 6500),
        ("연포탕", 8000),
        ("닭도리탕", 6500),
    },
}


@pytest.mark.parametrize(
    ("raw_name", "expected_menu", "expected_origin"),
    [
        ("육회국내산 암소한우", "육회", "국내산 암소한우"),
        ("육회비빔밥 국내산", "육회비빔밥", "국내산"),
        ("소고기국밥호주산", "소고기국밥", "호주산"),
        ("도가니탕 미국산", "도가니탕", "미국산"),
        ("수제돈까스(국내산)", "수제돈까스", "국내산"),
    ],
)
def test_origin_suffix_is_separated_from_menu_name(
    raw_name, expected_menu, expected_origin
):
    menu_name, origin_text = split_menu_name_and_origin(raw_name)

    assert normalize_menu_name(menu_name) == expected_menu
    assert origin_text == expected_origin


@pytest.mark.parametrize(
    "menu_name",
    ["자연산광어회", "부산어묵", "산채비빔밥", "한우육회", "국내산 한우육회"],
)
def test_non_suffix_origin_like_text_is_not_removed(menu_name):
    split_name, origin_text = split_menu_name_and_origin(menu_name)

    assert split_name == menu_name
    assert origin_text is None


def test_origin_is_preserved_in_ocr_menu_response():
    source_lines = [
        {"text": "소고기국밥 호주산", "page": 1, "x1": 0, "y1": 0, "x2": 100, "y2": 20},
        {"text": "8,000", "page": 1, "x1": 110, "y1": 0, "x2": 160, "y2": 20},
    ]
    menu = build_menu_item(
        "소고기국밥 호주산", 8000, source_lines, price_raw="8,000"
    )

    assert menu["normalizedCandidate"] == "소고기국밥"
    assert menu["originText"] == "호주산"
    assert menu["correctionReason"] == "origin_metadata_removed"

    response = build_menu_analysis(menu, display_order=1)
    assert response["raw_menu_name"] == "소고기국밥 호주산"
    assert response["menu_name_ko"] == "소고기국밥"
    assert response["origin_text"] == "호주산"
    assert response["ocr_confidence"] == 0.88


def _clova_token(text, x1, y1, x2, y2):
    return {
        "text": text,
        "page": 1,
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "polygon": ((x1, y1), (x2, y1), (x2, y2), (x1, y2)),
        "confidence": 0.99,
        "source": "clova_field",
    }


@pytest.mark.parametrize("menu_name", ["Chicken", "牛肉麵", "チキンカレー"])
def test_foreign_only_menu_names_are_not_dropped(menu_name):
    pairs = parse_spatial_pairs(
        [
            _clova_token(menu_name, 100, 100, 220, 124),
            _clova_token("12,000", 250, 100, 310, 124),
        ]
    )

    assert len(pairs) == 1
    assert pairs[0].name == menu_name


@pytest.mark.parametrize("menu_name", ["소주/맥주", "고기+야채추가"])
def test_internal_menu_delimiters_are_preserved_for_normalization(menu_name):
    menus = parse_menu_candidates(
        [
            _clova_token(menu_name, 100, 100, 220, 124),
            _clova_token("8,000", 250, 100, 310, 124),
        ]
    )

    assert len(menus) == 1
    assert menus[0]["rawName"] == menu_name
    assert build_menu_analysis(menus[0], 1)["raw_menu_name"] == menu_name


def test_slash_choices_expand_but_plus_combination_stays_one_menu():
    source = {
        "rawName": "소주/맥주",
        "normalizedCandidate": "소주맥주",
        "originText": None,
    }
    combined = {
        "rawName": "치킨+감자튀김",
        "normalizedCandidate": "치킨감자튀김",
        "originText": None,
    }

    expanded = expand_slash_menu_choices([source, combined])

    assert [menu["rawName"] for menu in expanded] == [
        "소주",
        "맥주",
        "치킨+감자튀김",
    ]
    assert [menu["normalizedCandidate"] for menu in expanded] == [
        "소주",
        "맥주",
        "치킨감자튀김",
    ]


def test_numeric_fraction_is_not_split_as_menu_choices():
    source = {"rawName": "반마리 1/2", "normalizedCandidate": "반마리1/2"}
    assert expand_slash_menu_choices([source]) == [source]


def test_edge_slash_is_decoration_but_internal_slash_is_preserved():
    menus = parse_menu_candidates(
        [
            _clova_token("/ 소주/맥주 /", 100, 100, 220, 124),
            _clova_token("8,000", 250, 100, 310, 124),
        ]
    )

    assert menus[0]["rawName"] == "소주/맥주"


def test_serving_text_is_preserved_in_ocr_raw_name_for_agent_two():
    menus = parse_menu_candidates(
        [
            _clova_token("된장찌개(1인)", 100, 100, 230, 124),
            _clova_token("8,000", 250, 100, 310, 124),
        ]
    )

    assert menus[0]["rawName"] == "된장찌개(1인)"
    assert menus[0]["normalizedCandidate"] == "된장찌개"


def test_translation_on_a_separate_baseline_does_not_enter_menu_name():
    pairs = parse_spatial_pairs(
        [
            _clova_token("물냉면", 100, 100, 180, 124),
            _clova_token("Cold Noodle", 100, 132, 220, 150),
            _clova_token("10,000", 250, 100, 310, 124),
        ]
    )

    assert len(pairs) == 1
    assert pairs[0].name == "물냉면"


def test_foreign_text_starts_as_unknown_until_agent_two_classifies_it():
    response = build_menu_analysis(
        {"rawName": "Beef 불고기", "normalizedCandidate": "beef불고기"},
        display_order=1,
    )

    assert response["foreign_text_segments"] == [
        {"text": "Beef", "role": "unknown", "confidence": 0.0}
    ]


def test_korean_size_rows_are_grouped_into_single_menu_options():
    tokens = [
        _clova_token("소고기버섯전골", 100, 100, 240, 124),
        _clova_token("소", 250, 100, 270, 124),
        _clova_token("30,000", 290, 100, 350, 124),
        _clova_token("대", 250, 132, 270, 156),
        _clova_token("40,000", 290, 132, 350, 156),
        _clova_token("매운갈비찜", 100, 172, 220, 196),
        _clova_token("소", 250, 172, 270, 196),
        _clova_token("22,000", 290, 172, 350, 196),
        _clova_token("중", 250, 204, 270, 228),
        _clova_token("33,000", 290, 204, 350, 228),
        _clova_token("대", 250, 236, 270, 260),
        _clova_token("45,000", 290, 236, 350, 260),
    ]

    pairs = parse_spatial_pairs(tokens)

    assert [(pair.name, pair.price) for pair in pairs] == [
        ("소고기버섯전골", 30000),
        ("매운갈비찜", 22000),
    ]
    assert [[(option.label, option.price) for option in pair.options] for pair in pairs] == [
        [("소", 30000), ("대", 40000)],
        [("소", 22000), ("중", 33000), ("대", 45000)],
    ]

    menus = parse_menu_candidates(tokens)
    response = build_menu_analysis(menus[1], display_order=2)
    assert response["menu_name_ko"] == "매운갈비찜"
    assert response["price_text"] == "22000"
    assert response["price_options"] == [
        {"label": "소", "price": 22000},
        {"label": "중", "price": 33000},
        {"label": "대", "price": 45000},
    ]


def test_single_korean_size_token_is_not_treated_as_option_group():
    tokens = [
        _clova_token("소고기", 100, 100, 180, 124),
        _clova_token("소", 250, 100, 270, 124),
        _clova_token("20,000", 290, 100, 350, 124),
    ]

    pairs = parse_spatial_pairs(tokens)

    assert len(pairs) == 1
    assert pairs[0].name == "소고기 소"
    assert pairs[0].options == ()


def test_cjk_size_labels_are_normalized_to_korean_options():
    tokens = [
        _clova_token("감자탕", 100, 100, 180, 124),
        _clova_token("大", 250, 100, 270, 124),
        _clova_token("39,000", 290, 100, 350, 124),
        _clova_token("中", 250, 132, 270, 156),
        _clova_token("34,000", 290, 132, 350, 156),
        _clova_token("小", 250, 164, 270, 188),
        _clova_token("28,000", 290, 164, 350, 188),
    ]

    pairs = parse_spatial_pairs(tokens)

    assert len(pairs) == 1
    assert pairs[0].name == "감자탕"
    assert [(option.label, option.price) for option in pairs[0].options] == [
        ("대", 39000),
        ("중", 34000),
        ("소", 28000),
    ]


def test_menu_005_recovers_cjk_and_observed_misread_size_labels():
    menus = parse_menu_candidates(_sample_tokens("menu_005"))
    menus_by_name = {menu["normalizedCandidate"]: menu for menu in menus}

    assert menus_by_name["감자탕"]["options"] == [
        {"name": "대", "price": 39000, "priceRaw": "39,000"},
        {"name": "중", "price": 34000, "priceRaw": "34,000"},
        {
            "name": "소",
            "price": 28000,
            "priceRaw": "28,000",
            "inferred": True,
        },
    ]
    assert menus_by_name["등뼈찜"]["options"] == [
        {"name": "대", "price": 44000, "priceRaw": "44,000"},
        {"name": "중", "price": 38000, "priceRaw": "38,000"},
    ]

    response = build_menu_analysis(menus_by_name["감자탕"], display_order=1)
    assert response["price_options"][-1] == {
        "label": "소",
        "price": 28000,
        "inferred": True,
    }


@pytest.mark.parametrize(
    ("missing_label", "visible_rows", "missing_price"),
    [
        ("대", [("중", 34000), ("소", 28000)], 39000),
        ("중", [("대", 39000), ("소", 28000)], 34000),
        ("소", [("대", 39000), ("중", 34000)], 28000),
    ],
)
def test_missing_size_label_is_inferred_at_any_position(
    missing_label, visible_rows, missing_price
):
    rows = [("대", 39000), ("중", 34000), ("소", 28000)]
    tokens = [_clova_token("감자탕", 100, 100, 180, 124)]
    for index, (label, price) in enumerate(rows):
        y1 = 100 + (index * 32)
        if (label, price) in visible_rows:
            tokens.append(_clova_token(label, 250, y1, 270, y1 + 24))
        tokens.append(
            _clova_token(f"{price:,}", 290, y1, 350, y1 + 24)
        )

    pairs = parse_spatial_pairs(tokens)

    assert len(pairs) == 1
    inferred = next(option for option in pairs[0].options if option.inferred)
    assert (inferred.label, inferred.price) == (missing_label, missing_price)


def test_missing_label_inference_does_not_absorb_next_menu_price():
    tokens = [
        _clova_token("감자탕", 100, 100, 180, 124),
        _clova_token("대", 250, 100, 270, 124),
        _clova_token("39,000", 290, 100, 350, 124),
        _clova_token("중", 250, 132, 270, 156),
        _clova_token("34,000", 290, 132, 350, 156),
        _clova_token("해물부추전", 100, 164, 220, 188),
        _clova_token("15,000", 290, 164, 350, 188),
    ]

    pairs = parse_spatial_pairs(tokens)
    pairs_by_name = {pair.name: pair for pair in pairs}

    assert {pair.name for pair in pairs} == {"감자탕", "해물부추전"}
    actual_options = [
        (option.label, option.price)
        for option in pairs_by_name["감자탕"].options
    ]
    assert actual_options == [
        ("대", 39000),
        ("중", 34000),
    ]


def test_observed_misread_symbol_is_not_corrected_without_repeated_option_rows():
    tokens = [
        _clova_token("감자탕", 100, 100, 180, 124),
        _clova_token("★", 250, 100, 270, 124),
        _clova_token("39,000", 290, 100, 350, 124),
    ]

    pairs = parse_spatial_pairs(tokens)

    assert len(pairs) == 1
    assert pairs[0].name == "감자탕"
    assert pairs[0].options == ()


@pytest.mark.parametrize("sample_name", ["menu_001", "menu_002", "menu_003"])
def test_clova_sample_extracts_exact_menu_price_pairs(sample_name):
    payload_path = ROOT / "sample_data" / f"clova_response_{sample_name}.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))

    tokens = extract_clova_fields(payload)
    menus = parse_menu_candidates(tokens)
    actual = {(menu["normalizedCandidate"], menu["price"]) for menu in menus}

    assert actual == EXPECTED_NORMALIZED_MENUS[sample_name]
    assert all(menu["confidence"] > 0 for menu in menus)
    assert all(menu["source"]["provider"] == "clova" for menu in menus)
    if sample_name == "menu_003":
        corrected = next(
            menu for menu in menus if menu["normalizedCandidate"] == "가자미탕"
        )
        assert corrected["rawName"].replace(" ", "") == "가재미탕"
        assert corrected["nameCorrected"] is True


def test_calendar_range_and_url_are_not_prices():
    payload = json.loads(
        (ROOT / "sample_data" / "clova_response_menu_003.json").read_text(
            encoding="utf-8"
        )
    )
    tokens = extract_clova_fields(payload)

    menus = parse_menu_candidates(tokens)

    assert count_price_anchors(tokens) == 12
    assert all(menu["price"] != 718 for menu in menus)
    assert all("http" not in menu["rawName"].lower() for menu in menus)


def test_low_resolution_with_complete_pairs_is_usable():
    tokens = _sample_tokens("menu_003")
    menus = parse_menu_candidates(tokens)

    result = build_final_result(
        str(ROOT / "images" / "menu_003.jpg"),
        menus,
        raw_lines=tokens,
        enable_gpt_post_process=False,
        enable_gpt_judgment=False,
    )
    quality = result["scan_quality"]

    assert min(quality["image_width"], quality["image_height"]) < 600
    assert quality["status"] == "usable"
    assert quality["reasons"] == []
    assert quality["retake_suggestions"] == []
    assert quality["price_anchor_count"] == 12
    assert quality["pair_coverage"] == 1.0


@pytest.mark.parametrize("sample_name", ["menu_004", "menu_005"])
def test_small_samples_with_good_ocr_structure_are_usable(sample_name):
    tokens = _sample_tokens(sample_name)

    quality = build_scan_quality(
        ROOT / "images" / f"{sample_name}.jpg", parse_menu_candidates(tokens), raw_lines=tokens
    )

    assert min(quality["image_width"], quality["image_height"]) < 600
    assert quality["status"] == "usable"
    assert quality["reasons"] == []


def test_low_resolution_with_poor_ocr_structure_is_not_usable():
    tokens = _sample_tokens("menu_003")[:6]

    quality = build_scan_quality(
        ROOT / "images" / "menu_003.jpg", parse_menu_candidates(tokens), raw_lines=tokens
    )

    assert quality["status"] in {"low_confidence", "needs_retake"}
    assert "이미지 해상도가 낮아 메뉴판 판독이 어렵습니다." in quality["reasons"]
    assert quality["score"] < 70


def test_menu_005_quality_counts_all_option_price_anchors():
    tokens = _sample_tokens("menu_005")
    menus = parse_menu_candidates(tokens)

    result = build_final_result(
        str(ROOT / "images" / "menu_005.jpg"),
        menus,
        raw_lines=tokens,
        enable_gpt_post_process=False,
        enable_gpt_judgment=False,
    )
    quality = result["scan_quality"]

    assert len(menus) == 18
    assert quality["price_anchor_count"] == 21
    assert quality["price_match_count"] == 21
    assert quality["pair_coverage"] == 1.0


_BRIGHT_GLARE_QUALITY = {
    "available": True,
    "score": 30,
    "brightness": 230.0,
    "glare_ratio": 0.8,
    "reasons": [
        "이미지가 너무 밝아 글자가 날아갈 수 있습니다.",
        "강한 빛 반사로 보이는 영역이 많습니다.",
    ],
    "suggestions": ["노출을 낮추거나 빛을 직접 받지 않는 각도로 촬영해주세요."],
}


def test_white_paper_menu_with_good_ocr_structure_is_usable(tmp_path):
    cv2 = pytest.importorskip("cv2")
    image = cv2.imread(str(ROOT / "images" / "menu_005.jpg"))
    upscaled = tmp_path / "menu_005_x3.png"
    cv2.imwrite(str(upscaled), cv2.resize(image, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC))
    tokens = _sample_tokens("menu_005")

    quality = build_scan_quality(upscaled, parse_menu_candidates(tokens), raw_lines=tokens)

    assert quality["image_quality"]["glare_ratio"] >= 0.08
    assert quality["image_quality"]["reasons"]
    assert quality["status"] == "usable"
    assert quality["reasons"] == []


def test_image_warnings_with_good_ocr_structure_stay_observations_only(monkeypatch):
    monkeypatch.setattr("result_builder.analyze_image_quality", lambda path: _BRIGHT_GLARE_QUALITY)
    tokens = _sample_tokens("menu_001")

    quality = build_scan_quality(
        ROOT / "images" / "menu_001.jpg", parse_menu_candidates(tokens), raw_lines=tokens
    )

    assert quality["status"] == "usable"
    assert quality["score"] >= 70
    assert quality["reasons"] == []
    assert quality["retake_suggestions"] == []
    assert quality["image_quality"]["reasons"] == _BRIGHT_GLARE_QUALITY["reasons"]


def test_blurry_image_with_poor_ocr_structure_is_not_usable(monkeypatch):
    blur_reason = "이미지가 흐려 글자 경계가 선명하지 않습니다."
    monkeypatch.setattr(
        "result_builder.analyze_image_quality",
        lambda path: {
            "available": True,
            "score": 40,
            "blur_score": 20.0,
            "reasons": [blur_reason],
            "suggestions": ["카메라 초점을 메뉴판 글자에 맞춘 뒤 흔들리지 않게 다시 촬영해주세요."],
        },
    )
    tokens = _sample_tokens("menu_001")[:6]

    quality = build_scan_quality(
        ROOT / "images" / "menu_001.jpg", parse_menu_candidates(tokens), raw_lines=tokens
    )

    assert quality["status"] in {"low_confidence", "needs_retake"}
    assert blur_reason in quality["reasons"]
    assert quality["retake_suggestions"]


def test_option_prices_prevent_unnecessary_preprocessing_retry():
    tokens = []
    for menu_index, menu_name in enumerate(("감자탕", "전골", "갈비찜")):
        y1 = 100 + (menu_index * 80)
        tokens.extend(
            [
                _clova_token(menu_name, 100, y1, 180, y1 + 24),
                _clova_token("소", 250, y1, 270, y1 + 24),
                _clova_token("20,000", 290, y1, 350, y1 + 24),
                _clova_token("대", 250, y1 + 32, 270, y1 + 56),
                _clova_token("30,000", 290, y1 + 32, 350, y1 + 56),
            ]
        )

    menus = parse_menu_candidates(tokens)

    assert len(menus) == 3
    assert should_retry_with_preprocessing(tokens, menus) is False


def test_clova_client_sends_v2_multipart_request(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOVA_OCR_URL", "https://example.test/ocr")
    monkeypatch.setenv("CLOVA_OCR_SECRET", "test-secret")
    monkeypatch.setenv("CLOVA_OCR_MIN_INTERVAL_SECONDS", "0")
    image_path = tmp_path / "menu.jpg"
    image_path.write_bytes(b"fake-image")
    sample = json.loads(
        (ROOT / "sample_data" / "clova_response_menu_001.json").read_text(
            encoding="utf-8"
        )
    )

    def handler(request: httpx.Request):
        assert request.headers["X-OCR-SECRET"] == "test-secret"
        assert request.headers["content-type"].startswith("multipart/form-data")
        body = request.content.decode("utf-8")
        assert 'name="message"' in body
        assert '"version": "V2"' in body
        assert '"enableTableDetection": false' in body
        assert '"format": "jpg"' in body
        assert 'name="file"; filename="menu.jpg"' in body
        assert "fake-image" in body
        return httpx.Response(200, json=sample)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = ClovaOCRClient(client=http_client)

    tokens = client.analyze_image(str(image_path))

    assert len(tokens) == 54
    assert tokens[0]["source"] == "clova_field"


def test_pipeline_uses_original_when_clova_parse_is_good(monkeypatch):
    tokens = _sample_tokens("menu_001")

    class FakeClient:
        calls_started = 0

        def analyze_image(self, image_path, model_id):
            self.calls_started += 1
            return tokens

        def can_start_call(self, min_remaining_seconds=0):
            return self.calls_started < 2

        def close(self):
            pass

    fake = FakeClient()
    monkeypatch.setattr(ocr_client, "ClovaOCRClient", lambda **kwargs: fake)
    monkeypatch.setattr(
        image_quality,
        "analyze_image_quality",
        lambda path: {"available": False},
    )

    result = ocr_main.analyze_menu_image(
        str(ROOT / "images" / "menu_001.jpg"),
        enable_gpt_post_process=False,
        enable_gpt_judgment=False,
    )

    assert fake.calls_started == 1
    assert result["preprocessingApplied"] is False
    assert result["final"]["scan_session"]["menu_count"] == 17


def test_pipeline_retries_once_and_selects_better_preprocessed_result(
    tmp_path, monkeypatch
):
    good_tokens = _sample_tokens("menu_003")
    source = tmp_path / "source.jpg"
    processed = tmp_path / "processed.jpg"
    source.write_bytes(b"source")
    processed.write_bytes(b"processed")

    class FakeClient:
        calls_started = 0

        def analyze_image(self, image_path, model_id):
            self.calls_started += 1
            return [] if self.calls_started == 1 else good_tokens

        def can_start_call(self, min_remaining_seconds=0):
            return self.calls_started < 2

        def close(self):
            pass

    fake = FakeClient()
    monkeypatch.setattr(ocr_client, "ClovaOCRClient", lambda **kwargs: fake)
    monkeypatch.setattr(
        image_quality,
        "analyze_image_quality",
        lambda path: {"available": False},
    )
    monkeypatch.setattr(
        ocr_main, "prepare_ocr_image", lambda *args, **kwargs: processed
    )

    result = ocr_main.analyze_menu_image(
        str(source),
        enable_gpt_post_process=False,
        enable_gpt_judgment=False,
    )

    assert fake.calls_started == 2
    assert result["preprocessingApplied"] is True
    assert result["final"]["scan_session"]["menu_count"] == 12
    assert not processed.exists()


def test_pipeline_preprocesses_before_first_clova_call_for_clearly_bad_image(
    tmp_path, monkeypatch
):
    tokens = _sample_tokens("menu_003")
    source = tmp_path / "source.jpg"
    processed = tmp_path / "processed.jpg"
    source.write_bytes(b"source")
    processed.write_bytes(b"processed")

    class FakeClient:
        def __init__(self):
            self.calls_started = 0
            self.received_paths = []

        def analyze_image(self, image_path, model_id):
            self.calls_started += 1
            self.received_paths.append(image_path)
            return tokens

        def can_start_call(self, min_remaining_seconds=0):
            return self.calls_started < 2

        def close(self):
            pass

    fake = FakeClient()
    monkeypatch.setattr(ocr_client, "ClovaOCRClient", lambda **kwargs: fake)
    monkeypatch.setattr(
        image_quality,
        "analyze_image_quality",
        lambda path: {
            "available": True,
            "width": 500,
            "height": 900,
            "blur_score": 120,
            "skew_angle": 0,
        },
    )
    monkeypatch.setattr(
        ocr_main, "prepare_ocr_image", lambda *args, **kwargs: processed
    )

    result = ocr_main.analyze_menu_image(
        str(source),
        enable_gpt_post_process=False,
        enable_gpt_judgment=False,
    )

    assert fake.calls_started == 1
    assert fake.received_paths == [str(processed)]
    assert result["preprocessingApplied"] is True
    assert result["final"]["scan_quality"]["selected_ocr_attempt"] == "preprocessed"
    assert not processed.exists()


def test_pipeline_skips_quality_retry_when_deadline_budget_is_insufficient(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source")

    class FakeClient:
        calls_started = 0

        def analyze_image(self, image_path, model_id):
            self.calls_started += 1
            return []

        def can_start_call(self, min_remaining_seconds=0):
            return False

        def close(self):
            pass

    fake = FakeClient()
    monkeypatch.setattr(ocr_client, "ClovaOCRClient", lambda **kwargs: fake)
    monkeypatch.setattr(
        image_quality,
        "analyze_image_quality",
        lambda path: {"available": False},
    )

    result = ocr_main.analyze_menu_image(
        str(source),
        enable_gpt_post_process=False,
        enable_gpt_judgment=False,
    )

    assert fake.calls_started == 1
    assert result["final"]["scan_quality"]["retry_skipped_reason"] == (
        "insufficient_time_or_call_budget"
    )


def test_clova_client_caps_total_outbound_calls_per_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOVA_OCR_URL", "https://example.test/ocr")
    monkeypatch.setenv("CLOVA_OCR_SECRET", "test-secret")
    monkeypatch.setenv("CLOVA_OCR_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("CLOVA_OCR_MAX_ATTEMPTS", "5")
    image_path = tmp_path / "menu.jpg"
    image_path.write_bytes(b"fake-image")
    requests = 0

    def handler(request: httpx.Request):
        nonlocal requests
        requests += 1
        return httpx.Response(503, json={"message": "temporary"})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = ClovaOCRClient(client=http_client, max_calls_per_scan=2)

    with pytest.raises(ocr_client.OCRServiceError):
        client.analyze_image(str(image_path))

    assert requests == 2
    assert client.calls_started == 2


def _sample_tokens(sample_name: str) -> list[dict]:
    path = ROOT / "sample_data" / f"clova_response_{sample_name}.json"
    return extract_clova_fields(json.loads(path.read_text(encoding="utf-8")))
