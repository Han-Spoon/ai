"""⑧ result message: verdict line and notes in the user's language (docs/8-agent-xai.md §4, §4-1).

Fixed templates, no LLM. Ingredient names come from ``localized_name`` (Korean when
there is no translation); nothing here translates or guesses. The verdict line follows
the result screen, but first says whether the user can eat the dish (2026-11-22), then
who confirmed what and what is still unchecked, with no percentages (the screen shows
them per ingredient). ``reason`` is the
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
# "and N more". Korean, Chinese and Japanese attach it to the list without "and";
# the Korean particle then follows "개".
MORE: dict[str, Callable[[int], str]] = {
    "ko": lambda n: f" 외 {n}개",
    "en": lambda n: f"{n} more",
    "ar": lambda n: f"{n} من المكونات الأخرى",
    "zh-CN": lambda n: f"及其他{n}种",
    "zh-TW": lambda n: f"及其他{n}種",
    "ja": lambda n: f"ほか{n}種類",
    "es": lambda n: f"{n} más",
}
LISTS_NO_AND: dict[str, Callable[[list[str]], str]] = {
    "ko": ", ".join,
    "zh-CN": "、".join,
    "zh-TW": "、".join,
    "ja": "、".join,
}


def _ko(word: str, with_final: str, without: str) -> str:
    return with_final if _has_final_consonant(word) else without


# The verdict line follows the result screen (front Figma S10, 2026-11-06): whether the
# user can eat it and for which restriction (first, 2026-11-22), who confirmed what, and
# what is still unchecked.
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

# The first sentence says whether the user can eat the dish (2026-11-22 feedback: the
# user wants to know at once if they can eat it, then why). {r} names the restrictions
# the dish goes against; the plain versions are for when there is none to name.
CANNOT_EAT_FOR: dict[str, Callable[[str], str]] = {
    "ko": lambda r: f"{r} 기준으로는 드시기 어려워요.",
    "en": lambda r: f"This dish isn't suitable for your dietary needs ({r}).",
    "ar": lambda r: f"هذا الطبق غير مناسب لاحتياجاتك الغذائية ({r}).",
    "zh-CN": lambda r: f"这道菜不符合您的饮食限制（{r}），不宜食用。",
    "zh-TW": lambda r: f"這道菜不符合您的飲食限制（{r}），不宜食用。",
    "ja": lambda r: f"食事制限（{r}）があるため、この料理は食べられません。",
    "es": lambda r: f"Este plato no es apto para tus restricciones ({r}).",
}
CANNOT_EAT: dict[str, str] = {
    "ko": "드시기 어려워요.",
    "en": "This dish isn't suitable for you.",
    "ar": "هذا الطبق غير مناسب لك.",
    "zh-CN": "这道菜不适合您食用。",
    "zh-TW": "這道菜不適合您食用。",
    "ja": "この料理は食べられません。",
    "es": "Este plato no es apto para ti.",
}
CHECK_BEFORE_EATING_FOR: dict[str, Callable[[str], str]] = {
    "ko": lambda r: f"{r} 기준으로는 드시기 전에 직원 확인이 필요해요.",
    "en": lambda r: f"With your dietary needs ({r}), please check with the staff before eating this dish.",
    "ar": lambda r: f"بسبب احتياجاتك الغذائية ({r})، يُرجى التأكد من الموظفين قبل تناول هذا الطبق.",
    "zh-CN": lambda r: f"根据您的饮食限制（{r}），食用前请先向店员确认。",
    "zh-TW": lambda r: f"依您的飲食限制（{r}），食用前請先向店員確認。",
    "ja": lambda r: f"食事制限（{r}）があるため、食べる前にスタッフに確認してください。",
    "es": lambda r: f"Por tus restricciones ({r}), consulta con el personal antes de comer este plato.",
}
CHECK_BEFORE_EATING: dict[str, str] = {
    "ko": "드시기 전에 직원 확인이 필요해요.",
    "en": "Please check with the staff before eating this dish.",
    "ar": "يُرجى التأكد من الموظفين قبل تناول هذا الطبق.",
    "zh-CN": "食用前请先向店员确认。",
    "zh-TW": "食用前請先向店員確認。",
    "ja": "食べる前にスタッフに確認してください。",
    "es": "Consulta con el personal antes de comer este plato.",
}
CAN_EAT_FOR: dict[str, Callable[[str], str]] = {
    "ko": lambda r: f"{r} 기준으로 드실 수 있어요.",
    "en": lambda r: f"This dish fits your dietary needs ({r}).",
    "ar": lambda r: f"هذا الطبق يناسب احتياجاتك الغذائية ({r}).",
    "zh-CN": lambda r: f"这道菜符合您的饮食限制（{r}），可以食用。",
    "zh-TW": lambda r: f"這道菜符合您的飲食限制（{r}），可以食用。",
    "ja": lambda r: f"この料理は食事制限（{r}）に合っていて、食べられます。",
    "es": lambda r: f"Este plato es apto para tus restricciones ({r}).",
}
CAN_EAT: dict[str, str] = {
    "ko": "드실 수 있어요.",
    "en": "You can eat this dish.",
    "ar": "يمكنك تناول هذا الطبق.",
    "zh-CN": "这道菜可以食用。",
    "zh-TW": "這道菜可以食用。",
    "ja": "この料理は食べられます。",
    "es": "Puedes comer este plato.",
}

# SAFE: what the owner's answers showed, after CAN_EAT(_FOR).
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

# After the LLM's reason (2026-11-22): fixed sentences the LLM kept getting wrong (it
# wrote "80% 이상" for 어묵 at 52%, and picked the wrong card). {x} names, {last} the last
# word, {p} the threshold ("80%"). "Hanspoon" stays in Latin letters like the app name.
VERY_OFTEN: dict[str, Callable[[str, str, str, str], str]] = {
    # Korean names the dish ("한스푼이 수집한 부대찌개 레시피의 80%", 2026-11-22); other
    # languages say "this dish", as the reason does.
    "ko": lambda x, last, p, menu: f"{x}{_ko(last, '은', '는')} 한스푼이 수집한 {menu} 레시피의 {p} 이상에 들어가는 재료라서 특히 주의해야 해요.",
    "en": lambda x, last, p, menu: f"At least {p} of the recipes Hanspoon collected for this dish use {x}, so please be especially careful.",
    "ar": lambda x, last, p, menu: f"تستخدم {p} على الأقل من الوصفات التي جمعها Hanspoon لهذا الطبق {x}، لذا انتبه جيدًا.",
    "zh-CN": lambda x, last, p, menu: f"在Hanspoon收集的这道菜的食谱中，至少{p}用到{x}，请特别注意。",
    "zh-TW": lambda x, last, p, menu: f"在Hanspoon收集的這道菜的食譜中，至少{p}用到{x}，請特別注意。",
    "ja": lambda x, last, p, menu: f"Hanspoonが集めたこの料理のレシピの{p}以上に{x}が使われているため、特に注意してください。",
    "es": lambda x, last, p, menu: f"Al menos el {p} de las recetas que recopiló Hanspoon para este plato llevan {x}, así que ten especial cuidado.",
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
    """At most ``MAX_NAMES`` names, the rest counted ("외 6개"). The screen lists every
    ingredient with its own probability below the sentence (Figma S10), so the sentence
    need not repeat a long list; names come in the order given (highest probability
    first for estimates)."""

    return template[lang](*_joined(names, lang))


def _joined(names: list[str], lang: str) -> tuple[str, str]:
    """``names`` joined, at most ``MAX_NAMES`` and the rest counted, with the last word
    (Korean particles follow it)."""

    if len(names) <= MAX_NAMES:
        return AND_LISTS[lang](names), names[-1]
    shown, rest = names[:MAX_NAMES], len(names) - MAX_NAMES
    more = MORE[lang](rest)
    if lang in ("ko", "zh-CN", "zh-TW", "ja"):
        return LISTS_NO_AND[lang](shown) + more, more
    return AND_LISTS[lang]([*shown, more]), more


def reason_tail(very_often: list[str], remove: bool, ask: bool, percent: str, menu: str, lang: str) -> str:
    """The fixed sentences after the LLM's reason (2026-11-22): the ingredients most
    recipes use (names), then what to do on the result screen, with no names: whether
    any ingredient goes to the remove-request card (``remove``) or the question card
    (``ask``)."""

    parts = []
    if very_often:
        parts.append(VERY_OFTEN[lang](*_joined(very_often, lang), percent, menu))
    if remove and ask:
        parts.append(REMOVE_OR_QUESTION_CARD_STEP[lang])
    elif remove:
        parts.append(REMOVE_CARD_STEP[lang])
    elif ask:
        parts.append(QUESTION_CARD_STEP[lang])
    return _sentences(parts, lang)


def _sentences(parts: list[str], lang: str) -> str:
    return ("" if lang in ("zh-CN", "zh-TW", "ja") else " ").join(parts)


def restriction_names(profile: UserProfile, lang: str, tags: set[str] | None = None) -> str:
    """The user's restrictions by name; with ``tags``, only those that forbid one of them
    ("비건" for 어묵, "새우 알레르기" for 새우)."""

    return RESTRICTION_SEPARATOR[lang].join(restriction_list(profile, lang, tags))


def restriction_list(
    profile: UserProfile, lang: str, tags: set[str] | None = None, *, allergies: bool = True
) -> list[str]:
    """``restriction_names`` as a list, for the reason LLM (2026-11-22); without
    ``allergies``, only diets, religion, alcohol and spicy."""

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
    for tag in profile.allergies if allergies else ():
        add(frozenset({tag}), ALLERGY[lang].format(x=tag_name(tag, lang)))
    return list(dict.fromkeys(names))


def owner_said_absent(request: DecisionRequest, outcome: DecisionOutcome, lang: str) -> list[str]:
    """Forbidden ingredients the owner said are not in the dish (달걀 지단 for a vegan)."""

    forbidden = set(outcome.forbidden_tags)
    localized = {n.ingredient_id: n.localized_name for n in request.localized_names}
    return list(dict.fromkeys(
        display_name(c.ingredient, localized, lang)
        for c in request.confirmed_results
        if is_hard_evidence(c) and c.status == "absent" and set(c.constraint_tags) & forbidden
    ))


def _lead(table_for: dict[str, Callable[[str], str]], table: dict[str, str], restrictions: str, lang: str) -> str:
    return table_for[lang](restrictions) if restrictions else table[lang]


def verdict(request: DecisionRequest, outcome: DecisionOutcome, lang: str) -> str:
    """Whether the user can eat it first, then why (2026-11-22)."""

    level, basis = outcome.risk_level, outcome.evidence_basis
    profile = request.user_profile
    if level == "safe" and basis == "confirmed":
        return _sentences([_lead(CAN_EAT_FOR, CAN_EAT, restriction_names(profile, lang), lang), SAFE[lang]], lang)
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
            # Restrictions it surely breaks; with only "or" answers, any of them may be the one.
            if not tags:
                tags = {t for c in one_of for t in c.matched_tags}
            restrictions = restriction_names(profile, lang, tags)
            return _sentences([_lead(CANNOT_EAT_FOR, CANNOT_EAT, restrictions, lang), *parts], lang)
    absent = owner_said_absent(request, outcome, lang)
    owner = [_say(OWNER_SAYS_ABSENT, absent, lang)] if absent else []
    if level == "caution" and basis == "estimated":
        items = _without_carried(outcome.risk_ingredients)
        names = _named(items)
        # Tags matched by a menu attribute (e.g. spicy) rather than an ingredient (§2-1 5-1).
        by_ingredient = {t for r in outcome.risk_ingredients for t in r.matched_tags}
        names += [tag_name(tag, lang) for tag in outcome.matched_tags if tag not in by_ingredient]
        if names:
            tags = by_ingredient | set(outcome.matched_tags)
            restrictions = restriction_names(profile, lang, tags)
            lead = _lead(CHECK_BEFORE_EATING_FOR, CHECK_BEFORE_EATING, restrictions, lang)
            return _sentences([lead, *owner, _say(MAY_CONTAIN, names, lang)], lang)
        return _sentences([CHECK_BEFORE_EATING[lang], *owner, NOT_CONFIRMED[lang]], lang)
    # caution/unknown, and anything the policy does not produce, asks the user to check
    # with the staff: never a sentence that sounds safe.
    if request.no_information or has_no_ingredients(request):
        return _sentences([CHECK_BEFORE_EATING[lang], NO_INFORMATION[lang]], lang)
    return _sentences([CHECK_BEFORE_EATING[lang], *owner, NOT_ENOUGH_INFORMATION[lang]], lang)


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
