# ⑥ Web Search Agent

DB에 없는 메뉴의 재료를 웹 레시피에서 모읍니다. 기준은 [`docs/6-agent-websearch.md`](../docs/6-agent-websearch.md)와 ⓪ §4-5 두 단계 형식(`ai_supervisor/contracts/web_search.py`)입니다.

- ⑥은 **모으고 세기만** 합니다. 재료 태그는 ④가 붙이고, 저장은 백엔드가 ⑦ 명령으로 합니다. ⑥ 안에는 DB 연결이 없습니다.
- 웹 결과는 미검증 정보(soft evidence)입니다. 판정은 CAUTION까지이고, DB 등록은 관리자가 통과시킨 레시피가 30개 이상일 때 백엔드가 합니다.

## 지금 있는 것

| 파일 | 내용 |
|---|---|
| `aggregate.py` | **2단계(집계)** `await WebAggregateAgent().run(WebAggregateRequest)` → `WebAggregateResponse`. ④가 표준 재료로 맞춘 레시피를 재료·사이트별로 세고(`observations`), 유효 레시피가 10개 미만이면 `insufficient_sources` 경고, 관리자 검토 문구(`review_notes`) 규칙 1·2·3·4·6·7 |
| `providers.py` | 검색 도구 틀(`SearchProvider`)과 테스트용 `StaticSearchProvider` |

## 아직 없는 것

- **1단계(수집)**: 검색 → 이 메뉴의 레시피 글인지 판별 → 재료 뽑기 (⑥ §3). 재료 사전 출처와 일반 웹으로 넓힐 때의 `next_rank` 기준(⑥ §5)을 정한 뒤 만듭니다
- **네이버 검색 연결** (#254)
- **LLM**: 레시피 판별, 사전에 없는 재료 찾기, 검토 문구 다듬기, 검토 규칙 5(LLM만 찾은 재료)

옛 코드(룰엔진 결과에 웹 태그를 덧붙이고 SQLite에 저장하던 구조, Tavily 검색, CLI)는 새 설계와 맞지 않아 지웠습니다.

## 테스트

```bash
pytest -q ai_web_search_agent
```
