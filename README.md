# Menu OCR AI

한국 로컬 식당 메뉴판 이미지를 Azure Document Intelligence로 OCR 처리한 뒤, 백엔드가 저장하기 쉬운 JSON으로 구조화하는 Python MVP 모듈입니다.

이 파트는 OCR, 메뉴명/가격 추출, 메뉴명 후처리, ERD 기반 JSON 생성까지만 담당합니다. 번역, 위험도 판단, 매움 여부 판단, DB 저장은 후속 파트와 백엔드가 담당합니다.

## 폴더 구조

```text
AI_industry_lecture/
  ai_ocr/                    # OCR 실행 코드
    main.py                  # 실제 이미지 OCR 실행
    ocr_client.py            # Azure Document Intelligence 호출
    parser.py                # OCR line에서 메뉴 후보 추출
    normalizer.py            # 가격/메뉴명 정규화와 OCR 잡문자 제거
    menu_dictionary.py       # 메뉴명 사전
    preprocess_image.py      # 이미지 전처리
    reprocess_raw.py         # 저장된 raw OCR 재처리
    compare_models.py        # Azure OCR 모델 비교
    result_builder.py        # ERD 기반 최종 JSON 생성
    test_parser_with_mock.py # mock 데이터 테스트
  images/                    # OCR 테스트 이미지
  sample_data/               # Azure 호출 없는 mock 데이터
  outputs/
    raw/                     # Azure OCR 원본 line JSON
    final/                   # 백엔드 전달용 최종 JSON
    model_compare/           # 모델 비교 결과
  requirements.txt
  README.md
```

## 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## .env 설정

프로젝트 루트에 `.env` 파일이 없으면 새로 만들고 아래 값을 입력합니다. 이미 있으면 그대로 사용하면 됩니다.

```bash
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=https://your-resource-name.cognitiveservices.azure.com/
AZURE_DOCUMENT_INTELLIGENCE_KEY=your_azure_document_intelligence_key

AZURE_OPENAI_ENDPOINT=https://your-resource-name.openai.azure.com/
AZURE_OPENAI_KEY=your_azure_openai_key
AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini
AZURE_OPENAI_API_VERSION=2024-10-01-preview
```

`.env`에는 실제 key가 들어가므로 Git에 올리면 안 됩니다.

`AZURE_OPENAI_DEPLOYMENT`에는 Azure OpenAI에서 만든 GPT-4o-mini 배포 이름을 넣습니다. 배포 이름을 모델명과 다르게 만들었다면 실제 배포 이름으로 바꿔야 합니다. `AZURE_OPENAI_KEY` 대신 `AZURE_OPENAI_API_KEY`를 써도 됩니다.

## 실제 이미지 실행

기본 실행:

```bash
python3 ai_ocr/main.py --image images/menu_001.jpg --model prebuilt-layout
```

기본 실행은 최종 JSON 생성 직전에 GPT-4o-mini로 메뉴명/설명 후처리를 시도하고, OCR 품질 판단 결과를 `gpt_quality_judgment`에 추가합니다. Azure OpenAI 설정이나 패키지가 없으면 경고만 출력하고 기존 룰 기반 결과로 계속 진행합니다.

다른 이미지를 실행하려면 `images/` 폴더에 이미지를 넣고 `--image`만 바꿉니다.

```bash
python3 ai_ocr/main.py --image images/내이미지파일.jpg --model prebuilt-layout
```

생성 파일:

```text
outputs/raw/이미지명_prebuilt-layout_raw.json
outputs/final/이미지명_result.json
```

원본 이미지를 전처리 없이 바로 Azure에 보내려면:

```bash
python3 ai_ocr/main.py --image images/menu_001.jpg --model prebuilt-layout --no-preprocess
```

GPT 후처리 또는 품질 판단만 끄려면:

```bash
python3 ai_ocr/main.py --image images/menu_001.jpg --no-gpt-post-process
python3 ai_ocr/main.py --image images/menu_001.jpg --no-gpt-judgment
```

기본 전처리는 메뉴판 외곽이 잡히면 자동 원근 보정을 하고, 텍스트 방향을 기준으로 기울기를 보정합니다. 기울어진 촬영본은 보통 기본 실행만으로 보정된 이미지를 `images/preprocessed/`에 저장한 뒤 OCR에 사용합니다.

```bash
python3 ai_ocr/main.py --image images/tilted_menu.jpg --model prebuilt-layout
```

전처리만 따로 확인하려면:

```bash
python3 ai_ocr/preprocess_image.py --image images/tilted_menu.jpg --output images/preprocessed/tilted_menu_fixed.jpg
```

원근 보정이나 기울기 보정이 오히려 결과를 망치면 개별 단계만 끌 수 있습니다.

```bash
python3 ai_ocr/main.py --image images/menu_001.jpg --no-perspective
python3 ai_ocr/main.py --image images/menu_001.jpg --no-deskew
python3 ai_ocr/main.py --image images/menu_001.jpg --max-deskew-angle 25
```

## 백엔드 연동용 실행

백엔드가 이미지를 저장한 뒤 OCR을 호출할 때는 저장 정보를 함께 넘길 수 있습니다.

```bash
python3 ai_ocr/main.py \
  --image uploads/menu_003.jpg \
  --model prebuilt-layout \
  --source camera \
  --storage-key scans/menu_003.jpg \
  --image-url https://example.com/scans/menu_003.jpg \
  --print-json
```

연동 옵션:

| 옵션 | JSON 필드 | 설명 |
| --- | --- | --- |
| `--source` | `menu_image.source` | `camera` 또는 `upload` |
| `--storage-key` | `menu_image.storage_key` | 서버/S3 등에 저장된 이미지 키 |
| `--image-url` | `menu_image.image_url` | 백엔드가 접근 가능한 이미지 URL |
| `--print-json` | stdout | 최종 JSON을 터미널에도 출력 |

## Azure 비용 없이 테스트

parser만 테스트할 때는 mock 데이터를 사용합니다. Azure 비용이 발생하지 않습니다.

```bash
python3 ai_ocr/test_parser_with_mock.py --input sample_data/mock_ocr_lines.json
```

이미 저장된 raw OCR 결과를 다시 후처리하려면:

```bash
python3 ai_ocr/reprocess_raw.py --input outputs/raw/menu_001_prebuilt-layout_raw.json
```

모델 비교가 필요할 때만 아래 명령을 사용합니다. 여러 Azure 모델을 호출하므로 비용이 더 발생할 수 있습니다.

```bash
python3 ai_ocr/compare_models.py --image images/menu_001.jpg
```

## 처리 흐름

1. 메뉴판 이미지를 입력합니다.
2. 필요하면 로컬 전처리 이미지를 만듭니다.
3. Azure Document Intelligence로 OCR line을 추출합니다.
4. OCR line에서 메뉴명과 가격 후보를 찾습니다.
5. 메뉴명 앞뒤의 OCR 잡문자와 용량 표기를 제거합니다.
   예: `■김치찌개–` -> `김치찌개`, `■두루치기200g出` -> `두루치기`
6. `scan_session`, `menu_image`, `menu_analyses` 구조의 최종 JSON을 생성합니다.

## 출력 JSON 형식

최종 출력은 `outputs/final/이미지명_result.json`입니다.

```json
{
  "scan_session": {
    "title": "menu_001.jpg",
    "menu_count": 1,
    "risky_menu_count": null,
    "scan_status": "completed",
    "scanned_at": "2026-05-28T12:00:00Z"
  },
  "menu_image": {
    "source": "upload",
    "storage_key": null,
    "image_url": null,
    "mime_type": "image/jpeg",
    "file_size": 123456
  },
  "scan_quality": {
    "status": "usable",
    "score": 100,
    "raw_line_count": 12,
    "price_match_count": 1,
    "price_match_ratio": 1.0,
    "image_width": 1280,
    "image_height": 960,
    "reasons": []
  },
  "menu_analyses": [
    {
      "menu_name_ko": "수육국밥",
      "menu_name_en": null,
      "description_ko": "",
      "description_en": null,
      "price_text": "10,000",
      "risk_level": null,
      "is_spicy": null,
      "image_url": null,
      "display_order": 1
    }
  ]
}
```

ERD 매핑:

| JSON 키 | 저장 대상 |
| --- | --- |
| `scan_session` | `scan_sessions` |
| `menu_image` | `menu_images` |
| `scan_quality` | 재촬영/검수 판단용 OCR 품질 메타데이터 |
| `menu_analyses[]` | `menu_analyses` |

재촬영 판단 기준:

- `scan_quality.status == "needs_retake"`: 재촬영 요청
- `scan_quality.status == "low_confidence"`: 결과는 보여주되 사용자가 확인하도록 안내
- `scan_quality.status == "usable"`: 정상 사용 가능

초기 기준은 메뉴 후보 0개, OCR line 3개 미만, 낮은 해상도는 재촬영으로 보고, 메뉴 후보가 3개 미만이거나 가격 매칭 비율이 50% 미만이면 낮은 신뢰도로 표시합니다.

후속 파트가 채우는 필드:

- `menu_name_en`
- `description_en`
- `risk_level`
- `is_spicy`
- `menu_analyses.image_url`
- `scan_session.risky_menu_count`

## 프론트/백엔드 연결 방향

권장 흐름:

1. 프론트가 카메라 촬영 또는 이미지 업로드로 메뉴판 이미지를 백엔드에 보냅니다.
2. 백엔드는 이미지를 서버나 외부 저장소에 저장합니다.
3. 백엔드는 저장된 이미지 경로로 `ai_ocr/main.py`를 호출합니다.
4. OCR 결과 JSON을 읽어 `scan_sessions`, `menu_images`, `menu_analyses`에 저장합니다.
5. 후속 분석 파트가 저장된 메뉴 분석 데이터를 기준으로 번역/위험도/매움 여부를 업데이트합니다.

초기에는 파일 기반 연결이 가장 단순합니다. 나중에는 `ai_ocr/result_builder.py`가 만드는 JSON 구조를 유지하면서 백엔드 API 응답으로 바로 넘기도록 바꾸면 됩니다.

## 입력 이미지 권장 기준

- 메뉴판이 이미지 대부분을 차지하게 촬영합니다.
- 글자가 흐리지 않게 초점을 맞춥니다.
- 빛 반사, 그림자, 가림이 메뉴명과 가격을 덮지 않게 합니다.
- 한 이미지에 여러 메뉴판이나 포스터가 섞이지 않게 합니다.
- JPG, PNG, WEBP 이미지를 권장합니다.
- 가능하면 1280px 이상 해상도를 사용합니다.
