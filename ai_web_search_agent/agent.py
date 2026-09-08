from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from cache import WebSearchCache
from config import AgentConfig
from extractor import extract_ingredient_evidence
from providers import SearchProvider, StaticSearchProvider
from query_builder import build_queries
from ruleengine_client import RuleEngineClient

BASE_DIR = Path(__file__).resolve().parents[1]
RULE_DIR = BASE_DIR / "ai_ruleengine"
if str(RULE_DIR) not in sys.path:
    sys.path.insert(0, str(RULE_DIR))

from ingredient_tagger import tag_explicit, tag_variants  # noqa: E402
from menu_matcher import find_base_menu  # noqa: E402
from modifier_strip import strip_modifiers  # noqa: E402


class WebSearchAgent:
    def __init__(
        self,
        provider: SearchProvider | None = None,
        config: AgentConfig | None = None,
        cache: WebSearchCache | None = None,
    ):
        self.config = config or AgentConfig()
        self.provider = provider or self._build_provider()
        self.cache = cache or WebSearchCache(self.config.db_path, self.config.cache_ttl_days)
        self.ruleengine = RuleEngineClient()

    def verify(self, rule_result: dict, use_cache: bool = True) -> dict:
        normalized_menu_name, queries = build_queries(rule_result, max_queries=self.config.max_queries)

        if use_cache:
            cached = self.cache.get(normalized_menu_name)
            if cached:
                return _with_rule_comparison(cached, rule_result)

        all_results = []
        for query in queries:
            all_results.extend(self.provider.search(query, self.config.top_k))

        evidence_by_ingredient = extract_ingredient_evidence(normalized_menu_name, all_results)
        ingredients = [row.to_dict() for row in evidence_by_ingredient.values()]
        ingredients.sort(key=lambda row: (-row["evidence_score"], row["name"]))

        web_tags = sorted({tag for row in ingredients for tag in row["tags"]})
        sources = _unique_sources(ingredients)
        evidence_score = round(max([row["evidence_score"] for row in ingredients] or [0.0]), 4)
        status = "searched" if ingredients else "unresolved"

        payload = {
            "menu_name_ko": rule_result.get("menu_name_ko"),
            "normalized_menu_name": normalized_menu_name,
            "search_status": status,
            "queries": queries,
            "ingredients": ingredients,
            "web_tags": web_tags,
            "sources": sources,
            "evidence_score": evidence_score,
            "searched_at": datetime.now(timezone.utc).isoformat(),
            "search_provider": self.provider.name,
            "max_search_depth": self.config.max_search_depth,
            "unresolved_ingredients": [] if ingredients else [normalized_menu_name],
        }
        payload = _with_rule_comparison(payload, rule_result)
        self.cache.save(normalized_menu_name, payload)
        return payload

    def verify_all(self, judged_result: dict, use_cache: bool = True) -> dict:
        result = dict(judged_result)
        analyses = []
        for rule_result in judged_result.get("menu_analyses", []):
            enriched = dict(rule_result)
            enriched["web_verification"] = self.verify(rule_result, use_cache=use_cache)
            enriched["risk_level_after_web"] = _merge_risk_level(rule_result, enriched["web_verification"])
            analyses.append(enriched)
        result["menu_analyses"] = analyses
        session = dict(result.get("scan_session", {}))
        session["risky_menu_count_after_web"] = sum(
            1 for item in analyses if item.get("risk_level_after_web") in ("danger", "caution")
        )
        result["scan_session"] = session
        return result

    def analyze_and_verify_all(
        self,
        ocr_result: dict,
        profile: dict,
        use_cache: bool = True,
        verbose: bool = False,
    ) -> dict:
        judged_result = self.ruleengine.analyze_ocr_result(ocr_result, profile, verbose=verbose)
        return self.verify_all(judged_result, use_cache=use_cache)

    def _build_provider(self) -> SearchProvider:
        if self.config.provider_name == "tavily" and self.config.tavily_api_key:
            from tavily_provider import TavilySearchProvider

            return TavilySearchProvider(self.config.tavily_api_key)
        return StaticSearchProvider()


def _with_rule_comparison(payload: dict, rule_result: dict) -> dict:
    payload = dict(payload)
    rule_tags = sorted(_derive_rule_tags(rule_result))
    web_tags = set(payload.get("web_tags", []))
    forbidden_tags = set(rule_result.get("forbidden_tags", []))
    web_forbidden_hits = sorted(web_tags & forbidden_tags)

    payload["rule_tags"] = rule_tags
    payload["web_only_tags"] = sorted(web_tags - set(rule_tags))
    payload["web_forbidden_hits"] = web_forbidden_hits
    payload["verification_status"] = (
        "additional_forbidden_risk_found"
        if web_forbidden_hits
        else "additional_risk_found"
        if payload["web_only_tags"]
        else "no_additional_risk_found"
    )
    return payload


def _derive_rule_tags(rule_result: dict) -> set[str]:
    stripped_name, _ = strip_modifiers(rule_result.get("menu_name_ko") or "")
    base_menu, remain_tokens = find_base_menu(stripped_name)
    tags = set(rule_result.get("hit_tags", [])) - {"is_spicy"}
    if base_menu:
        tags |= tag_explicit(base_menu)
        tags |= tag_variants(remain_tokens)
    return tags


def _merge_risk_level(rule_result: dict, web_result: dict) -> str:
    current = rule_result.get("risk_level", "safe")
    if current == "danger":
        return "danger"
    if web_result.get("web_forbidden_hits"):
        return "danger"
    if current == "caution" or web_result.get("web_only_tags"):
        return "caution"
    return "safe"


def _unique_sources(ingredients: list[dict]) -> list[dict]:
    by_url = {}
    for ingredient in ingredients:
        for source in ingredient.get("sources", []):
            by_url.setdefault(source["url"], source)
    return list(by_url.values())
