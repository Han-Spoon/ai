"""⑧ result message: verdict line and notes in the user's language (docs/8-agent-xai.md §4, §4-1).

Fixed templates, no LLM. Ingredient names come from ``localized_name`` (Korean when
there is no translation); nothing here translates or guesses. The verdict line follows
the result screen: who confirmed what and what is still unchecked, with no "can you eat
it" lead (dropped 2026-11-24); an estimate also gives the highest chance as "n% 이상"
(2026-11-23). ``reason`` is the
LLM part (§0-2); the verdict and notes stand on their own (§4 "LLM 실패").
"""

from __future__ import annotations

import math
from collections.abc import Callable

from ai_supervisor.contracts.common import Locale
from ai_supervisor.contracts.decision import DecisionRequest, DecisionRiskIngredient
from ai_supervisor.contracts.external import DecisionMessage, MessageNote

from .names import display_name
from .notes import NoteChoice, select_notes
from .policy import DecisionOutcome, has_no_ingredients, is_hard_evidence
from .questions import FLAG_PLACES, LISTS, _has_final_consonant, _tags, tag_name


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


# The verdict names at most this many ingredients (2026-11-08); the screen shows them all.
MAX_NAMES = 7
# "and others", with no count (2026-11-24): "외 31개" next to the screen's "+43" chip
# looked like two different counts (the sentence leaves out carried rows, the screen
# does not), so only the screen counts. Korean, Chinese and Japanese attach it to the
# list without "and"; the Korean particle then follows "등".
MORE: dict[str, str] = {
    "ko": " 등",
    "en": "other ingredients",
    "ar": "مكونات أخرى",
    "zh-CN": "等",
    "zh-TW": "等",
    "ja": "など",
    "es": "otros ingredientes",
}
LISTS_NO_AND: dict[str, Callable[[list[str]], str]] = {
    "ko": ", ".join,
    "zh-CN": "、".join,
    "zh-TW": "、".join,
    "ja": "、".join,
}


def _ko(word: str, with_final: str, without: str) -> str:
    return with_final if _has_final_consonant(word) else without


# The verdict line follows the result screen (front Figma S10, 2026-11-06): who confirmed
# what and what is still unchecked. The "can you eat it, for which restriction" lead
# (2026-11-22) was dropped on 2026-11-24: the screen already shows the level and the diet.
# {x} is a joined list of names, {last} its last plain name (Korean particles follow it).
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
# DANGER from a "present" answer to a flag card that asked about several tags at once
# ("육수에 돼지고기나 새우가 들어가나요?"). "Yes" means at least one of them (§1-1), so the
# sentence repeats what the owner said with "or" instead of claiming every tag (2026-11-17).
# {place} is where the flag hides it; None (has_variant) means the whole dish.
OWNER_SAYS_ONE_OF: dict[str, Callable[[str | None, str], str]] = {
    "ko": lambda place, x: f"사장님이 {place or '이 메뉴'}에 {x}{_ko(x, '이', '가')} 들어간다고 답했어요.",
    "en": lambda place, x: f"The owner said there is {x} in {place or 'this dish'}.",
    "ar": lambda place, x: f"قال صاحب المطعم إن {place or 'هذا الطبق'} يحتوي على {x}.",
    "zh-CN": lambda place, x: f"店主表示{place or '这道菜'}里有{x}。",
    "zh-TW": lambda place, x: f"店家表示{place or '這道菜'}裡有{x}。",
    "ja": lambda place, x: f"店主によると、{place or 'この料理'}に{x}が入っています。",
    "es": lambda place, x: f"El dueño dijo que hay {x} en {place or 'este plato'}.",
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
# CAUTION/estimated: how likely the most likely restricted thing is (2026-11-23, the
# verdict shows a percent again). The highest ingredient estimate is a lower bound of
# the chance that anything restricted is in the dish, so "at least", never "at most".
AT_LEAST: dict[str, Callable[[str, int], str]] = {
    "ko": lambda x, p: f"{x}{_ko(x, '이', '가')} 들어갈 확률이 {p}% 이상이에요.",
    "en": lambda x, p: f"The chance it contains {x} is at least {p}%.",
    "ar": lambda x, p: f"احتمال احتوائه على {x} لا يقل عن {p}%.",
    "zh-CN": lambda x, p: f"含有{x}的可能性至少为{p}%。",
    "zh-TW": lambda x, p: f"含有{x}的可能性至少為{p}%。",
    "ja": lambda x, p: f"{x}が入っている確率は{p}%以上です。",
    "es": lambda x, p: f"La probabilidad de que lleve {x} es de al menos un {p}%.",
}
# Under 1%: "1% 이상" would be untrue and "0%" reads as "none" (§3 확률 수치 표시).
BELOW_ONE_PERCENT: dict[str, Callable[[str], str]] = {
    "ko": lambda x: f"{x}{_ko(x, '이', '가')} 들어갈 확률은 1% 미만이에요.",
    "en": lambda x: f"The chance it contains {x} is under 1%.",
    "ar": lambda x: f"احتمال احتوائه على {x} أقل من 1%.",
    "zh-CN": lambda x: f"含有{x}的可能性低于1%。",
    "zh-TW": lambda x: f"含有{x}的可能性低於1%。",
    "ja": lambda x: f"{x}が入っている確率は1%未満です。",
    "es": lambda x: f"La probabilidad de que lleve {x} es menor del 1%.",
}


def shown_percent(posterior_mean: float) -> int:
    """The whole number shown for an estimate, rounded down (2026-11-23): the verdict
    says "n% 이상", so 4.5% is "4%", never "5%". Never 0% or 100%: under 1% is 1 (the
    verdict says "1% 미만" then, ``is_below_one_percent``), and 100% is 99."""

    if is_below_one_percent(posterior_mean):
        return 1
    # round() first so 0.29 * 100 = 28.999… still shows 29.
    return min(99, math.floor(round(posterior_mean * 100, 6)))


def is_below_one_percent(posterior_mean: float) -> bool:
    return posterior_mean < 0.01


# CAUTION/estimated: ingredients that may be in the dish (an estimate, never "is in").
MAY_CONTAIN: dict[str, VerdictTemplate] = {
    "ko": lambda x, last: f"{x}{_ko(last, '이', '가')} 들어 있을 수 있어요.",
    "en": lambda x, last: f"It may contain {x}.",
    "ar": lambda x, last: f"قد يحتوي على {x}.",
    "zh-CN": lambda x, last: f"可能含有{x}。",
    "zh-TW": lambda x, last: f"可能含有{x}。",
    "ja": lambda x, last: f"{x}が入っている可能性があります。",
    "es": lambda x, last: f"Puede llevar {x}.",
}

# SAFE: what the owner's answers showed.
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
    "ko": "피해야 할 재료는 찾지 못했지만, 추정 결과예요.",
    "en": "We found nothing you avoid, but this is only an estimate.",
    "ar": "لم نجد مكونات تتجنبها، لكن هذه نتيجة تقديرية فقط.",
    "zh-CN": "没有发现您需要避免的食材，但这只是推测结果。",
    "zh-TW": "沒有發現您需要避免的食材，但這只是推測結果。",
    "ja": "避けるべき食材は見つかりませんでしたが、推定の結果です。",
    "es": "No encontramos ingredientes que evites, pero es solo una estimación.",
}
# CAUTION with nothing to go on (no information, no ingredients).
NO_INFORMATION: dict[str, str] = {
    "ko": "재료 정보를 찾지 못했어요.",
    "en": "We couldn't find ingredient information for this dish.",
    "ar": "لم نجد معلومات عن مكونات هذا الطبق.",
    "zh-CN": "没有找到这道菜的食材信息。",
    "zh-TW": "沒有找到這道菜的食材資訊。",
    "ja": "この料理の食材情報が見つかりませんでした。",
    "es": "No encontramos información de los ingredientes de este plato.",
}
# CAUTION/unknown for any other gap (an unanswered flag, failed lookup, no store).
NOT_ENOUGH_INFORMATION: dict[str, str] = {
    "ko": "아직 확인되지 않은 정보가 있어요.",
    "en": "Some information about this dish isn't confirmed yet.",
    "ar": "بعض المعلومات عن هذا الطبق لم تُؤكَّد بعد.",
    "zh-CN": "这道菜还有未确认的信息。",
    "zh-TW": "這道菜還有未確認的資訊。",
    "ja": "まだ確認されていない情報があります。",
    "es": "Hay información de este plato que aún no está confirmada.",
}

# Something likely can be left out: the result screen's "빼고 요청" (Request removal)
# card. No names: "사골은 빼고 요청 카드로…" picked one ingredient out of many (2026-11-22).
REMOVE_CARD_STEP: dict[str, str] = {
    "ko": "빼고 요청 카드로 빼 달라고 부탁할 수 있어요.",
    "en": "You can ask them to leave it out with the Request removal card.",
    "ar": "يمكنك أن تطلب إزالته باستخدام بطاقة طلب الإزالة.",
    "zh-CN": "您可以用“请求去除食材”卡片请店家去掉。",
    "zh-TW": "您可以用「請求去除食材」卡片請店家去掉。",
    "ja": "「食材抜きのお願い」カードで抜いてもらうよう頼めます。",
    "es": "Puedes pedir que lo quiten con la tarjeta «Pedir sin un ingrediente».",
}
# Both cards apply: one sentence.
REMOVE_OR_QUESTION_CARD_STEP: dict[str, str] = {
    "ko": "빼고 요청 카드로 빼 달라고 부탁하거나, 질문 카드로 직원에게 직접 확인해 보세요.",
    "en": "You can ask them to leave it out with the Request removal card, or ask the staff directly with the question card.",
    "ar": "يمكنك أن تطلب إزالته باستخدام بطاقة طلب الإزالة، أو أن تسأل الموظفين مباشرة باستخدام بطاقة الأسئلة.",
    "zh-CN": "您可以用“请求去除食材”卡片请店家去掉，或用问题卡片直接向店员确认。",
    "zh-TW": "您可以用「請求去除食材」卡片請店家去掉，或用問題卡片直接向店員確認。",
    "ja": "「食材抜きのお願い」カードで抜いてもらうよう頼むか、質問カードでスタッフに直接確認してみてください。",
    "es": "Puedes pedir que lo quiten con la tarjeta «Pedir sin un ingrediente» o preguntarle directamente al personal con la tarjeta de preguntas.",
}
# Everything else: the question card (the "재료 N개 확인하기" button).
QUESTION_CARD_STEP: dict[str, str] = {
    "ko": "질문 카드로 직원에게 직접 확인해 보세요.",
    "en": "You can ask the staff directly with the question card.",
    "ar": "يمكنك أن تسأل الموظفين مباشرة باستخدام بطاقة الأسئلة.",
    "zh-CN": "您可以用问题卡片直接向店员确认。",
    "zh-TW": "您可以用問題卡片直接向店員確認。",
    "ja": "質問カードでスタッフに直接確認してみてください。",
    "es": "Puedes preguntarle directamente al personal con la tarjeta de preguntas.",
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
# Several names in one note (2026-11-17): "까나리, 다시마, 소고기는 … 빼기 어려울 수 있어요"
# instead of one note per ingredient (a vegan 비빔밥 got ten notes saying the same thing).
# Korean, Chinese and Japanese read the same with a list; these need the plural.
MAIN_INGREDIENTS: dict[str, Callable[[str], str]] = {
    "en": lambda names: f"{_capital(names)} are main ingredients of this dish, so they may be hard to leave out.",
    "ar": lambda names: f"قد يصعب طلب هذا الطبق بدون {names} لأنّ هذه المكوّنات أساسية فيه.",
    "es": lambda names: f"Puede ser difícil pedir este plato sin {names}, porque son ingredientes principales.",
}
HIDDEN_INGREDIENTS: dict[str, Callable[[str], str]] = {
    "en": lambda names: f"{_capital(names)} are inside other ingredients such as sauces or seasoning, so they may be hard to leave out.",
    "ar": lambda names: f"قد يصعب طلب هذا الطبق بدون {names} لأنّ هذه المكوّنات موجودة داخل مكوّنات أخرى مثل الصلصة أو التتبيلة.",
    "es": lambda names: f"Puede ser difícil pedir este plato sin {names}, porque van dentro de otros ingredientes, como salsas o aliños.",
}


def _capital(word: str) -> str:
    return word[:1].upper() + word[1:]


def _named(items: list[DecisionRiskIngredient]) -> list[str]:
    """Names, highest probability first. ``items`` is already one per ingredient
    (``merge_same_ingredient`` joins the rows ④ sends per tag bundle, #327)."""

    ordered = sorted(items, key=lambda i: -i.posterior_mean if i.posterior_mean is not None else 1.0)
    return [item.ingredient.localized_name for item in ordered]


def _without_carried(items: list[DecisionRiskIngredient]) -> list[DecisionRiskIngredient]:
    """Drop a hidden ingredient that only repeats one listed above it (2026-11-15).

    ④ expands 맛술 into its hidden 알코올 with 맛술's own count, so both reached the
    sentence ("みりん、アルコール"). ⑧ gets no parent link, so a hidden row is read as
    carried when a non-hidden row covers its restrictions with the same probability.
    The sentence only: the screen list and the cards keep every row.
    """

    shown = [i for i in items if i.role != "hidden"]

    def carried(item: DecisionRiskIngredient) -> bool:
        return item.role == "hidden" and item.posterior_mean is not None and any(
            other.posterior_mean is not None
            and round(other.posterior_mean, 2) == round(item.posterior_mean, 2)
            and set(item.matched_tags) <= set(other.matched_tags)
            for other in shown
        )

    return [i for i in items if not carried(i)]


def _say(template: dict[str, VerdictTemplate], names: list[str], lang: str) -> str:
    """At most ``MAX_NAMES`` names, then "등" with no count. The screen lists every
    ingredient with its own probability below the sentence (Figma S10), so the sentence
    need not repeat a long list; names come in the order given (highest probability
    first for estimates)."""

    return template[lang](*_joined(names, lang))


def _joined(names: list[str], lang: str) -> tuple[str, str]:
    """``names`` joined, at most ``MAX_NAMES`` then "등", with the last word (Korean
    particles follow it)."""

    if len(names) <= MAX_NAMES:
        return AND_LISTS[lang](names), names[-1]
    shown, more = names[:MAX_NAMES], MORE[lang]
    if lang in ("ko", "zh-CN", "zh-TW", "ja"):
        return LISTS_NO_AND[lang](shown) + more, more
    return AND_LISTS[lang]([*shown, more]), more


def card_step(remove: bool, ask: bool, lang: str) -> str:
    """What to do on the result screen, with no names (2026-11-22): whether any ingredient
    goes to the remove-request card (``remove``) or the question card (``ask``). The
    "80% 이상" sentence before it was dropped on 2026-11-23: the verdict already gives a
    percent, and two different numbers made the reason hard to read."""

    if remove and ask:
        return REMOVE_OR_QUESTION_CARD_STEP[lang]
    if remove:
        return REMOVE_CARD_STEP[lang]
    if ask:
        return QUESTION_CARD_STEP[lang]
    return ""


# Why the result is less certain, as fixed sentences (2026-11-23). These went to the LLM
# as facts to retell until then, and with nothing else to explain it tied them to other
# ingredients: a pork-allergy 새우튀김 read "가쓰오부시, 간장, … 미성분이 확인되지 않은
# 튀김가루와 빵가루는 … 돼지고기가 들어갈 수 있어요". Code adds them now; the LLM never
# sees them (reason.py ``reason_route``). ko 외 6개 언어는 원어민 확인 전 초안 (#135).
LIMIT_SENTENCES: dict[str, dict[str, str]] = {
    "few_web_recipes": {
        "ko": "이 메뉴는 웹에서 찾은 레시피가 적어서 결과가 덜 확실해요.",
        "en": "Only a few web recipes were found for this dish, so the result is less certain.",
        "ar": "لم يُعثر إلا على عدد قليل من الوصفات لهذا الطبق على الإنترنت، لذا النتيجة أقل يقينًا.",
        "zh-CN": "网上找到的这道菜的食谱很少，所以结果不太确定。",
        "zh-TW": "網路上找到的這道菜的食譜很少，所以結果不太確定。",
        "ja": "この料理のウェブレシピが少ないため、結果はあまり確かではありません。",
        "es": "Encontramos pocas recetas de este plato en internet, así que el resultado es menos seguro.",
    },
    "web_timeout": {
        "ko": "웹 검색 시간이 모자라 레시피를 일부만 읽어서 결과가 덜 확실해요.",
        "en": "The web search ran out of time and read only some recipes, so the result is less certain.",
        "ar": "انتهى وقت البحث على الإنترنت قبل قراءة كل الوصفات، لذا النتيجة أقل يقينًا.",
        "zh-CN": "网络搜索时间不够，只读了部分食谱，所以结果不太确定。",
        "zh-TW": "網路搜尋時間不夠，只讀了部分食譜，所以結果不太確定。",
        "ja": "ウェブ検索の時間が足りず一部のレシピしか読めなかったため、結果はあまり確かではありません。",
        "es": "La búsqueda en internet se quedó sin tiempo y solo leyó algunas recetas, así que el resultado es menos seguro.",
    },
    "web_read_failed": {
        "ko": "일부 웹 레시피를 끝까지 읽지 못해서 빠진 재료가 있을 수 있어요.",
        "en": "Some web recipes could not be read fully, so some ingredients may be missing.",
        "ar": "تعذّرت قراءة بعض الوصفات كاملة، لذا قد تكون بعض المكونات ناقصة.",
        "zh-CN": "部分网络食谱没能完整读取，可能漏掉了一些食材。",
        "zh-TW": "部分網路食譜沒能完整讀取，可能漏掉了一些食材。",
        "ja": "一部のウェブレシピを最後まで読めなかったため、抜けている食材があるかもしれません。",
        "es": "No pudimos leer completas algunas recetas, así que puede faltar algún ingrediente.",
    },
    "saved_info_unavailable": {
        "ko": "이 메뉴에 저장된 정보 일부를 불러오지 못했어요.",
        "en": "Some saved information about this dish could not be loaded.",
        "ar": "تعذّر تحميل بعض المعلومات المحفوظة عن هذا الطبق.",
        "zh-CN": "这道菜的部分已保存信息没能加载。",
        "zh-TW": "這道菜的部分已儲存資訊沒能載入。",
        "ja": "この料理について保存された情報の一部を読み込めませんでした。",
        "es": "No se pudo cargar parte de la información guardada sobre este plato.",
    },
    "unclear_restrictions": {
        "ko": "일부 재료는 어떤 식단 제한에 걸리는지 확인하지 못했어요.",
        "en": "For some ingredients we could not tell which dietary restrictions they affect.",
        "ar": "لم نتمكن من معرفة القيود الغذائية التي تخص بعض المكونات.",
        "zh-CN": "有些食材无法确认会涉及哪些饮食限制。",
        "zh-TW": "有些食材無法確認會涉及哪些飲食限制。",
        "ja": "一部の食材は、どの食事制限に関わるか確認できませんでした。",
        "es": "En algunos ingredientes no pudimos saber a qué restricciones afectan.",
    },
    "nested_untraced": {
        "ko": "양념이나 소스 안에 든 재료를 끝까지 확인하지 못했어요.",
        "en": "Ingredients inside sauces or seasonings could not be traced all the way.",
        "ar": "تعذّر تتبّع كل المكونات الموجودة داخل الصلصات أو التوابل.",
        "zh-CN": "酱料或调味料里的食材没能完全追查。",
        "zh-TW": "醬料或調味料裡的食材沒能完全追查。",
        "ja": "たれやソースの中の食材を最後まで確認できませんでした。",
        "es": "No pudimos rastrear del todo los ingredientes dentro de salsas o condimentos.",
    },
    "no_store": {
        "ko": "식당을 고르지 않아서 사장님 확인 없이 레시피로만 추정했어요.",
        "en": "No restaurant was chosen, so this is estimated from recipes without the owner's answers.",
        "ar": "لم يتم اختيار مطعم، لذا هذه النتيجة تقديرية من الوصفات دون إجابات صاحب المطعم.",
        "zh-CN": "没有选择餐厅，所以只根据食谱推测，没有店主的确认。",
        "zh-TW": "沒有選擇餐廳，所以只根據食譜推測，沒有店家的確認。",
        "ja": "お店が選ばれていないため、店主の確認なしでレシピだけから推定しました。",
        "es": "No se eligió un restaurante, así que es una estimación a partir de recetas, sin respuestas del dueño.",
    },
    "partial_information": {
        "ko": "재료 정보 일부를 불러오지 못했어요.",
        "en": "Part of the ingredient information could not be loaded.",
        "ar": "تعذّر تحميل جزء من معلومات المكونات.",
        "zh-CN": "部分食材信息没能加载。",
        "zh-TW": "部分食材資訊沒能載入。",
        "ja": "食材情報の一部を読み込めませんでした。",
        "es": "No se pudo cargar parte de la información de los ingredientes.",
    },
    "no_information": {
        "ko": "재료 정보를 불러오지 못했어요.",
        "en": "The ingredient information could not be loaded.",
        "ar": "تعذّر تحميل معلومات المكونات.",
        "zh-CN": "食材信息没能加载。",
        "zh-TW": "食材資訊沒能載入。",
        "ja": "食材情報を読み込めませんでした。",
        "es": "No se pudo cargar la información de los ingredientes.",
    },
}
# Limits about named ingredients. {x} is the joined names, {last} the last word.
GUESSED_FROM_MENU_NAME: dict[str, VerdictTemplate] = {
    "ko": lambda x, last: f"{x}{_ko(last, '은', '는')} 레시피가 아니라 메뉴 이름으로 추정한 재료예요.",
    "en": lambda x, last: f"We guessed {x} from the menu name, not from recipes.",
    "ar": lambda x, last: f"استنتجنا {x} من اسم الطبق وليس من الوصفات.",
    "zh-CN": lambda x, last: f"{x}是根据菜名推测的，不是根据食谱。",
    "zh-TW": lambda x, last: f"{x}是根據菜名推測的，不是根據食譜。",
    "ja": lambda x, last: f"{x}はレシピではなくメニュー名から推定した食材です。",
    "es": lambda x, last: f"Dedujimos {x} por el nombre del plato, no por recetas.",
}
NO_RECIPE_DATA: dict[str, VerdictTemplate] = {
    "ko": lambda x, last: f"{x}{_ko(last, '은', '는')} 이 메뉴의 레시피 데이터가 없어서 대략적인 기본값으로 추정했어요.",
    "en": lambda x, last: f"There is no recipe data for {x} in this dish, so its chance is only a rough default.",
    "ar": lambda x, last: f"لا توجد بيانات وصفات عن {x} في هذا الطبق، لذا الاحتمال تقديري فقط.",
    "zh-CN": lambda x, last: f"这道菜没有关于{x}的食谱数据，所以只是粗略的默认估计。",
    "zh-TW": lambda x, last: f"這道菜沒有關於{x}的食譜資料，所以只是粗略的預設估計。",
    "ja": lambda x, last: f"この料理には{x}のレシピデータがないため、大まかな既定値で推定しました。",
    "es": lambda x, last: f"No hay datos de recetas sobre {x} en este plato, así que es solo una estimación aproximada.",
}


# Most serious first. The reason shows only the first one, as its last line (2026-11-23:
# several limit sentences made the reason long and hard to read).
LIMIT_PRIORITY = (
    "no_information",
    "partial_information",
    "saved_info_unavailable",
    "unclear_restrictions",
    "nested_untraced",
    "web_read_failed",
    "web_timeout",
    "few_web_recipes",
    "guessed_from_menu_name",
    "no_recipe_data",
    "no_store",
)


def limit_sentences(codes: list[str], guessed: list[str], no_data: list[str], lang: str) -> list[str]:
    """Fixed sentences for ``codes`` (keys of ``LIMIT_SENTENCES``) and the ingredients
    guessed from the menu name or with no recipe data, most serious first
    (``LIMIT_PRIORITY``)."""

    found: dict[str, str] = {code: LIMIT_SENTENCES[code][lang] for code in codes}
    if guessed:
        found["guessed_from_menu_name"] = GUESSED_FROM_MENU_NAME[lang](*_joined(guessed, lang))
    if no_data:
        found["no_recipe_data"] = NO_RECIPE_DATA[lang](*_joined(no_data, lang))
    return [found[code] for code in LIMIT_PRIORITY if code in found]


def _sentences(parts: list[str], lang: str) -> str:
    return ("" if lang in ("zh-CN", "zh-TW", "ja") else " ").join(parts)


def owner_said_absent(request: DecisionRequest, outcome: DecisionOutcome, lang: str) -> list[str]:
    """Forbidden ingredients the owner said are not in the dish (달걀 지단 for a vegan)."""

    forbidden = set(outcome.forbidden_tags)
    localized = {n.ingredient_id: n.localized_name for n in request.localized_names}
    return list(dict.fromkeys(
        display_name(c.ingredient, localized, lang)
        for c in request.confirmed_results
        if is_hard_evidence(c) and c.status == "absent" and set(c.constraint_tags) & forbidden
    ))


def _at_least(items: list[DecisionRiskIngredient], lang: str) -> tuple[list[str], str | None]:
    """"밀이 들어갈 확률이 85% 이상이에요" from the most likely ingredient, named by what
    the user avoids in it (밀, not 라면사리), and that name. Empty when no ingredient has
    an estimate."""

    estimated = [i for i in items if i.posterior_mean is not None and i.matched_tags]
    if not estimated:
        return [], None
    top = max(estimated, key=lambda i: i.posterior_mean)
    subject = _tags(list(top.matched_tags), lang)
    if is_below_one_percent(top.posterior_mean):
        return [BELOW_ONE_PERCENT[lang](subject)], subject
    return [AT_LEAST[lang](subject, shown_percent(top.posterior_mean))], subject


def verdict(request: DecisionRequest, outcome: DecisionOutcome, lang: str) -> str:
    """Why, with no lead sentence (2026-11-24): "할랄 기준으로는 드시기 어려워요" only
    repeated what the screen already shows (the DANGER/CAUTION/SAFE badge and the user's
    diet), so the line starts with who confirmed what or how likely it is."""

    level, basis = outcome.risk_level, outcome.evidence_basis
    if level == "safe" and basis == "confirmed":
        return SAFE[lang]
    if level == "danger" and basis == "confirmed":
        present = [r for r in outcome.risk_ingredients if r.status == "present"]
        names = _named(present)
        tags = {t for r in present for t in r.matched_tags}
        danger_flags = [c for c in outcome.flag_checks if c.state == "danger"]
        # A "present" answer to a one-tag flag names that tag, not an ingredient (§2-1 5-2),
        # unless a confirmed ingredient already carries it ("새우젓" covers "새우").
        for check in danger_flags:
            if len(check.matched_tags) == 1 and not set(check.matched_tags) <= tags:
                names += [tag_name(tag, lang) for tag in check.matched_tags]
                tags |= set(check.matched_tags)
        names = list(dict.fromkeys(names))  # two flags can name the same tag
        # A several-tag flag answer says "one or more of them": say it as the owner did,
        # unless the confirmed ingredients above already cover every tag it asked about.
        one_of = [c for c in danger_flags if len(c.matched_tags) > 1 and not set(c.matched_tags) <= tags]
        parts = [_say(OWNER_SAYS_PRESENT, names, lang)] if names else []
        for check in one_of:
            place = FLAG_PLACES[check.flag]
            parts.append(OWNER_SAYS_ONE_OF[lang](place[lang] if place else None, _tags(check.matched_tags, lang)))
        if parts:
            return _sentences(parts, lang)
    absent = owner_said_absent(request, outcome, lang)
    owner = [_say(OWNER_SAYS_ABSENT, absent, lang)] if absent else []
    if level == "caution" and basis == "estimated":
        items = _without_carried(outcome.risk_ingredients)
        names = _named(items)
        # Tags matched by a menu attribute (e.g. spicy) rather than an ingredient (§2-1 5-1).
        by_ingredient = {t for r in outcome.risk_ingredients for t in r.matched_tags}
        names += [tag_name(tag, lang) for tag in outcome.matched_tags if tag not in by_ingredient]
        if names:
            at_least, subject = _at_least(items, lang)
            # "돼지고기가 들어갈 확률이 62% 이상이에요. 돼지고기가 들어 있을 수 있어요." says it
            # twice: the list goes when it only names what the percent already does.
            may_contain = [] if names == [subject] else [_say(MAY_CONTAIN, names, lang)]
            return _sentences([*owner, *at_least, *may_contain], lang)
        return _sentences([*owner, NOT_CONFIRMED[lang]], lang)
    # caution/unknown, and anything the policy does not produce: never a sentence that
    # sounds safe.
    if request.no_information or has_no_ingredients(request):
        return NO_INFORMATION[lang]
    return _sentences([*owner, NOT_ENOUGH_INFORMATION[lang]], lang)


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


def hard_to_remove_text(names: list[str], role: str, lang: str) -> str:
    """One note for every ingredient of the same role, names in the given order."""

    if len(names) == 1:
        return (MAIN_INGREDIENT if role == "identity" else HIDDEN_INGREDIENT)[lang](names[0])
    plural = MAIN_INGREDIENTS if role == "identity" else HIDDEN_INGREDIENTS
    single = MAIN_INGREDIENT if role == "identity" else HIDDEN_INGREDIENT
    return plural.get(lang, single[lang])(AND_LISTS[lang](names))


def build_message(request: DecisionRequest, outcome: DecisionOutcome) -> DecisionMessage:
    lang: Locale = request.locale
    check_tags = {check.flag: check.ask_tags for check in outcome.flag_checks}
    # ``select_notes`` picks one note per ingredient; the text joins those of the same
    # role into one sentence, placed where the first of them was (2026-11-17).
    texts: list[tuple[str, str | list[str]]] = []
    by_role: dict[str, list[str]] = {}
    for note in select_notes(outcome):
        if note.code == "hard_to_remove" and note.ingredient is not None and note.role is not None:
            if note.role not in by_role:
                by_role[note.role] = []
                texts.append((note.code, note.role))
            by_role[note.role].append(note.ingredient.localized_name)
        else:
            texts.append((note.code, note_text(note, check_tags, lang)))
    notes = [
        MessageNote(
            code=code,
            text=hard_to_remove_text(list(dict.fromkeys(by_role[body])), body, lang)
            if code == "hard_to_remove" and body in by_role
            else body,
        )
        for code, body in texts
    ]
    return DecisionMessage(verdict=verdict(request, outcome, lang), reason=None, notes=notes)


# A side item ("…사리") is something added to the main dish, not a dish of its own
# (2026-11-24, 파사리·고기사리): say so, naming what it adds. More than one name means
# "one of these, or the like" (고기사리: 소고기, 돼지고기, 닭고기 등). Korean is built
# by ``_korean_side_item``; the other languages cannot name the menu's own word.
SIDE_ITEM_SUFFIX = "사리"
SIDE_ITEM: dict[str, Callable[[str, str, bool], str]] = {
    "en": lambda menu, names, many: f"This is an add-on for the main dish: {'such as ' if many else ''}{names}.",
    "ar": lambda menu, names, many: f"هذه إضافة توضع في الطبق الرئيسي: {'مثل ' if many else ''}{names}.",
    "zh-CN": lambda menu, names, many: f"这是加进主菜里的配料：{names}{'等' if many else ''}。",
    "zh-TW": lambda menu, names, many: f"這是加進主菜裡的配料：{names}{'等' if many else ''}。",
    "ja": lambda menu, names, many: f"メイン料理に追加して食べる具材です：{names}{'など' if many else ''}。",
    "es": lambda menu, names, many: f"Es un complemento que se añade al plato principal: {'como ' if many else ''}{names}.",
}


def side_item_text(request: DecisionRequest) -> str | None:
    """The side-item sentence for a menu whose name ends in 사리, naming its own
    ingredients (not those hidden inside them). None for any other menu, with no
    ingredient, or when a name has no translation (Korean must not show elsewhere)."""

    menu = request.menu.normalized_menu_name
    if not menu.endswith(SIDE_ITEM_SUFFIX) or len(menu) <= len(SIDE_ITEM_SUFFIX):
        return None
    lang = request.locale
    localized = {n.ingredient_id: n.localized_name for n in request.localized_names}
    names = list(
        dict.fromkeys(
            display_name(item.ingredient, localized, lang)
            for item in request.probability_results
            if item.role != "hidden"
        )
    )
    if not names:
        return None
    if lang == "ko":
        return _korean_side_item(menu, names)
    if any("가" <= ch <= "힣" for name in names for ch in name):
        return None
    return SIDE_ITEM[lang](menu, AND_LISTS[lang](names), len(names) > 1)


def _korean_side_item(menu: str, names: list[str]) -> str:
    """Korean names what it adds by the menu's own word, ingredients in brackets when
    they say more (2026-11-24): "라면사리 … 라면(밀)을", "고기사리 … 고기(소고기, 돼지고기,
    닭고기 등)를", but "파사리 … 파를" (대파 only repeats 파)."""

    stem = menu[: -len(SIDE_ITEM_SUFFIX)]
    many = len(names) > 1
    detail = ""
    if many or any(stem not in name for name in names):
        detail = f"({AND_LISTS['ko'](names)}{' 등' if many else ''})"
    return f"{menu}{_ko(menu, '은', '는')} 메인 메뉴에 {stem}{detail}{_ko(stem, '을', '를')} 추가해 먹는 사리예요."


# The reason reads as plain sentences (2026-11-24): the result screen shows it in one
# paragraph, so "• 멸치액젓: 생선으로 만든 젓갈" lines ran into each other. Code turns
# each LLM phrase into "<names> is <phrase>." in the user's language.
EXPLAIN_SENTENCE: dict[str, Callable[[str, str, bool], str]] = {
    "ko": lambda names, phrase, many: (
        f"{names}{_ko(names, '은', '는')} {phrase}."
        if phrase.endswith("요")
        else f"{names}{_ko(names, '은', '는')} {phrase}{_ko(phrase, '이에요', '예요')}."
    ),
    "en": lambda names, phrase, many: f"{names[:1].upper()}{names[1:]} {'are' if many else 'is'} {phrase}.",
    "ar": lambda names, phrase, many: f"{names}: {phrase}.",
    "zh-CN": lambda names, phrase, many: f"{names}是{phrase}。",
    "zh-TW": lambda names, phrase, many: f"{names}是{phrase}。",
    "ja": lambda names, phrase, many: f"{names}は{phrase}です。",
    "es": lambda names, phrase, many: f"{names[:1].upper()}{names[1:]} {'son' if many else 'es'} {phrase}.",
}
