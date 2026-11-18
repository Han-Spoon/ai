"""Deterministic beverage vocabulary for ②-A out-of-scope filtering.

The list intentionally mixes stable beverage types with common restaurant brands.
Types cover new/local products such as ``지역명+막걸리`` without requiring every SKU.
Brand entries cover names whose beverage meaning is not visible from a type suffix.
"""

from __future__ import annotations

import re
import unicodedata


# These suffixes are safe only when they end the complete OCR item.  This keeps
# food or combo names such as "막걸리파전" and "와인소스스테이크" analyzable.
BEVERAGE_TYPE_SUFFIXES = frozenset(
    {
        # Korean alcohol types
        "소주",
        "맥주",
        "막걸리",
        "동동주",
        "탁주",
        "약주",
        "청주",
        "정종",
        "사케",
        "고량주",
        "이과두주",
        "과실주",
        "복분자주",
        "매실주",
        "오디주",
        "오미자주",
        "인삼주",
        "하이볼",
        "와인",
        "샴페인",
        "위스키",
        "보드카",
        "브랜디",
        "코냑",
        "칵테일",
        "리큐르",
        # Korean non-alcoholic types
        "콜라",
        "사이다",
        "탄산음료",
        "탄산수",
        "소다",
        "토닉워터",
        "진저에일",
        "주스",
        "쥬스",
        "에이드",
        "스무디",
        "셰이크",
        "쉐이크",
        "라떼",
        "아메리카노",
        "카푸치노",
        "에스프레소",
        "커피",
        "아이스티",
        # Common English menu labels
        "beer",
        "soju",
        "makgeolli",
        "wine",
        "champagne",
        "highball",
        "whisky",
        "whiskey",
        "vodka",
        "rum",
        "gin",
        "brandy",
        "cognac",
        "cocktail",
        "cola",
        "coke",
        "cider",
        "soda",
        "sparklingwater",
        "tonicwater",
        "juice",
        "ade",
        "smoothie",
        "shake",
        "coffee",
        "americano",
        "latte",
        "cappuccino",
        "espresso",
        "tea",
        "water",
        # Common Japanese/Chinese menu labels
        "ビール",
        "焼酎",
        "日本酒",
        "ハイボール",
        "ワイン",
        "コーラ",
        "サイダー",
        "ジュース",
        "お茶",
        "コーヒー",
        "啤酒",
        "烧酒",
        "燒酒",
        "白酒",
        "高粱酒",
        "葡萄酒",
        "米酒",
        "清酒",
        "可乐",
        "可樂",
        "汽水",
        "果汁",
        "咖啡",
        "矿泉水",
        "礦泉水",
    }
)


# Exact names are needed for brands and traditional drinks that do not expose a
# stable type suffix. Keys use ``_beverage_key`` form: NFKC, case-folded, and
# stripped of whitespace/basic punctuation.
BEVERAGE_EXACT_NAMES = frozenset(
    {
        # Generic labels and Korean traditional non-alcoholic drinks
        "음료",
        "음료수",
        "탄산",
        "생수",
        "물",
        "식혜",
        "수정과",
        "미숫가루",
        "숭늉",
        "우유",
        "두유",
        "요구르트",
        "요거트드링크",
        "녹차",
        "홍차",
        "보리차",
        "둥굴레차",
        "결명자차",
        "옥수수수염차",
        "우엉차",
        "유자차",
        "대추차",
        "생강차",
        "모과차",
        "오미자차",
        "매실차",
        "레몬차",
        "자몽차",
        "복숭아아이스티",
        # Common restaurant shorthand with the trailing 차/주 omitted. These
        # remain exact-name matches so food such as 매실장아찌 is preserved.
        "매실",
        "유자",
        "대추",
        "생강",
        "모과",
        "오미자",
        "복분자",
        # Mixed/general alcohol labels
        "술",
        "양주",
        "소맥",
        "폭탄주",
        "럼",
        # Soju and distilled-liquor brands commonly sold in Korean restaurants
        "참이슬",
        "참이슬후레쉬",
        "참이슬fresh",
        "참이슬오리지널",
        "진로",
        "진로골드",
        "일품진로",
        "일품진로oak25",
        "일품진로oak43",
        "진로1924헤리티지",
        "처음처럼",
        "순하리",
        "새로",
        "새로살구",
        "새로다래",
        "새로오미자",
        "여울",
        "좋은데이",
        "대선",
        "시원",
        "시원한청풍",
        "이제우린",
        "잎새주",
        "한라산",
        "맛있는참",
        "금복주",
        "찰랑",
        "뉴트럴",
        "청하",
        "별빛청하",
        "백화수복",
        "설중매",
        "레몬진",
        "미림",
        "마주앙",
        "스카치블루",
        # Beer brands commonly sold in Korean restaurants
        "카스",
        "카스후레쉬",
        "카스fresh",
        "카스라이트",
        "카스제로",
        "카스00",
        "카스올제로",
        "카스레몬스퀴즈",
        "카스레몬스퀴즈제로",
        "한맥",
        "오비라거",
        "필굿",
        "하이트",
        "테라",
        "켈리",
        "필라이트",
        "slight",
        "클라우드",
        "클라우드크러시",
        "크러시",
        "카프리",
        "맥스",
        "버드와이저",
        "호가든",
        "스텔라",
        "스텔라아르투아",
        "코로나",
        "코로나엑스트라",
        "구스아일랜드",
        "산토리",
        "아사히",
        "칭따오",
        "하이네켄",
        "기네스",
        "삿포로",
        "기린",
        "산미구엘",
        "1664블랑",
        "블랑",
        "레페",
        "하얼빈",
        # Traditional Korean, Chinese, and Japanese alcohol brands
        "백세주",
        "보해복분자",
        "산사춘",
        "매취순",
        "매화수",
        "화요",
        "문배주",
        "이강주",
        "한산소곡주",
        "경주법주",
        "서울장수막걸리",
        "장수막걸리",
        "느린마을막걸리",
        "지평막걸리",
        "국순당쌀막걸리",
        "가평잣막걸리",
        "우도땅콩막걸리",
        "알밤막걸리",
        "생탁",
        "연태고량",
        "연태고량주",
        "공부가주",
        "금문고량주",
        "수정방",
        "오량액",
        "오제키",
        "간바레오또상",
        "월계관",
        # Carbonated soft drinks
        "코카콜라",
        "코카콜라제로",
        "코크",
        "코크제로",
        "펩시",
        "펩시콜라",
        "펩시제로",
        "펩시제로슈거",
        "칠성사이다",
        "칠성사이다제로",
        "스프라이트",
        "스프라이트제로",
        "환타",
        "환타오렌지",
        "환타파인애플",
        "웰치스",
        "웰치스포도",
        "웰치스청포도",
        "밀키스",
        "밀키스제로",
        "미린다",
        "탐스",
        "탐스제로",
        "맥콜",
        "데미소다",
        "닥터페퍼",
        "마운틴듀",
        "트레비",
        "씨그램",
        "초정탄산수",
        # Water, sports, energy, canned coffee, and other common brands
        "삼다수",
        "아이시스",
        "아이시스80",
        "백산수",
        "평창수",
        "에비앙",
        "볼빅",
        "포카리스웨트",
        "게토레이",
        "파워에이드",
        "핫식스",
        "몬스터",
        "몬스터에너지",
        "레드불",
        "비타500",
        "박카스",
        "2%부족할때",
        "깨수깡",
        "솔의눈",
        "델몬트",
        "레쓰비",
        "칸타타",
        "조지아",
        "맥심커피",
        "티오피",
        # Common Latin-script brands/labels after key normalization
        "cocacola",
        "cocacolazero",
        "pepsi",
        "pepsizero",
        "sprite",
        "spritezero",
        "fanta",
        "welchs",
        "milkis",
        "cass",
        "cassfresh",
        "cass00",
        "terra",
        "kelly",
        "hite",
        "kloud",
        "budweiser",
        "hoegaarden",
        "heineken",
        "guinness",
        "corona",
        "tsingtao",
        "asahi",
        "sapporo",
    }
)


_KEY_PUNCTUATION_RE = re.compile(r"[\s·•._\-()\[\]{}'\"]+")
_COMBINATION_SEPARATOR_RE = re.compile(r"[+&/]")
_PACKAGING_SUFFIX_RE = re.compile(
    r"(?:(?:\d+(?:ml|l|cc))|(?:\d+)?(?:병|캔|잔|컵|팩|페트|pet|보틀|bottle)|(?:소|중|대))+$"
)
_VARIANT_SUFFIXES = (
    "nonalcoholic",
    "무알코올",
    "논알코올",
    "무알콜",
    "논알콜",
    "오리지널",
    "original",
    "드래프트",
    "draft",
    "후레쉬",
    "fresh",
    "라이트",
    "light",
    "제로슈거",
    "zerosugar",
    "제로",
    "zero",
    "세트",
    "생",
)
_VARIANT_BRANDS = frozenset(
    {
        "새로",
        "카스",
        "환타",
        "웰치스",
        "데미소다",
        "탐스",
        "밀키스",
        "펩시",
        "코카콜라",
        "칠성사이다",
        "스프라이트",
        "게토레이",
        "파워에이드",
        "핫식스",
        "몬스터",
    }
)
_BRAND_VARIANT_TOKENS = tuple(
    sorted(
        {
            "레몬스퀴즈",
            "제로슈거",
            "오리지널",
            "파인애플",
            "블루베리",
            "복숭아",
            "청포도",
            "오렌지",
            "자몽",
            "오미자",
            "후레쉬",
            "라이트",
            "에너지",
            "울트라",
            "더킹",
            "레몬",
            "라임",
            "포도",
            "파인",
            "사과",
            "망고",
            "살구",
            "다래",
            "딸기",
            "골드",
            "제로",
            "fresh",
            "light",
            "zero",
            "00",
        },
        key=len,
        reverse=True,
    )
)


def is_beverage_item(text: str) -> bool:
    """Return true only when the complete menu item is confidently a beverage.

    Mixed ``+``, ``&``, or ``/`` items are excluded only when every component is
    itself a beverage. This preserves food-bearing bundles such as ``치킨+맥주``.
    """

    key = _beverage_key(text)
    if not key:
        return False
    parts = [part for part in _COMBINATION_SEPARATOR_RE.split(key) if part]
    if len(parts) > 1:
        return all(_is_single_beverage(part) for part in parts)
    return _is_single_beverage(key)


def _is_single_beverage(key: str) -> bool:
    candidates = {key}
    stripped = _strip_packaging(key)
    candidates.add(stripped)
    for candidate in tuple(candidates):
        variant_stripped = _strip_variant_suffixes(candidate)
        candidates.add(variant_stripped)
        candidates.add(_strip_packaging(variant_stripped))
    return any(
        candidate in BEVERAGE_EXACT_NAMES
        or any(candidate.endswith(suffix) for suffix in BEVERAGE_TYPE_SUFFIXES)
        or _is_known_brand_variant(candidate)
        for candidate in candidates
        if candidate
    )


def _beverage_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return _KEY_PUNCTUATION_RE.sub("", normalized)


def _strip_packaging(key: str) -> str:
    return _PACKAGING_SUFFIX_RE.sub("", key)


def _strip_variant_suffixes(key: str) -> str:
    current = key
    changed = True
    while changed:
        changed = False
        for suffix in _VARIANT_SUFFIXES:
            if len(current) > len(suffix) and current.endswith(suffix):
                current = current[: -len(suffix)]
                changed = True
                break
    return current


def _is_known_brand_variant(key: str) -> bool:
    for brand in _VARIANT_BRANDS:
        if not key.startswith(brand) or len(key) == len(brand):
            continue
        remainder = key[len(brand) :]
        while remainder:
            token = next(
                (value for value in _BRAND_VARIANT_TOKENS if remainder.startswith(value)),
                None,
            )
            if token is None:
                break
            remainder = remainder[len(token) :]
        if not remainder:
            return True
    return False


__all__ = [
    "BEVERAGE_EXACT_NAMES",
    "BEVERAGE_TYPE_SUFFIXES",
    "is_beverage_item",
]
