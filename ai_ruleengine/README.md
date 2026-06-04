# Rule Engine

사용자 프로필(할랄, 비건, 알레르기, 매운맛 선호도)을 기반으로 OCR이 추출한 메뉴별 위험도를 판정하는 모듈입니다. GPT 호출 없이 규칙 기반으로 동작하며, 판단이 불확실한 경우에만 `need_gpt=True`를 반환합니다.

## 폴더 구조

```text
ai_ruleengine/
  constants.py          # 재료 태그 사전 + 수식어 토큰 (수정 금지)
  data/menus.csv        # 76개 메뉴 DB (카테고리/레시피/애매함 태그)
  menu_db.py            # CSV 로더
  profile_mapper.py     # 프로필 → 금지 태그 set
  modifier_strip.py     # 수식어 제거 + 매운맛 감지
  menu_matcher.py       # Longest-match 베이스 메뉴 탐색
  ingredient_tagger.py  # 레시피·변형 재료 태깅
  risk_judge.py         # danger / caution / safe 판정
  reason_generator.py   # 한국어 이유 문장 생성
  engine.py             # analyze() / analyze_all() 통합
  main.py               # CLI
examples/
  run_demo.py           # 시연 케이스 10개
```

## 실행

```bash
python ai_ruleengine/main.py \
  --ocr-json outputs/final/menu_001_result.json \
  --profile profile.json \
  --output outputs/final/menu_001_judged.json
```

단계별 처리 내용 출력:

```bash
python ai_ruleengine/main.py \
  --ocr-json outputs/final/menu_001_result.json \
  --profile profile.json \
  --verbose
```

## 프로필 형식

```json
{
  "religion_type": "halal",
  "vegan_type": null,
  "no_alcohol": false,
  "allergies": ["shrimp"],
  "is_spicy": false
}
```

| 필드 | 값 |
|------|----|
| `religion_type` | `"halal"` `"kosher"` `"hindu"` `null` |
| `vegan_type` | `"vegan"` `"lacto"` `"ovo"` `"lacto_ovo"` `"pesco"` `null` |
| `no_alcohol` | `true` / `false` |
| `allergies` | `["shrimp", "crab", ...]` |
| `is_spicy` | `true`(선호) / `false`(비선호) / `null`(무관) |

## 출력 필드

`menu_analyses[]` 각 항목에 아래 필드가 추가됩니다.

| 필드 | 설명 |
|------|------|
| `risk_level` | `"danger"` `"caution"` `"safe"` |
| `risk_reasons` | 원인 태그 목록 (예: `["is_pork"]`) |
| `reason_ko` | 한국어 이유 문장 |
| `reason_en` | `null` (후속 LLM 담당) |
| `need_gpt` | GPT 에스컬레이션 필요 여부 |
| `is_spicy` | 메뉴명에서 매운맛 감지 여부 |

## 판정 흐름

```
Step 1  프로필 → 금지 태그 추출
Step 2  수식어 제거 + 매운맛 토큰 감지
Step 3  DB에서 베이스 메뉴 Longest-match 탐색
Step 4  레시피 재료 태깅
Step 5  변형 재료(remain 토큰) 태깅
Step 6  금지 태그 교집합 → danger
Step 7  매운맛 비선호 + 매운 메뉴 → danger
Step 8  애매함 플래그 관련성 판단 → caution + need_gpt
Step 9  미확인 remain 토큰 → need_gpt
Step 10 한국어 이유 문장 생성
```

## 데모

```bash
python examples/run_demo.py --verbose
```
