"""QA scenarios for the whole scan flow (menu board -> Prepare -> Judge).

Each scenario has a plain-language situation and expected result (rendered into
``docs/qa-scenarios.md`` by ``python -m tests.qa.make_doc``) and a check that runs it
through ``tests/qa/harness.py``. ``known_issue`` marks a scenario that fails today
because of an open defect; the test is a strict xfail so it turns red once fixed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ai_normalization.dictionaries import is_beverage_item
from ai_ontology.tests.seed import seed
from ai_ontology import lookup_tags

from .harness import Scan, answer_everything, prepare_only, run, run_with_web


@dataclass
class Scenario:
    id: str
    area: str
    title: str
    given: str
    expect: str
    check: Callable[[], None]
    known_issue: str | None = None


SCENARIOS: list[Scenario] = []
AREAS = {
    "A": "메뉴판 읽기 (OCR 결과 → 메뉴 이름)",
    "B": "알레르기 놓치지 않기 (DB에 있는 메뉴)",
    "C": "걸릴 게 없는 메뉴",
    "D": "사용자 조건 (종교·채식·술·매운맛)",
    "E": "사장님 답변",
    "F": "가게 없는 스캔",
    "G": "DB에 없는 메뉴 (웹 검색)",
    "H": "메뉴 후보 고르기와 변형 메뉴",
    "I": "저장 명령",
    "J": "언어와 안내 문구",
}


def scenario(area: str, title: str, given: str, expect: str, known_issue: str | None = None):
    def wrap(fn: Callable[[], None]) -> Callable[[], None]:
        n = sum(s.area == area for s in SCENARIOS) + 1
        SCENARIOS.append(Scenario(f"{area}{n:02d}", area, title, given, expect, fn, known_issue))
        return fn

    return wrap


def names(menus: list[str]) -> list[tuple[str, str]]:
    return [(p.display_name, p.normalized_menu_name) for p in prepare_only(Scan(menus)).prepared_items]


def one(menu: str, allergies: list[str] | None = None, **kw):
    return run(Scan([menu], allergies or [], **kw)).item(menu)


def flagged(item, tag: str) -> None:
    assert tag in item.matched_tags, item.matched_tags
    assert item.risk_level != "safe"
    assert item.owner_cards, "a store scan must ask the owner about a possible allergen"


# ---------------------------------------------------------------- A. 메뉴판 읽기

@scenario("A", "장식 기호와 가격을 떼어낸다", "메뉴판 줄 `■ 김치 찌개 8,000원`", "메뉴명 `김치 찌개`, 검색용 이름 `김치찌개`")
def _():
    assert names(["■ 김치 찌개 8,000원"]) == [("김치 찌개", "김치찌개")]


@scenario("A", "이름 속 글자는 지우지 않는다", "`오삼불고기 12,000원`", "`오삼불고기` (오·삼을 지우지 않음)")
def _():
    assert names(["오삼불고기 12,000원"]) == [("오삼불고기", "오삼불고기")]


@scenario("A", "인분 표기는 옵션으로 분리한다", "`된장찌개 1인`", "이름 `된장찌개`, 옵션 `1인`")
def _():
    item = prepare_only(Scan(["된장찌개 1인"])).prepared_items[0]
    assert item.normalized_menu_name == "된장찌개" and "1인" in item.options


@scenario("A", "곱빼기는 크기 옵션으로 분리한다", "`김치찌개 곱빼기`", "이름 `김치찌개`, 옵션 `곱빼기`")
def _():
    item = prepare_only(Scan(["김치찌개 곱빼기"])).prepared_items[0]
    assert item.normalized_menu_name == "김치찌개" and "곱빼기" in item.options


@scenario("A", "메뉴명 안의 '대'는 크기로 보지 않는다", "`대구탕`", "`대구탕` 그대로")
def _():
    assert names(["대구탕"]) == [("대구탕", "대구탕")]


@scenario("A", "흔한 OCR 오탈자를 고친다", "`김치찌게`", "검색용 이름 `김치찌개`")
def _():
    assert names(["김치찌게"])[0][1] == "김치찌개"


@scenario(
    "A",
    "영문 메뉴명을 한글로 바꾼다",
    "`Bulgogi`, `kimchi stew`",
    "`불고기`, `김치찌개`",
)
def _():
    assert [n for _, n in names(["Bulgogi", "kimchi stew"])] == ["불고기", "김치찌개"]


@scenario("A", "섹션 제목은 메뉴에서 뺀다", "`메뉴판`, `김치찌개`", "`김치찌개` 하나만 남음")
def _():
    assert [d for d, _ in names(["메뉴판", "김치찌개"])] == ["김치찌개"]


@scenario("A", "전화번호는 메뉴에서 뺀다", "`02-123-4567`, `비빔밥`", "`비빔밥` 하나만 남음")
def _():
    assert [d for d, _ in names(["02-123-4567", "비빔밥"])] == ["비빔밥"]


@scenario("A", "재료 추가 옵션은 이름에 남긴다", "`치즈 추가 떡볶이`", "옵션 `치즈 추가` 기록, 이름에서 치즈를 지우지 않음")
def _():
    item = prepare_only(Scan(["치즈 추가 떡볶이"])).prepared_items[0]
    assert "치즈" in item.normalized_menu_name and "치즈 추가" in item.options


@scenario("A", "메뉴판의 매운 표시를 기록한다", "고추 표시가 있는 `떡볶이`", "매운맛 관측(`is_spicy`)이 붙음")
def _():
    item = prepare_only(Scan(["떡볶이"], spicy={"떡볶이"})).prepared_items[0]
    assert [o.tag for o in item.risk_tag_observations] == ["is_spicy"]


@scenario("A", "흐린 사진은 판정으로 넘기지 않는다", "OCR 품질 `needs_retake`", "판정할 메뉴 0개 (다시 찍기 안내)")
def _():
    prep = prepare_only(Scan(["김치찌개"], scan_quality="needs_retake"))
    assert prep.scan_quality.status == "needs_retake" and not prep.prepared_items


@scenario("A", "신뢰도 낮은 사진도 판정으로 넘기지 않는다", "OCR 품질 `low_confidence`", "판정할 메뉴 0개, 품질 경고")
def _():
    prep = prepare_only(Scan(["김치찌개"], scan_quality="low_confidence"))
    assert not prep.prepared_items
    assert "scan_quality_low_confidence" in [w.code for w in prep.warnings]


@scenario("A", "메뉴판 순서를 유지한다", "`비빔밥`, `불고기`, `김치찌개` 순서", "결과도 같은 순서")
def _():
    r = run(Scan(["비빔밥", "불고기", "김치찌개"], ["is_beef"]))
    shown = {p.item_id: p.display_name for p in r.prepare.prepared_items}
    assert [shown[i.item_id] for i in r.judge.items] == ["비빔밥", "불고기", "김치찌개"]


# ------------------------------------------------- B. 알레르기 놓치지 않기 (FN)

FN_CASES = [
    ("김치찌개", "is_shrimp", "새우젓"),
    ("김치찌개", "is_pork", "돼지고기"),
    ("된장찌개", "is_soybean", "된장·두부"),
    ("된장찌개", "is_fish", "멸치 육수"),
    ("불고기", "is_beef", "소고기"),
    ("비빔밥", "is_beef", "소고기 고명"),
    ("잡채", "is_egg", "계란 지단"),
    ("떡볶이", "is_wheat", "고추장·소시지 속 밀"),
    ("파전", "is_shellfish", "조갯살"),
    ("간장게장", "is_crab", "꽃게"),
    ("새우튀김", "is_shrimp", "새우"),
    ("오징어 순대", "is_squid", "오징어"),
    ("물냉면", "is_buckwheat", "메밀면"),
    ("삼계탕", "is_chicken", "닭"),
    ("후라이드치킨", "is_milk", "우유 반죽"),
    ("붕어빵", "is_milk", "우유"),
    ("호떡", "is_peanut", "땅콩"),
    ("약과", "is_pinenut", "잣"),
    ("약과", "is_peanut", "견과류"),
    ("양념치킨", "is_tomato", "케첩"),
    ("전복죽", "is_shellfish", "전복"),
    ("깍두기", "is_shrimp", "새우젓"),
    ("순두부찌개", "is_shellfish", "바지락"),
    ("계란말이", "is_egg", "계란"),
]
TAG_KO = {
    "is_shrimp": "새우", "is_pork": "돼지고기", "is_soybean": "대두", "is_fish": "생선", "is_beef": "소고기",
    "is_egg": "계란", "is_wheat": "밀", "is_shellfish": "조개류", "is_crab": "게", "is_squid": "오징어",
    "is_buckwheat": "메밀", "is_chicken": "닭고기", "is_milk": "우유", "is_peanut": "땅콩", "is_walnut": "호두",
    "is_pinenut": "잣", "is_tomato": "토마토", "is_alcohol": "술", "is_spicy": "매운맛",
}


def _fn_case(menu: str, tag: str, why: str) -> None:
    @scenario(
        "B",
        f"{menu}의 {TAG_KO[tag]}를 놓치지 않는다",
        f"{TAG_KO[tag]} 알레르기 손님이 가게에서 `{menu}` 스캔 ({why})",
        f"주의(CAUTION) 이상, `{tag}` 표시, 사장님 질문 카드 1장 이상",
    )
    def _():
        flagged(one(menu, [tag]), tag)


for _case in FN_CASES:
    _fn_case(*_case)


@scenario(
    "B",
    "레시피에 있는 알레르겐은 전부 잡는다 (전체 일괄)",
    "음료·주류를 제외한 시드 음식 메뉴 × 레시피에 실제 들어간 알레르겐 전부를 하나씩 손님 알레르기로 넣고 스캔",
    "놓친 조합 0개 (전부 `matched_tags`에 포함)",
)
def _():
    s = seed()
    missed = []
    for menu, recipe in s.recipe.items():
        if is_beverage_item(menu):
            continue
        ids = [s.id[i] for i in recipe]
        truth = sorted({t for v in lookup_tags(ids, s.graph, "qa").tags.values() for t in v})
        if not truth:
            continue
        item = run(Scan([menu], truth)).item(menu)
        missed += [(menu, t) for t in truth if t not in item.matched_tags]
    assert not missed, missed


# ------------------------------------------------------- C. 걸릴 게 없는 메뉴

@scenario("C", "알레르겐이 없어 보여도 SAFE로 단정하지 않는다", "새우 알레르기 손님이 `꿀떡` 스캔", "주의(CAUTION, 추정), 표시된 알레르겐 없음, 질문 카드 0장")
def _():
    item = one("꿀떡", ["is_shrimp"])
    assert (item.risk_level, item.evidence_basis, item.matched_tags, item.owner_cards) == ("caution", "estimated", [], [])


@scenario("C", "상관없는 알레르기는 질문을 만들지 않는다", "돼지고기 알레르기 손님이 `호박죽` 스캔", "질문 카드 0장")
def _():
    assert one("호박죽", ["is_pork"]).owner_cards == []


@scenario("C", "제한이 없는 손님도 SAFE는 아니다", "아무 제한 없는 손님이 `꿀떡` 스캔", "주의(CAUTION), 질문 카드 0장")
def _():
    item = one("꿀떡")
    assert item.risk_level == "caution" and item.owner_cards == []


@scenario("C", "추정이라는 점을 문구로 알린다", "새우 알레르기 손님이 `꿀떡` 스캔", "안내 문구에 추정(estimate)이라는 말이 들어감")
def _():
    assert "estimate" in one("꿀떡", ["is_shrimp"]).message.verdict


@scenario("C", "레시피엔 없어도 애매한 육수는 묻는다", "게 알레르기 손님이 `삼계탕` 스캔 (육수가 애매한 메뉴)", "육수 질문 카드 1장 (`has_unclear_broth`)")
def _():
    cards = one("삼계탕", ["is_crab"]).owner_cards
    assert [c.ambiguity_flag for c in cards] == ["has_unclear_broth"]


# --------------------------------------------------------- D. 사용자 조건

PROFILE_CASES = [
    ("할랄 손님의 돼지고기", "할랄 손님이 `김치찌개` 스캔", {"religion": "halal"}, "김치찌개", "is_pork"),
    ("할랄 손님의 술", "할랄 손님이 `보쌈` 스캔 (맛술)", {"religion": "halal"}, "보쌈", "is_alcohol"),
    ("코셔 손님의 새우", "코셔 손님이 `깍두기` 스캔 (새우젓)", {"religion": "kosher"}, "깍두기", "is_shrimp"),
    ("힌두 손님의 소고기", "힌두 손님이 `불고기` 스캔", {"religion": "hindu"}, "불고기", "is_beef"),
    ("비건 손님의 계란", "비건 손님이 `계란말이` 스캔", {"vegetarian": "vegan"}, "계란말이", "is_egg"),
    ("락토 채식 손님의 계란", "락토(우유는 먹음) 손님이 `붕어빵` 스캔", {"vegetarian": "lacto"}, "붕어빵", "is_egg"),
    ("오보 채식 손님의 우유", "오보(계란은 먹음) 손님이 `붕어빵` 스캔", {"vegetarian": "ovo"}, "붕어빵", "is_milk"),
    ("술 안 마시는 손님의 맛술", "술 제한 손님이 `갈비찜` 스캔", {"no_alcohol": True}, "갈비찜", "is_alcohol"),
]


def _profile_case(title: str, given: str, kw: dict, menu: str, tag: str) -> None:
    @scenario("D", f"{title}를 잡는다", given, f"`{tag}` 표시, 주의(CAUTION) 이상")
    def _():
        flagged(one(menu, **kw), tag)


for _case in PROFILE_CASES:
    _profile_case(*_case)


@scenario("D", "락토 채식 손님에게 우유는 문제 삼지 않는다", "락토 손님이 `붕어빵` 스캔", "`is_milk`는 표시 안 됨")
def _():
    assert "is_milk" not in one("붕어빵", vegetarian="lacto").matched_tags


@scenario("D", "페스코 채식 손님에게 생선은 문제 삼지 않는다", "페스코(생선은 먹음) 손님이 `생선전` 스캔", "`is_fish`는 표시 안 됨")
def _():
    assert "is_fish" not in one("생선전", vegetarian="pesco").matched_tags


@scenario("D", "매운 것 못 먹는 손님에게 매운 표시를 알린다", "매운맛 제한 손님, 고추 표시가 있는 `떡볶이`", "`is_spicy` 표시, 질문 카드 1장 이상")
def _():
    flagged(one("떡볶이", no_spicy=True, spicy={"떡볶이"}), "is_spicy")


@scenario("D", "알레르기 여러 개를 한 번에 본다", "계란·밀·새우 알레르기 손님이 `김치전` 스캔", "세 가지 모두 표시")
def _():
    item = one("김치전", ["is_egg", "is_wheat", "is_shrimp"])
    assert {"is_egg", "is_wheat", "is_shrimp"} <= set(item.matched_tags)


# --------------------------------------------------------------- E. 사장님 답변

@scenario("E", "사장님이 '들어간다'고 하면 바로 위험", "돼지고기 알레르기, 사장님 답변: 돼지고기 있음", "위험(DANGER), 근거는 확정(confirmed)")
def _():
    item = one("김치찌개", ["is_pork"], owner={"김치찌개": {"돼지고기": "present"}})
    assert (item.risk_level, item.evidence_basis) == ("danger", "confirmed")


@scenario("E", "위험 문구에 사장님 확인이라고 밝힌다", "위 E01과 같은 상황", "안내 문구에 'owner confirmed'")
def _():
    item = one("김치찌개", ["is_pork"], owner={"김치찌개": {"돼지고기": "present"}})
    assert "owner confirmed" in item.message.verdict


@scenario("E", "'없다'고 답한 재료는 다시 묻지 않는다", "사장님 답변: 돼지고기 없음", "돼지고기 질문 카드가 사라짐 (1회만 묻기)")
def _():
    s = seed()
    item = one("김치찌개", ["is_pork"], owner={"김치찌개": {"돼지고기": "absent"}})
    assert all(c.ingredient is None or c.ingredient.ingredient_id != s.id["돼지고기"] for c in item.owner_cards)


@scenario("E", "'없다'는 답이 확률보다 우선한다", "사장님 답변: 돼지고기 없음 (레시피 확률은 높음)", "돼지고기가 위험 재료 목록에서 빠짐")
def _():
    item = one("김치찌개", ["is_pork"], owner={"김치찌개": {"돼지고기": "absent"}})
    assert "돼지고기" not in [r.ingredient.canonical_name for r in item.risk_ingredients]


@scenario("E", "다른 재료가 남아 있으면 여전히 주의", "돼지고기만 '없음', 햄·베이컨 등은 미확인", "주의(CAUTION), 남은 재료 질문 유지")
def _():
    item = one("김치찌개", ["is_pork"], owner={"김치찌개": {"돼지고기": "absent"}})
    assert item.risk_level == "caution" and item.owner_cards


@scenario("E", "'모르겠다'는 답은 확정으로 쓰지 않는다", "사장님 답변: 돼지고기 모름(unknown)", "주의(CAUTION), 질문 카드 유지")
def _():
    item = one("김치찌개", ["is_pork"], owner={"김치찌개": {"돼지고기": "unknown"}})
    assert item.risk_level == "caution" and item.owner_cards


@scenario("E", "모든 재료가 '없음'이면 SAFE", "첫 스캔 후 사장님이 저장된 재료 전부와 애매한 항목에 '없음'으로 답하고 다시 스캔", "안전(SAFE), 확정, 질문 0장, 사장님 답 경로(`exact_only`)")
def _():
    item = answer_everything(Scan(["김치찌개"], ["is_pork"]), "김치찌개").item("김치찌개")
    assert (item.risk_level, item.evidence_basis, item.route, item.owner_cards) == ("safe", "confirmed", "exact_only", [])


@scenario("E", "다 답했어도 하나가 '있음'이면 위험", "전부 답했는데 돼지고기만 '있음'", "위험(DANGER)")
def _():
    item = answer_everything(Scan(["김치찌개"], ["is_pork"]), "김치찌개", {"돼지고기"}).item("김치찌개")
    assert item.risk_level == "danger"


@scenario("E", "알레르기와 상관없는 재료가 '있음'이면 SAFE 유지", "새우 알레르기, 전부 답했고 돼지고기만 '있음'", "안전(SAFE)")
def _():
    item = answer_everything(Scan(["김치찌개"], ["is_shrimp"]), "김치찌개", {"돼지고기"}).item("김치찌개")
    assert item.risk_level == "safe"


@scenario("E", "다 답해도 매운 표시가 있으면 SAFE 아님", "매운맛 제한, 고추 표시, 재료는 전부 '없음'", "주의(CAUTION)")
def _():
    scan = Scan(["김치찌개"], ["is_pork"], no_spicy=True, spicy={"김치찌개"})
    assert answer_everything(scan, "김치찌개").item("김치찌개").risk_level == "caution"


@scenario("E", "저장된 재료 목록이 없으면 답이 있어도 SAFE 아님", "재료 하나에 답이 있지만 가게 재료 캐시가 없음", "주의(CAUTION), 일반 경로로 다시 계산")
def _():
    item = one("꿀떡", owner={"꿀떡": {"참기름": "absent"}})
    assert item.risk_level == "caution" and item.route != "exact_only"


@scenario("E", "애매한 육수 질문에 '있음'이면 위험", "조개류 알레르기, `김치찌개` 육수 질문에 사장님 '있음'", "위험(DANGER)")
def _():
    item = one("김치찌개", ["is_shellfish"], owner_flags={"김치찌개": {"has_unclear_broth": (["is_shellfish"], "present")}})
    assert item.risk_level == "danger"


# ------------------------------------------------------------ F. 가게 없는 스캔

@scenario("F", "가게 없는 스캔은 사장님께 묻지 않는다", "가게 선택 없이(`store_id: null`) `김치찌개` 스캔", "질문 카드 0장")
def _():
    assert one("김치찌개", ["is_pork"], store_id=None).owner_cards == []


@scenario("F", "가게 없는 스캔은 아무것도 저장하지 않는다", "가게 없이 메뉴 3개 스캔", "저장 명령 0개")
def _():
    assert run(Scan(["김치찌개", "불고기", "외계인스튜"], ["is_pork"], store_id=None)).commands() == []


@scenario("F", "가게 없는 스캔은 SAFE가 나오지 않는다", "가게 없이 걸릴 게 없는 `꿀떡` 스캔", "주의(CAUTION)")
def _():
    assert one("꿀떡", ["is_shrimp"], store_id=None).risk_level == "caution"


@scenario("F", "가게 없어도 알레르겐 표시는 그대로", "가게 없이 돼지고기 알레르기 손님이 `김치찌개` 스캔", "`is_pork` 표시")
def _():
    assert "is_pork" in one("김치찌개", ["is_pork"], store_id=None).matched_tags


@scenario("F", "가게 정보가 없다는 경고를 남긴다", "가게 없이 스캔", "경고 `store_context_missing`")
def _():
    assert "store_context_missing" in [w.code for w in one("김치찌개", ["is_pork"], store_id=None).warnings]


# --------------------------------------------------------- G. DB에 없는 메뉴

ALIEN = "외계인스튜"


@scenario("G", "DB에도 웹에도 없으면 정보 없음", f"`{ALIEN}` 스캔, 웹 검색 결과 없음", "주의(CAUTION), 근거 없음(unknown), 경로 `no_information`")
def _():
    item = one(ALIEN, ["is_egg"])
    assert (item.route, item.risk_level, item.evidence_basis) == ("no_information", "caution", "unknown")


@scenario("G", "정보 없는 메뉴는 화면용 카드 1장만", "위 G01과 같은 상황", "메뉴 단위 카드 1장, 저장 명령 0개")
def _():
    r = run(Scan([ALIEN], ["is_egg"]))
    assert len(r.item(ALIEN).owner_cards) == 1 and r.commands() == []


@scenario("G", "DB에 없는 메뉴는 웹을 찾는다", f"`{ALIEN}` 스캔", "웹 검색 1번 호출")
def _():
    _, web = run_with_web(Scan([ALIEN], ["is_egg"]))
    assert web.calls == [ALIEN]


@scenario("G", "DB에 있는 메뉴는 웹을 찾지 않는다", "`김치찌개` 스캔", "웹 검색 0번")
def _():
    _, web = run_with_web(Scan(["김치찌개"], ["is_pork"]))
    assert web.calls == []


MARA = {"마라탕": [("마라탕", ["땅콩", "소고기", "배추"]), ("마라탕 만들기", ["땅콩", "두부", "청경채"]), ("집 마라탕", ["소고기", "당면", "땅콩"])]}


@scenario("G", "웹 레시피의 알레르겐을 잡는다", "땅콩 알레르기, `마라탕` 웹 레시피 3개에 땅콩", "경로 `web_bayes`, `is_peanut` 표시, 질문 카드")
def _():
    item = one("마라탕", ["is_peanut"], web=MARA)
    assert item.route == "web_bayes"
    flagged(item, "is_peanut")


@scenario("G", "웹 레시피는 자동 등록 (검토 요청 없음)", "정상적인 `마라탕` 레시피 3개", "웹 캐시 저장 명령 있음, 운영자 검토 요청 없음")
def _():
    r = run(Scan(["마라탕"], ["is_peanut"], web=MARA))
    assert r.commands("save_web_search_cache") and not r.commands("create_review_item")


@scenario("G", "다른 요리 레시피는 운영자 검토로 보낸다", "`마라탕`을 찾았는데 짜장면 레시피만 나옴", "운영자 검토 요청 1개")
def _():
    r = run(Scan(["마라탕"], ["is_peanut"], web={"마라탕": [("짜장면 레시피", ["춘장", "밀가루"])]}))
    assert len(r.commands("create_review_item")) == 1


@scenario("G", "모르는 재료가 섞여도 아는 알레르겐은 잡는다", f"`{ALIEN}` 레시피에 `우주버섯`(DB에 없음)과 달걀", "`is_egg` 표시, 주의(CAUTION)")
def _():
    web = {ALIEN: [(ALIEN, ["달걀", "우주버섯", "소금"])]}
    flagged(one(ALIEN, ["is_egg"], web=web), "is_egg")


@scenario("G", "레시피가 적으면 부족하다고 표시한다", f"`{ALIEN}` 레시피 1개", "경고 `insufficient_sources`")
def _():
    item = one(ALIEN, ["is_egg"], web={ALIEN: [(ALIEN, ["달걀"])]})
    assert "insufficient_sources" in [w.code for w in item.warnings]


@scenario("G", "가게 없는 스캔도 웹 알레르겐은 잡는다", "가게 없이 `마라탕` 스캔", "`is_peanut` 표시, 질문·저장 0개")
def _():
    r = run(Scan(["마라탕"], ["is_peanut"], web=MARA, store_id=None))
    item = r.item("마라탕")
    assert "is_peanut" in item.matched_tags and not item.owner_cards and not r.commands()


# ------------------------------------------------ H. 후보 고르기와 변형 메뉴

@scenario("H", "OCR 교정 후보는 바로 고른다", "`김치찌게`, 후보 `김치찌개`(0.95, OCR 교정)", "`김치찌개`로 판정, `is_pork` 표시")
def _():
    item = one("김치찌게", ["is_pork"], candidates={"김치찌게": [("김치찌개", 0.95, "ocr_correction")]})
    assert item.normalization_status == "normalized"
    flagged(item, "is_pork")


@scenario("H", "비슷한 후보 두 개면 고르지 않는다", "후보 `김치찌개` 0.99, `김치전` 0.95 (차이 작음)", "후보 미확정, 주의(CAUTION)")
def _():
    item = one("김치찌개", ["is_pork"], candidates={"김치찌개": [("김치찌개", 0.99, "canonical"), ("김치전", 0.95, "fuzzy")]})
    assert item.normalization_status == "ambiguous" and item.risk_level == "caution"


@scenario("H", "점수가 낮은 후보는 쓰지 않는다", "후보 하나, 점수 0.6", "후보 없음으로 처리, 웹 검색으로 넘어감")
def _():
    item = one("김치찌개", ["is_pork"], candidates={"김치찌개": [("김치찌개", 0.6, "fuzzy")]})
    assert item.normalization_status == "unmatched"


@scenario("H", "변형 메뉴의 추가 재료를 잡는다", "소고기 알레르기, `차돌된장찌개`, 후보 `된장찌개`", "경로 `db_variant_bayes`, `is_beef` 표시 (차돌 → 소고기)")
def _():
    item = one("차돌된장찌개", ["is_beef"], candidates={"차돌된장찌개": [("된장찌개", 0.95, "fuzzy")]})
    assert item.route == "db_variant_bayes"
    flagged(item, "is_beef")


@scenario("H", "해물 변형의 새우를 잡는다", "새우 알레르기, `해물파전`, 후보 `파전`", "`is_shrimp` 표시")
def _():
    flagged(one("해물파전", ["is_shrimp"], candidates={"해물파전": [("파전", 0.95, "fuzzy")]}), "is_shrimp")


@scenario("H", "치즈 변형의 우유를 잡는다", "우유 알레르기, `치즈떡볶이`, 후보 `떡볶이`", "`is_milk` 표시")
def _():
    flagged(one("치즈떡볶이", ["is_milk"], candidates={"치즈떡볶이": [("떡볶이", 0.95, "fuzzy")]}), "is_milk")


@scenario("H", "변형 메뉴는 운영자 검토 요청을 남긴다", "가게에서 `차돌된장찌개` 스캔", "검토 요청(`variant_suggestion`) 1개")
def _():
    r = run(Scan(["차돌된장찌개"], ["is_beef"], candidates={"차돌된장찌개": [("된장찌개", 0.95, "fuzzy")]}))
    reviews = r.commands("create_review_item")
    assert len(reviews) == 1 and reviews[0].payload["review_origin"] == "variant_suggestion"


# --------------------------------------------------------------- I. 저장 명령

@scenario("I", "처음 스캔하면 재료 목록을 캐시로 저장한다", "가게에서 `김치찌개` 첫 스캔", "재료 캐시 저장 명령 1개")
def _():
    assert len(run(Scan(["김치찌개"], ["is_pork"])).commands("save_menu_ingredient_cache")) == 1


@scenario("I", "캐시가 있으면 다시 저장하지 않는다", "저장된 재료 캐시가 있는 상태에서 다시 스캔", "재료 캐시 저장 명령 0개")
def _():
    first = run(Scan(["김치찌개"], ["is_pork"]))
    snapshot = first.commands("save_menu_ingredient_cache")[0].payload["ingredients_snapshot"]
    again = run(Scan(["김치찌개"], ["is_pork"], cache={"김치찌개": snapshot}))
    assert again.commands("save_menu_ingredient_cache") == []


@scenario("I", "질문 카드마다 저장 요청이 하나씩", "가게에서 `김치찌개` 스캔", "질문 저장 명령 수 = 질문 카드 수")
def _():
    r = run(Scan(["김치찌개"], ["is_pork"]))
    assert len(r.commands("create_owner_verification_request")) == len(r.item("김치찌개").owner_cards)


@scenario("I", "같은 스캔을 다시 보내도 같은 명령", "같은 요청으로 Judge 2번", "저장 명령의 멱등 키가 모두 같음 (중복 저장 없음)")
def _():
    scan = Scan(["김치찌개", "마라탕"], ["is_pork", "is_peanut"], web=MARA)
    keys = [sorted(c.idempotency_key for c in run(scan).commands()) for _ in range(2)]
    assert keys[0] == keys[1]


@scenario(
    "I",
    "같은 메뉴가 메뉴판에 두 번 있어도 멈추지 않는다",
    "한 메뉴판에 `김치찌개`와 `김치찌개 곱빼기`",
    "두 메뉴 모두 판정, 재료 캐시 저장 1개",
    known_issue="⓪이 같은 캐시 키의 명령 두 개를 합치지 못하고 에러를 냄 (state.merge_commands). 문서 ⓪ §4는 키 기준 중복 제거",
)
def _():
    r = run(Scan(["김치찌개", "김치찌개 곱빼기"], ["is_pork"]))
    assert len(r.judge.items) == 2 and len(r.commands("save_menu_ingredient_cache")) == 1


# ---------------------------------------------------------- J. 언어와 안내 문구

@scenario("J", "영어 사용자에게 영어로 안내한다", "locale `en`, 돼지고기 알레르기, `김치찌개`", "문구에 'pork'")
def _():
    assert "pork" in one("김치찌개", ["is_pork"]).message.verdict


@scenario("J", "일본어 사용자에게 일본어로 안내한다", "locale `ja`", "문구에 '豚肉'")
def _():
    assert "豚肉" in one("김치찌개", ["is_pork"], locale="ja").message.verdict


@scenario("J", "질문 카드는 한국어와 손님 언어 두 가지", "locale `en`", "모든 카드에 `ko`, `en` 문구")
def _():
    assert all(set(c.question) == {"ko", "en"} for c in one("김치찌개", ["is_pork"]).owner_cards)


@scenario("J", "한국어 사용자면 질문 카드는 한국어만", "locale `ko`", "모든 카드에 `ko` 문구만")
def _():
    assert all(set(c.question) == {"ko"} for c in one("김치찌개", ["is_pork"], locale="ko").owner_cards)


@scenario("J", "모든 경로에서 안내 문구가 비지 않는다", "DB 메뉴, 웹 메뉴, 정보 없는 메뉴를 한 번에 스캔", "세 메뉴 모두 안내 문구 있음")
def _():
    r = run(Scan(["김치찌개", "마라탕", ALIEN], ["is_pork"], web=MARA))
    assert all(i.message.verdict.strip() for i in r.judge.items)


assert len(SCENARIOS) == 100, len(SCENARIOS)
