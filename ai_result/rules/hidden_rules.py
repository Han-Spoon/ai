from pydantic import BaseModel

from ai_result.rules.hidden_rules_data import HIDDEN_RULES


class HiddenCandidate(BaseModel):
    source: str
    hidden: list[str]
    tags: list[str]
    taxonomy: str | None = None
    flag: str | None = None
    note: str | None = None


def _match_hidden_rules(key: str) -> dict | None:
    """HIDDEN_RULES에서 완전 일치 -> 부분 일치 순으로 탐색한다."""
    if key in HIDDEN_RULES:
        return HIDDEN_RULES[key]

    for rule_key in HIDDEN_RULES:
        if rule_key in key:
            return HIDDEN_RULES[rule_key]

    return None


def _build_candidates(
    source: str,
    flags: dict,
    triggered_flags: list[str],
) -> list[HiddenCandidate]:
    candidates = []
    matched_flags = triggered_flags or list(flags.keys())

    for flag in matched_flags:
        hidden_items = flags.get(flag, [])
        if not hidden_items:
            continue

        candidates.append(
            HiddenCandidate(
                source=source,
                hidden=[item["name"] for item in hidden_items],
                tags=list(dict.fromkeys(item["tag"] for item in hidden_items if item["tag"])),
                flag=flag,
            )
        )

    return candidates


def lookup_hidden_candidates(
    menu_name: str,
    triggered_flags: list[str],
) -> list[HiddenCandidate]:
    """메뉴명 기준으로 HIDDEN_RULES를 조회한다."""
    candidates = []

    for source, flags in HIDDEN_RULES.items():
        if source not in menu_name:
            continue

        candidates.extend(_build_candidates(source, flags, triggered_flags))

    return candidates


def lookup_hidden_candidates_for_context(
    ingredients: list[str],
    triggered_flags: list[str],
) -> list[HiddenCandidate]:
    """
    레시피 재료 기준으로 HIDDEN_RULES를 조회한다.

    완전 일치 -> 부분 일치 순으로 탐색해 재료의 2차 재료 가능성까지 커버한다.
    예: "배추김치" -> 김치 hidden rule 후보까지 조회
    """
    candidates = []
    seen = set()

    for ingredient in ingredients:
        flags = _match_hidden_rules(ingredient)
        if not flags:
            continue

        key = (ingredient, str(triggered_flags))
        if key in seen:
            continue

        seen.add(key)
        candidates.extend(_build_candidates(ingredient, flags, triggered_flags))

    return candidates
