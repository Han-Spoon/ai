"""⑧ reason sentence: written by the LLM, checked by code (docs/8-agent-xai.md §0-2, §4).

Code first decides what the reason is made of (``reason_route``, 2026-11-23): nothing,
fixed limit sentences only (no LLM), or the LLM's explanation of the ingredient groups,
with the limit sentences added by code. The prompt is put together with only the rules
the menu needs (``reason_instructions``). The LLM gets the verdict code already decided
and only writes the group sentences in the user's language. Before they go out, code
checks them (#174):

- every percentage must be one the verdict already shows (never 0% or 100%),
- no ingredient name other than those that hit the user (2026-11-23; until then any
  name the request carried; a name given only in Korean may come back in the LLM's own
  translation, which it lists alongside, §4),
- every group named at least once (2026-11-23),
- no "it's safe" claim on a danger or caution verdict,
- no Hangul outside allowed names when the user's language is not Korean.

A copy of the verdict sentence inside the reason is cut out first.

Anything that fails, times out or errors becomes ``reason: None``; the verdict and
notes are templates and go out unchanged.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field

from ai_supervisor.contracts.common import Locale
from ai_supervisor.contracts.decision import DecisionRequest, DecisionResponse

from .llm import XaiLLM
from .messages import EXPLAIN_SENTENCE, card_step, limit_sentences, shown_percent, side_item_text
from .names import all_rows, dictionary_name, display_name, standard_rows, taxonomy_categories
from .policy import information_failed, is_low_chance
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

# The reason prompt (2026-11-23). The LLM writes one short phrase per ingredient group;
# code puts the group's names in front ("• 멸치액젓: 멸치를 발효시킨 액젓"), adds the
# card step and at most one limit line. Until then it wrote a paragraph of sentences
# (cause, hard to leave out, "80% 이상", limits, card), which the professor found hard
# to read (2026-11-23): the same names came three or four times, and two percentages.
# The hard-to-remove sentence went (the notes say it), and so did "80% 이상" (the
# verdict gives a percent).
# A menu description comes first since 2026-11-24, for every menu: a tourist who has
# never seen the dish learns what it is before why it was flagged. Its ingredients are
# the menu's own main ones (``menu_ingredients``), so it does not guess any.
REASON_PROMPT = """You write short text for a tourist at a Korean restaurant, shown under a
food safety verdict. Code already decided the verdict; you never judge it.
{description_task}{groups_task}
Rules for everything you write:
- Never write a percentage, how likely or how often anything is, what to do next, a
  card, whether it can be left out, or why the result is uncertain. Code adds what is
  needed.
- Never say or imply the dish is safe. Do not use the word "safe" (or its translation).
- Write every word in {language}.
- Do not mention the input, data, codes, or field names. No medical advice.{translate}{examples}"""
DESCRIPTION_TASK = """
menu_description: one short noun phrase in {language} saying what kind of dish the menu
is and how it is made, as a menu card would, so a tourist who has never seen it can
picture it. Code makes it a sentence with the dish as its subject ("dish is phrase").
- Do not start with the menu name and do not repeat it.
- A noun phrase, not a sentence: no verb ending and no period. At most {description_chars}
  characters.
- main_ingredients lists what this dish commonly has, the surest first. Pick at most
  {max_ingredients} that best show what the dish is; leave out seasonings, oils,
  garnishes and aromatic vegetables unless the dish is known for them. Since a
  restaurant's version may differ, end the ingredients you name with a word like
  "and so on" in {language}.
- Never name an ingredient, sauce or dish that main_ingredients lacks, even one this
  dish usually has: it is checked, and the whole description is dropped. Naming none
  is fine.
{article}"""
NO_DESCRIPTION_TASK = """
menu_description: leave it empty.
"""
GROUPS_TASK = """
phrases: for each group in explain_groups, in order, one short phrase in {language}
saying what kind of food its ingredients are and what they are made from, so the
tourist sees why they may contain avoided_in_them. Code makes each phrase a sentence
with the group's ingredient names as its subject ("names are phrase").
- Do not start with the ingredient names and do not repeat them.
- A noun phrase, not a sentence: no verb ending and no period. At most {max_chars}
  characters.
- Make clear what in them the tourist avoids: say avoided_in_them as given, or the
  ingredient they are made from.
- Name no other ingredient or dish. Saying what an ingredient itself is made from is
  fine.
{article}"""
NO_GROUPS_TASK = """
phrases: leave the list empty.
"""
# English and Spanish only (2026-11-24): code puts "is" / "es" in front of the phrase, and
# "This dish is spicy mixed stew with ham" came back with no article.
ARTICLE_RULE: dict[str, str] = {
    "en": '- Start the phrase with "a" or "an" ("a stew of ...").\n',
    "es": '- Start the phrase with "un" or "una" ("un guiso de ...").\n',
}
# Only when a name has no translation yet (non-Korean answers).
TRANSLATE_RULE = """
- Some names are given in Korean with no translation yet: translate each into
  {language} and list it with your translation in name_translations."""
# Korean only: an English example in the shared rules was copied into Korean and
# Chinese (2026-11-17). A copied example name not in the input fails the name check.
# No meat or fish named as what a dish is made with (2026-11-24): "김치찌개: 김치와
# 돼지고기를 넣고 끓인 찌개" taught that 김치찌개 is pork, though many are made with tuna.
# "… 등" since a restaurant's version may differ (부대찌개, 잡채; ⑧ 담당 the same day).
KOREAN_DESCRIPTION_EXAMPLES = (
    "\n- Korean menu_description looks like these (menu: phrase); write only the phrase,"
    " with this dish's own ingredients:"
    "\n  부대찌개: 햄과 소시지, 라면 등을 넣고 얼큰하게 끓인 찌개"
    "\n  잡채: 당면과 여러 채소 등을 볶아 무친 음식"
    "\n  떡볶이: 떡을 매콤달콤한 양념에 볶은 음식"
)
KOREAN_PHRASE_EXAMPLES = (
    "\n- Korean phrases look like these (names: phrase); write only the phrase, with this"
    " dish's own ingredients:"
    "\n  만두: 돼지고기와 채소로 소를 만들어 빚은 음식"
    "\n  멸치액젓: 멸치를 소금에 절여 발효시킨 액젓"
    "\n  어묵: 생선 살을 갈아 튀기거나 쪄서 만든 가공식품"
    "\n  굴소스: 굴을 졸여 만든 소스"
)
# Longest phrase kept, by language (a longer one is dropped, not cut).
MAX_PHRASE_CHARS: dict[str, int] = {
    "ko": 30, "ja": 30, "zh-CN": 25, "zh-TW": 25, "en": 70, "es": 80, "ar": 70,
}
MAX_DESCRIPTION_CHARS: dict[str, int] = {
    "ko": 40, "ja": 40, "zh-CN": 30, "zh-TW": 30, "en": 90, "es": 100, "ar": 90,
}
# The description's subject outside Korean, which names the menu: the Korean menu name
# inside another language read as a slip (``DISH_OTHERWISE``).
DISH_SUBJECT: dict[str, str] = {
    "en": "this dish", "es": "este plato", "ar": "هذا الطبق",
    "zh-CN": "这道菜", "zh-TW": "這道菜", "ja": "この料理",
}
# "햄·소시지는 …": how the group's names are joined in front of the phrase.
LABEL_JOIN: dict[str, str] = {
    "ko": "·", "ja": "・", "zh-CN": "、", "zh-TW": "、", "en": ", ", "es": ", ", "ar": "، ",
}
_PHRASE_END_RE = re.compile(r"[\s.。!！]+$")

# Added only for Korean: told to every language, the model put "요" after Chinese.
# The high-risk review uses it; the reason phrases have no sentence ending.
KOREAN_STYLE = "\n- Write in polite 해요체, like ~있어요 and ~해요. Do not add an extra 요."
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
    # Before the phrases, so the model settles each translation first.
    name_translations: list[NameTranslation] = Field(default_factory=list)
    # default_factory, not default="": a "default" key in the strict schema may be refused.
    menu_description: str = Field(default_factory=str, description="What kind of dish the menu is")
    phrases: list[str] = Field(
        default_factory=list,
        description="One short phrase per group in explain_groups, in the same order",
    )


def translated_names(pairs: Iterable[NameTranslation], korean_names: Iterable[str]) -> set[str]:
    """The LLM's translations of names the request gave only in Korean. Without them
    "멸치액젓" written as "anchovy fish sauce" reads as 멸치, a name it did not carry."""

    wanted = set(korean_names)
    return {p.translated.strip() for p in pairs if p.korean.strip() in wanted and p.translated.strip()}


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


def risk_names(response: DecisionResponse) -> set[str]:
    """Korean and localized names (with translations) of the ingredients that hit the
    user. The reason may name only these (2026-11-23): checking against every ingredient
    the request carried let a 새우튀김 reason name 간장 and 마요네즈 as causes of pork.

    A standard name inside one of them counts too (멸치 in 멸치액젓, 굴 in 굴소스): saying
    what an ingredient is made from is what the reason is for, and "멸치액젓과 까나리는
    멸치와 까나리로 만든…" was dropped for 멸치 in the first real run (2026-11-23)."""

    korean = [i.ingredient.canonical_name for i in response.risk_ingredients]
    parts = {n for n in known_names("ko") if any(n in name for name in korean)}
    names = with_translations([*korean, *parts])
    names |= {i.ingredient.localized_name for i in response.risk_ingredients}
    return names


# Warning codes the user can understand, mapped to messages.py ``LIMIT_SENTENCES``.
# Anything else is left out.
WARNING_LIMITS: dict[str, str] = {
    "insufficient_sources": "few_web_recipes",
    "web_search_deadline_exceeded": "web_timeout",
    "web_llm_extraction_failed": "web_read_failed",
    "knowledge_slice_unavailable": "saved_info_unavailable",
    "unmapped_constraint_tags": "unclear_restrictions",
    "constraint_tags_lookup_failed": "unclear_restrictions",
    "max_depth_reached": "nested_untraced",
    "store_context_missing": "no_store",
    "cycle_detected": "nested_untraced",
}
INFORMATION_LIMITS: dict[str, str] = {
    "partial": "partial_information",
    "none": "no_information",
}


# An estimate with no recipe data at all behind it (⑤ prior fallback). ``global`` is
# not one: it is this menu's own crawled recipes, where most estimates come from.
THIN_PRIORS = {"uninformative"}


def limit_text(request: DecisionRequest) -> list[str]:
    """Why the result is less certain, as fixed sentences in the user's language
    (2026-11-23; until then English facts for the LLM to retell): warnings,
    ``information_status``, and a restricted ingredient guessed from the menu name or
    estimated with no recipe data at all (2026-11-02)."""

    codes = [WARNING_LIMITS[w.code] for w in request.warnings if w.code in WARNING_LIMITS]
    if request.information_status == "none" or information_failed(request):
        codes.append(INFORMATION_LIMITS[request.information_status])

    forbidden = forbidden_tags(request.user_profile)
    localized = {n.ingredient_id: n.localized_name for n in request.localized_names}

    def name(ref) -> str:
        return display_name(ref, localized, request.locale)

    guessed = [
        name(v.ingredient)
        for v in request.proposed_variant_ingredients
        if set(v.constraint_tags) & forbidden
    ]
    # Not an estimate under 5%: the screen does not show it (2026-11-25).
    no_data = [
        name(r.ingredient)
        for r in request.probability_results
        if r.prior_source in THIN_PRIORS
        and set(r.constraint_tags) & forbidden
        and not is_low_chance(r)
    ]
    return limit_sentences(
        codes, list(dict.fromkeys(guessed)), list(dict.fromkeys(no_data)), request.locale
    )


def reason_payload(
    request: DecisionRequest, response: DecisionResponse, *, describe: bool = True, explain: bool = True
) -> dict:
    """Only what the LLM writes about (2026-11-23): the groups, and since 2026-11-24 the
    menu's main ingredients for its description. The menu's other ingredients and the
    known limits went in too until 2026-11-23, and the model tied them together as
    causes."""

    return {
        "menu": request.menu.normalized_menu_name,
        "main_ingredients": menu_ingredients(request) if describe else [],
        "explain_groups": explain_groups(request, response) if explain else [],
    }


# The menu description names at most this many ingredients (2026-11-24): a long list
# reads like the ingredient table, which the result screen already has. The LLM picks
# them from ``menu_ingredients`` (prompt rule; the length cap keeps it short).
MAX_MENU_INGREDIENTS = 3
# Sure: in this share of recipes or more, or a main ingredient at ``HIGH_CHANCE``
# (2026-11-24, "무조건 들어가는 재료").
SURE_CHANCE = 0.8
# Common: in this share of recipes or more (2026-11-24). Sure ones only left 부대찌개
# without its noodles (라면 44%), and the LLM, writing "ham, sausage, kimchi, and
# noodles", was dropped for naming one the list lacked. The description says "등" /
# "such as", since a restaurant's version may differ.
COMMON_CHANCE = 0.3
# Meat and seafood are offered only when sure (2026-11-24): the meat or fish in many
# dishes differs by restaurant, and 김치찌개 is made with pork (65% of crawled recipes)
# or tuna. "김치와 돼지고기를 넣고 끓인 찌개" taught that it is pork.
SWAPPABLE_TAGS = frozenset({
    "is_pork", "is_beef", "is_chicken", "is_duck", "is_fish", "is_shrimp", "is_crab",
    "is_squid", "is_shellfish", "is_mackerel",
})
# How many ingredients the LLM chooses from (2026-11-24). In the first real run code
# took the top three by likelihood and 김치찌개 read "배추김치와 대파, 마늘을 넣어 끓인
# 찌개" (대파 81%, 마늘 73%). A list of names to skip (aromatics, pastes) was tried the
# same day and dropped: whether a seasoning matters depends on the dish, so the LLM gets
# the list and picks the ones that show what the dish is.
MAX_MENU_CANDIDATES = 8


def menu_ingredients(request: DecisionRequest) -> list[dict]:
    """The ingredients the menu description may use (2026-11-24), at most
    ``MAX_MENU_CANDIDATES``: first the sure ones (owner-confirmed present, a main
    ingredient (``role: identity``) at ``HIGH_CHANCE`` or more, others at ``SURE_CHANCE``
    or more; main ingredients first, then most likely), then common ones at
    ``COMMON_CHANCE`` or more that are not meat or seafood (``SWAPPABLE_TAGS``), most
    likely first. Not those hidden inside another ingredient (a sauce's 멸치) nor any the
    owner said is not in it."""

    absent = {c.ingredient.canonical_name for c in request.confirmed_results if c.status == "absent"}
    sure = [(c.role, 1.0, c.ingredient) for c in request.confirmed_results if c.status == "present"]
    common = []
    for r in request.probability_results:
        if r.ingredient.canonical_name in absent:
            continue
        if r.posterior_mean >= (HIGH_CHANCE if r.role == "identity" else SURE_CHANCE):
            sure.append((r.role, r.posterior_mean, r.ingredient))
        elif r.posterior_mean >= COMMON_CHANCE and not set(r.constraint_tags) & SWAPPABLE_TAGS:
            common.append((r.role, r.posterior_mean, r.ingredient))
    sure.sort(key=lambda f: (f[0] != "identity", -f[1]))
    common.sort(key=lambda f: -f[1])
    localized = {n.ingredient_id: n.localized_name for n in request.localized_names}
    picked: dict[str, dict] = {}
    for role, _, ref in [*sure, *common]:
        name = ref.canonical_name
        if role == "hidden" or name in picked:
            continue
        picked[name] = {
            "name": display_name(ref, localized, request.locale),
            "korean_name": name,
        }
        if len(picked) == MAX_MENU_CANDIDATES:
            break
    return list(picked.values())


# Groups of ingredients the reason explains, one sentence each (2026-11-22). "At most two
# ingredients" left a vegan's 까나리 unexplained, and the model ignored it anyway (five
# ingredients for 부대찌개); grouping by what is avoided keeps every cause and the
# sentence count small.
# Two since 2026-11-23 (three made the reason long).
MAX_GROUPS = 2
MAX_PER_GROUP = 3


def explain_groups(request: DecisionRequest, response: DecisionResponse) -> list[dict]:
    """Risk ingredients whose name does not show why they hit the user, grouped by what
    in them the user avoids ("돼지고기": 햄·소시지, "생선": 멸치액젓·까나리), most likely
    first. An ingredient whose name shows it (새우 for shrimp) is left to the verdict.

    What is avoided is in the user's language, not tag codes (2026-11-15), and is never
    the user's restriction: called "matched_user_restrictions" until 2026-11-22, it made
    the model write "생선 제한" for a vegan's 어묵. A per-item rule with an English example
    was removed on 2026-11-17 (the model copied "may contain" into Korean); see ⑧ §8.
    How often and the user's restriction stay out: the fixed tail and the result screen
    say them ("잡채에는 이것도 자주 들어가고, 비건 기준에 맞지 않아요" only added words)."""

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
                }
                for item in items[:MAX_PER_GROUP]
            ],
        }
        for tags, items in list(groups.items())[:MAX_GROUPS]
    ]


def _by_likelihood(items):
    return sorted(items, key=lambda i: -(i.posterior_mean if i.posterior_mean is not None else 1.0))


def card_text(request: DecisionRequest, response: DecisionResponse) -> str:
    """The card step after the reason lines (2026-11-22, messages.py ``card_step``): the
    remove-request card for ingredients likely and removable, the question card for the
    rest. The LLM picked the wrong card, so this is a template."""

    items = response.risk_ingredients
    remove = any(next_step(i) == "remove_request_card" for i in items)
    ask = any(next_step(i) == "question_card" for i in items)
    return card_step(remove, ask, request.locale)


# Words the reason is told to use for a hidden ingredient ("inside a sauce or
# seasoning"), which are also ingredient names in the tables. Without this, "굴은 양념 안에
# 들어 있을 수 있어요" was dropped as naming a new ingredient (2026-11-22, 잡채·떡볶이).
# 젓갈 too: "새우젓도 해산물 젓갈이라" was dropped for 부대찌개 the same way, and the reason
# is now told to say what an unfamiliar ingredient is (2026-11-22). 소금 too (2026-11-23):
# the Korean example "멸치를 소금에 절여 발효시켜" says how a sauce is made, and a reason
# copying that wording was dropped.
GENERIC_WORDS = ("양념", "소스", "조미료", "젓갈", "소금")


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
    삼겹살 for pork. "돼지고기" for a pork restriction says it already.

    A plain ingredient (``taxonomy_category`` "base": 굴, 멸치, 오징어) is not explained
    either (2026-11-23): "까나리는 생선이라서 생선이 들어갈 수 있어요" only said it again.
    Processed, fermented, sauces and names outside the table still are."""

    name = item.ingredient.canonical_name
    if taxonomy_categories().get(name) == "base":
        return False
    for tag in item.matched_tags:
        words = (TAG_NAMES.get(tag, {}).get("ko"), *NAME_ALIASES.get(tag, ()))
        if any(word and word in name for word in words):
            continue
        if name in WELL_KNOWN_SOURCES.get(tag, ()):
            continue
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
# Foods everyone knows carry the tag, by exact name (2026-11-23): "햄·소시지: 돼지고기로
# 만든 가공육" told nobody anything. Only the pair counts: 만두 for pork is still
# explained, 햄 for pork is not. What is left to explain is where the cause is hidden
# (액젓, 젓갈, 굴소스, 어묵, 맛살, 가쓰오부시, 김치, 다시다, 맛술 …).
WELL_KNOWN_SOURCES: dict[str, frozenset[str]] = {
    "is_pork": frozenset({"햄", "소시지", "베이컨", "족발"}),
    "is_milk": frozenset({"버터", "치즈", "요구르트", "생크림", "아이스크림", "연유"}),
    "is_soybean": frozenset({"두부", "두유"}),
    "is_wheat": frozenset({
        "빵", "국수", "라면", "라면사리", "소면", "우동면", "칼국수면", "파스타면", "밀가루", "밀떡",
        "수제비", "빵가루", "튀김가루", "부침가루",
    }),
    "is_alcohol": frozenset({"소주", "맥주", "막걸리", "와인", "술"}),
}


# What the reason is made of, decided by code before any LLM call (2026-11-23):
# "none" no reason; "limits_only" fixed limit sentences, no LLM; "groups" the LLM
# explains the groups; "groups_and_limits" the LLM explains the groups and code adds the
# limit sentences. The LLM never sees the limits.
ReasonRoute = Literal["none", "limits_only", "groups", "groups_and_limits"]


def reason_route(request: DecisionRequest, response: DecisionResponse) -> ReasonRoute:
    """Write a reason only when it says something the verdict sentence and notes do not
    (2026-11-02): an ingredient whose name does not show the restriction it hits, or a
    known limit (few web recipes, missing information). A menu with no information at
    all, or a safe one, is already said by the verdict sentence. Neither is a user with
    no restriction: nothing can go against them, so a reason only spends a call
    (2026-11-18, 공기밥 for a user with no restriction got one)."""

    if response.risk_level == "safe" or request.no_information:
        return "none"
    if not forbidden_tags(request.user_profile):
        return "none"
    groups = any(needs_explaining(item) for item in response.risk_ingredients)
    limits = bool(limit_text(request))
    if groups:
        return "groups_and_limits" if limits else "groups"
    return "limits_only" if limits else "none"


def needs_reason(request: DecisionRequest, response: DecisionResponse) -> bool:
    return reason_route(request, response) != "none"


def reason_instructions(request: DecisionRequest, groups: list[dict], main: list[dict] | None = None) -> str:
    """The reason prompt: the description task with ``main`` (None: no description),
    the group task with ``groups``, the translation rule only when a name has no
    translation yet, the Korean examples only in Korean and only for the tasks given."""

    language = LANGUAGE_NAMES[request.locale]
    names = [item["name"] for g in groups for item in g["ingredients"]] + [i["name"] for i in main or []]
    translate = TRANSLATE_RULE if untranslated(names, request.locale) else ""
    examples = ""
    if request.locale == "ko":
        examples = (KOREAN_DESCRIPTION_EXAMPLES if main is not None else "") + (
            KOREAN_PHRASE_EXAMPLES if groups else ""
        )
    description_task = DESCRIPTION_TASK if main is not None else NO_DESCRIPTION_TASK
    article = ARTICLE_RULE.get(request.locale, "")
    return REASON_PROMPT.format(
        language=language,
        description_task=description_task.format(
            language=language,
            description_chars=MAX_DESCRIPTION_CHARS[request.locale],
            max_ingredients=MAX_MENU_INGREDIENTS,
            article=article,
        ),
        groups_task=(GROUPS_TASK if groups else NO_GROUPS_TASK).format(
            language=language, max_chars=MAX_PHRASE_CHARS[request.locale], article=article
        ),
        translate=translate.format(language=language),
        examples=examples,
    )


def group_label(group: dict, translations: Iterable[NameTranslation], locale: Locale) -> str | None:
    """The group's names as the line starts ("햄·소시지"). A name with no translation yet
    takes the LLM's translation; without one the line is dropped (None), since Korean
    must not show in another language."""

    told = {t.korean.strip(): t.translated.strip() for t in translations if t.translated.strip()}
    names = [
        told.get(item["name"], item["name"]) if untranslated([item["name"]], locale) else item["name"]
        for item in group["ingredients"]
    ]
    if untranslated(names, locale):
        return None
    return LABEL_JOIN[locale].join(dict.fromkeys(names))


# "등" after the last ingredient a Korean menu description names (2026-11-24, ⑧ 담당):
# a restaurant's version may differ, and the model left it out though told to
# ("배추김치와 두부를 넣고 얼큰하게 끓인 찌개"). The particle after the name moves behind
# 등, which ends in a consonant: 두부를 → 두부 등을. Longest first (으로 before 로).
_ETC_PARTICLES: dict[str, str] = {
    "으로": "으로", "이나": "이나", "로": "으로", "을": "을", "를": "을", "이": "이", "가": "이",
    "와": "과", "과": "과", "은": "은", "는": "은", "의": "의", "에": "에", "도": "도", "나": "이나",
}
_ETC_RE = re.compile(r"(?<![가-힣])등(?![가-힣])|등(?:을|이|과|으로|은|의|에|도|이나)")
# Where a list that goes on after 와/과 ends: a word with an object, subject or "with"
# particle ("채소를", "채소로").
_LIST_END_RE = re.compile(r"([가-힣]+?)(으로|을|를|이|가|로)(?![가-힣])")


def with_etc(phrase: str, names: Iterable[str]) -> str:
    """``phrase`` with "등" after the last of ``names`` it mentions as a word of its own
    (followed by a particle above), unless it says 등 already. The names are tried from
    the last one back, so a name inside a longer word ("두부조림", 김 in "김치를") is
    skipped. Unchanged when none fits."""

    if _ETC_RE.search(phrase):
        return phrase
    spans = {
        (match.end(), match.end() - match.start())
        for name in {n.strip() for n in names if n and n.strip()}
        for match in re.finditer(re.escape(name), phrase)
    }
    for end, length in sorted(spans, reverse=True):
        if re.match(r"[가-힣]", phrase[end - length - 1:end - length] or " "):
            continue
        changed = _etc_after(phrase, end)
        if changed is not None:
            return changed
    return phrase


def _etc_after(phrase: str, end: int) -> str | None:
    rest = phrase[end:]
    # "당면과 여러 채소를": the list goes on past the last name, so 등 closes the list
    # ("여러 채소 등을"), not the name ("당면 등과 여러 채소를").
    if rest[:1] in ("와", "과"):
        tail = _LIST_END_RE.search(rest, 1)
        if tail is None:
            return None
        cut = end + tail.start(2)
        return f"{phrase[:cut]} 등{_ETC_PARTICLES[tail.group(2)]}{phrase[end + tail.end(2):]}"
    for particle in sorted(_ETC_PARTICLES, key=len, reverse=True):
        if rest.startswith(particle) and not re.match(r"[가-힣]", rest[len(particle):len(particle) + 1] or " "):
            return f"{phrase[:end]} 등{_ETC_PARTICLES[particle]}{rest[len(particle):]}"
    return None


class ReasonWriter:
    """LLM side of ``message.reason``. Never raises; a failure is ``None``."""

    def __init__(self, llm: XaiLLM, *, timeout_seconds: float = 5.0) -> None:
        self.llm = llm
        self.timeout_seconds = timeout_seconds

    async def write(self, request: DecisionRequest, response: DecisionResponse) -> str | None:
        """Plain sentences in one paragraph, joined by a space (2026-11-24; bullet lines
        until then, which the result screen ran together, and a line break between
        sentences until the same day): the menu description, a sentence for each group
        the LLM explained and code checked, the card step, then the most serious limit.

        The description goes on every menu (2026-11-24), so every menu with a writer
        calls the LLM once, except a side item (…사리), which has its own template
        sentence (``tool.py``) and calls it only for groups. ``reason_route`` still decides
        the rest: groups, card and limit only when it is not "none", the card and limit
        only with a group sentence or a limit to go with. With nothing left, ``reason``
        stays None (§4 "LLM 실패")."""

        route = reason_route(request, response)
        explain = route in ("groups", "groups_and_limits")
        describe = side_item_text(request) is None
        description, lines = None, []
        if describe or explain:
            description, lines = await self._llm_sentences(request, response, describe, explain)
        limits = limit_text(request)[:1] if route != "none" else []
        tail = [card_text(request, response), *limits] if lines or limits else []
        parts = [description, *lines, *tail]
        return " ".join(p for p in parts if p) or None

    async def _llm_sentences(
        self, request: DecisionRequest, response: DecisionResponse, describe: bool, explain: bool
    ) -> tuple[str | None, list[str]]:
        payload = reason_payload(request, response, describe=describe, explain=explain)
        groups = payload["explain_groups"]
        main = payload["main_ingredients"] if describe else None
        try:
            output = await asyncio.wait_for(
                self.llm.parse(reason_instructions(request, groups, main), payload, ReasonOutput, MAX_OUTPUT_TOKENS),
                timeout=self.timeout_seconds,
            )
        except Exception:
            return None, []
        description = self._description(request, response, output, main) if main is not None else None
        return description, self._lines(request, response, output, groups)

    def _description(
        self, request: DecisionRequest, response: DecisionResponse, output: ReasonOutput, main: list[dict]
    ) -> str | None:
        """The menu description as a sentence with the dish as its subject, checked like
        the group sentences: only the main ingredients given, standard names inside the
        menu name (김치 in 김치찌개, 밥 in 비빔밥) and generic words may be named; no
        percentage at all; never Korean in another language (2026-11-24)."""

        locale = request.locale
        menu = request.menu.normalized_menu_name
        phrase = _PHRASE_END_RE.sub("", " ".join(output.menu_description.split()))
        if not phrase or len(phrase) > MAX_DESCRIPTION_CHARS[locale]:
            return None
        korean = [i["korean_name"] for i in main]
        parts = {n for n in known_names("ko") if n in menu or any(n in name for name in korean)}
        korean_only = untranslated([i["name"] for i in main], locale)
        allowed = (
            with_translations([*korean, *parts])
            | {i["name"] for i in main}
            | translated_names(output.name_translations, korean_only)
            | generic_words()
        )
        checked = check_reason(
            phrase,
            locale=locale,
            risk_level=response.risk_level,
            percents=[],
            allowed_names=allowed,
            menu_names=[menu],
            verdict=response.message.verdict,
            evidence_basis=response.evidence_basis,
            korean_names=[],
        )
        # ``check_reason`` lets the menu name through as is; outside Korean it must not be
        # in Korean either ("a spicy 김치찌개 with pork").
        if checked is None or (locale != "ko" and _HANGUL_RUN_RE.search(checked)):
            return None
        phrase = _PHRASE_END_RE.sub("", checked)
        if locale == "ko":
            phrase = with_etc(phrase, [*korean, *(i["name"] for i in main)])
        subject = menu if locale == "ko" else DISH_SUBJECT[locale]
        return EXPLAIN_SENTENCE[locale](subject, phrase, False)

    def _lines(
        self, request: DecisionRequest, response: DecisionResponse, output: ReasonOutput, groups: list[dict]
    ) -> list[str]:
        # One phrase per group, or the order cannot be trusted.
        if not groups or len(output.phrases) != len(groups):
            return []
        korean_only = untranslated(
            [i.ingredient.localized_name for i in response.risk_ingredients], request.locale
        )
        allowed = (
            risk_names(response)
            | restriction_names(forbidden_tags(request.user_profile))
            | translated_names(output.name_translations, korean_only)
            | generic_words()
        )
        percents = [
            shown_percent(item.posterior_mean)
            for item in response.risk_ingredients
            if item.posterior_mean is not None
        ]
        lines = []
        for group, phrase in zip(groups, output.phrases):
            phrase = _PHRASE_END_RE.sub("", " ".join(phrase.split()))
            if not phrase or len(phrase) > MAX_PHRASE_CHARS[request.locale]:
                continue
            checked = check_reason(
                phrase,
                locale=request.locale,
                risk_level=response.risk_level,
                percents=percents,
                allowed_names=allowed,
                menu_names=[request.menu.normalized_menu_name],
                verdict=response.message.verdict,
                evidence_basis=response.evidence_basis,
                korean_names=korean_only,
            )
            label = group_label(group, output.name_translations, request.locale)
            if checked is None or label is None:
                continue
            many = len(group["ingredients"]) > 1
            lines.append(EXPLAIN_SENTENCE[request.locale](label, _PHRASE_END_RE.sub("", checked), many))
        return lines
