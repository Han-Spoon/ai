"""⑧ result message: verdict line and notes in the user's language (docs/8-agent-xai.md §4, §4-1).

Fixed templates, no LLM. Ingredient names come from ``localized_name`` (Korean when
there is no translation) and probabilities from ``posterior_mean``; nothing here
translates or guesses. ``reason`` is the LLM part (§0-2) and stays ``None`` until the
LLM is attached; the verdict and notes stand on their own (§4 "LLM 실패").
"""

from __future__ import annotations

import math
from collections.abc import Callable

from ai_supervisor.contracts.common import Locale
from ai_supervisor.contracts.decision import DecisionRequest, DecisionRiskIngredient
from ai_supervisor.contracts.external import DecisionMessage, MessageNote

from .notes import NoteChoice, select_notes
from .policy import DecisionOutcome
from .questions import FLAG_PLACES, LISTS, _has_final_consonant, tag_name

# Percent shown in a sentence: whole numbers, never "0%" or "100%" (§4 확률 수치 표시).
PERCENT: dict[str, tuple[str, str, str]] = {
    # (about n, below 1, above 99)
    "ko": ("약 {n}%", "1% 미만", "99% 이상"),
    "en": ("about {n}%", "less than 1%", "over 99%"),
    "ar": ("حوالي {n}%", "أقل من 1%", "أكثر من 99%"),
    "zh-CN": ("约{n}%", "不到1%", "超过99%"),
    "zh-TW": ("約{n}%", "不到1%", "超過99%"),
    "ja": ("約{n}%", "1%未満", "99%以上"),
    "es": ("aprox. {n}%", "menos del 1%", "más del 99%"),
}

# How one ingredient and its probability sit in a list.
WITH_PERCENT: dict[str, str] = {
    "ko": "{name}({p})",
    "en": "{name} ({p})",
    "ar": "{name} ({p})",
    "zh-CN": "{name}（{p}）",
    "zh-TW": "{name}（{p}）",
    "ja": "{name}（{p}）",
    "es": "{name} ({p})",
}


def _and(comma: str, last: str) -> Callable[[list[str]], str]:
    def join(words: list[str]) -> str:
        if len(words) == 1:
            return words[0]
        return comma.join(words[:-1]) + last + words[-1]

    return join


# "A, B and C". Korean gets its particle from the last name, added by the template.
AND_LISTS: dict[str, Callable[[list[str]], str]] = {
    "ko": _and(", ", ", "),
    "en": _and(", ", " and "),
    "ar": _and("، ", " و"),
    "zh-CN": _and("、", "和"),
    "zh-TW": _and("、", "和"),
    "ja": _and("、", "、"),
    "es": _and(", ", " y "),
}


def _ko(word: str, with_final: str, without: str) -> str:
    return with_final if _has_final_consonant(word) else without


# {x} is the joined list, {last} the last plain name (Korean particles follow it).
VerdictTemplate = Callable[[str, str], str]

CONTAINS: dict[str, VerdictTemplate] = {
    "ko": lambda x, last: f"{x}{_ko(last, '이', '가')} 들어 있어요.",
    "en": lambda x, last: f"This dish contains {x}.",
    "ar": lambda x, last: f"يحتوي هذا الطبق على {x}.",
    "zh-CN": lambda x, last: f"这道菜含有{x}。",
    "zh-TW": lambda x, last: f"這道菜含有{x}。",
    "ja": lambda x, last: f"この料理には{x}が入っています。",
    "es": lambda x, last: f"Este plato contiene {x}.",
}
MAY_CONTAIN: dict[str, VerdictTemplate] = {
    "ko": lambda x, last: f"{x}{_ko(last, '이', '가')} 들어 있을 수 있어요.",
    "en": lambda x, last: f"This dish may contain {x}.",
    "ar": lambda x, last: f"قد يحتوي هذا الطبق على {x}.",
    "zh-CN": lambda x, last: f"这道菜可能含有{x}。",
    "zh-TW": lambda x, last: f"這道菜可能含有{x}。",
    "ja": lambda x, last: f"この料理には{x}が入っている可能性があります。",
    "es": lambda x, last: f"Este plato puede contener {x}.",
}
SAFE: dict[str, str] = {
    "ko": "사장님이 확인한 재료 중 피해야 할 재료가 없어요.",
    "en": "The owner confirmed none of the ingredients you avoid are in this dish.",
    "ar": "أكّد صاحب المطعم أن هذا الطبق لا يحتوي على أي من المكونات التي تتجنبها.",
    "zh-CN": "店主已确认，这道菜不含您需要避免的食材。",
    "zh-TW": "店家已確認，這道菜不含您需要避免的食材。",
    "ja": "店主の確認により、この料理には避けるべき食材が入っていません。",
    "es": "El dueño confirmó que este plato no lleva ningún ingrediente que evites.",
}
# CAUTION from estimates where nothing overlaps the user's restrictions.
NOT_CONFIRMED: dict[str, str] = {
    "ko": "피해야 할 재료는 찾지 못했지만, 추정 결과라 안전하다고 단정할 수 없어요.",
    "en": "We found nothing you avoid, but this is an estimate, so we can't say for sure it's safe.",
    "ar": "لم نجد مكونات تتجنبها، لكن هذه نتيجة تقديرية، لذلك لا يمكننا الجزم بأنه آمن.",
    "zh-CN": "没有发现您需要避免的食材，但这是推测结果，无法确定是否安全。",
    "zh-TW": "沒有發現您需要避免的食材，但這是推測結果，無法確定是否安全。",
    "ja": "避けるべき食材は見つかりませんでしたが、推定の結果のため安全とは言い切れません。",
    "es": "No encontramos ingredientes que evites, pero es una estimación, así que no podemos asegurar que sea seguro.",
}
NOT_ENOUGH_INFORMATION: dict[str, str] = {
    "ko": "정보가 부족해 안전한지 확인할 수 없어요.",
    "en": "We don't have enough information to confirm this dish is safe for you.",
    "ar": "لا تتوفر لدينا معلومات كافية للتأكد من أن هذا الطبق آمن لك.",
    "zh-CN": "信息不足，无法确认这道菜对您是否安全。",
    "zh-TW": "資訊不足，無法確認這道菜對您是否安全。",
    "ja": "情報が不足しているため、この料理が安全か確認できません。",
    "es": "No tenemos suficiente información para confirmar que este plato sea seguro para ti.",
}

# Notes. {x} is an "A or B" tag list, {place} where a flag hides it, {name} an ingredient.
AMBIGUOUS_PLACE: dict[str, Callable[[str, str], str]] = {
    "ko": lambda place, x: f"{place}에 {x}{_ko(x, '이', '가')} 들어갈 수 있어 확인이 필요해요.",
    "en": lambda place, x: f"There may be {x} in {place}, so it needs to be checked.",
    "ar": lambda place, x: f"قد يوجد {x} في {place}، لذا يلزم التحقق.",
    "zh-CN": lambda place, x: f"{place}里可能有{x}，需要确认。",
    "zh-TW": lambda place, x: f"{place}裡可能有{x}，需要確認。",
    "ja": lambda place, x: f"{place}に{x}が入っている可能性があるため、確認が必要です。",
    "es": lambda place, x: f"Puede haber {x} en {place}, por lo que hay que confirmarlo.",
}
AMBIGUOUS_VARIANT: dict[str, Callable[[str], str]] = {
    "ko": lambda x: f"재료가 바뀐 메뉴라 {x}{_ko(x, '이', '가')} 들어갈 수 있어 확인이 필요해요.",
    "en": lambda x: f"This is a variation of the dish with different ingredients, so it may contain {x} and needs to be checked.",
    "ar": lambda x: f"هذا الطبق نسخة معدّلة بمكونات مختلفة، لذا قد يحتوي على {x} ويلزم التحقق.",
    "zh-CN": lambda x: f"这道菜是改良版，食材可能不同，可能含有{x}，需要确认。",
    "zh-TW": lambda x: f"這道菜是改良版，食材可能不同，可能含有{x}，需要確認。",
    "ja": lambda x: f"アレンジされたメニューのため{x}が入っている可能性があり、確認が必要です。",
    "es": lambda x: f"Es una variante del plato con otros ingredientes, así que puede llevar {x} y hay que confirmarlo.",
}
MAIN_INGREDIENT: dict[str, Callable[[str], str]] = {
    "ko": lambda name: f"{name}{_ko(name, '은', '는')} 이 메뉴의 주재료라 빼기 어려울 수 있어요.",
    "en": lambda name: f"{_capital(name)} is a main ingredient of this dish, so it may be hard to leave out.",
    "ar": lambda name: f"قد يصعب طلب هذا الطبق بدون {name} لأنّ هذا المكوّن أساسي فيه.",
    "zh-CN": lambda name: f"{name}是这道菜的主要食材，可能很难去掉。",
    "zh-TW": lambda name: f"{name}是這道菜的主要食材，可能很難去掉。",
    "ja": lambda name: f"{name}はこの料理の主な食材なので、抜くのは難しいかもしれません。",
    # Spanish and Arabic put the name after "without": no article or gender to guess.
    "es": lambda name: f"Puede ser difícil pedir este plato sin {name}, porque es un ingrediente principal.",
}
HIDDEN_INGREDIENT: dict[str, Callable[[str], str]] = {
    "ko": lambda name: f"{name}{_ko(name, '은', '는')} 양념이나 소스 같은 다른 재료 안에 들어 있어 빼기 어려울 수 있어요.",
    "en": lambda name: f"{_capital(name)} is inside another ingredient such as a sauce or seasoning, so it may be hard to leave out.",
    "ar": lambda name: f"قد يصعب طلب هذا الطبق بدون {name} لأنّ هذا المكوّن موجود داخل مكوّن آخر مثل الصلصة أو التتبيلة.",
    "zh-CN": lambda name: f"{name}混在酱料或调味料等其他食材里，可能很难去掉。",
    "zh-TW": lambda name: f"{name}混在醬料或調味料等其他食材裡，可能很難去掉。",
    "ja": lambda name: f"{name}はタレや調味料など別の食材に含まれているため、抜くのは難しいかもしれません。",
    "es": lambda name: f"Puede ser difícil pedir este plato sin {name}, porque va dentro de otro ingrediente, como una salsa o un aliño.",
}


def _capital(word: str) -> str:
    return word[:1].upper() + word[1:]


def percent(posterior_mean: float, lang: str) -> str:
    about, below, above = PERCENT[lang]
    if posterior_mean < 0.01:
        return below
    if posterior_mean > 0.99:
        return above
    return about.format(n=math.floor(posterior_mean * 100 + 0.5))


def _key(item: DecisionRiskIngredient) -> tuple[str, int | str]:
    ingredient = item.ingredient
    if ingredient.ingredient_id is not None:
        return ("id", ingredient.ingredient_id)
    return ("name", ingredient.canonical_name)


def _named(items: list[DecisionRiskIngredient]) -> list[tuple[str, float | None]]:
    """One entry per ingredient; the same one can come once per tag bundle (④ #327).
    The higher probability wins, then the highest comes first."""

    best: dict[tuple[str, int | str], tuple[str, float | None]] = {}
    for item in items:
        name, p = item.ingredient.localized_name, item.posterior_mean
        seen = best.get(_key(item))
        if seen is None or (p is not None and (seen[1] is None or p > seen[1])):
            best[_key(item)] = (name, p)
    entries = list(best.values())
    return sorted(entries, key=lambda e: -e[1] if e[1] is not None else 1.0)


def _listed(template: dict[str, VerdictTemplate], entries, lang: str) -> str:
    labels = [
        WITH_PERCENT[lang].format(name=name, p=percent(p, lang)) if p is not None else name
        for name, p in entries
    ]
    return template[lang](AND_LISTS[lang](labels), entries[-1][0])


def verdict(outcome: DecisionOutcome, lang: str) -> str:
    level, basis = outcome.risk_level, outcome.evidence_basis
    if level == "safe" and basis == "confirmed":
        return SAFE[lang]
    if level == "danger" and basis == "confirmed":
        present = [r for r in outcome.risk_ingredients if r.status == "present"]
        entries = [(name, None) for name, _ in _named(present)]
        # A "present" answer to a flag names tags, not an ingredient (§2-1 5-2).
        entries += [
            (tag_name(tag, lang), None)
            for check in outcome.flag_checks
            if check.state == "danger"
            for tag in check.matched_tags
        ]
        entries = list(dict.fromkeys(entries))
        if entries:
            return _listed(CONTAINS, entries, lang)
    if level == "caution" and basis == "estimated":
        entries = _named(outcome.risk_ingredients)
        # Tags matched by a menu attribute (e.g. spicy) rather than an ingredient (§2-1 5-1).
        by_ingredient = {t for r in outcome.risk_ingredients for t in r.matched_tags}
        entries += [
            (tag_name(tag, lang), None)
            for tag in outcome.matched_tags
            if tag not in by_ingredient
        ]
        if entries:
            return _listed(MAY_CONTAIN, entries, lang)
        return NOT_CONFIRMED[lang]
    # caution/unknown, and anything the policy does not produce, reads as "not enough
    # information": the careful sentence, never one that sounds safe.
    return NOT_ENOUGH_INFORMATION[lang]


def note_text(note: NoteChoice, check_tags: dict[str, list[str]], lang: str) -> str:
    if note.code == "ambiguous_menu":
        assert note.ambiguity_flag is not None
        tags = LISTS[lang]([tag_name(t, lang) for t in check_tags[note.ambiguity_flag]])
        place = FLAG_PLACES[note.ambiguity_flag]
        if place is None:
            return AMBIGUOUS_VARIANT[lang](tags)
        return AMBIGUOUS_PLACE[lang](place[lang], tags)
    assert note.ingredient is not None
    name = note.ingredient.localized_name
    if note.role == "identity":
        return MAIN_INGREDIENT[lang](name)
    return HIDDEN_INGREDIENT[lang](name)


def build_message(request: DecisionRequest, outcome: DecisionOutcome) -> DecisionMessage:
    lang: Locale = request.locale
    check_tags = {check.flag: check.ask_tags for check in outcome.flag_checks}
    return DecisionMessage(
        verdict=verdict(outcome, lang),
        reason=None,
        notes=[
            MessageNote(code=note.code, text=note_text(note, check_tags, lang))
            for note in select_notes(outcome)
        ],
    )
