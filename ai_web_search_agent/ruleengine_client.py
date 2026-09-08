from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
RULE_DIR = BASE_DIR / "ai_ruleengine"
if str(RULE_DIR) not in sys.path:
    sys.path.insert(0, str(RULE_DIR))

from engine import analyze, analyze_all  # noqa: E402


class RuleEngineClient:
    """기존 ai_ruleengine 결과를 가져오는 얇은 어댑터."""

    def analyze_menu(self, menu_dict: dict, profile: dict, verbose: bool = False) -> dict:
        return analyze(menu_dict, profile, verbose=verbose)

    def analyze_ocr_result(self, ocr_result: dict, profile: dict, verbose: bool = False) -> dict:
        return analyze_all(ocr_result, profile, verbose=verbose)

