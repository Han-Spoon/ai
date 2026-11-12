# ⑥ Web Search Agent

DB에 없는 메뉴의 재료를 웹 레시피에서 모읍니다. 기준은 [`docs/6-agent-websearch.md`](../docs/6-agent-websearch.md)와 ⓪ §4-5 두 단계 형식(`ai_supervisor/contracts/web_search.py`)입니다.

- ⑥은 **모으고 세기만** 합니다. 재료 태그는 ④가 붙이고, 저장은 백엔드가 ⑦ 명령으로 합니다. ⑥ 안에는 DB 연결이 없습니다.
- 웹 결과는 미검증 정보(soft evidence)입니다. 판정은 CAUTION까지이고, DB 등록은 레시피가 30개 이상 쌓이면 백엔드가 합니다. 10/26 회의로 자동 등록이 기본이고 이상 신호가 잡힌 레시피만 운영자가 예/아니오로 정합니다. 신호는 3개(다른 음식 의심·제목에 메뉴명 없음·매핑 안 된 재료), 신호가 없는 레시피는 바로 통과예요 (11/02). 신호를 누가 계산할지는 ⑥ §5에서 정하는 중이에요.

## 지금 있는 것

| 파일 | 내용 |
|---|---|
| `collect.py` | **1단계(수집)** `await WebCollectAgent(provider, reader, pages=HttpPageFetcher()).run(WebCollectRequest)` → `WebCollectResponse`. 우선 사이트 3곳을 매번 1위부터 읽고 **레시피 주소만 남긴다**(`RECIPE_PATHS`: 회원 프로필·상담·목록·다른 도메인은 읽지 않음). 우리의식탁·새미네부엌은 제목에 메뉴명이 있을 때만 남긴다(`TITLE_CHECKED_SITES`, 11/08). 모자라면 **일반 웹(블로그 검색)으로 넓혀** 채운다(⑥ §2-3-1). 한 판에서 우선 사이트 후보가 모자라면 같은 판에서 블로그도 검색한다(11/02). 캐시에 있는 URL은 건너뛰고, 검색 순위는 저장·전달하지 않는다(10/27 회의). 남긴 글만 본문을 읽고(`pages`), 이 메뉴의 레시피로 남은 글에서 재료 이름을 사전(규칙)과 LLM으로 뽑아 합친다(LLM 이름은 본문에 있을 때만, LLM은 글 3개씩 나눠 동시에 부름). 빠진 글만큼 다음 결과를 더 읽어 유효 레시피 10개를 채운다. 모자란 수의 2배를 한 번에 읽는다. 시간 제한 7초(검색 3초·본문 1.5초·LLM 3초, 남은 시간 2초 미만이면 LLM을 부르지 않음), 캐시 URL은 다시 세지 않음. 본문을 제때 못 읽으면 검색 제목·요약만 쓴다. LLM이 없거나 실패하면 그 글을 판별 없이 남기고 사전 결과만 쓴다. 다만 판별된 레시피로 먼저 채우고, 판별 못 한 글은 10개가 모자랄 때만, 제목에 메뉴명이 있을 때만 넣고(11/08), 남긴 레시피의 절반 이상이 판별 못 한 글이면 `web_llm_extraction_failed`(11/02). 새 레시피도 캐시도 없으면 `found: false` + `web_search_timeout` |
| `aggregate.py` | **2단계(집계)** `await WebAggregateAgent().run(WebAggregateRequest)` → `WebAggregateResponse`. ④가 표준 재료로 맞춘 레시피를 재료·사이트별로 세고(`observations`), 유효 레시피가 10개 미만이면 `insufficient_sources` 경고, 관리자 검토 문구(`review_notes`) 규칙 1·2·3·4·6·7과 웹 레시피 자동 등록용 레시피별 이상 신호 3개(`recipe_signals`)를 응답에 싣는다. 매핑 안 된 재료 신호는 조미료·야채·반죽 같은 일반 단어만 있으면 내지 않는다("고기"는 냄, 11/12). 신호가 없으면 빈 배열을 꼭 보낸다(칸이 빠지면 ⓪이 새 레시피 전부를 운영자 검토로 보냄, ⓪ §4-6). ⓪은 신호가 잡힌 레시피만 운영자 검토로 보낸다(#368) |
| `reader.py` | 1단계의 **LLM** 쪽. `OpenAIRecipeReader`가 검색 결과 묶음을 한 번에 읽어 글마다 "이 메뉴의 레시피인지"와 적힌 재료 이름을 돌려준다. 애매하면 남긴다. 답이 빠지거나 형식이 틀리면 예외 → `collect.py`가 전부 남기고 `web_llm_extraction_failed`. `OpenAIRecipeReader.from_env_if_configured()`는 `OPENAI_API_KEY`가 없으면 `None` (환경 변수는 `.env.example`) |
| `providers.py` | 검색 도구 틀(`SearchProvider.search(query, start, count, sites)`: 한 검색 순위 목록의 `start`위부터 `count`개, `sites`가 비면 일반 웹), 테스트용 `StaticSearchProvider`, **`NaverSearchProvider`**(네이버 클라우드 NAVER API HUB). 우선 사이트는 사이트마다 웹문서 검색에 `site:`를 붙여 번갈아 합치고, 일반 웹은 블로그 검색. `NaverSearchProvider.from_env_if_configured()`는 `NAVER_SEARCH_CLIENT_ID`·`NAVER_SEARCH_CLIENT_SECRET`가 둘 다 없으면 `None`. **`search_provider_from_env()`**는 키가 있으면 네이버, 없으면 `UnconfiguredSearchProvider`(검색하면 `SearchNotConfiguredError`) → 수집이 `web_search_not_configured`로 알려 "검색했는데 못 찾음"(`web_search_timeout`)과 구분돼요 (#120) |
| `pages.py` | 남긴 검색 결과의 **본문 읽기** `HttpPageFetcher`. 우선 사이트 3곳은 페이지의 schema.org 레시피 데이터(`recipeIngredient`)에서 재료 목록만 읽는다 (만개의레시피는 페이지 글자에 음식 메뉴가 섞여 있어서). 그 밖의 글은 보이는 글자를 읽고, 네이버 블로그는 본문이 iframe 안에 있어 `m.blog.naver.com` 주소로 읽는다. 스레드 없이 비동기(`httpx.AsyncClient`)로 동시에 읽는다(11/08, 스레드 풀 대기로 1.5초를 넘기던 문제). 못 읽은 글은 제목·요약만 남는다 |
| `dictionary.py` | 1단계 재료 뽑기의 **규칙** 쪽. `find_ingredient_names(text)`가 재료 사전에 있는 이름을 글에 적힌 그대로 찾는다 (긴 이름 우선, 한 글자 재료는 단어 전체일 때만, "이라면"처럼 어미인 "라면"은 건너뜀). 표준 재료로 맞추는 건 ④ |
| `data/ingredient_dictionary.csv` | 재료 사전 (`surface`, `canonical_name`). 크롤링 표준 재료 324개 + 크롤링 레시피에서 두 번 이상 나온 원문 이름. `python -m ai_web_search_agent.build_dictionary`로 다시 만든다 |

## 아직 없는 것

- **⓪ 조립**: `app.py` `_build_supervisor_service()`의 `web_collect=WebCollectAgent(StaticSearchProvider())`를 `WebCollectAgent(search_provider_from_env(), OpenAIRecipeReader.from_env_if_configured(), pages=HttpPageFetcher())`로 바꾸는 일 (⑥ §7). 키가 없으면 `web_search_not_configured`로 알려요. 이게 없으면 검색 결과가 없어 LLM도 쓰이지 않습니다. 배포 환경에 네이버 키도 넣어야 합니다
- **묶음 단위 검토 문구 정리**: 운영자 페이지에는 레시피별 신호만 올라가므로 `review_notes`와 규칙 1·3·4·5·7은 지울 수 있어요. ⓪ 계약에 `review_notes` 칸이 남아 있어 아직 그대로 계산해요(⑥ §7). 검토 문구를 LLM으로 다듬는 일은 하지 않기로 했어요(11/02)

옛 코드(룰엔진 결과에 웹 태그를 덧붙이고 SQLite에 저장하던 구조, Tavily 검색, CLI)는 새 설계와 맞지 않아 지웠습니다.

## 테스트

```bash
pytest -q ai_web_search_agent
```
