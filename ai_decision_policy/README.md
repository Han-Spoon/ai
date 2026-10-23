# ⑧ Decision Policy / XAI Agent — 판정 규칙

사용자 제한과 재료 근거를 대조해 DANGER / CAUTION / SAFE를 정합니다. 규칙은 [`docs/8-agent-xai.md`](../docs/8-agent-xai.md) §2·§3이 기준입니다.

- 판정은 코드(규칙)로만 정합니다. LLM은 쓰지 않습니다. 같은 입력이면 항상 같은 판정입니다.
- DANGER·SAFE는 사장님 확인값으로만 나옵니다. 확률은 아무리 높거나 낮아도 CAUTION입니다.

## 파일

```text
ai_decision_policy/
  profile.py   # 사용자 프로필 → 금지 태그 (§2-1 1단계)
  flags.py     # 메뉴 애매함 플래그와 사장님 답 (§1-1 표, §2-1 5-2)
  policy.py    # 판정 (§2-1 2~5단계, §2-2 SAFE 금지 조건), 고위험 재검증 표시 (§2-3)
  tests/test_decision_policy.py  # 문서 §7 중 판정·재검증 표시 부분
```

## 판정 순서

| 순서 | 조건 | 결과 |
|---|---|---|
| 1 | 사장님 확인값으로 금지 재료가 "있음", 또는 플래그 답이 "있음"이고 물은 태그가 모두 금지 태그 | `danger` / `confirmed` |
| 2 | 정보 없음(`no_information`), 또는 판정할 재료가 0개 | `caution` / `unknown` |
| 3 | 확률·변형 추정 재료가 있음 | `caution` / `estimated` |
| 4 | 재료는 모두 사장님 확인값인데 정보가 빠짐 (`information_status`, 경고, 가게 없는 스캔, 풀리지 않은 플래그) | `caution` / `unknown` |
| 5 | 재료는 모두 사장님 확인값인데 매운맛 등 메뉴 속성이 겹침 | `caution` / `estimated` |
| 6 | 그 밖 | `safe` / `confirmed` |

같은 재료에 쓸 수 있는 사장님 확인값(`override_eligible: true`)이 있으면 확률보다 확인값을 씁니다 (§6).

입력은 ⓪ §4-7 `DecisionRequest`(`ai_supervisor/contracts/decision.py`)이고, 위험 재료와 재검증 표시는 같은 파일의 `DecisionRiskIngredient`·`HighRiskReview`로 냅니다.

## 고위험 재검증 표시 (§2-3)

가게 없는 스캔이면 항상 `required: false`입니다. 아래 신호가 여럿이면 위에 있는 것을 `reason_code`로 씁니다.

| `reason_code` | 신호 | 이 사용자와 관련 있을 때만 |
|---|---|---|
| `anomaly` | `anomaly_locked` 재료 | 그 재료 태그가 금지 태그와 겹칠 때 |
| `unknown_tag` | ④ 경고 `unmapped_constraint_tags`, `constraint_tags_lookup_failed` | 항상 (겹치는지 알 수 없음) |
| `no_information` | `no_information`, 또는 재료 0개 | 금지 태그가 하나라도 있을 때 |
| `partial_information` | `information_status`가 `complete`가 아님 | 금지 태그가 하나라도 있을 때 |
| `insufficient_web_sources` | ⑥ 경고 `insufficient_sources`, `web_search_deadline_exceeded`, `web_llm_extraction_failed` | 금지 태그가 하나라도 있을 때 |

## 아직 없는 것

- `DecisionResponse`로 감싸는 `run()`: 결과 문장(`message.verdict`)이 꼭 있어야 해서 문장이 정해진 뒤에 만듭니다
- 결과 문장(`message`), 사장님 질문 카드(`owner_cards`)

## 테스트

```bash
pytest -q ai_decision_policy
```
