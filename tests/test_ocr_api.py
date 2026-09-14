import importlib
import io
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

app_module = importlib.import_module("app")


def test_health_rejects_placeholder_clova_configuration(monkeypatch):
    monkeypatch.setenv("CLOVA_OCR_URL", "PLACEHOLDER")
    monkeypatch.setenv("CLOVA_OCR_SECRET", "PLACEHOLDER")

    response = TestClient(app_module.app).get("/health")

    assert response.status_code == 503
    assert response.json() == {"detail": "OCR runtime configuration is invalid."}


def test_health_accepts_valid_clova_configuration(monkeypatch):
    monkeypatch.setenv("CLOVA_OCR_URL", "https://example.test/ocr")
    monkeypatch.setenv("CLOVA_OCR_SECRET", "test-secret")

    response = TestClient(app_module.app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_result_endpoint_degrades_unknown_menu_to_caution_without_openai_key(
    monkeypatch,
):
    from ai_result.gpt.gpt_client import _get_client

    monkeypatch.setenv("OPENAI_API_KEY", "PLACEHOLDER")
    _get_client.cache_clear()
    try:
        response = TestClient(app_module.app).post(
            "/v1/result",
            json={
                "menu_analyses": [
                    {
                        "menu_name_ko": "DB미등록메뉴",
                        "risk_level": "caution",
                        "hit_tags": [],
                        "triggered_flags": [],
                        "forbidden_tags": ["is_pork"],
                        "need_gpt": True,
                        "escalation_case": ["unknown_menu"],
                    }
                ]
            },
        )
    finally:
        _get_client.cache_clear()

    assert response.status_code == 200
    menu = response.json()["menu_analyses"][0]
    assert menu["risk_level"] == "caution"
    assert menu["hits"] == []
    assert menu["owner_card"]["flag"] == "unknown_menu"


def test_runtime_ocr_pipeline_accepts_request_budget(tmp_path):
    with pytest.raises(FileNotFoundError):
        app_module._ocr_main.analyze_menu_image(
            str(tmp_path / "missing.jpg"), total_budget_seconds=14
        )


def test_v1_ocr_uses_latency_fast_path_without_gpt(tmp_path, monkeypatch):
    image_path = tmp_path / "menu.jpg"
    image_path.write_bytes(b"temporary")
    captured = {}

    monkeypatch.setattr(
        app_module,
        "_acquire_image",
        lambda request: app_module.DownloadedImage(
            path=str(image_path),
            mime_type="image/jpeg",
            file_size=9,
            source="presigned_url",
        ),
    )

    def fake_analyze_menu_image(path, **kwargs):
        captured.update(kwargs)
        return {
            "final": {
                "scan_quality": {},
                "scan_session": {"scan_status": "completed"},
                "menu_analyses": [],
            }
        }

    monkeypatch.setattr(app_module, "analyze_menu_image", fake_analyze_menu_image)
    monkeypatch.delenv("OCR_ENABLE_GPT_POST_PROCESS", raising=False)
    monkeypatch.delenv("OCR_ENABLE_GPT_JUDGMENT", raising=False)
    # 개발자의 개인 .env 값과 무관하게 운영 요청 예산을 검증한다.
    monkeypatch.setenv("OCR_REQUEST_BUDGET_SECONDS", "16")
    monkeypatch.setenv("OCR_TOTAL_BUDGET_SECONDS", "14")

    response = TestClient(app_module.app).post(
        "/v1/ocr",
        json={
            "store_id": 42,
            "source": "upload",
            "storage_key": "scans/00000000-0000-0000-0000-000000000000/menu.jpg",
            "image_url": "https://example.test/menu.jpg",
        },
    )

    assert response.status_code == 200
    assert captured["enable_gpt_post_process"] is False
    assert captured["enable_gpt_judgment"] is False
    assert 0 < captured["total_budget_seconds"] <= 14
    assert response.json()["scan_session"]["store_id"] == 42
    assert response.json()["scan_quality"]["image_fetch_source"] == "presigned_url"
    assert not image_path.exists()


def test_v1_ocr_rejects_non_positive_store_id():
    response = TestClient(app_module.app).post(
        "/v1/ocr",
        json={
            "store_id": 0,
            "storage_key": "scans/00000000-0000-0000-0000-000000000000/menu.jpg",
        },
    )

    assert response.status_code == 422


def test_ruleengine_preserves_matching_store_context(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "analyze_all",
        lambda ocr_result, profile: dict(ocr_result),
    )

    response = TestClient(app_module.app).post(
        "/v1/ruleengine",
        json={
            "store_id": 42,
            "profile": {},
            "ocr_result": {
                "scan_session": {"store_id": 42},
                "menu_analyses": [],
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["scan_session"]["store_id"] == 42


def test_ruleengine_rejects_store_context_mismatch():
    response = TestClient(app_module.app).post(
        "/v1/ruleengine",
        json={
            "store_id": 42,
            "profile": {},
            "ocr_result": {
                "scan_session": {"store_id": 99},
                "menu_analyses": [],
            },
        },
    )

    assert response.status_code == 422


def test_result_preserves_valid_store_context(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "build_final_results_from_judged",
        lambda judged_result: dict(judged_result),
    )

    response = TestClient(app_module.app).post(
        "/v1/result",
        json={
            "scan_session": {"store_id": 42},
            "menu_analyses": [],
        },
    )

    assert response.status_code == 200
    assert response.json()["scan_session"]["store_id"] == 42


def test_url_fallback_can_require_a_fail_closed_host_allowlist(monkeypatch):
    monkeypatch.setenv("OCR_REQUIRE_IMAGE_HOST_ALLOWLIST", "true")
    monkeypatch.delenv("OCR_ALLOWED_IMAGE_HOSTS", raising=False)

    with pytest.raises(HTTPException) as raised:
        app_module._download_url_image("https://example.test/menu.jpg")

    assert raised.value.status_code == 500


def test_s3_fetch_rejects_invalid_storage_key_before_aws_call(monkeypatch):
    monkeypatch.setenv("OCR_S3_FETCH_ENABLED", "true")
    monkeypatch.setenv("OCR_S3_BUCKET", "private-menu-images")
    request = app_module.OcrRequest(
        storage_key="../../metadata",
        image_url="https://example.test/menu.jpg",
    )

    with pytest.raises(HTTPException) as raised:
        app_module._acquire_image(request)

    assert raised.value.status_code == 400


def test_downloaded_webp_is_converted_to_clova_supported_jpeg(tmp_path):
    from PIL import Image

    image_path = tmp_path / "menu.img"
    Image.new("RGB", (32, 32), "white").save(image_path, format="WEBP")

    downloaded = app_module._validate_and_normalize_image(
        str(image_path), image_path.stat().st_size, source="test"
    )

    try:
        assert Path(downloaded.path).suffix == ".jpg"
        assert downloaded.mime_type == "image/jpeg"
        with Image.open(downloaded.path) as converted:
            assert converted.format == "JPEG"
    finally:
        app_module._safe_unlink(downloaded.path)


def test_s3_iam_fetch_streams_the_validated_object_version(tmp_path, monkeypatch):
    import boto3
    from PIL import Image

    source = tmp_path / "source.jpg"
    Image.new("RGB", (32, 32), "white").save(source, format="JPEG")
    image_bytes = source.read_bytes()
    captured = {}

    class FakeS3:
        def get_object(self, **request):
            captured.update(request)
            return {
                "ContentLength": len(image_bytes),
                "ETag": '"etag-1"',
                "Body": io.BytesIO(image_bytes),
            }

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: FakeS3())
    monkeypatch.setenv("OCR_S3_BUCKET", "private-menu-images")

    downloaded = app_module._download_s3_image(
        "scans/00000000-0000-0000-0000-000000000000/menu.jpg",
        version_id="version-1",
        expected_etag='"etag-1"',
    )

    try:
        assert captured == {
            "Bucket": "private-menu-images",
            "Key": "scans/00000000-0000-0000-0000-000000000000/menu.jpg",
            "VersionId": "version-1",
        }
        assert downloaded.source == "s3_iam"
        assert downloaded.mime_type == "image/jpeg"
    finally:
        app_module._safe_unlink(downloaded.path)


def test_s3_iam_fetch_fails_closed_when_etag_is_missing(tmp_path, monkeypatch):
    import boto3

    class FakeS3:
        def get_object(self, **request):
            return {
                "ContentLength": 3,
                "Body": io.BytesIO(b"bad"),
            }

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: FakeS3())
    monkeypatch.setenv("OCR_S3_BUCKET", "private-menu-images")

    with pytest.raises(HTTPException) as raised:
        app_module._download_s3_image(
            "scans/00000000-0000-0000-0000-000000000000/menu.jpg",
            version_id="version-1",
            expected_etag='"etag-1"',
        )

    assert raised.value.status_code == 409


def test_exif_orientation_is_applied_before_ocr(tmp_path):
    from PIL import Image

    image_path = tmp_path / "rotated.img"
    image = Image.new("RGB", (20, 10), "white")
    exif = image.getexif()
    exif[274] = 6
    image.save(image_path, format="JPEG", exif=exif)

    downloaded = app_module._validate_and_normalize_image(
        str(image_path), image_path.stat().st_size, source="test"
    )

    try:
        with Image.open(downloaded.path) as normalized:
            assert normalized.size == (10, 20)
            assert normalized.getexif().get(274, 1) == 1
    finally:
        app_module._safe_unlink(downloaded.path)
