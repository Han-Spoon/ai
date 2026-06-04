"""
한스푼 Rule Engine - 메인 엔진 (AI2 Step 1–10 통합)

analyze(menu_dict, profile, verbose=False) → 위험도 필드가 채워진 dict 반환
analyze_all(ocr_result, profile, verbose=False) → OCR 전체 결과의 menu_analyses[] 일괄 처리
"""

from ingredient_tagger import has_unknown_remain, tag_explicit, tag_variants
from menu_matcher import find_base_menu
from modifier_strip import strip_modifiers
from profile_mapper import map_profile_to_forbidden
from reason_generator import generate_risk_reasons
from risk_judge import judge_risk

# ── ANSI 색상 (터미널 출력용) ────────────────────────────────────────────────
_C = {
    "reset":  "\033[0m",
    "bold":   "\033[1m",
    "dim":    "\033[2m",
    "red":    "\033[91m",
    "yellow": "\033[93m",
    "green":  "\033[92m",
    "cyan":   "\033[96m",
    "blue":   "\033[94m",
    "gray":   "\033[90m",
}

_RISK_COLOR = {
    "danger":  _C["red"],
    "caution": _C["yellow"],
    "safe":    _C["green"],
}


def _fmt_set(s: set) -> str:
    return "{" + ", ".join(sorted(s)) + "}" if s else "∅"


def _fmt_list(lst: list) -> str:
    return "[" + ", ".join(lst) + "]" if lst else "[]"


def _vprint(verbose: bool, line: str = "") -> None:
    if verbose:
        print(line)


def _vheader(verbose: bool, menu_name: str) -> None:
    if not verbose:
        return
    bar = "─" * 60
    print(f"\n{_C['bold']}{bar}{_C['reset']}")
    print(f"{_C['bold']}  ▶  {menu_name}{_C['reset']}")
    print(f"{_C['bold']}{bar}{_C['reset']}")


def _vstep(verbose: bool, step: str, label: str, value: str, color: str = "") -> None:
    if not verbose:
        return
    c = color or _C["cyan"]
    print(f"  {_C['dim']}{step}{_C['reset']}  {_C['bold']}{label}{_C['reset']}")
    print(f"      {c}{value}{_C['reset']}")


def _vresult(verbose: bool, risk: str, hit_tags: list, triggered_flags: list, need_gpt: bool) -> None:
    if not verbose:
        return
    color = _RISK_COLOR.get(risk, "")
    icon = {"danger": "🔴", "caution": "🟡", "safe": "🟢"}.get(risk, "⚪")
    print(f"\n  {_C['bold']}━━ 최종 판정 ━━{_C['reset']}")
    print(f"  {icon}  risk_level      : {color}{_C['bold']}{risk.upper()}{_C['reset']}")
    print(f"  ⚑   hit_tags        : {_fmt_list(hit_tags)}")
    print(f"  🚩  triggered_flags : {_fmt_list(triggered_flags)}")
    print(f"  🤖  need_gpt        : {need_gpt}")


# ──────────────────────────────────────────────────────────────────────────────


def analyze(menu_dict: dict, profile: dict, verbose: bool = False) -> dict:
    """
    단일 메뉴 분석.

    Args:
        menu_dict: OCR 결과의 menu_analyses[] 항목 하나.
        profile  : 사용자 프로필 dict.
        verbose  : True 이면 각 단계별 처리 내용을 콘솔에 출력.

    Returns:
        입력 dict에 아래 필드가 추가된 새 dict:
          is_spicy         bool
          risk_level       "danger" | "caution" | "safe"
          hit_tags         list[str]  — 직접 금지 태그 히트
          triggered_flags  list[str]  — 관련 애매함 플래그
          forbidden_tags   list[str]
          need_gpt         bool
          gpt_context      dict | None  — need_gpt=True 일 때만 채워짐
          risk_reasons     list[dict] | None  — need_gpt=False 일 때만 채워짐
    """
    menu_name: str = (menu_dict.get("menu_name_ko") or "").strip()
    result = dict(menu_dict)

    if not menu_name:
        result.update(
            risk_level="safe",
            hit_tags=[],
            triggered_flags=[],
            forbidden_tags=[],
            need_gpt=False,
            gpt_context=None,
            risk_reasons=None,
        )
        return result

    _vheader(verbose, menu_name)

    # ── Step 1: 프로필 → 금지 태그 ─────────────────────────────────────────
    forbidden_tags = map_profile_to_forbidden(profile)
    _vstep(verbose, "Step 1", "forbidden_tags", _fmt_set(forbidden_tags))

    profile_no_spicy = profile.get("no_spicy")
    if profile_no_spicy is not None:
        spicy_pref = "매운맛 비선호" if profile_no_spicy else "매운맛 선호"
        _vstep(verbose, "      ", "spicy_pref", spicy_pref)

    # ── Step 2: 수식어 제거 (메뉴명 정제용, is_spicy 판정은 OCR 값 사용) ────
    stripped_name, _spicy_from_name = strip_modifiers(menu_name)
    # OCR spicy_detector 값 우선, 없으면 수식어 감지 값으로 fallback
    ocr_is_spicy = menu_dict.get("is_spicy")
    effective_is_spicy = ocr_is_spicy if ocr_is_spicy is not None else _spicy_from_name

    if stripped_name != menu_name:
        _vstep(verbose, "Step 2", "modifier_strip",
               f'"{menu_name}" → "{stripped_name}"', color="")
    else:
        _vstep(verbose, "Step 2", "modifier_strip", f'수식어 없음 → "{stripped_name}"',
               color=_C["gray"])
    _vstep(verbose, "      ", "is_spicy (OCR)",
           f"{ocr_is_spicy}  →  effective={effective_is_spicy}",
           color=_C["yellow"] if effective_is_spicy else _C["gray"])

    # ── Step 3: 베이스 메뉴 매칭 ────────────────────────────────────────────
    base_menu, remain_tokens = find_base_menu(stripped_name)

    if base_menu is None:
        _vstep(verbose, "Step 3", "menu_match",
               f'❌ DB 미등록 메뉴 → unknown_menu', color=_C["yellow"])
        _vresult(verbose, "caution", [], [], True)
        result.update(
            is_spicy=effective_is_spicy,
            risk_level="caution",
            hit_tags=[],
            triggered_flags=[],
            forbidden_tags=sorted(forbidden_tags),
            need_gpt=True,
            gpt_context={
                "base_menu": None,
                "ingredients_explicit": [],
                "explicit_tags": [],
                "variant_tags": [],
            },
            risk_reasons=None,
        )
        return result

    ambiguity_flags = base_menu.get("ambiguity_flags", set())
    match_detail = (
        f'✓ "{base_menu["name"]}" ({base_menu["category"]})'
        f'  remain={remain_tokens}'
        f'  ambiguity={_fmt_set(ambiguity_flags)}'
    )
    _vstep(verbose, "Step 3", "menu_match", match_detail)

    # ── Step 4–5: 재료 태깅 (explicit / variant 분리) ───────────────────────
    explicit_tags = tag_explicit(base_menu)
    variant_tags_set = tag_variants(remain_tokens)
    menu_tags = explicit_tags | variant_tags_set
    need_gpt_unknown = has_unknown_remain(remain_tokens)

    _vstep(verbose, "Step 4", "base ingredients → tags",
           f"레시피 {len(base_menu['ingredients'])}개 → {_fmt_set(explicit_tags)}")
    if remain_tokens:
        unknown_flag = "  ⚠ 미확인 토큰 포함" if need_gpt_unknown else ""
        _vstep(verbose, "Step 5", "remain tokens → tags",
               f"{remain_tokens}{unknown_flag}")

    def _gpt_context() -> dict:
        return {
            "base_menu": base_menu["name"],
            "ingredients_explicit": base_menu.get("ingredients", []),
            "explicit_tags": sorted(explicit_tags),
            "variant_tags": sorted(variant_tags_set),
        }

    # ── Step 6: 금지 재료 교집합 ────────────────────────────────────────────
    hits = forbidden_tags & menu_tags
    if hits:
        hit_list = sorted(hits)
        _vstep(verbose, "Step 6", "forbidden ∩ menu_tags",
               f"🔴 HIT → {_fmt_set(hits)}", color=_C["red"])
        _vresult(verbose, "danger", hit_list, [], False)
        result.update(
            is_spicy=effective_is_spicy,
            risk_level="danger",
            hit_tags=hit_list,
            triggered_flags=[],
            forbidden_tags=sorted(forbidden_tags),
            need_gpt=False,
            gpt_context=None,
            risk_reasons=generate_risk_reasons(hit_list, profile),
        )
        return result
    _vstep(verbose, "Step 6", "forbidden ∩ menu_tags",
           "교집합 없음", color=_C["gray"])

    # ── Step 7: 매운맛 프로필 대조 (OCR is_spicy 기준) ──────────────────────
    if profile_no_spicy and effective_is_spicy:
        _vstep(verbose, "Step 7", "spicy check",
               f"🟡 매운맛 비선호 + 매운 메뉴 → CAUTION (GPT 에스컬레이션)", color=_C["yellow"])
        _vresult(verbose, "caution", ["is_spicy"], [], True)
        result.update(
            is_spicy=effective_is_spicy,
            risk_level="caution",
            hit_tags=["is_spicy"],
            triggered_flags=[],
            forbidden_tags=sorted(forbidden_tags),
            need_gpt=True,
            gpt_context=_gpt_context(),
            risk_reasons=None,
        )
        return result
    _vstep(verbose, "Step 7", "spicy check",
           "해당 없음", color=_C["gray"])

    # ── Step 8: 애매함 플래그 관련성 판단 ───────────────────────────────────
    risk_level, hit_reasons, need_gpt = judge_risk(
        menu_tags=menu_tags,
        forbidden_tags=forbidden_tags,
        ambiguity_flags=ambiguity_flags,
        is_spicy_menu=effective_is_spicy,
        profile=profile,
    )

    # hit_reasons at this point = relevant ambiguity flags (or [] for safe)
    triggered_flags = list(hit_reasons)

    if ambiguity_flags:
        from risk_judge import _relevant_ambiguity_flags
        relevant = _relevant_ambiguity_flags(ambiguity_flags, forbidden_tags, profile)
        irrelevant = ambiguity_flags - relevant
        detail_parts = []
        if relevant:
            detail_parts.append(f"관련 있음={_fmt_set(relevant)}")
        if irrelevant:
            detail_parts.append(f"{_C['gray']}무관={_fmt_set(irrelevant)}{_C['reset']}")
        _vstep(verbose, "Step 8", "ambiguity flags",
               "  ".join(detail_parts) if detail_parts else "없음")
    else:
        _vstep(verbose, "Step 8", "ambiguity flags",
               "애매함 플래그 없음", color=_C["gray"])

    # ── Step 9: 미확인 remain 보정 ──────────────────────────────────────────
    if need_gpt_unknown:
        _vstep(verbose, "Step 9", "unknown remain",
               f"⚠ {remain_tokens} 에 미확인 토큰 → need_gpt=True", color=_C["yellow"])
        need_gpt = True
        if risk_level == "safe":
            risk_level = "caution"

    _vresult(verbose, risk_level, [], triggered_flags, need_gpt)

    result.update(
        is_spicy=effective_is_spicy,
        risk_level=risk_level,
        hit_tags=[],
        triggered_flags=triggered_flags,
        forbidden_tags=sorted(forbidden_tags),
        need_gpt=need_gpt,
        gpt_context=_gpt_context() if need_gpt else None,
        risk_reasons=None,
    )
    return result


def analyze_all(ocr_result: dict, profile: dict, verbose: bool = False) -> dict:
    """
    OCR 결과 전체의 menu_analyses[] 를 일괄 처리.
    """
    result = dict(ocr_result)
    analyses = [
        analyze(menu, profile, verbose=verbose)
        for menu in ocr_result.get("menu_analyses", [])
    ]
    result["menu_analyses"] = analyses

    risky_count = sum(
        1 for m in analyses
        if m.get("risk_level") in ("danger", "caution")
    )
    session = dict(result.get("scan_session", {}))
    session["risky_menu_count"] = risky_count
    result["scan_session"] = session

    return result
