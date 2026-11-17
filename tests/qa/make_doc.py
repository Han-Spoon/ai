"""Write docs/qa-scenarios.md from tests/qa/scenarios.py: python -m tests.qa.make_doc"""

from pathlib import Path

from .scenarios import AREAS, SCENARIOS

OUT = Path(__file__).resolve().parents[2] / "docs" / "qa-scenarios.md"


def render() -> str:
    lines = [
        "# QA 시나리오 100개",
        "",
        "메뉴판 사진(OCR 결과)부터 최종 판정·사장님 질문·저장 명령까지 서비스 전체 흐름을 확인하는 시나리오입니다.",
        "",
        "- **자동 실행**: `pytest -q tests/qa` (CI에서도 같이 돕니다). 시나리오 원본은 `tests/qa/scenarios.py`이고 이 문서는 `python -m tests.qa.make_doc`로 다시 만듭니다. 손으로 고치지 마세요.",
        "- **실행 조건**: 실제 시드 데이터(`crawling/db_seed.py`, 백엔드가 적재하는 것과 같은 행)를 쓰고, CLOVA·웹 검색·LLM은 부르지 않습니다. OCR 결과와 웹 레시피는 시나리오가 직접 넣고, LLM 자리는 규칙 기반으로 동작합니다.",
        "- **메뉴 후보 조회**: 백엔드 검색 대신 이름이 정확히 같은 메뉴만 후보로 넣습니다. 오탈자·변형 메뉴 후보는 H 영역처럼 시나리오가 직접 줍니다.",
        "- **결과 칸**: ✅ 통과 / ⚠️ 알려진 결함 (지금은 실패, 고치면 테스트가 알려줌)",
        "",
        "## 실제로 확인하지 못한 것",
        "",
        "- 실제 사진에서 CLOVA OCR이 글자를 읽는 정확도",
        "- 백엔드의 메뉴 검색(유사어·오탈자 후보 점수), DB 적재, 저장 명령 실행",
        "- 실제 웹 검색 품질과 LLM(메뉴 후보 선택, 이유 문구, 고위험 재검증)이 켜졌을 때의 결과",
        "",
        "## 영역별 개수",
        "",
        "| 영역 | 내용 | 개수 |",
        "|---|---|---|",
    ]
    for key, name in AREAS.items():
        lines.append(f"| {key} | {name} | {sum(s.area == key for s in SCENARIOS)} |")
    for key, name in AREAS.items():
        lines += ["", f"## {key}. {name}", "", "| 번호 | 확인할 것 | 상황 | 기대 결과 | 결과 |", "|---|---|---|---|---|"]
        for s in SCENARIOS:
            if s.area != key:
                continue
            mark = f"⚠️ {s.known_issue}" if s.known_issue else "✅"
            cells = [s.id, s.title, s.given, s.expect, mark]
            lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    return "\n".join(lines) + "\n"


def doc_is_current() -> bool:
    return OUT.exists() and OUT.read_text(encoding="utf-8") == render()


if __name__ == "__main__":
    OUT.write_text(render(), encoding="utf-8")
    print(f"wrote {OUT}")
