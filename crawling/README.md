# Wtable Recipe Crawling

우리의식탁(`wtable.co.kr`)에서 메뉴별 레시피 재료명을 수집합니다.

## 실행

```bash
python crawling/wtable_crawler.py
python crawling/normalize_ingredients.py crawling/wtable_recipes_raw.csv --out-prefix wtable
```

## 출력 컬럼

`wtable_crawler.py`는 아래 4개 컬럼만 저장합니다.

```text
menu_query, recipe_title, recipe_url, ingredient_name
```

`ingredient_name`은 사이트의 원문 재료명만 저장하며 수량은 제외합니다. 조리도구 섹션이나 도구명으로 보이는 값은 수집 단계에서 제외하고, 정규화 단계에서도 한 번 더 제거합니다.

## 메뉴 목록

기본 메뉴 목록은 `menu_queries.txt`입니다. 지금은 98개 메뉴가 들어 있습니다.

- 처음 76개: 쉼표 기준 목록에서 `꿀떡송편`은 이전 목록과 총 개수에 맞춰 `꿀떡`, `송편` 두 메뉴로 분리했습니다.
- 뒤 22개 (2026-11-22 추가): 용산 일대 음식점 조사(71곳, 음식 메뉴 804개)에서 여러 가게에 나오지만 레시피 DB에 없던 메뉴입니다. 수육, 국밥·해장국, 제육볶음, 매운탕류, 갈비, 낙지·쭈꾸미볶음, 계란찜, 밀면, 곱창·막창구이, 닭한마리·닭볶음탕, 콩국수.

## 검색 범위

기본값은 우리의식탁 레시피 검색 API 결과를 모두 수집하고, 각 행의 `menu_query`에는 검색에 사용한 메뉴명을 그대로 넣습니다.

레시피 제목이 메뉴명과 정확히 같은 경우만 수집하려면 다음처럼 실행합니다.

```bash
python crawling/wtable_crawler.py --exact-title-only
```

## 사이트별 결과와 합친 결과

세 사이트(semie, wtable, 10000recipe)를 같은 `normalize_ingredients.py`로 정규화한 결과를 사이트별로 둡니다. 백엔드 적재(`menu_recipe_corpora.corpus` 단위)는 사이트별 `<사이트>_prior.csv`를 사용합니다.

| 사이트 | 원본 | 정규화 결과 | prior |
|---|---|---|---|
| 세미 (semie) | `semie_recipes_raw.csv` | `semie_normalized.csv` | `semie_prior.csv` |
| 우리의식탁 (wtable) | `wtable_recipes_raw.csv` | `wtable_normalized.csv` | `wtable_prior.csv` |
| 만개의레시피 (10000recipe) | `10000recipe_recipes_raw.csv` | `10000recipe_normalized.csv` | `10000recipe_prior.csv` |

`10000recipe_recipes_raw.csv`의 처음 76개 메뉴는 크롤러 출력이 아니라, 이전에 정규화된 파일(`recipe_ingredients_all76_60_normalized.csv`)의 `ingredient_raw`를 원문으로 되돌려 만든 것입니다. 2026-11-22에 추가한 22개 메뉴는 `recipe10000_crawler.py`로 모아 같은 파일 뒤에 붙였습니다. 이 크롤러는 검색 결과 순서대로 메뉴당 60개(기본값)를 읽고, 본문 재료 목록의 이름만 저장합니다. 재료 목록 아래의 "조리도구" 칸(도마·찜기 등)은 읽지 않습니다. 22개 메뉴는 메뉴당 300개까지 읽었습니다. 이 22개를 처음 모을 때는 크롤러가 조리도구 칸까지 읽어서, 붙이기 전에 손으로 정리했습니다. 정규화 뒤에도 남는 도구 이름(볼·요리집게·면기·찜기·지퍼백·명주실 등 약 120개)을 지우고, "소금 숟가락"·"꿀 국자"·"낫도에너젠 계량컵 기준"처럼 재료 뒤에 계량 도구가 붙어 정규화가 통째로 지우던 19개 이름은 재료 이름(소금·꿀·낫또 등)으로 고쳐 살렸습니다.

처음 76개 중 35개 메뉴는 2026-11-22에 같은 크롤러로 메뉴당 300개까지 다시 읽었습니다(`menu_queries.txt`의 앞 35개: 갈비구이~김치볶음밥). 제목 필터를 거친 뒤 기존 60개와 합쳤고, 다시 읽은 레시피는 새 결과를 따르고 다시 나오지 않은 기존 레시피는 그대로 둡니다. 나머지 41개 메뉴는 기존 60개 그대로입니다. 붙이기 전에 정리한 것:

- 계량 도구가 붙은 이름 약 80행은 앞의 재료 이름만 남겼습니다("천일염 종이컵" → 천일염). 앞 단어가 재료가 아니면 손으로 고쳤습니다("국물용 멸치 1종이컵" → 멸치, "새우 마사고오븐구이" → 새우 + 마사고).
- 작성자가 재료 목록에 적은 도구(거름망·면보·밀대·찜솥 등 15행)를 지우고, 오타·꼬리말 6행을 고쳤습니다("다진마을" → 다진마늘, "파 파는 빨리 익어버리게 한데요!" → 파).
- 사이트에 레시피가 적은 메뉴(설렁탕 116, 꿀떡 60, 경단 91 등)와 다른 요리가 많이 섞인 메뉴(후라이드치킨 83, 물냉면 91, 갓김치 91)는 300개에 못 미칩니다.

```bash
python crawling/recipe10000_crawler.py --menu-file <메뉴 목록> --max-recipes-per-menu 300 --out <출력 CSV>
python crawling/recipe10000_crawler.py --recipes-from <기존 CSV> --out <출력 CSV>   # 검색 없이 그 레시피만 다시 읽기
```

### 제목 필터 (`filter_titles.py`)

검색이 느슨하면 다른 요리가 섞입니다. 우리의식탁에서 "닭한마리"를 검색하면 1,186개가 나오지만 제목이 맞는 것은 1개였고, 만개의레시피도 300위까지 읽으면 "떡갈비"에 떡볶이가 섞입니다. 그래서 22개 메뉴는 세 사이트 모두, 다시 읽은 35개 메뉴는 만개의레시피에서 레시피 제목에 메뉴 키워드가 있는 것만 남긴 뒤 원본에 붙였습니다. 표기 차이(달걀찜, 주꾸미, 닭도리탕, 육계장, 알타리김치 등)는 `TITLE_KEYWORDS`에 적습니다. 김치 메뉴는 김치로 만든 다른 요리(갓김치볶음밥, 총각김치지짐, 열무김치국수 등)를 `KIMCHI_DISH`로 뺍니다. 갓김치는 194개 중 103개가 이런 요리였습니다. 제목이 우연히 걸리는 진짜 김치 레시피("김장김치전에 담그는…")도 메뉴당 1~3개 빠집니다.

```bash
python crawling/filter_titles.py <크롤링 원본 CSV> --out <걸러낸 CSV>
```

세 사이트를 합친 prior(`merged_prior.csv`)는 실험·분석용입니다. 다음처럼 다시 만들 수 있으며, 중간 파일(`merged_recipes_raw.csv`, `merged_normalized.csv`)은 용량이 커서 저장소에 올리지 않습니다.

```bash
python crawling/merge_corpora.py
python crawling/normalize_ingredients.py crawling/merged_recipes_raw.csv --out-prefix merged
```

정규화 규칙을 바꾸면 사이트별 결과와 합친 결과를 모두 다시 만들어야 합니다.

## DB 시드 재생성

정규화 규칙(`normalize_ingredients.py`)이나 태그 사전(`ai_ruleengine/constants.py`)을 바꾸면 DB 시드도 다시 만들어 백엔드에 적재해야 합니다. 그대로 두면 룰엔진과 DB의 태그·재료 이름이 달라집니다 (④ §2-6).

```bash
python crawling/db_seed.py --out <폴더>   # 테이블마다 CSV 하나
```

- 재료 번호는 이름 가나다순이라, 원천이 같으면 이전 적재와 같은 번호가 나온다.
- **음식이 부모인 숨은 재료 규칙은 구성 관계로 넣지 않는다** (2026-11). 부모 이름을 표준 재료명으로 맞추면 음식이 원재료로 바뀌어(김밥 → 밥, 열무김치 → 열무, 김칫잎 → 김) "밥 안에 맛살" 같은 구성이 생긴다. 부모 이름이 바뀌고 바뀐 이름의 분류가 `base`·`garnish`이면 그 규칙을 뺀다. 재료 마스터에는 그대로 남아 번호가 바뀌지 않는다.
- **태그 보정** (2026-11): 태그는 사전 키워드가 이름에 들어 있으면 붙이므로(부분일치) 엉뚱하게 붙는 경우가 있다(땅콩 → 콩, 둥굴레 → 굴, 밀크초콜릿 → 밀). 재료 324개를 직접 검토해 확실히 틀린 것만 `TAG_REMOVE`로 빼고(2026-11-22에 새 메뉴에서 나온 돼지감자 → 돼지고기도 뺌, 지금 325개), 빠진 것은 `TAG_ADD`로 더한다(다시다 → 소고기, 젓갈 → 새우, 곱창 → 돼지고기, 와인 → 아황산 등). 애매하면 그대로 둔다(FN 우선). 숨은 재료 경로 태그는 그 알레르겐을 실제로 가진 자식에게만 둔다(`_owned_path_tags`). 예: 햄 → 소금·아질산나트륨의 돼지고기, 육수 → 다시마의 생선·소고기는 빼고, 같은 부모의 돼지고기·멸치·쇠고기가 그 태그를 유지한다. 그런 자식이 없으면 태그를 그대로 둔다(FN 우선). 다른 요리가 섞인 레시피 묶음은 `CORPUS_DROP`으로 확률 계산에서 뺀다(우리의식탁 간장게장 911개 중 꽃게 3개, 약과 1개는 아이스크림). 재료 목록은 뺀 묶음까지 포함해 만들어 재료 ID가 바뀌지 않는다. 룰엔진이 쓰는 `ai_ruleengine/constants.py` 사전은 바꾸지 않는다.
- `ingredients.name_i18n`(번역)은 이 스크립트가 만들지 않는다. 아래 표의 번역 CSV로 따로 채운다.

| DB 테이블 | 원천 |
|---|---|
| `menu_categories`, `menus`, `recipe_ingredients` | `ai_ruleengine/data/menus.csv` (큐레이션 레시피) |
| `ingredients` | 사이트별 prior의 표준 재료 + 큐레이션 레시피 재료 + 숨은 재료 구조의 재료. `taxonomy_category`는 `ingredient_taxonomy.csv` (없으면 `unknown`, ④ §4). `name_i18n`은 `ingredient_translations.csv` (`ko` 외 6개 언어, 표준 재료 324개. 없는 재료는 `ko`만 넣어 한국어 이름이 그대로 나감, ⑧ §1-2) `ingredient_translations_extra.csv`(표준 밖 이름 607개 번역, 2026-11-02)는 DB에 넣지 않는다. ④가 표준 재료로 못 맞춘 웹 재료의 이름을 ⑧이 직접 찾는 데 쓴다 |
| `ingredient_tags` | `ai_ruleengine/constants.py`의 `VARIANT_INGREDIENTS` (22종 `is_*`) |
| `ingredient_compositions`, `ingredient_composition_tags` | `ai_result/rules/hidden_rules_data.py` (구성 관계 `part_of`만) |
| `menu_recipe_corpora`, `menu_ingredient_priors` | `<사이트>_prior.csv` (사이트별 `n_total`, `k_count`) |
| `knowledge_import_batches` | 원천별 1행, `source_version`은 시드를 만든 커밋 SHA |

적재 순서는 외래키 때문에 위 표의 위에서 아래 순서입니다(`menu_categories` → `menus` → `ingredients` → 태그·레시피·구성 → 배치 → 코퍼스 → 관측값). 적재 뒤에는 영향받은 메뉴의 `knowledge_revision`을 올려 ④ 캐시를 무효화합니다.
