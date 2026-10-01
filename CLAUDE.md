# CLAUDE.md

이 파일은 팀 전원의 Claude가 동일한 기준으로 동작하도록 하는 공통 규칙입니다.
수정은 PR로만 하고, 1명 이상 리뷰 후 반영합니다. (관련 이슈: #45)

## 프로젝트

**Catoin** — 외국인 관광객이 한식당 메뉴판을 촬영하면 OCR → 재료 분석 → `DANGER` / `CAUTION` / `SAFE` 판정까지 수행하는 알레르기 안전 서비스.

- **북극성 지표: FN(false negative) 최소화, F2 score 기준**
- 판단이 애매하면 항상 안전한 쪽(더 위험하다고 보는 쪽)으로 기운다. 놓친 알레르겐 1건이 오탐 10건보다 치명적이다.

## 모듈 구조

| 경로 | 역할 |
|---|---|
| `app.py` | FastAPI 진입점. `/v1/ocr`, `/v1/ruleengine`, `/v1/result` |
| `ai_ocr/` | 메뉴판 이미지 → 메뉴명 추출 (CLOVA OCR + GPT 후처리) |
| `ai_ruleengine/` | 메뉴명 정규화·매칭, 재료 태깅, 룰 기반 위험 판정 |
| `ai_result/` | ⑧ XAI/설명 에이전트. 최종 판정과 근거 메시지 생성 |
| `ai_web_search_agent/` | ⑥ 웹서치 에이전트. DB 미등록 메뉴의 재료 후보 수집 |
| `crawling/` | 메뉴·재료 데이터 수집 및 전처리 |
| `docs/` | 에이전트 ①~⑧ 스펙, DB 스키마, 아키텍처 문서 |

## 개발 명령어

```bash
pytest -q
python -m compileall -q app.py ai_ocr ai_ruleengine ai_result
```

CI(`.github/workflows/ci.yml`)가 이 두 가지를 실행합니다. Python 3.11 기준입니다.

## 변경 금지 — 팀 확정 결정

아래는 `docs/`에서 "확정, 변경 금지"로 못박은 항목입니다. **코드나 문서에서 이와 다르게 제안하지 말 것.**

### 확률 모델
- 공식은 `α = k_count + 1`, `β = (n_total − k_count) + 1` (Beta(1,1) 라플라스 스무딩). `crawling/normalize_ingredients.py` 구현과 일치해야 한다.
- **doc2의 `α=5 / β=1` 규칙은 폐기됨.** 옛 문서를 참조하지 말 것.
- scale은 α·β를 **동일 비율로** 축소한다 (mean 보존, 분산 증가). α 단독 축소 금지.
- `variant_suggested`는 scale 0.5, `db_registered` 변형은 1.0.
- prior fallback 순서: `store → cluster → global → uninformative`. 전역 prior는 최후의 수단.

### 확정 정보 처리
- `status`는 **3값** `present` / `absent` / `unknown`. 2값으로 축약 금지.
- anomaly 판정된 재료는 override를 거부하고 **CAUTION 이상을 강제 유지**한다 (FN 최소화 우선).
- anomaly 재료도 확률 계산은 수행하되 `anomaly_locked`로 잠근다.
- 확정 정보 상속 범위는 `base_menu_id` 단일 메뉴까지. **형제 변형 제외.**

### 온톨로지
- **교차오염은 모델 범위 외.** 관측 불가능하므로 사장님 질문 경로로 위임한다.
- 변형 판별은 DB 컬럼(`base_menu_id` / `remain_token`) 우선. 파싱은 DB 부재 시에만.
- 알레르겐은 taxonomy가 아닌 별도 축(`allergen_tags`).

### DB 쓰기
- **물리 DB 쓰기는 백엔드만 수행한다.** ⑦ DB 업데이트 에이전트는 저장할 명령과 근거를 반환할 뿐이고, 권한·FK·멱등성 검증과 트랜잭션은 백엔드 몫이다.
- `ingredient_risk_scores`는 직접 UPDATE 금지. 항상 `ingredient_evidence_log` INSERT → 재계산 순서.
- 예외: `menu_ingredient_cache` 쓰기는 ④가 수행한다 (파생 캐시이므로 ⑦ 승인 대상 아님).
- 가게 검색·선택·공공데이터 매칭은 백엔드 책임. ① Supervisor는 확정된 `store_id`만 입력받는다.

## 용어

| 용어 | 뜻 |
|---|---|
| hard evidence | 사장님·사용자가 직접 확인해준 확정 정보. 확률 계산을 거치지 않고 override |
| soft evidence | 웹서치 등 미검증 정보. 관리자 컨펌 게이트를 거쳐야 DB 반영 |
| `anomaly_locked` | 통계적으로 이상한 답변으로 판정되어 override가 잠긴 상태. CAUTION 이상 강제 |
| `variant_origin` | 변형 메뉴의 출처. ⑤가 신뢰도를 차등 적용하는 근거 |
| `prior_source` | prior를 어느 단계에서 가져왔는지 (`store` / `cluster` / `global` / `uninformative`) |
| 에이전트 ①~⑧ | ① Supervisor ② 정규화 ③ Exact 피드백 ④ DB/온톨로지 ⑤ Bayesian ⑥ 웹서치 ⑦ DB 업데이트 ⑧ XAI |

## 커밋 규칙

형식: `{type}({영역}): {설명}` — 공통 변경이면 영역 생략

```
feat(ocr): add menu size option grouping
fix(ruleengine): resolve hanja misrecognition
docs: add API specification draft
```

- type: `feat` / `fix` / `docs` / `style` / `refactor` / `test` / `chore` / `hotfix`
- 소문자로 시작, 동사 원형, 현재형, 50자 이내, 마침표 없음
- **커밋 메시지에 `Co-Authored-By` 등 AI 서명 줄을 넣지 않는다**
- **커밋·푸시는 사용자가 명시적으로 요청할 때만 수행한다**

## 브랜치 규칙

> **팀 결정 대기 중.** 컨벤션 문서는 작업 브랜치를 `dev`에서 분기한다고 정하고 있으나, 실제로는 최근 작업이 모두 `main` 기준으로 진행되고 있습니다. 어느 쪽으로 통일할지 정해지면 이 절을 채웁니다. 그때까지 Claude는 브랜치 생성 전 사용자에게 분기 기준을 확인합니다.

## 이슈 / PR 규칙

- 이슈 제목: `[TYPE] 설명` — `[FEAT]` `[FIX]` `[HOTFIX]` `[CHORE]` `[DOCS]` `[REFACTOR]`
- PR 제목: `[TYPE] 설명 (#이슈번호)`
- PR 본문에 `Closes #이슈번호` 또는 `Resolves #이슈번호` 필수
- 이슈 하나 = 작업 하나
- 템플릿은 `.github/ISSUE_TEMPLATE/`, `.github/PULL_REQUEST_TEMPLATE.md` 사용

## 금지

- `.env` 파일을 읽거나 커밋하지 않는다. **실제 CLOVA OCR 시크릿과 OpenAI API 키가 들어 있다.**
- API 키·토큰·비밀번호를 코드나 문서에 하드코딩하지 않는다. `.env.example`에는 값 없이 키 이름만 둔다.
- `docs/`의 "확정, 변경 금지" 항목을 임의로 바꾸지 않는다. 변경이 필요하면 이슈로 올린다.
- 미확정 항목을 임의로 결정해서 구현하지 않는다. `docs/` 각 문서의 "미확정 항목" 절을 먼저 확인한다.

## 참고 문서

| 문서 | 내용 |
|---|---|
| `docs/catoin-multi-agent-architecture.md` | 전체 흐름도, 케이스별 시나리오, 에이전트별 역할 |
| `docs/catoin-db-schema.md` | DB 스키마 v3, 백엔드 전달용 제약사항 |
| `docs/agent-1-supervisor.md` ~ `agent-8-xai.md` | 에이전트별 입출력 스펙과 처리 로직 |

각 에이전트 문서 끝에 **"미확정 항목 (팀 확인 대기)"** 절이 있습니다. 구현 전 반드시 확인하세요.
