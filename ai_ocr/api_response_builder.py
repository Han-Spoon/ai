def build_api_response(final_result: dict, language_code: str = "ko", scan_id: str | None = None) -> dict:
    scan_session = final_result.get("scan_session", {})
    scan_quality = final_result.get("scan_quality", {})
    menu_analyses = final_result.get("menu_analyses", [])

    menus = [
        build_api_menu(menu_analysis, language_code, index)
        for index, menu_analysis in enumerate(menu_analyses, start=1)
    ]

    return {
        "scanId": scan_id,
        "scanStatus": scan_session.get("scan_status", "completed"),
        "scanQuality": build_api_scan_quality(scan_quality),
        "menuCount": scan_session.get("menu_count", len(menus)),
        "dangerCount": count_danger_menus(menus),
        "menus": menus,
    }


def build_api_scan_quality(scan_quality: dict) -> dict:
    return {
        "status": scan_quality.get("status"),
        "score": scan_quality.get("score"),
        "reasons": scan_quality.get("reasons", []),
        "retakeSuggestions": scan_quality.get("retake_suggestions", []),
    }


def build_api_menu(menu_analysis: dict, language_code: str, fallback_id: int) -> dict:
    risk_level = normalize_risk_level(menu_analysis.get("risk_level"))
    return {
        "id": str(menu_analysis.get("id") or menu_analysis.get("display_order") or fallback_id),
        "image": menu_analysis.get("image_url"),
        "menuNameKo": menu_analysis.get("menu_name_ko"),
        "menuName": localized_menu_name(menu_analysis, language_code),
        "description": localized_description(menu_analysis, language_code),
        "price": menu_analysis.get("price_text"),
        "riskLevel": risk_level,
        "riskReasons": localized_risk_reasons(menu_analysis, language_code),
        "isSpicy": bool(menu_analysis.get("is_spicy")),
    }


def localized_menu_name(menu_analysis: dict, language_code: str) -> str | None:
    if language_code == "en":
        return menu_analysis.get("menu_name_en") or menu_analysis.get("menu_name_ko")
    if language_code == "ar":
        return menu_analysis.get("menu_name_ar") or menu_analysis.get("menu_name_ko")
    return menu_analysis.get("menu_name_ko")


def localized_description(menu_analysis: dict, language_code: str) -> str:
    if language_code == "en":
        return menu_analysis.get("description_en") or menu_analysis.get("description_ko") or ""
    if language_code == "ar":
        return menu_analysis.get("description_ar") or menu_analysis.get("description_ko") or ""
    return menu_analysis.get("description_ko") or ""


def localized_risk_reasons(menu_analysis: dict, language_code: str) -> list[str]:
    if language_code == "en":
        return menu_analysis.get("risk_reasons_en") or menu_analysis.get("risk_reasons") or []
    if language_code == "ar":
        return menu_analysis.get("risk_reasons_ar") or menu_analysis.get("risk_reasons") or []
    return menu_analysis.get("risk_reasons_ko") or menu_analysis.get("risk_reasons") or []


def normalize_risk_level(risk_level: str | None) -> str:
    if risk_level in {"safe", "caution", "danger"}:
        return risk_level
    return "safe"


def count_danger_menus(menus: list[dict]) -> int:
    return sum(1 for menu in menus if menu.get("riskLevel") != "safe")
