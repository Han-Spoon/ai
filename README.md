# Catoin — AI

외국인 관광객이 한식당 메뉴판을 촬영하면 **OCR → 재료 분석 → `DANGER` / `CAUTION` / `SAFE` 판정**까지 수행하는 알레르기 안전 서비스의 AI 저장소입니다.

북극성 지표는 **FN(false negative) 최소화, F2 score 기준**입니다. 판단이 애매하면 항상 더 위험한 쪽으로 기울입니다 — 놓친 알레르겐 1건이 오탐 10건보다 치명적이기 때문입니다.

## 모듈

| 모듈 | 역할 | 문서 |
|---|---|---|
| `app.py` | FastAPI 진입점 (`/v1/ocr`, `/v1/ruleengine`, `/v1/result`) | — |
| `ai_ocr/` | 메뉴판 이미지 → 메뉴명 추출 (CLOVA OCR + GPT 후처리) | [README](ai_ocr/README.md) |
| `ai_ruleengine/` | 메뉴명 정규화·매칭, 재료 태깅, 룰 기반 위험 판정 | [README](ai_ruleengine/README.md) |
| `ai_result/` | 최종 판정과 근거 메시지 생성 (Decision / XAI) | [README](ai_result/README.md) |
| `ai_web_search_agent/` | DB 미등록 메뉴의 재료 후보 수집 | [README](ai_web_search_agent/README.md) |
| `crawling/` | 메뉴·재료 데이터 수집 및 전처리 | [README](crawling/README.md) |

## 문서

설계 문서는 [`docs/`](docs/README.md)에 있습니다. 읽는 순서와 문서별 설명은 그 안의 인덱스를 보세요.

처음 보신다면 이 둘부터 읽으시면 됩니다.

- [제출 PPT 기준 문서](docs/ppt-baseline.md) — 모든 문서의 기준. 여기와 다르면 틀린 것입니다
- [멀티 에이전트 아키텍처](docs/catoin-multi-agent-architecture.md) — 전체 흐름도와 에이전트별 역할

## 개발

Python 3.11 기준입니다.

```bash
pip install -r requirements-dev.txt
pytest -q
python -m compileall -q app.py ai_ocr ai_ruleengine ai_result
```

CI(`.github/workflows/ci.yml`)가 위 두 가지를 실행합니다. `main`에 머지되면 `.github/workflows/deploy-prod.yml`로 운영 배포가 나갑니다 (문서·마크다운 변경은 제외).

환경 변수는 [`.env.example`](.env.example)을 복사해 채우세요. **실제 키가 든 `.env`는 절대 커밋하지 않습니다.**

## 협업 규칙

작업 규칙은 [`CLAUDE.md`](CLAUDE.md)에 모여 있습니다 — 커밋 형식, 이슈·PR 규칙, 변경 금지 결정, 용어집. 팀 전원의 Claude가 이 파일을 읽고 동작합니다.

이슈 템플릿은 [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/), PR 템플릿은 [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md)를 사용합니다.
