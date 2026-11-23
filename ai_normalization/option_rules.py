"""Conservative option extraction that never drops ingredient meaning."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Serving and quantity options start with a digit, so they are split even when
# written without a space ("개성식손만두4개"). The lookbehind keeps a whole
# number together ("삼겹살1500g" -> "1500g", not "500g").
_SERVING_RE = re.compile(r"(?<![\d.])(?P<option>\d+\s*(?:인분|인|人))\s*$", re.IGNORECASE)
_SIZE_RE = re.compile(
    r"(?:^|\s)(?P<option>특대|소자|중자|대자|소짜|중짜|대짜|소|중|대|"
    r"小|中|大|특|보통|곱빼기|곱배기)\s*$"
)
_QUANTITY_RE = re.compile(
    r"(?<![\d.])(?P<option>\d+(?:\.\d+)?\s*(?:kg|g|그램|개|마리|접시|판|팩)"
    r"(?:\s*이상)?)\s*$",
    re.IGNORECASE,
)
_BRACKETED_TERMINAL_RE = re.compile(
    r"\s*[\(\[【]\s*(?P<option>"
    r"\d+\s*(?:인분|인|人)|"
    r"특대|소자|중자|대자|소짜|중짜|대짜|소|중|대|小|中|大|특|보통|곱빼기|곱배기|"
    r"\d+(?:\.\d+)?\s*(?:kg|g|그램|개|마리|접시|판|팩)(?:\s*이상)?"
    r")\s*[\)\]】]\s*$",
    re.IGNORECASE,
)

_OPTION_NORMALIZATIONS = {
    "小": "소",
    "中": "중",
    "大": "대",
    "소짜": "소자",
    "중짜": "중자",
    "대짜": "대자",
    "곱배기": "곱빼기",
}

_COOKING_OPTIONS = ("덜 맵게", "매운맛", "튀김")
_INGREDIENT_OPTION_PATTERNS = (
    re.compile(r"(?P<ingredient>치즈|차돌|고기|돼지|소고기|해물)\s*(?P<option>추가|교체)"),
    re.compile(r"(?<!\S)(?P<ingredient>해물)(?!\S)"),
)


@dataclass(frozen=True)
class OptionExtraction:
    menu_text: str
    options: tuple[str, ...]
    residual_tokens: tuple[str, ...]


def extract_options(text: str) -> OptionExtraction:
    """Split terminal size/serving options and observe semantic options.

    Ingredient and cooking expressions stay in ``menu_text``. Only terminal,
    standalone serving and size tokens are removed from the two menu names.
    """

    menu_text = text.strip()
    terminal_options: list[str] = []
    while menu_text:
        match = (
            _BRACKETED_TERMINAL_RE.search(menu_text)
            or _SERVING_RE.search(menu_text)
            or _SIZE_RE.search(menu_text)
            or _QUANTITY_RE.search(menu_text)
        )
        if match is None:
            break
        prefix = menu_text[: match.start()].rstrip()
        if not prefix:
            break
        option = re.sub(r"\s+", "", match.group("option"))
        option = _OPTION_NORMALIZATIONS.get(option, option)
        if option.endswith("개이상"):
            option = f"{option[:-2]} 이상"
        terminal_options.insert(0, option)
        menu_text = prefix

    observations: list[tuple[int, str, str | None]] = []
    for phrase in _COOKING_OPTIONS:
        for match in re.finditer(re.escape(phrase), menu_text):
            observations.append((match.start(), phrase, None))
    for pattern in _INGREDIENT_OPTION_PATTERNS:
        for match in pattern.finditer(menu_text):
            ingredient = match.group("ingredient")
            option_suffix = match.groupdict().get("option")
            option = f"{ingredient} {option_suffix}" if option_suffix else ingredient
            observations.append((match.start(), option, ingredient))

    observations.sort(key=lambda entry: entry[0])
    options = list(terminal_options)
    residual_tokens: list[str] = []
    for _, option, residual in observations:
        if option not in options:
            options.append(option)
        if residual is not None and residual not in residual_tokens:
            residual_tokens.append(residual)

    return OptionExtraction(
        menu_text=menu_text,
        options=tuple(options),
        residual_tokens=tuple(residual_tokens),
    )
