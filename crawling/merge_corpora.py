# -*- coding: utf-8 -*-
"""
사이트별 크롤링 원본(raw)을 하나로 합치는 스크립트.

합친 파일을 normalize_ingredients.py로 정규화하면 사이트 구분 없이 계산한
prior(merged_prior.csv)를 얻는다. 실험·분석용이며, 백엔드 적재는 사이트별
prior(<사이트>_prior.csv)를 사용한다 (menu_recipe_corpora.corpus 단위).

실행:
    python crawling/merge_corpora.py
    python crawling/normalize_ingredients.py crawling/merged_recipes_raw.csv --out-prefix merged
"""
import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = ["semie", "wtable", "10000recipe"]
FIELDS = ["menu_query", "recipe_title", "recipe_url", "ingredient_name"]


def main():
    out_path = os.path.join(HERE, "merged_recipes_raw.csv")
    total = 0
    with open(out_path, "w", newline="", encoding="utf-8-sig") as out:
        writer = csv.DictWriter(out, fieldnames=FIELDS)
        writer.writeheader()
        for source in SOURCES:
            path = os.path.join(HERE, f"{source}_recipes_raw.csv")
            with open(path, encoding="utf-8-sig") as f:
                count = 0
                for row in csv.DictReader(f):
                    writer.writerow({k: row[k] for k in FIELDS})
                    count += 1
            print(f"{source}: {count}행")
            total += count
    print(f"합계: {total}행 -> {out_path}")


if __name__ == "__main__":
    main()
