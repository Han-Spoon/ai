"""Owner question text for ⑧ cards (docs/8-agent-xai.md §1-3, §4).

Cards carry Korean plus the user's language (only Korean when the user reads Korean).
Fixed templates, no LLM: ingredient names come from code values, never a translation
model (§0-2). Every language except ``ko`` is a draft that native speakers have not
checked yet (⑧ §8, #135).
"""

from __future__ import annotations

from collections.abc import Callable

from ai_supervisor.contracts.common import AmbiguityFlag, Locale

TAG_NAMES: dict[str, dict[str, str]] = {
    "is_pork": {
        "ko": "돼지고기", "en": "pork", "ar": "لحم الخنزير",
        "zh-CN": "猪肉", "zh-TW": "豬肉", "ja": "豚肉", "es": "cerdo",
    },
    "is_beef": {
        "ko": "소고기", "en": "beef", "ar": "لحم البقر",
        "zh-CN": "牛肉", "zh-TW": "牛肉", "ja": "牛肉", "es": "carne de res",
    },
    "is_chicken": {
        "ko": "닭고기", "en": "chicken", "ar": "لحم الدجاج",
        "zh-CN": "鸡肉", "zh-TW": "雞肉", "ja": "鶏肉", "es": "pollo",
    },
    "is_duck": {
        "ko": "오리고기", "en": "duck", "ar": "لحم البط",
        "zh-CN": "鸭肉", "zh-TW": "鴨肉", "ja": "鴨肉", "es": "pato",
    },
    "is_fish": {
        "ko": "생선", "en": "fish", "ar": "السمك",
        "zh-CN": "鱼", "zh-TW": "魚", "ja": "魚", "es": "pescado",
    },
    "is_shrimp": {
        "ko": "새우", "en": "shrimp", "ar": "الروبيان",
        "zh-CN": "虾", "zh-TW": "蝦", "ja": "えび", "es": "camarón",
    },
    "is_crab": {
        "ko": "게", "en": "crab", "ar": "السلطعون",
        "zh-CN": "蟹", "zh-TW": "螃蟹", "ja": "かに", "es": "cangrejo",
    },
    "is_squid": {
        "ko": "오징어", "en": "squid", "ar": "الحبار",
        "zh-CN": "鱿鱼", "zh-TW": "魷魚", "ja": "いか", "es": "calamar",
    },
    "is_shellfish": {
        "ko": "조개류", "en": "shellfish", "ar": "المحار",
        "zh-CN": "贝类", "zh-TW": "貝類", "ja": "貝類", "es": "moluscos",
    },
    "is_mackerel": {
        "ko": "고등어", "en": "mackerel", "ar": "الماكريل",
        "zh-CN": "鲭鱼", "zh-TW": "鯖魚", "ja": "さば", "es": "caballa",
    },
    "is_alcohol": {
        "ko": "술", "en": "alcohol", "ar": "الكحول",
        "zh-CN": "酒", "zh-TW": "酒", "ja": "お酒", "es": "alcohol",
    },
    "is_egg": {
        "ko": "달걀", "en": "egg", "ar": "البيض",
        "zh-CN": "鸡蛋", "zh-TW": "雞蛋", "ja": "卵", "es": "huevo",
    },
    "is_milk": {
        "ko": "우유", "en": "milk", "ar": "الحليب",
        "zh-CN": "牛奶", "zh-TW": "牛奶", "ja": "牛乳", "es": "leche",
    },
    "is_soybean": {
        "ko": "대두", "en": "soybean", "ar": "فول الصويا",
        "zh-CN": "大豆", "zh-TW": "大豆", "ja": "大豆", "es": "soja",
    },
    "is_wheat": {
        "ko": "밀", "en": "wheat", "ar": "القمح",
        "zh-CN": "小麦", "zh-TW": "小麥", "ja": "小麦", "es": "trigo",
    },
    "is_buckwheat": {
        "ko": "메밀", "en": "buckwheat", "ar": "الحنطة السوداء",
        "zh-CN": "荞麦", "zh-TW": "蕎麥", "ja": "そば", "es": "trigo sarraceno",
    },
    "is_peanut": {
        "ko": "땅콩", "en": "peanut", "ar": "الفول السوداني",
        "zh-CN": "花生", "zh-TW": "花生", "ja": "落花生", "es": "cacahuete",
    },
    "is_walnut": {
        "ko": "호두", "en": "walnut", "ar": "الجوز",
        "zh-CN": "核桃", "zh-TW": "核桃", "ja": "くるみ", "es": "nuez",
    },
    "is_pinenut": {
        "ko": "잣", "en": "pine nut", "ar": "الصنوبر",
        "zh-CN": "松子", "zh-TW": "松子", "ja": "松の実", "es": "piñones",
    },
    "is_peach": {
        "ko": "복숭아", "en": "peach", "ar": "الخوخ",
        "zh-CN": "桃子", "zh-TW": "桃子", "ja": "もも", "es": "melocotón",
    },
    "is_tomato": {
        "ko": "토마토", "en": "tomato", "ar": "الطماطم",
        "zh-CN": "番茄", "zh-TW": "番茄", "ja": "トマト", "es": "tomate",
    },
    "is_sulfite": {
        "ko": "아황산류", "en": "sulfites", "ar": "الكبريتيت",
        "zh-CN": "亚硫酸盐", "zh-TW": "亞硫酸鹽", "ja": "亜硫酸塩", "es": "sulfitos",
    },
    "is_spicy": {
        "ko": "매운 재료", "en": "spicy ingredients", "ar": "مكونات حارة",
        "zh-CN": "辣的食材", "zh-TW": "辣的食材", "ja": "辛い食材", "es": "ingredientes picantes",
    },
}

# Where in the dish each flag can hide something. None asks about the whole dish.
FLAG_PLACES: dict[AmbiguityFlag, dict[str, str] | None] = {
    "has_unclear_broth": {
        "ko": "육수", "en": "the broth", "ar": "المرق",
        "zh-CN": "汤底", "zh-TW": "湯底", "ja": "スープ", "es": "el caldo",
    },
    "has_unclear_seasoning": {
        "ko": "양념", "en": "the seasoning", "ar": "التتبيلة",
        "zh-CN": "调味料", "zh-TW": "調味料", "ja": "調味料", "es": "el aliño",
    },
    "has_unclear_jeotgal": {
        "ko": "젓갈",
        "en": "the salted seafood (jeotgal)",
        "ar": "المأكولات البحرية المملحة (جوتغال)",
        "zh-CN": "腌海鲜酱（jeotgal）",
        "zh-TW": "醃海鮮醬（jeotgal）",
        "ja": "塩辛（チョッカル）",
        "es": "el marisco salado (jeotgal)",
    },
    "has_hidden_animal": {
        "ko": "양념·소스·육수",
        "en": "the sauces, seasoning or broth",
        "ar": "الصلصات أو التتبيلة أو المرق",
        "zh-CN": "酱料、调味料或汤底",
        "zh-TW": "醬料、調味料或湯底",
        "ja": "タレ・調味料・スープ",
        "es": "las salsas, el aliño o el caldo",
    },
    "has_variant": None,
}


def _has_final_consonant(word: str) -> bool:
    last = word.rstrip()[-1:]
    if not ("가" <= last <= "힣"):
        return False
    return (ord(last) - ord("가")) % 28 != 0


def _subject(word: str) -> str:
    return word + ("이" if _has_final_consonant(word) else "가")


def _or(word: str) -> str:
    return word + ("이나" if _has_final_consonant(word) else "나")


def _ko_list(words: list[str]) -> str:
    """"돼지고기, 소고기나 술" — the caller adds the subject particle to the result."""

    if len(words) == 1:
        return words[0]
    return ", ".join(words[:-2] + [_or(words[-2])]) + " " + words[-1]


def _joined(comma: str, last: str) -> Callable[[list[str]], str]:
    def join(words: list[str]) -> str:
        if len(words) == 1:
            return words[0]
        return comma.join(words[:-1]) + last + words[-1]

    return join


# One "A, B or C" list per language. Korean picks particles from the final consonant.
LISTS: dict[str, Callable[[list[str]], str]] = {
    "ko": _ko_list,
    "en": _joined(", ", " or "),
    "ar": _joined(" أو ", " أو "),
    "zh-CN": _joined("、", "或"),
    "zh-TW": _joined("、", "或"),
    "ja": _joined("・", "・"),
    "es": _joined(", ", " o "),
}

# {x} is the ingredient or tag list, {place} is where a flag hides it.
DISH_QUESTIONS: dict[str, Callable[[str], str]] = {
    "ko": lambda x: f"이 메뉴에 {_subject(x)} 들어가나요?",
    "en": lambda x: f"Does this dish contain {x}?",
    "ar": lambda x: f"هل يوجد {x} في هذا الطبق؟",
    "zh-CN": lambda x: f"这道菜里有{x}吗？",
    "zh-TW": lambda x: f"這道菜裡有{x}嗎？",
    "ja": lambda x: f"この料理に{x}は入っていますか？",
    "es": lambda x: f"¿Este plato lleva {x}?",
}
PLACE_QUESTIONS: dict[str, Callable[[str, str], str]] = {
    "ko": lambda place, x: f"이 메뉴의 {place}에 {_subject(x)} 들어가나요?",
    "en": lambda place, x: f"Does {place} in this dish contain {x}?",
    "ar": lambda place, x: f"هل يوجد {x} في {place} في هذا الطبق؟",
    "zh-CN": lambda place, x: f"这道菜的{place}里有{x}吗？",
    "zh-TW": lambda place, x: f"這道菜的{place}裡有{x}嗎？",
    "ja": lambda place, x: f"この料理の{place}に{x}は入っていますか？",
    "es": lambda place, x: f"¿Hay {x} en {place} de este plato?",
}


def tag_name(tag: str, lang: str) -> str:
    # An unlisted tag still shows as text; a blank question is worse than a plain one.
    return TAG_NAMES.get(tag, {}).get(lang, tag.removeprefix("is_").replace("_", " "))


def _tags(tags: list[str], lang: str) -> str:
    return LISTS[lang]([tag_name(t, lang) for t in tags])


def _langs(locale: Locale) -> list[str]:
    return ["ko"] if locale == "ko" else ["ko", locale]


def ingredient_question(korean_name: str, localized_name: str, locale: Locale) -> dict[str, str]:
    names = {locale: localized_name, "ko": korean_name}
    return {lang: DISH_QUESTIONS[lang](names[lang]) for lang in _langs(locale)}


def flag_question(flag: AmbiguityFlag, tags: list[str], locale: Locale) -> dict[str, str]:
    place = FLAG_PLACES[flag]
    if place is None:
        return menu_question(tags, locale)
    return {
        lang: PLACE_QUESTIONS[lang](place[lang], _tags(tags, lang)) for lang in _langs(locale)
    }


def menu_question(tags: list[str], locale: Locale) -> dict[str, str]:
    return {lang: DISH_QUESTIONS[lang](_tags(tags, lang)) for lang in _langs(locale)}
