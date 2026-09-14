# ⑧ XAI/설명 에이전트 스펙

담당: 정유진
상태: 초안 (미확정 항목은 §8 참조)
상위 문서: `catoin-multi-agent-architecture.md`, `catoin-db-schema.md`

---

## 0. 역할 범위

두 가지 책임을 가진다.

1. **판정**: 확정값(hard evidence)과 확률(soft evidence)을 사용자 개인의 알레르기·식이 제약과 대조해 DANGER/CAUTION/SAFE를 결정
2. **설명**: 판정 근거를 사람이 이해할 수 있는 문장으로 바꾸고, 정보가 부족한 재료에 대해 사장님에게 물어볼 질문을 만듦

판정 로직과 설명 로직을 분리하지 않고 XAI 하나에 묶은 이유는 반대로 **"둘 다 사용자 프로필과 재료 데이터를 같은 순간에 대조해야" 자연스럽기 때문**이다. 대신 이 에이전트를 확률 계산(⑤)이나 하드 오버라이드 조회(③)로부터는 분리해둬서, 판정 기준(threshold)이나 문구·언어가 바뀔 때 이 문서/이 에이전트만 고치면 되게 했다.

---

## 1. 입력 / 출력 스펙

### 입력 (Supervisor가 취합해서 전달)

| 필드 | 타입 | 필수 | 출처 | 설명 |
|---|---|---|---|---|
| `confirmed_results` | `{ingredient_id: bool}` | 있으면 전달 | ③ Exact 피드백 | hard evidence. 없으면 빈 객체 |
| `probability_results` | `{ingredient_id: {posterior_mean: float, anomaly_locked: bool}}` | 있으면 전달 | ⑤ Bayesian | soft evidence. `anomaly_locked: true`면 확률값과 무관하게 CAUTION 이상 강제(아래 처리 로직 참고) |
| `proposed_variant_ingredients` | `{ingredient_id: source}` | 있으면 전달 | ④ 변형 태깅 | **DB 미반영 제안**, source=`variant_suggested` 등 신뢰도 낮음 |
| `no_information` | bool | 필수 | ④/⑥ | 메뉴/재료 정보 자체가 없을 때 true (엣지 케이스 4) |
| `user_profile` | object | 필수 | `user_profiles` | `religion_type`, `is_vegetarian`, `vegetarian_type`, `no_alcohol`, `allergies`, `no_spicy` |

### 출력 (Supervisor에게 반환)

```json
{
  "risk_level": "danger" | "caution" | "safe",
  "hits": ["is_pork"],
  "confidence": "confirmed" | "estimated" | "unknown",
  "message": {
    "ko": "돼지고기 성분이 포함되어 있어요.",
    "en": "This menu contains pork.",
    "ja": "このメニューには豚肉が含まれています。",
    "zh-Hans": "这道菜含有猪肉成分。",
    "zh-Hant": "這道菜含有豬肉成分。",
    "es": "Este plato contiene cerdo."
  },
  "owner_card": {
    "menu_id": "str",
    "ingredient_id": "str",
    "question": {
      "ko": "이 메뉴에 돼지고기 성분이 들어가나요?",
      "en": "Does this menu contain pork?",
      "ja": "このメニューには豚肉が入っていますか？",
      "zh-Hans": "这道菜里有猪肉吗？",
      "zh-Hant": "這道菜裡有豬肉嗎？",
      "es": "¿Este plato contiene cerdo?"
    }
  } | null
}
```

`confidence` 필드가 핵심이다 — 같은 `risk_level: danger`라도 **확정값 기반인지 확률 추정 기반인지**를 사용자에게 다른 톤으로 전달해야 한다 (§4 참고).

---

## 2. 처리 로직

```mermaid
flowchart TD
    IN["입력 수신"] --> P1["Step 1: user_profile → forbidden_tags 매핑\n(religion/vegetarian/alcohol/allergy/spicy)"]
    P1 --> P2{confirmed_results에\nforbidden_tags 교집합?}
    P2 -->|Yes| DANGER["risk_level: danger\nconfidence: confirmed\n즉시 확정"]
    P2 -->|No| P3{no_information?}
    P3 -->|Yes| CAUTION1["risk_level: caution 이상 강제\nconfidence: unknown\n(FN-minimization)"]
    P3 -->|No| P3B{"anomaly_locked: true인\n재료 존재?"}
    P3B -->|Yes| CAUTION2["risk_level: caution 이상 강제\nconfidence: estimated\n확률값 무시"]
    P3B -->|No| P4["probability_results에 threshold 적용"]
    CAUTION2 --> P4
    P4 --> P5{proposed_variant_ingredients\n존재?}
    P5 -->|Yes| P6["낮은 신뢰도로 반영\n(확정형 문구 금지, 최소 caution)"]
    P5 -->|No| P7[결과 확정]
    P6 --> P7
    P7 --> MSG["다국어 메시지 생성"]
    DANGER --> MSG
    CAUTION1 --> MSG
    MSG --> OC{애매한 재료\n존재?}
    OC -->|Yes| CARD["owner_card 생성\n(질문 텍스트, 사용자 태그 기준으로 우선순위)"]
    OC -->|No| DONE["owner_card: null"]
    CARD --> OUT[Supervisor에 반환]
    DONE --> OUT
```

### 2-1. Step별 설명

1. **forbidden_tags 매핑**: `religion_type`(halal→is_pork/is_alcohol 등), `vegetarian_type`, `no_alcohol`, `allergies`, `no_spicy`를 하나의 금지 태그 집합으로 변환.
2. **hard evidence 우선 체크**: `confirmed_results`에 forbidden_tags와 겹치는 재료가 있으면 다른 계산 없이 즉시 `danger` + `confidence: confirmed`.
3. **정보 없음 우선순위**: hard evidence로 안 걸렸어도 `no_information: true`면 확률 계산 자체를 건너뛰고 `caution` 이상 강제 (엣지 케이스 4와 동일 원칙).
3-1. **anomaly_locked 강제**: `probability_results[ingredient_id].anomaly_locked: true`인 재료는 posterior_mean이 아무리 낮게 나와도 `caution` 이상으로 강제하고 `confidence: estimated`로 표시 — ③이 override를 거부한 이상 답변이 확률 계산을 거치며 조용히 SAFE로 새는 것을 막기 위함(이 값이 ④를 거쳐 여기까지 끊기지 않고 와야 함).
4. **확률 기반 판정**: (anomaly_locked가 아닌 재료에 한해) `probability_results`에 판정 임계값(§3, 미확정)을 적용해 caution/safe 결정.
5. **변형 제안 반영**: `proposed_variant_ingredients`가 있으면 확정 재료와 **절대 같은 신뢰도로 취급하지 않는다** — 최소 caution 처리하고, 문구도 추정형으로만 생성 (danger 확정 근거로 쓰지 않음).
6. **메시지 생성**: `confidence`에 따라 확정형/추정형 문구 템플릿 분기 (§4).
7. **사장님 질문 생성**: 확정도 안 되고 확률도 애매한 재료 중 사용자 태그와 관련 있는 것 하나를 골라 `owner_card` 생성.

---

## 3. 판정 임계값 (doc3 4번 섹션과 동일 이슈, 아직 미확정)

- DANGER/CAUTION/SAFE를 가르는 확률 컷오프 자체가 아직 숫자로 정해지지 않음 — F2 score 최적화로 정하기로만 합의됨
- 컷오프 계산/보관 주체가 Supervisor 내부 규칙인지 XAI 내부 상수인지도 미정 — 이 문서에서는 일단 **XAI가 threshold를 들고 있다고 가정**하고 작성함 (§8에서 재확인 필요)

---

## 4. 다국어 메시지 정책

6개 언어를 지원하기로 확정: `ko`(한국어), `en`(영어), `ja`(일본어), `zh-Hans`(중국어 간체), `zh-Hant`(중국어 번체), `es`(스페인어).

**확정형 vs 추정형 톤 구분이 핵심 설계 포인트다**:

| confidence | 문구 톤 | 예시 (ko) |
|---|---|---|
| `confirmed` | 단정형 | "돼지고기 성분이 포함되어 있어요." |
| `estimated` | 추정형, 확률 뉘앙스 포함 | "돼지고기가 들어있을 가능성이 있어요." |
| `unknown` | 정보 부족 명시 | "이 메뉴에 대한 정보가 부족해 안전을 확인할 수 없어요." |

이 구분을 안 하면 확률 추정치를 확정 사실처럼 전달하게 되어, 사용자가 과신하거나(추정을 확정으로 오해) 반대로 신뢰를 잃을(나중에 틀렸을 때) 위험이 있다.

---

## 5. Supervisor와의 계약

| ⑧이 받는 것 | 보장 사항 |
|---|---|
| `confirmed_results`/`probability_results` | Supervisor가 ③/⑤ 호출을 마친 뒤에만 전달 — 부분적으로만 채워질 수 있음(둘 다 없을 수도 있음, 그 경우 `no_information` 처리) |
| `user_profile` | 온보딩 단계에서 필수로 수집되므로 항상 존재함이 보장됨 (비로그인/미입력 케이스 없음) |

| ⑧이 돌려주는 것 | Supervisor의 후속 판단 |
|---|---|
| `owner_card != null` | Supervisor가 ⑦에 전달 → ⑦이 저장 명령 생성 → **백엔드가 `owner_verification_requests`에 INSERT**. XAI와 ⑦은 DB 자격 증명을 갖지 않음 |
| `risk_level` | 최종 사용자 응답에 그대로 포함, 추가 라우팅 없음 (파이프라인의 마지막 단계) |

---

## 6. 예외 처리

| 상황 | 처리 |
|---|---|
| `confirmed_results`와 `probability_results`가 동시에 같은 재료를 다르게 판정 | `confirmed_results`(hard evidence)가 항상 우선 |
| `proposed_variant_ingredients`만 있고 나머지는 다 없음 | caution으로 처리하되 `confidence: estimated`, danger로 격상 금지 |
| 사용자 태그와 무관한 재료만 애매함 | `owner_card` 생성 안 함 — 관련 없는 질문으로 사장님 피로도 유발 방지 |

---

## 7. 테스트 케이스

| # | 입력 | 기대 출력 | 검증 포인트 |
|---|---|---|---|
| 1 | 할랄 사용자 + confirmed: `{is_pork: true}` | `danger`, `confidence: confirmed` | 확정형 문구 사용 |
| 2 | 비건 사용자 + probability: `{is_milk: 0.3}` (threshold 미만) | `safe` 또는 `caution` (threshold에 따라) | 추정형 문구, danger로 확정 짓지 않음 |
| 3 | `no_information: true` | `caution` 이상 강제 | FN-minimization 원칙 위반 없을 것 |
| 4 | `proposed_variant_ingredients`만 있음(차돌된장찌개, 소고기 제안) + 채식 사용자 | `caution`, `confidence: estimated` | `danger`로 절대 확정 짓지 않을 것 |
| 5 | 알레르기 태그와 무관한 애매 재료만 존재 | `owner_card: null` | 불필요한 사장님 질문 생성 안 할 것 |
| 6 | `confirmed_results`와 `probability_results`가 상반 | `confirmed_results` 값이 이김 | hard evidence 우선순위 확인 |

---

## 8. 미확정 항목 (팀 확인 대기)

- [ ] **DANGER/CAUTION/SAFE 컷오프 실제 값** — F2 최적화로 정하기로만 합의, 숫자 미정
- [ ] **컷오프 계산/보관 주체** — Supervisor 내부 규칙인지 XAI 내부 상수인지
