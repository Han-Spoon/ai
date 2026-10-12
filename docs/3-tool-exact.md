# ③ Exact Feedback Tool (재료 단위 확인 조회)

담당: 정유진 / 상위 문서: `caution-multi-agent-architecture.md`

---

## 0. 역할 범위

**③은 순수 조회기다. 판단하지 않는다.**

- 하는 것: `store_id + menu_id`로 `ingredient_confirmations` 조회 → 재료 단위 확인 맵 반환
- 하지 않는 것: 재료 태그(`constraint_tags`, 재료가 걸리는 제한 태그, 예: 돼지고기 → `is_pork`)는 붙이지 않는다. **태그는 ④만 붙인다** (결정, 2026-10-10 회의, ④ §2-7). ③은 확인값마다 `ingredient_id`만 정확히 돌려주고 `constraint_tags`는 `[]`로 보낸다
- 태그를 채우는 방법: **③이 ④를 직접 부르지 않는다. Supervisor가 ③ 결과를 받은 뒤 ④의 태그 조회(`lookup_tags`)를 불러 채운다** (⓪ §1-2, §2-11 `tag_lookup`). Agent·Tool끼리는 직접 호출하지 않기 때문이다 (⓪ §0). ③ 확인값이 ⑧로 가는 경로는 모두 채워야 한다. ③만 거치는 경로뿐 아니라 ④⑤를 거치는 경로도 마찬가지다. ④는 확인된 재료를 확장 결과에서 빼므로(④ §2-1 7단계), 따로 채우지 않으면 확인값의 태그가 빈 채로 ⑧에 간다 (§6)
- 설계 원칙: **메뉴 단위 이분법 금지.** 부분 확인을 반드시 지원한다.

| 하지 않음 | 담당 |
|---|---|
| 재료 확장 / 변형 판정 | ④ |
| 전부·일부 판단 후 라우팅 | ⓪ Supervisor Agent |
| 사용자 알레르기 태그 매칭 | ⑧ Decision Policy / XAI Agent |
| 확률 계산 | ⑤ |
| DB 쓰기 | ⑦ |

### 0-1. 순서 문제와 해결

③은 ④보다 먼저 실행되므로 **그 메뉴의 전체 재료 목록(분모)을 모른다.** 따라서 "전부 확인됨"을 ③이 단독 판정할 수 없다.

→ **③은 보유한 확인 레코드만 반환한다.** 완전성은 `menu_ingredient_cache`(④가 과거 확장 결과를 저장한 테이블)가 있을 때만 `complete`로 표기하고, 없으면 `unknown`으로 반환해 Supervisor가 ④를 호출하게 한다.

### 0-2. 기준 문서(PPT) 대응

[`ppt-baseline.md`](ppt-baseline.md)의 ③ 관련 내용과 이 문서의 대응 위치다. 이 표의 항목이 빠지면 이 문서가 틀린 것이다.

| PPT 표현 | 이 문서 |
|---|---|
| 7쪽: 검증·저장된 메뉴의 재료 확정 근거(Hard Evidence)를 조회 (`확정 근거 조회`) | §0, §2. `ingredient_confirmations`를 `store_id + menu_id`로 조회. 확정 근거는 사장님 답변만 (§1-4) |
| 7쪽: 존재 / 부재 / 미확인 상태를 반환 (`3-State`) | §1-3. `present` / `absent` / `unknown` |
| 7쪽: Agent는 판단하고 Tool은 수행한다 | §0. ③은 조회기다. 만료·override 가능 여부·완전성 표시는 정해진 규칙으로 계산한다 (§1-6, §1-7, §2) |
| 3쪽 외부 연결: 가게 직원 — 질문, 답변 | §1-4. 확정 근거의 출처 |
| 8쪽 1: 모든 호출에 `store_id`를 유지해 가게별 확정 재료가 섞이지 않게 | §1-1, §4-2. `store_id` 필수, 없으면 에러, 전역 조회 금지 |
| 8쪽 2: 확인된 재료는 그대로 유지, 미확인 재료만 확장·추정, 전부 확인되면 확률 계산 생략 | §0(부분 확인 지원), §2(`completeness`), §3-3(`complete`면 ④⑤ 생략) |
| 8쪽 흐름: ③ 확정 재료 조회 (가게에서 제공 재료 정보 · 사용자 피드백 우선 반영) | §3 호출 시점. "사용자 피드백"은 손님 화면의 확인 질문으로 받은 **사장님 답변**으로 해석한다 (§1-4) |
| 8쪽 흐름: 미확정 재료가 없으면 확정 재료로 바로 판정 | §3-3. `completeness: complete` → ⑧ 직행 |
| 8쪽 5: 사장님 확인 정보 → 최소 n회 이상 시 기록 · 이상 여부 검사 | ⑦ 담당 (n=2: 1·2차가 같으면 확정, 다르면 3차까지, ⑦ §2-3). ③은 확정된 값과 ⑦이 붙인 `flagged_anomaly`를 읽기만 한다 (§1-6, §4-2) |
| 8쪽 6: 사장님 확인 n건↑ → DANGER/SAFE 확정 | ③은 `override_eligible: true`로 확정 근거임을 표시하고, 판정은 ⑧이 한다 (§1-6) |
| 8쪽 6: n개월마다 재확인 | §1-7. 3개월이 지나면 `expired: true`로 override하지 않는다. 다시 묻는 경로는 §6 미확정 |
| 10쪽: `Exact-first` | §3. ④보다 먼저 호출해 전부 확인된 메뉴는 가벼운 경로로 끝낸다 |
| 10쪽 RAI 02: 기록 재확인 경로 제공 | §1-7 만료 표시. 재질문 경로는 §6 미확정 |

- PPT에 없는 내용: 기본 메뉴(`base_menu_id`)의 확인값을 가져오는 2차 호출(§3-1)은 PPT에 없다. 10/10 회의 결정으로 쓸 곳이 없어져 보류 중이다 (§6).

---

## 1. 입력 / 출력 스펙

### 1-1. 입력 — `ExactFeedbackRequest` (Supervisor로부터)

입출력 JSON은 ⓪ §1(공통 모델)·§4-2를 기준으로 한다. 아래는 같은 내용을 옮긴 것이다.

```json
{
  "context": {
    "schema_version": "1.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "item",
    "item_id": "scan-123:0"
  },
  "menu": {
    "menu_id": "menu-001",
    "normalized_menu_name": "김치찌개"
  },
  "scope_hint": "exact",
  "warnings": [],
  "errors": []
}
```

| 필드 | 설명 |
|---|---|
| `context` | 요청 공통 정보 (⓪ §1-2 `NodeContext`). `context.store_id`는 양수 필수. 없거나 0 이하면 즉시 에러. 전역 조회 금지. ③은 `context`를 수정하지 않고 그대로 반환 |
| `menu.menu_id` | 조회 대상 메뉴. 변형/base 구분 없이 이 값 하나만 봄. `menu_id`가 없으면 Supervisor가 ③을 호출하지 않음 |
| `menu.normalized_menu_name` | 정규화된 메뉴명. ③ 조회에는 쓰지 않고 로그·추적용 |
| `scope_hint` | `exact \| inherited`. 반환 레코드에 붙일 라벨. ③은 이 값을 **해석하지 않고** `confirmation_scope`로 그대로 반환 |
| `warnings` / `errors` | 앞 단계에서 넘어온 신호. ③은 그대로 보존하고 자기 신호를 덧붙임 |

### 1-2. 출력 — `ExactFeedbackResponse` (Supervisor에게 반환)

```json
{
  "context": {
    "schema_version": "1.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "item",
    "item_id": "scan-123:0"
  },
  "menu_id": "menu-001",
  "confirmation_scope": "exact",
  "completeness": "unknown",
  "confirmations": [
    {
      "ingredient": {
        "ingredient_id": "ingredient-001",
        "canonical_name": "돼지고기"
      },
      "constraint_tags": [],
      "status": "present",
      "evidence": {
        "evidence_id": "evidence-001",
        "evidence_class": "hard",
        "source_type": "owner_feedback",
        "source_ref": "ingredient_confirmations:confirmation-001",
        "verification_status": "owner_confirmed",
        "reliability_weight": 1.0,
        "observed_at": "2026-09-01T00:00:00Z"
      },
      "flagged_anomaly": false,
      "override_eligible": true,
      "expired": false
    }
  ],
  "warnings": [],
  "errors": []
}
```

| 필드 | 설명 |
|---|---|
| `context` | 입력 `context`를 그대로 반환 |
| `menu_id` | 조회한 메뉴 |
| `confirmation_scope` | 입력 `scope_hint` 값 그대로 (§1-5) |
| `completeness` | `complete \| unknown` (§2). 두 값만 쓴다 (2026-10-12, §6) |
| `confirmations[].ingredient` | 재료 (⓪ §1-2 `IngredientRef`) |
| `confirmations[].constraint_tags` | ③은 항상 `[]`로 보낸다. Supervisor가 ④ `lookup_tags`를 불러 채운다 (§0) |
| `confirmations[].status` | `present \| absent \| unknown` (§1-3) |
| `confirmations[].evidence` | 근거 (⓪ §1-2 `EvidenceRef`). 사장님 답변은 `evidence_class: hard`, `source_type: owner_feedback`, `verification_status: owner_confirmed` (§1-4). `observed_at`은 DB `ingredient_confirmations.confirmed_at` 값 |
| `confirmations[].flagged_anomaly` | 통계적으로 이상한 답변 표시. ⑦이 저장할 때 붙인 값을 ③은 그대로 읽는다 (기준은 §1-6) |
| `confirmations[].override_eligible` | 이 확인값으로 판정을 덮어써도 되는지 (§1-6) |
| `confirmations[].expired` | 확인 후 3개월이 지났는지 (§1-7) |
| `warnings` / `errors` | 입력 신호를 보존하고 ③의 신호를 덧붙임 |

### 1-3. `status` — 3값 필수 (2값 금지)

| 값 | 의미 |
|---|---|
| `present` | 들어있다고 확인됨 |
| `absent` | 없다고 확인됨 |
| `unknown` | 아직 확인 안 됨 (레코드 없음과 동일 취급) |

> ⚠️ `true/false` 2값으로 설계하면 **"없다고 확인됨"과 "아직 안 물어봄"이 구분되지 않는다.** 전자는 override 대상, 후자는 확률 계산 대상 — 정반대 처리다.

### 1-4. `evidence` — 근거 종류

| `evidence.source_type` | `evidence_class` | `verification_status` | 후속 처리 |
|---|---|---|---|
| `owner_feedback` | `hard` | `owner_confirmed` | override 후보 |

> 확인 기록은 **사장님 답변만** 다룬다. 사용자(관광객)가 직접 알려준 재료 정보(`user_hard`)는 수집 경로가 없어 제외했다 (2026-10-09). 제출 PPT 8쪽의 "사용자 피드백 우선 반영"은 사용자 화면에 띄운 확인 질문을 통해 받은 **사장님 답변**으로 해석한다 (`ppt-baseline.md` 7쪽 ③ 정의 "확정 근거(Hard Evidence) 조회", 8쪽 "사장님 확인 정보 → 기록").

### 1-5. `confirmation_scope`

| 값 | 의미 | Supervisor의 사용법 |
|---|---|---|
| `exact` | 이 menu_id에 직접 확인됨 | **override** — ⑤ 스킵 가능 (단 `override_eligible: true`일 때만) |
| `inherited` | base 메뉴에서 상속 | **보류** — 원래는 ⑤ prior 보정에만 썼지만, 10/10 회의 결정(사장님 답변은 확률 계산에 쓰지 않음)으로 쓸 곳이 없어졌다. override도 하지 않는다 (§6) |

### 1-6. `override_eligible` — anomaly 정책 반영 (확정)

**정책 확정**: FN-minimization 원칙에 따라, base rate와 명백히 모순되는 확인값은 override 대상에서 제외한다. (예: 돈까스에 `돼지고기: absent`)

**anomaly 판정 기준 (결정, 2026-10-06, #193 — ⑦ §2-3)**: 레시피상 그 재료가 들어갈 확률(⑤ `posterior_mean`)이 **0.9 이상**인데 사장님 답이 **"없음"(`absent`)**이면 `flagged_anomaly: true`다.

- **"있음"(`present`) 답변은 anomaly가 아니다.** "있음"을 믿는 쪽이 더 조심하는 방향이라 위험을 놓치지 않는다. "있음"에 표시를 붙이면 override가 거부돼 DANGER 근거가 CAUTION으로 내려간다.
- 표시를 붙이는 주체는 ⑦이다 (사장님 답변 저장 시). ③은 판정하지 않고 DB의 `flagged_anomaly`를 그대로 읽는다.
- DB 스키마의 예외 규칙(`flagged_anomaly: true` + `present: false`, `caution-db-schema.md` §3 `ingredient_confirmations`)과 같은 방향이다.

| 조건 | `override_eligible` | Supervisor 동작 |
|---|---|---|
| `flagged_anomaly: false` AND `expired: false` AND `scope: exact` | `true` | override 적용, ⑤ 스킵 가능 |
| `flagged_anomaly: true` | **`false`** | **override 거부 → 해당 재료를 ⑤로 전달**, ⑧에서 CAUTION 이상 강제 유지 |
| `expired: true` (§1-7) | **`false`** | override 거부 → 해당 재료를 ⑤로 전달 |
| `scope: inherited` | `false` | override하지 않는다. 쓰임은 보류 (§1-5, §6) |

> ⚠️ **anomaly 재료를 "무시"하면 안 된다.** 재료 자체가 리스트에서 사라지면 확률 계산도 안 되고 판정에서도 빠져 **FN이 발생한다.** 반드시 ⑤로 넘겨 확률을 계산하게 하고, 결과를 CAUTION 이상으로 유지한다.

### 1-7. 확인값 유효기간 — 3개월 (결정, 2026-10-06 회의)

사장님 확인값은 **확인 시점(`confirmed_at`)부터 3개월 동안만** 확정 정보로 쓴다. 3개월은 **등록(확인)한 날짜부터 달력 기준 3개월**이다 (2026-10-10 회의). 메뉴 레시피는 바뀔 수 있으므로 오래된 확인값으로 계속 override하면 FN이 생길 수 있다.

| 조건 | `expired` | 처리 |
|---|---|---|
| `confirmed_at`이 3개월 이내 | `false` | 기존 규칙대로 (§1-6) |
| `confirmed_at`이 3개월 경과 | `true` | `override_eligible: false`. 확정 정보로 쓰지 않는다 |

- **만료 레코드도 드롭하지 않는다.** `expired: true`로 표기해 반환한다. 재료가 리스트에서 사라지면 FN이 생긴다 (§1-6과 같은 이유).
- 만료 판정은 ③이 조회 시점에 `confirmed_at`만 보고 한다. DB 레코드를 지우거나 바꾸지 않는다 (DB 쓰기는 ③ 범위 밖).
- 만료된 재료는 사장님에게 다시 확인받을 대상이다. ⑧이 다시 묻는 질문 카드를 만든다 (⑧ §2-1 7단계, 이유 `expired`).

---

## 2. 처리 로직

```mermaid
flowchart TD
    S[Supervisor 호출] --> V{store_id 존재?}
    V -->|없음| ERR[StoreIdRequiredError]
    V -->|있음| Q["ingredient_confirmations 조회<br/>WHERE store_id AND menu_id"]
    Q --> N{레코드 존재?}
    N -->|없음| E["빈 배열<br/>completeness: unknown"]
    N -->|있음| TG["ingredient_id 정리<br/>(태그는 ④가 붙임)"]
    TG --> EX["만료 표시<br/>(confirmed_at 3개월 경과 → expired)"]
    EX --> A{"flagged_anomaly 또는 expired?"}
    A -->|Yes| MARK["override_eligible: false<br/>드롭 금지, 플래그와 함께 반환"]
    A -->|No| OK["override_eligible: true"]
    MARK --> C[캐시 대조]
    OK --> C
    C --> CP{"캐시 존재 AND 전 재료 확인<br/>AND anomaly·만료 0건?"}
    CP -->|Yes| COMP["completeness: complete"]
    CP -->|No| UNK["completeness: unknown"]
    COMP --> OUT[Supervisor에 반환]
    UNK --> OUT
    E --> OUT
```

### 2-1. 함수 시그니처

```python
def query_exact_confirmations(
    request: ExactFeedbackRequest,                  # context.store_id 필수·양수. None/0 이하 → StoreIdRequiredError
) -> ExactFeedbackResponse:
    """context.store_id + menu.menu_id 스코프의 재료 단위 확인 레코드를 조회해 그대로 반환.
    판단·확장·라우팅 없음."""


def check_completeness(
    confirmations: list[Confirmation],
    store_id: int,
    menu_id: str,
) -> Literal["complete", "unknown"]:
    """menu_ingredient_cache가 있고, 전 재료가 확인됐고, anomaly·만료가 0건일 때만 complete.
    캐시가 없으면 항상 unknown (④ 호출 유도)."""


def resolve_override_eligibility(c: Confirmation, scope: str) -> bool:
    """anomaly이거나, 만료(expired)됐거나, inherited 스코프면 False.
    False인 재료는 드롭하지 않고 ⑤로 전달한다."""
```

### 2-2. 의사코드

```
store_id = request.context.store_id
menu_id = request.menu.menu_id
scope_hint = request.scope_hint
warnings = list(request.warnings)                    # 입력 신호 보존

if store_id is None or store_id <= 0:
    raise StoreIdRequiredError

rows = db.query("ingredient_confirmations",
                store_id=store_id,          # 스코프 필수
                menu_id=menu_id)

confirmations = [to_confirmation(r) for r in rows]   # anomaly 포함, 드롭 금지

for c in confirmations:
    c.constraint_tags = []                           # 태그는 ④가 붙인다 (④ §2-7)

for c in confirmations:                              # §1-7. 드롭하지 않고 표기만
    c.expired = c.confirmed_at < now() - months(3)

for c in confirmations:
    c.override_eligible = resolve_override_eligibility(c, scope_hint)

cached = db.get_menu_ingredient_cache(store_id, menu_id)
completeness = (
    "complete"
    if cached
       and all(c.status != "unknown" for c in cover(cached, confirmations))
       and all(not c.flagged_anomaly and not c.expired for c in confirmations)
    else "unknown"
)

return ExactFeedbackResponse(
    context             = request.context,          # 수정 없이 그대로
    menu_id             = menu_id,
    confirmation_scope  = scope_hint,
    completeness        = completeness,
    confirmations       = confirmations,            # evidence.observed_at = DB confirmed_at
    warnings            = warnings,
    errors              = list(request.errors),
)
```

> **`complete` 조건에 anomaly 0건이 포함되는 이유**: anomaly는 "확인은 됐지만 믿을 수 없는 확인"이다. 이를 `complete`로 처리하면 ④⑤가 스킵되고 모순된 값이 그대로 판정에 반영된다.

---

## 3. Supervisor와의 계약 (Contract)

**호출 시점**: ② Menu Normalization Agent 직후, ④보다 **먼저**. 가벼운 경로 조기 종료를 위해.

### 3-1. 호출 횟수: 최대 2회 — ③은 자신이 몇 번째 호출인지 모른다

| 회차 | 인자 | 시점 | 결과 사용 |
|---|---|---|---|
| 1차 | `menu_id`=대상 메뉴, `scope_hint="exact"` | ④ 이전 | `complete`면 ④⑤ 스킵하고 ⑧ 직행 |
| 2차 | `menu_id`=`base_menu_id`, `scope_hint="inherited"` | ④가 `base_menu_id`를 반환한 **이후** | **보류** — 결과를 ⑤ prior 보정에 쓰지 않는다 (10/10 회의 결정, §6) |

> 2차 호출이 필요한 이유: `base_menu_id`는 ④가 DB 컬럼을 읽거나 longest-match로 판별한 결과다. ③에 변형 판정을 넣으면 순서 모순이 발생하므로, 상속은 **Supervisor의 재호출**로 처리한다.

### 3-2. 상속 범위: `base_menu_id` 단일 메뉴만 (확정)

형제 변형(해물된장찌개 등)은 상속 대상이 **아니다.**

- base(된장찌개)는 변형(차돌된장찌개)의 **부분집합**이므로 상속 근거가 성립한다.
- 형제(해물된장찌개)는 부분집합 관계가 아니다. 해물된장찌개의 `새우: present`가 차돌된장찌개로 넘어오면 오판이다.
- 형제 상속을 하려면 "공통 재료 vs 변형 고유 재료" 필터가 필요한데, 그것은 사실상 base를 다시 계산하는 것이다.

### 3-3. ③ → Supervisor 반환값별 후속 판단

| 반환 | Supervisor 동작 |
|---|---|
| `completeness: complete` | ④⑤ 스킵 → ⑧ 직행 |
| `confirmations` 일부 존재 | ④ 호출 시 `confirmed_ingredients`에 **`status != unknown` AND `override_eligible: true`** 인 재료만 전달 |
| `confirmations` 빈 배열 | ④ 호출 (`confirmed_ingredients=[]`) |
| `override_eligible: false` 포함 | 해당 재료를 override에서 제외하고 **⑤ 계산 대상에 포함** |
| `confirmation_scope: inherited` | **override 금지.** 쓰임은 보류 (§6) |

**③이 직접 호출하지 않는 것**: 어떤 Agent·Tool도 호출하지 않는다. 조회 후 반환하고 종료.

---

## 4. 케이스별 관여 범위 / 예외

### 4-1. 원본 시나리오별 동작

| 케이스 | ③의 동작 | 반환 |
|---|---|---|
| **1) DB 존재 + 피드백 없음** | 조회 → 레코드 없음 | 빈 배열, `unknown` |
| **2) DB 존재 + 피드백 일부** | 해당 재료만 반환 | 부분 맵, `unknown` |
| **2-b) 피드백 전부 (anomaly 없음)** | 캐시 대조 후 완전 확인 | `complete` → ④⑤ 스킵 |
| **2-c) 피드백 전부 (anomaly 포함)** | 캐시 대조하되 `complete` 불가 | `unknown` → ④⑤ 정상 진행 |
| **3) 변형 메뉴** | 1차: 변형 menu_id / 2차: `base_menu_id` | `exact` + `inherited` 2건 |
| **4) DB에 없는 unknown 메뉴** | **호출되지 않음.** `menu_id`가 없어 Supervisor가 ④ unknown 경로로 바로 보낸다 (§1-1) | — |
| **5) 웹서치까지 실패 (엣지)** | **관여 없음** | 4번과 같이 호출되지 않음 |

### 4-2. 예외 처리

| 상황 | 처리 |
|---|---|
| `context.store_id` 누락/null/0 이하 | `StoreIdRequiredError`. **전역 조회 fallback 금지** |
| `menu_id`가 DB에 없음 | 빈 배열 + `unknown` 반환. 예외 발생시키지 않음 |
| `flagged_anomaly: true` | **드롭 금지.** `override_eligible: false`로 표기해 반환 → ⑤ 계산 대상 |
| 같은 재료의 확정값이 여러 개 | 생기지 않는다. 확정값 표(`ingredient_confirmations`)는 가게·메뉴·재료당 1행이다 (UNIQUE). 1·2·3차 사장님 답변은 질문·답변 기록(`owner_verification_requests`)에 따로 쌓이고, 확정 규칙은 ⑦이 센다 (⑦ §2-3, #167). ③은 확정값 표만 읽는다 |
| 확인 후 3개월 경과 | **드롭 금지.** `expired: true`, `override_eligible: false`로 반환 → ⑤ 계산 대상 (§1-7) |
| `menu_ingredient_cache` 없음 | `completeness: unknown` 강제 → ④ 호출 유도 |
| 2차 호출인데 `base_menu_id`가 null | Supervisor가 호출하지 않음. 도달 시 빈 배열 반환 |
| 캐시와 실제 재료 목록 불일치 | 캐시 무효로 간주 → `unknown` 반환 (안전 방향) |

---

## 5. 테스트 케이스

| # | 입력 | 기대 동작 | 검증 포인트 |
|---|---|---|---|
| 1 | 확인 레코드 0건 | 빈 배열 + `unknown` | 에러 발생하지 않을 것 |
| 2 | 5개 중 2개만 확인 | 2건 반환 + `unknown` | **메뉴 단위 이분법 없을 것** (부분 확인 지원) |
| 3 | `status: absent` 레코드 | `absent`로 반환 | `unknown`과 구분될 것 (3값 검증) |
| 4 | 차돌된장찌개 1차 호출 | 빈 배열 + `scope: exact` | base 정보를 ③이 스스로 끌어오지 않을 것 |
| 5 | 된장찌개 2차 호출 | `scope: inherited`, `override_eligible: false` | Supervisor가 override로 쓰지 않을 것 |
| 6 | `context.store_id` 누락 | 즉시 에러 | 전역 fallback 없을 것 |
| 7 | 돈까스 `돼지고기: absent` (anomaly) | `override_eligible: false` + 재료 유지 | **드롭도 자동채택도 하지 않을 것**, ⑤로 전달될 것 |
| 8 | 전 재료 확인 + anomaly 1건 | `completeness: unknown` | anomaly가 `complete`를 막을 것 |
| 9 | 캐시 존재 + 전 재료 확인 + anomaly 0 | `complete` | ④⑤ 스킵 경로 작동할 것 |
| 10 | 4개월 전 `돼지고기: absent` | `expired: true`, `override_eligible: false` + 재료 유지 | override되지 않고 ⑤로 전달될 것 |
| 11 | 캐시 존재 + 전 재료 확인 + 그중 1건 만료 | `completeness: unknown` | 만료값이 `complete`를 막을 것 |
| 12 | 돼지고기 `status: present` 확인값 | `ingredient_id` 정확히 반환, `constraint_tags: []` | 태그를 ③이 붙이지 않을 것. Supervisor가 ④ 태그 조회 후 ⑧이 `is_pork`로 대조할 것 (⓪ 테스트 12) |
| 13 | 김치찌개 `땅콩: present` + `flagged_anomaly: false` (레시피 확률 낮음) | `override_eligible: true` | "있음" 답변은 anomaly가 아니므로 override가 거부되지 않을 것 |
| 14 | 일부만 확인된 메뉴(④⑤ 경로) + 돼지고기 `status: present` | ③은 `constraint_tags: []`로 반환, Supervisor가 ④ `lookup_tags`로 `is_pork`를 채운 뒤 ⑧에 전달 | ④⑤를 거치는 경로에서도 확인값 태그가 빠지지 않을 것 (§0) |

---

## 6. 미확정 항목 (팀 확인 대기)

- [x] **확인 정보 유효기간** — 3개월로 결정 (2026-10-06 회의, §1-7)
- [x] **3개월 세는 기준** — 등록(확인)한 날짜부터 달력 기준 3개월 (2026-10-10 회의, §1-7)
- [x] **만료된 재료 재질문 경로** — ⑧이 만료된 확인값(`expired: true`) 중 사용자 태그와 관련 있는 재료를 다시 묻는 질문 카드를 만든다(이유 `expired`). ⓪ §4-7이 ⑧에 `expired`를 넘겨야 한다 (2026-10-12, ⑧ §2-1 7단계)
- [x] **`menu_ingredient_cache` 무효화 시점** — ④ 방식을 따른다 (④ §2-1 6단계): 캐시는 `menus.knowledge_revision`이 같을 때만 유효하고, 메뉴의 레시피·온톨로지가 바뀌면(웹 레시피 DB 등록, 크롤링 데이터 재적재, 숨은 재료 규칙 추가 등) 리비전이 올라가 자동 무효화된다. 사장님 확인값은 캐시에 넣지 않으므로 새 확인값이 들어와도 무효화하지 않는다. 재료 단위 변경 시 리비전을 올리는 방식은 ④ §8 (#169)
- [ ] **2차 호출(`inherited`) 보류** — 10/10 회의에서 사장님 답변은 확률 계산에 쓰지 않기로 해서, 기본 메뉴 답변을 ⑤ prior 보정에 넘기는 2차 호출(§1-5, §3-1, §3-3)은 쓸 곳이 없어짐. 유지·삭제 여부는 보류
- [ ] **④⑤ 경로에서 확인값 태그 채우기 (외부 의존)** — ⓪ §2-11은 ③만 거치는 경로에서만 `tag_lookup`을 부르고, ④ §2-7도 그 경로만 적어 둠. 일부만 확인된 메뉴는 ④가 확인 재료를 빼고 반환하므로 확인값 태그가 비어, 할랄 사용자에게 돼지고기 `present`가 DANGER로 걸리지 않는다. 캐시 없이 전부 확인된 메뉴는 확률 결과가 비어 SAFE까지 나갈 수 있다 (FN). ⓪·④ 담당자에게 ③ 확인값이 ⑧로 가는 모든 경로에서 `lookup_tags`를 부르도록 요청
- [x] **`completeness: partial` 기준** — `partial`은 쓰지 않고 `complete | unknown` 두 값만 쓴다. ⓪·④·⑤는 "complete인지 아닌지"로만 경로를 나누어 `partial`과 `unknown`의 차이가 어디에도 쓰이지 않기 때문이다. ⓪ §4-2의 값 목록과 예시에서 `partial`을 지운다 (2026-10-12)
- [x] **anomaly 판정 기준** — 레시피 확률(⑤ `posterior_mean`) 0.9 이상 + "없음" 답변이면 anomaly. "있음"은 대상 아님 (2026-10-06, #193, ⑦ §2-3, §1-6)

## 7. 확정된 결정 (변경 금지)

- **anomaly 정책**: override 거부, CAUTION 이상 강제 유지 (FN-minimization 우선) — 팀 확정
- **anomaly 판정 기준**: 레시피 확률 0.9 이상 + "없음" 답변. "있음"은 anomaly 아님. 표시는 ⑦이 붙인다 (#193)
- **상속 범위**: `base_menu_id` 단일 메뉴만. 형제 변형 제외
- **`status` 3값**: `present` / `absent` / `unknown`
- **`menu_ingredient_cache`**: 생성하기로 확정. 무효화는 ④ 방식을 따른다 — 메뉴 지식 리비전(`menus.knowledge_revision`)이 바뀌면 자동 무효화, 사장님 확인값으로는 무효화하지 않음 (2026-10-11, ④ §2-1)
- **확정 근거는 사장님 답변만**: 관광객이 직접 알려주는 재료 정보(`user_hard`)는 수집 경로가 없어 쓰지 않는다 (2026-10-09, #214)
- **확정값은 재료당 1행**: 1·2·3차 답변은 질문·답변 기록에 따로 쌓이고 확정 규칙은 ⑦이 센다. ③은 확정값 표만 읽는다 (#167)
- **태그는 ④만 붙인다**: ③은 `constraint_tags: []`로 반환하고 Supervisor가 ④ `lookup_tags`로 채운다 (2026-10-10 회의)
- **확인값 유효기간 3개월**: 등록(확인)한 날짜부터 달력 기준 3개월. 만료되면 override 거부, 드롭 금지 (2026-10-06·10-10 회의)
