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
| [`app.py`](app.py) | FastAPI 진입점. `/v1/ocr`, `/v1/ruleengine`, `/v1/result` |
| [`ai_ocr/`](ai_ocr/README.md) | 메뉴판 이미지 → 메뉴명 추출 (CLOVA OCR + GPT 후처리) |
| [`ai_ruleengine/`](ai_ruleengine/README.md) | 메뉴명 정규화·매칭, 재료 태깅, 룰 기반 위험 판정 |
| [`ai_result/`](ai_result/README.md) | ⑧ Decision Policy / XAI Agent. 최종 판정과 근거 메시지 생성 |
| [`ai_web_search_agent/`](ai_web_search_agent/README.md) | ⑥ Web Search Agent. DB 미등록 메뉴의 재료 후보 수집 |
| [`crawling/`](crawling/README.md) | 메뉴·재료 데이터 수집 및 전처리 |
| [`docs/`](docs/README.md) | 제출 PPT 기준 문서, 아키텍처, DB 스키마, 에이전트 스펙. 인덱스는 `docs/README.md` |

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
| Agent / Tool | **기준 문서(PPT) 표기**를 따른다. Supervisor Agent(번호 없음) · ① OCR Tool · ② Menu Normalization Agent · ③ Exact Feedback Tool · ④ DB / Ontology Tool · ⑤ Bayesian Tool · ⑥ Web Search Agent · ⑦ DB Update Tool · ⑧ Decision Policy / XAI Agent · ⑨ Curation Tool |
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

PR은 **CI(`AI CI` 워크플로의 `test` 잡)가 통과하면 자동으로 머지된다.** 리뷰 승인은 요구하지 않는다.

`.github/workflows/auto-merge.yml`이 PR이 열릴 때 auto-merge를 켜두기 때문에, CI가 녹색이 되는 시점에 GitHub가 알아서 머지한다. 버튼을 누를 필요가 없다.

- **충돌이 있으면 자동 머지가 멈춘다.** GitHub이 알아서 보류하므로 따로 설정할 것은 없다
- **draft PR은 제외된다.** 아직 머지되면 안 되는 PR은 draft로 올리거나, PR 페이지에서 auto-merge를 끈다
- 리뷰를 꼭 받고 싶은 PR은 draft로 두거나 auto-merge를 끄고 리뷰어를 지정한다
- 머지 방식은 merge commit (기존 히스토리와 동일)

> 참고: `.github/workflows/deploy-prod.yml`이 `main` push에서 돌기 때문에 **`main` 머지가 곧 배포**입니다 (단, `**.md`·`docs/**`·`images/**` 변경은 제외). 현재는 실사용자가 없는 프로젝트 단계라 배포 승인 게이트를 따로 두지 않았습니다. 실제 사용자를 받기 전에 다시 판단하세요.
>
> 테스트 통과는 "깨지지 않았다"는 신호일 뿐 "의도한 동작이 맞다"는 보장이 아닙니다. 특히 FN(false negative)에 영향을 주는 판정 로직은 CI가 녹색이어도 틀릴 수 있으니, 해당 변경에는 테스트를 함께 추가하세요 (#91).

## 이슈 / PR 규칙

- 이슈 제목: `[TYPE] 설명` — `[FEAT]` `[FIX]` `[HOTFIX]` `[CHORE]` `[DOCS]` `[REFACTOR]`
- PR 제목: `[TYPE] 설명 (#이슈번호)`
- PR 본문에 이슈 연결 필수
  - 이슈를 **완전히 끝내는** PR → `Closes #이슈번호` / `Resolves #이슈번호`
  - 이슈의 **일부만 다루는** PR → `Part of #이슈번호` (closing keyword 금지). 상위 이슈가 조기에 닫히는 것을 막는다
- 이슈 하나 = 작업 하나
- 템플릿은 `.github/ISSUE_TEMPLATE/`, `.github/PULL_REQUEST_TEMPLATE.md` 사용

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
| [`docs/catoin-multi-agent-architecture.md`](docs/catoin-multi-agent-architecture.md) | 전체 흐름도, 케이스별 시나리오, 에이전트별 역할 |
| [`docs/catoin-db-schema.md`](docs/catoin-db-schema.md) | DB 스키마 v3, 백엔드 전달용 제약사항 |
| [`docs/agent-1-supervisor.md`](docs/agent-1-supervisor.md) ~ [`agent-8-xai.md`](docs/agent-8-xai.md) | 에이전트별 입출력 스펙과 처리 로직 |

문서 전체 목록과 읽는 순서는 [`docs/README.md`](docs/README.md)에 있습니다.

각 에이전트 문서 끝에 **"미확정 항목 (팀 확인 대기)"** 절이 있습니다. 구현 전 반드시 확인하세요.
