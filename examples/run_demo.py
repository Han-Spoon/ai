"""
한스푼 Rule Engine 시연 케이스 (5개 이상)

실행:
    python3 examples/run_demo.py
"""

import argparse
import json
import sys
from pathlib import Path

# 프로젝트 루트 및 ai_ruleengine 을 sys.path 에 추가
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "ai_ruleengine"))

from engine import analyze

# ──────────────────────────────────────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────────────────────────────────────

def _menu(name: str) -> dict:
    return {
        "menu_name_ko": name,
        "menu_name_en": None,
        "description_ko": "",
        "description_en": None,
        "price_text": None,
        "risk_level": None,
        "is_spicy": None,
        "image_url": None,
        "display_order": 1,
    }


_VERBOSE = False


def run_case(label: str, menu_name: str, profile: dict, expected_risk: str) -> None:
    result = analyze(_menu(menu_name), profile, verbose=_VERBOSE)
    risk    = result["risk_level"]
    reasons = result["risk_reasons"]
    need    = result["need_gpt"]
    spicy   = result["is_spicy"]
    reason_ko = result.get("reason_ko") or "-"

    passed = (risk == expected_risk)
    status = "PASS" if passed else "FAIL"

    print(f"[{status}] {label}")
    print(f"  메뉴        : {menu_name}")
    print(f"  프로필      : {json.dumps(profile, ensure_ascii=False)}")
    print(f"  기대 risk   : {expected_risk}")
    print(f"  실제 risk   : {risk}  (is_spicy={spicy}, need_gpt={need})")
    print(f"  hit_reasons : {reasons}")
    print(f"  reason_ko   : {reason_ko}")
    print()


# ──────────────────────────────────────────────────────────────────────────────
# 시연 케이스
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 70)
    print("한스푼 Rule Engine — 시연 케이스")
    print("=" * 70)
    print()

    # Case 1: 무슬림 + 삼겹살 → danger (is_pork 직접 히트)
    run_case(
        label        = "Case 1: 무슬림 + 삼겹살",
        menu_name    = "삼겹살",
        profile      = {"religion_type": "halal", "vegan_type": None,
                        "no_alcohol": False, "allergies": [], "is_spicy": None},
        expected_risk= "danger",
    )

    # Case 2: 비건 + 된장찌개 → caution + need_gpt (has_unclear_broth)
    #   레시피에 멸치(is_fish) 명시되어 있지만, is_fish 는 비건 forbidden 에 미포함
    #   (내부 전용 태그 → 애매함 플래그 관련성 판단에서 처리)
    run_case(
        label        = "Case 2: 비건 + 된장찌개",
        menu_name    = "된장찌개",
        profile      = {"religion_type": None, "vegan_type": "vegan",
                        "no_alcohol": False, "allergies": [], "is_spicy": None},
        expected_risk= "caution",
    )

    # Case 3: 갑각류 알레르기 + 김치찌개 → caution + need_gpt (has_unclear_jeotgal)
    #   레시피에 돼지고기만 명시, 새우/게는 없음 → 직접 히트 없음
    #   has_unclear_jeotgal 플래그가 갑각류 알레르기와 관련 → caution
    run_case(
        label        = "Case 3: 갑각류 알레르기 + 김치찌개",
        menu_name    = "김치찌개",
        profile      = {"religion_type": None, "vegan_type": None,
                        "no_alcohol": False, "allergies": ["crab", "shrimp"],
                        "is_spicy": None},
        expected_risk= "caution",
    )

    # Case 4: 매운맛 비선호 + 얼큰 김치찌개 → danger (is_spicy 플래그)
    #   "얼큰" 이 SPICY_TOKENS 에 포함 → is_spicy=True 감지
    run_case(
        label        = "Case 4: 매운맛 비선호 + 얼큰 김치찌개",
        menu_name    = "얼큰 김치찌개",
        profile      = {"religion_type": None, "vegan_type": None,
                        "no_alcohol": False, "allergies": [], "is_spicy": False},
        expected_risk= "danger",
    )

    # Case 5: CSV 에 없는 메뉴 (마라탕) → caution + need_gpt (unknown_menu)
    run_case(
        label        = "Case 5: 마라탕 (DB 미등록)",
        menu_name    = "마라탕",
        profile      = {"religion_type": None, "vegan_type": None,
                        "no_alcohol": False, "allergies": [], "is_spicy": None},
        expected_risk= "caution",
    )

    # ── 추가 케이스 ──────────────────────────────────────────────────────────

    # Case 6: 힌두교 + 갈비구이 → danger (is_beef 직접 히트)
    run_case(
        label        = "Case 6: 힌두교 + 갈비구이",
        menu_name    = "갈비구이",
        profile      = {"religion_type": "hindu", "vegan_type": None,
                        "no_alcohol": False, "allergies": [], "is_spicy": None},
        expected_risk= "danger",
    )

    # Case 7: 할랄 + 불고기 → caution + need_gpt (has_unclear_seasoning: 맛술 가능성)
    #   레시피에 청주 미포함 → 직접 is_alcohol 히트 없음
    #   has_unclear_seasoning 플래그 + is_alcohol in forbidden → caution
    run_case(
        label        = "Case 7: 할랄 + 불고기",
        menu_name    = "불고기",
        profile      = {"religion_type": "halal", "vegan_type": None,
                        "no_alcohol": False, "allergies": [], "is_spicy": None},
        expected_risk= "caution",
    )

    # Case 8: 비건 + 오리비빔밥 → danger (is_duck 히트)
    #   base=비빔밥, remain=오리 → VARIANT_INGREDIENTS["is_duck"]에서 "오리" 매칭
    run_case(
        label        = "Case 8: 비건 + 오리비빔밥",
        menu_name    = "오리비빔밥",
        profile      = {"religion_type": None, "vegan_type": "vegan",
                        "no_alcohol": False, "allergies": [], "is_spicy": None},
        expected_risk= "danger",
    )

    # Case 9: 수식어 strip 확인 — "시그니처 매콤 차돌된장찌개"
    #   시그니처·매콤 strip → "차돌된장찌개" → base=된장찌개, remain=차돌 → is_beef
    #   비건 forbidden 에 is_beef → danger
    run_case(
        label        = "Case 9: 비건 + 시그니처 매콤 차돌된장찌개",
        menu_name    = "시그니처 매콤 차돌된장찌개",
        profile      = {"religion_type": None, "vegan_type": "vegan",
                        "no_alcohol": False, "allergies": [], "is_spicy": None},
        expected_risk= "danger",
    )

    # Case 10: 제한 없는 사용자 + 삼겹살 → safe
    run_case(
        label        = "Case 10: 제한 없음 + 삼겹살",
        menu_name    = "삼겹살",
        profile      = {"religion_type": None, "vegan_type": None,
                        "no_alcohol": False, "allergies": [], "is_spicy": None},
        expected_risk= "safe",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="한스푼 Rule Engine 시연")
    parser.add_argument("--verbose", action="store_true",
                        help="각 단계별 처리 내용 출력")
    args = parser.parse_args()
    _VERBOSE = args.verbose
    main()
