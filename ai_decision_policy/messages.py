"""⑧ result message: verdict line and notes in the user's language (docs/8-agent-xai.md §4, §4-1).

Fixed templates, no LLM. Ingredient names come from ``localized_name`` (Korean when
there is no translation); nothing here translates or guesses. The verdict line follows
the result screen: who confirmed what, what is still unchecked, and which restriction it
breaks, with no percentages (the screen shows them per ingredient). ``reason`` is the
LLM part (§0-2); the verdict and notes stand on their own (§4 "LLM 실패").
"""

from __future__ import annotations

from collections.abc import Callable

from ai_supervisor.contracts.common import Locale, UserProfile
from ai_supervisor.contracts.decision import DecisionRequest, DecisionRiskIngredient
from ai_supervisor.contracts.external import DecisionMessage, MessageNote

from .names import display_name
from .notes import NoteChoice, select_notes
from .policy import DecisionOutcome, has_no_ingredients, is_hard_evidence
from .profile import RELIGION_TAGS, VEGETARIAN_TAGS
from .questions import FLAG_PLACES, LISTS, _has_final_consonant, tag_name


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


# The verdict line follows the result screen (front Figma S10, 2026-11-06): who confirmed
# what, what is still unchecked, and which of the user's restrictions it breaks.
# Probabilities are not in the sentence; the screen shows them next to each ingredient.
# {x} is a joined list of names, {last} its last plain name (Korean particles follow it),
# {r} the user's restriction names.
VerdictTemplate = Callable[[str, str], str]

# DANGER: the owner said a forbidden ingredient (or flag tag) is in the dish.
OWNER_SAYS_PRESENT: dict[str, VerdictTemplate] = {
    "ko": lambda x, last: f"사장님이 {x}{_ko(last, '이', '가')} 들어 있다고 확인했어요.",
    "en": lambda x, last: f"The owner confirmed this dish contains {x}.",
    "ar": lambda x, last: f"أكّد صاحب المطعم أن هذا الطبق يحتوي على {x}.",
    "zh-CN": lambda x, last: f"店主已确认这道菜含有{x}。",
    "zh-TW": lambda x, last: f"店家已確認這道菜含有{x}。",
    "ja": lambda x, last: f"店主が、この料理に{x}が入っていると確認しました。",
    "es": lambda x, last: f"El dueño confirmó que este plato lleva {x}.",
}
BREAKS: dict[str, Callable[[str], str]] = {
    "ko": lambda r: f"{r} 기준에 맞지 않아요.",
    "en": lambda r: f"It doesn't fit your dietary needs ({r}).",
    "ar": lambda r: f"لا يناسب احتياجاتك الغذائية ({r}).",
    "zh-CN": lambda r: f"不符合您的饮食限制（{r}）。",
    "zh-TW": lambda r: f"不符合您的飲食限制（{r}）。",
    "ja": lambda r: f"あなたの食事制限（{r}）に合いません。",
    "es": lambda r: f"No es apto para tus restricciones ({r}).",
}
# CAUTION: forbidden ingredients the owner said are not in the dish come first.
OWNER_SAYS_ABSENT: dict[str, VerdictTemplate] = {
    "ko": lambda x, last: f"사장님이 {x}{_ko(last, '은', '는')} 넣지 않는다고 확인했어요.",
    "en": lambda x, last: f"The owner confirmed there is no {x}.",
    "ar": lambda x, last: f"أكّد صاحب المطعم عدم وجود {x}.",
    "zh-CN": lambda x, last: f"店主已确认不放{x}。",
    "zh-TW": lambda x, last: f"店家已確認不放{x}。",
    "ja": lambda x, last: f"店主が、{x}は入れないと確認しました。",
    "es": lambda x, last: f"El dueño confirmó que no lleva {x}.",
}
STILL_TO_CHECK: dict[str, VerdictTemplate] = {
    "ko": lambda x, last: f"{x}{_ko(last, '은', '는')} 아직 확인이 필요해요.",
    "en": lambda x, last: f"We still need to check for {x}.",
    "ar": lambda x, last: f"ما زال يلزم التحقق من {x}.",
    "zh-CN": lambda x, last: f"{x}还需要确认。",
    "zh-TW": lambda x, last: f"{x}還需要確認。",
    "ja": lambda x, last: f"{x}はまだ確認が必要です。",
    "es": lambda x, last: f"Aún hay que confirmar si lleva {x}.",
}
SAFE_FOR: dict[str, Callable[[str], str]] = {
    "ko": lambda r: f"사장님이 확인한 재료에 {r} 기준에 맞지 않는 재료가 없어요.",
    "en": lambda r: f"The owner confirmed none of the ingredients goes against your dietary needs ({r}).",
    "ar": lambda r: f"أكّد صاحب المطعم أنه لا يوجد أي مكوّن يخالف احتياجاتك الغذائية ({r}).",
    "zh-CN": lambda r: f"店主已确认，没有不符合您饮食限制（{r}）的食材。",
    "zh-TW": lambda r: f"店家已確認，沒有不符合您飲食限制（{r}）的食材。",
    "ja": lambda r: f"店主の確認により、食事制限（{r}）に合わない食材は入っていません。",
    "es": lambda r: f"El dueño confirmó que ningún ingrediente va contra tus restricciones ({r}).",
}
# SAFE for a user with no restriction at all (nothing to name).
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
    "ko": "피해야 할 재료는 찾지 못했지만, 추정 결과라 확인이 필요해요.",
    "en": "We found nothing you avoid, but this is an estimate, so it still needs to be checked.",
    "ar": "لم نجد مكونات تتجنبها، لكن هذه نتيجة تقديرية، لذا يلزم التحقق.",
    "zh-CN": "没有发现您需要避免的食材，但这是推测结果，仍需确认。",
    "zh-TW": "沒有發現您需要避免的食材，但這是推測結果，仍需確認。",
    "ja": "避けるべき食材は見つかりませんでしたが、推定の結果のため確認が必要です。",
    "es": "No encontramos ingredientes que evites, pero es una estimación, así que hay que confirmarlo.",
}
# CAUTION with nothing to go on (no information, no ingredients).
NO_INFORMATION: dict[str, str] = {
    "ko": "재료 정보를 찾지 못했어요. 직원에게 직접 확인해 주세요.",
    "en": "We couldn't find ingredient information for this dish. Please check with the staff.",
    "ar": "لم نجد معلومات عن مكونات هذا الطبق. يُرجى التأكد من الموظفين.",
    "zh-CN": "没有找到这道菜的食材信息，请直接向店员确认。",
    "zh-TW": "沒有找到這道菜的食材資訊，請直接向店員確認。",
    "ja": "この料理の食材情報が見つかりませんでした。スタッフに直接確認してください。",
    "es": "No encontramos información de los ingredientes de este plato. Confírmalo con el personal.",
}
# CAUTION/unknown for any other gap (an unanswered flag, failed lookup, no store).
NOT_ENOUGH_INFORMATION: dict[str, str] = {
    "ko": "아직 확인되지 않은 정보가 있어요. 직원에게 직접 확인해 주세요.",
    "en": "Some information about this dish isn't confirmed yet. Please check with the staff.",
    "ar": "بعض المعلومات عن هذا الطبق لم تُؤكَّد بعد. يُرجى التأكد من الموظفين.",
    "zh-CN": "这道菜还有未确认的信息，请直接向店员确认。",
    "zh-TW": "這道菜還有未確認的資訊，請直接向店員確認。",
    "ja": "まだ確認されていない情報があります。スタッフに直接確認してください。",
    "es": "Hay información de este plato que aún no está confirmada. Confírmalo con el personal.",
}

# The user's restrictions by name, as the screen shows them ("내 식단: 비건 · 새우 알레르기").
RESTRICTION_NAMES: dict[str, dict[str, str]] = {
    "halal": {"ko": "할랄", "en": "halal", "ar": "الحلال", "zh-CN": "清真", "zh-TW": "清真", "ja": "ハラール", "es": "halal"},
    "kosher": {"ko": "코셔", "en": "kosher", "ar": "الكوشر", "zh-CN": "犹太洁食", "zh-TW": "猶太潔食", "ja": "コーシャ", "es": "kosher"},
    "hindu": {"ko": "힌두교 식단", "en": "Hindu diet", "ar": "النظام الغذائي الهندوسي", "zh-CN": "印度教饮食", "zh-TW": "印度教飲食", "ja": "ヒンドゥー教の食事", "es": "dieta hindú"},
    "vegan": {"ko": "비건", "en": "vegan", "ar": "نباتي صرف", "zh-CN": "纯素", "zh-TW": "純素", "ja": "ヴィーガン", "es": "vegano"},
    "lacto": {"ko": "락토 채식", "en": "lacto-vegetarian", "ar": "نباتي يتناول الألبان", "zh-CN": "奶素", "zh-TW": "奶素", "ja": "ラクト・ベジタリアン", "es": "lactovegetariano"},
    "ovo": {"ko": "오보 채식", "en": "ovo-vegetarian", "ar": "نباتي يتناول البيض", "zh-CN": "蛋素", "zh-TW": "蛋素", "ja": "オボ・ベジタリアン", "es": "ovovegetariano"},
    "lacto_ovo": {"ko": "락토오보 채식", "en": "lacto-ovo vegetarian", "ar": "نباتي يتناول الألبان والبيض", "zh-CN": "蛋奶素", "zh-TW": "蛋奶素", "ja": "ラクト・オボ・ベジタリアン", "es": "ovolactovegetariano"},
    "pesco": {"ko": "페스코 채식", "en": "pescatarian", "ar": "نباتي يأكل المأكولات البحرية", "zh-CN": "鱼素", "zh-TW": "魚素", "ja": "ペスコ・ベジタリアン", "es": "pescetariano"},
    "no_alcohol": {"ko": "무알코올", "en": "no alcohol", "ar": "بدون كحول", "zh-CN": "不含酒", "zh-TW": "不含酒", "ja": "アルコールなし", "es": "sin alcohol"},
    "no_spicy": {"ko": "안 매운 음식", "en": "no spicy food", "ar": "بدون طعام حار", "zh-CN": "不吃辣", "zh-TW": "不吃辣", "ja": "辛くない料理", "es": "sin picante"},
}
ALLERGY: dict[str, str] = {
    "ko": "{x} 알레르기", "en": "{x} allergy", "ar": "حساسية {x}", "zh-CN": "{x}过敏",
    "zh-TW": "{x}過敏", "ja": "{x}アレルギー", "es": "alergia a {x}",
}
RESTRICTION_SEPARATOR: dict[str, str] = {
    "ko": " · ", "en": ", ", "ar": "، ", "zh-CN": "、", "zh-TW": "、", "ja": "・", "es": ", ",
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


def _named(items: list[DecisionRiskIngredient]) -> list[str]:
    """Names, highest probability first. ``items`` is already one per ingredient
    (``merge_same_ingredient`` joins the rows ④ sends per tag bundle, #327)."""

    ordered = sorted(items, key=lambda i: -i.posterior_mean if i.posterior_mean is not None else 1.0)
    return [item.ingredient.localized_name for item in ordered]


def _say(template: dict[str, VerdictTemplate], names: list[str], lang: str) -> str:
    return template[lang](AND_LISTS[lang](names), names[-1])


def _sentences(parts: list[str], lang: str) -> str:
    return ("" if lang in ("zh-CN", "zh-TW", "ja") else " ").join(parts)


def restriction_names(profile: UserProfile, lang: str, tags: set[str] | None = None) -> str:
    """The user's restrictions by name; with ``tags``, only those that forbid one of them
    ("비건" for 어묵, "새우 알레르기" for 새우)."""

    names: list[str] = []

    def add(covers: frozenset[str], name: str) -> None:
        if tags is None or covers & tags:
            names.append(name)

    if profile.religion_type is not None:
        add(RELIGION_TAGS[profile.religion_type], RESTRICTION_NAMES[profile.religion_type][lang])
    if profile.vegetarian_type is not None:
        add(VEGETARIAN_TAGS[profile.vegetarian_type], RESTRICTION_NAMES[profile.vegetarian_type][lang])
    if profile.no_alcohol:
        add(frozenset({"is_alcohol"}), RESTRICTION_NAMES["no_alcohol"][lang])
    if profile.no_spicy:
        add(frozenset({"is_spicy"}), RESTRICTION_NAMES["no_spicy"][lang])
    for tag in profile.allergies:
        add(frozenset({tag}), ALLERGY[lang].format(x=tag_name(tag, lang)))
    return RESTRICTION_SEPARATOR[lang].join(dict.fromkeys(names))


def owner_said_absent(request: DecisionRequest, outcome: DecisionOutcome, lang: str) -> list[str]:
    """Forbidden ingredients the owner said are not in the dish (달걀 지단 for a vegan)."""

    forbidden = set(outcome.forbidden_tags)
    localized = {n.ingredient_id: n.localized_name for n in request.localized_names}
    return list(dict.fromkeys(
        display_name(c.ingredient, localized, lang)
        for c in request.confirmed_results
        if is_hard_evidence(c) and c.status == "absent" and set(c.constraint_tags) & forbidden
    ))


def verdict(request: DecisionRequest, outcome: DecisionOutcome, lang: str) -> str:
    level, basis = outcome.risk_level, outcome.evidence_basis
    profile = request.user_profile
    if level == "safe" and basis == "confirmed":
        restrictions = restriction_names(profile, lang)
        return SAFE_FOR[lang](restrictions) if restrictions else SAFE[lang]
    if level == "danger" and basis == "confirmed":
        present = [r for r in outcome.risk_ingredients if r.status == "present"]
        names = _named(present)
        tags = {t for r in present for t in r.matched_tags}
        # A "present" answer to a flag names tags, not an ingredient (§2-1 5-2).
        for check in outcome.flag_checks:
            if check.state == "danger":
                names += [tag_name(tag, lang) for tag in check.matched_tags]
                tags |= set(check.matched_tags)
        names = list(dict.fromkeys(names))  # two flags can name the same tag
        if names:
            parts = [_say(OWNER_SAYS_PRESENT, names, lang)]
            restrictions = restriction_names(profile, lang, tags)
            if restrictions:
                parts.append(BREAKS[lang](restrictions))
            return _sentences(parts, lang)
    absent = owner_said_absent(request, outcome, lang)
    lead = [_say(OWNER_SAYS_ABSENT, absent, lang)] if absent else []
    if level == "caution" and basis == "estimated":
        names = _named(outcome.risk_ingredients)
        # Tags matched by a menu attribute (e.g. spicy) rather than an ingredient (§2-1 5-1).
        by_ingredient = {t for r in outcome.risk_ingredients for t in r.matched_tags}
        names += [tag_name(tag, lang) for tag in outcome.matched_tags if tag not in by_ingredient]
        if names:
            return _sentences([*lead, _say(STILL_TO_CHECK, names, lang)], lang)
        return _sentences([*lead, NOT_CONFIRMED[lang]], lang)
    # caution/unknown, and anything the policy does not produce, asks the user to check
    # with the staff: never a sentence that sounds safe.
    if request.no_information or has_no_ingredients(request):
        return NO_INFORMATION[lang]
    return _sentences([*lead, NOT_ENOUGH_INFORMATION[lang]], lang)


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
        verdict=verdict(request, outcome, lang),
        reason=None,
        notes=[
            MessageNote(code=note.code, text=note_text(note, check_tags, lang))
            for note in select_notes(outcome)
        ],
    )
