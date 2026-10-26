# ai_ontology — ④ DB / Ontology Tool

스펙: [`docs/4-tool-DBontology.md`](../docs/4-tool-DBontology.md). 입출력 형식은 [`ai_supervisor/contracts/ontology.py`](../ai_supervisor/contracts/ontology.py)를 그대로 쓴다.

판정 중에는 DB를 조회하지 않고, 백엔드가 넘긴 지식 묶음(KnowledgeBundle)만 읽는다. LLM은 쓰지 않는다.

| 진입점 | 상태 | 하는 일 |
|---|---|---|
| `IngredientMappingTool` / `map_ingredients` (`ingredient_mapping` node) | 구현됨 | ⑥ 웹 재료 이름을 표준 재료·태그로 바꾼다. 원문 → `crawling/normalize_ingredients.py` 정제·대표명 순서로 이름·별칭과 비교. 못 찾은 이름은 `ingredient_id: null`로 남기고 `unmapped_constraint_tags` 경고 |
| `TagLookupTool` (`tag_lookup` node) | 구현됨 | ⑧ 바로 앞에서 ③ 확인 재료의 위험 태그를 채운다. 하위 재료(김치 → 멸치액젓 → 멸치)의 태그까지 합친다 |
| `OntologyTool` / `query_ontology` (`ontology` node) | 구현됨 | 메뉴 조회, 변형 판별(DB 컬럼 우선, 없으면 후보 이름을 뺀 나머지 토큰), 재귀 확장, 태그, 관측값, `role`, 캐시 명령 |

태그 조회에 실패하면 빈 배열로 끝내지 않는다. `constraint_tags_lookup_failed` 경고(SAFE 금지)를 붙인다.

메뉴 조회에서 정한 것 (문서와 다르거나 문서에 없던 부분):

- 캐시가 유효해도 다시 펼친다. 재료 그래프가 작아 비용이 거의 없고, 캐시에는 경로 정보가 없어 `absent` 재료 아래 경로를 정확히 뺄 수 없다. 유효한 캐시가 있으면 저장 명령만 만들지 않는다.
- 같은 재료가 여러 경로로 나오면 하나로 합친다. 가장 얕은 위치를 쓰고 태그는 모든 경로의 합집합이다.
- 신규 변형(`runtime_tagged`)은 캐시 저장 명령을 만들지 않는다. 검토 전 재료가 원본 메뉴 캐시에 섞이면 안 된다.
- 후보를 고르지 못한 경로(`fallback_ontology`)는 웹 검색으로 보낸다. 묶음에 원본 메뉴 이름이 없어 나머지 토큰을 자를 수 없다.
