"""Measure what ⑧'s LLM steps cost per call, then per scan (docs/8-agent-xai.md §7-1).

    python -m evaluation.xai_cost                          # ko and en, prices left out
    python -m evaluation.xai_cost --locales ko en ja --price-in IN --price-out OUT

A few fixed menus go through the real LLM once per language: three that need a reason
sentence (needs_reason) and one high-risk review. That is 4 calls per language, so the
default run is 8 calls. The per-call tokens then give a per-scan estimate:

    scan = menus × reason_share × (one reason call) + reviews × (one review call)

``--menus``, ``--reason-share`` and ``--reviews`` set that scan (default 10 menus, half
of them with a reason, 3 reviews: the most a scan reviews, §2-3).
"""

from __future__ import annotations

import argparse
import asyncio

from ai_decision_policy import DecisionTool, HighRiskReviewAgent, ReasonWriter, XaiLLM
from ai_decision_policy.tests.test_decision_policy import (
    FISH_ALLERGY,
    FISH_SAUCE,
    HALAL,
    PORK,
    probability,
    request,
    warning,
)
from ai_decision_policy.tests.test_llm import PORK_BELLY, review_request
from evaluation.cost import MeteredClient, UsageMeter, cost_lines


def reason_requests(locale: str) -> list:
    """Menus whose reason says something the verdict sentence does not (§8)."""

    return [
        request(HALAL, probability_results=[probability(PORK_BELLY, ["is_pork"], 0.62)], locale=locale),
        request(
            FISH_ALLERGY,
            probability_results=[probability(FISH_SAUCE, ["is_fish"], 0.8, role="hidden")],
            locale=locale,
        ),
        request(
            HALAL,
            probability_results=[probability(PORK, ["is_pork"], 0.62)],
            warnings=[warning("insufficient_sources")],
            locale=locale,
        ),
    ]


async def measure(llm: XaiLLM, locales: list[str]) -> tuple[UsageMeter, UsageMeter]:
    reason_meter, review_meter = UsageMeter(), UsageMeter()
    reason_llm = XaiLLM(MeteredClient(llm.client, reason_meter), llm.model)
    review_llm = XaiLLM(MeteredClient(llm.client, review_meter), llm.model)
    tool = DecisionTool(ReasonWriter(reason_llm))
    reviewer = HighRiskReviewAgent(review_llm)
    for locale in locales:
        for req in reason_requests(locale):
            await tool.run(req)
        await reviewer.run(review_request(locale=locale))
    return reason_meter, review_meter


def per_call(meter: UsageMeter) -> UsageMeter:
    n = max(meter.calls, 1)
    return UsageMeter(
        calls=1,
        answered=1,
        input_tokens=round(meter.estimated_input_tokens() / n),
        output_tokens=round(meter.output_tokens / n),
    )


def scan_estimate(reason: UsageMeter, review: UsageMeter, menus: int, share: float, reviews: int) -> UsageMeter:
    one_reason, one_review = per_call(reason), per_call(review)
    reason_calls = menus * share
    return UsageMeter(
        calls=round(reason_calls + reviews),
        answered=round(reason_calls + reviews),
        input_tokens=round(reason_calls * one_reason.input_tokens + reviews * one_review.input_tokens),
        output_tokens=round(reason_calls * one_reason.output_tokens + reviews * one_review.output_tokens),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--locales", nargs="+", default=["ko", "en"])
    parser.add_argument("--menus", type=int, default=10, help="한 스캔의 메뉴 수")
    parser.add_argument("--reason-share", type=float, default=0.5, help="이유 문장을 쓰는 메뉴 비율")
    parser.add_argument("--reviews", type=int, default=3, help="한 스캔의 고위험 재검증 수 (최대 3)")
    parser.add_argument("--price-in", type=float, help="입력 1M 토큰당 가격 (BAZE 대시보드 값)")
    parser.add_argument("--price-out", type=float, help="출력 1M 토큰당 가격")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    from evaluation.websearch import ROOT

    load_dotenv(ROOT / ".env")
    llm = XaiLLM.from_env_if_configured()
    if llm is None:
        raise SystemExit("OPENAI_API_KEY가 없어 ⑧ LLM을 부를 수 없어요")
    reason, review = asyncio.run(measure(llm, args.locales))
    scan = scan_estimate(reason, review, args.menus, args.reason_share, args.reviews)
    prices = (args.price_in, args.price_out)
    lines = [
        "# ⑧ LLM 비용",
        "",
        f"## 이유 문장 (언어 {len(args.locales)}개 × 메뉴 3개)",
        *cost_lines(reason, reason.calls, "호출", *prices),
        "",
        f"## 고위험 재검증 (언어 {len(args.locales)}개 × 메뉴 1개)",
        *cost_lines(review, review.calls, "호출", *prices),
        "",
        f"## 스캔 하나 추정 (메뉴 {args.menus}개, 이유 문장 {args.reason_share:.0%}, 재검증 {args.reviews}개)",
        *cost_lines(scan, 1, "스캔", *prices),
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
