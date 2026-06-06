# AI Result

`ai_result`는 `ai_ruleengine`이 만든 메뉴별 판정 결과를 받아 화면 표시용 최종 JSON 템플릿으로 변환하는 모듈입니다.

이 모듈은 OCR을 직접 처리하지 않습니다. 입력은 반드시 룰엔진을 통과한 메뉴 분석 결과여야 합니다.

## 역할

`ai_result`의 책임은 아래 세 가지입니다.

1. 룰엔진 결과의 `risk_level`, `escalation_case`를 보고 케이스를 라우팅합니다.
2. `danger`, `safe`, `caution`, `unknown_menu`, `unknown_remain` 케이스를 최종 템플릿으로 조립합니다.
3. 화면에 표시할 `message`와 사장님 확인용 `owner_card`를 생성합니다.

일반 `caution` 케이스는 GPT를 호출하지 않습니다. `hidden_rules`와 `forbidden_tags`의 교집합으로 최종 `hits`를 확정합니다.

GPT는 아래 두 케이스에서만 사용합니다.

- `unknown_menu`: DB에 없는 메뉴
- `unknown_remain`: 베이스 메뉴는 잡혔지만 variant 토큰이 미인식된 메뉴

## 폴더 구조

```text
ai_result/
  main.py                         # judged JSON 입력 → 메뉴별 최종 템플릿 생성

  core/
    case_router.py                # risk_level + escalation_case 기준 handler 분기
    template_builder.py           # FinalOutput / owner_card 조립
    message_builder.py            # ko/en/ar 안내 문구 생성

  handlers/
    danger_handler.py             # danger 케이스 처리
    caution_handler.py            # ambiguity/caution 케이스 처리. GPT 호출 없음
    unknown_menu_handler.py       # escalation_case: unknown_menu
    unknown_remain_handler.py     # escalation_case: unknown_remain

  rules/
    hidden_rules.py               # 음식명/재료/triggered_flags 기준 hidden rule 조회
    hidden_rules_data.py          # 숨은 재료 후보 데이터

  gpt/
    gpt_client.py                 # Azure OpenAI 호출 공통 클라이언트
    response_parser.py            # GPT 응답 dict 파싱
    prompts/
      unknown_menu.py             # DB 미등록 메뉴 분석 프롬프트
      unknown_remain.py           # 미인식 variant owner_card 생성 프롬프트

  models/
    input_verification.py         # 룰엔진 결과 입력 스키마
    final_output.py               # 최종 출력 스키마

  tests/
    test_case_router.py
    test_danger.py
    test_caution.py
    test_unknown_menu.py
    test_unknown_remain.py
    test_template_builder.py
```

루트 `tests/`에는 실제 룰엔진 입력 형태의 시나리오와 결과 JSON 생성용 테스트가 있습니다.

```text
tests/
  test_pipeline.py                # pytest 기반 6개 파이프라인 케이스 테스트
  pipeline_demo_data.py           # 룰엔진 출력 형태의 가상 시나리오 6개
  generate_pipeline_json.py       # pipeline_scenarios/results JSON 생성
  pipeline_scenarios.json         # 입력 시나리오 확인용 JSON
  pipeline_results.json           # 실제 ai_result 실행 결과 JSON
```

## 입력 형식

`ai_result`는 룰엔진이 넘겨주는 메뉴별 결과를 입력으로 받습니다.

```json
{
  "menu_name_ko": "된장찌개",
  "is_spicy": false,
  "risk_level": "caution",
  "hit_tags": [],
  "triggered_flags": ["has_unclear_broth", "has_unclear_jeotgal"],
  "forbidden_tags": [
    "is_beef",
    "is_chicken",
    "is_duck",
    "is_egg",
    "is_milk",
    "is_pork",
    "is_crab",
    "is_shrimp",
    "is_squid",
    "is_mackerel",
    "is_shellfish",
    "is_fish"
  ],
  "need_gpt": true,
  "escalation_case": ["ambiguity"],
  "gpt_context": {
    "base_menu": "된장찌개",
    "ingredients_explicit": ["된장", "두부", "애호박", "감자", "양파", "대파", "마늘", "고춧가루"],
    "explicit_tags": ["is_soybean"],
    "variant_tags": []
  },
  "risk_reasons": null
}
```

### 입력 필드

| 필드 | 설명 |
| --- | --- |
| `menu_name_ko` | 한국어 메뉴명 |
| `is_spicy` | 룰엔진이 판단한 매운맛 여부 |
| `risk_level` | 룰엔진 1차 판정. `danger`, `caution`, `safe` |
| `hit_tags` | 룰엔진 1차에서 바로 확정된 위험 태그 |
| `triggered_flags` | 육수, 젓갈, 양념 등 애매함 플래그 |
| `forbidden_tags` | 사용자 프로필에서 나온 제한 태그 |
| `need_gpt` | 룰엔진이 판단한 GPT 필요 여부 |
| `escalation_case` | `ambiguity`, `unknown_menu`, `unknown_remain` 등 |
| `gpt_context` | 베이스 메뉴, 명시 재료, variant 정보 |
| `risk_reasons` | 룰엔진 위험 사유. 없으면 `null` |

## 출력 형식

최종 출력은 화면 표시용 `FinalOutput`입니다.

```json
{
  "menu_name": "김치찌개",
  "risk_level": "caution",
  "hits": ["is_fish", "is_shellfish"],
  "message": {
    "ko": "생선, 조개류 성분이 포함되어 있을 수 있어요.",
    "en": "This menu may contain fish, shellfish.",
    "ar": "قد يحتوي هذا الطبق على سمك, المحار."
  },
  "owner_card": {
    "menu_name": "김치찌개",
    "flag": "has_unclear_jeotgal",
    "question": {
      "ko": "이 메뉴에 액젓, 멸치액젓, 까나리액젓, 새우젓 성분이 들어가나요?",
      "en": "Does this menu contain fish, shellfish?",
      "ar": "هل يحتوي هذا الطبق على سمك, المحار؟"
    }
  }
}
```

### 출력 필드

| 필드 | 설명 |
| --- | --- |
| `menu_name` | 화면에 표시할 메뉴명 |
| `risk_level` | 최종 위험도. `danger`, `caution`, `safe` |
| `hits` | 최종적으로 사용자 제한 태그와 매칭된 태그 |
| `message` | 사용자 안내 문구. `ko`, `en`, `ar` 포함 |
| `owner_card` | 사장님에게 확인할 질문 카드. 필요 없으면 `null` |

## 케이스 처리 흐름

### danger

룰엔진이 이미 `hit_tags`를 확정한 경우입니다.

```text
risk_level=danger
→ danger_handler
→ hit_tags를 hits로 전달
→ 확정형 문구 생성
```

예시 문구:

```json
{
  "ko": "돼지고기 성분이 포함되어 있어요.",
  "en": "This menu contains pork.",
  "ar": "يحتوي هذا الطبق على لحم الخنزير."
}
```

### safe

룰엔진이 안전하다고 판단한 경우입니다.

```text
risk_level=safe
→ GPT 호출 없음
→ safe 템플릿 반환
```

### caution / ambiguity

룰엔진이 `risk_level=caution`, `escalation_case=["ambiguity"]` 형태로 넘긴 일반 애매함 케이스입니다.

```text
메뉴명 + triggered_flags로 hidden_rules 조회
gpt_context.ingredients_explicit로 hidden_rules 추가 조회
hidden candidate tags와 forbidden_tags 교집합 계산
hits 없으면 safe로 전환
hits 있으면 caution 유지 + owner_card 생성
```

이 케이스에서는 GPT를 호출하지 않습니다.

### unknown_menu

DB에 없는 메뉴입니다.

```text
escalation_case contains unknown_menu
→ unknown_menu_handler
→ GPT가 메뉴명과 forbidden_tags 기반으로 hit_tags / message / owner_card 생성
→ hits가 없어도 caution 유지
```

GPT가 owner_card를 만들지 못하면 `forbidden_tags` 기반 fallback 질문을 생성합니다.

### unknown_remain

베이스 메뉴는 인식했지만 variant 토큰이 미인식된 케이스입니다.

```text
escalation_case contains unknown_remain
→ unknown_remain_handler
→ GPT가 미인식 variant 확인 질문 생성
→ hits=[] 유지
→ caution 유지
```

GPT가 owner_card를 만들지 못하면 메뉴명 기반 fallback 질문을 생성합니다.

## 실행 방법

룰엔진 결과 JSON을 준비한 뒤 실행합니다.

```bash
python -m ai_result.main \
  --judged-json outputs/final/menu_001_judged.json \
  --print-json
```

파일로 저장하려면:

```bash
python -m ai_result.main \
  --judged-json outputs/final/menu_001_judged.json \
  --output outputs/final/menu_001_ai_result.json
```

## Azure OpenAI 설정

`unknown_menu`, `unknown_remain` 케이스는 Azure OpenAI를 호출합니다.

`ai_result/.env` 또는 실행 환경에 아래 값이 있어야 합니다.

```bash
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://...
```

현재 배포 모델명은 `gpt_client.py`에서 `gpt-5.4-nano`를 사용합니다.

## 테스트

### 단위 테스트

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s ai_result/tests
```

### 파이프라인 테스트

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/test_pipeline.py
```

### 시나리오 / 결과 JSON 재생성

룰엔진 출력 형태의 가상 입력 6개를 실행해 `pipeline_scenarios.json`, `pipeline_results.json`을 다시 생성합니다.

```bash
python -m tests.generate_pipeline_json
```

주의:

- `ambiguity` 케이스는 GPT를 호출하지 않습니다.
- `unknown_menu`, `unknown_remain` 케이스는 실제 Azure OpenAI를 호출합니다.
- 네트워크, 키, quota 상태에 따라 결과 문구가 달라질 수 있습니다.

## 현재 테스트 시나리오

| case_id | 설명 | GPT |
| --- | --- | --- |
| `01_danger_halal_pork` | 할랄 사용자 + 삼겹살 | 사용 안 함 |
| `02_safe_vegan_sanchae_bibimbap` | 비건 사용자 + 산채비빔밥 safe | 사용 안 함 |
| `03_ambiguity_to_safe_vegan_doenjang_jjigae` | 비건 사용자 + 된장찌개 ambiguity → safe | 사용 안 함 |
| `04_ambiguity_to_caution_shellfish_kimchi_jjigae` | 어류/갑각류 알레르기 + 김치찌개 ambiguity → caution | 사용 안 함 |
| `05_unknown_menu_halal_bacon_cream_pasta` | 할랄 사용자 + DB 미등록 베이컨크림파스타 | 실제 호출 |
| `06_unknown_remain_vegan_chef_special_bibimbap` | 비건 사용자 + 미인식 셰프특선비빔밥 | 실제 호출 |
