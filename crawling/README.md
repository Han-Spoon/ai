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

정규화할 때 `recipe_relation.py`가 레시피 제목을 `exact`(동일 메뉴), `variant`(변형), `other_dish`(다른 음식), `derivative`(완제품 활용), `unrelated`, `ambiguous`로 분류합니다. **기본 메뉴 prior에는 `exact`만 넣습니다.** 동일 메뉴의 다른 이름(달걀찜→계란찜, 닭도리탕→닭볶음탕 등)은 `recipe_relation.py`의 `SAME_MENU_ALIASES`에 명시한 별칭만 허용합니다. 제목에 검색 메뉴명이 들어 있어도 불고기김밥·잡채볶음밥·오징어순대처럼 더 구체적인 다른 메뉴, 다른 요리 형태, 재료 수식어가 붙으면 기본 메뉴 통계에서 제외합니다.

불확실한 레시피를 `재료가 없었던 레시피`로 세지 않습니다. 레시피 전체를 분모 `n_total`에서도 빼기 때문에 불확실한 자료가 알레르겐 확률을 낮추지 않습니다. `<사이트>_normalized.csv`에는 웹검색 재료 사전을 만들 때 필요한 정제된 전체 어휘를 유지하지만, `<사이트>_prior.csv` 계산에는 `exact` 행만 사용합니다. 제외된 자료는 사이트별 `<사이트>_recipe_audit.csv`에 제목·URL·관계·사유를 한 레시피당 한 행으로 남깁니다. 예를 들어 불고기 검색에 섞인 불고기김밥의 김과, 순대 검색에 섞인 까르보순대의 생크림은 normalized와 감사 파일에서는 확인할 수 있지만 prior에는 들어가지 않습니다.

| 사이트 | 원본 | 정규화 결과 | prior |
|---|---|---|---|
| 세미 (semie) | `semie_recipes_raw.csv` | `semie_normalized.csv` | `semie_prior.csv` |
| 우리의식탁 (wtable) | `wtable_recipes_raw.csv` | `wtable_normalized.csv` | `wtable_prior.csv` |
| 만개의레시피 (10000recipe) | `10000recipe_recipes_raw.csv` | `10000recipe_normalized.csv` | `10000recipe_prior.csv` |

메뉴 하나를 직접 확인할 때는 다음처럼 prior와 감사 파일을 함께 봅니다.

```bash
rg '^순대,' crawling/merged_prior.csv
rg '^순대,' crawling/merged_recipe_audit.csv
rg '^불고기,' crawling/merged_recipe_audit.csv | rg ',(variant|other_dish|derivative|ambiguous),'
```

감사 파일의 `included_in_base_prior=true`만 해당 기본 메뉴의 확률 집계에 들어갑니다. `variant`는 `base_menu_name`과 `remain_token`을 남기지만 자동으로 기본 메뉴에 합치지 않습니다. 서비스에서 실제 변형 메뉴가 들어오면 기존 확정 구조대로 `menus.base_menu_id`/`remain_token`을 우선 사용하고, 미등록 변형은 ④의 런타임 변형 경로와 검토 절차를 사용합니다.

`10000recipe_recipes_raw.csv`의 처음 76개 메뉴는 크롤러 출력이 아니라, 이전에 정규화된 파일(`recipe_ingredients_all76_60_normalized.csv`)의 `ingredient_raw`를 원문으로 되돌려 만든 것입니다. 2026-11-22에 추가한 22개 메뉴는 `recipe10000_crawler.py`로 모아 같은 파일 뒤에 붙였습니다. 이 크롤러는 검색 결과 순서대로 메뉴당 60개(기본값)를 읽고, 본문 재료 목록의 이름만 저장합니다. 재료 목록 아래의 "조리도구" 칸(도마·찜기 등)은 읽지 않습니다. 22개 메뉴는 메뉴당 300개까지 읽었습니다. 이 22개를 처음 모을 때는 크롤러가 조리도구 칸까지 읽어서, 붙이기 전에 손으로 정리했습니다. 정규화 뒤에도 남는 도구 이름(볼·요리집게·면기·찜기·지퍼백·명주실 등 약 120개)을 지우고, "소금 숟가락"·"꿀 국자"·"낫도에너젠 계량컵 기준"처럼 재료 뒤에 계량 도구가 붙어 정규화가 통째로 지우던 19개 이름은 재료 이름(소금·꿀·낫또 등)으로 고쳐 살렸습니다.

처음 76개 메뉴도 2026-11-22~23에 같은 크롤러로 메뉴당 300개까지 다시 읽었습니다. 제목 필터를 거친 뒤 기존 60개와 합쳤습니다. 다시 읽은 레시피는 새 결과를 따르고, 다시 나오지 않은 기존 레시피(44개)도 같은 제목 필터를 거쳐 남깁니다. 76개 메뉴의 레시피 수는 중앙값 268개, 평균 222개입니다. 붙이기 전에 정리한 것:

- 계량 도구가 붙은 이름 약 130행은 앞의 재료 이름만 남겼습니다("천일염 종이컵" → 천일염). 앞 단어가 재료가 아니면 손으로 고쳤습니다("국물용 멸치 1종이컵" → 멸치, "새우 마사고오븐구이" → 새우 + 마사고, "백설뚝배기불고기양념" → 불고기양념).
- 작성자가 재료 목록에 적은 도구(거름망·면보·밀대·찜솥·꼬지·키친타월·포일 등 약 30종)를 지웠습니다.
- 오타 약 50종을 고쳤습니다. 알레르겐 이름이 틀리면 태그가 붙지 않아 놓치므로(FN) 특히 챙겼습니다: "말가루" → 밀가루, "댤갈" → 달걀, "메츄리알" → 메추리알, "멸차액젖" → 멸치액젓, "고들어캔" → 고등어캔, "참께" → 참깨, "어목" → 어묵, "국장간" → 국간장 등.
- 남은 김치찌개를 재료로 적은 레시피("쌈 싸 먹는 김치찌개")는 재료를 김치로 고쳤습니다. 그대로 두면 ⑥ 재료 사전에 요리 이름 "김치찌개"가 재료로 들어갑니다.
- 사이트에 레시피가 적은 메뉴(설렁탕 116, 꿀떡 60, 수정과 70, 약과 37, 붕어빵 59 등)와 다른 요리가 많이 섞인 메뉴(후라이드치킨 83, 물냉면 91, 갓김치 91, 생선전 46, 곱창전골 38, 슈크림 붕어빵 6)는 300개에 못 미칩니다.

```bash
python crawling/recipe10000_crawler.py --menu-file <메뉴 목록> --max-recipes-per-menu 300 --out <출력 CSV>
python crawling/recipe10000_crawler.py --recipes-from <기존 CSV> --out <출력 CSV>   # 검색 없이 그 레시피만 다시 읽기
```

### 제목 필터 (`filter_titles.py`)

검색이 느슨하면 다른 요리가 섞입니다. 우리의식탁에서 "닭한마리"를 검색하면 1,186개가 나오지만 제목이 맞는 것은 1개였고, 만개의레시피도 300위까지 읽으면 "떡갈비"에 떡볶이가 섞입니다. 최종 필터는 검색어 포함 여부가 아니라 `recipe_relation.py`의 관계 분류를 사용합니다. 표기 차이(달걀찜, 주꾸미, 닭도리탕, 육계장, 알타리김치, 김치찌게, 깻잎짱아찌 등)는 `SAME_MENU_ALIASES`에 명시한 것만 같은 메뉴로 허용합니다. 다른 알려진 메뉴·다른 요리 형태·재료 수식어가 잡히거나 판별이 애매하면 기본 prior에서 제외하고 감사 CSV에 남깁니다.

```bash
python crawling/filter_titles.py <크롤링 원본 CSV> --out <걸러낸 CSV>
```

세 사이트를 합친 prior(`merged_prior.csv`)는 실험·분석용입니다. 다음처럼 다시 만들 수 있으며, 중간 파일(`merged_recipes_raw.csv`, `merged_normalized.csv`)은 용량이 커서 저장소에 올리지 않습니다.

```bash
python crawling/merge_corpora.py
python crawling/normalize_ingredients.py crawling/merged_recipes_raw.csv --out-prefix merged
```

정규화·제목 관계 규칙을 바꾸면 사이트별 `normalized`/`prior`/`recipe_audit`과 합친 결과를 모두 다시 만들어야 합니다. 그 뒤 `python -m ai_web_search_agent.build_dictionary`를 실행해 웹검색 재료 사전이 normalized 코퍼스와 일치하는지도 확인합니다.

## DB 시드 재생성

정규화 규칙(`normalize_ingredients.py`·`recipe_relation.py`)이나 태그 사전(`ai_ruleengine/constants.py`)을 바꾸면 DB 시드도 다시 만들어 백엔드에 적재해야 합니다. 그대로 두면 서비스 DB는 이전 prior를 계속 사용합니다 (④ §2-6).

```bash
python crawling/db_seed.py --register-ids   # 새 메뉴·재료가 생겼으면 번호 등록 (같은 PR에 seed_ids.csv 커밋)
python crawling/db_seed.py --out <폴더>     # 테이블마다 CSV 하나
```

- **메뉴·재료·메뉴 분류 번호는 `seed_ids.csv`에 고정한다** (2026-11-23). 예전에는 이름 가나다순 위치가 번호여서, 새 이름 하나가 끼면 뒤 번호가 전부 밀렸다(#393에서 메뉴 68개·재료 248개). 백엔드에 저장된 가게 메뉴 연결·사장님 답변·캐시가 다른 행을 가리키게 된다. 기준은 처음 적재한 #393 이전 시드이고, 새 이름은 지금까지 쓴 가장 큰 번호 다음을 받는다. 번호는 바꾸거나 재사용하지 않는다. 새 이름이 등록 안 되면 테스트가 실패한다.
- **음식이 부모인 숨은 재료 규칙은 구성 관계로 넣지 않는다** (2026-11). 부모 이름을 표준 재료명으로 맞추면 음식이 원재료로 바뀌어(김밥 → 밥, 열무김치 → 열무, 김칫잎 → 김) "밥 안에 맛살" 같은 구성이 생긴다. 부모 이름이 바뀌고 바뀐 이름의 분류가 `base`·`garnish`이면 그 규칙을 뺀다. 재료 마스터에는 그대로 남아 번호가 바뀌지 않는다.
- **태그 보정** (2026-11): 태그는 사전 키워드가 이름에 들어 있으면 붙이므로(부분일치) 엉뚱하게 붙는 경우가 있다(땅콩 → 콩, 둥굴레 → 굴, 밀크초콜릿 → 밀). 재료 324개를 직접 검토해 확실히 틀린 것만 `TAG_REMOVE`로 빼고(2026-11-22에 새 메뉴에서 나온 돼지감자 → 돼지고기도 뺌, 지금 325개), 빠진 것은 `TAG_ADD`로 더한다(다시다 → 소고기, 젓갈 → 새우, 곱창 → 돼지고기, 와인 → 아황산 등). 애매하면 그대로 둔다(FN 우선). 숨은 재료 경로 태그는 그 알레르겐을 실제로 가진 자식에게만 둔다(`_owned_path_tags`). 예: 햄 → 소금·아질산나트륨의 돼지고기, 육수 → 다시마의 생선·소고기는 빼고, 같은 부모의 돼지고기·멸치·쇠고기가 그 태그를 유지한다. 그런 자식이 없으면 태그를 그대로 둔다(FN 우선). 숨은 재료 규칙의 이름이 표준화로 다른 재료에 합쳐지면, 그 규칙의 자식이 합쳐진 재료 전체에 붙는다. 그중 확실히 틀린 것은 `RULE_DROP`으로 뺀다 (2026-11-24): 청주 → 맛술로 합쳐져 청주의 밀(누룩)이 모든 맛술에 붙었다("맛술은 술과 밀로 만든"). 같은 이유로 쌈장 → 된장으로 합쳐져 쌈장의 "돼지고기 지방"이 모든 된장에 돼지고기로 붙었는데("된장은 대두·밀·돼지고기 원료"), 흔한 쌈장에는 돼지고기가 없어 쌈장 규칙(`ai_result/rules/hidden_rules_data.py`)에서 아예 뺐다. 된장의 대두·밀, 맛술의 알코올은 각자 규칙으로 그대로 있다. 김치 → 액젓의 조개류는 멸치액젓에 붙어 "멸치액젓은 생선과 조개류로 담근"처럼 읽히지만, 사장님이 새우젓 없음이라고 하면 김치의 유일한 조개류 경로라 남긴다(굴 김치, FN 우선). 다른 요리가 섞인 레시피 묶음은 `CORPUS_DROP`으로 확률 계산에서 뺀다(우리의식탁 간장게장 911개 중 꽃게 3개, 약과 1개는 아이스크림). 재료 목록은 뺀 묶음까지 포함해 만들어 재료 ID가 바뀌지 않는다. 룰엔진이 쓰는 `ai_ruleengine/constants.py` 사전은 바꾸지 않는다.
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

`merged_prior.csv`만 손으로 고쳐서는 서비스에 반영되지 않습니다. 사이트별 prior 재생성 → `db_seed.py` 실행 → 백엔드 import batch 적재 → 영향 메뉴 `knowledge_revision` 증가 순서가 모두 필요합니다. `ingredient_risk_scores`는 이 전역 크롤링 prior와 다른 가게별 증거 테이블이므로 직접 수정하지 않습니다.
