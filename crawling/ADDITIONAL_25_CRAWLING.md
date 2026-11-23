# 신규 25개 메뉴 크롤링 절차

이 문서는 공용 크롤러를 변경하지 않고 신규 25개 메뉴만 별도 수집하는 절차입니다.

적재 순서는 외래키 때문에 위 표의 위에서 아래 순서입니다(`menu_categories` → `menus` → `ingredients` → 태그·레시피·구성 → 배치 → 코퍼스 → 관측값). 적재 뒤에는 영향받은 메뉴의 `knowledge_revision`을 올려 ④ 캐시를 무효화합니다.

## 추가 25개 메뉴 안전 크롤링 절차

`menu_queries_additional_25.txt`는 기존 98개와 섞지 않고 새로 수집할
25개 메뉴만 담는다. 아래 명령은 모두 `crawling/work/additional_25/`
안에 결과를 만들며, 커밋된 기존 CSV를 덮어쓰지 않는다. 공용 `.gitignore`는
수정하지 않았으므로 `crawling/work/` 결과는 직접 커밋하지 않도록 주의한다.

### 1. 작업 폴더

```bash
mkdir -p crawling/work/additional_25/raw crawling/work/additional_25/reports crawling/work/additional_25/filtered crawling/work/additional_25/rejected crawling/work/additional_25/staging
```

### 2. 사이트별 크롤링

만개의레시피는 메뉴당 검색 상위 300개까지 읽는다.

```bash
python crawling/recipe10000_crawler.py --menu-file crawling/menu_queries_additional_25.txt --max-recipes-per-menu 300 --out crawling/work/additional_25/raw/10000recipe.csv --report crawling/work/additional_25/reports/10000recipe.csv
```

우리의식탁은 검색 API가 돌려주는 결과를 모두 읽고, 뒤의 제목 필터에서
다른 요리를 뺀다. `--exact-title-only`는 표기 차이를 놓치므로 쓰지 않는다.

```bash
python crawling/wtable_crawler_additional_25.py --menu-file crawling/menu_queries_additional_25.txt --out crawling/work/additional_25/raw/wtable.csv --report crawling/work/additional_25/reports/wtable.csv
```

세미는 기존 목록 캐시를 재사용하고 메뉴당 재료가 있는 레시피를 최대 15개 읽는다.

```bash
python crawling/semie_crawler_additional_25.py --menu-file crawling/menu_queries_additional_25.txt --max-recipes-per-menu 15 --out crawling/work/additional_25/raw/semie.csv --report crawling/work/additional_25/reports/semie.csv
```

중단된 경우 같은 명령 끝에 `--resume`을 붙인다. 리포트에 `ok`, `partial`,
`no_recipe`로 적힌 메뉴는 건너뛰고 `error` 메뉴만 다시 실행한다.

```bash
python crawling/recipe10000_crawler.py --menu-file crawling/menu_queries_additional_25.txt --max-recipes-per-menu 300 --out crawling/work/additional_25/raw/10000recipe.csv --report crawling/work/additional_25/reports/10000recipe.csv --resume
python crawling/wtable_crawler_additional_25.py --menu-file crawling/menu_queries_additional_25.txt --out crawling/work/additional_25/raw/wtable.csv --report crawling/work/additional_25/reports/wtable.csv --resume
python crawling/semie_crawler_additional_25.py --menu-file crawling/menu_queries_additional_25.txt --max-recipes-per-menu 15 --out crawling/work/additional_25/raw/semie.csv --report crawling/work/additional_25/reports/semie.csv --resume
```

세미의 목록 캐시를 실제 사이트에서 다시 만들려면 첫 실행에만
`--rebuild-index`를 붙인다. 이 옵션은 목록 전체를 네트워크로 다시 읽는다.

### 3. 제목 필터와 검수

```bash
python crawling/filter_titles_additional_25.py crawling/work/additional_25/raw/10000recipe.csv --out crawling/work/additional_25/filtered/10000recipe.csv --rejected-out crawling/work/additional_25/rejected/10000recipe.csv
python crawling/filter_titles_additional_25.py crawling/work/additional_25/raw/wtable.csv --out crawling/work/additional_25/filtered/wtable.csv --rejected-out crawling/work/additional_25/rejected/wtable.csv
python crawling/filter_titles_additional_25.py crawling/work/additional_25/raw/semie.csv --out crawling/work/additional_25/filtered/semie.csv --rejected-out crawling/work/additional_25/rejected/semie.csv
```

```bash
python crawling/crawl_data_tool.py validate crawling/work/additional_25/filtered/10000recipe.csv --menu-file crawling/menu_queries_additional_25.txt --check-titles
python crawling/crawl_data_tool.py validate crawling/work/additional_25/filtered/wtable.csv --menu-file crawling/menu_queries_additional_25.txt --check-titles
python crawling/crawl_data_tool.py validate crawling/work/additional_25/filtered/semie.csv --menu-file crawling/menu_queries_additional_25.txt --check-titles
python crawling/crawl_data_tool.py combine crawling/work/additional_25/filtered/10000recipe.csv crawling/work/additional_25/filtered/wtable.csv crawling/work/additional_25/filtered/semie.csv --out crawling/work/additional_25/filtered/all_sites.csv
python crawling/crawl_data_tool.py validate crawling/work/additional_25/filtered/all_sites.csv --menu-file crawling/menu_queries_additional_25.txt --check-titles --require-all-menus
```

검수는 메뉴별 고유 URL 수와 재료 행 수를 출력한다. 빈 필드, 정확히 같은
행, 메뉴 누락, 필터에 어긋나는 제목을 보고한다. 사이트 하나에 특정 메뉴가
없는 것은 정상일 수 있으므로 누락과 정확히 같은 행은 경고하고, 최종 3사이트
합친 결과에서만 25개 전체를 필수로 검증한다. 10개 미만은 경고만 내고
다른 요리를 섞어 숫자를 맞추지 않는다. 하나의 URL이 여러 메뉴에 속하면
경고하므로 사람이 두 메뉴 중 어느 쪽인지 확인한다.

### 4. 기존 데이터와 staging 병합

아래 명령은 커밋된 CSV를 바꾸지 않고 staging 복사본만 만든다. 검수가
끝나기 전에는 staging 결과를 기존 경로로 복사하지 않는다.

```bash
python crawling/crawl_data_tool.py merge-menus --base crawling/menu_queries.txt --additional crawling/menu_queries_additional_25.txt --out crawling/work/additional_25/staging/menu_queries.txt
python crawling/crawl_data_tool.py merge --base crawling/10000recipe_recipes_raw.csv --additional crawling/work/additional_25/filtered/10000recipe.csv --menu-file crawling/menu_queries_additional_25.txt --out crawling/work/additional_25/staging/10000recipe_recipes_raw.csv
python crawling/crawl_data_tool.py merge --base crawling/wtable_recipes_raw.csv --additional crawling/work/additional_25/filtered/wtable.csv --menu-file crawling/menu_queries_additional_25.txt --out crawling/work/additional_25/staging/wtable_recipes_raw.csv
python crawling/crawl_data_tool.py merge --base crawling/semie_recipes_raw.csv --additional crawling/work/additional_25/filtered/semie.csv --menu-file crawling/menu_queries_additional_25.txt --out crawling/work/additional_25/staging/semie_recipes_raw.csv
```

같은 staging 파일을 다시 만들 때는 해당 명령에 `--force`를 붙인다.

### 5. staging 정규화·prior·사전 재생성

```bash
python crawling/normalize_ingredients.py crawling/work/additional_25/staging/10000recipe_recipes_raw.csv --out-dir crawling/work/additional_25/staging --out-prefix 10000recipe
python crawling/normalize_ingredients.py crawling/work/additional_25/staging/wtable_recipes_raw.csv --out-dir crawling/work/additional_25/staging --out-prefix wtable
python crawling/normalize_ingredients.py crawling/work/additional_25/staging/semie_recipes_raw.csv --out-dir crawling/work/additional_25/staging --out-prefix semie
python crawling/merge_corpora_additional_25.py --input-dir crawling/work/additional_25/staging --out crawling/work/additional_25/staging/merged_recipes_raw.csv
python crawling/normalize_ingredients.py crawling/work/additional_25/staging/merged_recipes_raw.csv --out-dir crawling/work/additional_25/staging --out-prefix merged
python -m ai_web_search_agent.build_dictionary_additional_25 --crawling-dir crawling/work/additional_25/staging --taxonomy crawling/ingredient_taxonomy.csv --out crawling/work/additional_25/staging/ingredient_dictionary.csv
```

prior의 공식은 `alpha = k_count + 1`, `beta = (n_total - k_count) + 1`을 유지한다.
신규 표준 재료가 생기면 `ingredient_taxonomy.csv`, `ingredient_translations.csv`와
잘못 붙는 알레르겐 태그를 사람이 검토한 뒤 시드를 재생성한다. 신규 메뉴와
재료는 가다나순 ID를 밀 수 있으므로 백엔드는 시드를 전체 재적재하고 기존
사장님 답변·캐시의 ID를 확인해야 한다.

### 6. 사람 검수 후에만 기존 경로로 반영

다음을 먼저 직접 확인한다.

- `rejected/`에 정상 레시피가 표기 차이 때문에 빠지지 않았는지
- `filtered/`에 볶음밥·칼국수·정식·다른 요리가 남지 않았는지
- 재료명에 조리도구, 수량, 설명 문장이 남지 않았는지
- 대표 재료가 비정상적으로 적은 메뉴가 있는지
- 10개 미만 메뉴를 그대로 쓸지, 검색어를 보강해 다시 받을지
- 신규 표준 재료의 taxonomy·번역·알레르겐 태그가 맞는지

검수를 승인한 뒤에만 staging 파일을 기존 경로로 복사한다.

```bash
cp crawling/work/additional_25/staging/menu_queries.txt crawling/menu_queries.txt
cp crawling/work/additional_25/staging/10000recipe_recipes_raw.csv crawling/10000recipe_recipes_raw.csv
cp crawling/work/additional_25/staging/10000recipe_normalized.csv crawling/10000recipe_normalized.csv
cp crawling/work/additional_25/staging/10000recipe_prior.csv crawling/10000recipe_prior.csv
cp crawling/work/additional_25/staging/10000recipe_review.csv crawling/10000recipe_review.csv
cp crawling/work/additional_25/staging/wtable_recipes_raw.csv crawling/wtable_recipes_raw.csv
cp crawling/work/additional_25/staging/wtable_normalized.csv crawling/wtable_normalized.csv
cp crawling/work/additional_25/staging/wtable_prior.csv crawling/wtable_prior.csv
cp crawling/work/additional_25/staging/wtable_review.csv crawling/wtable_review.csv
cp crawling/work/additional_25/staging/semie_recipes_raw.csv crawling/semie_recipes_raw.csv
cp crawling/work/additional_25/staging/semie_normalized.csv crawling/semie_normalized.csv
cp crawling/work/additional_25/staging/semie_prior.csv crawling/semie_prior.csv
cp crawling/work/additional_25/staging/semie_review.csv crawling/semie_review.csv
cp crawling/work/additional_25/staging/merged_prior.csv crawling/merged_prior.csv
cp crawling/work/additional_25/staging/merged_review.csv crawling/merged_review.csv
cp crawling/work/additional_25/staging/ingredient_dictionary.csv ai_web_search_agent/data/ingredient_dictionary.csv
```

### 7. 코드 검증

```bash
pytest -q
python -m compileall -q app.py ai_ocr ai_ruleengine ai_result ai_supervisor ai_normalization ai_exact_feedback ai_decision_policy ai_web_search_agent ai_ontology ai_bayesian ai_db_update
git diff --check
```
