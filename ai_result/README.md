# AI Result

룰엔진 결과 JSON을 받아 케이스를 라우팅하고, 화면 표시용 최종 템플릿을 반환하는 모듈입니다.

`ai_result`는 OCR 결과를 직접 처리하지 않습니다.  
입력은 반드시 `ai_ruleengine`이 생성한 judged JSON입니다.

---

## 폴더 구조

```text
ai_result/
  main.py                         # 엔트리포인트: 룰엔진 JSON 입력 → 케이스 라우팅 → 최종 템플릿 반환

  core/
    case_router.py                # risk_level + escalation_case 기준 handler 분기
    template_builder.py           # 최종 출력 JSON 템플릿 조립

  handlers/
    danger_handler.py             # danger 케이스 처리
    caution_handler.py            # caution 일반 케이스 처리
    unknown_menu_handler.py       # escalation_case: unknown_menu
    unknown_remain_handler.py     # escalation_case: unknown_remain

  rules/
    hidden_rules.py               # triggered_flag + 음식명/재료 기반 hidden_rules 조회 로직
    hidden_rules_data.py          # hidden ingredient candidate DB

  gpt/
    gpt_client.py                 # OpenAI API 호출 공통 클라이언트
    response_parser.py            # GPT 응답 dict → 내부 구조체 파싱
    prompts/
      caution_verify.py           # caution hidden_rules 검증 프롬프트
      unknown_menu.py             # DB 미등록 메뉴 분석 프롬프트
      unknown_remain.py           # 미인식 variant owner_card 생성 프롬프트

  models/
    rule_engine_input.py          # 룰엔진 결과 JSON 입력 스키마
    final_output.py               # 최종 출력 템플릿 스키마

  tests/
    test_case_router.py           # 라우터 우선순위 테스트
    test_danger.py                # danger 케이스 테스트
    test_caution.py               # caution / safe 전환 테스트
    test_unknown_menu.py          # unknown_menu 테스트
    test_unknown_remain.py        # unknown_remain 테스트
```

---

## 입력

`ai_result`는 `ai_ruleengine.analyze_all()`을 거친 결과 JSON을 입력으로 받습니다.

```json
{
  "menu_analyses": [
    {
      "menu_name_ko": "된장찌개",
      "is_spicy": false,
      "risk_level": "caution",
      "hit_tags": [],
      "triggered_flags": ["has_unclear_jeotgal"],
      "forbidden_tags": ["is_fish"],
      "need_gpt": true,
      "escalation_case": ["ambiguity"],
      "gpt_context": {
        "base_menu": "된장찌개",
        "ingredients_explicit": ["된장", "두부", "대파"],
        "explicit_tags": [],
        "variant_tags": []
      },
      "risk_reasons": null
    }
  ]
}
```

### 주요 입력 필드

| 필드 | 설명 |
|------|------|
| `menu_name_ko` | 메뉴명 |
| `risk_level` | 룰엔진 판정값. `"danger"` `"caution"` `"safe"` |
| `hit_tags` | 룰엔진에서 바로 확정된 위험 태그 |
| `triggered_flags` | 애매함 플래그 목록 |
| `forbidden_tags` | 사용자 프로필에서 도출된 제한 태그 |
| `need_gpt` | GPT 확인 필요 여부 |
| `escalation_case` | GPT 호출 사유. `"unknown_menu"` `"ambiguity"` `"unknown_remain"` |
| `gpt_context` | GPT 프롬프트에 전달할 메뉴/재료 컨텍스트 |
| `risk_reasons` | 룰엔진이 생성한 위험 사유 |

---

## 출력

최종 출력은 화면 표시용 템플릿입니다.

```json
{
  "menu_name": "된장찌개",
  "risk_level": "caution",
  "hits": ["is_fish"],
  "message": {
    "ko": "어류 성분이 포함되어 있을 가능성이 있습니다",
    "en": null,
    "ar": null
  },
  "owner_card": {
    "menu_name": "된장찌개",
    "flag": "has_unclear_jeotgal",
    "question": {
      "ko": "혹시 새우젓이나 멸치젓을 사용하시나요?",
      "en": "Do you use salted shrimp or anchovy jeotgal?",
      "ar": "هل تستخدم معجون الروبيان المملح أو الأنشوجة؟"
    }
  }
}
```

### 출력 필드

| 필드 | 설명 |
|------|------|
| `menu_name` | 메뉴명 |
| `risk_level` | 최종 위험도. `"danger"` `"caution"` `"safe"` |
| `hits` | 최종적으로 사용자 제한 태그와 매칭된 태그 |
| `message.ko` | 사용자 화면에 표시할 한국어 안내 문구 |
| `message.en` | 영어 안내 문구. 현재는 `null` 가능 |
| `message.ar` | 아랍어 안내 문구. 현재는 `null` 가능 |
| `owner_card` | 사장님에게 확인할 질문 카드. 필요 없으면 `null` |

---

## 케이스 라우팅

라우팅 기준은 `risk_level`과 `escalation_case`입니다.

```text
danger
  → danger_handler
  → hit_tags가 이미 확정되어 있으므로 바로 템플릿 생성

safe
  → GPT 호출 없이 safe 템플릿 반환

caution + unknown_menu
  → unknown_menu_handler
  → DB 미등록 메뉴를 GPT가 분석해 템플릿 생성

caution + unknown_remain
  → unknown_remain_handler
  → hit_tags가 없어도 caution 유지, owner_card 생성

caution 일반 케이스
  → caution_handler
  → hidden_rules 조회 + GPT 검증
  → 최종 hits가 없으면 safe로 전환
```

`danger`는 이미 룰엔진에서 `hit_tags`가 확정된 상태이므로, `escalation_case`보다 우선 처리합니다.

---

## 실행

먼저 룰엔진 팀에서 judged JSON을 생성합니다.

```bash
python ai_ruleengine/main.py \
  --ocr-json outputs/final/menu_001_result.json \
  --profile profile.json \
  --output outputs/final/menu_001_judged.json
```

그 다음 `ai_result`를 실행합니다.

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

---

## GPT 사용

모든 GPT 호출은 아래 파일을 통해서만 수행합니다.

```text
ai_result/gpt/gpt_client.py
```

기본 모델은 `.env`의 `OPENAI_MODEL`을 우선 사용하고, 없으면 `gpt-5.4-nano`를 사용합니다.

---

## 테스트

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s ai_result/tests
```

테스트 범위:

| 테스트 | 확인 내용 |
|--------|-----------|
| `test_case_router.py` | safe가 caution handler로 가지 않는지, danger 우선순위 |
| `test_danger.py` | danger hit_tags 템플릿 생성 |
| `test_caution.py` | caution GPT 검증, hits 없을 때 safe 전환 |
| `test_unknown_menu.py` | DB 미등록 메뉴 템플릿 생성 |
| `test_unknown_remain.py` | unknown_remain caution 유지 + owner_card 생성 |
