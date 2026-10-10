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

기본 메뉴 목록은 `menu_queries.txt`입니다. 현재 요청 기준 76개 메뉴를 넣어두었습니다. 쉼표 기준 목록에서 `꿀떡송편`은 이전 목록과 총 개수에 맞춰 `꿀떡`, `송편` 두 메뉴로 분리했습니다.

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

`10000recipe_recipes_raw.csv`는 크롤러 출력이 아니라, 이전에 정규화된 파일(`recipe_ingredients_all76_60_normalized.csv`)의 `ingredient_raw`를 원문으로 되돌려 만든 파일입니다.

세 사이트를 합친 prior(`merged_prior.csv`)는 실험·분석용입니다. 다음처럼 다시 만들 수 있으며, 중간 파일(`merged_recipes_raw.csv`, `merged_normalized.csv`)은 용량이 커서 저장소에 올리지 않습니다.

```bash
python crawling/merge_corpora.py
python crawling/normalize_ingredients.py crawling/merged_recipes_raw.csv --out-prefix merged
```

정규화 규칙을 바꾸면 사이트별 결과와 합친 결과를 모두 다시 만들어야 합니다.
