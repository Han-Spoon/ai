# Rule Engine

사용자 프로필(할랄, 비건, 알레르기, 매운맛 선호도)을 기반으로 OCR이 추출한 메뉴별 위험도를 판정하는 모듈입니다. GPT 호출 없이 규칙 기반으로 동작하며, 판단이 불확실한 경우에만 `need_gpt: true`를 반환합니다.

---

## 폴더 구조

```text
ai_ruleengine/
  constants.py          # 재료 태그 사전 + 수식어 토큰 (수정 금지)
  data/menus.csv        # 76개 메뉴 DB (카테고리/레시피/애매함 플래그)
  menu_db.py            # CSV 로더
  profile_mapper.py     # 프로필 → 금지 태그 set
  modifier_strip.py     # 수식어 제거 + 매운맛 감지
  menu_matcher.py       # Longest-match 베이스 메뉴 탐색
  ingredient_tagger.py  # 레시피·변형 재료 태깅
  risk_judge.py         # 애매함 플래그 관련성 판단 (Step 8)
  reason_generator.py   # 한국어 이유 문장 생성 (Step 10)
  engine.py             # analyze() / analyze_all() 통합 (Step 1~10)
  main.py               # CLI
examples/
  run_demo.py           # 시연 케이스 10개
```

---

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

---

## 프로필 형식

```json
{
  "religion_type": "halal",
  "is_vegetarian": true,
  "vegetarian_type": "vegan",
  "no_alcohol": false,
  "allergies": ["shrimp", "crab"],
  "no_spicy": true
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `religion_type` | `"halal"` \| `"kosher"` \| `"hindu"` \| `null` | 종교 식이 제한 |
| `is_vegetarian` | `bool` | 채식 여부 |
| `vegetarian_type` | `"vegan"` \| `"lacto"` \| `"ovo"` \| `"lacto_ovo"` \| `"pesco"` \| `null` | 채식 세부 유형 |
| `no_alcohol` | `bool` | `true`이면 알코올 금지 |
| `allergies` | `list[str]` | 알레르기 태그 목록 (예: `["is_egg", "shrimp"]`) |
| `no_spicy` | `bool` | `true`이면 매운맛 비선호 |

**종교별 금지 태그**

| religion_type | 금지 태그 |
|---------------|-----------|
| `halal` | `is_pork`, `is_alcohol` |
| `kosher` | `is_pork`, `is_crab`, `is_shrimp`, `is_shellfish` |
| `hindu` | `is_beef` |

**채식 유형별 금지 태그**

| vegetarian_type | 금지 태그 |
|-----------------|-----------|
| `vegan` | 모든 육류 + 갑각류/해물 + `is_milk`, `is_egg` |
| `lacto` | 모든 육류 + 갑각류/해물 + `is_egg` |
| `ovo` | 모든 육류 + 갑각류/해물 + `is_milk` |
| `lacto_ovo` | 모든 육류 + 갑각류/해물 |
| `pesco` | 모든 육류(`is_pork`, `is_beef`, `is_chicken`, `is_duck`) |

---

## 최종 출력 JSON

`analyze()` 반환값. OCR 원본 필드는 포함되지 않으며, 아래 엔진 필드만 출력됩니다.

### 필드 목록

| 필드 | 타입 | 설명 |
|------|------|------|
| `menu_name_ko` | `str` | 메뉴명 (OCR 원본) |
| `is_spicy` | `bool \| null` | 매운 메뉴 여부 (OCR spicy_detector 우선, 없으면 수식어 감지 fallback) |
| `risk_level` | `"danger"` \| `"caution"` \| `"safe"` | 최종 위험도 |
| `hit_tags` | `list[str]` | 직접 금지 태그 히트 목록. 매운맛 비선호 시 `"is_spicy"` 포함 가능 |
| `triggered_flags` | `list[str]` | 관련 애매함 플래그 목록 (Step 8 결과) |
| `forbidden_tags` | `list[str]` | 프로필에서 도출된 전체 금지 태그 |
| `need_gpt` | `bool` | GPT 에스컬레이션 필요 여부 |
| `escalation_case` | `list[str]` | GPT 호출 사유 목록. `need_gpt: false`이면 항상 `[]` |
| `gpt_context` | `dict \| null` | `need_gpt: true`일 때만 채워짐 |
| `risk_reasons` | `list[dict] \| null` | `need_gpt: false`이고 `hit_tags`가 있을 때만 채워짐 |

**`escalation_case` 값**

| 값 | 설명 |
|----|------|
| `"unknown_menu"` | DB에 없는 미등록 메뉴 (Step 3) |
| `"ambiguity"` | 관련 애매함 플래그 존재 (Step 8) |
| `"unknown_remain"` | 메뉴명 내 인식 불가 토큰 (Step 9) |

### 케이스별 출력 예시

**danger — 금지 재료 직접 히트 (Step 6)**

```json
{
  "menu_name_ko": "삼겹살",
  "is_spicy": false,
  "risk_level": "danger",
  "hit_tags": ["is_pork"],
  "triggered_flags": [],
  "forbidden_tags": ["is_alcohol", "is_pork"],
  "need_gpt": false,
  "escalation_case": [],
  "gpt_context": null,
  "risk_reasons": [
    {
      "reason_type": "halal",
      "reason_ko": "돼지고기 성분이 포함되어 있습니다"
    }
  ]
}
```

**caution + need_gpt: true — 애매함 플래그 (Step 8)**

```json
{
  "menu_name_ko": "된장찌개",
  "is_spicy": false,
  "risk_level": "caution",
  "hit_tags": [],
  "triggered_flags": ["has_unclear_broth", "has_unclear_jeotgal"],
  "forbidden_tags": ["is_beef", "is_chicken", "is_crab", "is_duck", "is_egg",
                     "is_fish", "is_mackerel", "is_milk", "is_pork",
                     "is_shellfish", "is_shrimp", "is_squid"],
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

**caution + need_gpt: true — DB 미등록 메뉴 (Step 3)**

```json
{
  "menu_name_ko": "마라탕",
  "is_spicy": null,
  "risk_level": "caution",
  "hit_tags": [],
  "triggered_flags": [],
  "forbidden_tags": ["is_alcohol", "is_pork"],
  "need_gpt": true,
  "escalation_case": ["unknown_menu"],
  "gpt_context": {
    "base_menu": null,
    "ingredients_explicit": [],
    "explicit_tags": [],
    "variant_tags": []
  },
  "risk_reasons": null
}
```

**caution + need_gpt: false — 매운맛 비선호 (Step 7 + 10)**

```json
{
  "menu_name_ko": "얼큰 김치찌개",
  "is_spicy": true,
  "risk_level": "caution",
  "hit_tags": ["is_spicy"],
  "triggered_flags": [],
  "forbidden_tags": [],
  "need_gpt": false,
  "escalation_case": [],
  "gpt_context": null,
  "risk_reasons": [
    {
      "reason_type": "spicy",
      "reason_ko": "매운 메뉴입니다. 매운맛을 선호하지 않으시는 분께 적합하지 않을 수 있습니다"
    }
  ]
}
```

**safe**

```json
{
  "menu_name_ko": "비빔밥",
  "is_spicy": false,
  "risk_level": "safe",
  "hit_tags": [],
  "triggered_flags": [],
  "forbidden_tags": [],
  "need_gpt": false,
  "escalation_case": [],
  "gpt_context": null,
  "risk_reasons": null
}
```

### `gpt_context` 구조

`need_gpt: true`일 때만 채워집니다. GPT 프롬프팅 파트로 전달됩니다.

```json
{
  "base_menu": "된장찌개",
  "ingredients_explicit": ["된장", "두부", "애호박"],
  "explicit_tags": ["is_soybean"],
  "variant_tags": ["is_beef"]
}
```

| 필드 | 설명 |
|------|------|
| `base_menu` | DB에서 매칭된 베이스 메뉴명. DB 미등록이면 `null` |
| `ingredients_explicit` | DB에 등록된 레시피 재료 목록 |
| `explicit_tags` | 레시피 재료에서 도출된 재료 태그 |
| `variant_tags` | remain 토큰(변형 재료)에서 도출된 재료 태그 |

### `risk_reasons` 구조

`need_gpt: false`이고 `hit_tags`가 있을 때만 채워집니다.

```json
[
  {
    "reason_type": "halal",
    "reason_ko": "돼지고기 성분이 포함되어 있습니다"
  }
]
```

| `reason_type` | 설명 |
|---------------|------|
| `"halal"` | 할랄 금지 재료 |
| `"kosher"` | 코셔 금지 재료 |
| `"hindu"` | 힌두교 금지 재료 |
| `"vegetarian"` | 채식 금지 재료 |
| `"alcohol"` | 알코올 제한 |
| `"allergy"` | 알레르기 |
| `"spicy"` | 매운맛 비선호 |

---

## 판정 흐름 (Step 1 ~ 10)

```
Step 1   프로필 → 금지 태그 추출
Step 2   수식어 제거 + 매운맛 토큰 감지
Step 3   DB에서 베이스 메뉴 Longest-match 탐색
Step 4   레시피 재료 태깅 (explicit)
Step 5   변형 재료(remain 토큰) 태깅 (variant)
Step 6   금지 태그 교집합 → danger 즉시 반환
Step 7   매운맛 비선호 + 매운 메뉴 → caution 플래그 세팅 (계속 진행)
Step 8   애매함 플래그 관련성 판단 → caution + need_gpt
Step 9   미확인 remain 토큰 → need_gpt 보정
Step 10  Step 7 · 8 · 9 결과 합산 → 최종 출력
```

---

### Step 1 — 프로필 → 금지 태그 추출

`profile_mapper.py`의 `map_profile_to_forbidden()`이 프로필 dict를 금지 태그 set으로 변환합니다.

- `religion_type` → 종교별 금지 태그 추가
- `is_vegetarian + vegetarian_type` → 채식 유형별 금지 태그 추가
- `no_alcohol: true` → `is_alcohol` 추가
- `allergies` → `is_` 접두사를 붙여 태그 변환 후 추가 (예: `"shrimp"` → `"is_shrimp"`)
- `no_spicy`는 이 단계에서 처리하지 않고 Step 7에서 별도 분기

---

### Step 2 — 수식어 제거 + 매운맛 토큰 감지

`modifier_strip.py`의 `strip_modifiers()`가 메뉴명에서 정체성과 무관한 수식어를 제거합니다.

- `SPICY_TOKENS`(예: `얼큰`, `매콤`, `불`) 포함 시 `is_spicy = True`로 감지하고 이름에서 제거
- `REMOVE_TOKENS`(예: `시그니처`, `특제`) 제거
- 예: `"시그니처 매콤 차돌된장찌개"` → `"차돌된장찌개"`, `is_spicy=True`

OCR의 `is_spicy` 값이 있으면 우선 사용하고, 없으면 수식어 감지 결과를 fallback으로 사용합니다.

---

### Step 3 — 베이스 메뉴 매칭

`menu_matcher.py`의 `find_base_menu()`가 Longest-match 전략으로 DB에서 베이스 메뉴를 탐색합니다.

- 수식어 제거 후 메뉴명을 DB 76개 메뉴와 비교, 가장 긴 매칭을 선택
- 매칭 후 남은 토큰(remain)을 Step 5에서 변형 재료로 처리
- 예: `"차돌된장찌개"` → base=`"된장찌개"`, remain=`["차돌"]`

**DB 미등록 메뉴인 경우** → `caution`, `need_gpt: true`, `escalation_case: ["unknown_menu"]`로 즉시 반환합니다.

---

### Step 4 — 레시피 재료 태깅 (explicit)

`ingredient_tagger.py`의 `tag_explicit()`가 DB에 등록된 레시피 재료를 재료 태그로 변환합니다.

- `constants.py`의 `VARIANT_INGREDIENTS` 사전을 기준으로 각 재료명에서 태그 매칭
- 긴 키워드부터 매칭하여 오탐 방지 (예: `"소고기"` → `is_beef`)

---

### Step 5 — 변형 재료 태깅 (variant)

`ingredient_tagger.py`의 `tag_variants()`가 remain 토큰에서 추가 태그를 도출합니다.

- `GROUP_VARIANTS` 그룹 키(예: `"해물"`, `"고기"`) 매칭 시 해당 태그 세트 전체 추가
- 이후 `VARIANT_INGREDIENTS`로 개별 태그 매칭
- 예: remain=`["오리"]` → `is_duck` 추가

미확인 토큰(어느 사전에도 매칭 안 되는 토큰)이 있으면 `need_gpt_unknown = True` 플래그를 세웁니다.

---

### Step 6 — 금지 태그 교집합 → danger

Step 1의 `forbidden_tags`와 Step 4·5의 `menu_tags` 교집합을 계산합니다.

- 교집합이 존재하면 → `risk_level: "danger"`, `need_gpt: false`, `risk_reasons` 생성 후 **즉시 반환**
- 교집합이 없으면 → Step 7 진행

---

### Step 7 — 매운맛 비선호 + 매운 메뉴

`profile.no_spicy: true`이고 `is_spicy: true`이면 `spicy_hit` 플래그를 세웁니다.

- **조기 반환하지 않고 Step 8·9를 계속 진행합니다.**
- 매운맛 단독으로는 GPT를 호출하지 않습니다.
- 최종 판정은 Step 10에서 합산됩니다.

---

### Step 8 — 애매함 플래그 관련성 판단

`risk_judge.py`의 `judge_risk()`가 메뉴 DB의 `ambiguity_flags`와 사용자 프로필의 관련성을 판단합니다.

**애매함 플래그 종류 및 관련성 기준**

| 플래그 | 설명 | 관련성 기준 |
|--------|------|-------------|
| `has_unclear_broth` | 육수 종류 불분명 | 동물성 재료가 `forbidden_tags`에 포함되거나 채식 프로필 |
| `has_unclear_seasoning` | 맛술·청주 등 양념 불분명 | `is_alcohol`이 `forbidden_tags`에 포함 |
| `has_unclear_jeotgal` | 젓갈류 사용 여부 불분명 | 해물류 알레르기 또는 채식 프로필 |
| `has_hidden_animal` | 명시되지 않은 동물성 재료 가능성 | 동물성 태그가 `forbidden_tags`에 포함되거나 비건 프로필 |
| `has_variant` | 동일 메뉴명의 재료 변형 존재 | `forbidden_tags`가 비어있지 않은 모든 프로필 |

관련 있는 플래그가 하나라도 있으면 → `risk_level: "caution"`, `need_gpt: true`, 해당 플래그를 `triggered_flags`에 추가합니다.

---

### Step 9 — 미확인 remain 토큰 보정

Step 5에서 세운 `need_gpt_unknown` 플래그를 반영합니다.

- `need_gpt_unknown: true`이면 → `need_gpt: true`로 강제 설정
- 이때 `risk_level`이 `"safe"`였다면 `"caution"`으로 올림
- `escalation_case`에 `"unknown_remain"` 추가

---

### Step 10 — 결과 합산 → 최종 출력

Step 7의 `spicy_hit`과 Step 8·9 결과를 합산하여 최종 dict를 구성합니다.

```
spicy_hit = True
  → "is_spicy"를 hit_tags에 추가
  → risk_level이 "safe"이면 "caution"으로 업데이트 (이미 "caution"이면 유지)
  → need_gpt는 Step 8·9 결과를 그대로 따름 (spicy 단독으로 GPT 호출 안 함)

escalation_case 결정
  → need_gpt: true인 경우에만 채움
  → unknown_remain 여부 → "unknown_remain"
  → triggered_flags 비어있지 않음 → "ambiguity"
  → (복합 가능: ["ambiguity", "unknown_remain"])

risk_reasons 결정
  → hit_tags가 있고 need_gpt: false인 경우에만 생성
  → reason_generator.py의 generate_risk_reasons() 호출
```

**최종 risk_level 결정 요약**

| 조건 | risk_level | need_gpt | escalation_case |
|------|------------|----------|-----------------|
| Step 6 forbidden 히트 | `danger` | `false` | `[]` |
| Step 7 spicy_hit만 | `caution` | `false` | `[]` |
| Step 7 spicy_hit + Step 8 ambiguity | `caution` | `true` | `["ambiguity"]` |
| Step 8 ambiguity만 | `caution` | `true` | `["ambiguity"]` |
| Step 9 unknown_remain만 | `caution` | `true` | `["unknown_remain"]` |
| Step 3 DB 미등록 | `caution` | `true` | `["unknown_menu"]` |
| 아무것도 해당 없음 | `safe` | `false` | `[]` |

---

## 데모

```bash
python examples/run_demo.py --verbose
```

10가지 케이스(무슬림+삼겹살, 비건+된장찌개, 갑각류 알레르기+김치찌개 등)를 실행하고 PASS/FAIL 결과와 판정 이유를 출력합니다.
