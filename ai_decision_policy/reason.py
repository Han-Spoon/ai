"""⑧ reason sentence: written by the LLM, checked by code (docs/8-agent-xai.md §0-2, §4).

The LLM gets the verdict code already decided and only writes one or two sentences in
the user's language. Before the sentence goes out, code checks it (#174):

- every percentage must be one the verdict already shows (never 0% or 100%),
- no ingredient name the request did not carry,
- no "it's safe" claim on a danger or caution verdict,
- no Hangul outside allowed names when the user's language is not Korean.

A copy of the verdict sentence inside the reason is cut out first.

Anything that fails, times out or errors becomes ``reason: None``; the verdict and
notes are templates and go out unchanged.
"""

from __future__ import annotations

import asyncio
import csv
import math
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from ai_supervisor.contracts.common import Locale
from ai_supervisor.contracts.decision import DecisionRequest, DecisionResponse

from .llm import XaiLLM

TRANSLATIONS_PATH = (
    Path(__file__).resolve().parent.parent / "crawling" / "ingredient_translations.csv"
)
MAX_REASON_CHARS = 300
MAX_OUTPUT_TOKENS = 400
LANGUAGE_NAMES: dict[str, str] = {
    "ko": "Korean",
    "en": "English",
    "ar": "Arabic",
    "zh-CN": "Simplified Chinese",
    "zh-TW": "Traditional Chinese",
    "ja": "Japanese",
    "es": "Spanish",
}
# Phrases that call the dish safe or free of something. Never allowed unless the
# verdict itself is safe. Matched on lowercased text.
SAFE_CLAIMS: dict[str, tuple[str, ...]] = {
    "ko": ("안전해요", "안전합니다", "안전한 메뉴", "드셔도 괜찮", "먹어도 괜찮", "들어 있지 않", "들어있지 않", "걱정 없"),
    "en": ("is safe", "safe to eat", "safe for you", "no risk", "does not contain", "doesn't contain", "free of"),
    "ar": ("آمن تماما", "آمن للأكل", "لا يحتوي"),
    "zh-CN": ("是安全的", "很安全", "放心食用", "不含有"),
    "zh-TW": ("是安全的", "很安全", "放心食用", "不含有"),
    "ja": ("安全です", "安心して", "含まれていません"),
    "es": ("es seguro", "sin riesgo", "no contiene"),
}
_WORD_LOCALES = {"en", "es"}
_HANGUL_RUN_RE = re.compile(r"[가-힣]+")
_PERCENT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_EASTERN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٪", "0123456789%")

INSTRUCTIONS = """You write the short "why" line under a food safety verdict for a tourist
at a Korean restaurant. Code already decided the verdict; you only explain it.
Write one or two short, friendly sentences in {language}, speaking to the tourist.
- Say why the listed ingredients matter: role "identity" is a main ingredient,
  "component" is a regular ingredient, "hidden" is inside a sauce or base.
  If role is null, do not say what part it plays.
- Use only ingredients from the input, with the given names. Never add others.
- If you give a percentage, copy one from the input exactly. Never write 0% or 100%.
- For evidence_basis "estimated", speak of a possibility, never a certainty.
- Never say or imply the dish is safe. Never soften or change the verdict.
- Write every word in {language}. Call the dish "this dish" in {language}, not by
  its Korean name.
- Do not mention the input, data, codes, or field names. Do not repeat
  verdict_sentence. No medical advice. In Korean, end sentences with -요."""


class ReasonOutput(BaseModel):
    reason: str = Field(description="One or two sentences in the requested language")


def shown_percent(posterior_mean: float) -> int:
    """The whole number the verdict template shows (messages.percent)."""

    if posterior_mean < 0.01:
        return 1
    if posterior_mean > 0.99:
        return 99
    return math.floor(posterior_mean * 100 + 0.5)


@lru_cache(maxsize=1)
def _translation_rows() -> list[dict[str, str]]:
    with TRANSLATIONS_PATH.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@lru_cache(maxsize=8)
def known_names(locale: str) -> frozenset[str]:
    """Every standard ingredient name in ``locale`` (324 from the translation table)."""

    column = "name_ko" if locale == "ko" else locale
    return frozenset(
        row[column].strip().lower() for row in _translation_rows() if row.get(column, "").strip()
    )


def with_translations(korean_names: Iterable[str]) -> set[str]:
    """The names plus their translations in every language, from the same table."""

    wanted = set(korean_names)
    names = set(wanted)
    for row in _translation_rows():
        if row["name_ko"] in wanted:
            names |= {value for value in row.values() if value}
    return names


def _mentions(text: str, name: str, locale: str) -> bool:
    if locale == "ko":
        runs = _HANGUL_RUN_RE.findall(text)
        # A one-letter name (김, 무, 파) only counts as a whole word, as in ⑥'s dictionary.
        if len(name) == 1:
            return name in runs
        return any(name in run for run in runs) or name in text
    if locale in _WORD_LOCALES:
        return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text) is not None
    return name in text


def check_reason(
    text: str | None,
    *,
    locale: Locale,
    risk_level: str,
    percents: Iterable[int],
    allowed_names: Iterable[str],
    menu_names: Iterable[str] = (),
    verdict: str | None = None,
) -> str | None:
    """``text`` when it passes the #174 checks, else ``None``."""

    if text is None:
        return None
    text = " ".join(text.split())
    if not text or len(text) > MAX_REASON_CHARS:
        return None

    shown = set(percents)
    for match in _PERCENT_RE.finditer(text.translate(_EASTERN_DIGITS)):
        number = match.group(1).replace(",", ".")
        if "." in number or int(number) not in shown:
            return None

    lowered = text.lower()
    if verdict and verdict.lower() in lowered:
        lowered = lowered.replace(verdict.lower(), " ")
        text = " ".join(text.replace(verdict, " ").split())
        if not text:
            return None
    if risk_level != "safe" and any(claim in lowered for claim in SAFE_CLAIMS[locale]):
        return None

    # Blank out the menu name and the allowed names first (longest first), so "고기"
    # inside an allowed "돼지고기" or "김치" inside "김치찌개" is not read as a new name.
    allowed = {name.strip().lower() for name in allowed_names if name and name.strip()}
    blanks = {name.strip().lower() for name in menu_names if name and name.strip()} | allowed
    for name in sorted(blanks, key=len, reverse=True):
        lowered = lowered.replace(name, " ")
    for name in known_names(locale) - allowed:
        if _mentions(lowered, name, locale):
            return None
    # Outside Korean, Hangul may only appear inside allowed names (web ingredients
    # without a translation stay Korean, §4); anything else means the wrong language.
    if locale != "ko" and _HANGUL_RUN_RE.search(lowered):
        return None
    return text


def request_names(request: DecisionRequest) -> set[str]:
    """Korean and localized names of every ingredient this request carries."""

    localized = {n.ingredient_id: n.localized_name for n in request.localized_names}
    refs = [
        *(r.ingredient for r in request.confirmed_results),
        *(r.ingredient for r in request.probability_results),
        *(r.ingredient for r in request.proposed_variant_ingredients),
    ]
    names = with_translations(ref.canonical_name for ref in refs)
    names |= {localized[ref.ingredient_id] for ref in refs if ref.ingredient_id in localized}
    return names


def reason_payload(request: DecisionRequest, response: DecisionResponse) -> dict:
    localized = {n.ingredient_id: n.localized_name for n in request.localized_names}

    def name(ref) -> str:
        return localized.get(ref.ingredient_id, ref.canonical_name)

    return {
        "menu": request.menu.normalized_menu_name,
        "verdict_code": {
            "risk_level": response.risk_level,
            "evidence_basis": response.evidence_basis,
        },
        "verdict_sentence": response.message.verdict,
        "risk_ingredients": [
            {
                "name": item.ingredient.localized_name,
                "korean_name": item.ingredient.canonical_name,
                "role": item.role,
                "matched_user_restrictions": item.matched_tags,
                "status": item.status,
                "percent": (
                    f"{shown_percent(item.posterior_mean)}%"
                    if item.posterior_mean is not None
                    else None
                ),
            }
            for item in response.risk_ingredients
        ],
        "other_ingredients_in_menu": sorted(
            {name(r.ingredient) for r in request.probability_results}
            | {name(r.ingredient) for r in request.confirmed_results}
        ),
    }


def needs_reason(response: DecisionResponse) -> bool:
    """Explain only when some ingredient drives the verdict; otherwise the verdict
    sentence already says everything (no information, or nothing to avoid)."""

    return response.risk_level != "safe" and bool(response.risk_ingredients)


class ReasonWriter:
    """LLM side of ``message.reason``. Never raises; a failure is ``None``."""

    def __init__(self, llm: XaiLLM, *, timeout_seconds: float = 5.0) -> None:
        self.llm = llm
        self.timeout_seconds = timeout_seconds

    async def write(self, request: DecisionRequest, response: DecisionResponse) -> str | None:
        if not needs_reason(response):
            return None
        try:
            output = await asyncio.wait_for(
                self.llm.parse(
                    INSTRUCTIONS.format(language=LANGUAGE_NAMES[request.locale]),
                    reason_payload(request, response),
                    ReasonOutput,
                    MAX_OUTPUT_TOKENS,
                ),
                timeout=self.timeout_seconds,
            )
        except Exception:
            return None
        return check_reason(
            output.reason,
            locale=request.locale,
            risk_level=response.risk_level,
            percents=[
                shown_percent(item.posterior_mean)
                for item in response.risk_ingredients
                if item.posterior_mean is not None
            ],
            allowed_names=request_names(request),
            menu_names=[request.menu.normalized_menu_name],
            verdict=response.message.verdict,
        )
