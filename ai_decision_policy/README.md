# ⑧ Decision Policy / XAI Agent — 판정 규칙

사용자 제한과 재료 근거를 대조해 DANGER / CAUTION / SAFE를 정합니다. 규칙은 [`docs/8-agent-xai.md`](../docs/8-agent-xai.md) §2·§3이 기준입니다.

- 판정은 코드(규칙)로만 정합니다. LLM은 쓰지 않습니다. 같은 입력이면 항상 같은 판정입니다.
- DANGER·SAFE는 사장님 확인값으로만 나옵니다. 확률은 아무리 높거나 낮아도 CAUTION입니다.

## 파일

```text
ai_decision_policy/
  profile.py   # 사용자 프로필 → 금지 태그 (§2-1 1단계)
  flags.py     # 메뉴 애매함 플래그와 사장님 답 (§1-1 표, §2-1 5-2)
  policy.py    # 판정 (§2-1 2~5단계, §2-2 SAFE 금지 조건)
  tests/test_decision_policy.py  # 문서 §7 중 판정 부분
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

## 아직 없는 것

- 요청·응답 틀(`DecisionRequest`·`DecisionResponse`, ⓪ §4-7). 지금은 `policy.py`의 입력·결과 상자를 씁니다
- 결과 문장(`message`), 사장님 질문 카드(`owner_cards`), 고위험 재검증 표시(`high_risk_review`)

## 테스트

```bash
pytest -q ai_decision_policy
```
