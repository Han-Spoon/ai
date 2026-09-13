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
from clova_layout import count_price_anchors, extract_clova_fields  # noqa: E402
from ocr_client import ClovaOCRClient, OCRConfigError  # noqa: E402
from normalizer import normalize_menu_name, split_menu_name_and_origin  # noqa: E402
from parser import build_menu_item, parse_menu_candidates  # noqa: E402
from result_builder import build_final_result, build_menu_analysis  # noqa: E402

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
    assert response["menu_name_ko"] == "소고기국밥"
    assert response["origin_text"] == "호주산"


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


def test_low_resolution_with_complete_pairs_is_reviewable_not_hard_failure():
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

    assert quality["status"] == "low_confidence"
    assert quality["price_anchor_count"] == 12
    assert quality["pair_coverage"] == 1.0


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
