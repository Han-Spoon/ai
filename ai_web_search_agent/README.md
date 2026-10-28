# ⑥ Web Search Agent

DB에 없는 메뉴의 재료를 웹 레시피에서 모읍니다. 기준은 [`docs/6-agent-websearch.md`](../docs/6-agent-websearch.md)와 ⓪ §4-5 두 단계 형식(`ai_supervisor/contracts/web_search.py`)입니다.

- ⑥은 **모으고 세기만** 합니다. 재료 태그는 ④가 붙이고, 저장은 백엔드가 ⑦ 명령으로 합니다. ⑥ 안에는 DB 연결이 없습니다.
- 웹 결과는 미검증 정보(soft evidence)입니다. 판정은 CAUTION까지이고, DB 등록은 관리자가 통과시킨 레시피가 30개 이상일 때 백엔드가 합니다.

## 지금 있는 것

| 파일 | 내용 |
|---|---|
| `collect.py` | **1단계(수집)** `await WebCollectAgent(provider, reader).run(WebCollectRequest)` → `WebCollectResponse`. 우선 사이트 3곳을 `next_rank`부터 읽고, 이 메뉴의 레시피로 남은 글에서 재료 이름을 사전(규칙)과 LLM으로 뽑아 합친다(LLM 이름은 본문에 있을 때만). 빠진 글만큼 다음 결과를 더 읽어 유효 레시피 10개를 채운다. 시간 제한 7초(검색 3초·LLM 3초), 캐시 URL은 다시 세지 않음. LLM이 없거나 실패하면 모든 글을 남기고 사전 결과만 쓴 뒤 `web_llm_extraction_failed`. 새 레시피도 캐시도 없으면 `found: false` + `web_search_timeout` |
| `aggregate.py` | **2단계(집계)** `await WebAggregateAgent().run(WebAggregateRequest)` → `WebAggregateResponse`. ④가 표준 재료로 맞춘 레시피를 재료·사이트별로 세고(`observations`), 유효 레시피가 10개 미만이면 `insufficient_sources` 경고, 관리자 검토 문구(`review_notes`) 규칙 1·2·3·4·6·7 |
| `reader.py` | 1단계의 **LLM** 쪽. `OpenAIRecipeReader`가 검색 결과 묶음을 한 번에 읽어 글마다 "이 메뉴의 레시피인지"와 적힌 재료 이름을 돌려준다. 애매하면 남긴다. 답이 빠지거나 형식이 틀리면 예외 → `collect.py`가 전부 남기고 `web_llm_extraction_failed`. `OpenAIRecipeReader.from_env_if_configured()`는 `OPENAI_API_KEY`가 없으면 `None` (환경 변수는 `.env.example`) |
| `providers.py` | 검색 도구 틀(`SearchProvider.search(query, start, count, sites)`: 한 검색 순위 목록의 `start`위부터 `count`개)과 테스트용 `StaticSearchProvider` |
| `dictionary.py` | 1단계 재료 뽑기의 **규칙** 쪽. `find_ingredient_names(text)`가 재료 사전에 있는 이름을 글에 적힌 그대로 찾는다 (긴 이름 우선, 한 글자 재료는 단어 전체일 때만). 표준 재료로 맞추는 건 ④ |
| `data/ingredient_dictionary.csv` | 재료 사전 (`surface`, `canonical_name`). 크롤링 표준 재료 324개 + 크롤링 레시피에서 두 번 이상 나온 원문 이름. `python -m ai_web_search_agent.build_dictionary`로 다시 만든다 |

## 아직 없는 것

- **일반 웹으로 넓히기** (⑥ §2-3-1): 우선 사이트에서 10개를 못 채울 때. 그때 `next_rank`가 어느 검색의 순위인지(⑥ §5)를 정한 뒤 붙입니다. 지금은 우선 사이트만 읽습니다
- **네이버 검색 연결** (#254). 이게 없으면 읽을 검색 결과가 없어 LLM도 쓰이지 않습니다
- **⓪ 조립**: `app.py` `_build_supervisor_service()`의 `web_collect=WebCollectAgent(StaticSearchProvider())`를 `WebCollectAgent(네이버_provider, OpenAIRecipeReader.from_env_if_configured())`로 바꾸는 일 (⑥ §6)
- **LLM 검토 문구**: 검토 문구 다듬기, 검토 규칙 5(LLM만 찾은 재료)

옛 코드(룰엔진 결과에 웹 태그를 덧붙이고 SQLite에 저장하던 구조, Tavily 검색, CLI)는 새 설계와 맞지 않아 지웠습니다.

## 테스트

```bash
pytest -q ai_web_search_agent
```
