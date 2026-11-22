"""⑧ reason sentence: written by the LLM, checked by code (docs/8-agent-xai.md §0-2, §4).

The LLM gets the verdict code already decided and only writes one or two sentences in
the user's language. Before the sentence goes out, code checks it (#174):

- every percentage must be one the verdict already shows (never 0% or 100%),
- no ingredient name the request did not carry (a name the request carries only in
  Korean may come back in the LLM's own translation, which it lists alongside, §4),
- no "it's safe" claim on a danger or caution verdict,
- no Hangul outside allowed names when the user's language is not Korean.

A copy of the verdict sentence inside the reason is cut out first.

Anything that fails, times out or errors becomes ``reason: None``; the verdict and
notes are templates and go out unchanged.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Iterable
from functools import lru_cache

from pydantic import BaseModel, Field

from ai_supervisor.contracts.common import Locale
from ai_supervisor.contracts.decision import DecisionRequest, DecisionResponse

from .llm import XaiLLM
from .messages import _sentences, reason_tail
from .names import all_rows, dictionary_name, display_name, standard_rows
from .policy import information_failed
from .profile import forbidden_tags
from .questions import TAG_NAMES, _has_final_consonant, tag_name

# 300 until 2026-11-22, when the reason went from one or two sentences to three or four
# (problem, then the next step on the result screen).
MAX_REASON_CHARS = 600
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
# "Nothing more to worry about" counts too (2026-11-17): a CAUTION reason read "there is
# no additional soybean item … that clearly needs to be flagged". So does "we did not see
# any" (2026-11-18): a pesco user's 고등어구이 read "没有看到你特别需要避开的牛、鸡…成分"
# while the verdict asked to check 갈비·된장·라면.
SAFE_CLAIMS: dict[str, tuple[str, ...]] = {
    "ko": ("안전해요", "보이지 않아", "보이지 않습니다", "찾지 못했어요", "찾지 못했습니다",  "안전합니다", "안전한 메뉴", "드셔도 괜찮", "먹어도 괜찮", "들어 있지 않", "들어있지 않", "걱정 없",
           "걱정할 필요", "걱정하지 않으셔도", "추가로 확인할 필요", "더 확인할 필요는 없"),
    "en": ("is safe", "did not see", "didn't see", "did not find", "didn't find", "no sign of",  "safe to eat", "safe for you", "no risk", "does not contain", "doesn't contain", "free of",
           "no additional", "nothing else to", "no need to worry", "nothing to worry", "needs to be flagged",
           "need to be flagged", "no further concern"),
    "ar": ("آمن تماما", "لم نجد",  "آمن للأكل", "لا يحتوي", "لا داعي للقلق"),
    "zh-CN": ("是安全的", "没有看到", "没有发现", "未发现",  "很安全", "放心食用", "不含有", "不用担心", "无需担心"),
    "zh-TW": ("是安全的", "沒有看到", "沒有發現", "未發現",  "很安全", "放心食用", "不含有", "不用擔心", "無需擔心"),
    "ja": ("安全です", "見当たりません", "見つかりませんでした",  "安心して", "含まれていません", "心配ありません", "心配いりません"),
    "es": ("es seguro", "no vimos", "no encontramos", "no se ve",  "sin riesgo", "no contiene", "no hay que preocuparse", "no te preocupes"),
}
# An estimate told as fact. Only phrases that state the ingredient is in, so "may
# contain", "들어갈 수 있어요" and hedges like "확실히 알 수 없어요" / "can't say for
# sure" pass; in the 2026-11-12 run 꽃게탕 read "This dish already includes blue crab".
CERTAIN_CLAIMS: dict[str, tuple[str, ...]] = {
    "ko": ("이미 들어", "반드시 들어", "틀림없이 들어", "확실히 들어"),
    "en": ("already includes", "already contains", "definitely contains", "definitely includes",
           "certainly contains", "certainly includes"),
    "ar": ("يحتوي بالفعل", "يحتوي بالتأكيد"),
    "zh-CN": ("肯定含有", "一定含有", "已经含有"),
    "zh-TW": ("肯定含有", "一定含有", "已經含有"),
    "ja": ("必ず入って", "すでに入って", "間違いなく入って", "確実に入って"),
    "es": ("ya contiene", "ya incluye", "contiene sin duda", "definitivamente contiene"),
}
# Words about our own data rather than the dish (2026-11-18 run): "The percentages show
# these ingredients can appear at nonzero rates", "the ingredients are marked unknown",
# "listed as hidden". The instructions already forbid them; this catches the slips.
META_CLAIMS: dict[str, tuple[str, ...]] = {
    "ko": ("퍼센트", "데이터", "표시되어", "입력값", "0이 아닌"),
    "en": ("percentage", "nonzero", "non-zero", "marked unknown", "marked as unknown", "listed as hidden",
           "listed amounts", "the data", "the input", "field", "listed as regular"),
    "ar": ("النسب المئوية", "البيانات"),
    "zh-CN": ("百分比", "数据", "标记为"),
    "zh-TW": ("百分比", "資料", "數據", "標記為"),
    "ja": ("パーセンテージ", "データ", "と表示"),
    "es": ("porcentajes", "los datos", "marcado como", "aparecen como"),
}
# A verdict sentence shorter than this (squashed) is not looked for in the reason.
MIN_REPEAT_CHARS = 10
_SQUASH_RE = re.compile(r"[\W_]+")


def _squash(text: str) -> str:
    return _SQUASH_RE.sub("", text.lower())


_WORD_LOCALES = {"en", "es"}
_LATIN_LOCALES = {"en", "es"}
_LATIN_WORD_RE = re.compile(r"[a-z]{3,}")
_ENGLISH_IN_SPANISH_RE = re.compile(
    r"(?<!\w)(this|dish|the|and|with|may|contain|contains|which|because|ingredients?)(?!\w)"
)
_HANGUL_RUN_RE = re.compile(r"[가-힣]+")
_PERCENT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
# Arabic-Indic and full-width digits and percent signs read as plain ones.
_EASTERN_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩٪０１２３４５６７８９％", "0123456789%0123456789%"
)
# A percentage written as a word counts as "%", so "80 percent" is checked too.
_PERCENT_WORD_RE = re.compile(
    r"\s*(?:percent|per cent|por ciento|퍼센트|프로|パーセント|بالمئة|في المئة)", re.IGNORECASE
)
# "百分之80" puts the word first; with Chinese numerals it cannot be checked at all.
_ZH_PERCENT_RE = re.compile(r"百分之\s*(\d+(?:[.,]\d+)?)")

INSTRUCTIONS = """You write the short "why" line under a food safety verdict for a tourist
at a Korean restaurant. Code already decided the verdict; you only explain it.
Write one short, friendly sentence for each group in explain_groups, in order, in
{language}, speaking to the tourist.
- Do not copy verdict_sentence or write a sentence that only says it again in other
  words. Your sentences must add the explanation it does not give.
- Each sentence explains one group: name all its ingredients together, say what
  kind of food they are, and so that they may contain avoided_in_them (naming each
  word as it is). If a tourist may not know them (most Korean
  ingredients such as fermented sauces, salted seafood or fish cake), say what they
  are made from and how. Say "may contain" only once in the sentence. Never put a
  vague phrase such as "what you avoid" in its place.
- If any ingredients have hard_to_remove true, add exactly one more sentence for all
  of them together, after the group sentences: name just those ingredients and why
  they may be hard to leave out, joining the two reasons in that one sentence when
  both apply: a main ingredient of this dish (role "identity"), or it goes inside a
  sauce or seasoning (role "hidden"). Never call a hidden one a main ingredient.
  Never say what part any other ingredient plays.
- Explain only the ingredients in explain_groups. Say nothing about how often they
  go into this dish, and not that they go against the tourist's restriction (the
  verdict already says so).
- Fixed sentences are added after yours: which ingredients most of our recipes
  use, and what the tourist can do next with the cards on the screen. So never
  write a percentage or how likely anything is, never say what to do next, and
  never mention a card.
- Say that ingredients may go into this dish, not that they are mixed into it.
- End every sentence with a period. Never use a semicolon.
- Use only ingredients from the input, with the given names. Never add others.
  A name given in Korean has no translation yet: translate it into {language}, and
  list each such name with your translation in name_translations.
- State only what the input says. Do not name other dishes, broths, or serving
  styles, and do not invent examples of how it is used. Saying how an ingredient
  itself is made is fine.
- For evidence_basis "estimated", speak of a possibility, never a certainty.
- If known_limits is not empty, also say in plain words why the result is less
  certain, using only those facts.
- Never say or imply the dish is safe. Do not use the word "safe" (or its translation)
  at all, not even in "we can't say it is safe". Never soften or change the verdict.
- Write every word in {language}. {dish}
- Do not mention the input, data, codes, or field names. No medical advice.{style}"""

# Added only for Korean: told to every language, the model put "요" after Chinese.
KOREAN_STYLE = "\n- Write in polite 해요체, like ~있어요 and ~해요. Do not add an extra 요."
# Korean wording for the reason only; the high-risk review shares KOREAN_STYLE. Card names and the "80% 이상" sentence are templates now (messages.py
# ``reason_tail``, 2026-11-22).
REASON_KOREAN_STYLE = (
    # Rules piece by piece kept slipping (2026-11-22~23): "가공육이라 돼지고기가 들어갈 수
    # 있어요" after "돼지고기가 들어간", "소주는 술이라서 술이 들어갈 수 있어요", "가공육예요",
    # "(부대찌개)의 주재료라서", "섞여 있을 수 있어서". Example sentences instead, Korean
    # only: an English example in the shared rules was copied into Korean and Chinese
    # (2026-11-17). An example name not in the input fails the name check, so a copied
    # example is dropped, never shown.
    "\n- Write like these examples, with this dish's own ingredients and words:"
    "\n  햄, 소시지, 베이컨은 가공육이어서 돼지고기가 들어갈 수 있어요."
    "\n  멸치액젓은 멸치를 소금에 절여 발효시켜 만든 액젓이어서 생선이 들어갈 수 있어요."
    "\n  어묵은 생선 살을 갈아 반죽해 튀기거나 쪄서 만든 가공식품이어서 생선이 들어갈 수 있어요."
    "\n  굴소스는 굴을 끓여 졸여 만든 소스여서 조개류가 들어갈 수 있어요."
    # One sentence for every hard-to-leave-out ingredient: "멸치액젓은 … 빼기 어려울 수
    # 있어요. 새우젓은 … 빼기 어려울 수 있어요." repeated itself (2026-11-23).
    "\n  멸치액젓과 새우젓은 양념이나 소스 안에 들어가서 빼기 어려울 수 있어요."
    "\n  햄과 소시지는 이 메뉴의 주재료라서, 새우젓은 양념이나 소스 안에 들어가서 빼기"
    " 어려울 수 있어요."
)
# Korean may name the dish ("이것도 김치찌개에 들어갈 수 있어요", 2026-11-22). Other
# languages say "this dish": the Korean menu name inside them read as a slip.
DISH_IN_KOREAN = "Call the dish by its menu name."
DISH_OTHERWISE = 'Call the dish "this dish" in {language}, not by its Korean name.'
# "~없어요요": the style rule once made the model add 요 twice at a sentence end.
_DOUBLE_YO_RE = re.compile(r"요요(?=[.!?。\s]|$)")
_SENTENCE_RE = re.compile(r"[^.!?。！？]+[.!?。！？]*")


# "여러 가지" (several kinds) is not 가지 (eggplant): two vegan reasons were dropped as
# naming an ingredient the dish did not carry (2026-11-22, 김치찌개·떡볶이).
_KO_KINDS_RE = re.compile(r"(?:여러|몇|한|두|세|네|다섯|모든|온갖|갖은|이런|그런|어떤)\s*가지")
_KO_SEMICOLON_RE = re.compile(r"\s*[;；]\s*")
_KO_COMMA_SPLICE_RE = re.compile(r"요,\s+")
_KO_YEYO_RE = re.compile(r"([가-힣])(?:이에요|예요)")
_PERCENT_SIGN_RE = re.compile(r"[%％٪]")
_DIGIT_RE = re.compile(r"[0-9٠-٩０-９]")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,!?。，！？،])")


def _drop_lone_percent(text: str) -> str:
    """Remove a percent sign with no number before it. "…맞지 않아요. %" came back as a
    reason's last word (2026-11-22) and passed, since only "number %" is checked."""

    def keep(match: re.Match[str]) -> str:
        before = text[: match.start()].rstrip()
        return match.group(0) if before and _DIGIT_RE.fullmatch(before[-1]) else ""

    cleaned = _PERCENT_SIGN_RE.sub(keep, text)
    if cleaned == text:
        return text
    return _SPACE_BEFORE_PUNCT_RE.sub(r"\1", " ".join(cleaned.split()))


def instructions(template: str, locale: str, *, korean_extra: str = "") -> str:
    language = LANGUAGE_NAMES[locale]
    return template.format(
        language=language,
        style=KOREAN_STYLE + korean_extra if locale == "ko" else "",
        dish=DISH_IN_KOREAN if locale == "ko" else DISH_OTHERWISE.format(language=language),
    )


class NameTranslation(BaseModel):
    korean: str
    translated: str


class ReasonOutput(BaseModel):
    # Before the reason, so the model settles each translation first.
    name_translations: list[NameTranslation] = Field(default_factory=list)
    reason: str = Field(description="One or two sentences in the requested language")


def translated_names(pairs: Iterable[NameTranslation], korean_names: Iterable[str]) -> set[str]:
    """The LLM's translations of names the request gave only in Korean. Without them
    "멸치액젓" written as "anchovy fish sauce" reads as 멸치, a name it did not carry."""

    wanted = set(korean_names)
    return {p.translated.strip() for p in pairs if p.korean.strip() in wanted and p.translated.strip()}


def shown_percent(posterior_mean: float) -> int:
    """The whole number the verdict template shows (messages.percent)."""

    if posterior_mean < 0.01:
        return 1
    if posterior_mean > 0.99:
        return 99
    return math.floor(posterior_mean * 100 + 0.5)


def _translation_rows() -> list[dict[str, str]]:
    return standard_rows()


@lru_cache(maxsize=8)
def known_names(locale: str) -> frozenset[str]:
    """Every standard ingredient name in ``locale`` (324 from the translation table)."""

    column = "name_ko" if locale == "ko" else locale
    return frozenset(
        row[column].strip().lower() for row in _translation_rows() if row.get(column, "").strip()
    )


def with_translations(korean_names: Iterable[str]) -> set[str]:
    """The names plus their translations in every language, from both tables (names.py)."""

    wanted = set(korean_names)
    names = set(wanted)
    for row in all_rows():
        if row["name_ko"] in wanted:
            names |= {value for value in row.values() if value}
    return names


def restriction_names(tags: Iterable[str]) -> set[str]:
    """The user's restriction names (생선, fish, 魚 ...) in every language. Saying "if
    you avoid fish" names the restriction, not a new ingredient."""

    names: set[str] = set()
    for tag in tags:
        names |= set(TAG_NAMES.get(tag, {}).values())
    return names


def localized_name(korean_name: str, locale: str) -> str:
    """``korean_name`` in ``locale`` from the translation tables, else Korean as is."""

    return dictionary_name(korean_name, locale) or korean_name


def untranslated(names: Iterable[str], locale: str) -> list[str]:
    """Names still in Korean in a non-Korean answer: the ones the LLM translates."""

    if locale == "ko":
        return []
    return list(dict.fromkeys(n for n in names if _HANGUL_RUN_RE.search(n)))


def _mentions(text: str, name: str, locale: str) -> bool:
    if locale == "ko":
        runs = _HANGUL_RUN_RE.findall(text)
        # A one-letter name (김, 무, 파) only counts as a whole word, as in ⑥'s dictionary.
        if len(name) == 1:
            return name in runs
        if not _HANGUL_RUN_RE.fullmatch(name):
            return name in text
        # Only at the start of a word: "라면을" names 라면, "분이라면" does not.
        return any(run.startswith(name) for run in runs)
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
    dropped_names: Iterable[str] = (),
    evidence_basis: str | None = None,
    korean_names: Iterable[str] | None = None,
) -> str | None:
    """``text`` when it passes the #174 checks, else ``None``.

    With ``evidence_basis`` other than ``confirmed``, a sentence that states an estimate
    as fact ("already includes blue crab", 2026-11-12) is dropped (``CERTAIN_CLAIMS``).

    ``dropped_names`` may never appear, even when ``allowed_names`` has them: the
    high-risk review passes the choices it judged rare. A nut allergy's restriction name
    (땅콩) is also the ingredient's name, so being allowed as a restriction let "땅콩은
    일부 변형에서 들어갈 수 있어요" through for 제육볶음 (2026-11-12).
    """

    if text is None:
        return None
    text = " ".join(text.split())
    if locale == "ko":
        text = _DOUBLE_YO_RE.sub("요", text)
        # Sentences end with a period: "빼기 어려울 수 있어요; 까나리는 …" (2026-11-22).
        text = _KO_SEMICOLON_RE.sub(". ", text).strip()
        # "…들어갈 수 있어요, 그리고 햄과 소시지는 …" (2026-11-22): one sentence each.
        text = _KO_COMMA_SPLICE_RE.sub("요. ", text)
        # "가공육예요" (2026-11-22): 이에요 after a final consonant.
        text = _KO_YEYO_RE.sub(lambda m: m.group(1) + ("이에요" if _has_final_consonant(m.group(1)) else "예요"), text)
    text = _drop_lone_percent(text)
    if not text or len(text) > MAX_REASON_CHARS:
        return None

    shown = set(percents)
    numbers = _ZH_PERCENT_RE.sub(r"%", text.translate(_EASTERN_DIGITS))
    numbers = _PERCENT_WORD_RE.sub("%", numbers)
    if "百分之" in numbers:
        return None
    for match in _PERCENT_RE.finditer(numbers):
        number = match.group(1).replace(",", ".")
        if "." in number or int(number) not in shown:
            return None

    if verdict:
        # Drop each sentence that repeats the verdict, so "이 음식에는 새우가 들어 있어요."
        # goes as a whole instead of leaving "이 음식에는" behind. Compared without spaces
        # and punctuation: "yogurt, and cheese" repeated "yogurt and cheese" (2026-11-12),
        # and sentence by sentence, so a repeat of one verdict sentence goes too.
        cores = {_squash(verdict)} | {
            _squash(s) for s in _SENTENCE_RE.findall(verdict) if len(_squash(s)) >= MIN_REPEAT_CHARS
        }
        cores.discard("")
        sentences = [s.strip() for s in _SENTENCE_RE.findall(text) if s.strip()]
        kept = [s for s in sentences if not any(core in _squash(s) for core in cores)]
        if len(kept) != len(sentences):
            text = " ".join(kept)
            if not text:
                return None
    lowered = text.lower()
    if evidence_basis != "confirmed" and evidence_basis is not None and any(
        claim in lowered for claim in CERTAIN_CLAIMS[locale]
    ):
        return None
    if risk_level != "safe" and any(claim in lowered for claim in SAFE_CLAIMS[locale]):
        return None
    if any(word in lowered for word in META_CLAIMS[locale]):
        return None

    # Blank out the menu name and the allowed names first (longest first), so "고기"
    # inside an allowed "돼지고기" or "김치" inside "김치찌개" is not read as a new name.
    allowed = {name.strip().lower() for name in allowed_names if name and name.strip()}
    blanks = {name.strip().lower() for name in menu_names if name and name.strip()} | allowed
    dropped = {name.strip().lower() for name in dropped_names if name and name.strip()}
    if dropped:
        probe = lowered
        for name in sorted(blanks - dropped, key=len, reverse=True):
            probe = probe.replace(name, " ")
        if any(_mentions(probe, name, locale) for name in dropped):
            return None
    for name in sorted(blanks, key=len, reverse=True):
        lowered = lowered.replace(name, " ")
    if locale == "ko":
        lowered = _KO_KINDS_RE.sub(" ", lowered)
    for name in known_names(locale) - allowed:
        if _mentions(lowered, name, locale):
            return None
    # Outside Korean, Hangul may only appear inside allowed names (the LLM may leave an
    # untranslated name in Korean); anything else means the wrong language.
    if locale != "ko" and _HANGUL_RUN_RE.search(lowered):
        return None
    # With ``korean_names``, only the names the LLM was given in Korean (no translation
    # yet) may stay in Korean. "can still contain 양념" passed in English (2026-11-17)
    # because 양념 is an allowed name, though the LLM had it as "seasoning sauce".
    if locale != "ko" and korean_names is not None:
        probe = text.lower()
        keep = {n.strip().lower() for n in [*korean_names, *menu_names] if n and n.strip()}
        for name in sorted(keep, key=len, reverse=True):
            probe = probe.replace(name, " ")
        if _HANGUL_RUN_RE.search(probe):
            return None
    # In a language not written in Latin letters, a Latin word left after the allowed
    # names are blanked is English slipping in ("굴소스 may contain 조개류", 2026-11-17).
    if locale not in _LATIN_LOCALES and _LATIN_WORD_RE.search(lowered):
        return None
    # Spanish uses the same letters, so look for common English words instead: the
    # instruction's "this dish" came back as "Esta dish" / "this dish lleve" (2026-11-18).
    if locale == "es" and _ENGLISH_IN_SPANISH_RE.search(lowered):
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


# Why the result is uncertain, as plain facts the LLM may retell (never the codes
# themselves). Only signals the user can understand; anything else is left out.
WARNING_FACTS: dict[str, str] = {
    "insufficient_sources": "Only a few web recipes were found for this dish.",
    "web_search_deadline_exceeded": "The web search ran out of time, so only some recipes were read.",
    "web_llm_extraction_failed": "Some web recipes could not be read fully, so ingredients may be missing.",
    "knowledge_slice_unavailable": "Some saved information about this dish could not be loaded.",
    "unmapped_constraint_tags": "For some ingredients it is unclear which food restrictions they affect.",
    "constraint_tags_lookup_failed": "For some ingredients it is unclear which food restrictions they affect.",
    "max_depth_reached": "Ingredients inside sauces or bases could not be traced all the way.",
    "store_context_missing": "No restaurant was chosen for this scan, so the owner's answers cannot be used.",
    "cycle_detected": "Ingredients inside sauces or bases could not be traced all the way.",
}
INFORMATION_FACTS: dict[str, str] = {
    "partial": "Part of the ingredient information could not be loaded.",
    "none": "The ingredient information could not be loaded.",
}


# An estimate with no recipe data at all behind it (⑤ prior fallback). ``global`` is
# not one: it is this menu's own crawled recipes, where most estimates come from.
THIN_PRIORS = {"uninformative"}


def reason_facts(request: DecisionRequest) -> list[str]:
    """Plain reasons the result is less certain: warnings, ``information_status``, and
    (2026-11-02) a restricted ingredient guessed from the menu name or estimated with no
    recipe data at all."""

    facts = [WARNING_FACTS[w.code] for w in request.warnings if w.code in WARNING_FACTS]
    if request.information_status == "none" or information_failed(request):
        facts.append(INFORMATION_FACTS[request.information_status])

    forbidden = forbidden_tags(request.user_profile)
    localized = {n.ingredient_id: n.localized_name for n in request.localized_names}

    def name(ref) -> str:
        return display_name(ref, localized, request.locale)

    for variant in request.proposed_variant_ingredients:
        if set(variant.constraint_tags) & forbidden:
            facts.append(f"{name(variant.ingredient)} is guessed from the menu name, not from recipes.")
    for result in request.probability_results:
        if result.prior_source in THIN_PRIORS and set(result.constraint_tags) & forbidden:
            facts.append(
                f"There is no recipe data for {name(result.ingredient)} in this dish, so its "
                f"chance is only a rough default."
            )
    return list(dict.fromkeys(facts))


def reason_payload(request: DecisionRequest, response: DecisionResponse) -> dict:
    localized = {n.ingredient_id: n.localized_name for n in request.localized_names}

    def name(ref) -> str:
        return display_name(ref, localized, request.locale)

    return {
        "menu": request.menu.normalized_menu_name,
        "verdict_code": {
            "risk_level": response.risk_level,
            "evidence_basis": response.evidence_basis,
        },
        "verdict_sentence": response.message.verdict,
        "explain_groups": explain_groups(request, response),
        "other_ingredients_in_menu": sorted(
            {name(r.ingredient) for r in request.probability_results}
            | {name(r.ingredient) for r in request.confirmed_results}
        ),
        "known_limits": reason_facts(request),
    }


# Groups of ingredients the reason explains, one sentence each (2026-11-22). "At most two
# ingredients" left a vegan's 까나리 unexplained, and the model ignored it anyway (five
# ingredients for 부대찌개); grouping by what is avoided keeps every cause and the
# sentence count small.
MAX_GROUPS = 3
MAX_PER_GROUP = 3


def explain_groups(request: DecisionRequest, response: DecisionResponse) -> list[dict]:
    """Risk ingredients whose name does not show why they hit the user, grouped by what
    in them the user avoids ("돼지고기": 햄·소시지, "생선": 멸치액젓·까나리), most likely
    first. An ingredient whose name shows it (새우 for shrimp) is left to the verdict.

    What is avoided is in the user's language, not tag codes (2026-11-15), and is never
    the user's restriction: called "matched_user_restrictions" until 2026-11-22, it made
    the model write "생선 제한" for a vegan's 어묵. A per-item rule with an English example
    was removed on 2026-11-17 (the model copied "may contain" into Korean); see ⑧ §8.
    How often and the user's restriction stay out: the fixed tail and the verdict say
    them ("잡채에는 이것도 자주 들어가고, 비건 기준에 맞지 않아요" only added words)."""

    groups: dict[tuple[str, ...], list] = {}
    for item in _by_likelihood(response.risk_ingredients):
        if needs_explaining(item):
            groups.setdefault(tuple(sorted(item.matched_tags)), []).append(item)
    return [
        {
            "avoided_in_them": [tag_name(t, request.locale) for t in tags],
            "ingredients": [
                {
                    "name": item.ingredient.localized_name,
                    "korean_name": item.ingredient.canonical_name,
                    "role": item.role,
                    "hard_to_remove": item.role in ("identity", "hidden"),
                }
                for item in items[:MAX_PER_GROUP]
            ],
        }
        for tags, items in list(groups.items())[:MAX_GROUPS]
    ]


def _by_likelihood(items):
    return sorted(items, key=lambda i: -(i.posterior_mean if i.posterior_mean is not None else 1.0))


def tail_text(request: DecisionRequest, response: DecisionResponse) -> str:
    """Fixed sentences after the LLM's reason (2026-11-22, messages.py ``reason_tail``):
    the ingredients at ``very_often``, then the remove-request card for those likely and
    removable, then the question card for the rest. The LLM wrote "80% 이상" for 어묵 at
    52% and picked the wrong card, so these are templates."""

    items = response.risk_ingredients
    very_often = [i for i in _by_likelihood(items) if how_often(i, request) == "very_often"]
    remove = any(next_step(i) == "remove_request_card" for i in items)
    ask = any(next_step(i) == "question_card" for i in items)
    names = lambda rows: list(dict.fromkeys(i.ingredient.localized_name for i in rows))  # noqa: E731
    return reason_tail(
        names(very_often), remove, ask, f"{round(VERY_HIGH_CHANCE * 100)}%",
        request.menu.normalized_menu_name, request.locale
    )


# Words the reason is told to use for a hidden ingredient ("inside a sauce or
# seasoning"), which are also ingredient names in the tables. Without this, "굴은 양념 안에
# 들어 있을 수 있어요" was dropped as naming a new ingredient (2026-11-22, 잡채·떡볶이).
# 젓갈 too: "새우젓도 해산물 젓갈이라" was dropped for 부대찌개 the same way, and the reason
# is now told to say what an unfamiliar ingredient is (2026-11-22).
GENERIC_WORDS = ("양념", "소스", "조미료", "젓갈")


@lru_cache(maxsize=1)
def generic_words() -> frozenset[str]:
    return frozenset(with_translations(GENERIC_WORDS))


# At or above this posterior mean an estimated ingredient reads as "very likely, be
# careful"; below it, "low, but it cannot be ruled out" (2026-11-22, wording only: the
# verdict does not change).
HIGH_CHANCE = 0.5


def chance(item) -> str | None:
    """"confirmed" for an owner-confirmed ingredient, else "high" / "low"; None with no
    probability."""

    if item.status in ("present", "absent"):
        return "confirmed"
    if item.posterior_mean is None:
        return None
    return "high" if item.posterior_mean >= HIGH_CHANCE else "low"


# How often recipes for the dish use an ingredient, in words (2026-11-22). The percent
# is said only when very high or very low: "한스푼이 수집한 레시피의 21%에 들어가요" after
# every ingredient was noise, and the screen shows each percent anyway. "자주" only from
# HIGH_CHANCE up; below it, "이것도 김치찌개에 들어갈 수 있어요".
VERY_HIGH_CHANCE = 0.8
VERY_LOW_CHANCE = 0.1


def how_often(item, request: DecisionRequest) -> str | None:
    """"very_often" / "often" / "sometimes" / "rarely", or None for an owner-confirmed
    ingredient, no probability, or a rough default with no recipe data
    (``THIN_PRIORS``), which is not a share of recipes."""

    if item.posterior_mean is None or item.status in ("present", "absent"):
        return None
    if any(
        r.prior_source in THIN_PRIORS and r.ingredient.canonical_name == item.ingredient.canonical_name
        for r in request.probability_results
    ):
        return None
    p = item.posterior_mean
    if p >= VERY_HIGH_CHANCE:
        return "very_often"
    if p >= HIGH_CHANCE:
        return "often"
    return "rarely" if p <= VERY_LOW_CHANCE else "sometimes"


def next_step(item) -> str:
    """What the result screen lets the tourist do next (2026-11-22): ask to leave it out
    with the remove-request card when it is likely in the dish and can be left out,
    otherwise ask the staff directly with the question card. A main or hidden ingredient
    may be hard to leave out (§4-1), so it goes to the question card."""

    likely = chance(item) in ("high", "confirmed")
    if likely and item.role not in ("identity", "hidden"):
        return "remove_request_card"
    return "question_card"


def needs_explaining(item) -> bool:
    """The name alone does not say why the ingredient hits the user: 액젓 for fish,
    삼겹살 for pork. "돼지고기" for a pork restriction says it already."""

    name = item.ingredient.canonical_name
    for tag in item.matched_tags:
        words = (TAG_NAMES.get(tag, {}).get("ko"), *NAME_ALIASES.get(tag, ()))
        if not any(word and word in name for word in words):
            return True
    return False


# Other Korean words that already show the restriction in an ingredient name: 계란 for
# 달걀 (egg) called the LLM for "계란" on every vegan scan (2026-11-17).
NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "is_egg": ("계란",),
    "is_beef": ("쇠고기",),
    "is_pork": ("돼지",),
    "is_chicken": ("닭",),
    # "알코올은 술이 들어갈 수 있어요" for every halal scan (2026-11-22): ④ expands 맛술
    # into a hidden 알코올, whose name already says it. 소주 and the like stay explained.
    "is_alcohol": ("알코올",),
}


def needs_reason(request: DecisionRequest, response: DecisionResponse) -> bool:
    """Write a reason only when it says something the verdict sentence and notes do not
    (2026-11-02): an ingredient whose name does not show the restriction it hits, or a
    known limit (few web recipes, missing information). A menu with no information at
    all, or a safe one, is already said by the verdict sentence. Neither is a user with
    no restriction: nothing can go against them, so a reason only spends a call
    (2026-11-18, 공기밥 for a user with no restriction got one)."""

    if response.risk_level == "safe" or request.no_information:
        return False
    if not forbidden_tags(request.user_profile):
        return False
    return bool(reason_facts(request)) or any(
        needs_explaining(item) for item in response.risk_ingredients
    )


class ReasonWriter:
    """LLM side of ``message.reason``. Never raises; a failure is ``None``."""

    def __init__(self, llm: XaiLLM, *, timeout_seconds: float = 5.0) -> None:
        self.llm = llm
        self.timeout_seconds = timeout_seconds

    async def write(self, request: DecisionRequest, response: DecisionResponse) -> str | None:
        """The LLM's checked sentences, then the fixed tail. When the LLM fails or is
        dropped, ``reason`` stays None as before (§4 "LLM 실패"): the tail goes only
        with a reason it follows."""

        if not needs_reason(request, response):
            return None
        written = await self._write_checked(request, response)
        if written is None:
            return None
        return _sentences([p for p in (written, tail_text(request, response)) if p], request.locale)

    async def _write_checked(self, request: DecisionRequest, response: DecisionResponse) -> str | None:
        payload = reason_payload(request, response)
        try:
            output = await asyncio.wait_for(
                self.llm.parse(
                    instructions(INSTRUCTIONS, request.locale, korean_extra=REASON_KOREAN_STYLE),
                    payload,
                    ReasonOutput,
                    MAX_OUTPUT_TOKENS,
                ),
                timeout=self.timeout_seconds,
            )
        except Exception:
            return None
        korean_only = untranslated(
            [*(i.ingredient.localized_name for i in response.risk_ingredients), *payload["other_ingredients_in_menu"]],
            request.locale,
        )
        return check_reason(
            output.reason,
            locale=request.locale,
            risk_level=response.risk_level,
            percents=[
                shown_percent(item.posterior_mean)
                for item in response.risk_ingredients
                if item.posterior_mean is not None
            ],
            allowed_names=request_names(request)
            | restriction_names(forbidden_tags(request.user_profile))
            | translated_names(output.name_translations, korean_only)
            | generic_words(),
            menu_names=[request.menu.normalized_menu_name],
            verdict=response.message.verdict,
            evidence_basis=response.evidence_basis,
            korean_names=korean_only,
        )
