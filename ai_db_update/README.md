# ai_db_update — ⑦ DB Update Tool

스펙: [`docs/7-tool-dbupdate.md`](../docs/7-tool-dbupdate.md). 입출력 형식은 [`ai_supervisor/contracts/db_update.py`](../ai_supervisor/contracts/db_update.py)를 그대로 쓴다.

Supervisor가 보낸 저장할 일(`operations`)마다 저장 명령(`commands`)을 하나씩 만든다. **DB에 쓰지 않는다.** 권한·FK·중복 확인과 실제 쓰기는 백엔드가 한다 (AGENTS.md).

| 진입점 | 하는 일 |
|---|---|
| `DbUpdateTool` / `build_commands` (`db_update` node) | 입력 payload를 그대로 두고 출처 기록(`provenance`)을 붙여, 내용 기준 중복 방지 키와 함께 반환 |

- 중복 방지 키: `"sha256:" + sha256(action + "|" + 내용 키 정규 JSON)`. 시각·`trace_id`·`operation_id`는 넣지 않아 같은 스캔을 다시 돌려도 같은 키다. 배열(출처 URL, 물어본 태그)은 정렬한다. 내용 키와 출처 고정값은 ⓪ §4-6 표.
- 사장님 답(`record_owner_answer`)은 질문 ID가 키다. 답 한 번으로 백엔드가 바로 확정하고, 이상 답변 표시는 질문 때 저장한 확률로 백엔드가 계산한다 (⑦ §2-3).
- 출처 신뢰도 갱신(`update_source_reliability`)은 받은 증가분으로 명령만 만든다. 증가분을 계산하는 비교 로직(⑦ §3-1)은 회의 결정 전이라 구현하지 않았다.
