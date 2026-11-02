"""Measure ⑥ against the hand-written menus (``ai_ruleengine/data/menus.csv``).

    python -m evaluation.websearch                 # search, then score
    python -m evaluation.websearch --raw FILE      # score saved results again
    python -m evaluation.websearch --limit 10 --price-in IN --price-out OUT
                                                   # 10 menus, with the LLM cost
                                                   # (IN/OUT: price per 1M tokens)

The 77 menus there carry one standard recipe each (농림축산식품부, 한식진흥원), written
by the team. Each menu goes through ⑥ stage 1 as if the DB did not know it, with the
same search, page reading and LLM as production. Both the standard recipe and every
web recipe are turned into allergen tags with the same keyword table
(``ai_ruleengine.constants.VARIANT_INGREDIENTS``), so the numbers measure what ⑥
collects, not the tagger. ④ expands composites (김치 → 멸치액젓) from the backend
ingredient graph, which is not available offline, so neither side expands here.

Recall is the main number: a standard-recipe allergen ⑥ never found is a miss (FN).
An allergen only the web found is counted as FP but is often real (restaurants add
새우 to 김치찌개), so precision is for reference only.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ai_ruleengine.constants import ALLERGY_TAGS, VARIANT_INGREDIENTS
from ai_supervisor.contracts.web_search import WebCollectRequest
from evaluation.cost import MeteredClient, UsageMeter, cost_lines

ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / "ai_ruleengine" / "data" / "menus.csv"
OUT_DIR = ROOT / "outputs" / "websearch_eval"
# 0 means "at least one recipe mentions it": any mention reaches ⑤ as an observation.
MIN_SHARES = (0.0, 0.3, 0.5)
CONCURRENCY = 3
BOOTSTRAP_ROUNDS = 2000

_SORTED_KEYWORDS = {
    tag: sorted(VARIANT_INGREDIENTS[tag], key=len, reverse=True) for tag in ALLERGY_TAGS
}


# Words that contain a keyword but not that allergen. The rule engine's plain substring
# check tags 스파게티 as crab. Anything not listed here stays a substring match, so a
# doubtful name is tagged rather than missed.
_FALSE_HITS = {"게": ("스파게티",)}


def _has_keyword(name: str, keyword: str) -> bool:
    for word in _FALSE_HITS.get(keyword, ()):
        name = name.replace(word, "")
    return keyword in name


def tag_names(names: list[str]) -> set[str]:
    """식약처 19 allergen tags found in these ingredient names (longest keyword first)."""

    tags: set[str] = set()
    for name in names:
        name = name.replace(" ", "")
        for tag, keywords in _SORTED_KEYWORDS.items():
            if any(_has_keyword(name, keyword) for keyword in keywords):
                tags.add(tag)
    return tags


@dataclass
class GoldMenu:
    name: str
    ingredients: list[str]
    allergens: set[str]


@dataclass
class MenuRun:
    """⑥ stage 1 result for one menu, kept as plain data so it can be saved and rescored."""

    name: str
    sources: list[dict] = field(default_factory=list)  # {"url", "title", "ingredients"}
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def predicted(self, min_share: float) -> set[str]:
        """Allergens in at least ``min_share`` of the recipes (any recipe when 0)."""

        if not self.sources:
            return set()
        counts: dict[str, int] = {}
        for source in self.sources:
            for tag in tag_names(source["ingredients"]):
                counts[tag] = counts.get(tag, 0) + 1
        total = len(self.sources)
        return {tag for tag, k in counts.items() if k >= 1 and k / total >= min_share}


def load_gold(path: Path = GOLD_PATH) -> list[GoldMenu]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    menus = []
    for row in rows:
        ingredients = [name.strip() for name in row["레시피"].split(",") if name.strip()]
        menus.append(GoldMenu(row["메뉴명"].strip(), ingredients, tag_names(ingredients)))
    return menus


# ---------------------------------------------------------------- scoring


@dataclass
class Counts:
    tp: int = 0
    fn: int = 0
    fp: int = 0

    def add(self, other: "Counts") -> None:
        self.tp += other.tp
        self.fn += other.fn
        self.fp += other.fp


def f2(counts: Counts) -> float:
    denominator = 5 * counts.tp + 4 * counts.fn + counts.fp
    return 5 * counts.tp / denominator if denominator else 0.0


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes out of n."""

    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


def bootstrap_f2(per_menu: list[Counts], rounds: int = BOOTSTRAP_ROUNDS, seed: int = 0) -> tuple[float, float]:
    """95% interval of F2, resampling menus (allergens of one menu move together)."""

    if not per_menu:
        return (0.0, 0.0)
    rng = random.Random(seed)
    scores = []
    for _ in range(rounds):
        total = Counts()
        for counts in rng.choices(per_menu, k=len(per_menu)):
            total.add(counts)
        scores.append(f2(total))
    scores.sort()
    return (scores[int(0.025 * rounds)], scores[int(0.975 * rounds) - 1])


@dataclass
class Score:
    min_share: float
    total: Counts
    per_menu: list[Counts]
    per_allergen: dict[str, Counts]
    missed: list[tuple[str, str]]  # (menu, allergen)
    extra: list[tuple[str, str]]


def score(gold: list[GoldMenu], runs: dict[str, MenuRun], min_share: float) -> Score:
    result = Score(min_share, Counts(), [], {tag: Counts() for tag in ALLERGY_TAGS}, [], [])
    for menu in gold:
        run = runs.get(menu.name)
        predicted = run.predicted(min_share) if run else set()
        counts = Counts()
        for tag in ALLERGY_TAGS:
            if tag in menu.allergens and tag in predicted:
                counts.tp += 1
                result.per_allergen[tag].tp += 1
            elif tag in menu.allergens:
                counts.fn += 1
                result.per_allergen[tag].fn += 1
                result.missed.append((menu.name, tag))
            elif tag in predicted:
                counts.fp += 1
                result.per_allergen[tag].fp += 1
                result.extra.append((menu.name, tag))
        result.per_menu.append(counts)
        result.total.add(counts)
    return result


# ---------------------------------------------------------------- running ⑥


def collect_request(menu_name: str, index: int) -> WebCollectRequest:
    return WebCollectRequest.model_validate(
        {
            "context": {
                "schema_version": "3.0",
                "trace_id": str(uuid.uuid4()),
                "scan_session_id": "websearch-eval",
                "store_id": None,
                "call_scope": "item",
                "item_id": f"websearch-eval:{index}",
            },
            "menu": {"menu_id": None, "normalized_menu_name": menu_name, "locale_hint": "ko"},
            "max_sources": 10,
            "cached_sources": [],
            "warnings": [],
            "errors": [],
        }
    )


async def collect_all(agent, names: list[str], concurrency: int = CONCURRENCY) -> dict[str, MenuRun]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(index: int, name: str) -> MenuRun:
        async with semaphore:
            response = await agent.run(collect_request(name, index))
        return MenuRun(
            name=name,
            sources=[
                {
                    "url": source.source_url,
                    "title": source.title,
                    "ingredients": [ref.canonical_name for ref in source.ingredients],
                }
                for source in response.sources
            ],
            warnings=[warning.code for warning in response.warnings],
            errors=[error.code for error in response.errors],
        )

    runs = await asyncio.gather(*(one(i, name) for i, name in enumerate(names)))
    return {run.name: run for run in runs}


def production_agent(meter: UsageMeter | None = None):
    from dotenv import load_dotenv

    from ai_web_search_agent.collect import WebCollectAgent
    from ai_web_search_agent.pages import HttpPageFetcher
    from ai_web_search_agent.providers import UnconfiguredSearchProvider, search_provider_from_env
    from ai_web_search_agent.reader import OpenAIRecipeReader

    load_dotenv(ROOT / ".env")
    provider = search_provider_from_env()
    if isinstance(provider, UnconfiguredSearchProvider):
        raise SystemExit("NAVER_SEARCH_CLIENT_ID / NAVER_SEARCH_CLIENT_SECRET가 없어 검색할 수 없어요")
    reader = OpenAIRecipeReader.from_env_if_configured()
    if reader is None:
        print("OPENAI_API_KEY가 없어 LLM 없이 재료 사전만으로 수집해요")
    elif meter is not None:
        reader.client = MeteredClient(reader.client, meter)
    return WebCollectAgent(provider, reader, pages=HttpPageFetcher())


def save_runs(runs: dict[str, MenuRun], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [run.__dict__ for run in runs.values()]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_runs(path: Path) -> dict[str, MenuRun]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {item["name"]: MenuRun(**item) for item in data}


# ---------------------------------------------------------------- report


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _share_label(min_share: float) -> str:
    return "레시피 1개 이상" if min_share == 0 else f"레시피 {int(min_share * 100)}% 이상"


def render_report(
    gold: list[GoldMenu],
    runs: dict[str, MenuRun],
    scores: list[Score],
    cost: list[str] | None = None,
) -> str:
    found = [menu for menu in gold if runs.get(menu.name) and runs[menu.name].sources]
    recipe_counts = [len(runs[menu.name].sources) for menu in found]
    gold_pairs = sum(len(menu.allergens) for menu in gold)
    lines = [
        "# ⑥ 웹서치 평가",
        "",
        f"- 정답: `ai_ruleengine/data/menus.csv` 메뉴 {len(gold)}개, 정답 (메뉴 × 알레르겐) {gold_pairs}개",
        f"- 레시피를 하나라도 찾은 메뉴: {len(found)}/{len(gold)} ({_pct(len(found) / len(gold)) if gold else '-'})",
        f"- 찾은 메뉴의 평균 레시피 수: {sum(recipe_counts) / len(recipe_counts):.1f}개" if recipe_counts else "- 찾은 레시피 없음",
        "",
        "## 알레르겐 재현율·F2",
        "",
        "| 판단 기준 | 재현율 (95% CI) | 정밀도 (참고, 95% CI) | F2 (95% CI) | 놓침 | 정답 외 |",
        "|---|---|---|---|---|---|",
    ]
    for item in scores:
        t = item.total
        recall_lo, recall_hi = wilson(t.tp, t.tp + t.fn)
        precision_lo, precision_hi = wilson(t.tp, t.tp + t.fp)
        f2_lo, f2_hi = bootstrap_f2(item.per_menu)
        recall = t.tp / (t.tp + t.fn) if t.tp + t.fn else 0.0
        precision = t.tp / (t.tp + t.fp) if t.tp + t.fp else 0.0
        lines.append(
            f"| {_share_label(item.min_share)} "
            f"| {_pct(recall)} ({_pct(recall_lo)}–{_pct(recall_hi)}) "
            f"| {_pct(precision)} ({_pct(precision_lo)}–{_pct(precision_hi)}) "
            f"| {f2(t):.3f} ({f2_lo:.3f}–{f2_hi:.3f}) | {t.fn} | {t.fp} |"
        )

    main = scores[0]
    lines += [
        "",
        f"## 알레르겐별 ({_share_label(main.min_share)})",
        "",
        "| 알레르겐 | 정답 수 | 찾음 | 재현율 |",
        "|---|---|---|---|",
    ]
    for tag in ALLERGY_TAGS:
        counts = main.per_allergen[tag]
        n = counts.tp + counts.fn
        if n:
            lines.append(f"| `{tag}` | {n} | {counts.tp} | {_pct(counts.tp / n)} |")

    lines += ["", f"## 놓친 알레르겐 ({_share_label(main.min_share)})", ""]
    if not main.missed:
        lines.append("없음")
    by_menu: dict[str, list[str]] = {}
    for menu_name, tag in main.missed:
        by_menu.setdefault(menu_name, []).append(tag)
    for menu_name, tags in by_menu.items():
        run = runs.get(menu_name)
        note = f"레시피 {len(run.sources)}개" if run and run.sources else "레시피 못 찾음"
        if run and run.errors:
            note += f", {', '.join(run.errors)}"
        lines.append(f"- {menu_name} ({note}): {', '.join(f'`{tag}`' for tag in tags)}")

    warning_counts: dict[str, int] = {}
    for run in runs.values():
        for code in run.warnings + run.errors:
            warning_counts[code] = warning_counts.get(code, 0) + 1
    if warning_counts:
        lines += ["", "## ⑥ 경고·오류", "", "| 코드 | 메뉴 수 |", "|---|---|"]
        lines += [f"| `{code}` | {n} |" for code, n in sorted(warning_counts.items())]
    if cost:
        lines += ["", "## LLM 비용 (레시피 판별)", "", *cost]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gold", type=Path, default=GOLD_PATH)
    parser.add_argument("--raw", type=Path, help="이전에 저장한 ⑥ 결과 JSON (주면 검색하지 않음)")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--limit", type=int, help="앞에서부터 이만큼의 메뉴만")
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY)
    parser.add_argument("--price-in", type=float, help="입력 1M 토큰당 가격 (BAZE 대시보드 값)")
    parser.add_argument("--price-out", type=float, help="출력 1M 토큰당 가격")
    args = parser.parse_args(argv)

    gold = load_gold(args.gold)[: args.limit]
    cost = None
    if args.raw:
        runs = load_runs(args.raw)
    else:
        meter = UsageMeter()
        runs = asyncio.run(collect_all(production_agent(meter), [m.name for m in gold], args.concurrency))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        raw_path = args.out / f"raw_{stamp}.json"
        save_runs(runs, raw_path)
        (args.out / f"usage_{stamp}.json").write_text(json.dumps(meter.as_dict()), encoding="utf-8")
        print(f"⑥ 결과 저장: {raw_path}")
        cost = cost_lines(meter, len(gold), "메뉴", args.price_in, args.price_out)

    scores = [score(gold, runs, share) for share in MIN_SHARES]
    report = render_report(gold, runs, scores, cost)
    args.out.mkdir(parents=True, exist_ok=True)
    report_path = args.out / "report.md"
    report_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"보고서 저장: {report_path}")


if __name__ == "__main__":
    main()
