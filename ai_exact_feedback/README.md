# ③ Exact Feedback Tool

사장님이 확인해 준 재료 정보(확인값)를 메뉴 하나 단위로 읽어, 재료마다 판정에 그대로 써도 되는지 표시하고 메뉴 재료가 전부 확인됐는지 알려 줍니다. 규칙은 [`docs/3-tool-exact.md`](../docs/3-tool-exact.md)가 기준입니다.

- DB를 부르지 않습니다. 백엔드가 Judge 요청에 담아 준 자료만 읽습니다.
- LLM을 쓰지 않고, 다른 Agent·Tool도 부르지 않습니다.
- 재료 태그(`constraint_tags`)는 붙이지 않습니다. ④가 붙입니다.

## 파일

```text
ai_exact_feedback/
  tool.py                       # 덮어쓰기 가능 여부·completeness 계산, ExactFeedbackTool
  tests/test_exact_feedback.py  # 문서 §5 테스트
  tests/test_tool.py            # 요청·응답 틀로 감싼 run() 테스트
```

## 쓰는 법

```python
from ai_exact_feedback import ExactFeedbackTool

tool = ExactFeedbackTool(task.knowledge)   # 같은 메뉴 칸의 ItemTaskKnowledge (⓪ §2-6)
response = await tool.run(request)         # ExactFeedbackRequest → ExactFeedbackResponse
```

요청·응답 틀은 ⓪ §4-2를 구현한 `ai_supervisor/contracts/exact.py`를 씁니다. ③ 요청에는 확인값 자료가 없으므로, Supervisor가 그 메뉴 칸의 자료(`ItemTask.knowledge`, 고른 메뉴의 자료만 든 `ItemTaskKnowledge`)를 도구를 만들 때 넘깁니다. 다른 메뉴 칸·다른 메뉴의 자료가 들어오면 조용히 넘어가지 않고 에러를 냅니다.

## 지키는 규칙

| 규칙 | 문서 |
|---|---|
| `store_id`가 없거나 0 이하면 `StoreIdRequiredError` | §1-1 |
| 이상 답변(`flagged_anomaly`) 재료는 `override_eligible: false`로 표시하되 목록에서 빼지 않음 | §1-6 |
| 사장님 답은 기간 만료 없이 씀. 오래된 답도 다시 묻거나 막지 않음. `expired`는 ⓪ 계약 코드에서 빠질 때까지 항상 `false` | §1-7 |
| 백엔드가 보낸 `expired`·`override_eligible`은 쓰지 않고 다시 계산 | §1-2 |
| 캐시가 있고, 캐시 재료가 전부 확인됐고, 이상 답변이 0건일 때만 `complete`. 애매하면 `unknown` | §2 |
| `context`·`warnings`·`errors`는 받은 그대로 돌려줌. `scope_hint`는 `confirmation_scope`로 그대로 | §1-1 |

## 테스트

```bash
pytest -q ai_exact_feedback
```
