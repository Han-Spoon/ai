# ai_db_update — ⑦ DB Update Tool

스펙: [`docs/7-tool-dbupdate.md`](../docs/7-tool-dbupdate.md). 입출력 형식은 [`ai_supervisor/contracts/db_update.py`](../ai_supervisor/contracts/db_update.py)를 그대로 쓴다.

Supervisor가 보낸 저장할 일(`operations`)마다 저장 명령(`commands`)을 하나씩 만든다. **DB에 쓰지 않는다.** 권한·FK·중복 확인과 실제 쓰기는 백엔드가 한다 (AGENTS.md).

| 진입점 | 하는 일 |
|---|---|
| `DbUpdateTool` / `build_commands` (`db_update` node) | 입력 payload를 그대로 두고 출처 기록(`provenance`)을 붙여, 내용 기준 중복 방지 키와 함께 반환 |

- 중복 방지 키: `"sha256:" + sha256(action + "|" + 내용 키 정규 JSON)`. 웹 캐시는 메뉴 이름 + 정렬한 새 출처 주소 목록이다 (검색 순위 폐기, 10/27). 시각·`trace_id`·`operation_id`는 넣지 않아 같은 스캔을 다시 돌려도 같은 키다. 배열(출처 URL, 물어본 태그)은 정렬한다. 내용 키와 출처 고정값은 ⓪ §4-6 표.
- 사장님 답(`record_owner_answer`)은 질문 ID가 키다. 답 한 번으로 백엔드가 바로 확정한다. 이상 답변 잠금은 폐기됐다 (10/27 회의).
- 출처 신뢰도(⑦ §3-1, 10/26 회의에서 유지로 결정): `claims_for`가 재료의 출처별 주장(사이트별 관측값, 큐레이션, 웹)을 만들고, `reliability_updates`가 사장님 답과 비교해 출처별 증가분 payload를 만든다. 모든 답을 정답으로 쓴다 (이상 답변 잠금 폐기). 답이 들어올 때 주장을 넘기는 계약 연결은 아직 정해야 한다.
