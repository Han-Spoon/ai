"""QA harness: a menu photo's OCR lines -> Prepare -> seed KnowledgeBundle -> Judge.

Runs the real Supervisor service end to end with no network and no LLM:

- OCR is a fake runner that returns the given menu lines (CLOVA is not called).
- Knowledge comes from the repo seed (``crawling/db_seed.py``), the same rows the
  backend loads. Candidate lookup is exact-name only, like a backend without fuzzy search.
- Web search returns the recipes a scenario gives (or nothing).
- Every LLM slot is the rules-only fallback, so results are deterministic.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from ai_decision_policy import DecisionTool, HighRiskReviewAgent
from ai_normalization import CandidateSelectionAgent, NameCleaningAgent
from ai_ontology import normalize
from ai_ontology.tests.seed import seed
from ai_supervisor.adapters import LegacyOcrAdapter
from ai_supervisor.contracts.external import JudgeRequest, JudgeResponse, PrepareRequest, PrepareResponse
from ai_supervisor.contracts.web_search import WebCollectResponse, WebMenu
from ai_supervisor.service import SupervisorService, SupervisorServiceDependencies

TRACE = UUID("0199a2f0-0000-7000-8000-000000000200")
PREPARE = UUID("0199a2f0-0000-7000-8000-000000000201")
SESSION = "qa"
NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
EMPTY_ONTOLOGY = {
    "status": "not_found",
    "menu": None,
    "recipe_ingredients": [],
    "menu_recipe_corpora": [],
    "menu_ingredient_priors": [],
    "cache": None,
}


@dataclass
class Scan:
    """One scan: what the menu board shows, who scans it, and what the DB knows."""

    menus: list[str]
    allergies: list[str] = field(default_factory=list)
    religion: str | None = None
    vegetarian: str | None = None
    no_alcohol: bool = False
    no_spicy: bool = False
    spicy: set[str] = field(default_factory=set)  # OCR saw a chili mark next to these
    store_id: int | None = 1
    scan_quality: str = "usable"
    locale: str = "en"
    # menu name -> {ingredient: "present" | "absent" | "unknown"} owner answers
    owner: dict[str, dict[str, str]] = field(default_factory=dict)
    # menu name -> [(recipe title, [ingredient names])] the web search finds
    web: dict[str, list[tuple[str, list[str]]]] = field(default_factory=dict)
    # menu name -> [(seed menu name, score, match_type)] instead of exact-name lookup
    candidates: dict[str, list[tuple[str, float, str]]] = field(default_factory=dict)
    # menu name -> {ingredient: (alpha, beta)} store prior
    store_prior: dict[str, dict[str, tuple[float, float]]] = field(default_factory=dict)
    reliability: dict[str, float] = field(default_factory=dict)
    # menu name -> ingredients_snapshot the backend saved from an earlier scan
    cache: dict[str, list[dict]] = field(default_factory=dict)
    # menu name -> {ambiguity_flag: (constraint_tags, status)} owner answers to flag cards
    owner_flags: dict[str, dict[str, tuple[list[str], str]]] = field(default_factory=dict)


@dataclass
class Result:
    prepare: PrepareResponse
    judge: JudgeResponse | None

    def item(self, name: str):
        names = {p.item_id: p.display_name for p in self.prepare.prepared_items}
        target = normalize(name)
        return next(
            i for i in self.judge.items if normalize(names[i.item_id]) == target
        )

    def commands(self, action: str | None = None) -> list:
        cmds = self.judge.persistence_commands
        return [c for c in cmds if action is None or c.action == action]


def _ocr(scan: Scan):
    def runner(image):
        return {
            "final": {
                "scan_quality": {
                    "status": scan.scan_quality,
                    "score": {"usable": 95, "low_confidence": 60, "needs_retake": 20}[scan.scan_quality],
                    "reasons": [],
                    "retake_suggestions": [],
                },
                "menu_analyses": [
                    {
                        "display_order": i + 1,
                        "menu_name_ko": m,
                        "description_ko": None,
                        "price_text": None,
                        "price_options": [],
                        "ocr_confidence": 0.95,
                        "is_spicy": m in scan.spicy,
                    }
                    for i, m in enumerate(scan.menus)
                ],
            }
        }

    return runner


class FakeWeb:
    def __init__(self, scan: Scan) -> None:
        self.scan = scan
        self.calls: list[str] = []

    async def run(self, request):
        name = request.menu.normalized_menu_name
        self.calls.append(name)
        recipes = next((v for k, v in self.scan.web.items() if normalize(k) == name), [])
        sources = [
            {
                "source_url": f"https://recipe.example/{name}/{i}",
                "title": title,
                "fetched_at": NOW,
                "evidence": {
                    "evidence_id": f"web:{name}:{i}",
                    "evidence_class": "soft",
                    "source_type": "web_search",
                    "source_ref": f"https://recipe.example/{name}/{i}",
                    "verification_status": "pending_review",
                    "reliability_weight": None,
                    "observed_at": NOW,
                },
                "ingredients": [{"ingredient_id": None, "canonical_name": n} for n in ings],
            }
            for i, (title, ings) in enumerate(recipes)
        ]
        return WebCollectResponse.model_validate(
            {
                "context": request.context.model_dump(mode="json"),
                "menu": WebMenu(menu_id=request.menu.menu_id, normalized_menu_name=name).model_dump(),
                "found": bool(sources),
                "sources": sources,
                "warnings": [w.model_dump(mode="json") for w in request.warnings],
                "errors": [e.model_dump(mode="json") for e in request.errors],
            }
        )


def _evidence(eid: str) -> dict:
    return {
        "evidence_id": eid,
        "evidence_class": "hard",
        "source_type": "owner_feedback",
        "source_ref": eid,
        "verification_status": "owner_confirmed",
        "reliability_weight": None,
        "observed_at": NOW,
    }


def _knowledge(scan: Scan, item, s) -> dict:
    lookup = scan.candidates.get(item.display_name)
    if lookup is None:
        by_name = {normalize(m): m for m in s.menu_id}
        hit = by_name.get(normalize(item.normalized_menu_name))
        lookup = [(hit, 1.0, "canonical")] if hit else []
    lookup = sorted(lookup, key=lambda c: (-c[1], s.menu_id[c[0]]))
    cands, know = [], []
    owner = scan.owner.get(item.display_name, {})
    prior = scan.store_prior.get(item.display_name, {})
    for menu, score, match_type in lookup:
        mid = s.menu_id[menu]
        cands.append(
            {"menu_id": mid, "menu_category_id": None, "canonical_name": menu, "score": score, "match_type": match_type}
        )
        tags = s.data.tags
        exact = {"status": "not_found", "exact": None}
        flags = scan.owner_flags.get(item.display_name, {})
        if (owner or flags) and scan.store_id is not None:
            exact = {
                "status": "ok",
                "exact": {
                    "menu_id": mid,
                    "confirmations": [
                        {
                            "ingredient": {"ingredient_id": s.id[n], "canonical_name": n},
                            "constraint_tags": sorted(tags[n]),
                            "status": st,
                            "evidence": _evidence(f"owner:{mid}:{s.id[n]}"),
                        }
                        for n, st in owner.items()
                    ],
                    "flag_confirmations": [
                        {
                            "ambiguity_flag": flag,
                            "constraint_tags": tags_,
                            "status": st,
                            "evidence": _evidence(f"owner-flag:{mid}:{flag}"),
                        }
                        for flag, (tags_, st) in flags.items()
                    ],
                },
            }
        priors = {"status": "not_found", "store": [], "evidence": []}
        if prior and scan.store_id is not None:
            priors = {
                "status": "ok",
                "store": [
                    {"ingredient_id": s.id[n], "alpha": a, "beta": b, "evidence_refs": [f"store:{mid}:{s.id[n]}"]}
                    for n, (a, b) in prior.items()
                ],
                "evidence": [
                    {**_evidence(f"store:{mid}:{s.id[n]}"), "source_type": "recipe", "evidence_class": "soft",
                     "verification_status": "approved"}
                    for n in prior
                ],
            }
        ontology = s.ontology(menu).model_dump(mode="json")
        snapshot = scan.cache.get(item.display_name)
        if snapshot is not None and scan.store_id is not None:
            ontology["cache"] = {"menu_revision": 1, "ingredients_snapshot": snapshot}
        know.append(
            {
                "menu_id": mid,
                "exact_feedback": exact,
                "ontology": ontology,
                "priors": priors,
            }
        )
    return {
        "item_id": item.item_id,
        "candidate_lookup": {"status": "ok" if cands else "not_found", "candidates": cands},
        "candidate_knowledge": know,
        "fallback_ontology": EMPTY_ONTOLOGY,
        "web_search_cache": {"status": "not_found", "cached_sources": []},
    }


def _service(scan: Scan) -> tuple[SupervisorService, FakeWeb]:
    web = FakeWeb(scan)
    svc = SupervisorService(
        SupervisorServiceDependencies(
            ocr=LegacyOcrAdapter(_ocr(scan), observed_at_factory=lambda: NOW),
            web_collect=web,
            decision=DecisionTool(None),
            high_risk_review=HighRiskReviewAgent(None),
            normalization=NameCleaningAgent(None),
            candidate_selection=CandidateSelectionAgent(None),
            prepare_id_factory=lambda: PREPARE,
            now_factory=lambda: NOW,
        )
    )
    return svc, web


async def _run(scan: Scan) -> tuple[Result, FakeWeb]:
    s = seed()
    svc, web = _service(scan)
    prep = await svc.prepare(
        PrepareRequest.model_validate(
            {
                "schema_version": "3.0",
                "trace_id": str(TRACE),
                "scan_session_id": SESSION,
                "image": {"source": "upload", "storage_key": "qa.jpg", "version_id": None, "expected_etag": None},
                "locale": scan.locale,
            }
        )
    )
    if prep.scan_quality.status != "usable" or not prep.prepared_items:
        return Result(prep, None), web
    vegan = scan.vegetarian
    request = JudgeRequest.model_validate(
        {
            "schema_version": "3.0",
            "trace_id": str(TRACE),
            "prepare_id": str(prep.prepare_id),
            "scan_session_id": SESSION,
            "store_id": scan.store_id,
            "prepared_items_hash": prep.prepared_items_hash,
            "knowledge_revision": "seed",
            "prepare_signals": prep.prepare_signals.model_dump(mode="json"),
            "prepared_items": [i.model_dump(mode="json") for i in prep.prepared_items],
            "knowledge_bundle": {
                "bundle_schema_version": "3.0",
                "ingredient_graph": s.graph.model_dump(mode="json"),
                "items": [_knowledge(scan, i, s) for i in prep.prepared_items],
                "source_reliability": {
                    "status": "ok" if scan.reliability else "not_found",
                    "weights": [{"source_key": k, "weight": w} for k, w in scan.reliability.items()],
                },
            },
            "user_profile": {
                "religion_type": scan.religion,
                "is_vegetarian": vegan is not None,
                "vegetarian_type": vegan,
                "no_alcohol": scan.no_alcohol,
                "allergies": scan.allergies,
                "no_spicy": scan.no_spicy,
            },
            "locale": scan.locale,
        }
    )
    res = await svc.judge(request)
    await svc.aclose()
    return Result(prep, res), web


def run(scan: Scan) -> Result:
    return asyncio.run(_run(scan))[0]


def run_with_web(scan: Scan) -> tuple[Result, FakeWeb]:
    return asyncio.run(_run(scan))


def answer_everything(scan: Scan, menu: str, present: set[str] = frozenset()) -> Result:
    """Two scans at one store: the first saves the cache and asks the owner; the owner
    answers every cached ingredient (``present`` ones yes, the rest no) and every flag
    card no; the second scan reads those answers."""

    first = run(scan)
    cached = next(
        c.payload["ingredients_snapshot"]
        for c in first.commands("save_menu_ingredient_cache")
        if c.payload["menu_id"] == first.item(menu).menu_id
    )
    names = [e["ingredient"]["canonical_name"] for e in cached]
    flag_cards = [c for c in first.item(menu).owner_cards if c.ambiguity_flag]
    second = Scan(**{**scan.__dict__})
    second.cache = {**scan.cache, menu: cached}
    second.owner = {**scan.owner, menu: {n: "present" if n in present else "absent" for n in names}}
    second.owner_flags = {
        **scan.owner_flags,
        menu: {c.ambiguity_flag: (list(c.constraint_tags), "absent") for c in flag_cards},
    }
    return run(second)


async def _prepare(scan: Scan) -> PrepareResponse:
    svc, _ = _service(scan)
    return await svc.prepare(
        PrepareRequest.model_validate(
            {
                "schema_version": "3.0",
                "trace_id": str(TRACE),
                "scan_session_id": SESSION,
                "image": {"source": "upload", "storage_key": "qa.jpg", "version_id": None, "expected_etag": None},
                "locale": scan.locale,
            }
        )
    )


def prepare_only(scan: Scan) -> PrepareResponse:
    return asyncio.run(_prepare(scan))
