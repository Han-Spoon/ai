from pydantic import BaseModel

from ai_result.rules.hidden_rules_data import HIDDEN_RULES


class HiddenCandidate(BaseModel):
    source: str
    hidden: list[str]
    tags: list[str]
    taxonomy: str | None = None
    flag: str | None = None
    note: str | None = None


def lookup_hidden_candidates(
    menu_name: str,
    triggered_flags: list[str],
) -> list[HiddenCandidate]:
    candidates = []
    for source, flags in HIDDEN_RULES.items():
        if source not in menu_name:
            continue
        matched_flags = triggered_flags or list(flags.keys())
        for flag in matched_flags:
            hidden_items = flags.get(flag, [])
            if not hidden_items:
                continue
            candidates.append(
                HiddenCandidate(
                    source=source,
                    hidden=[item["name"] for item in hidden_items],
                    tags=list(dict.fromkeys(item["tag"] for item in hidden_items)),
                    taxonomy=None,
                    flag=flag,
                    note=None,
                )
            )
    return candidates


def lookup_hidden_candidates_for_context(
    ingredients: list[str],
    triggered_flags: list[str],
) -> list[HiddenCandidate]:
    candidates = []
    for ingredient in ingredients:
        flags = HIDDEN_RULES.get(ingredient)
        if not flags:
            continue
        matched_flags = triggered_flags or list(flags.keys())
        for flag in matched_flags:
            hidden_items = flags.get(flag, [])
            if not hidden_items:
                continue
            candidates.append(
                HiddenCandidate(
                    source=ingredient,
                    hidden=[item["name"] for item in hidden_items],
                    tags=list(dict.fromkeys(item["tag"] for item in hidden_items)),
                    taxonomy=None,
                    flag=flag,
                    note=None,
                )
            )
    return candidates
