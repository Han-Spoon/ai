# Agent·Tool 공통 구현 가이드

⓪~⑨를 구현할 때 **노드마다 똑같이 반복되는 결정**을 한곳에 모으는 문서입니다. 프레임워크, 공통 데이터 계약, 오류 규격, LLM 호출 규칙, 테스트 위치처럼 "어느 Agent를 만들든 먼저 알아야 하는 것"이 여기 들어갑니다.

> **현재 상태: 틀만 있는 문서입니다.**
> 각 절은 **"현재 알려진 내용"** 과 **"여기에 적을 것"** 으로 나뉩니다. "현재 알려진 내용"은 다른 문서에 이미 적혀 있어 옮겨온 것이고 출처를 함께 적었습니다. 비어 있는 항목은 아직 팀이 정하지 않은 것이며, **이 문서를 채우는 사람이 임의로 결정하지 않습니다** (→ [§1](#1-이-문서를-채울-때의-규칙)).

---

## 0. 이 문서의 경계

같은 내용이 여러 문서에 흩어지면 도구·사람에 따라 다르게 구현됩니다. 어디에 적을지는 아래 기준으로 나눕니다.

| 내용 | 적는 곳 |
|---|---|
| 프레임워크, 공통 계약, 공통 오류, 로깅, 테스트 배치, 디렉터리 구조 | **이 문서** |
| 특정 노드의 입출력 필드, 처리 로직, 그 노드만의 예외, 테스트 케이스 | 해당 `agent-N-*.md` |
| 전체 흐름도, 라우팅, 케이스별 시나리오 | [`caution-multi-agent-architecture.md`](caution-multi-agent-architecture.md) |
| 테이블·컬럼·제약 | [`caution-db-schema.md`](caution-db-schema.md) |
| 커밋·브랜치·이슈·PR 규칙, 용어집, 금지 사항 | [`../AGENTS.md`](../AGENTS.md) |
| 제출 PPT 기준 (충돌 시 최우선) | [`ppt-baseline.md`](ppt-baseline.md) |

**중복 금지.** 이 문서에 있는 규칙을 각 Agent 문서에 다시 적지 않고 이 문서를 링크합니다. 반대로 이 문서는 특정 노드의 로직을 설명하지 않습니다.

### ⓪ Supervisor와 헷갈리기 쉬운 경계

⓪도 노드 하나이므로 "전체에 관한 것"과 "⓪이 하는 일"이 섞이기 쉽습니다. 기준은 **"⓪ 말고 다른 노드도 이걸 알아야 하는가"** 입니다.

| 이 문서 | [`agent-0-supervisor.md`](agent-0-supervisor.md) |
|---|---|
| 모든 노드가 받고 그대로 돌려주는 필드 | graph·state·node 시그니처, state 필드 소유 |
| 모든 노드가 같은 모양으로 뱉는 오류 스키마 | 그 오류를 받아 재시도·우회·부분 응답을 만드는 정책 |
| 노드가 값을 손대지 않고 전달하도록 모델로 보장하는 방법 | 어떤 값을 누구에게 언제 넘길지 (호출 계약·라우팅) |
| 테스트·로깅·설정처럼 노드마다 반복되는 작업 방식 | 단일 진입점, 배치·동시성, API 호환 |

---

## 1. 이 문서를 채울 때의 규칙

- **미확정 항목을 여기서 결정하지 않습니다.** 정해지지 않은 것은 "정해야 할 것"으로 적고 [§12](#12-미확정-항목-팀-확인-대기)에 모읍니다. 결정은 이슈 → 리뷰 → PR로 들어옵니다.
- **출처 없는 규칙을 쓰지 않습니다.** 확정 항목을 옮겨올 때는 원 문서와 절 번호를 함께 적습니다. 원본이 바뀌면 이 문서가 따라갑니다.
- **같은 내용을 두 곳에 적지 않습니다.** 원본이 있으면 요약 + 링크만 둡니다.
- 코드 용어는 처음 나올 때 한 줄로 뜻을 풀거나 [`../AGENTS.md`](../AGENTS.md)의 용어 표를 가리킵니다.
- 완료 조건은 확인 가능한 문장으로 씁니다. ("개선한다" ✗ → "`status` 3값이 ③→④ 출력까지 유지된다" ○)

---

## 2. 구현 프레임워크

### 현재 알려진 내용

[`agent-0-supervisor.md`](agent-0-supervisor.md) §0-2의 **제안**이며 **아직 팀 승인 전**입니다 (이슈 #76과 함께 결정).

- Supervisor는 LangChain 생태계 + LangGraph `StateGraph`로 구현한다.
- 라우팅은 LLM에 맡기지 않고 명시적 conditional edge로 구현한다.
- LLM 추론은 ②·⑥·⑧처럼 판단이 필요한 Agent 내부로 제한한다.
- ①·③·④·⑤·⑦·⑨ Tool은 결정론적 Python 함수 또는 API adapter로 연결한다.
- 각 node는 공용 state를 읽고 자신이 담당하는 필드만 갱신한다.
- `store_id`처럼 실행 중 바뀌면 안 되는 값은 run-scoped context로 분리한다.
- 초기 버전은 요청 단위 휘발성 state를 쓴다. 영속 checkpoint는 보존 기간·재실행 요구가 확정된 뒤 도입한다.

현재 [`../requirements.txt`](../requirements.txt)에는 langchain·langgraph가 **없습니다.** 승인 전까지 의존성을 추가하지 않습니다.

### 여기에 적을 것

- [ ] 승인 결과와 결정 근거 (승인되면 이 절을 "확정"으로 승격)
- [ ] langchain / langgraph 버전 고정값과 Python 3.11 호환 확인 결과
- [ ] 모든 노드가 공통으로 쓰는 LLM / HTTP 클라이언트를 어디서 생성할지
- [ ] 동기/비동기 선택 — FastAPI async 경계와 LLM 호출 blocking 처리
- [ ] 승인되지 않을 경우의 대안 (직접 오케스트레이션) 비교

> graph·state·node 시그니처처럼 **Supervisor 내부 구조**에 해당하는 항목은 [`agent-0-supervisor.md`](agent-0-supervisor.md) §0-2와 §9가 소유합니다. 여기 적지 않습니다.

---

## 3. 공통 데이터 계약

### 현재 알려진 내용

모든 Agent·Tool 호출에 포함하는 공통 문맥 ([`agent-0-supervisor.md`](agent-0-supervisor.md) §1-3):

```json
{
  "schema_version": "1.0",
  "trace_id": "0199...",
  "scan_session_id": "scan-123",
  "store_id": 123456,
  "item_id": "scan-123:0"
}
```

- 모든 외부 경계는 Pydantic 모델로 검증한다.
- `item_id`는 ① OCR Tool 출력 순서로 생성하고 최종 응답까지 바꾸지 않는다. 메뉴판 단위 호출은 생략 가능, 메뉴 단위 호출은 필수.
- 하위 노드는 `store_id` / `scan_session_id` / `item_id`를 **수정하지 않고 그대로 반환**한다.
- `store_id`는 정수(DB `BIGINT` ↔ Java `Long` ↔ Python `int`)이며 AI가 새로 발급하거나 변경하지 않는다.
- 재료 식별자는 `ingredient_id` + `canonical_name`을 함께 쓴다. 웹 신규 후보만 `ingredient_id: null`을 허용한다.
- `schema_version`은 breaking change면 major, 신규 선택 필드는 minor.
- `status`는 3값 `present` / `absent` / `unknown`. 2값 축약 금지 ([`../AGENTS.md`](../AGENTS.md) 변경 금지 항목).

### 여기에 적을 것

- [ ] ①~⑨ 요청·응답 Pydantic 모델 전체 표 — 지금 문서별로 `ingredient`, `ingredient_id`, `name`이 혼용되므로 통일 결과를 여기 고정
- [ ] 공통 모델이 사는 모듈 경로와 누가 import하는지
- [ ] evidence / provenance 공통 구조 — hard evidence(사장님·사용자 확정)와 soft evidence(웹서치 등 미검증)를 끝까지 구분해 전달하는 필드
- [ ] 3-State가 ③→④→⑤→⑧까지 유지되는지 검증하는 방법
- [ ] `anomaly_locked`(통계적 이상 답변으로 override가 잠긴 상태)를 모든 노드가 손대지 않고 그대로 전달하도록 모델에서 보장하는 방법 — 누가 이 값을 만들고 누가 해석하는지는 [`agent-0-supervisor.md`](agent-0-supervisor.md) §4-7 소유
- [ ] 모델 변경 절차 — `schema_version`을 올리는 기준과 리뷰 담당

---

## 4. 모듈·디렉터리 구조

### 현재 알려진 내용

| 경로 | 현재 역할 |
|---|---|
| [`../app.py`](../app.py) | FastAPI 진입점. `/v1/ocr`, `/v1/ruleengine`, `/v1/result` |
| [`../ai_ocr/`](../ai_ocr/README.md) | ① 메뉴판 이미지 → 메뉴명 추출 |
| [`../ai_ruleengine/`](../ai_ruleengine/README.md) | 메뉴명 정규화·매칭, 재료 태깅, 룰 기반 위험 판정 |
| [`../ai_result/`](../ai_result/README.md) | ⑧ 최종 판정·근거 메시지 |
| [`../ai_web_search_agent/`](../ai_web_search_agent/README.md) | ⑥ 재료 후보 수집 |
| [`../crawling/`](../crawling/README.md) | 메뉴·재료 데이터 수집·전처리 |

현재 디렉터리는 **⓪~⑨ 번호와 1:1로 대응하지 않습니다.** 한 패키지가 여러 노드를 담고 있고, ⓪·③·⑦·⑨에 해당하는 자리가 없습니다.

### 여기에 적을 것

- [ ] ⓪~⑨ ↔ 디렉터리 대응 표 (지금 없는 노드를 어디에 만들지)
- [ ] 기존 패키지를 쪼갤지 유지할지 — 쪼갠다면 전환 순서
- [ ] 노드 간 직접 import를 막는 코드 규약 — 호출 경계 자체는 [`agent-0-supervisor.md`](agent-0-supervisor.md) §4가 정하고, 여기서는 그걸 어떻게 강제할지만 정한다 (패키지 분리, lint 규칙 등)
- [ ] 새 노드를 추가할 때 만들어야 하는 파일 목록 (`__init__.py`, models, adapter, tests, README)

---

## 5. LLM 호출 규칙 (Agent 노드 공통)

**Agent는 판단하고 Tool은 수행합니다.** LLM 호출은 Agent 노드(②·⑥·⑧) 안에만 둡니다.

### 여기에 적을 것

- [ ] 사용 모델과 버전 고정 방식, 노드별로 다른 모델을 쓸지
- [ ] 구조화 출력 방식 — Pydantic 스키마 강제, 파싱 실패 시 처리
- [ ] LLM에게 **금지**할 공통 사항 — 예: 메뉴를 임의로 확정하지 않기, 재료를 추측으로 추가하지 않기, 위험 판정을 직접 내리지 않기. 노드별 금지 목록은 해당 문서에 두고 공통 금지만 여기 둔다
- [ ] 프롬프트 보관 위치 (코드 상수 / 파일) 와 변경 이력 관리 방식
- [ ] 타임아웃·재시도·토큰 한도 기본값
- [ ] 실패 시 fallback — LLM이 죽어도 FN이 늘지 않는 경로
- [ ] 호출량·비용 기록 방식
- [ ] 입력에 사용자 프로필·메뉴 원문을 어디까지 넣을지 (로그 마스킹과 함께 결정)

---

## 6. 결정론적 Tool 구현 규칙

①·③·④·⑤·⑦·⑨는 LLM 없이 정해진 절차만 수행합니다.

### 현재 알려진 내용

[`../AGENTS.md`](../AGENTS.md) "DB 쓰기" 확정 항목:

- 물리 DB 쓰기는 백엔드만 수행한다. ⑦ DB Update Tool은 저장할 명령과 근거를 반환할 뿐이다.
- `ingredient_risk_scores`는 직접 UPDATE 금지. 항상 `ingredient_evidence_log` INSERT → 재계산 순서.
- 예외: `menu_ingredient_cache` 쓰기는 ④가 수행한다.

### 여기에 적을 것

- [ ] 같은 입력 → 같은 출력 보장 범위 (DB 상태 의존을 어떻게 다룰지)
- [ ] 읽기 전용 DB 접근을 각 Tool이 직접 할지, 공통 adapter를 둘지
- [ ] ④의 캐시 쓰기 예외를 코드에서 어떻게 분리해 실수로 다른 쓰기가 섞이지 않게 할지
- [ ] 외부 API 호출(CLOVA OCR, 웹서치)의 재시도·캐시 공통 규칙
- [ ] 수치 계산 Tool(⑤)의 부동소수점·반올림 규칙

---

## 7. 오류 스키마와 로깅

모든 노드가 **같은 모양의 오류를 뱉는 것**까지가 이 절의 범위입니다. 그 오류를 받아서 재시도할지, 부분 실패를 어떻게 합칠지는 Supervisor 정책이므로 [`agent-0-supervisor.md`](agent-0-supervisor.md) §6이 소유합니다.

### 현재 알려진 내용

공통 오류 모델 ([`agent-0-supervisor.md`](agent-0-supervisor.md) §6-1):

```json
{
  "code": "WEB_SEARCH_TIMEOUT",
  "node": "web_search",
  "item_id": "scan-123:0",
  "message": "웹 검색 시간이 초과되었습니다.",
  "retryable": true,
  "fallback": "no_information"
}
```

- 오류를 내는 노드는 `code`, `node`, `item_id`, `message`, `retryable`, `fallback`을 모두 채운다. `retryable`과 `fallback`은 **판단 재료를 넘기는 필드**이고, 그 값을 보고 실제로 재시도·우회하는 주체는 Supervisor다.
- `trace_id`, `item_id`, node, route, latency를 구조화 로그로 남긴다.
- 사용자 프로필과 원문 메뉴는 로그에 그대로 남기지 않고 마스킹·요약 정책을 적용한다.

재시도 대상 제한, 부분 실패 시 CAUTION 유지, 사용자에게 보여줄 오류 문구는 [`agent-0-supervisor.md`](agent-0-supervisor.md) §6·§8에 있습니다.

### 여기에 적을 것

- [ ] `code` 네이밍 규칙과 전체 목록 (노드별 prefix를 둘지)
- [ ] `retryable`을 노드가 어떤 기준으로 판정해 채우는지
- [ ] 마스킹 정책 구체화 — 어떤 필드를 어떻게 가릴지
- [ ] 로그 출력 형식(JSON 여부)과 수집 경로
- [ ] 예외를 오류 모델로 변환하는 공통 헬퍼를 둘지

---

## 8. 설정·시크릿

### 현재 알려진 내용

[`../AGENTS.md`](../AGENTS.md) 금지 항목:

- `.env`를 읽거나 커밋하지 않는다. 실제 CLOVA OCR 시크릿과 OpenAI API 키가 들어 있다.
- API 키·토큰·비밀번호를 코드나 문서에 하드코딩하지 않는다. `.env.example`에는 값 없이 키 이름만 둔다.

### 여기에 적을 것

- [ ] 노드별로 필요한 환경변수 **키 이름** 목록 (값 없이)
- [ ] 설정 로딩 방식 — 노드마다 따로 읽을지, 공통 settings 객체를 둘지
- [ ] 필수 키 누락 시 기동 실패 vs 해당 노드만 비활성
- [ ] threshold·scale처럼 **판정에 영향을 주는 값**을 설정으로 뺄지 코드에 둘지 — 뺀다면 변경 이력을 남기는 방법

---

## 9. 테스트 규칙

### 현재 알려진 내용

```bash
pytest -q
python -m compileall -q app.py ai_ocr ai_ruleengine ai_result
```

CI(`.github/workflows/ci.yml`)가 이 두 가지를 실행하고, `AI CI` 워크플로의 `test` 잡이 통과해야 머지 버튼이 열립니다. Python 3.11 기준입니다.

테스트는 현재 [`../tests/`](../tests), `ai_result/tests`, `ai_web_search_agent/tests`, `ai_ocr/test_parser_with_mock.py`에 흩어져 있습니다.

### 여기에 적을 것

- [ ] 테스트 배치 규칙 — 패키지 안에 둘지 루트 `tests/`로 모을지
- [ ] 각 Agent 문서의 **"테스트 케이스" 표를 실제 테스트로 옮기는 규칙** (케이스 번호를 테스트 이름에 남길지)
- [ ] 외부 호출(OCR·LLM·웹서치) mock 방식과 공용 fixture 위치
- [ ] 샘플 데이터 사용 기준 — [`../sample_data/`](../sample_data), [`../examples/`](../examples)
- [ ] FN·F2 회귀 테스트 구성 방법 (북극성 지표를 CI에서 확인할 수 있게 할지)
- [ ] `compileall` 대상에 새 패키지를 추가하는 절차

---

## 10. 안전 기본값 구현 체크리스트 (FN 최소화)

북극성 지표는 **FN(false negative) 최소화, F2 기준**입니다. 판단이 애매하면 더 위험한 쪽으로 기웁니다. 노드를 만들 때마다 아래를 확인합니다.

- [ ] `status` 3값(`present` / `absent` / `unknown`)을 내 노드 출력까지 유지했는가 — `unknown`을 `absent`로 떨어뜨리지 않았는가
- [ ] `anomaly_locked` 재료의 override를 거부하고 CAUTION 이상을 유지했는가
- [ ] `no_information`(DB에도 없고 웹서치도 실패) 경로에서 SAFE를 반환하지 않는가
- [ ] 내 노드가 실패했을 때 결과가 **더 안전한 쪽**으로 떨어지는가 — 실패가 SAFE로 이어지지 않는가
- [ ] 변형 메뉴 scale은 α·β를 동일 비율로 축소했는가 (`variant_suggested` 0.5, `db_registered` 1.0)
- [ ] prior fallback 순서(`store → cluster → global → uninformative`)를 지켰는가
- [ ] 위험 재료 토큰을 정규화 단계에서 지우지 않았는가

> 근거는 [`../AGENTS.md`](../AGENTS.md) "변경 금지 — 팀 확정 결정"과 각 Agent 문서의 "확정된 결정" 절입니다.

---

## 11. 새 Agent·Tool을 만들 때의 순서

### 여기에 적을 것

아래는 초안입니다. 팀 확정 후 체크리스트로 고정합니다.

- [ ] 1. 해당 `agent-N-*.md`의 **"미확정 항목"** 절을 먼저 확인한다. 미확정이면 구현 전에 이슈로 올린다
- [ ] 2. 입출력 Pydantic 모델을 공통 계약([§3](#3-공통-데이터-계약))에 맞춰 정의한다
- [ ] 3. 노드를 Agent / Tool로 분류하고 LLM 사용 여부를 정한다
- [ ] 4. 문서의 "테스트 케이스" 표를 테스트로 옮기고 실패 상태에서 시작한다
- [ ] 5. 구현 후 `pytest -q`와 `compileall`을 통과시킨다
- [ ] 6. [§10](#10-안전-기본값-구현-체크리스트-fn-최소화) 체크리스트를 확인한다
- [ ] 7. 문서와 구현이 다르면 **문서를 먼저 고친다** (`ppt-baseline.md`는 수정 대상이 아니다)
- [ ] 8. `{type}({영역}): {설명}` 형식으로 커밋하고 PR 본문에 이슈를 연결한다

---

## 12. 미확정 항목 (팀 확인 대기)

- [ ] LangChain 생태계 + LangGraph `StateGraph` 사용 최종 승인 (#76)
- [ ] 공통 Pydantic 모델이 사는 모듈 경로와 소유자
- [ ] 재료 식별자 `ingredient_id` + `canonical_name` 통일 적용 시점
- [ ] 오류 `code` 체계와 전체 목록
- [ ] 로그 마스킹 정책 구체화
- [ ] 테스트 배치 규칙 (패키지 내부 vs 루트 `tests/`)
- [ ] ⓪~⑨ ↔ 디렉터리 대응과 기존 패키지 재배치 여부
- [ ] 이 문서를 [`README.md`](README.md) 읽는 순서 표의 어느 위치에 넣을지

여기에는 **구현 방식에 관한 미확정 항목만** 둡니다. 판정 로직·계약에 관한 미확정 항목은 이미 각 문서에 등록되어 있으므로 옮겨 적지 않습니다.

| 다른 곳에 등록된 항목 | 소유 문서 |
|---|---|
| 3-State를 ⑧ 입력까지 유지하는 계약 | [`agent-0-supervisor.md`](agent-0-supervisor.md) §8 |
| DANGER / CAUTION / SAFE threshold 보유 주체 | [`agent-8-xai.md`](agent-8-xai.md) §3, [`caution-multi-agent-architecture.md`](caution-multi-agent-architecture.md) §4 |
| `/v1/analyze` 추가와 기존 API 호환 기간 | [`agent-0-supervisor.md`](agent-0-supervisor.md) §8 |
| 배치 API 형태와 동시성 제한, checkpoint 사용 여부 | [`agent-0-supervisor.md`](agent-0-supervisor.md) §8 |

---

## 13. 참고 문서

| 문서 | 이 문서와의 관계 |
|---|---|
| [`ppt-baseline.md`](ppt-baseline.md) | 충돌 시 최우선. 수정 금지 |
| [`caution-multi-agent-architecture.md`](caution-multi-agent-architecture.md) | 흐름·라우팅. 이 문서는 그 흐름을 코드로 옮기는 방법만 다룬다 |
| [`caution-db-schema.md`](caution-db-schema.md) | 테이블·제약. DB 쓰기 경계는 [§6](#6-결정론적-tool-구현-규칙) |
| [`agent-0-supervisor.md`](agent-0-supervisor.md) | 프레임워크 제안(§0-2), 공통 문맥(§1-3), 공통 오류(§6-1)의 원 출처 |
| [`agent-2-normalization.md`](agent-2-normalization.md) | `9. 구현 계획` 절 형식의 참고 사례 |
| [`../AGENTS.md`](../AGENTS.md) | 커밋·브랜치·이슈·PR 규칙, 금지 사항, 용어집 |
| [`README.md`](README.md) | 문서 인덱스와 읽는 순서 |
