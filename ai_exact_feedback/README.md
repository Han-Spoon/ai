# ③ Exact Feedback Tool

사장님이 확인해 준 재료 정보(확인값)를 메뉴 하나 단위로 읽어, 재료마다 판정에 그대로 써도 되는지 표시하고 메뉴 재료가 전부 확인됐는지 알려 줍니다. 규칙은 [`docs/3-tool-exact.md`](../docs/3-tool-exact.md)가 기준입니다.

- DB를 부르지 않습니다. 백엔드가 Judge 요청에 담아 준 자료만 읽습니다.
- LLM을 쓰지 않고, 다른 Agent·Tool도 부르지 않습니다.
- 재료 태그(`constraint_tags`)는 붙이지 않습니다. ④가 붙입니다.

## 파일

```text
ai_exact_feedback/
  tool.py                       # 만료·덮어쓰기 가능 여부·completeness 계산
  tests/test_exact_feedback.py  # 문서 §5 테스트
```

## 지키는 규칙

| 규칙 | 문서 |
|---|---|
| `store_id`가 없거나 0 이하면 `StoreIdRequiredError` | §1-1 |
| 이상 답변(`flagged_anomaly`)·만료 재료는 `override_eligible: false`로 표시하되 목록에서 빼지 않음 | §1-6 |
| 확인한 한국 날짜부터 달력 기준 3개월 지나면 만료. 월말에 같은 날이 없으면 그 달 마지막 날로 당김 | §1-7 |
| 백엔드가 보낸 `expired`·`override_eligible`은 쓰지 않고 다시 계산 | §1-2 |
| 캐시가 있고, 캐시 재료가 전부 확인됐고, 이상 답변·만료가 0건일 때만 `complete`. 애매하면 `unknown` | §2 |

요청·응답 틀(`ExactFeedbackRequest`·`ExactFeedbackResponse`)과 이를 받는 `query_exact_confirmations()`는 ⓪ 계약 모델이 생기면 추가합니다.

## 테스트

```bash
pytest -q ai_exact_feedback
```
