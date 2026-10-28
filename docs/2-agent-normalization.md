# ② Menu Normalization Agent 스펙

담당: 박다은
상태: 구현 기준안 (Prepare/Judge 2-phase 계약·v1 판정 정책·현재 구현 운영값 확정)
상위 문서: `0-agent-supervisor.md`, `caution-multi-agent-architecture.md`, `caution-db-schema.md`

## 문서 읽는 방법

- **전체 흐름만 볼 사람**: §0-1 → §2 → §4 순서로 읽는다.
- **백엔드 담당자**: §3의 후보 조회 계약과 §1-2 JSON을 구현한다.
- **② 구현 담당자**: §1 전체, §2의 ②-A, §4의 ②-B, §5를 기준으로 구현한다.
- **리뷰 담당자**: §6 오류 정책과 §7 테스트를 확인한다.

## 목차

1. [역할과 2-phase 책임](#0-역할-범위)
2. [완전한 입출력 JSON](#1-입력--출력-계약)
3. [②-A 문자열 정제](#2-prepare--②-a-문자열-정제)
4. [백엔드 후보 조회](#3-prepare와-judge-사이--백엔드-후보-조회)
5. [②-B 후보 선택](#4-judge--②-b-후보-선택)
6. [효율적인 구현 방식](#5-효율적인-구현-방식)
7. [경고와 예외](#6-경고와-예외-처리)
8. [테스트 케이스](#7-테스트-케이스)

---

## 0. 역할 범위

②는 OCR 문자열을 안전하게 정리하고, 백엔드가 조회한 후보 안에서 표준 메뉴 후보를 선택하는 Agent다. 재료를 추론하거나 DB 메뉴를 새로 만들지 않는다.

### 0-1. 2-phase에서의 위치

```text
Prepare
① OCR → ②-A 문자열 정제 → PrepareResponse
                              ↓
                    백엔드 후보 조회
                              ↓
Judge
JudgeRequest → ②-B 후보 선택 → ③~⑧ → JudgeResponse
```

- **②-A Name Cleaning**: DB를 조회하지 않는다. OCR 원문을 정리하고 옵션과 위험 가능 토큰을 보존한다.
- **백엔드 후보 조회**: 정제된 이름으로 `menus`·`menu_aliases`를 조회해 메뉴별 최대 5개 후보와 KnowledgeBundle을 만든다.
- **②-B Candidate Selection**: 백엔드가 준 후보만 비교한다. 후보 밖 `menu_id`를 만들거나 선택하지 않는다. Judge의 `store_id`가 양의 정수 또는 null이어도 동일하게 후보를 선택하며, ②는 이 값을 해석하거나 변경하지 않고 응답 context에 그대로 반환한다.
- **④ DB/Ontology Tool**: 선택된 메뉴의 재료 조회와 base/remain 변형 판정을 담당한다. ②는 `차돌된장찌개`를 임의로 `된장찌개`로 잘라 확정하지 않는다.

### 0-2. 하지 않는 일

| 하지 않음 | 담당 |
|---|---|
| 이미지 OCR, 가격·설명 영역 인식, `is_spicy` 관측 생성 | ① OCR Tool |
| 메뉴 후보 DB 조회와 KnowledgeBundle 구성 | 백엔드 |
| base 메뉴·`remain_token`·변형 재료 확정 | ④ DB/Ontology Tool |
| 재료·확률·위험도 추론 | ④·⑤·⑧ |
| 신규 메뉴나 별칭 저장 명령 생성 | ⑦ DB Update Tool |
| 물리 DB 쓰기 | 백엔드 |

### 0-3. 안전 원칙

- `raw_menu_name`, `item_id`, `source_index`와 입력 순서를 바꾸지 않는다.
- 위험과 연결될 수 있는 단어는 삭제하지 않는다. `치즈`, `차돌`, `해물`, `돼지` 같은 토큰은 전체 메뉴명 또는 `residual_tokens`에 반드시 남는다.
- 규칙과 사전을 먼저 사용하고 LLM은 다국어 또는 모호한 후보에서만 호출한다.
- LLM 실패는 전체 배치 실패로 만들지 않는다.
- 모르면 `ambiguous` 또는 `unmatched`로 남긴다. 편의를 위해 잘못된 메뉴를 확정하지 않는다.
- 사용자의 알레르기·종교 프로필은 정규화에 필요하지 않으므로 ②에 전달하지 않는다.

---

## 1. 입력 / 출력 계약

wire JSON의 최종 기준은 ⓪ 문서 §1-2와 §4-1이다. 이 절은 ② 담당자가 그대로 구현할 수 있도록 같은 계약을 단계별로 풀어쓴다. 모든 모델은 Pydantic `extra="forbid"`인 closed schema이며 공통 `context`·`warnings`·`errors`를 항상 포함한다.

### 1-1. Prepare — `NameCleaningRequest / Response`

#### 요청

```json
{
  "context": {
    "schema_version": "3.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "call_scope": "scan",
    "item_id": null
  },
  "items": [
    {
      "item_id": "scan-123:0",
      "source_index": 0,
      "raw_menu_name": "■ 김치 찌개 2인",
      "description": null,
      "price_text": "8,000원",
      "price_options": [],
      "ocr_confidence": 0.93,
      "locale_hint": "ko",
      "risk_tag_observations": []
    }
  ],
  "warnings": [],
  "errors": []
}
```

#### 응답

```json
{
  "context": {
    "schema_version": "3.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "call_scope": "scan",
    "item_id": null
  },
  "items": [
    {
      "item_id": "scan-123:0",
      "source_index": 0,
      "raw_menu_name": "■ 김치 찌개 2인",
      "normalized_menu_name": "김치찌개",
      "display_name": "김치 찌개",
      "description": null,
      "price_text": "8,000원",
      "price_options": [],
      "ocr_confidence": 0.93,
      "locale_hint": "ko",
      "options": ["2인"],
      "residual_tokens": [],
      "cleaning_status": "cleaned",
      "risk_tag_observations": []
    }
  ],
  "warnings": [],
  "errors": []
}
```

- `cleaning_status`: `cleaned | unchanged | invalid`.
- `cleaned | unchanged`이면 `normalized_menu_name`은 비어 있지 않은 string이다.
- `invalid`이면 `normalized_menu_name: null`이다. Supervisor는 이 항목을 Prepare 외부 응답에서 제외하고 item warning을 보존한다.
- `display_name`은 프론트에서 사용자에게 보여줄 가게 표기다. 불릿·가격과 끝에 독립적으로 붙은 인분·크기 옵션을 제거하되, 원래 메뉴명 표기와 띄어쓰기는 보존한다. 예: `돈까스 → 돈까스`, `부대찌개 대자 → 부대찌개`.
- `normalized_menu_name`은 후보 검색용 이름이다. 장식·가격·비위험 옵션·불필요 공백을 제거하되 재료 의미는 보존한다.
- `options`는 분리한 인분·크기·조리·추가 옵션 전체다.
- `residual_tokens`는 `치즈 추가`처럼 재료 의미가 있어 ④가 다시 확인해야 하는 옵션 토큰만 담는다.
- `description`, `price_text`, `price_options`, `ocr_confidence`, `locale_hint`, `risk_tag_observations`는 해석하거나 수정하지 않고 보존한다.

### 1-2. Judge — `CandidateSelectionRequest / Response`

#### 요청

```json
{
  "context": {
    "schema_version": "3.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "scan",
    "item_id": null
  },
  "items": [
    {
      "item_id": "scan-123:0",
      "source_index": 0,
      "raw_menu_name": "■ 김치 찌개 2인",
      "normalized_menu_name": "김치찌개",
      "display_name": "김치 찌개",
      "description": null,
      "price_text": "8,000원",
      "price_options": [],
      "ocr_confidence": 0.93,
      "locale_hint": "ko",
      "options": ["2인"],
      "residual_tokens": [],
      "risk_tag_observations": [],
      "candidate_status": "ok",
      "match_candidates": [
        {
          "menu_id": 1001,
          "menu_category_id": 11,
          "canonical_name": "김치찌개",
          "score": 1.0,
          "match_type": "canonical"
        }
      ]
    }
  ],
  "warnings": [],
  "errors": []
}
```

#### 응답

```json
{
  "context": {
    "schema_version": "3.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "scan",
    "item_id": null
  },
  "items": [
    {
      "item_id": "scan-123:0",
      "source_index": 0,
      "raw_menu_name": "■ 김치 찌개 2인",
      "normalized_menu_name": "김치찌개",
      "display_name": "김치 찌개",
      "description": null,
      "price_text": "8,000원",
      "price_options": [],
      "ocr_confidence": 0.93,
      "locale_hint": "ko",
      "options": ["2인"],
      "residual_tokens": [],
      "risk_tag_observations": [],
      "candidate_status": "ok",
      "normalization_status": "normalized",
      "selection_method": "deterministic",
      "selection_reason_codes": ["exact_unique"],
      "selected_candidate": {
        "menu_id": 1001,
        "menu_category_id": 11,
        "canonical_name": "김치찌개",
        "score": 1.0,
        "match_type": "canonical"
      },
      "match_candidates": [
        {
          "menu_id": 1001,
          "menu_category_id": 11,
          "canonical_name": "김치찌개",
          "score": 1.0,
          "match_type": "canonical"
        }
      ]
    }
  ],
  "warnings": [],
  "errors": []
}
```

`selected_candidate`는 반드시 입력 `match_candidates` 원소 하나를 값까지 그대로 복사한다. 이름·점수·분류를 ②가 다시 만들지 않는다.

### 1-3. 상태와 enum

| 필드 | 허용값 | 의미 |
|---|---|---|
| `candidate_status` | `ok | not_found | unavailable` | 후보 조회 성공, 데이터 없음, 조회 실패 구분 |
| `normalization_status` | `normalized | ambiguous | unmatched | invalid` | 최종 선택 상태 |
| `selection_method` | `deterministic | llm | none` | 최종 후보를 고른 방법 |
| `match_type` | `canonical | spacing | synonym | ocr_correction | fuzzy` | 백엔드가 후보를 찾은 방법 |

`selection_reason_codes` 허용값은 다음과 같다.

- `exact_unique`: 정확·공백·사전 후보가 유일하고 안정적임
- `score_and_margin`: 점수와 1·2위 차이가 자동 선택 기준을 충족함
- `llm_context_resolved`: 허용 후보 중 하나를 문맥으로 해소함
- `multiple_close_candidates`: 상위 후보 점수가 가까움
- `below_accept_threshold`: 후보가 있지만 자동 선택 기준 미달
- `candidate_not_found`: 정상 조회했으나 후보 없음
- `candidate_unavailable`: 후보 조회 실패
- `invalid_input`: Prepare에서 분석 불가능한 문자열
- `llm_failed`: timeout·검증 실패로 규칙 결과 유지

`normalized`일 때만 `selected_candidate`가 객체이고 method는 `deterministic | llm`이다. 나머지는 `selected_candidate: null`, method는 `none`이다.

---

## 2. Prepare — ②-A 문자열 정제

②-A는 모든 항목을 한 번의 배치로 받아 항목별로 독립 처리한다. 한 항목 실패가 나머지 항목을 막지 않는다.

### 2-1. 처리 순서

1. 원문·식별자와 OCR 부가 필드를 복사해 보존한다.
2. Unicode NFKC, 제어문자 제거, 연속 공백 축소를 적용한다.
3. 선행 불릿·장식 문자와 끝에 독립적으로 붙은 가격 패턴만 제거한다.
4. 인분·크기·조리·추가 옵션을 분류한다.
5. 표시용 이름과 검색용 이름에서 인분·크기 옵션을 제거하고, 재료·조리 의미 토큰은 보존한다.
6. 버전 관리 사전으로 안전한 표기만 통일한다.
7. 다국어·혼합 표기이며 사전으로 해석되지 않을 때만 제한적 LLM 해석을 시도한다.
8. 메뉴가 아닌 문자열을 `invalid`로 분류한다.
9. 원래 `source_index` 순서로 반환한다.

### 2-2. 문자열 처리 규칙

| 유형 | 예시 | `display_name` | `normalized_menu_name` |
|---|---|---|---|
| 장식·가격 | `■ 김치 찌개 8,000원` | `김치 찌개` | `김치찌개` |
| 인분 | `부대찌개 2인` | `부대찌개` | `부대찌개` |
| 크기 | `부대찌개 대자` | `부대찌개` | `부대찌개` |
| 재료 추가 | `라면 치즈 추가` | 원문 표기 보존 | `라면치즈추가` |
| 변형 메뉴 | `차돌 된장찌개` | 원문 표기 보존 | `차돌된장찌개` |
| 조리·매운맛 | `매운 닭갈비` | 원문 표기 보존 | `매운닭갈비` |
| 표기 사전 | `돈까스` | `돈까스` | `돈가스` |

가격 제거는 통화 기호 또는 `원`과 결합되거나 문자열 끝에서 가격 열로 확실히 분리된 숫자에만 적용한다. `오삼불고기`처럼 이름에 포함된 숫자·문자를 가격으로 오인해 지우지 않는다.

검색 키에서는 한글 음절 사이 공백과 일반 구분 공백을 제거한다. 사용자 표시 이름에서는 인분·크기 옵션을 분리한 뒤 원래 메뉴명의 띄어쓰기와 표기를 최대한 보존한다. Latin 문자는 검색 비교용으로만 case-fold하고 `display_name`의 메뉴명 표기는 바꾸지 않는다.

`cleaning_status: unchanged`는 NFKC·제어문자·공백 처리 후의 원문과 `display_name`, `normalized_menu_name`이 모두 같고 옵션 분리와 사전 치환도 없을 때만 사용한다. 그 외 유효한 변경은 `cleaned`다.

v1에서 선행 장식으로 제거하는 문자는 `· • ▶ ▷ ■ □ ◆ ◇ ★ ☆ ▪ ▫ _ - – — | ※ *`다. 끝 가격은 메뉴명과 공백으로 분리된 3~7자리 숫자 또는 쉼표 천 단위 숫자이며, 선택적으로 `₩ ￦ $ € ¥`나 `원`이 붙은 형태만 제거한다. 통화 기호·`원` 없이 붙은 숫자는 반드시 앞 메뉴명과 공백으로 분리돼야 한다.

### 2-3. 옵션과 위험 토큰

| 분류 | 예시 | `options` | `residual_tokens` | 이름에서 제거 |
|---|---|---|---|---|
| 인분·수량 | `1인`, `2인`, `2인분` | 포함 | 미포함 | O (`display_name`, `normalized_menu_name` 모두) |
| 크기 | `소`, `중`, `대`, `소자`, `중자`, `대자`, `곱빼기` | 포함 | 미포함 | O (`display_name`, `normalized_menu_name` 모두) |
| 조리·맵기 | `매운맛`, `덜 맵게`, `튀김` | 포함 | 미포함 | X |
| 재료 추가·교체 | `치즈 추가`, `차돌 추가`, `해물` | 포함 | 재료 토큰 포함 | X |

`소/중/대/소자/중자/대자/곱빼기`는 문자열 끝에서 독립 토큰이고 앞에 실제 메뉴명이 있을 때만 크기 옵션이다. 인분 표기도 문자열 끝에서 독립된 `숫자+인` 또는 `숫자+인분` 형태일 때만 분리한다. `대구탕`의 `대`처럼 메뉴명 내부 문자는 제거하지 않는다.

v1 옵션 사전은 다음으로 고정한다.

- 조리·맵기 관측 문구: `덜 맵게`, `매운맛`, `튀김`
- 재료 추가·교체의 재료 토큰: `치즈`, `차돌`, `고기`, `돼지`, `소고기`, `해물`
- 재료 옵션 형태: 위 토큰 뒤의 `추가 | 교체`, 또는 독립 토큰 `해물`

조리·맵기와 재료 옵션은 `options`에 기록하되 메뉴 이름에서 제거하지 않는다. 재료 옵션의 재료 부분은 `residual_tokens`에도 한 번만 기록한다. 같은 옵션을 여러 규칙이 찾더라도 최초 위치 순서로 중복 없이 반환한다.

②는 `risk_tag_observations`를 새로 만들지 않는다. 매운맛 문구를 분리하더라도 ①이 만든 관측을 그대로 보존한다. 재료 옵션은 ④가 해석할 수 있도록 전체 이름과 `residual_tokens` 양쪽에서 잃지 않는다.

### 2-4. 표기 사전과 다국어

표기 사전은 저장소의 버전 관리 데이터로 시작한다. 항목은 `source_text → normalized_text`와 버전을 가지며, 위험 재료를 삭제하거나 더 일반적인 메뉴로 축약하는 규칙은 등록할 수 없다.

- 허용: `돈까스 → 돈가스`, 검증된 OCR 오탈자 교정
- 금지: `차돌된장찌개 → 된장찌개`, `치즈라면 → 라면`
- 매장별 별칭과 DB 메뉴 ID는 ②-A가 조회하지 않는다.
- DB 기반 별칭은 Prepare 이후 백엔드의 `menu_aliases` 후보 조회에서 처리한다.

현재 저장소 사전은 `ai_normalization/dictionaries/__init__.py`에 두고 애플리케이션 import 시 한 번 immutable 객체로 만든다. 버전은 `2026-10-v1`이다. 사전 내용이 바뀌면 버전도 함께 바꾸고 §7 회귀 테스트를 통과해야 한다.

| 사전 | 현재 v1 항목 |
|---|---|
| 안전한 표기·OCR 교정 | `돈까스 → 돈가스`, `찌게 → 찌개`, `가재미탕 → 가자미탕` |
| 검증된 영문 별칭 | `bibimbap → 비빔밥`, `bulgogi → 불고기`, `kimchi jjigae → 김치찌개`, `kimchi stew → 김치찌개` |
| 섹션 헤더 | `메뉴`, `메뉴판`, `식사류`, `안주류`, `주류`, `음료`, `음료류`, `추천 메뉴`, `대표 메뉴` |

사전 비교 키는 NFKC 적용 후 공백을 제거하고 Latin 문자를 case-fold한 값이다. 따라서 `Kimchi Stew`, `kimchi stew`, `kimchistew`는 같은 키로 비교한다. `display_name`은 사전으로 바꾸지 않는다.

다국어 처리는 다음 순서다.

1. 검증된 다국어 별칭 사전
2. 한글·Latin 혼합 표기의 결정론적 정리
3. 둘로 해석되지 않을 때만 OpenAI Responses API 구조화 출력

②-A의 LLM은 `normalized_menu_name` 후보와 confidence만 반환한다. confidence가 0.90 미만이거나 빈 문자열·과도한 삭제·schema 위반이면 결과를 폐기하고 정제된 원문을 사용한다. LLM은 `menu_id`를 받거나 만들지 않는다.

v1에서 “과도한 삭제”는 다음처럼 검증한다.

- 원문 `display_name`에 남은 모든 한글 연속 토큰이 LLM 결과에도 있어야 한다.
- `residual_tokens`의 모든 토큰이 LLM 결과에도 있어야 한다.
- 하나라도 사라지면 `name_llm_failed` warning을 남기고 결정론적 검색명을 유지한다.

현재 구현은 `OpenAINormalizationResolver`를 `NameResolver` 인터페이스로 주입한다. `OPENAI_API_KEY`가 있으면 Supervisor 서비스 생성 시 Resolver를 한 번 만들고 ②-A·②-B가 공유한다. 키가 없으면 검증된 사전·점수 규칙만 사용하며, 해석되지 않은 외국어·혼합 표기는 정제된 원문 검색명을 유지한다.

기본 모델은 `gpt-5.4-mini`, API는 Responses API, 출력은 Pydantic Structured Outputs다. 기본 timeout은 5초, SDK 재시도는 최대 1회, 최대 출력은 256 token이다. 모델만 바꾸려면 `NORMALIZATION_OPENAI_MODEL`을 설정하며, 일반 실행에는 `OPENAI_API_KEY` 외 설정이 필요 없다. reasoning·temperature는 별도로 지정하지 않고 모델 기본값을 사용한다.

Resolver가 있을 때는 사전에 적중하지 않은 유효 이름에 Latin 문자가 하나 이상 포함된 경우 ②-A LLM 대상이 된다. 순수 Latin 메뉴도 `locale_hint` 값과 관계없이 대상이며, 한글·숫자만 있는 이름은 호출하지 않는다.

### 2-5. invalid 판정

다음은 `invalid`다.

- 정제 후 빈 문자열
- 가격만 남은 문자열
- `식사류`, `주류`, `추천 메뉴`처럼 버전 관리된 섹션 헤더와 정확히 일치
- 전화번호·영업시간처럼 메뉴가 아님을 규칙으로 확정할 수 있는 문자열

전화번호는 번호 묶음을 하이픈 또는 공백으로 구분한 `지역/휴대전화 식별 번호-중간 번호-끝 번호` 형태, 영업시간은 `HH:MM~HH:MM` 형태(`~`, `-`, `–` 구분자)와 앞의 `전화`, `Tel.`, `영업시간` 라벨을 허용해 정확히 일치할 때만 invalid로 만든다. 이 경우 warning code는 `invalid_menu_text`다. 일부 문자열이 비슷하다는 이유만으로 메뉴를 invalid로 만들지 않는다.

한 글자, 낯선 외국어, 처음 보는 메뉴라는 이유만으로 invalid로 만들지 않는다. 이런 값은 유효한 이름으로 Prepare에 남겨 백엔드 조회 후 `unmatched` 경로를 허용한다.

---

## 3. Prepare와 Judge 사이 — 백엔드 후보 조회

백엔드는 `PrepareResponse.prepared_items[]`의 `normalized_menu_name`으로 후보를 조회한다. ②가 DB를 직접 호출하지 않으므로 Prepare가 빠르고, 같은 DB snapshot으로 KnowledgeBundle을 만들 수 있다.

### 3-1. 조회 순서

1. `menus.name_normalized` exact
2. `menu_aliases.alias_normalized` exact
3. 공백 차이·검증된 OCR 교정 사전
4. 위 결과가 없거나 하나로 충분하지 않을 때만 fuzzy 검색

exact·alias 단계에서 유일한 안정 후보를 찾으면 불필요한 fuzzy 검색을 생략한다. 후보는 메뉴 하나당 최대 5개다.

### 3-2. 후보 점수 표준

서로 다른 구현이 같은 기준으로 동작하도록 v1 점수를 고정한다.

| `match_type` | score |
|---|---:|
| `canonical` | 1.00 |
| `spacing` | 0.99 |
| `synonym` | 0.98 |
| `ocr_correction` | 0.95 |
| `fuzzy` | 사용한 문자열 유사도 0~1, 최대 0.94 |

fuzzy 알고리즘은 Unicode 정규화 후 문자 기반 유사도를 0~1로 변환한다. 구현 라이브러리와 버전은 §8에서 고정하되, 결과 score 의미는 위 계약을 지킨다.

후보 배열은 score 내림차순, 동점이면 `menu_id` 오름차순이다. 같은 `menu_id`가 여러 경로에서 나오면 가장 높은 score 하나만 남긴다. `menu_id`는 integer, `menu_category_id`는 integer 또는 null이다.

### 3-3. 조회 상태

- `ok`: 후보가 1개 이상이며 후보마다 같은 `menu_id`의 KnowledgeBundle 자료가 정확히 하나 있음
- `not_found`: 조회는 성공했지만 후보가 없음. `match_candidates: []`
- `unavailable`: DB 장애·timeout으로 조회하지 못함. 빈 후보를 “없는 메뉴”로 해석하지 않음

백엔드는 후보 밖 메뉴 자료를 ②-B에 넣지 않는다. `unavailable`은 Supervisor warning과 함께 전달하며 해당 메뉴의 SAFE를 금지한다.

---

## 4. Judge — ②-B 후보 선택

### 4-1. v1 판정 상수

| 설정 | 값 | 역할 |
|---|---:|---|
| `candidate_floor` | 0.65 | 이보다 낮으면 `unmatched` |
| `accept_threshold` | 0.92 | 자동 선택 최소 점수 |
| `ambiguity_margin` | 0.08 | 1·2위 점수 차이 최소값 |
| `llm_accept_confidence` | 0.90 | 문맥 선택을 인정할 최소 구조화 confidence |

후보가 하나면 margin은 1.0으로 본다. 상수는 코드에 흩어 쓰지 않고 하나의 정책 설정에 둔다. v1 이후 수치를 바꾸려면 고정 검증셋에서 메뉴 오매칭 FN, precision/recall/F2와 경계 사례를 기록한다.

### 4-2. 결정 순서

1. Prepare에서 제외돼야 할 `invalid` 항목이 들어오면 `invalid`, method `none`, reason `invalid_input`으로 방어 처리한다.
2. `candidate_status: unavailable` → `unmatched`, method `none`, reason `candidate_unavailable`.
3. `candidate_status: not_found` → `unmatched`, reason `candidate_not_found`.
4. `candidate_status: ok`인데 후보가 비어 있으면 계약 위반이므로 Supervisor가 `bundle_miss`로 Judge 요청을 거부한다.
5. 1위 score < 0.65 → `unmatched`, reason `below_accept_threshold`.
6. 1위 score ≥ 0.92이고 margin ≥ 0.08 → `normalized`, method `deterministic`.
7. 위 조건을 못 채운 후보가 1~5개이면 `ambiguous` 후보로 두고, §4-3 조건에서만 LLM을 호출한다.
8. LLM 결과가 검증 조건을 모두 통과하면 `normalized`, method `llm`.
9. LLM 미호출·실패·낮은 confidence이면 `ambiguous`, method `none`.

1위가 `canonical | spacing | synonym | ocr_correction`이고 유일하며 자동 선택 기준을 충족하면 reason은 `exact_unique`다. fuzzy를 포함한 일반 점수 선택은 `score_and_margin`이다.

### 4-3. LLM 호출 조건과 제한

다음 조건을 모두 만족할 때만 ②-B LLM을 호출한다.

- 자동 선택 기준은 충족하지 못했지만 1위 score가 `candidate_floor` 이상
- 후보가 1~5개
- `description`, `locale_hint`, 메뉴판 인접 문맥 등 규칙 외의 실제 문맥이 하나 이상 있음
- Supervisor의 남은 시간 예산 안에서 호출 가능

LLM 입력에는 OCR 이름·설명·옵션과 허용 후보의 ID·이름만 보낸다. 사용자 프로필, 재료 정보, 가격, OCR 위험 관측, 후보 score·분류·match type, 다른 가게 정보는 보내지 않는다. 이를 코드에서 강제하기 위해 전체 wire item을 Resolver에 넘기지 않고 다음 최소 view를 만든다.

- ②-A `NameResolutionInput`: `display_name`, `normalized_menu_name`, `locale_hint`, `residual_tokens`
- ②-B `CandidateResolutionInput`: `raw_menu_name`, `normalized_menu_name`, `display_name`, `description`, `locale_hint`, `options`, `residual_tokens`, 후보별 `menu_id`·`canonical_name`

구조화 출력은 다음 내부 모델이다.

```json
{
  "selected_menu_id": 1001,
  "confidence": 0.93,
  "reason_code": "description_supports_candidate"
}
```

결과는 다음을 모두 만족해야 한다.

- `selected_menu_id`가 요청 후보에 존재
- confidence ≥ 0.90
- 선택 근거가 입력 문맥에 있음
- 후보 이름을 새로 만들거나 `residual_tokens`를 삭제하지 않음
- Pydantic closed schema 검증 통과

v1 내부 `reason_code`는 다음 세 값만 허용하고 해당 문맥 필드가 실제로 있어야 한다.

| `reason_code` | 필요한 입력 문맥 |
|---|---|
| `description_supports_candidate` | 비어 있지 않은 `description` |
| `locale_supports_candidate` | null이 아닌 `locale_hint` |
| `option_supports_candidate` | 하나 이상의 `options` 또는 `residual_tokens` |

허용하지 않은 reason code, 문맥 필드가 없는 reason code, 후보 밖 ID는 `invalid_llm_candidate`로 폐기한다. LLM으로 모호성이 해소돼 `normalized`가 되면 `multiple_close_candidates` warning은 최종 응답에 남기지 않는다.

실패하면 결과를 폐기하고 규칙 기반 `ambiguous`를 유지한다. LLM은 후보 점수나 `match_type`을 바꾸지 않는다.

### 4-4. 변형 메뉴 처리

`차돌된장찌개`에 `된장찌개` 후보가 있더라도 ②는 전체 정규화 이름을 `차돌된장찌개`로 유지한다. 후보를 선택했다면 ④가 원문 전체와 후보의 `menu_id`를 함께 보고 DB의 `base_menu_id/remain_token` 또는 longest-match 규칙으로 변형 여부를 판단한다.

②-B의 선택은 “참조할 수 있는 가장 가까운 DB 메뉴”를 고르는 일이지, 추가 토큰이 재료인지 확정하는 일이 아니다. 위험 토큰이 남아 있으면 Supervisor는 ④의 변형 경로를 반드시 실행한다.

### 4-5. 간단한 의사 코드

```python
def select_candidate(item):
    if item.candidate_status == "unavailable":
        return unmatched("candidate_unavailable")
    if item.candidate_status == "not_found" or not item.match_candidates:
        return unmatched("candidate_not_found")

    candidates = validate_sorted_and_unique(item.match_candidates)
    top = candidates[0]
    margin = 1.0 if len(candidates) == 1 else top.score - candidates[1].score

    if top.score < 0.65:
        return unmatched("below_accept_threshold")

    if top.score >= 0.92 and margin >= 0.08:
        reason = (
            "exact_unique"
            if len(candidates) == 1 and top.match_type != "fuzzy"
            else "score_and_margin"
        )
        return normalized(top, method="deterministic", reason=reason)

    llm_result = resolve_with_llm_only_if_context_exists(item, candidates)
    if validated_existing_candidate(llm_result, min_confidence=0.90):
        return normalized(
            candidate_by_id(candidates, llm_result.selected_menu_id),
            method="llm",
            reason="llm_context_resolved",
        )

    return ambiguous(
        candidates,
        reasons=["multiple_close_candidates", "llm_failed"],
    )
```

실제 구현은 LLM을 호출하지 않은 경우 `llm_failed`를 넣지 않는다. 후보가 가까우면 `multiple_close_candidates`, 단순 점수 미달이면 `below_accept_threshold`를 사용한다.

---

## 5. 효율적인 구현 방식

- 문자열 규칙은 순수 함수로 만들고 전체 메뉴를 한 번에 배치 처리한다.
- 사전은 요청마다 파일을 읽지 않고 애플리케이션 시작 시 immutable 구조로 로드한다.
- exact·alias DB 인덱스를 먼저 사용하고 fuzzy는 필요한 이름에만 수행한다.
- LLM은 정확 후보·후보 없음·문맥 없음에는 호출하지 않는다.
- ②-A LLM 캐시는 `cleaned_name + locale_hint + dictionary_version + model_version`, ②-B 캐시는 `item context hash + ordered candidate IDs/scores + policy_version + model_version`을 키로 삼을 수 있다.
- 캐시는 결과 재사용 최적화일 뿐 계약의 정본이 아니다. 실패하면 동일 규칙으로 재계산한다.
- 모든 응답은 `source_index`로 정렬하고 병렬 완료 순서를 출력 순서로 쓰지 않는다.
- LLM temperature는 0, 출력은 Pydantic 구조화 모델, timeout 시 규칙 결과를 사용한다.

### 5-1. 구현 모듈 경계

```text
ai_normalization/
├── __init__.py         # 공개 import 경계
├── README.md           # 현재 구현 범위와 실행 방법
├── contracts.py       # §1 모델
├── cleaning.py        # NFKC·장식·가격·옵션·표기 사전
├── option_rules.py    # 크기/인분/조리/재료 옵션 분류
├── candidate_policy.py# §4의 순수 판정 함수
├── openai_resolver.py # Responses API 구조화 LLM adapter
├── dictionaries/      # `2026-10-v1` 표기·헤더·다국어 사전
└── tests/
```

`contracts.py`의 `NameResolver`와 `CandidateResolver`가 제한적 LLM 경계다. `openai_resolver.py`의 한 lifespan-scoped adapter가 두 Protocol을 구현하며, 요청마다 client를 다시 만들지 않는다. ②의 두 실행 객체는 Supervisor의 비동기 adapter 규칙에 맞게 `async run(request) -> response` 형태로 제공한다.

`CandidatePolicy`의 v1 기본값은 §4-1의 네 값이다. 테스트나 다음 정책 버전 검증에서는 객체를 주입할 수 있지만, 운영에서 값을 바꾸려면 문서·정책 버전·고정 검증 결과를 같은 PR에서 갱신한다.

### 5-2. 현재 구현 범위

| 항목 | 현재 상태 | 소유자/후속 작업 |
|---|---|---|
| ②-A 결정론적 정제·사전·invalid | 구현 | `ai_normalization` |
| ②-B 후보 검증·상태 판정 | 구현 | `ai_normalization` |
| 후보 exact·alias·fuzzy 조회와 score 생성 | AI에서 구현하지 않음 | 백엔드가 §3 계약으로 제공 |
| OpenAI provider·모델·timeout | 구현 | `OpenAINormalizationResolver`, 기본 `gpt-5.4-mini`·5초·재시도 1회 |
| Supervisor Prepare Graph의 ②-A 연결 | 구현 | `ai_supervisor/prepare_graph.py` |
| Supervisor Judge Graph의 ②-B 연결 | 구현 | `ai_supervisor/judge_graph.py` |
| 기존 OCR·Rule Engine 중복 로직 제거 | 미완료 | #165 |

②가 소유하는 v1 코드와 #164의 OpenAI 연결은 구현 완료다. #165의 기존 모듈 정리는 별도 후속 이슈다. API 오류·timeout·schema 위반 시에는 현재의 결정론적 fallback을 유지한다.

Resolver가 없거나 호출이 실패하면 규칙 결과를 유지한다. 후보 생성은 빈 목록을 AI가 만들어 대신하지 않고 백엔드의 `candidate_status`를 그대로 받는다.

기존 `ai_ocr`의 텍스트 구조화와 `ai_ruleengine`의 표기 정리 코드는 동작을 조사해 재사용하되, ①과 ④가 같은 문자열을 다시 정규화하지 않도록 책임을 한곳으로 모은다.

---

## 6. 경고와 예외 처리

모든 warning은 ⓪의 `NodeWarning` 형식을 사용한다.

| code | 상황 | 처리 | `forces_caution` |
|---|---|---|---|
| `empty_menu_name` | 정제 후 빈 문자열 | `invalid`, Prepare 외부 목록에서 제외 | false |
| `price_only` | 가격만 존재 | `invalid`, 제외 | false |
| `section_header` | 확정된 메뉴판 헤더 | `invalid`, 제외 | false |
| `invalid_menu_text` | 전화번호·영업시간과 정확히 일치 | `invalid`, 제외 | false |
| `low_ocr_confidence` | 유효 메뉴의 `ocr_confidence < 0.65` | 이름은 보존하고 계속 진행 | true |
| `name_llm_failed` | ②-A 다국어 해석 실패 | 정제된 원문 fallback | false |
| `candidate_unavailable` | 백엔드 후보 조회 실패 | `unmatched`, unknown 경로 | true |
| `multiple_close_candidates` | 자동 선택할 수 없는 근접 후보 | `ambiguous` | true |
| `invalid_llm_candidate` | LLM이 후보 밖 ID 또는 잘못된 형식 반환 | LLM 결과 폐기 | true |
| `candidate_llm_failed` | ②-B timeout·파싱 실패 | `ambiguous` 유지 | true |

한 항목의 오류는 해당 항목에만 기록한다. 전체 요청을 실패시키는 경우는 공통 context 위변조, schema 위반, 입력 item_id 중복처럼 배치 계약 자체가 깨진 경우뿐이다.

Prepare의 context는 `store_id` 필드 자체를 포함하면 안 되며 명시적 null도 거부한다. Judge의 context는 양의 정수 또는 null인 `store_id` 필드를 반드시 포함한다. 입력 warning/error는 그대로 보존하고, 새 warning은 `(code, node, item_id)`가 같은 경우 한 번만 반환한다.

---

## 7. 테스트 케이스

| # | 입력/상황 | 기대 결과 |
|---|---|---|
| 1 | `■ 김치 찌개 8,000원` | display `김치 찌개`, normalized `김치찌개` |
| 2 | `돈까스` | 사전으로 `돈가스`, 원문·display 유지 |
| 3 | `부대찌개 2인` | display·normalized `부대찌개`, options `["2인"]` |
| 3-a | `부대찌개 대자` | display·normalized `부대찌개`, options `["대자"]` |
| 4 | `라면 치즈 추가` | 치즈가 이름과 residual token에 남음 |
| 5 | `차돌된장찌개` | 전체 이름 유지, ②가 `된장찌개`로 축약하지 않음 |
| 6 | `대구탕` | `대`를 크기 옵션으로 잘못 제거하지 않음 |
| 7 | `식사류` | `invalid`, Prepare 외부 목록에서 제외 |
| 8 | 낯선 한 글자 메뉴 | invalid가 아니라 유효 이름으로 후보 조회 |
| 9 | canonical 1.00 단일 후보 | deterministic `normalized` |
| 10 | fuzzy 0.93 / 0.80 | margin 0.13, deterministic `normalized` |
| 11 | 후보 0.91 / 0.87 | `ambiguous`, 문맥 있을 때만 LLM |
| 12 | 후보 최고 0.60 | `unmatched` |
| 13 | 후보 조회 `not_found` | `unmatched`, ④ unknown 경로 |
| 14 | 후보 조회 `unavailable` | warning + SAFE 금지 |
| 15 | LLM이 후보 밖 ID 반환 | 결과 폐기, `ambiguous` |
| 16 | LLM timeout | 규칙 결과 유지, 다른 메뉴 정상 처리 |
| 17 | 다국어 별칭 사전 적중 | LLM 없이 한국어 검색명 생성 |
| 18 | 메뉴 20개 중 정확 후보 18개 | LLM은 나머지 문맥 있는 모호 항목에만 호출 |
| 19 | 입력 순서와 비동기 완료 순서가 다름 | 출력은 `source_index` 순서 |
| 20 | ②-B 선택 후보 | `selected_candidate`가 입력 후보와 값까지 동일 |
| 21 | 유효 메뉴의 OCR confidence 0.64 | `low_ocr_confidence`, `forces_caution: true` |
| 22 | `02-123-4567`, `영업시간 09:00~21:00` | `invalid_menu_text`, `invalid` |
| 23 | Prepare context에 `store_id: null` | schema 거부 |
| 24 | Judge context에서 `store_id` 누락 | schema 거부 |
| 25 | LLM reason과 실제 문맥 필드 불일치 | 결과 폐기, `invalid_llm_candidate` |

---

## 8. 확정 상태와 운영값

### 확정

- ②-A는 DB를 조회하지 않고 문자열만 정제한다.
- 후보 조회와 KnowledgeBundle 구성은 백엔드가 담당한다.
- ②-B는 최대 5개 허용 후보 안에서만 선택한다.
- 위험 가능 토큰은 삭제하지 않고 전체 이름 또는 `residual_tokens`에 남긴다.
- exact·alias·점수 기준을 먼저 쓰고 LLM은 문맥이 있는 모호한 경우에만 쓴다.
- v1 판정값은 `candidate_floor=0.65`, `accept_threshold=0.92`, `ambiguity_margin=0.08`, `llm_accept_confidence=0.90`이다.
- 후보 조회 실패와 모호성은 SAFE 금지 신호로 보존한다.
- schema 3.0의 완전한 wire JSON은 ⓪ §4-1과 이 문서 §1을 함께 갱신한다.
- Python 패키지 경로는 `ai_normalization/`이다.
- 사전 경로와 버전은 `ai_normalization/dictionaries/__init__.py`, `2026-10-v1`이다.
- item 단위 저신뢰 기준은 `ocr_confidence < 0.65`이며 유효 메뉴에만 `low_ocr_confidence`를 만든다.
- LLM 내부 reason code는 §4-3의 세 값이다.
- warning은 `(code, node, item_id)`로 중복 제거한다.

### 외부 의존과 후속 구현

다음은 현재 코드에서 임의로 선택하지 않는다. 값이 정해지면 이 절을 먼저 수정한 뒤 같은 PR에서 구현·테스트를 추가한다.

- 후보 조회의 fuzzy 문자열 유사도 라이브러리와 버전은 후보 조회를 소유한 백엔드가 고정하고, AI는 0~1 score와 최대 0.94 계약만 검증한다.
- 운영 배포는 `OPENAI_API_KEY`를 secret으로 주입한다. 모델 변경은 `NORMALIZATION_OPENAI_MODEL`로만 수행하고 문서·회귀 테스트를 같은 PR에서 갱신한다.
- 고정 검증셋 위치와 정책 수치 변경 승인 절차

---

## 9. 구현 추적

이 문서는 ②의 실행 계약과 정책만 유지한다. 일정·담당자·완료 체크는 중복을 막기 위해 GitHub Issues와 Projects Iteration에서 관리한다.
