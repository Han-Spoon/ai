# Menu OCR AI

한국 로컬 식당 메뉴판 이미지를 Azure Document Intelligence로 OCR 처리한 뒤, 메뉴명과 가격 후보를 표준 JSON으로 구조화하는 Python MVP 모듈입니다.

이 파트는 OCR과 메뉴판 구조화만 담당합니다. GPT, Azure AI Search, 별도 서버 실행은 사용하지 않습니다.

## 설치 방법

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 폴더 구조와 역할

```text
menu-ocr-ai/
  images/                 # OCR을 실행할 메뉴판 이미지 보관 폴더
  sample_data/            # Azure 호출 없이 parser를 테스트하는 mock 데이터
  outputs/
    raw/                  # Azure OCR에서 추출한 원본 line JSON 저장
    final/                # 백엔드 팀에 전달할 최종 메뉴 구조화 JSON 저장
    model_compare/        # 모델별 OCR 비교 결과 저장
  ocr_client.py           # Azure Document Intelligence 호출과 raw line 추출
  normalizer.py           # 가격 보정, 메뉴명 정규화, 메뉴명 후보 필터링
  parser.py               # OCR line을 행 단위로 묶고 메뉴명/가격 후보 생성
  main.py                 # 실제 이미지 1장을 OCR 후 최종 JSON 생성
  compare_models.py       # 같은 이미지로 Azure OCR 모델 3종 비교
  test_parser_with_mock.py # mock OCR line JSON으로 parser만 로컬 테스트
  requirements.txt        # Python 패키지 목록
  .env.example            # Azure endpoint/key 작성 예시
  README.md               # 실행 방법과 협업 전달 문서
```

## 협업 흐름

이 모듈은 첫 번째 단계인 OCR/메뉴판 구조화만 담당합니다.

1. 메뉴판 이미지를 `images/`에 넣습니다.
2. `main.py`로 Azure OCR을 실행합니다.
3. OCR raw line은 `outputs/raw/`에 저장됩니다.
4. 메뉴명/가격 후보가 포함된 최종 JSON은 `outputs/final/`에 저장됩니다.
5. 백엔드 팀은 `outputs/final/이미지명_result.json` 파일을 받아 메뉴 매칭과 식단 위험 판단에 사용합니다.

## .env 작성 방법

프로젝트 루트에 `.env` 파일을 만들고 아래 값을 입력합니다.

```bash
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=https://your-resource-name.cognitiveservices.azure.com/
AZURE_DOCUMENT_INTELLIGENCE_KEY=your_azure_document_intelligence_key
```

Azure Portal에서 Document Intelligence 리소스로 이동한 뒤 `Resource Management > Keys and Endpoint` 메뉴에서 endpoint와 key를 확인할 수 있습니다.

`.env` 파일에는 실제 key가 들어가므로 Git에 올리면 안 됩니다. 이 프로젝트의 `.gitignore`에는 `.env`가 포함되어 있습니다.

## Azure 비용 아끼는 방법

Azure OCR 호출은 비용이 발생하므로 아래 순서로 테스트하는 것을 권장합니다.

1. parser 수정 후에는 먼저 mock 테스트만 실행합니다.

```bash
python test_parser_with_mock.py --input sample_data/mock_ocr_lines.json
```

2. 실제 Azure 호출은 대표 이미지 몇 장으로만 실행합니다.

```bash
python main.py --image images/menu_001.jpg --model prebuilt-layout
```

3. 모델 비교는 비용이 3번 발생하므로 꼭 필요할 때만 실행합니다.

```bash
python compare_models.py --image images/menu_001.jpg
```

4. 한 번 생성된 `outputs/raw/` 결과는 보관해두고, parser 실험에는 mock 또는 저장된 raw JSON을 재사용합니다.

## 이미지 OCR 실행

기본 모델은 `prebuilt-layout`입니다.

```bash
python main.py --image images/menu_001.jpg --model prebuilt-layout
```

비용을 아끼기 위해 기본 실행은 `prebuilt-layout`을 먼저 1번만 호출합니다. `prebuilt-layout` 호출이 실패하거나 OCR line이 0개일 때만 `prebuilt-read`로 한 번 더 재시도합니다. 메뉴 후보가 0개라는 이유만으로는 자동 재호출하지 않습니다.

재시도 없이 layout만 실행하고 싶으면 아래처럼 실행합니다.

```bash
python main.py --image images/menu_001.jpg --model prebuilt-layout --no-fallback-read
```

저장 파일:

- OCR raw line: `outputs/raw/menu_001_prebuilt-layout_raw.json`
- 최종 JSON: `outputs/final/menu_001_result.json`

## 모델 비교 실행

같은 이미지에 대해 `prebuilt-read`, `prebuilt-layout`, `prebuilt-document`를 실행하고 line 개수와 추출 텍스트를 출력합니다.

```bash
python compare_models.py --image images/menu_001.jpg
```

저장 파일:

- `outputs/model_compare/menu_001/prebuilt-read.json`
- `outputs/model_compare/menu_001/prebuilt-layout.json`
- `outputs/model_compare/menu_001/prebuilt-document.json`

참고: Azure Document Intelligence v4에서는 general document 기능이 layout 모델 쪽으로 통합되어 있습니다. 코드에서는 `prebuilt-document` 테스트 시 호환을 위해 API version `2023-07-31`을 사용합니다. 리소스/지역/계정 설정에 따라 `prebuilt-document`가 지원되지 않으면 `.error.json` 파일에 오류를 저장합니다.

## Mock Parser 테스트

Azure 호출 비용 없이 parser만 테스트할 수 있습니다.

```bash
python test_parser_with_mock.py --input sample_data/mock_ocr_lines.json
```

저장 파일:

- `outputs/final/mock_ocr_lines_result.json`

## Spring Boot 백엔드 전달 형식

백엔드 팀에는 `outputs/final/이미지명_result.json` 파일을 전달합니다.

```json
{
  "image": "menu_001.jpg",
  "modelId": "prebuilt-layout",
  "menus": [
    {
      "rawName": "수육국밥",
      "normalizedCandidate": "수육국밥",
      "price": 10000,
      "confidence": 0.88,
      "source": {
        "page": 1,
        "lineTexts": ["수육국밥", "10,000"],
        "bbox": [100.0, 120.0, 580.0, 140.0]
      }
    }
  ],
  "rawLines": [
    {
      "text": "수육국밥",
      "page": 1,
      "x1": 100.0,
      "y1": 120.0,
      "x2": 220.0,
      "y2": 140.0,
      "confidence": 1.0
    }
  ]
}
```

`menus`는 후속 메뉴 매칭과 식단 위험 판단에 사용하고, `rawLines`는 OCR 디버깅과 재분석을 위해 함께 보관합니다.
