import json
import os
import re


class GPTConfigError(RuntimeError):
    pass


class AzureGPTClient:
    """Azure OpenAI GPT-4o-mini를 사용한 후처리 및 판단 클라이언트"""

    def __init__(self):
        self._load_dotenv_if_available()

        try:
            from openai import AzureOpenAI
        except ImportError as error:
            raise GPTConfigError(
                "GPT 후처리를 사용하려면 openai 패키지를 설치해주세요. "
                "예: pip install -r requirements.txt"
            ) from error

        self.endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        self.key = os.getenv("AZURE_OPENAI_KEY") or os.getenv("AZURE_OPENAI_API_KEY")
        self.deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
        self.api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-01-preview")

        if not self.endpoint or not self.key:
            raise GPTConfigError(
                ".env에 AZURE_OPENAI_ENDPOINT와 AZURE_OPENAI_KEY를 설정해주세요."
            )

        self.client = AzureOpenAI(
            api_key=self.key,
            api_version=self.api_version,
            azure_endpoint=self.endpoint,
        )

    def _load_dotenv_if_available(self):
        try:
            from dotenv import load_dotenv
        except ImportError:
            return

        load_dotenv()

    def judge_ocr_quality(self, menus: list, raw_lines: list) -> dict:
        """OCR 인식이 제대로 되었는지 판단"""
        if not menus or not raw_lines:
            return {
                "is_reliable": False,
                "confidence": 0.0,
                "issues": ["메뉴 데이터가 없습니다."],
                "suggestions": [],
            }

        menu_text = self._format_menus_for_judgment(menus)
        raw_text = "\n".join([line.get("text", "") for line in raw_lines])

        prompt = f"""다음 OCR 인식 결과를 평가해주세요.

[OCR 원본 텍스트]
{raw_text}

[파싱된 메뉴 데이터]
{menu_text}

평가 기준:
1. 메뉴명이 정상적으로 인식되었는가?
2. 가격이 올바르게 추출되었는가?
3. 설명 텍스트가 이상한 문자(오류 문자)를 포함하고 있는가?
4. 메뉴 개수가 합리적인가?

JSON 형식으로 다음을 반환해주세요:
{{
    "is_reliable": boolean,
    "confidence": 0.0~1.0 사이의 숫자,
    "issues": ["문제점1", "문제점2"],
    "suggestions": ["개선방안1", "개선방안2"]
}}
"""

        response = self.client.chat.completions.create(
            model=self.deployment,
            messages=[
                {
                    "role": "system",
                    "content": "당신은 OCR 결과를 평가하는 전문가입니다.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=500,
        )

        result_text = response.choices[0].message.content
        return self._parse_json_response(result_text)

    def post_process_menu_analysis(self, menu_analysis: dict) -> dict:
        """메뉴 분석 결과를 후처리 (오류 수정)"""
        menu_name_ko = menu_analysis.get("menu_name_ko", "")
        description_ko = menu_analysis.get("description_ko", "")

        if not self._needs_post_processing(menu_name_ko, description_ko):
            menu_analysis["correction_applied"] = False
            return menu_analysis

        prompt = f"""다음 메뉴 정보를 검토하고 필요시 수정해주세요.

메뉴명: {menu_name_ko}
설명: {description_ko}
가격: {menu_analysis.get('price_text', '')}

수정이 필요한 경우:
- 이상한 문자(예: 網, 특수문자) 제거
- 오타 수정
- 불필요한 공백 제거
- 원문 근거 없이 메뉴명을 새 메뉴로 바꾸지 않기
- 확실하지 않은 설명은 빈 문자열로 정리하기

JSON 형식으로 다음을 반환해주세요:
{{
    "menu_name_ko": "수정된 메뉴명",
    "description_ko": "수정된 설명",
    "needs_correction": boolean,
    "correction_reason": "수정 사유"
}}
"""

        response = self.client.chat.completions.create(
            model=self.deployment,
            messages=[
                {
                    "role": "system",
                    "content": "당신은 한국어 메뉴판 OCR 결과를 검수하는 전문가입니다.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=300,
        )

        result_text = response.choices[0].message.content
        correction_result = self._parse_json_response(result_text)

        if correction_result.get("needs_correction"):
            menu_analysis["menu_name_ko"] = correction_result.get(
                "menu_name_ko", menu_name_ko
            )
            menu_analysis["description_ko"] = correction_result.get(
                "description_ko", description_ko
            )
            menu_analysis["correction_applied"] = True
            menu_analysis["correction_reason"] = correction_result.get(
                "correction_reason"
            )
        else:
            menu_analysis["correction_applied"] = False

        return menu_analysis

    def _needs_post_processing(self, menu_name: str, description: str) -> bool:
        """후처리가 필요한지 판단"""
        suspicious_patterns = ["網", "  ", "!!", "##", "�"]
        suspicious_chars = ["︀", "︁", "‌", "‍", "‎", "‏"]

        combined = f"{menu_name} {description}"

        for pattern in suspicious_patterns:
            if pattern in combined:
                return True

        for char in suspicious_chars:
            if char in combined:
                return True

        if len(menu_name.strip()) == 0 or len(menu_name) > 50:
            return True

        if description and "\n" in description:
            return True

        if self._has_suspicious_symbol_noise(combined):
            return True

        if self._looks_like_orphan_ocr_fragment(description):
            return True

        return False

    def _has_suspicious_symbol_noise(self, text: str) -> bool:
        if not text:
            return False

        symbol_count = len(re.findall(r"[^\w\s가-힣ㄱ-ㅎㅏ-ㅣ.,()/+-]", text))
        return symbol_count >= 1

    def _looks_like_orphan_ocr_fragment(self, text: str) -> bool:
        if not text:
            return False

        stripped = text.strip()
        if not stripped:
            return False

        has_korean = bool(re.search(r"[가-힣]", stripped))
        has_digit = bool(re.search(r"\d", stripped))
        latin_letters = re.findall(r"[A-Za-z]", stripped)

        return not has_korean and not has_digit and 0 < len(latin_letters) <= 3

    def _format_menus_for_judgment(self, menus: list) -> str:
        """메뉴 목록을 판단용 텍스트로 포맷"""
        formatted = []
        for i, menu in enumerate(menus, start=1):
            menu_name = (
                menu.get("matchedMenu")
                or menu.get("normalizedCandidate")
                or menu.get("rawName", "")
            )
            description = menu.get("description", "")
            price = menu.get("priceRaw") or menu.get("price", "")
            formatted.append(f"{i}. {menu_name} - {description} ({price}원)")

        return "\n".join(formatted)

    def _parse_json_response(self, response_text: str) -> dict:
        """GPT 응답에서 JSON 파싱"""
        try:
            json_str = response_text.strip()
            if json_str.startswith("```json"):
                json_str = json_str[7:]
            if json_str.startswith("```"):
                json_str = json_str[3:]
            if json_str.endswith("```"):
                json_str = json_str[:-3]

            return json.loads(json_str.strip())
        except json.JSONDecodeError:
            return {
                "error": "응답 파싱 실패",
                "raw_response": response_text,
            }
