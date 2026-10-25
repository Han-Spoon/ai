# ai_ontology — ④ DB / Ontology Tool

스펙: [`docs/4-tool-DBontology.md`](../docs/4-tool-DBontology.md). 입출력 형식은 [`ai_supervisor/contracts/ontology.py`](../ai_supervisor/contracts/ontology.py)를 그대로 쓴다.

판정 중에는 DB를 조회하지 않고, 백엔드가 넘긴 지식 묶음(KnowledgeBundle)만 읽는다. LLM은 쓰지 않는다.

| 진입점 | 상태 | 하는 일 |
|---|---|---|
| `TagLookupTool` (`tag_lookup` node) | 구현됨 | ⑧ 바로 앞에서 ③ 확인 재료의 위험 태그를 채운다. 하위 재료(김치 → 멸치액젓 → 멸치)의 태그까지 합친다 |
| `query_ontology` (`ontology` node) | 예정 | 메뉴 조회, 변형 판별, 재귀 확장, 관측값, `role`, 캐시 명령 |

태그 조회에 실패하면 빈 배열로 끝내지 않는다. `constraint_tags_lookup_failed` 경고(SAFE 금지)를 붙인다.
