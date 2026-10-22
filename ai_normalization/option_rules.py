"""Conservative option extraction that never drops ingredient meaning."""

from __future__ import annotations

import re
from dataclasses import dataclass

_SERVING_RE = re.compile(r"(?:^|\s)(?P<option>\d+\s*(?:인분|인|人))\s*$", re.IGNORECASE)
_SIZE_RE = re.compile(r"(?:^|\s)(?P<option>소자|중자|대자|소|중|대|곱빼기)\s*$")

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
        match = _SERVING_RE.search(menu_text) or _SIZE_RE.search(menu_text)
        if match is None or match.start("option") == 0:
            break
        terminal_options.insert(0, re.sub(r"\s+", "", match.group("option")))
        menu_text = menu_text[: match.start()].rstrip()

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
