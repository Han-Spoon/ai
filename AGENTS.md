# AGENTS.md

이 파일은 팀이 쓰는 AI 코딩 에이전트가 **도구와 무관하게 동일한 기준으로 동작하도록** 하는 공통 규칙입니다. **규칙 본문은 이 파일 하나에만 둡니다.**

| 도구 | 이 규칙을 어떻게 읽는가 |
|---|---|
| Codex | `AGENTS.md`를 자동으로 읽습니다 |
| Claude Code | `CLAUDE.md`를 읽고, 그 파일이 이 파일을 가져옵니다 |

도구별 설정(권한, 커밋 서명 등)은 각 도구의 자리에 둡니다 — 예: `.claude/settings.json`. **팀 규칙은 거기 적지 않습니다.** 한 도구에만 적힌 규칙은 다른 도구를 쓰는 팀원에게 적용되지 않기 때문입니다.

수정은 PR로만 하고, 1명 이상 리뷰 후 반영합니다. (관련 이슈: #45)

## 프로젝트

**Caution** — 외국인 관광객이 한식당 메뉴판을 촬영하면 OCR → 재료 분석 → `DANGER` / `CAUTION` / `SAFE` 판정까지 수행하는 알레르기 안전 서비스.

- **북극성 지표: FN(false negative) 최소화, F2 score 기준**
- 판단이 애매하면 항상 안전한 쪽(더 위험하다고 보는 쪽)으로 기운다. 놓친 알레르겐 1건이 오탐 10건보다 치명적이다.

## 모듈 구조

| 경로 | 역할 |
|---|---|
| [`app.py`](app.py) | FastAPI 진입점. `/v1/ocr`, `/v1/ruleengine`, `/v1/result` |
| [`ai_ocr/`](ai_ocr/README.md) | 메뉴판 이미지 → 메뉴명 추출 (CLOVA OCR + GPT 후처리) |
| [`ai_ruleengine/`](ai_ruleengine/README.md) | 메뉴명 정규화·매칭, 재료 태깅, 룰 기반 위험 판정 |
| [`ai_result/`](ai_result/README.md) | ⑧ Decision Policy / XAI Agent. 최종 판정과 근거 메시지 생성 |
| [`ai_web_search_agent/`](ai_web_search_agent/README.md) | ⑥ Web Search Agent. DB 미등록 메뉴의 재료 후보 수집 |
| [`crawling/`](crawling/README.md) | 메뉴·재료 데이터 수집 및 전처리 |
| [`docs/`](docs/README.md) | 제출 PPT 기준 문서, 아키텍처, DB 스키마, Agent·Tool 스펙. 인덱스는 `docs/README.md` |

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
- **교차오염은 다루지 않는다**
- 변형 판별은 DB 컬럼(`base_menu_id` / `remain_token`) 우선. 파싱은 DB 부재 시에만.
- 알레르겐은 taxonomy가 아닌 별도 축(`allergen_tags`).

### DB 쓰기
- **물리 DB 쓰기는 백엔드만 수행한다.** ⑦ DB Update Tool은 저장할 명령과 근거를 반환할 뿐이고, 권한·FK·멱등성 검증과 트랜잭션은 백엔드 몫이다.
- `ingredient_risk_scores`는 직접 UPDATE 금지. 항상 `ingredient_evidence_log` INSERT → 재계산 순서.
- 예외: `menu_ingredient_cache` 쓰기는 ④가 수행한다 (파생 캐시이므로 ⑦ 승인 대상 아님).
- 가게 검색·선택·공공데이터 매칭은 백엔드 책임. ⓪ Supervisor Agent는 확정된 `store_id`만 입력받는다.

## 용어

| 용어 | 뜻 |
|---|---|
| hard evidence | 사장님이 직접 확인해준 확정 정보. 확률 계산을 거치지 않고 override |
| soft evidence | 웹서치 등 미검증 정보. 관리자 컨펌 게이트를 거쳐야 DB 반영 |
| `anomaly_locked` | 통계적으로 이상한 답변으로 판정되어 override가 잠긴 상태. CAUTION 이상 강제 |
| `variant_origin` | 변형 메뉴의 출처. ⑤가 신뢰도를 차등 적용하는 근거 |
| `prior_source` | prior를 어느 단계에서 가져왔는지 (`store` / `cluster` / `global` / `uninformative`) |
| Agent / Tool | **기준 문서(PPT) 표기**를 따른다. ⓪ Supervisor Agent · ① OCR Tool · ② Menu Normalization Agent · ③ Exact Feedback Tool · ④ DB / Ontology Tool · ⑤ Bayesian Tool · ⑥ Web Search Agent · ⑦ DB Update Tool · ⑧ Decision Policy / XAI Agent · ⑨ Curation Tool |
| Agent vs Tool | **Agent는 판단하고 Tool은 수행한다.** Agent = LLM 추론이 필요한 판단 노드, Tool = 정해진 절차를 수행하는 실행 노드 |

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

**작업 브랜치는 `main`에서 분기한다.** `dev`는 사용하지 않는다 (10/2 회의 결정).

```
{type}/{설명}            docs/claude-md-sync
{type}/#{이슈번호}-{설명}   feat/#56-menu-normalization
```

- type은 커밋 규칙과 동일: `feat` / `fix` / `docs` / `style` / `refactor` / `test` / `chore` / `hotfix`
- 소문자, 단어는 하이픈으로 연결
- **`main` 직접 푸시 금지.** 모든 변경은 PR로 들어온다

### 머지

**사람이 직접 머지한다.** 자동 머지는 쓰지 않는다.

- CI(`AI CI` 워크플로의 `test` 잡)가 통과해야 머지 버튼이 열린다. 빨간불이면 머지할 수 없다
- 머지 방식은 merge commit (기존 히스토리와 동일)

> 참고: `.github/workflows/deploy-prod.yml`이 `main` push에서 돌기 때문에 **`main` 머지가 곧 배포**입니다 (단, `**.md`·`docs/**`·`images/**` 변경은 제외).

## 이슈 / PR 규칙

- 이슈 제목: `[TYPE] 설명` — `[FEAT]` `[FIX]` `[HOTFIX]` `[CHORE]` `[DOCS]` `[REFACTOR]`
- PR 제목: `[TYPE] 설명 (#이슈번호)`
- PR 본문에 이슈 연결 필수
  - 이슈를 **완전히 끝내는** PR → `Closes #이슈번호` / `Resolves #이슈번호`
  - 이슈의 **일부만 다루는** PR → `Part of #이슈번호` (closing keyword 금지). 상위 이슈가 조기에 닫히는 것을 막는다
- 이슈 하나 = 작업 하나
- 템플릿은 `.github/ISSUE_TEMPLATE/`, `.github/PULL_REQUEST_TEMPLATE.md` 사용

### 이슈·PR은 알아듣기 쉽게 쓴다

읽는 사람은 이 작업의 맥락을 모르는 팀원이다. 코드 내부 용어를 아는 사람만 이해할 글은 쓰지 않는다.

- **첫 문장에 "무엇이 문제이고 / 무엇을 하려는지"를 평문으로 쓴다.** 필드명·함수명으로 시작하지 않는다.
- **사용자나 팀에게 어떤 영향이 있는지** 한 줄로 적는다. (예: "이대로면 anomaly 재료가 SAFE로 판정될 수 있다")
- 코드 용어(`anomaly_locked`, `prior_source` 등)는 처음 나올 때 **한 줄로 뜻을 풀어쓴다.** 용어 표는 위 "용어" 절을 가리켜도 된다.
- 해결 방법이 정해지지 않았으면 단정하지 말고 **"정해야 할 것"** 으로 적는다. 미확정 항목을 이슈 본문에서 결정해 버리지 않는다.
- 완료 조건은 **체크리스트로, 확인 가능한 문장**으로 쓴다. ("개선한다" ✗ → "`status` 3값이 ③→④ 출력까지 유지된다" ○)

## 금지

- `.env` 파일을 읽거나 커밋하지 않는다. **실제 CLOVA OCR 시크릿과 OpenAI API 키가 들어 있다.**
- API 키·토큰·비밀번호를 코드나 문서에 하드코딩하지 않는다. `.env.example`에는 값 없이 키 이름만 둔다.
- `docs/`의 "확정, 변경 금지" 항목을 임의로 바꾸지 않는다. 변경이 필요하면 이슈로 올린다.
- `docs/ppt-baseline.md`(제출 PPT 기준 문서)는 수정하지 않는다. 제출본을 그대로 옮긴 기록이며, 다른 문서가 이 내용과 다르면 다른 문서를 고친다.
- 미확정 항목을 임의로 결정해서 구현하지 않는다. `docs/` 각 문서의 "미확정 항목" 절을 먼저 확인한다.

## 참고 문서

| 문서 | 내용 |
|---|---|
| [`docs/ppt-baseline.md`](docs/ppt-baseline.md) | **제출 PPT 기준 문서(AI 파트 7~10쪽).** 이미 제출된 내용이라 수정 대상이 아니며, 다른 문서·코드와 충돌하면 이 문서가 우선한다 |
| [`docs/caution-multi-agent-architecture.md`](docs/caution-multi-agent-architecture.md) | 전체 흐름도, 케이스별 시나리오, Agent·Tool별 역할 |
| [`docs/caution-db-schema.md`](docs/caution-db-schema.md) | DB 스키마 v3, 백엔드 전달용 제약사항 |
| [`docs/agent-0-supervisor.md`](docs/agent-0-supervisor.md) ~ [`agent-9-curation.md`](docs/agent-9-curation.md) | Agent·Tool별 입출력 스펙과 처리 로직 |

문서 전체 목록과 읽는 순서는 [`docs/README.md`](docs/README.md)에 있습니다.

각 Agent·Tool 문서 끝에 **"미확정 항목 (팀 확인 대기)"** 절이 있습니다. 구현 전 반드시 확인하세요.
