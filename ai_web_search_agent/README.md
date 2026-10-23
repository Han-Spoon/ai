# Hanspoon Web Search Agent

> **새 설계로 옮기는 중입니다.** 기준은 [`docs/6-agent-websearch.md`](../docs/6-agent-websearch.md)와 ⓪ §4-5 두 단계 형식(`ai_supervisor/contracts/web_search.py`)입니다.
>
> - **2단계(집계)** — `aggregate.py`의 `await WebAggregateAgent().run(WebAggregateRequest)` → `WebAggregateResponse`: ④가 표준 재료로 맞춘 레시피를 재료·사이트별로 세고(`observations`), 유효 레시피가 10개 미만이면 `insufficient_sources` 경고, 관리자 검토 문구(`review_notes`) 규칙 1·2·3·4·6·7을 만듭니다. 규칙 5(LLM만 찾은 재료)는 LLM 추출을 붙일 때 추가합니다.
> - **1단계(수집)** — 아직 아래 옛 코드입니다. 옛 코드는 재료에 태그를 직접 붙이고 SQLite에 저장하는데, 새 설계에서는 태그는 ④만 붙이고 저장은 백엔드만 합니다. 1단계를 새 형식으로 바꿀 때 걷어냅니다.
>
> ```bash
> pytest -q ai_web_search_agent/tests/test_aggregate.py
> ```

Rule Engine 결과를 받은 뒤 웹 레시피 근거로 누락 가능 재료 태그를 보완합니다.

원칙:

- 검색 결과는 실제 식당 레시피로 간주하지 않습니다.
- Rule Engine의 `danger` 판단은 Web 결과로 낮추지 않습니다.
- 웹에서 발견한 재료는 기존 `ai_ruleengine`의 `VARIANT_INGREDIENTS`와 `tag_explicit()`으로만 태깅합니다.
- 검색 결과와 추출 evidence는 SQLite DB에 캐시합니다.

## 실행

실제 검색은 `TAVILY_API_KEY` 환경변수가 있을 때 Tavily adapter를 사용합니다. 키가 없으면 빈 `StaticSearchProvider`로 동작해 DB/파이프라인 테스트만 가능합니다.

Rule Engine 결과를 이미 가지고 있으면:

```bash
python3 ai_web_search_agent/main.py --judged-json outputs/final/menu_001_judged.json --print-json
```

OCR 결과와 프로필만 가지고 있으면 Web Search Agent가 기존 `ai_ruleengine.analyze_all()`을 먼저 실행한 뒤 웹 검증을 붙입니다.

```bash
python3 ai_web_search_agent/main.py --ocr-json outputs/final/menu_001_result.json --profile profile.json --print-json
```

DB 기본 경로:

```text
ai_web_search_agent/data/web_search_cache.sqlite3
```
