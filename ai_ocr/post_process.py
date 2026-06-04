"""메뉴 분석 결과 후처리"""

import re


def create_gpt_client():
    from gpt_client import AzureGPTClient

    return AzureGPTClient()


def clean_description_ko(description: str | None) -> str:
    """한국어 설명으로 보기 어려운 OCR/GPT 조각은 제거"""
    if description is None:
        return ""

    cleaned = str(description).strip()
    if not cleaned:
        return ""

    if not re.search(r"[가-힣]", cleaned):
        return ""

    return cleaned


def post_process_menu_analyses(
    menu_analyses: list,
    enable_gpt_correction: bool = True,
) -> list:
    """
    메뉴 분석 결과 목록을 후처리

    Args:
        menu_analyses: 메뉴 분석 결과 목록
        enable_gpt_correction: GPT를 사용한 자동 수정 여부

    Returns:
        후처리된 메뉴 분석 결과 목록
    """
    if not menu_analyses:
        return menu_analyses

    for menu_analysis in menu_analyses:
        menu_analysis["description_ko"] = clean_description_ko(
            menu_analysis.get("description_ko")
        )

    if not enable_gpt_correction:
        return menu_analyses

    try:
        gpt_client = create_gpt_client()
    except Exception as e:
        print(f"[경고] GPT 클라이언트 초기화 실패 ({e}), GPT 후처리 스킵")
        return menu_analyses

    processed = []
    for menu_analysis in menu_analyses:
        try:
            corrected = gpt_client.post_process_menu_analysis(menu_analysis)
            corrected["description_ko"] = clean_description_ko(
                corrected.get("description_ko")
            )
            processed.append(corrected)
        except Exception as e:
            print(
                f"[경고] 메뉴 '{menu_analysis.get('menu_name_ko', 'N/A')}' 후처리 실패 ({e})"
            )
            processed.append(menu_analysis)

    return processed


def judge_ocr_quality(
    menus: list,
    raw_lines: list,
    enable_gpt_judgment: bool = True,
) -> dict | None:
    """
    GPT를 사용하여 OCR 품질 판단

    Args:
        menus: 파싱된 메뉴 목록
        raw_lines: OCR 원본 라인 목록
        enable_gpt_judgment: GPT를 사용한 품질 판단 여부

    Returns:
        품질 판단 결과 또는 None (판단 비활성화시)
    """
    if not enable_gpt_judgment:
        return None

    try:
        gpt_client = create_gpt_client()
        judgment = gpt_client.judge_ocr_quality(menus, raw_lines)
        return judgment
    except Exception as e:
        print(f"[경고] GPT 품질 판단 실패 ({e})")
        return None
