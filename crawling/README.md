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
