# ⓪ Supervisor Agent 스펙

담당: 박다은
상태: 구현 기준안 (Prepare/Judge 2-phase 및 외부 JSON 계약 확정, 운영값은 §8에서 협의 중)
상위 문서: `caution-multi-agent-architecture.md`, `caution-db-schema.md`

## 문서 읽는 방법

- **전체 흐름만 확인할 사람**: §0-4 → §0-5 → §1-3 순서로 읽는다.
- **백엔드 연동 담당자**: §1-3의 Prepare/Judge·KnowledgeBundle 계약과 §6의 오류 규칙을 기준으로 구현한다.
- **AI Agent·Tool 담당자**: §1-2 공통 모델을 먼저 읽고, §4에서 자기 번호의 완전한 요청·응답을 확인한다.
- **Supervisor 구현 담당자**: §2의 LangGraph 구조, §3의 state, §5의 분기 규칙까지 읽는다.
- **리뷰 담당자**: §7 테스트와 §8 확정 상태를 확인한다.

## 목차

1. [역할 범위와 설계 원칙](#0-역할-범위)
2. [외부 Prepare/Judge와 JSON 조립 방법](#0-4-외부-진입점--preparejudge-2-phase)
3. [공통 모델과 외부 API 계약](#1-입력--출력-스펙)
4. [LangGraph 구현 구조](#2-처리-로직)
5. [Supervisor state와 가게 문맥](#3-가게-컨텍스트-계약)
6. [①~⑨ 노드별 완전한 JSON](#4-하위-agenttool-호출-계약)
7. [라우팅·실패 처리](#5-라우팅-규칙)
8. [예외 처리](#6-예외-처리)
9. [테스트 케이스](#7-테스트-케이스)
10. [남은 협의](#8-남은-협의)

---

## 0. 역할 범위

Supervisor는 사용자 스캔 요청의 **유일한 진입점**이며, ① OCR Tool 호출부터 ⑨ Curation Tool 결과 취합까지 전체 파이프라인을 오케스트레이션한다.

핵심 책임은 세 가지다.

1. **백엔드가 확정한 `store_id` 검증과 스코프 강제**
2. **하위 Agent·Tool 호출 순서 결정과 결과 취합**
3. **요청 상태·근거·오류·실행 로그의 중앙 관리**

모든 하위 Agent·Tool은 Supervisor의 호출을 받아서만 동작하고, 결과도 Supervisor에게만 반환한다. Agent·Tool 간 직접 호출은 금지한다.

| 하지 않음 | 담당 |
|---|---|
| OCR 자체 수행 | ① OCR Tool |
| 메뉴명 문자열 정제 | ② |
| 확정 재료 조회 | ③ |
| 재료 확장/변형 태깅 | ④ |
| 확률 계산 | ⑤ |
| 웹 크롤링 | ⑥ |
| 물리 DB 쓰기/트랜잭션 | 백엔드 영속화 계층 |
| 저장 명령 생성 | ⑦ |
| 최종 위험 문구 생성 | ⑧ |

### 0-1. 중앙집중형 구조를 쓰는 이유

`store_id`가 빠지면 가게별 확정값과 prior가 전역으로 섞인다. 이 경우 다른 식당의 사장님 답변이나 웹서치 기반 추정이 현재 식당의 위험도에 영향을 줄 수 있어 FN(false negative) 위험이 커진다.

→ Supervisor는 모든 하위 호출에 `store_id`를 필수로 실어 보내고, `store_id`가 없거나 유효하지 않은 요청은 ①~⑨를 호출하지 않는다.

### 0-2. 구현 프레임워크 — LangChain + LangGraph

Supervisor는 **LangGraph `StateGraph`로 전체 실행 순서를 만들고, LLM이 필요한 Agent 내부에서만 LangChain을 사용**한다. 두 라이브러리는 같은 일을 중복해서 하는 것이 아니다.

| 구성 요소 | 쉬운 설명 | 이 프로젝트에서 하는 일 |
|---|---|---|
| LangGraph | 업무 순서표와 진행 상황판 | ①~⑨ 호출 순서, 조건 분기, 메뉴별 병렬 처리, 결과 합치기, 실패 우회 |
| `StateGraph` | LangGraph로 만드는 실제 상태 기반 워크플로 | 각 node가 공용 state를 읽고 자기 결과만 반환하도록 강제 |
| LangChain | LLM을 정해진 입력·출력으로 호출하는 도구 | ② 정규화 보조, ⑥ 검색 판단·추출, ⑧ 설명 문구 생성 |
| Pydantic | JSON 출입구 검사기 | §1·§4의 필드, 타입, enum, null 허용 여부 검증 |
| 일반 Python/API adapter | LLM이 필요 없는 실행 코드 | ①·③·④·⑤·⑦·⑨ 연결 |

쉽게 비유하면 **LangGraph가 공장 컨베이어 벨트**, **각 node가 작업대**, **state가 작업 중인 제품에 붙은 기록지**, **LangChain은 일부 작업대에서만 쓰는 언어 해석 장비**다. Supervisor 자체가 거대한 LLM Agent가 되는 구조가 아니다.

#### 0-2-1. 왜 Supervisor를 자유형 LLM Agent로 만들지 않는가

Supervisor에게 "다음 Tool을 알아서 선택하라"고 맡기면 같은 입력도 실행 경로가 달라질 수 있고, `store_id`, 3-State, `anomaly_locked`, 근거 ID가 중간에 빠질 수 있다. 이 프로젝트는 FN 최소화가 우선이므로 호출 순서를 코드로 고정한다.

- 분기 판단은 `if`와 conditional edge가 담당한다.
- LLM은 graph의 다음 node 이름을 만들 수 없다.
- LLM은 `store_id`, hard evidence 우선순위, SAFE 금지 조건을 바꿀 수 없다.
- LLM 결과는 Pydantic 검증을 통과한 뒤에만 state에 들어간다.
- 검증 실패·timeout 시 미리 정한 보수적 fallback으로 이동한다.

#### 0-2-2. LangChain을 쓰는 곳과 쓰지 않는 곳

| 번호 | 구현 방식 | LangChain 사용 | 이유 |
|---|---|---|---|
| ① OCR Tool | 기존 OCR Python/API adapter | 사용하지 않음 | 이미지 OCR 실행은 정해진 절차 |
| ② Normalization Agent | 규칙·사전 우선 + 제한적 구조화 LLM | 필요할 때만 사용 | 다국어·오탈자·복수 후보의 문맥 판단 |
| ③ Exact Feedback Tool | KnowledgeBundle read adapter (DB 조회 없음) | 사용하지 않음 | 확정값 조회는 결정론적이어야 함 |
| ④ DB/Ontology Tool | KnowledgeBundle read adapter + 재귀 확장 함수 (Judge 중 DB 조회 없음) | 사용하지 않음 | 전달받은 온톨로지 관계만 결정론적으로 확장해야 함 |
| ⑤ Bayesian Tool | 수식 기반 Python 함수 | 사용하지 않음 | 같은 입력은 같은 확률을 내야 함 |
| ⑥ Web Search Agent | 검색 전략·추출에 구조화 LLM + 검색 provider | 사용 | 검색어 구성과 문서 속 재료 추출에 언어 판단 필요 |
| ⑦ DB Update Tool | 저장 명령 builder | 사용하지 않음 | 권한·근거·명령 형식은 고정 규칙 |
| ⑧ Decision/XAI Agent | 위험 판정은 정책 함수, 다국어 설명만 구조화 LLM 가능 | 제한적으로 사용 | LLM이 SAFE/DANGER를 임의 결정하면 안 됨 |
| ⑨ Curation Tool | 넘겨받은 승인 콘텐츠 연결·점수 정렬 (인덱스 직접 조회 없음) | 기본적으로 사용하지 않음 | 승인된 데이터와 정렬식만 사용 |

⑧에서 가장 중요한 경계는 **판정과 문장 생성을 분리**하는 것이다. `risk_level`, `matched_tags`, `evidence_basis`는 Python 정책 함수가 만들고, LangChain은 이 확정 결과를 자연어 문장으로 표현할 때만 사용할 수 있다. LLM이 반환한 문장이 판정 JSON을 다시 덮어쓸 수 없다.

#### 0-2-3. 구현 시 고정할 원칙

- graph는 애플리케이션 시작 시 한 번 `compile()`하고 요청마다 다시 만들지 않는다.
- FastAPI 경계에서는 동기 `invoke()`가 아니라 비동기 `ainvoke()`를 사용한다.
- graph node는 `async def node(state, runtime) -> dict` 형태로 만들고 **전체 state가 아니라 변경 필드만** 반환한다.
- 외부 API·DB·LLM client는 node 안에서 매번 생성하지 않고 FastAPI lifespan에서 만든 뒤 adapter에 주입한다.
- `store_id`, `trace_id`, `scan_session_id`, `schema_version`은 LangGraph run-scoped context의 immutable 값으로 둔다.
- `call_scope`와 `item_id`는 각 node가 공통 context에서 호출 대상에 맞게 조립한다.
- 여러 메뉴의 결과는 reducer로 모으고, 최종 출력 전에 `source_index`로 다시 정렬한다. 병렬 완료 순서를 사용자 출력 순서로 사용하지 않는다.
- 초기 버전은 `compile(checkpointer=False)` 또는 checkpointer 없이 요청 단위 휘발성 state로 실행한다. checkpoint 도입은 §8 결정 뒤에 한다.
- 라이브러리 도입 전 `langgraph`, `langchain`, `langchain-core`, 사용하는 모델 provider 패키지(예: `langchain-openai`)의 **정확한 호환 버전**을 #76에서 고정한다. 특히 현재 `openai==3.8.0`과 provider 패키지의 호환성을 함께 확인한다. 현재 `requirements.txt`에는 LangChain/LangGraph 의존성이 없으므로 승인 전 설치했다고 가정하지 않는다.

이 문서의 코드 예시는 LangGraph 1.x 공개 API인 `StateGraph`, `context_schema`, reducer, conditional edge, `Send`, `compile()`, `ainvoke()`를 기준으로 한다. 정확한 pin을 정한 뒤 import와 인자 이름을 해당 버전에서 한 번 더 확인한다.

### 0-3. 입출력 계약의 기준 문서

**①~⑨ Agent·Tool과 Supervisor 사이의 wire JSON은 이 문서 §1·§4를 단일 기준으로 사용한다.** 개별 Agent·Tool 문서는 각 노드의 내부 처리 로직과 도메인 규칙을 설명하지만, 필드명·타입·필수 여부가 이 문서와 다르면 이 문서에 맞춰 수정한다.

JSON 계약은 **공통 envelope + 노드별 payload**의 2층 구조로 유지한다.

- 공통 envelope: 모든 노드가 동일하게 쓰는 `context`, `warnings`, `errors`
- 노드별 payload: OCR의 `scan_quality`, ⑤의 확률값, ⑧의 판정처럼 해당 노드에만 필요한 필드

모든 노드의 JSON을 완전히 같은 모양으로 만들면 필요 없는 필드와 `null`이 늘고, 어떤 노드가 어떤 값을 책임지는지 불명확해진다. 반대로 공통 필드까지 문서마다 따로 정의하면 이름과 타입이 쉽게 어긋난다. 따라서 공통 모델은 §1-2에서 한 번만 정의하고, 각 번호의 완전한 요청·응답은 §4에서 번호별로 구분한다. 개별 Agent·Tool 문서에는 내부 로직과 함께 자기 번호의 완성된 예시를 보여줄 수 있지만, 계약의 정본은 중복 정의하지 않고 이 문서 §4를 가리킨다.

- 모든 노드는 §1-2의 `context`, `warnings`, `errors` 형식을 공통으로 사용한다.
- 내부 구현의 클래스명이나 DB 컬럼명이 달라도 경계에서는 이 문서의 JSON으로 변환한다.
- 새 필드가 필요하면 개별 문서만 먼저 바꾸지 않고 이 문서의 계약과 `schema_version`을 함께 변경한다.
- 예시 JSON도 계약으로 취급한다. `"str | null"`처럼 설명용 union 문자열을 실제 값으로 보내지 않는다.
- 요청의 `warnings`·`errors`에는 해당 branch에서 앞 단계가 만든 신호를 한 번씩 넣는다. 응답은 이를 그대로 보존하고 현재 노드 신호를 append한다. Supervisor는 `(code, node, item_id)` 기준으로 중복 제거한다.

### 0-4. 외부 진입점 — Prepare/Judge 2-phase

Supervisor의 신규 구현과 백엔드 연동은 다음 두 경로만 사용한다.

| 순서 | 신규 API | Supervisor가 하는 일 | 결과를 사용하는 주체 |
|---|---|---|---|
| 1 | `/v1/analyze/prepare` | ① OCR → 촬영 품질 분기 → ②-A 메뉴명 정제 | 백엔드가 `prepared_items`를 저장하고 메뉴 후보·판정 지식을 조회 |
| 2 | `/v1/analyze/judge` | ②-B 후보 선택 → 메뉴별 ③~⑧ 판정 → 결과 취합 → ⑨ Curation | 백엔드가 최종 결과와 ⑦ 저장 명령을 처리 |

두 요청 사이에는 백엔드의 지식 조회 단계가 들어간다.

```text
POST /v1/analyze/prepare
  → Prepare Graph
  → prepared_items
  → 백엔드 메뉴 후보 조회 및 KnowledgeBundle 구성
  → POST /v1/analyze/judge
  → Judge Graph
  → 최종 판정
```

구현 원칙은 다음과 같다.

- Supervisor 외부 진입점은 `/v1/analyze/prepare`, `/v1/analyze/judge` 두 개만 사용한다.
- 단일 `/v1/analyze` 경로는 만들지 않는다.
- 두 경로 모두 Supervisor가 진입점이다. 백엔드는 ①~⑨ Agent·Tool을 직접 호출하지 않는다.
- Prepare와 Judge 사이의 상태를 AI 프로세스 메모리나 LangGraph checkpoint에 보관하지 않는다. Judge에 필요한 값은 백엔드가 Prepare 결과와 KnowledgeBundle로 다시 전달한다.
- Prepare/Judge와 KnowledgeBundle의 JSON 형식은 §1-3을 따른다. 백엔드는 이 계약에 맞춰 조회 결과를 조립하고, AI는 계약 밖 필드를 추측하지 않는다.

> 전환 참고: 과거의 3-phase API는 신규 구현 및 연동 대상이 아니다. 이 문서의 구현 기준은 위 두 경로뿐이다.

### 0-5. JSON을 조립해서 전달하는 방법

각 Agent·Tool의 응답 JSON이 그대로 다음 Agent·Tool의 요청이 되는 것은 아니다. Supervisor가 응답을 LangGraph state에 저장한 뒤, 다음 노드의 Request 모델에 필요한 값만 골라 새로운 JSON을 만든다.

쉽게 말해 **백엔드는 판정에 필요한 DB 자료를 한 상자(`KnowledgeBundle`)로 보내고, Supervisor는 현재 작업대에 필요한 자료만 꺼내 각 Agent·Tool의 요청 JSON으로 다시 포장한다.** 각 Agent·Tool은 자기 요청에 정의된 값만 읽고 자기 응답만 반환한다. 따라서 담당자는 거대한 전체 JSON을 직접 다룰 필요 없이 §4에서 자기 번호의 Request/Response만 구현하면 된다.

```text
앞 단계 Response
  → Supervisor state에 저장
  → 다음 Request에 필요한 필드 선택
  → Pydantic 모델로 검증
  → 다음 Agent·Tool 호출
```

예를 들어 ③의 `ExactFeedbackResponse` 전체를 ④에 그대로 보내지 않는다. Supervisor는 확정 재료, override 가능 여부, anomaly·만료 상태, 근거 참조를 state에 보존하고 ④·⑤·⑧이 요구하는 Request 모양으로 각각 조립한다.

#### 공통 영역과 노드별 영역

```text
모든 요청·응답의 공통 영역
  context + warnings + errors

노드별 payload 예시
  ① OCR         : image, scan_quality, items
  ③ Exact       : confirmations
  ④ Ontology    : ingredients, variant_suggestion
  ⑤ Bayesian    : probabilities
  ⑧ Decision    : risk_level, message, owner_cards
```

모든 JSON을 같은 모양으로 만들지 않는다. 공통 영역은 동일하게 유지하고, 각 노드는 자기 역할에 필요한 payload만 가진다.

#### Supervisor가 끝까지 보존하는 값

- `schema_version`, `trace_id`, `scan_session_id`, `store_id`
- `item_id`, `source_index`
- OCR 원문, 정규화 메뉴명, 가격과 설명
- `constraint_tags`, `role`, `anomaly_locked`
- `evidence_refs`, `warnings`, `errors`

노드 요청에 당장 쓰이지 않는 값도 state에서는 삭제하지 않는다. 예를 들어 ②가 가격을 사용하지 않더라도 Supervisor는 가격을 state에 보존해 Judge 결과에 다시 넣는다.

#### Prepare에서 Judge까지의 조립 흐름

```text
OcrResponse.items[]
  → NameCleaningRequest.items[]
  → NameCleaningResponse.items[]
  → PrepareResponse.prepared_items[]
  → 백엔드 후보 조회 및 KnowledgeBundle 구성
  → CandidateSelectionRequest.items[]
  → CandidateSelectionResponse.items[]
  → 메뉴별 Item Graph
```

Prepare는 DB의 `menu_id`를 임의로 만들거나 확정하지 않는다. Judge는 백엔드가 제공한 후보 밖의 `menu_id`를 새로 만들 수 없다. `item_id`와 `source_index`는 Prepare부터 최종 결과까지 바꾸지 않는다.

#### Judge 내부의 대표 조립 흐름

```text
③ ExactFeedbackResponse.confirmations[]
  → override 가능한 값은 ⑧ confirmed_results[]
  → 만료·anomaly 등 override 불가 값은 버리지 않고 ④·⑤ 경로

③ ExactFeedbackResponse.flag_confirmations[]
  → ⑧ DecisionRequest.flag_confirmations[]

③ ExactFeedbackResponse.owner_question_states[]
  → ⑧ DecisionRequest.owner_question_states[]
  → needs_second는 2차, needs_third는 3차 owner_card 생성

④ OntologyResponse.menu_ambiguity_flags
  (④ 확장을 건너뛰는 exact complete 경로는 Bundle ontology.menu.ambiguity_flags)
  → ⑧ DecisionRequest.menu_ambiguity_flags

④ OntologyResponse.ingredients[]
  → ⑤ BayesianRequest.ingredients[]

⑤ BayesianResponse.probabilities[]
  → ⑧ DecisionRequest.probability_results[]

⑧ DecisionResponse
  → 메뉴별 ItemResult
  → source_index 순서로 취합
  → ⑨ CurationRequest.decision_results[]
```

`warnings`와 `errors`는 앞 단계 값을 보존하고 현재 노드 값을 append한다. `forces_caution: true`인 warning이 하나라도 있으면 해당 메뉴는 SAFE가 될 수 없다. 근거 원본은 state의 evidence registry에 한 번 등록하고, 이후 결과는 `evidence_refs`로 가리킨다.

값이 없을 때 목록은 `[]`, nullable 단일 값과 객체는 `null`을 사용한다. 이전 Response에만 존재하고 다음 Request에 정의되지 않은 필드를 임의로 복사하지 않으며, 다음 Request에 없는 값을 새로 추측해 만들지도 않는다.

#### 한 메뉴가 실제로 전달되는 예시

1. Prepare가 OCR에서 찾은 김치찌개에 `item_id = "scan-123:0"`을 붙여 반환한다.
2. 백엔드는 공통 재료 그래프를 `KnowledgeBundle.ingredient_graph`에 한 번 넣고, 이 `item_id`로 찾은 메뉴 후보와 각 후보의 확정 재료·펼치기 전 레시피 행·가게 prior를 `KnowledgeBundle.items[]`에 넣는다.
3. Judge는 Prepare 결과와 Bundle의 `item_id`가 정확히 일치하는지 먼저 검사한다.
4. ②-B는 백엔드가 준 후보 안에서만 `menu_id`를 선택한다. 후보가 없으면 `menu_id = null`로 유지한다.
5. Supervisor는 선택된 후보의 자료만 꺼내 ③·④·⑤ 요청으로 조립하고, 각 응답을 state에 누적한다.
6. ⑧은 누적된 확정값·확률·경고를 받아 판정하고, ⑨는 모든 메뉴 판정이 끝난 뒤 출력 순서와 추천을 만든다.

| 값의 소유자 | 하는 일 | 하면 안 되는 일 |
|---|---|---|
| 백엔드 | DB를 조회해 `KnowledgeBundle` 구성, Prepare 결과 보관, 저장 명령 실행 | Agent·Tool을 개별 호출하거나 AI 판정값 생성 |
| Supervisor | 식별자·hash 검증, 필요한 slice 선택, 노드별 JSON 조립, 결과 취합 | Bundle에 없는 DB 사실 추측 |
| 각 Agent·Tool | §4의 자기 Request 처리 후 Response 반환 | 다른 Agent·Tool 직접 호출, 공통 context 변경 |

`item_id`는 같은 메뉴를 연결하는 열쇠다. `PrepareResponse.prepared_items[].item_id`, `KnowledgeBundle.items[].item_id`, ②-B 결과와 최종 `JudgeResponse.items[].item_id`는 모두 같아야 한다. DB의 `menu_id`는 후보 선택 결과이므로 이 연결 키로 사용하지 않는다.

---

## 1. 입력 / 출력 스펙

### 1-1. JSON 공통 규칙

- 필드명은 `snake_case`, enum 값은 소문자 `snake_case`를 사용한다.
- 시간은 UTC ISO-8601 문자열을 사용한다. 예: `2026-10-08T03:00:00Z`.
- 실행 식별자(`trace_id`, `scan_session_id`, `item_id`, `evidence_id`)는 문자열을 사용한다. DB 식별자인 `store_id`, `menu_id`, `menu_category_id`, `ingredient_id`는 JSON 정수를 사용하며, 아직 DB에 매핑되지 않은 메뉴·재료 ID만 `null`을 허용한다.
- 목록은 값이 없을 때 `[]`, 객체형 결과가 없을 때 `null`을 사용한다. 목록에 `null`을 넣지 않는다.
- 0~1 점수는 JSON number이며 범위를 벗어나면 validation error다.
- 재료 상태는 `present | absent | unknown`만 허용한다. boolean으로 축약하지 않는다.
- 모든 노드 요청·응답은 `context`, `warnings`, `errors`를 포함한다.
- 노드는 입력 `context`를 수정하지 않고 그대로 반환한다.
- §4의 객체는 **closed schema**로 구현한다. 예시에 나온 필드는 모두 필수이며, 값이 없어도 생략하지 않고 예시에 따라 `null` 또는 `[]`를 보낸다. 정의되지 않은 추가 필드는 거부한다.
- `null`은 예시나 설명에서 명시적으로 허용한 필드에만 사용할 수 있다. 새 필드·enum·null 허용 여부를 바꾸면 `schema_version`과 이 문서를 먼저 변경한다.

### 1-2. 공통 모델

#### `NodeContext`

```json
{
  "schema_version": "2.0",
  "trace_id": "0199a2f0-0000-7000-8000-000000000001",
  "scan_session_id": "scan-123",
  "store_id": 123456,
  "call_scope": "item",
  "item_id": "scan-123:0"
}
```

| 필드 | 타입 | 규칙 |
|---|---|---|
| `schema_version` | string | 현재값 `2.0`. breaking change는 major 증가 |
| `trace_id` | UUID string | 요청 전체에서 동일. 없으면 Supervisor가 최초 1회 발급 |
| `scan_session_id` | string | 요청 전체에서 동일 |
| `store_id` | integer | 양수 필수. 백엔드가 존재·활성 검증을 마친 값 |
| `call_scope` | `scan | item | out_of_band` | 메뉴판 배치, 메뉴 단위, 스캔 외 피드백 호출 구분 |
| `item_id` | string \| null | `item`이면 필수, `scan`·`out_of_band`이면 null 허용 |

#### `IngredientRef`

```json
{
  "ingredient_id": 101,
  "canonical_name": "돼지고기"
}
```

`ingredient_id`의 타입은 `integer | null`이다. 표준 재료로 매핑되었으면 DB의 정수 ID를 보내고, 웹에서 처음 발견했거나 아직 매핑하지 못한 후보만 `null`을 허용한다. `canonical_name`은 ID 유무와 관계없이 항상 필수다.

#### `constraint_tags`

재료가 사용자 제한과 충돌하는지를 판정하기 위한 `is_*` 표준 태그 배열이다. 예: `is_pork`, `is_fish`, `is_beef`, `is_alcohol`. 어휘는 식약처 알레르기 19종에 `is_fish`, `is_duck`, `is_alcohol`을 더한 22종이며, 원본은 `ingredient_tags`와 `ai_ruleengine/constants.py`다. **태그는 ④만 붙인다** (결정, 2026-10-10, ④ §2-7). ③·⑥은 `[]`로 보내고, Supervisor가 ④ 태그 조회로 채운다. ⑤·⑧은 받은 값을 바꾸지 않는다. `is_spicy`는 재료 태그가 아니라 ① OCR이 메뉴 문구에서 찾는 메뉴 속성이다 (§4-0). 값이 없으면 `[]`이지만 표준 재료의 태그 조회 자체가 실패한 경우에는 빈 배열만 반환하지 않고 warning을 함께 보낸다.

#### `UserProfile`

```json
{
  "religion_type": "halal",
  "is_vegetarian": false,
  "vegetarian_type": null,
  "no_alcohol": false,
  "allergies": ["is_fish"],
  "no_spicy": false
}
```

| 필드 | 타입·허용값 |
|---|---|
| `religion_type` | `halal | kosher | hindu | null` |
| `is_vegetarian` | boolean |
| `vegetarian_type` | `vegan | lacto | ovo | lacto_ovo | pesco | null` |
| `no_alcohol` | boolean |
| `allergies` | 중복 없는 표준 `is_*` constraint tag 배열 |
| `no_spicy` | boolean |

`is_vegetarian: false`이면 `vegetarian_type`은 `null`이어야 하고, true이면 null이 아닌 허용값이 필요하다. 원본 사용자 ID·이름 등 판정에 필요 없는 개인정보는 AI에 보내지 않는다.

#### `EvidenceRef`

```json
{
  "evidence_id": "evidence-001",
  "evidence_class": "hard",
  "source_type": "owner_feedback",
  "source_ref": "owner_verification_requests:question-001",
  "verification_status": "owner_confirmed",
  "reliability_weight": 1.0,
  "observed_at": "2026-10-08T03:00:00Z"
}
```

- `evidence_class`: `hard | soft`
- `source_type`: `owner_feedback | user_feedback | recipe | expanded | web_search | ocr_variant_tag | ocr_text_hint`
- `verification_status`: `owner_confirmed | approved | pending_review | rejected | inferred`
- `reliability_weight`: 0~1 number 또는 아직 출처 신뢰도를 계산하지 않은 경우 `null`. ⑥의 신규 웹 출처는 `null`로 시작한다.
- DB PK가 아직 없는 런타임 근거도 `evidence_id`를 생략하거나 null로 두지 않는다. 생성 노드가 `runtime:{scan_session_id}:{item_id}:{source_type}:{ordinal}` 형식의 run-local ID를 발급하고, 백엔드가 저장 후 DB ID와의 매핑을 관리한다.
- 웹서치 근거는 캐시에서 여러 스캔이 재사용하므로 예외적으로 `web:{canonical_url}` 형식을 사용한다. `canonical_url`은 fragment와 추적용 query parameter를 제거한 정규화 URL이다.

#### `NodeWarning`

```json
{
  "code": "cycle_detected",
  "node": "ontology",
  "item_id": "scan-123:0",
  "message": "재료 확장 중 순환 참조를 차단했습니다.",
  "forces_caution": true
}
```

다음 warning code는 모두 `forces_caution: true`로 고정한다.

| code | 발생 조건 |
|---|---|
| `max_depth_reached` | ④ 온톨로지 확장이 제한 깊이에 도달해 일부 하위 재료가 남을 수 있음 |
| `constraint_tags_lookup_failed` | ④ 표준 재료의 제한 태그를 조회하지 못함 |
| `unmapped_constraint_tags` | 웹 등에서 찾은 재료를 표준 재료나 제한 태그에 매핑하지 못함 |
| `knowledge_slice_unavailable` | 후보·확정값·온톨로지·prior·출처 신뢰도 중 판정 필수 조회가 실패함 |

Supervisor는 위 warning이 하나라도 있으면 해당 메뉴를 SAFE로 확정하지 않는다.

#### `NodeError`

```json
{
  "code": "web_search_timeout",
  "node": "web_search",
  "item_id": "scan-123:0",
  "message": "웹 검색 시간이 초과되었습니다.",
  "retryable": true,
  "fallback": "no_information"
}
```

### 1-3. Prepare/Judge 외부 계약

외부 요청·응답 모델은 다음 네 개만 정의한다.

- `PrepareRequest`: 이미지 참조, 실행 문맥, 언어
- `PrepareResponse`: 촬영 품질, OCR 원문, 정제된 메뉴 목록, 경고와 오류
- `JudgeRequest`: Prepare 결과, 사용자 프로필, KnowledgeBundle, Curation 옵션
- `JudgeResponse`: 메뉴별 판정, 근거, 추천, 저장 명령, 경고와 오류

#### `/v1/analyze/prepare` 요청 — `PrepareRequest`

```json
{
  "schema_version": "2.0",
  "trace_id": "0199a2f0-0000-7000-8000-000000000001",
  "scan_session_id": "scan-123",
  "store_id": 123456,
  "image": {
    "source": "camera",
    "storage_key": "scans/menu.jpg",
    "version_id": null,
    "expected_etag": null
  },
  "locale": "ko"
}
```

`store_id`는 양의 정수이고, `image.storage_key`는 비어 있지 않은 문자열이어야 한다. 외부 계약에서 레거시 `image.image_url`은 사용하지 않는다. `source`는 `camera | upload`, `locale`은 `ko | en | ar | zh-CN | zh-TW | ja | es` 중 응답 문구의 언어다. `trace_id`는 요청 추적 ID, `scan_session_id`는 한 번의 메뉴판 스캔을 식별하는 ID이며 백엔드가 생성해 전달한다.

#### `/v1/analyze/prepare` 응답 — `PrepareResponse`

Prepare 응답은 기존 OCR 응답의 촬영 품질·메뉴 추출 구조를 유지하고, ②-A가 만든 정규화 필드를 같은 메뉴 객체에 추가한다. 백엔드가 기존 OCR 연동 모델을 최대한 재사용할 수 있도록 OCR 결과와 정규화 결과를 별도 배열로 나누지 않는다.

```json
{
  "schema_version": "2.0",
  "trace_id": "0199a2f0-0000-7000-8000-000000000001",
  "prepare_id": "0199a2f0-0000-7000-8000-000000000101",
  "scan_session_id": "scan-123",
  "store_id": 123456,
  "prepared_items_hash": "sha256:8d4f000000000000000000000000000000000000000000000000000000000001",
  "scan_quality": {
    "status": "usable",
    "reasons": [],
    "retake_suggestions": []
  },
  "prepare_signals": {
    "scan_quality_status": "usable",
    "warnings": []
  },
  "prepared_items": [
    {
      "item_id": "scan-123:0",
      "source_index": 0,
      "raw_menu_name": "■ 김치 찌개 2인",
      "normalized_menu_name": "김치찌개",
      "display_name": "김치 찌개 2인",
      "description": null,
      "price_text": "8,000원",
      "price_options": [],
      "locale_hint": "ko",
      "options": ["2인"],
      "residual_tokens": [],
      "risk_tag_observations": []
    }
  ],
  "warnings": [],
  "errors": []
}
```

필드 규칙은 다음과 같다.

- `prepare_id`는 Supervisor가 Prepare 요청마다 한 번 발급하는 UUID이며 재시도 추적과 Judge 연결에 사용한다. 백엔드는 값을 변경하지 않고 Prepare 결과·KnowledgeBundle과 함께 보존한다.
- `prepared_items_hash`는 Supervisor가 `{prepared_items, prepare_signals}` 객체를 **RFC 8785 JSON Canonicalization Scheme(JCS)** 으로 직렬화한 뒤 계산한 SHA-256이다. 출력 형식은 `sha256:`과 소문자 64자리 hex를 연결한 문자열이다. 백엔드는 Judge 요청에 두 값과 hash를 그대로 돌려보내며 AI가 다시 계산해 검증한다.
- `scan_quality.status`는 `usable | low_confidence | needs_retake`다. 수치형 품질 점수와 메뉴별 OCR confidence는 AI 내부 관측값이며 외부 계약에는 넣지 않는다.
- `prepare_signals`는 Judge까지 보존해야 하는 Prepare 안전 신호다. `scan_quality_status`는 `scan_quality.status`와 같고, `warnings`에는 `forces_caution: true`인 warning만 넣는다. `prepared_items_hash`가 이 객체까지 보호하므로 백엔드는 값을 수정하거나 일부만 누락할 수 없다.
- `prepared_items[]`는 기존 OCR 메뉴 필드에 `normalized_menu_name`, `display_name`, `options`, `residual_tokens`를 추가한 구조다.
- `item_id`는 `{scan_session_id}:{source_index}` 형식으로 한 번만 만들고 Judge 최종 결과까지 유지한다.
- Prepare에서는 DB 후보를 조회하거나 `menu_id`, `menu_category_id`를 확정하지 않으므로 두 필드를 응답에 넣지 않는다.
- `needs_retake`이면 ②-A를 실행하지 않고 `prepared_items: []`로 반환한다. `reasons`와 `retake_suggestions`로 재촬영 이유를 전달하며 이 응답으로 Judge를 호출할 수 없다.
- `low_confidence`이면 ②-A까지 실행하고 메뉴를 반환하되 `forces_caution: true` warning을 함께 보존한다.
- `description`, `price_text`는 인식하지 못하면 `null`, 목록형 필드는 값이 없으면 `[]`다.

#### `/v1/analyze/judge` 요청 — `JudgeRequest`

```json
{
  "schema_version": "2.0",
  "trace_id": "0199a2f0-0000-7000-8000-000000000001",
  "prepare_id": "0199a2f0-0000-7000-8000-000000000101",
  "scan_session_id": "scan-123",
  "store_id": 123456,
  "prepared_items_hash": "sha256:8d4f000000000000000000000000000000000000000000000000000000000001",
  "knowledge_revision": "2026-10-12T03:00:00Z",
  "prepare_signals": {
    "scan_quality_status": "usable",
    "warnings": []
  },
  "prepared_items": [
    {
      "item_id": "scan-123:0",
      "source_index": 0,
      "raw_menu_name": "■ 김치 찌개 2인",
      "normalized_menu_name": "김치찌개",
      "display_name": "김치 찌개 2인",
      "description": null,
      "price_text": "8,000원",
      "price_options": [],
      "locale_hint": "ko",
      "options": ["2인"],
      "residual_tokens": [],
      "risk_tag_observations": []
    }
  ],
  "knowledge_bundle": {
    "bundle_schema_version": "2.0",
    "ingredient_graph": {
      "status": "ok",
      "ingredients": [
        {
          "ingredient_id": 109,
          "canonical_name": "김치",
          "name_normalized": "김치",
          "taxonomy_category": "fermented"
        },
        {
          "ingredient_id": 110,
          "canonical_name": "액젓",
          "name_normalized": "액젓",
          "taxonomy_category": "fermented"
        }
      ],
      "aliases": [],
      "tags": [
        {
          "ingredient_id": 110,
          "tag_code": "is_fish"
        }
      ],
      "compositions": [
        {
          "parent_ingredient_id": 109,
          "child_ingredient_id": 110,
          "ambiguity_flag": "has_unclear_jeotgal"
        }
      ],
      "composition_tags": []
    },
    "items": [
      {
        "item_id": "scan-123:0",
        "candidate_lookup": {
          "status": "ok",
          "candidates": [
            {
              "menu_id": 1001,
              "menu_category_id": 11,
              "canonical_name": "김치찌개",
              "score": 1.0,
              "match_type": "canonical"
            }
          ]
        },
        "candidate_knowledge": [
          {
            "menu_id": 1001,
            "exact_feedback": {
              "status": "ok",
              "exact": {
                "menu_id": 1001,
                "confirmations": [],
                "flag_confirmations": [],
                "owner_question_states": []
              }
            },
            "ontology": {
              "status": "ok",
              "menu": {
                "menu_id": 1001,
                "base_menu_id": null,
                "remain_token": null,
                "menu_category_id": 11,
                "source": "curated",
                "ambiguity_flags": ["has_unclear_jeotgal"],
                "knowledge_revision": 7
              },
              "recipe_ingredients": [
                {
                  "ingredient_id": 109
                }
              ],
              "menu_recipe_corpora": [
                {
                  "corpus": "10000recipe",
                  "n_total": 57
                }
              ],
              "menu_ingredient_priors": [
                {
                  "corpus": "10000recipe",
                  "ingredient_id": 109,
                  "k_count": 41
                }
              ],
              "cache": null
            },
            "priors": {
              "status": "ok",
              "store": [
                {
                  "ingredient_id": 101,
                  "alpha": 8.0,
                  "beta": 2.0,
                  "evidence_refs": ["evidence-prior-001"]
                }
              ],
              "evidence": [
                {
                  "evidence_id": "evidence-prior-001",
                  "evidence_class": "soft",
                  "source_type": "web_search",
                  "source_ref": "ingredient_evidence_log:9001",
                  "verification_status": "approved",
                  "reliability_weight": 0.5,
                  "observed_at": "2026-10-12T03:00:00Z"
                }
              ]
            }
          }
        ],
        "fallback_ontology": {
          "status": "not_found",
          "menu": null,
          "recipe_ingredients": [],
          "menu_recipe_corpora": [],
          "menu_ingredient_priors": [],
          "cache": null
        },
        "web_search_cache": {
          "status": "not_found",
          "cached_sources": [],
          "next_rank": 1
        }
      }
    ],
    "source_reliability": {
      "status": "ok",
      "weights": [
        {
          "source_key": "corpus:10000recipe",
          "weight": 0.8
        },
        {
          "source_key": "web_search",
          "weight": 0.5
        },
        {
          "source_key": "curated",
          "weight": 1.0
        }
      ]
    },
    "curation_index": {
      "status": "ok",
      "contents": [
        {
          "menu_id": 1001,
          "content_id": "culture-101",
          "locale": "ko",
          "title": "김치찌개 이야기",
          "content": "검수된 문화 설명 본문",
          "summary": "김치찌개의 문화적 배경",
          "source_id": "source-101",
          "constraint_tags": [],
          "review_status": "approved",
          "updated_at": "2026-10-12T03:00:00Z"
        }
      ]
    }
  },
  "user_profile": {
    "religion_type": "halal",
    "is_vegetarian": false,
    "vegetarian_type": null,
    "no_alcohol": false,
    "allergies": [],
    "no_spicy": false
  },
  "locale": "ko"
}
```

`user_profile`은 §1-2 `UserProfile`의 closed schema를 사용한다. `locale`은 `ko | en | ar | zh-CN | zh-TW | ja | es` 중 하나다. `curation_options`는 선택 객체이며, 보낼 경우 `limit`만 허용한다. `curation_options.limit`은 1~20의 integer이고 객체 또는 필드를 생략하면 Supervisor가 기본값 5를 사용한다.

`prepared_items`와 `prepare_signals`에는 같은 `prepare_id`의 `PrepareResponse` 값을 수정 없이 다시 넣는다. `knowledge_bundle.items`와 `item_id`가 정확히 1:1로 대응해야 한다. Supervisor는 `{prepared_items, prepare_signals}`로 `prepared_items_hash`를 다시 계산해 Prepare의 안전 신호까지 함께 검증한다.

`candidate_lookup.candidates[]`는 score 내림차순·`menu_id` 오름차순으로 정렬하고 초기 최대 개수는 5개로 고정한다. 백엔드는 후보마다 같은 `menu_id`의 `candidate_knowledge[]`를 정확히 하나 제공한다. ②-B가 후보를 고른 뒤 Supervisor는 그 `menu_id`의 지식만 Item Graph에 전달한다. 후보가 없거나 ②-B가 선택하지 못하면 `fallback_ontology`로 longest-match·unknown 경로를 실행하며, 다른 후보의 지식을 대신 사용하지 않는다.

`exact_feedback.exact`는 해당 후보의 확인값이며 `{menu_id, confirmations, flag_confirmations, owner_question_states}`를 사용한다. `completeness`는 ③이 캐시(`ontology.cache`)와 이상 답변·3개월 만료 규칙으로 계산하므로 백엔드는 보내지 않는다 (같은 규칙을 두 곳에 두지 않기 위해, ③ §2). `flag_confirmations`는 메뉴 애매함 플래그에 대한 사장님 답이며 없으면 `[]`다. `owner_question_states`는 이전 질문의 최신 처리 상태이며 진행 중인 질문이 없으면 `[]`다. 사용하지 않는 `exact_feedback.inherited`는 외부 계약에 넣지 않고, 백엔드는 기본 메뉴의 사장님 답변을 조회하지 않는다. `confirmations[]`·`flag_confirmations[]`·`owner_question_states[]`와 `web_search_cache.cached_sources[]`는 각각 §4-2, §4-5의 동일 이름 closed schema를 사용한다.

`ingredient_graph`는 한 Bundle에 한 번만 보내는 공통 read-only 재료 그래프이며 `{status, ingredients, aliases, tags, compositions, composition_tags}`의 closed schema다. `ingredients[]`는 `{ingredient_id, canonical_name, name_normalized, taxonomy_category}`, `aliases[]`는 `{ingredient_id, alias_normalized}`, `tags[]`는 `{ingredient_id, tag_code}`, `compositions[]`는 `{parent_ingredient_id, child_ingredient_id, ambiguity_flag}`, `composition_tags[]`는 `{parent_ingredient_id, child_ingredient_id, tag_code}`다. `compositions[]`에는 `part_of` 관계만 넣는다. ④는 이 그래프로 재귀 확장·순환 차단·깊이 제한·태그와 `role` 부착을 수행하며 백엔드는 펼친 결과를 만들지 않는다.

`ontology`와 `fallback_ontology`는 펼친 재료 결과가 아니라 DB에서 가져온 원재료 행이다. `menu`와 함께 `recipe_ingredients[]`, `menu_recipe_corpora[]`, `menu_ingredient_priors[]`, `cache`를 항상 포함한다. `menu`는 원본 컬럼인 `menu_id`, `base_menu_id`, `remain_token`, `menu_category_id`, `source`, `ambiguity_flags`, `knowledge_revision`만 전달한다. `source`는 DB의 `curated | web_search_generated | variant_generated` 값이다. `exists_in_db`, `is_variant`, `variant_origin`, `menu_category` 같은 파생값은 백엔드가 만들지 않고 Supervisor가 원본 컬럼으로 도출한다. `menu.ambiguity_flags`는 DB `menus.ambiguity_flags`(메뉴 애매함 플래그 `has_unclear_broth | has_unclear_seasoning | has_unclear_jeotgal | has_hidden_animal | has_variant`의 부분집합)를 그대로 넣고, 없으면 `[]`다. 레시피 목록 밖에 무엇이 더 들어갈지 불분명하다는 표시라 ⑧의 SAFE 판단에 쓰인다 (§4-7). `recipe_ingredients[]`는 `{ingredient_id}`, `menu_recipe_corpora[]`는 `{corpus, n_total}`, `menu_ingredient_priors[]`는 `{corpus, ingredient_id, k_count}`다. `cache`는 revision이 일치하는 스냅샷이 있으면 `{menu_revision: integer, ingredients_snapshot: object[]}`, 없으면 `null`이다. `ingredients_snapshot`은 확인 재료를 빼기 전의 전체 확장 목록이며 `anomaly_locked`와 가게 확정값을 포함하지 않는다. ④가 두 재료 목록의 합집합을 시작점으로 만들고, corpus별 0회 관측 채우기와 숨은 재료 관측값 상속까지 한 곳에서 수행한다.

`priors.store[]`의 각 원소는 `{ingredient_id: integer, alpha: number, beta: number, evidence_refs: string[]}`이며 `alpha > 0`, `beta > 0`이어야 한다. 가게 prior에는 관리자 승인된 `web_search`와 `ocr_variant_tag` 근거만 포함하고 `owner_feedback`은 포함하지 않는다. 사용하지 않는 `priors.cluster`와 `priors.global`은 외부 계약에 넣지 않는다. ⑤의 논리적 fallback 순서 `store → cluster → global → uninformative`는 유지하되, cluster 데이터가 없는 현재 계약에서는 건너뛰고 global은 ④의 corpus별 `observations`와 출처 신뢰도로 계산한다. `priors.evidence[]`는 `store[].evidence_refs`가 가리키는 `EvidenceRef`를 중복 없이 담는다.

`source_reliability.weights[]`는 `{source_key: string, weight: number}`이고 `weight` 범위는 0~1이다. `source_key`는 ④의 `observations[].corpus`와 연결할 때 `corpus:{corpus}` 형식을 사용하며, 큐레이션과 웹 관측치는 각각 `curated`, `web_search`를 사용한다. 같은 `source_key`는 한 번만 보낸다. 목록에 없는 신규 출처의 기본 weight는 0.5다. status가 `unavailable`이어도 계산을 계속해야 한다면 0.5를 적용하되 SAFE 금지 warning을 유지한다.

`curation_index.contents[]`는 `{menu_id, content_id, locale, title, content, summary, source_id, constraint_tags, review_status, updated_at}`의 closed schema다. `menu_id`는 integer, 나머지 식별자·본문·시간은 string, `constraint_tags`는 string 배열이다. `review_status`는 현재 `approved`만 Judge에 전달할 수 있다. ⑨는 locale이 요청과 맞고 사용자 제한 태그와 충돌하지 않는 승인 콘텐츠만 사용한다.

#### KnowledgeBundle slice 필수 규칙

| 영역 | `ok`일 때 | `not_found`일 때 | `unavailable`일 때 |
|---|---|---|---|
| `ingredient_graph` | 다섯 배열을 모두 제공. 태그가 없는 표준 재료의 `tags` 행 부재는 정상 | 다섯 배열 모두 `[]`, 모든 메뉴 SAFE 금지 | 다섯 배열 모두 `[]`, 모든 메뉴 SAFE 금지 |
| `candidate_lookup` | `candidates[]` 1개 이상, 각 후보와 같은 `candidate_knowledge` 정확히 1개 | 두 배열 모두 `[]`, `fallback_ontology`로 진행 | 두 배열 모두 `[]`, 해당 메뉴 SAFE 금지 |
| `exact_feedback` | `exact` 필수(`confirmations`, `flag_confirmations`, `owner_question_states` 포함) | `exact: null`, heavy path로 진행 | `exact: null`, 확정값이 없다고 간주하지 않고 SAFE 금지 |
| `ontology` / `fallback_ontology` | `menu`, 원재료 행 세 배열, `cache` 제공 | `menu: null`, 세 배열 `[]`, `cache: null` | 같은 빈 형태와 warning을 만들고 SAFE 금지 |
| `priors` | `store[]`와 `evidence[]` 제공 | 두 배열 모두 `[]`, ⑤가 관측값 또는 uninformative prior 사용 | 두 배열 모두 `[]`, 가능한 관측값으로 계산하되 SAFE 금지 |
| `web_search_cache` | `cached_sources[]` 1개 이상, `next_rank`는 다음 양의 정수 | `cached_sources: []`, `next_rank: 1` | 캐시 없음으로 오인하지 않고 필요 시 새 검색하되 warning 유지 |
| `source_reliability` | `weights[]` 제공 | `weights: []`, 기본 신뢰도 정책 사용 | 관측치 가중 불가 상태로 SAFE 금지 |
| `curation_index` | 승인된 `contents[]` 제공 | 추천·문화 콘텐츠만 빈 배열로 반환 | 위험 판정은 유지하고 추천·문화 콘텐츠만 비활성화 |

`bundle_schema_version`, `ingredient_graph`, `items`, `source_reliability`, `curation_index`는 항상 존재해야 한다. `knowledge_bundle.items[]`의 `item_id` 집합은 `prepared_items[]`와 중복 없이 정확히 같아야 한다. status와 무관하게 표에 정한 객체·배열 필드는 생략하지 않는다. 구조 또는 필수 slice가 빠진 `bundle_miss`는 부분 판정으로 넘기지 않고 Judge 요청 자체를 거부한다.

각 조회 영역의 `status`는 `ok | not_found | unavailable`이다.

- `ok`: 조회 성공. 결과가 필요한 영역은 배열에 값을 넣는다.
- `not_found`: 조회는 성공했지만 저장된 데이터가 없다. 후보는 unknown/웹 경로, 나머지는 다음 fallback으로 진행한다.
- `unavailable`: 장애·timeout 때문에 조회하지 못했다. `ingredient_graph`, `candidate_lookup`, `exact_feedback`, `ontology`, `priors`, `source_reliability`처럼 판정 근거에 직접 쓰이는 safety slice는 데이터 없음으로 해석하지 않고 `knowledge_slice_unavailable` warning(`forces_caution: true`)을 만든다. `web_search_cache`는 새 검색을 시도하고, `curation_index`는 추천만 비활성화하므로 두 영역의 장애 자체가 기존 위험 판정을 바꾸지는 않는다.
- `bundle_miss`: 백엔드가 보내는 status 값이 아니라 Supervisor 검증 결과다. 해당 `item_id` 또는 실행에 필요한 영역 자체가 누락된 경우 만들며 HTTP 422로 Judge 요청을 거부한다.

백엔드는 `knowledge_revision`을 동일 DB 스냅샷을 식별하는 변경 불가 문자열로 생성한다. 값은 ISO-8601 시각, DB transaction ID 또는 revision ID 중 백엔드가 일관되게 발급할 수 있는 형식을 사용하며 AI는 내용을 해석하지 않고 응답에 그대로 반환한다. 요청의 `knowledge_bundle`에는 백엔드가 계산한 `bundle_hash`를 받지 않는다. Supervisor가 수신한 `knowledge_bundle` 전체를 RFC 8785 JCS로 직렬화해 SHA-256을 계산하고 JudgeResponse에 수신 내용 식별자로 반환한다. Supervisor는 `prepared_items_hash`, `prepare_id`, `scan_session_id`, `store_id`, 모든 `item_id`를 검증하며, 불일치하면 하위 Agent·Tool을 호출하지 않고 Judge 요청 전체를 종료한다.

#### 외부 API 오류 응답

Prepare/Judge가 노드를 호출하기 전에 요청을 거부하면 정상 응답 대신 다음 공통 오류 형식을 사용한다.

```json
{
  "schema_version": "2.0",
  "trace_id": "0199a2f0-0000-7000-8000-000000000001",
  "error": {
    "code": "prepared_items_hash_mismatch",
    "message": "Prepare 결과 또는 안전 신호가 변경되었습니다.",
    "retryable": false,
    "details": {
      "field": "prepared_items_hash",
      "item_id": null
    }
  }
}
```

| HTTP | `error.code` | 사용 조건 |
|---|---|---|
| 422 | `request_validation_failed` | 필수 필드·타입·enum·closed schema 위반 |
| 422 | `bundle_miss` | 필수 slice 또는 item/candidate 대응 누락 |
| 409 | `prepare_context_mismatch` | `prepare_id`, `scan_session_id`, `store_id` 연결 불일치 |
| 409 | `prepared_items_hash_mismatch` | Prepare 항목 또는 안전 신호가 변경됨 |
| 409 | `bundle_schema_version_mismatch` | 현재값 `2.0`과 Bundle schema version 불일치 |

slice의 status가 `unavailable`인 것은 요청 형식 오류가 아니므로 HTTP 오류로 종료하지 않는다. safety slice이면 Judge를 계속 실행하되 해당 메뉴의 warning·`partial_failed` 상태를 남기고 SAFE를 금지한다. 웹 캐시·Curation 전용 slice이면 각각 새 웹검색·추천 비활성화 fallback을 사용하며, 그 장애만으로 이미 완료된 위험 판정을 낮추거나 실패 처리하지 않는다.

#### `/v1/analyze/judge` 응답 — `JudgeResponse`

```json
{
  "schema_version": "2.0",
  "trace_id": "0199a2f0-0000-7000-8000-000000000001",
  "prepare_id": "0199a2f0-0000-7000-8000-000000000101",
  "scan_session_id": "scan-123",
  "store_id": 123456,
  "prepared_items_hash": "sha256:8d4f000000000000000000000000000000000000000000000000000000000001",
  "bundle_hash": "sha256:74ab000000000000000000000000000000000000000000000000000000000002",
  "knowledge_revision": "2026-10-12T03:00:00Z",
  "items": [
    {
      "item_id": "scan-123:0",
      "source_index": 0,
      "menu_id": 1001,
      "menu_category_id": 11,
      "normalization_status": "normalized",
      "match_type": "canonical",
      "selection_method": "deterministic",
      "status": "completed",
      "route": "exact_only",
      "risk_level": "danger",
      "evidence_basis": "confirmed",
      "information_status": "complete",
      "risk_ingredients": [
        {
          "ingredient": {
            "ingredient_id": 101,
            "canonical_name": "돼지고기",
            "localized_name": "돼지고기"
          },
          "constraint_tags": ["is_pork"],
          "matched_tags": ["is_pork"],
          "role": "identity",
          "status": "present",
          "posterior_mean": null,
          "confidence": null,
          "prior_source": null,
          "evidence_refs": ["evidence-001"]
        }
      ],
      "matched_tags": ["is_pork"],
      "evidence_refs": ["evidence-001"],
      "message": "돼지고기 성분이 포함되어 있어요.",
      "owner_cards": [],
      "warnings": [],
      "errors": []
    }
  ],
  "menu_order": ["scan-123:0"],
  "recommendations": [],
  "evidence": [],
  "persistence_commands": [],
  "warnings": [],
  "errors": []
}
```

`items[]`는 `source_index` 순서로 반환하고, `menu_order`는 ⑨가 정한 표시 순서다. 백엔드는 보관한 Prepare 결과의 `display_name`, `description`, `price_text`, `price_options`를 `item_id`로 합쳐 화면 응답을 만든다. JudgeResponse는 `raw_menu_name`, `normalized_menu_name`과 이 표시용 필드를 중복 반환하지 않는다. `evidence`에는 `items[].evidence_refs`와 저장 명령이 참조하는 모든 `EvidenceRef`를 중복 없이 넣는다. `persistence_commands`는 §4-6의 `DbUpdateResponse.commands[]`를 누적한 배열이며 실제 DB 저장 성공을 뜻하지 않는다.

⑨의 내부 결과 중 `recommendations[]`에는 외부 계약에 필요한 `rank`, `item_id`, `menu_id`, `reason_codes`, `culture_contents`만 넣는다. 내부 정렬용 `menu_name`, `recommendation_score`, 현재 기능이 없는 `sponsored`는 JudgeResponse에 노출하지 않는다.

단일 `AnalyzeRequest`·`AnalyzeResponse` 모델은 만들지 않는다. 네 외부 모델과 KnowledgeBundle은 이 절을 기준으로 구현하고, §4의 내부 Agent·Tool 계약을 외부 API payload로 그대로 노출하지 않는다.

공통 식별자와 `warnings`·`errors`는 §1-2를 따르고, 메뉴별 결과의 `status`는 `completed | partial_failed | failed`, `route`는 §5의 다섯 값 중 하나를 사용한다.

`JudgeResponse.items[]`와 내부 `ItemResult`의 핵심 필드는 다음처럼 고정한다.

| 필드 | 타입 | 규칙 |
|---|---|---|
| `menu_id` | integer \| null | DB에 매핑된 메뉴는 정수 ID, 미등록·미매칭 메뉴는 null |
| `menu_category_id` | integer \| null | DB 메뉴 분류가 있으면 정수 ID, 없거나 미매칭이면 null |
| `normalization_status` | `normalized \| ambiguous \| unmatched \| invalid` | ②-B의 최종 정규화·매칭 상태 |
| `match_type` | `canonical \| spacing \| synonym \| ocr_correction \| fuzzy \| null` | 선택 후보의 조회 방식. 최종 후보가 없으면 null |
| `selection_method` | `deterministic \| llm \| none` | 최종 후보 선택 방식 |
| `evidence_basis` | `confirmed \| estimated \| unknown` | 확정 근거, 추정 근거, 근거 부족을 구분 |
| `risk_ingredients[].ingredient.ingredient_id` | integer \| null | 표준 재료 ID. 미매핑 재료는 null |
| `risk_ingredients[].ingredient.canonical_name` | string | 한국어 표준 재료명 |
| `risk_ingredients[].ingredient.localized_name` | string | 요청 `locale`의 재료명. `locale: ko`이면 `canonical_name`과 같은 값 |
| `risk_ingredients[].role` | `identity \| component \| hidden \| null` | 주재료·속재료·숨은 재료 구분. 판정에는 사용하지 않음 |
| `risk_ingredients[].confidence` | number \| null | ⑤의 0~1 근거 충분도. 확정값이면 null |
| `risk_ingredients[].prior_source` | `store \| cluster \| global \| uninformative \| null` | ⑤ prior의 fallback 출처. 확정값이면 null |
| `owner_cards[].round` | `1 \| 2 \| 3` | 각 사장님 질문의 차수. 질문이 없으면 owner_cards는 빈 배열 |
| `owner_cards[].item_id` | string | 카드가 속한 Prepare 메뉴 항목 ID |
| `owner_cards[].card_id` | string | 카드와 질문 저장 명령을 잇는 Supervisor 발급 ID |
| `owner_cards[].previous_question_id` | string \| null | 1차는 null, 2·3차는 직전 질문 ID |
| `owner_cards[].ingredient` | object \| null | 재료 카드면 물어볼 재료, 메뉴 애매함 플래그 카드면 null (§4-7) |
| `owner_cards[].ambiguity_flag` | string \| null | 메뉴 애매함 플래그 카드일 때만 플래그 이름 |

`high_risk_review`와 `risk_ingredients[].anomaly_locked`는 Judge Graph 내부에서 재검증·SAFE 차단·질문 생성에 사용하지만 최종 JudgeResponse에는 노출하지 않는다. `risk_ingredients[].role`, `confidence`, `prior_source`는 사용자 설명에 필요하므로 외부 응답에 유지한다.

`items[].message`는 요청 `locale`로 작성한 단일 string이다. 판정 결과에는 다른 언어 번역을 함께 넣지 않는다. 반면 `owner_cards[].question`은 가게에서 손님과 사장님이 함께 보는 문구이므로 한국어와 요청 `locale`만 담는다. 요청 언어가 한국어면 `ko` 하나만 담는다.

`owner_question_round`는 `owner_cards[].round`와 중복되므로 만들지 않는다. `risk_ingredients[].confirmation`도 기존 `status`, `evidence_refs`, `EvidenceRef.observed_at`, ③의 `expired`와 중복되므로 만들지 않는다.

---

## 2. 처리 로직

```mermaid
flowchart TD
    subgraph P["Prepare Graph — AI 내부 확정"]
        IN["Prepare 입력"] --> STORE{"공통 문맥과 store_id가 유효한가?"}
        STORE -->|No| ERR["입력 오류<br/>①~⑨ 호출 금지"]
        STORE -->|Yes| OCR["① OCR Tool<br/>메뉴명·가격·설명 구조화"]
        OCR --> QUALITY{"needs_retake?"}
        QUALITY -->|Yes| RETAKE["재촬영 결과<br/>②~⑨ 호출 금지"]
        QUALITY -->|No| CLEAN["②-A 메뉴명 정제<br/>DB 식별자 확정 금지"]
        CLEAN --> PREPARED["prepared_items 반환"]
    end

    PREPARED -. "백엔드 후보 조회·KnowledgeBundle 구성\n§1-3 JSON 계약 사용" .-> JUDGE_IN

    subgraph J["Judge Graph — AI 내부 확정"]
    JUDGE_IN["Judge 입력 검증<br/>prepared_items + 지식 데이터"] --> MATCH["②-B 후보 해석·최종 선택"]
    MATCH --> LOOP["메뉴별 Item Graph fan-out"]
    LOOP --> MID{"menu_id 후보 있음?"}
    MID -->|Yes| EXACT["③ Exact Feedback Tool 조회"]
    MID -->|No| ONTO_UNKNOWN["④ DB / Ontology Tool<br/>Bundle 기반 unknown/longest-match"]
    EXACT --> COMP{"completeness: complete<br/>AND override 불가 확인값 없음?"}
    COMP -->|Yes| TAG1["④ lookup_tags<br/>확인값 태그 조회"]
    TAG1 --> XAI1["⑧ Decision Policy / XAI Agent<br/>confirmed_results만 전달"]
    COMP -->|No| ONTO["④ DB / Ontology Tool<br/>Bundle 읽기·재귀 확장"]

    ONTO --> DBOK{"exists_in_db OR<br/>base_menu_id 있음?"}
    ONTO_UNKNOWN --> DBOK
    DBOK -->|Yes| BASE{"base_menu_id 있음?"}
    BASE -->|Yes| INHERIT["exact_inherited 보류 node<br/>③ 재호출 없이 통과"]
    BASE -->|No| BAYES["⑤ Bayesian Tool"]
    INHERIT --> BAYES
    BAYES --> TAG2["④ lookup_tags<br/>확인값 태그 조회"]
    TAG2 --> XAI2["⑧ Decision Policy / XAI Agent"]

    DBOK -->|No| WEB["⑥ Web Search Agent"]
    WEB --> FOUND{"수집·캐시 결과가 있는가?"}
    FOUND -->|Yes| MAP["④ map_ingredients<br/>표준 재료·태그 매핑"]
    MAP --> WAGG["⑥ Web Search Agent 2단계<br/>표준 재료 기준 집계"]
    WAGG --> DBUP2["⑦ DB Update Tool<br/>관리자 검토 명령 생성"]
    DBUP2 --> BAYES2["⑤ Bayesian Tool<br/>웹서치 후보 기반"]
    BAYES2 --> TAG3["④ lookup_tags<br/>확인값 태그 조회"]
    TAG3 --> XAI3["⑧ Decision Policy / XAI Agent"]
    FOUND -->|No| XAI4["⑧ Decision Policy / XAI Agent<br/>no_information: true"]

    ONTO -.soft evidence.-> DBUP1["⑦ DB Update Tool<br/>관리자 검토 항목 생성"]
    WEB -.soft evidence.-> DBUP1
    XAI1 --> OUT["메뉴별 결과 취합"]
    XAI2 --> OUT
    XAI3 --> OUT
    XAI4 --> OUT
    OUT --> CUR["⑨ Curation Tool<br/>안전 후보 추천"]
    CUR --> USER["Judge 결과 반환"]
    end
```

위 그림의 **AI 내부 실행 경계와 순서**, `/v1/analyze/prepare`, `/v1/analyze/judge` 경로와 §1-3의 외부 계약은 확정이다. `②-A`와 `②-B`는 별도 Agent 두 개가 아니라 같은 Normalization 책임을 두 operation으로 나눈 이름이다.

### 2-1. 기본 순서

**Prepare Graph**

1. 요청 schema와 백엔드가 확정한 `store_id`를 검증한다.
2. 이미지 참조와 실행 문맥을 state에 보존하고 ①을 호출한다.
3. ① 결과에 안정적인 `item_id = {scan_session_id}:{source_index}`를 한 번만 부여한다.
4. `scan_quality.status: needs_retake`면 ②~⑨를 호출하지 않고 재촬영 결과로 종료한다.
5. `low_confidence`면 원문과 경고를 보존하고 진행하되 이후 SAFE 제한 신호가 사라지지 않게 한다.
6. ②-A는 OCR 노이즈, 띄어쓰기, 인원·크기 옵션, 다국어 표기를 정리해 `normalized_menu_name`을 만든다. 이 단계에서는 DB 후보가 없으므로 `menu_id`를 확정하지 않는다.
7. §1-3의 `PrepareResponse`로 `scan_quality`와 `prepared_items`를 OCR 순서대로 반환한다. 백엔드는 `prepare_id`와 함께 저장하고 메뉴 후보·KnowledgeBundle을 조회한다.

**Judge Graph**

1. Prepare 결과와 백엔드가 제공한 지식 데이터의 `prepare_id`, `prepared_items_hash`, `prepare_signals`, `knowledge_revision`, item 대응 관계를 검증하고, 수신한 KnowledgeBundle의 `bundle_hash`를 계산한다.
2. ②-B가 허용된 메뉴 후보 안에서 최종 후보를 선택한다. 후보 밖 `menu_id`는 만들 수 없다.
3. 메뉴마다 Item Graph를 fan-out한다. `menu_id`가 있으면 ③, 없으면 ④ unknown 경로에서 시작한다.
4. ③이 `completeness: complete`이고 `override_eligible: false`가 없으면 확장·확률 계산은 건너뛴다. `unknown`이거나 override 불가 확인값이 있으면 ④→⑤ heavy path로 간다.
5. ④가 exact DB 메뉴 또는 `base_menu_id`를 찾으면 ⑤로 간다. `exact_inherited` 단계는 이름만 유지하되 현재는 보류 node로 통과하며 ③을 다시 호출하거나 사장님 답변을 ⑤에 전달하지 않는다. 둘 다 찾지 못하면 ⑥으로 간다.
6. ⑥ 1단계가 출처별 재료명을 수집하면 ④가 표준 재료·태그로 매핑하고, ⑥ 2단계가 표준 재료 기준으로 집계한다. 수집 결과와 캐시가 모두 없을 때만 ⑧에 `no_information: true`를 전달한다.
7. ⑧을 호출하기 직전에 모든 경로가 ④ `lookup_tags`를 거친다. ③ 확인값이 없으면 no-op이고, 있으면 `confirmed_results[].constraint_tags`를 채운다.
8. 각 Item Graph의 오류는 해당 메뉴를 `partial_failed` 또는 `failed`로 만들고 SAFE를 금지한다. 다른 메뉴의 실행은 취소하지 않는다.
9. 결과를 `source_index`로 정렬하고 근거·경고·저장 명령을 중복 제거한 뒤 SAFE 후보 유무와 관계없이 ⑨를 호출한다. ⑨는 판정을 변경할 수 없다.
10. 판정, 근거, 위험 재료, 확인 질문, 전체 메뉴 순서, 추천, 저장 명령, 오류를 Judge 결과로 반환한다.

### 2-2. 배치 처리 원칙

①과 ②-A는 Prepare Graph에서 메뉴판 1장 단위로 실행한다. ②-B는 Judge Graph 진입 시 후보가 포함된 메뉴 목록을 배치로 해석한다. ③④⑤⑧은 메뉴별 호출을 기본으로 하되, 구현에서 같은 타입의 호출을 배치로 최적화할 수 있다.

웹서치(⑥)는 DB에 없는 메뉴에만 호출한다. OCR 결과 전체에 대해 무조건 웹서치를 돌리지 않는다.

⑥을 동시에 실행하는 메뉴는 초기값 **최대 5개**로 제한한다. 6번째 메뉴부터는 앞선 웹서치가 끝나는 순서대로 실행한다. Judge 제한 시간 안에 실행 차례가 오지 않으면 캐시가 있을 때는 캐시만으로 계속 처리하고, 캐시도 없으면 `found: false`, `no_information: true`로 보수 처리한다. 이 제한은 설정값 `max_concurrent_web_searches`로 두되 기본값과 상한은 모두 5다.

LangGraph 구현에서는 Judge Graph가 메뉴 항목을 fan-out해 독립 처리한 뒤 `item_id`와 OCR 순서로 collect한다. 병렬 처리는 같은 메뉴의 상태를 공유하지 않으며, 한 항목의 실패가 다른 항목을 취소하지 않게 한다.

### 2-3. StateGraph 상태 모델

실행 중 바뀌면 안 되는 ID는 state 바깥의 run-scoped context에 둔다.

```json
{
  "schema_version": "2.0",
  "trace_id": "0199a2f0-0000-7000-8000-000000000001",
  "scan_session_id": "scan-123",
  "store_id": 123456
}
```

Prepare Graph의 누적 state는 다음 모양이다.

```json
{
  "image": {
    "source": "camera",
    "storage_key": "scans/.../menu.jpg",
    "image_url": null,
    "version_id": null,
    "expected_etag": null
  },
  "locale": "ko",
  "ocr_response": null,
  "prepared_items": [],
  "warnings": [],
  "errors": []
}
```

Judge Graph의 누적 state는 다음 모양이다. `knowledge_bundle`은 §1-3 외부 계약을 검증한 뒤 내부 adapter가 필요한 slice를 읽는다.

```json
{
  "prepared_items": [],
  "knowledge_bundle": {
    "bundle_schema_version": "2.0",
    "items": [],
    "source_reliability": {
      "status": "not_found",
      "weights": []
    },
    "curation_index": {
      "status": "not_found",
      "contents": []
    }
  },
  "locale": "ko",
  "curation_options": {
    "limit": 5
  },
  "candidate_selection_response": null,
  "item_updates": [],
  "ordered_items": [],
  "evidence": {},
  "recommendations": [],
  "persistence_commands": [],
  "warnings": [],
  "errors": []
}
```

- Prepare Graph는 `image`, OCR 원문, `source_index`, `item_id`, 품질 경고를 보존한다.
- Judge Graph는 전달받은 `prepared_items`를 다시 OCR하거나 이름을 임의로 바꾸지 않는다.
- `ocr_response.scan_quality`는 재촬영 여부와 사용자 안내에 사용한다.
- `item_updates`는 병렬 Item Graph가 reducer로 append하는 channel이다.
- `ordered_items`는 collect 후 `source_index` 순서로 정렬된 최종 메뉴 배열이다.
- `evidence`는 `evidence_id`를 키로 중복 제거한 근거 레지스트리다. 각 노드의 `evidence_refs`는 반드시 이 레지스트리에 존재하는 ID만 가리킨다.
- 각 node는 자신의 결과 필드만 갱신하며 이전 근거를 삭제하거나 재해석하지 않는다.
- `status`와 `route`는 각 `ItemResult` 안에서 §1-3과 §5의 enum을 사용한다.

### 2-4. Graph를 세 단위로 나누는 방법

OCR·재촬영 경계와 DB 지식이 필요한 판정 경계를 분리하기 위해 다음 세 단위로 나눈다.

```text
Prepare Graph — 이미지에서 판정 준비 자료 생성
  입력 검증 → ① OCR → 품질 분기 → ②-A 메뉴명 정제 → prepared_items

Judge Graph — 준비 자료와 지식 데이터로 전체 판정
  입력 검증 → ②-B 후보 선택 → 메뉴별 Item Graph 병렬 실행
            → 결과 취합 → ⑨ 큐레이션 → 최종 응답

Item Graph — 메뉴 하나
  ③ Exact → ④ Ontology → ⑤ Bayesian / ⑥ Web Search
           → 필요 시 ⑦ 저장 명령 → ⑧ Decision
           → 필요 시 ⑦ 사장님 질문 저장 명령
```

- **Prepare Graph**는 이미지·OCR 품질·OCR 순서·메뉴명 정제 결과를 소유한다.
- **Judge Graph**는 사용자 프로필·지식 데이터·메뉴별 실행·근거 취합·최종 추천을 소유한다.
- **Item Graph**는 반드시 메뉴 하나만 처리한다. 다른 메뉴 state를 읽거나 수정하지 않는다.
- Item Graph의 최종 출력은 하나의 `ItemResult`다.
- Judge Graph는 여러 `ItemResult`를 reducer로 모은 뒤 `source_index` 순으로 정렬한다.
- Item Graph 안에서 Agent·Tool을 직접 서로 호출하지 않는다. 각 node가 adapter를 호출하고 다음 node는 state에 저장된 결과를 읽는다.

이렇게 나누면 메뉴 10개가 있어도 같은 Item Graph를 10번 병렬 실행할 수 있고, 한 메뉴가 실패해도 나머지 9개 결과를 유지할 수 있다.

### 2-5. 구현 모듈 경계

구현 파일명은 달라질 수 있지만 책임은 다음처럼 분리한다.

```text
ai_supervisor/
├── service.py          # Prepare/Judge 서비스 진입점
├── prepare_graph.py    # ① → 품질 분기 → ②-A
├── judge_graph.py      # 검증 → ②-B → fan-out → collect → ⑨
├── item_graph.py       # 메뉴 하나의 ③~⑧ 흐름
├── state.py            # RunContext와 reducer
├── routers.py          # 부작용 없는 조건 분기
├── adapters.py         # 기존 모듈을 §4 JSON으로 변환
├── contracts/          # §1·§4 Pydantic closed schema
└── chains/             # ②·⑥·⑧의 제한적 LangChain
```

기존 `ai_ocr`, `ai_ruleengine`, `ai_web_search_agent`, `ai_result` 코드를 복사하지 않는다. adapter가 기존 모듈의 입력·출력을 §4 계약으로 바꿔 호출한다.

### 2-6. 경계 모델과 내부 state

- 외부 API와 ①~⑨ wire JSON은 Pydantic `extra="forbid"` 모델을 사용한다.
- graph 누적 state는 `TypedDict`를 사용하고 node는 변경한 필드만 반환한다.
- `RunContext`는 frozen 타입이며 `schema_version`, `trace_id`, `scan_session_id`, `store_id`를 보관한다.
- node 호출 때만 `RunContext + call_scope + item_id`로 `NodeContext`를 만든다.
- Prepare/Judge는 별도 HTTP 호출이지만 같은 trace·scan·store 식별자를 사용한다.

| 내부 타입 | 필수 핵심 값 |
|---|---|
| `PrepareState` | image, locale, OCR response, scan quality, prepared items, warnings/errors |
| `JudgeState` | prepared items, KnowledgeBundle, profile, candidate result, item updates, ordered items, evidence, commands |
| `ItemTask` | normalized item, 같은 `item_id`의 `ItemKnowledge`, 공통 source reliability, profile, locale |
| `ItemState` | ItemTask 값 + exact/ontology/bayesian/web/decision 응답 + route + commands |
| `ItemResult` | 메뉴 하나의 최종 판정·근거·경고·저장 명령 |

`ItemKnowledge`는 `KnowledgeBundle.items[]`에서 같은 `item_id` 한 건만 떼어낸 read-only view다. Item Graph는 다른 메뉴 자료를 볼 수 없다. ②-B가 후보를 선택하면 adapter가 같은 `menu_id`의 exact·ontology·priors를 사용하고, 미매칭이면 `fallback_ontology`를 사용한다. Bundle 공통 `ingredient_graph`는 모든 Item Graph가 변경 없이 공유하고 ④ adapter에만 전달한다.

### 2-7. Adapter와 node 규칙

번호별 adapter는 다음 형태를 따른다.

```python
class NodeAdapter(Protocol):
    async def run(self, request: NodeRequest) -> NodeResponse: ...
```

Normalization은 `clean()`과 `select()`, Web Search는 `collect()`과 `aggregate()`처럼 operation이 다른 메서드만 분리한다. 실제 client는 FastAPI lifespan에서 한 번 만들고 의존성 컨테이너에 주입한다.

Adapter의 공통 순서는 다음과 같다.

1. state와 `RunContext`로 §4 Request를 만든다.
2. Pydantic JSON을 기존 Python 함수 또는 HTTP API에 전달한다.
3. 받은 값을 §4 Response 모델로 검증한다.
4. 응답 `context`가 요청과 같은지 검사한다.
5. `evidence_refs`, 3-State, tags, `anomaly_locked` 보존 여부를 검사한다.
6. 검증된 모델만 node가 state update로 반환한다.

node는 `async def node(state, runtime) -> dict` 형태로 자기 결과 필드만 반환한다. 다음을 금지한다.

- 입력 state 직접 수정 또는 전체 state 복사 반환
- node 안에서 다음 node 직접 호출
- node 안에서 식별자 생성·변경
- 검증 전 dict를 state에 저장
- 예외를 빈 배열이나 SAFE 결과로 숨김

### 2-8. Router와 Item Graph

Router는 외부 호출이나 LLM 호출이 없는 순수 함수다.

| 현재 node | 조건 | 다음 node |
|---|---|---|
| `prepare_item` | `menu_id != null` / null | `exact` / `ontology` |
| `exact` | complete이고 override 불가 없음 | `tag_lookup` |
| `exact` | unknown 또는 override 불가 포함 | `ontology` |
| `ontology` | response menu가 null | `web_search` |
| `ontology` | runtime 변형 | `variant_review` |
| `ontology` | `base_menu_id != null` | `exact_inherited` 보류 node |
| `ontology` | exact/base DB 근거 있음 | `bayesian` |
| `ontology` | exact/base 모두 없음 | `web_search` |
| `web_search` | 신규·캐시 출처 있음 / 모두 없음 | `ingredient_mapping` / `decision_no_information` |
| `ingredient_mapping` | 항상 | `web_aggregate` |
| `web_aggregate` | 항상 | `web_review` |
| `variant_review / web_review` | 항상 | `bayesian` |
| `bayesian` | 항상 | `tag_lookup` |
| `tag_lookup` | 항상 | `decision` |
| `decision*` | 저장할 owner card 있음 / 없음 (화면 표시용 카드만 있으면 없음) | `owner_request` / `finish_item` |

`variant_review`, `web_review`, `owner_request`는 모두 ⑦ adapter를 사용하지만 trace에서 원인을 구분하기 위해 node 이름을 나눈다. `decision_no_information`은 같은 ⑧ adapter에 `no_information: true`를 보내는 node다.

`exact_inherited`는 현재 ③을 재호출하지 않고 다음 update만 반환한다.

```python
return {"inherited_confirmations": []}
```

### 2-9. 메뉴 fan-out과 collect

Judge는 ②-B 결과를 메뉴별 `Send("analyze_item", task)`로 fan-out한다. task에는 메뉴와 같은 `item_id`의 지식 slice만 넣는다. source reliability는 모든 task가 read-only로 공유한다.

병렬 결과는 `item_updates` reducer에 append하고, collect에서 다음 순서로 처리한다.

1. `source_index` 오름차순 정렬
2. evidence ID 중복 제거
3. `idempotency_key` 기준 저장 명령 중복 제거
4. `(code, node, item_id)` 기준 warning/error 중복 제거
5. `ordered_items`에 저장

누적 channel인 `item_updates`를 정렬 결과로 덮어쓰지 않는다. 최종 channel `ordered_items`를 따로 사용해 reducer가 결과를 다시 append하는 문제를 막는다.

한 Item Graph가 실패해도 다른 task는 취소하지 않는다. 실패한 메뉴만 보수적 `ItemResult`로 만들고 전체 메뉴판 결과는 collect한다.

### 2-10. Graph 조립 순서

```text
Prepare Graph
START → ocr → (needs_retake ? finalize_retake : clean_names)
      → finalize_prepared → END

Judge Graph
START → validate_knowledge → select_candidates
      → analyze_item fan-out → collect_items
      → high_risk_review(max 3) → curation → finalize → END

Item Graph
START → prepare_item → §2-8의 조건 분기
      → decision → 필요 시 owner_request → finish_item → END
```

`validate_knowledge`는 DB를 조회하지 않고 §1-3의 ID·status·hash·item/candidate 대응만 검사한다. graph는 애플리케이션 시작 때 한 번 compile하고 초기 버전은 영속 checkpointer를 쓰지 않는다.

collect 뒤 `high_risk_review.required: true`인 메뉴 중 최대 3개를 `anomaly → unknown_tag → no_information → partial_information → insufficient_web_sources → source_index` 순으로 고른다. 재검증은 판정을 바꾸지 않고 질문 카드와 문구만 보강한다. 재검증 대상은 이미 SAFE가 금지된 메뉴이고 DANGER는 사장님 확인값으로만 나오므로 판정을 바꿀 경우가 없다 (⑧ §2-3, #234). timeout이면 기존 결과를 유지한다.

### 2-11. LangChain 사용 경계

②·⑥·⑧은 `prompt → temperature 0 chat model → Pydantic structured output` 형태만 사용한다. 자유 텍스트를 `json.loads()`로 억지 파싱하거나 ReAct/`create_agent()` loop를 사용하지 않는다.

- ②-A: 규칙·사전으로 해석되지 않는 다국어 이름만 보조
- ②-B: 백엔드 후보 안의 모호한 선택만 보조
- ⑥: 검색어와 검색 결과 속 재료 추출 보조
- ⑧: Python 정책이 확정한 판정을 다국어 문장으로 표현

모든 LLM 출력은 후보·ID·enum 범위를 다시 검증한다. 실패하면 정해진 규칙 결과나 고정 문구로 fallback하며 LLM이 graph 경로나 위험 판정을 정하지 않는다.

### 2-12. FastAPI 연결

외부 router는 다음 두 서비스 메서드만 호출한다.

```python
await supervisor.prepare(prepare_input, run_context)
await supervisor.judge(judge_input, run_context)
```

등록 경로는 `POST /v1/analyze/prepare`와 `POST /v1/analyze/judge`뿐이다. endpoint는 §1-3 모델을 검증하고 graph 입력으로 변환한 뒤 결과를 외부 응답 모델로 다시 검증한다.

Prepare와 Judge 사이 상태는 AI 메모리나 checkpoint로 공유하지 않는다. 백엔드가 Prepare 결과와 KnowledgeBundle을 Judge에 다시 보내야 한다.

### 2-13. Timeout·retry·fallback 구현 위치

| node | 자동 retry | timeout 후 fallback |
|---|---|---|
| ① OCR | 읽기 요청에 한해 최대 1회 재시도 | `needs_retake` 또는 OCR error 응답 |
| ② Normalization | LLM transient error 1회 | 규칙 기반 후보·원문 보존 |
| ③ Exact | 재시도 없음 (Bundle만 읽음) | ④ heavy path |
| ④ Ontology | 재시도 없음 (Bundle만 읽고 결정론적으로 확장) | 입력·확장 오류 시 partial + ⑥/⑧ 보수 경로 |
| ⑤ Bayesian | 재시도 없음 | unknown + partial |
| ⑥ Web Search | 전체 7초 안에서 provider transient error 1회 | 수집 결과나 캐시가 있으면 부분 결과 + `web_search_deadline_exceeded`; 둘 다 없을 때만 `found: false`, `no_information: true` |
| ⑦ DB Update | Supervisor 자동 재시도 금지 | 판정 유지 + command 생성 error |
| ⑧ Decision | 정책 함수 재시도 없음, 문장 LLM 1회 | 보수 판정 유지 + 고정 문장 템플릿 |
| ⑨ Curation | 재시도 없음 (넘겨받은 `curation_contents`만 사용) | `recommendations: []` |

SDK·adapter·graph 세 계층이 동시에 재시도하지 않는다. 실제 재시도 책임은 adapter 한 곳에만 둔다. 예상한 timeout·validation 오류는 `NodeError`로 변환하고, 프로그래밍 오류나 invariant 위반은 숨기지 말고 요청 전체를 실패시켜 로그에서 바로 발견할 수 있게 한다.

### 2-14. 이 설계대로 구현됐는지 확인하는 기준

- `build_prepare_graph(fake_deps)`와 `build_judge_graph(fake_deps)`가 외부 서비스 없이 compile된다.
- 각 `graph.get_graph()`에서 Prepare/Judge Graph의 node와 edge를 확인할 수 있다.
- router 단위 테스트가 모든 반환 node 이름을 검증한다.
- 메뉴 3개를 서로 다른 지연시간으로 처리해도 최종 `items`가 `source_index` 순서다.
- 한 Item Graph가 timeout이어도 다른 Item Graph 결과가 사라지지 않는다.
- ②·⑥·⑧ LLM이 schema 밖 필드를 반환하면 Pydantic validation으로 거부된다.
- LLM을 전부 실패시키는 fake를 넣어도 SAFE 쪽으로 fallback하지 않는다.
- node 전후의 `store_id`, `trace_id`, `scan_session_id`가 동일하다.
- Prepare가 끝난 뒤 process memory를 비워도 저장된 `prepared_items`로 Judge를 다시 실행할 수 있다.
- `prepared_items`의 `item_id`·`source_index`가 Judge 최종 결과까지 유지된다.
- `evidence_refs`가 evidence registry에 없는 ID를 가리키면 finalize가 응답을 만들지 않는다.
- 같은 ⑦ operation을 두 번 생성해도 `idempotency_key`가 같다.

### 2-15. 구현 참고 자료

- [LangGraph `StateGraph` 공식 API](https://reference.langchain.com/python/langgraph/graph/state/StateGraph)
- [LangGraph graph module과 START/END](https://reference.langchain.com/python/langgraph/graph)
- [LangGraph `compile()`과 checkpointer](https://reference.langchain.com/python/langgraph/graph/state/StateGraph/compile)
- [LangChain·LangGraph 학습 경로](https://docs.langchain.com/oss/python/learn)

---

## 3. 가게 컨텍스트 계약

### 3-1. 책임 경계

| 주체 | 책임 |
|---|---|
| 백엔드 | GPS 동의/입력 처리, 공공데이터 후보 조회, Kakao fallback, 사용자 선택, 기존 공공데이터 가게 매칭, `scan_sessions` 연결 및 스냅샷 저장 |
| AI Supervisor | 전달받은 `store_id` 형식 검증, 동일 값을 ①~⑨ 호출에 전파, 분석 결과에 그대로 반환 |

백엔드는 AI 호출 전에 `stores.id`의 존재와 `status='active'`를 검증한다. AI는 가게 마스터 DB를 조회·생성·수정하지 않는다. 가게가 선택되지 않은 요청은 백엔드가 거절하므로 AI에는 GPS, 검색어, 가게 후보 목록 계약을 두지 않는다.

### 3-2. 타입 계약

| 경계 | 타입 |
|---|---|
| PostgreSQL | `BIGINT` |
| Spring Boot | `Long` |
| JSON | number (정수) |
| Python | `int` |

`null`, 문자열 숫자(`"123"`), 0, 음수는 모두 `StoreIdRequiredError` 대상이다. 이 검증은 다른 가게의 데이터로 fallback하는 것보다 요청을 실패시키는 편이 안전하다는 FN 최소화 원칙을 따른다.

---

## 4. 하위 Agent·Tool 호출 계약

각 요청·응답은 §1-2의 공통 모델을 사용한다. 아래 JSON이 ①~⑨ 경계의 canonical wire contract다.

- 모든 응답은 입력 `context`를 그대로 반환한다.
- `warnings`와 `errors`는 항상 배열로 존재한다.
- `ingredient`, `name`, 재료명 문자열만 단독으로 보내는 형식은 사용하지 않고 `IngredientRef`로 통일한다.
- Supervisor는 한 노드의 응답을 다음 노드 요청으로 조합하지만 근거·경고·식별자를 삭제하지 않는다.
- `evidence` 객체를 만든 ①·③·④·⑥ 응답과 ④의 `variant_suggestion`을 받는 즉시 Supervisor가 state의 evidence registry에 등록한다. 이후 `evidence_refs`는 등록된 ID만 사용한다.

### 4-0. ① OCR Tool

#### 요청 — `OcrRequest`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "scan",
    "item_id": null
  },
  "image": {
    "source": "camera",
    "storage_key": "scans/menu.jpg",
    "image_url": null,
    "version_id": null,
    "expected_etag": null
  },
  "warnings": [],
  "errors": []
}
```

#### 응답 — `OcrResponse`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "scan",
    "item_id": null
  },
  "scan_quality": {
    "status": "usable",
    "score": 0.94,
    "reasons": [],
    "retake_suggestions": []
  },
  "items": [
    {
      "item_id": "scan-123:0",
      "source_index": 0,
      "raw_menu_name": "■ 김치 찌개",
      "description": null,
      "price_text": "8,000원",
      "price_options": [],
      "ocr_confidence": 0.93,
      "locale_hint": "ko",
      "risk_tag_observations": [
        {
          "tag": "is_spicy",
          "confidence": 0.78,
          "evidence": {
            "evidence_id": "ocr:scan-123:0:is_spicy",
            "evidence_class": "soft",
            "source_type": "ocr_text_hint",
            "source_ref": "scan-123:0",
            "verification_status": "inferred",
            "reliability_weight": 0.4,
            "observed_at": "2026-10-08T03:00:00Z"
          }
        }
      ]
    }
  ],
  "warnings": [],
  "errors": []
}
```

Supervisor의 OCR adapter가 기존 OCR 출력의 `menu_name_ko`, `description_ko`, `price_text`, `price_options`, `display_order`, `scan_quality`, `is_spicy`를 위 형식으로 변환한다. 기존 품질 점수가 0~100이면 0~1로 나눠 변환한다. `item_id`는 `scan_session_id:source_index`로 한 번만 발급한다. OCR은 메뉴명을 의미적으로 정규화하지 않는다.

`price_options` 원소는 `{ "label": string, "price_text": string, "inferred": boolean }`이다. 설명이나 가격을 읽지 못하면 각각 `description: null`, `price_text: null`로 반환한다. `risk_tag_observations`에는 **긍정적으로 감지한 태그만** 넣으며 감지하지 못한 것을 `absent` 근거로 만들지 않는다. 현재 허용 태그는 `is_spicy`이고, 이 값은 soft evidence이므로 단독으로 DANGER 확정에 사용하지 않는다.

### 4-1. ② Menu Normalization Agent

2-phase 내부 실행에서는 ②의 책임을 두 단계로 분리한다. Prepare의 ②-A는 문자열만 정리하고, Judge의 ②-B는 백엔드가 조회한 허용 후보 안에서 최종 후보를 선택한다.

#### Prepare 요청 — `NameCleaningRequest`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "scan",
    "item_id": null
  },
  "items": [
    {
      "item_id": "scan-123:0",
      "source_index": 0,
      "raw_menu_name": "■ 김치 찌개 2인",
      "description": null,
      "price_text": "8,000원",
      "price_options": [],
      "ocr_confidence": 0.93,
      "locale_hint": "ko",
      "risk_tag_observations": []
    }
  ],
  "warnings": [],
  "errors": []
}
```

#### Prepare 응답 — `NameCleaningResponse`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "scan",
    "item_id": null
  },
  "items": [
    {
      "item_id": "scan-123:0",
      "source_index": 0,
      "raw_menu_name": "■ 김치 찌개 2인",
      "normalized_menu_name": "김치찌개",
      "display_name": "김치 찌개 2인",
      "description": null,
      "price_text": "8,000원",
      "price_options": [],
      "ocr_confidence": 0.93,
      "locale_hint": "ko",
      "options": ["2인"],
      "residual_tokens": [],
      "cleaning_status": "cleaned",
      "risk_tag_observations": []
    }
  ],
  "warnings": [],
  "errors": []
}
```

#### Judge 요청 — `CandidateSelectionRequest`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "scan",
    "item_id": null
  },
  "items": [
    {
      "item_id": "scan-123:0",
      "source_index": 0,
      "raw_menu_name": "■ 김치 찌개 2인",
      "normalized_menu_name": "김치찌개",
      "display_name": "김치 찌개 2인",
      "description": null,
      "price_text": "8,000원",
      "price_options": [],
      "ocr_confidence": 0.93,
      "locale_hint": "ko",
      "options": ["2인"],
      "residual_tokens": [],
      "risk_tag_observations": [],
      "candidate_status": "ok",
      "match_candidates": [
        {
          "menu_id": 1001,
          "menu_category_id": 11,
          "canonical_name": "김치찌개",
          "score": 1.0,
          "match_type": "canonical"
        }
      ]
    }
  ],
  "warnings": [],
  "errors": []
}
```

#### Judge 응답 — `CandidateSelectionResponse`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "scan",
    "item_id": null
  },
  "items": [
    {
      "item_id": "scan-123:0",
      "source_index": 0,
      "raw_menu_name": "■ 김치 찌개 2인",
      "normalized_menu_name": "김치찌개",
      "display_name": "김치 찌개 2인",
      "description": null,
      "price_text": "8,000원",
      "price_options": [],
      "ocr_confidence": 0.93,
      "locale_hint": "ko",
      "options": ["2인"],
      "residual_tokens": [],
      "risk_tag_observations": [],
      "candidate_status": "ok",
      "normalization_status": "normalized",
      "selection_method": "deterministic",
      "selection_reason_codes": ["exact_unique"],
      "selected_candidate": {
        "menu_id": 1001,
        "menu_category_id": 11,
        "canonical_name": "김치찌개",
        "score": 1.0,
        "match_type": "canonical"
      },
      "match_candidates": [
        {
          "menu_id": 1001,
          "menu_category_id": 11,
          "canonical_name": "김치찌개",
          "score": 1.0,
          "match_type": "canonical"
        }
      ]
    }
  ],
  "warnings": [],
  "errors": []
}
```

`cleaning_status`는 `cleaned | unchanged | invalid`다. `cleaned | unchanged`이면 `normalized_menu_name`은 비어 있지 않은 string이고, `invalid`이면 null이다. `invalid` 항목은 ②-A 응답에는 남겨 추적하지만 Supervisor가 `PrepareResponse.prepared_items`를 만들 때 제외하고 item 단위 warning을 보존한다. 유효한 메뉴가 하나도 없으면 `no_menu_items` 오류로 Judge 진입을 막는다.

`candidate_status`는 `ok | not_found | unavailable`이며 ②-B 응답까지 그대로 보존한다. `unavailable`은 후보가 없다는 뜻이 아니라 조회하지 못했다는 뜻이므로 해당 메뉴의 SAFE 판정을 금지한다. `not_found`면 후보 없이 unknown 경로로 진행한다.

`normalization_status`는 `normalized | ambiguous | unmatched | invalid`다. `normalized`일 때만 `selected_candidate`를 채운다. 나머지는 `null`이며 후보는 `match_candidates`에 보존한다. ②-B는 입력에 없는 `menu_id`를 새로 만들거나 선택할 수 없다.

`selection_method`는 `deterministic | llm | none`이다. `selection_reason_codes` 허용값은 `exact_unique | score_and_margin | llm_context_resolved | multiple_close_candidates | below_accept_threshold | candidate_not_found | candidate_unavailable | invalid_input | llm_failed`다. `normalized`이면 method는 `deterministic | llm`, 그 외에는 `none`이다.

`match_type`은 후보를 **어떻게 조회했는지** 나타내는 `canonical | spacing | synonym | ocr_correction | fuzzy`이고, `score`는 0~1이다. match type별 v1 점수와 자동 선택 기준은 ② 문서 §3-2·§4-1을 따른다. LLM 사용 여부는 `match_type`에 섞지 않고 `selection_method`로 구분한다. `match_candidates`는 score 내림차순이며 동점이면 `menu_id` 오름차순으로 정렬한다.

후보의 `menu_id`와 `menu_category_id`는 백엔드 DB의 정수 ID다. 최종 매칭에 실패하면 `selected_candidate` 자체를 `null`로 두며, 최종 `ItemResult`의 두 ID도 `null`을 허용한다.

Supervisor는 ②-A가 반환한 `raw_menu_name`과 `normalized_menu_name`의 매핑을 Judge 처리 완료까지 내부적으로 유지한다. 외부 JudgeResponse에는 두 이름을 중복 반환하지 않고, 백엔드가 보관한 Prepare 결과를 `item_id`로 합쳐 사용자 표시명과 내부 매칭 결과를 연결한다. ②-A와 ②-B는 `risk_tag_observations`를 해석하거나 수정하지 않고 입력 그대로 반환한다. `CandidateSelectionRequest`는 `KnowledgeBundle.items[].candidate_lookup`을 내부 최소 입력 view로 변환하며 후보는 §1-3에 따라 최대 5개다.

### 4-2. ③ Exact Feedback Tool

#### 요청 — `ExactFeedbackRequest`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "item",
    "item_id": "scan-123:0"
  },
  "menu": {
    "menu_id": 1001,
    "menu_category_id": 11,
    "normalized_menu_name": "김치찌개"
  },
  "scope_hint": "exact",
  "warnings": [],
  "errors": []
}
```

#### 응답 — `ExactFeedbackResponse`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "item",
    "item_id": "scan-123:0"
  },
  "menu_id": 1001,
  "confirmation_scope": "exact",
  "completeness": "unknown",
  "confirmations": [
    {
      "ingredient": {
        "ingredient_id": 101,
        "canonical_name": "돼지고기"
      },
      "constraint_tags": [],
      "status": "present",
      "evidence": {
        "evidence_id": "evidence-001",
        "evidence_class": "hard",
        "source_type": "owner_feedback",
        "source_ref": "ingredient_confirmations:confirmation-001",
        "verification_status": "owner_confirmed",
        "reliability_weight": 1.0,
        "observed_at": "2026-10-08T03:00:00Z"
      },
      "flagged_anomaly": false,
      "override_eligible": true,
      "expired": false
    }
  ],
  "flag_confirmations": [
    {
      "ambiguity_flag": "has_unclear_jeotgal",
      "constraint_tags": ["is_fish"],
      "status": "absent",
      "evidence": {
        "evidence_id": "evidence-101",
        "evidence_class": "hard",
        "source_type": "owner_feedback",
        "source_ref": "owner_flag_confirmations:confirmation-101",
        "verification_status": "owner_confirmed",
        "reliability_weight": 1.0,
        "observed_at": "2026-10-08T03:00:00Z"
      },
      "override_eligible": true,
      "expired": false
    }
  ],
  "owner_question_states": [
    {
      "question_id": "question-002",
      "ingredient": {
        "ingredient_id": 110,
        "canonical_name": "액젓"
      },
      "ambiguity_flag": null,
      "constraint_tags": [],
      "answered_round": 2,
      "confirmation_state": "needs_third"
    }
  ],
  "warnings": [],
  "errors": []
}
```

`scope_hint`와 `confirmation_scope`는 `exact | inherited`, `completeness`는 `complete | unknown`이다. `expired`는 사장님 확인값의 유효기간이 지났는지 여부다 (기준은 ③ 문서). `status`는 3-State를 유지한다. ③은 `constraint_tags: []`로 반환하며 `conflict`, `superseded` 같은 정의되지 않은 필드는 보내지 않는다.

`flag_confirmations[]`는 메뉴 애매함 플래그 질문 카드(§4-7)에 대한 사장님 답이다. `ambiguity_flag`는 §1-3 `menu.ambiguity_flags`의 값 하나, `constraint_tags`는 그때 물어본 태그(질문에 들어 있던 값이며 ④가 붙이는 재료 태그가 아님), `status`는 3-State다. `absent`는 물어본 태그 **전부**가 없다는 뜻이고 `present`는 그중 **하나 이상**이 있다는 뜻이다. 비교할 확률이 없으므로 anomaly 표시는 없고, `override_eligible`은 `expired: false`일 때만 `true`다. 같은 플래그를 다른 태그로 물은 답은 따로 반환한다. `completeness`는 재료 확인값만 보고 정하며 `flag_confirmations`의 영향을 받지 않는다. 저장 테이블 이름(`source_ref`의 `owner_flag_confirmations`)은 백엔드가 정할 때까지 예시다. Supervisor는 경로와 관계없이 ⑧ 직전에 ④ 태그 조회(`tag_lookup` node)를 불러 모든 ③ 확인값의 태그를 채운다 (§2-8, ④ §2-7).

`owner_question_states[]`는 백엔드가 `record_owner_answer`를 처리한 뒤 저장한 최신 질문 진행 상태다. 재료 질문은 `ingredient`를 채우고 `ambiguity_flag: null`, 플래그 질문은 `ingredient: null`과 `ambiguity_flag`를 사용한다. `answered_round`는 방금 답변한 차수인 `1 | 2 | 3`, `confirmation_state`는 `confirmed | needs_second | needs_third`다. `needs_second`는 `answered_round: 1`, `needs_third`는 `answered_round: 2`에서만 허용하고 3차 답변 뒤에는 반드시 `confirmed`여야 한다. ③은 이 배열을 계산하거나 변경하지 않고 ⑧까지 그대로 전달한다.

첫 번째 호출은 대상 `menu_id`가 안정적으로 식별된 경우에만 `scope_hint="exact"`로 전달한다. `menu_id`가 없으면 ③을 호출하지 않는다.

`scope_hint="inherited"` 두 번째 호출은 현재 보류한다. `exact_inherited` node 이름은 유지하지만 ③을 호출하지 않고, 기본 메뉴의 사장님 답변을 ⑤ prior 보정에 전달하지 않는다. 향후 상속 정책이 별도 이슈에서 확정되기 전까지 `confirmation_scope: inherited` 응답은 생성하지 않는다.

### 4-3. ④ DB / Ontology Tool

#### 요청 — `OntologyRequest`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "item",
    "item_id": "scan-123:0"
  },
  "menu": {
    "menu_id": null,
    "normalized_menu_name": "차돌김치찌개",
    "match_candidates": [],
    "residual_tokens": ["차돌"]
  },
  "unconfirmed_only": true,
  "confirmed_ingredients": [],
  "anomaly_locked_ingredients": [],
  "knowledge": {
    "status": "ok",
    "menu": {
      "menu_id": 1001,
      "base_menu_id": null,
      "remain_token": null,
      "menu_category_id": 11,
      "menu_category": "찌개",
      "exists_in_db": true,
      "is_variant": false,
      "variant_origin": null,
      "ambiguity_flags": ["has_unclear_jeotgal"],
      "knowledge_revision": 7
    },
    "recipe_ingredients": [
      {
        "ingredient_id": 109
      }
    ],
    "menu_recipe_corpora": [
      {
        "corpus": "10000recipe",
        "n_total": 57
      }
    ],
    "menu_ingredient_priors": [
      {
        "corpus": "10000recipe",
        "ingredient_id": 109,
        "k_count": 41
      }
    ],
    "cache": null
  },
  "ingredient_graph": {
    "status": "ok",
    "ingredients": [
      {
        "ingredient_id": 109,
        "canonical_name": "김치",
        "name_normalized": "김치",
        "taxonomy_category": "fermented"
      },
      {
        "ingredient_id": 110,
        "canonical_name": "액젓",
        "name_normalized": "액젓",
        "taxonomy_category": "fermented"
      }
    ],
    "aliases": [],
    "tags": [
      {
        "ingredient_id": 110,
        "tag_code": "is_fish"
      }
    ],
    "compositions": [
      {
        "parent_ingredient_id": 109,
        "child_ingredient_id": 110,
        "ambiguity_flag": "has_unclear_jeotgal"
      }
    ],
    "composition_tags": []
  },
  "warnings": [],
  "errors": []
}
```

`knowledge`는 ②-B가 고른 후보의 `ontology` 또는 미매칭 경로의 `fallback_ontology`를 수정 없이 넣는다. `ingredient_graph`는 JudgeRequest의 공통 graph를 모든 Item Graph가 read-only로 공유해 넣는다. ④는 두 객체만 사용해 원재료 확장과 태그 매핑을 수행하며 판정 단계에서 DB를 직접 조회하지 않는다.

#### 응답 — `OntologyResponse`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "item",
    "item_id": "scan-123:0"
  },
  "menu": {
    "menu_id": null,
    "base_menu_id": 1001,
    "remain_token": "차돌",
    "menu_category_id": 11,
    "menu_category": "찌개",
    "exists_in_db": false,
    "is_variant": true,
    "variant_origin": "runtime_tagged"
  },
  "menu_ambiguity_flags": ["has_unclear_jeotgal"],
  "ingredients": [
    {
      "ingredient": {
        "ingredient_id": 110,
        "canonical_name": "액젓"
      },
      "taxonomy_category": "fermented",
      "constraint_tags": ["is_fish"],
      "role": "hidden",
      "depth": 1,
      "parent_ingredient": {
        "ingredient_id": 109,
        "canonical_name": "김치"
      },
      "source": "expanded",
      "curated": true,
      "observation_basis": "inherited",
      "observations": [
        {
          "corpus": "10000recipe",
          "k_count": 41,
          "n_total": 57
        }
      ],
      "anomaly_locked": false,
      "evidence_refs": ["recipe:menu-001:ingredient-010"]
    }
  ],
  "evidence": [
    {
      "evidence_id": "recipe:menu-001:ingredient-010",
      "evidence_class": "soft",
      "source_type": "expanded",
      "source_ref": "recipe_ingredients:menu-001:ingredient-009",
      "verification_status": "approved",
      "reliability_weight": 0.8,
      "observed_at": "2026-10-08T03:00:00Z"
    }
  ],
  "variant_suggestion": {
    "base_menu_id": 1001,
    "remain_token": "차돌",
    "suggested_ingredients": [
      {
        "ingredient": {
          "ingredient_id": 120,
          "canonical_name": "소고기"
        },
        "constraint_tags": ["is_beef"],
        "role": "identity"
      }
    ],
    "evidence": {
      "evidence_id": "runtime:scan-123:0:ocr_variant_tag:0",
      "evidence_class": "soft",
      "source_type": "ocr_variant_tag",
      "source_ref": "scan-123:0:차돌",
      "verification_status": "pending_review",
      "reliability_weight": 0.3,
      "observed_at": "2026-10-08T03:00:00Z"
    }
  },
  "unmapped_tokens": [],
  "cache_command": null,
  "warnings": [],
  "errors": []
}
```

`exists_in_db`는 **현재 메뉴 자체의 exact row 존재 여부**다. `false`여도 `base_menu_id`가 있으면 신규 runtime 변형이므로 웹서치하지 않고 base recipe 확장 → `exact_inherited` 보류 node → ⑤로 진행한다. 보류 node에서는 ③을 호출하지 않는다. 웹서치는 `exists_in_db: false`이면서 `base_menu_id: null`일 때만 호출한다.

`menu_ambiguity_flags`는 `knowledge.menu.ambiguity_flags`를 그대로 옮긴 값이며, `knowledge.menu`가 null이면 `[]`다. ④는 값을 해석하지 않고 ⑧까지 전달되게 반환만 한다.

`variant_origin`은 `db_registered | runtime_tagged | null`, 재료 `source`는 `recipe | expanded | variant_suggested | web_search`다. `variant_suggestion`은 `variant_origin: runtime_tagged`일 때 위 객체이고, 그 외에는 `null`이다. `evidence`에는 `ingredients[].evidence_refs`가 참조하는 모든 `EvidenceRef`를 넣는다. 재료 위험 속성은 `constraint_tags` 하나만 사용하며 ④가 `ingredient_graph.tags`와 `composition_tags`를 합쳐 중복 제거한 `is_*` 표준 태그 배열로 만든다. `curated`는 검수된 레시피·온톨로지 자료인지 표시하고, `observation_basis`는 `direct | inherited | none`이다. `none`은 관측값이 없는 재료(`observations: []`, 예: `variant_suggested`, 메뉴가 어느 코퍼스에도 없음)다. `observations[]`는 출처별 `{corpus, k_count, n_total}`이며 `0 <= k_count <= n_total`을 만족한다. ⑤가 출처 신뢰도를 적용할 수 있도록 서로 다른 corpus의 관측치를 미리 합치지 않는다.

`role`은 `identity | component | hidden | null`이다. ④가 레시피·온톨로지 관계로 주재료(`identity`), 속재료(`component`), 양념·가공식품 안의 숨은 재료(`hidden`)를 구분하고 판단할 근거가 없으면 `null`을 반환한다. 이 값은 “빼기 어려울 수 있음” 안내에만 사용하며 위험도 판정에는 사용하지 않는다. ④ `lookup_tags`가 ③ 확인값을 보강할 때도 같은 재료의 `role`을 함께 채운다.

③의 출력 중 `status != unknown` AND `override_eligible: true`인 재료만 `confirmed_ingredients`로 전달한다. 원소는 `{ingredient: IngredientRef, status: present | absent}`다.

`override_eligible: false`인 재료는 누락하지 않고 ⑤ 계산 대상에 남긴다. 단 `anomaly_locked: true`는 `flagged_anomaly: true`인 재료에만 부여한다. `expired: true` 또는 `confirmation_scope: inherited`라는 이유만으로 override가 거부된 재료에는 `anomaly_locked: false`를 유지한다.

`anomaly_locked_ingredients`에도 `flagged_anomaly: true`인 재료만 넣는다. `not override_eligible` 전체를 이 배열로 복사하지 않는다. 원소는 `IngredientRef`다.

`cache_command`는 ④가 만든 `save_menu_ingredient_cache` 저장 명령이다. 캐시를 쓰지 않고 새로 펼쳤을 때만 채우고, 캐시를 그대로 썼거나 `knowledge.menu`가 null이면 `null`이다. 형식은 §4-6 `DbUpdateResponse.commands[]` 원소와 같다 (`operation_id`는 ⑦ 요청이 없으므로 `null`). Supervisor는 이 명령을 ⑦ 명령과 함께 `persistence_commands`에 넣는다. 관리자 게이트를 거치지 않는 파생 캐시라 ⑦을 거치지 않는다 (AGENTS.md 예외).

```json
{
  "command_id": "command-cache-001",
  "operation_id": null,
  "item_id": "scan-123:0",
  "card_id": null,
  "action": "save_menu_ingredient_cache",
  "idempotency_key": "sha256:<action·store_id·menu_id·menu_revision 해시>",
  "payload": {
    "menu_id": 1001,
    "menu_revision": 7,
    "ingredients_snapshot": []
  }
}
```

`ingredients_snapshot`은 확인 재료를 빼기 전 전체 확장 목록(`ingredients[]`와 같은 원소, `anomaly_locked`·확인값 제외)이다. `store_id`는 `context.store_id`를 쓴다.

④가 내는 warning code는 `cycle_detected`(순환 참조 차단), `max_depth_reached`, `constraint_tags_lookup_failed`, `unmapped_constraint_tags`, `empty_recipe`(레시피 재료 0개), `broken_base_menu`(참조한 원본 메뉴가 없음)다. 앞의 네 개는 §1-2 표대로 `forces_caution: true`이고, `empty_recipe`·`broken_base_menu`도 재료를 놓칠 수 있는 상태라 `forces_caution: true`로 보낸다.

### 4-4. ⑤ Bayesian Tool

#### 요청 — `BayesianRequest`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "item",
    "item_id": "scan-123:0"
  },
  "menu_category_id": 11,
  "ingredients": [
    {
      "ingredient": {
        "ingredient_id": 110,
        "canonical_name": "액젓"
      },
      "constraint_tags": ["is_fish"],
      "role": "hidden",
      "source": "expanded",
      "depth": 1,
      "curated": true,
      "observation_basis": "inherited",
      "observations": [
        {
          "corpus": "10000recipe",
          "k_count": 41,
          "n_total": 57
        }
      ],
      "anomaly_locked": false,
      "evidence_refs": ["recipe:menu-001:ingredient-010"]
    }
  ],
  "priors": {
    "status": "ok",
    "store": [
      {
        "ingredient_id": 110,
        "alpha": 8.0,
        "beta": 2.0,
        "evidence_refs": ["evidence-prior-001"]
      }
    ],
    "cluster": [],
    "global": [],
    "evidence": [
      {
        "evidence_id": "evidence-prior-001",
        "evidence_class": "soft",
        "source_type": "web_search",
        "source_ref": "ingredient_evidence_log:9001",
        "verification_status": "approved",
        "reliability_weight": 0.5,
        "observed_at": "2026-10-12T03:00:00Z"
      }
    ]
  },
  "source_reliability": {
    "status": "ok",
    "weights": [
      {
        "source_key": "corpus:10000recipe",
        "weight": 0.8
      },
      {
        "source_key": "curated",
        "weight": 1.0
      }
    ]
  },
  "inherited_confirmations": [],
  "warnings": [],
  "errors": []
}
```

#### 응답 — `BayesianResponse`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "item",
    "item_id": "scan-123:0"
  },
  "probabilities": [
    {
      "ingredient": {
        "ingredient_id": 110,
        "canonical_name": "액젓"
      },
      "constraint_tags": ["is_fish"],
      "role": "hidden",
      "posterior_mean": 0.62,
      "alpha_post": 42.0,
      "beta_post": 17.0,
      "prior_source": "store",
      "source": "expanded",
      "depth": 1,
      "confidence": 0.55,
      "anomaly_locked": false,
      "evidence_refs": ["recipe:menu-001:ingredient-010"]
    }
  ],
  "warnings": [],
  "errors": []
}
```

`prior_source`는 `store | cluster | global | uninformative`다. Supervisor는 선택된 후보의 KnowledgeBundle에서 `priors.store`, `priors.evidence`, `source_reliability`를 읽고, 외부 계약에서 제거한 `priors.cluster`와 `priors.global`은 내부 ⑤ 요청을 조립할 때 빈 배열로 채운다. ⑤는 store prior가 없으면 빈 cluster 단계를 건너뛰고 ④의 corpus별 `observations`로 global prior를 계산하며, 관측도 없으면 uninformative prior를 사용한다. `not_found`는 정의된 fallback으로 진행하고 `unavailable`은 warning과 SAFE 금지 신호를 유지한다.

⑤는 질문 후보를 확률이나 confidence 숫자로 거르지 않으므로 질문 후보용 확률 구간 필드를 계산하거나 반환하지 않는다. `inherited_confirmations`는 현재 보류 상태이므로 항상 `[]`이며 ⑤가 사장님 답변을 prior 보정에 사용하지 않는다. ⑤에는 확정 override 대상이 아닌 재료만 전달한다. ⑤는 `constraint_tags`, `role`, `anomaly_locked`, `evidence_refs`를 계산하거나 수정하지 않고 입력 그대로 각 probability에 복사한다. ⑤의 결과는 최종 판정이 아니며 ⑧이 사용자 프로필과 정책을 적용해 해석한다.

⑤가 내는 warning code는 `empty_ingredients`(계산할 재료 0개), `integrity_error`(`k_count > n_total` 등 관측값 이상, 해당 재료는 무정보로 계산), `prior_global_ignored`(항상 빈 배열이어야 할 `priors.global`·`cluster`에 값이 와서 무시함)다. `empty_ingredients`와 `integrity_error`는 `forces_caution: true`다.

`menu_category_id`는 category prior 조회에 사용하는 DB 정수 ID이며 없으면 `null`이다. null이면 ⑤는 category/cluster prior를 건너뛰고 정의된 다음 fallback 단계로 이동한다.

### 4-5. ⑥ Web Search Agent

#### 1단계 요청 — `WebCollectRequest`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "item",
    "item_id": "scan-123:0"
  },
  "menu": {
    "menu_id": null,
    "normalized_menu_name": "마라탕",
    "locale_hint": "ko"
  },
  "max_sources": 10,
  "cached_sources": [],
  "next_rank": 1,
  "warnings": [],
  "errors": []
}
```

#### 1단계 응답 — `WebCollectResponse`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "item",
    "item_id": "scan-123:0"
  },
  "menu": {
    "menu_id": null,
    "normalized_menu_name": "마라탕"
  },
  "found": true,
  "sources": [
    {
      "source_url": "https://example.com/recipe/mala-tang",
      "title": "마라탕 레시피",
      "fetched_at": "2026-10-08T03:00:00Z",
      "evidence": {
        "evidence_id": "web:https://example.com/recipe/mala-tang",
        "evidence_class": "soft",
        "source_type": "web_search",
        "source_ref": "https://example.com/recipe/mala-tang",
        "verification_status": "pending_review",
        "reliability_weight": null,
        "observed_at": "2026-10-08T03:00:00Z"
      },
      "ingredients": [
        {
          "ingredient_id": null,
          "canonical_name": "소고기"
        }
      ]
    }
  ],
  "warnings": [],
  "errors": []
}
```

`cached_sources`는 백엔드가 `JudgeRequest.knowledge_bundle.items[].web_search_cache`에서 꺼내 전달하며 1단계 응답의 `sources[]`와 같은 closed schema를 사용한다. 캐시가 없으면 `[]`이고 `next_rank`는 `1`이다. 캐시가 있으면 마지막으로 수집한 다음 검색 순위를 보내며, 예를 들어 1~10위가 저장되어 있으면 `11`이다.

KnowledgeBundle의 웹 캐시 영역은 다음 형식으로 고정한다.

```json
{
  "items": [
    {
      "item_id": "scan-123:0",
      "web_search_cache": {
        "status": "not_found",
        "cached_sources": [],
        "next_rank": 1
      }
    }
  ]
}
```

Supervisor는 `item_id`가 같은 항목만 해당 Item Graph에 전달하고, 다른 메뉴의 캐시를 fallback으로 사용하지 않는다. 캐시 데이터에 기록된 메뉴명이 Prepare의 `normalized_menu_name`과 다르면 백엔드가 해당 cache slice를 `unavailable`로 보내야 하며, AI가 다른 메뉴 캐시로 대체하지 않는다.

1단계는 출처별 원문과 추출한 재료명만 반환한다. 이 단계에서는 이름을 합치거나 `k_count`, `n_total`을 계산하지 않는다. Supervisor는 `sources[].ingredients`를 ④ `map_ingredients`에 보내 표준 `ingredient_id`, `canonical_name`, `constraint_tags`를 채운다.

#### 2단계 요청 — `WebAggregateRequest`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "item",
    "item_id": "scan-123:0"
  },
  "menu": {
    "menu_id": null,
    "normalized_menu_name": "마라탕"
  },
  "mapped_sources": [
    {
      "source_url": "https://example.com/recipe/mala-tang",
      "source_origin": "new",
      "corpus": "web_search",
      "evidence_id": "web:https://example.com/recipe/mala-tang",
      "ingredients": [
        {
          "ingredient": {
            "ingredient_id": 120,
            "canonical_name": "소고기"
          },
          "constraint_tags": ["is_beef"],
          "role": "identity"
        }
      ]
    }
  ],
  "warnings": [],
  "errors": []
}
```

#### 2단계 응답 — `WebAggregateResponse`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "item",
    "item_id": "scan-123:0"
  },
  "menu": {
    "menu_id": null,
    "normalized_menu_name": "마라탕"
  },
  "found": true,
  "sources": [
    {
      "source_url": "https://example.com/recipe/mala-tang",
      "title": "마라탕 레시피",
      "fetched_at": "2026-10-08T03:00:00Z",
      "evidence": {
        "evidence_id": "web:https://example.com/recipe/mala-tang",
        "evidence_class": "soft",
        "source_type": "web_search",
        "source_ref": "https://example.com/recipe/mala-tang",
        "verification_status": "pending_review",
        "reliability_weight": null,
        "observed_at": "2026-10-08T03:00:00Z"
      },
      "ingredients": [
        {
          "ingredient_id": null,
          "canonical_name": "소고기"
        }
      ]
    }
  ],
  "ingredients": [
    {
      "ingredient": {
        "ingredient_id": 120,
        "canonical_name": "소고기"
      },
      "constraint_tags": ["is_beef"],
      "role": "identity",
      "source": "web_search",
      "depth": 0,
      "curated": false,
      "observation_basis": "direct",
      "observations": [
        {
          "corpus": "web_search",
          "k_count": 1,
          "n_total": 1
        }
      ],
      "anomaly_locked": false,
      "evidence_refs": ["web:https://example.com/recipe/mala-tang"]
    }
  ],
  "review_notes": [
    {
      "rule_code": "general_web_source",
      "message": "일반 웹 출처이므로 관리자 확인이 필요해요."
    }
  ],
  "warnings": [],
  "errors": []
}
```

Supervisor는 신규 수집 결과와 캐시를 모두 ④에 매핑한 뒤 `mapped_sources`로 전달한다. `source_origin`은 `new | cache`이며, 2단계는 둘을 합쳐 표준 재료·사이트별 `observations`를 계산한다. 응답 `sources`에는 저장·관리자 검토 대상인 신규 출처만 넣고 캐시 출처를 다시 넣지 않는다. `curated`는 false, `observation_basis`는 `direct`로 고정한다. `role`은 ④ 매핑 결과를 그대로 옮기며 ⑥이 새로 판단하지 않는다.

집계 재료의 `evidence_refs`는 원본 출처를 가리킨다. 매핑하지 못한 재료는 버리지 않고 `ingredient_id: null`로 유지하며, ④의 `unmapped_constraint_tags` warning을 이어받아 ⑧이 SAFE를 막게 한다. `review_notes`는 관리자 검토용이며 판정에는 사용하지 않는다. 수집 결과와 캐시가 모두 없을 때만 `found: false`다.

⑥의 warning `code`에는 `insufficient_sources`(유효 출처 부족), `web_search_deadline_exceeded`(시간 제한 초과로 일부 결과만 반환), `web_llm_extraction_failed`(LLM 추출 실패로 규칙 결과만 반환)가 있다. 모두 `forces_caution: true`다. 기준값은 ⑥ 문서를 따른다.

④가 `exists_in_db: false`이면서 `base_menu_id: null`을 반환한 경우에만 호출한다. ⑥ 결과는 실시간 ⑤ 계산에는 사용할 수 있지만 DB에 자동 반영하지 않는다.

### 4-6. ⑦ DB Update Tool

#### 요청 — `DbUpdateRequest`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "item",
    "item_id": "scan-123:0"
  },
  "operations": [
    {
      "operation_id": "operation-001",
      "operation_type": "create_review_item",
      "payload": {
        "review_origin": "web_search",
        "menu": {
          "menu_id": null,
          "normalized_menu_name": "마라탕"
        },
        "web_sources": [
          {
            "source_url": "https://example.com/recipe/mala-tang",
            "title": "마라탕 레시피",
            "fetched_at": "2026-10-08T03:00:00Z",
            "ingredients": [
              {
                "ingredient_id": null,
                "canonical_name": "소고기"
              }
            ],
            "evidence": {
              "evidence_id": "web:https://example.com/recipe/mala-tang",
              "evidence_class": "soft",
              "source_type": "web_search",
              "source_ref": "https://example.com/recipe/mala-tang",
              "verification_status": "pending_review",
              "reliability_weight": null,
              "observed_at": "2026-10-08T03:00:00Z"
            }
          }
        ],
        "review_notes": [
          {
            "rule_code": "general_web_source",
            "message": "일반 웹 출처이므로 관리자 확인이 필요해요."
          }
        ],
        "variant_suggestion": null
      }
    },
    {
      "operation_id": "operation-002",
      "operation_type": "save_web_search_cache",
      "payload": {
        "normalized_menu_name": "마라탕",
        "original_menu_name": "마라탕",
        "search_status": "searched",
        "sources": [
          {
            "source_url": "https://example.com/recipe/mala-tang",
            "title": "마라탕 레시피",
            "fetched_at": "2026-10-08T03:00:00Z",
            "ingredients": [
              {
                "ingredient_id": null,
                "canonical_name": "소고기"
              }
            ],
            "evidence": {
              "evidence_id": "web:https://example.com/recipe/mala-tang",
              "evidence_class": "soft",
              "source_type": "web_search",
              "source_ref": "https://example.com/recipe/mala-tang",
              "verification_status": "pending_review",
              "reliability_weight": null,
              "observed_at": "2026-10-08T03:00:00Z"
            }
          }
        ],
        "next_rank": 11,
        "searched_at": "2026-10-08T03:00:00Z"
      }
    }
  ],
  "warnings": [],
  "errors": []
}
```

`operation_type`은 다음 다섯 값만 허용한다. ④의 `save_menu_ingredient_cache`는 ④가 직접 만들므로 이 요청의 operation에는 넣지 않는다.

- `create_review_item`: 웹서치·런타임 변형 soft evidence 검토 요청
- `create_owner_verification_request`: ⑧의 `owner_cards[]` 각 카드에 대한 저장 요청. 카드마다 명령 하나 생성. 단 정보 없는 메뉴의 화면 표시용 카드(`ingredient: null`, `ambiguity_flag: null`)는 저장 요청을 만들지 않는다 (§4-7)
- `record_owner_answer`: 사장님 답변 기록·확정값 갱신 요청
- `update_source_reliability`: 출처별 신뢰도 갱신 요청
- `save_web_search_cache`: ⑥이 새로 수집한 출처와 다음 검색 순위를 캐시에 저장하는 요청

`payload`는 `operation_type`별로 다음 closed schema 중 하나를 사용한다. 서로 다른 타입의 payload 필드를 섞지 않는다. 아래 최상위 다섯 key는 타입별 payload를 한 번에 보여주기 위한 문서용 라벨이며 실제 wire JSON에는 보내지 않는다.

```json
{
  "create_review_item": {
    "review_origin": "web_search",
    "menu": {
      "menu_id": null,
      "normalized_menu_name": "마라탕"
    },
    "web_sources": [
      {
        "source_url": "https://example.com/recipe/mala-tang",
        "title": "마라탕 레시피",
        "fetched_at": "2026-10-08T03:00:00Z",
        "ingredients": [
          {
            "ingredient_id": null,
            "canonical_name": "소고기"
          }
        ],
        "evidence": {
          "evidence_id": "web:https://example.com/recipe/mala-tang",
          "evidence_class": "soft",
          "source_type": "web_search",
          "source_ref": "https://example.com/recipe/mala-tang",
          "verification_status": "pending_review",
          "reliability_weight": null,
          "observed_at": "2026-10-08T03:00:00Z"
        }
      }
    ],
    "review_notes": [
      {
        "rule_code": "general_web_source",
        "message": "일반 웹 출처이므로 관리자 확인이 필요해요."
      }
    ],
    "variant_suggestion": null
  },
  "create_owner_verification_request": {
    "item_id": "scan-123:0",
    "card_id": "card-001",
    "menu_id": 1001,
    "round": 1,
    "previous_question_id": null,
    "ingredient": {
      "ingredient_id": 110,
      "canonical_name": "액젓"
    },
    "ambiguity_flag": null,
    "constraint_tags": [],
    "recipe_probability": 0.93,
    "question": {
      "ko": "이 메뉴에 액젓이 들어가나요?",
      "en": "Does this menu contain fish sauce?"
    },
    "reason_codes": ["low_confidence"],
    "evidence_refs": ["evidence-010"]
  },
  "record_owner_answer": {
    "menu_id": 1001,
    "ingredient": {
      "ingredient_id": 110,
      "canonical_name": "액젓"
    },
    "ambiguity_flag": null,
    "constraint_tags": [],
    "question_id": "question-001",
    "present": true,
    "answered_at": "2026-10-08T03:10:00Z"
  },
  "update_source_reliability": {
    "source_type": "web_search",
    "correct_count_delta": 1,
    "comparison_count_delta": 1,
    "trigger_evidence_ref": "evidence-001"
  },
  "save_web_search_cache": {
    "normalized_menu_name": "마라탕",
    "original_menu_name": "마라탕",
    "search_status": "searched",
    "sources": [
      {
        "source_url": "https://example.com/recipe/mala-tang",
        "title": "마라탕 레시피",
        "fetched_at": "2026-10-08T03:00:00Z",
        "ingredients": [
          {
            "ingredient_id": null,
            "canonical_name": "소고기"
          }
        ],
        "evidence": {
          "evidence_id": "web:https://example.com/recipe/mala-tang",
          "evidence_class": "soft",
          "source_type": "web_search",
          "source_ref": "https://example.com/recipe/mala-tang",
          "verification_status": "pending_review",
          "reliability_weight": null,
          "observed_at": "2026-10-08T03:00:00Z"
        }
      }
    ],
    "next_rank": 11,
    "searched_at": "2026-10-08T03:00:00Z"
  }
}
```

- `create_review_item.review_origin`은 `web_search | variant_suggestion`이다. `web_search`이면 `web_sources`가 비어 있지 않고 `variant_suggestion`은 `null`; `variant_suggestion`이면 `web_sources: []`이고 ④가 반환한 `variant_suggestion` 객체를 그대로 넣는다.
- `create_review_item.review_notes`는 ⑥ 응답을 그대로 보존한다. 검토 화면에만 사용하며 위험 판정이나 확률 계산에는 사용하지 않는다.
- `create_owner_verification_request`와 `record_owner_answer`는 ⑧ 카드의 종류를 그대로 따른다. `create_owner_verification_request.item_id`와 `card_id`는 원래 `owner_cards[]`의 같은 값을 보존해 화면 카드와 저장 명령을 잇는다. 재료 카드는 `ingredient` 객체 + `ambiguity_flag: null` + `constraint_tags: []`, 메뉴 애매함 플래그 카드는 `ingredient: null` + `ambiguity_flag` + 물어본 `constraint_tags`다. 플래그 답은 재료 확인값(`ingredient_confirmations`)과 다른 곳에 저장해 ③ `flag_confirmations`로 돌아와야 하며, 저장 테이블은 백엔드가 정한다 (§8).
- `create_owner_verification_request.question`은 한국어 `ko`와 Judge 요청 `locale`의 문구만 보존한다. 요청 언어가 한국어면 `ko` 하나만 보낸다.
- `create_owner_verification_request.round`는 `1 | 2 | 3`이며 `previous_question_id`는 1차 질문이면 `null`, 2·3차 질문이면 직전 질문 ID다. 백엔드는 두 값을 질문 행에 보존해 질문 사슬과 차수를 검증한다.
- `create_owner_verification_request.recipe_probability`는 Supervisor가 같은 스캔의 ⑤ 결과에서 그 재료의 `posterior_mean`을 그대로 넣는다 (0~1). ⑧ 카드에는 이 필드가 없다. 플래그 카드는 `null`이다. 백엔드가 질문 행에 저장했다가, 사장님 답을 확정할 때 이상 답변(`flagged_anomaly`: 이 값 0.9 이상 + "없음") 판정에 쓴다 (⑦ §2-3).
- `record_owner_answer.present`만 사장님이 답한 yes/no 사실이므로 boolean을 쓴다. 재료 판정 상태로 변환된 뒤에는 반드시 `present | absent | unknown`을 사용한다.
- `update_source_reliability`의 두 delta는 0 이상의 정수이며 `correct_count_delta <= comparison_count_delta`여야 한다. 신뢰도는 `source_type` 단위로만 센다 (`corpus:semie`, `web_search` 등). 사이트 도메인별로 나누지 않는다 (DB 스키마 `source_reliability`).
- `save_web_search_cache.sources`에는 이번 호출에서 새로 찾은 ⑥ `sources[]`만 넣는다. 기존 캐시를 다시 넣지 않는다. `search_status`는 `searched | unresolved`, `next_rank`는 다음 검색 시작 순위인 1 이상의 정수다. `searched_at`은 UTC ISO 8601 문자열이다. 캐시 유효기간을 두지 않으므로 항상 `null`이던 `expires_at`은 외부 명령에 넣지 않는다 (⑥ §2-4).
- 신규 웹 출처가 있으면 Supervisor는 같은 `DbUpdateRequest`에 `create_review_item`과 `save_web_search_cache`를 각각 하나의 operation으로 함께 넣는다. ⑦은 두 저장 명령을 만들고, 백엔드는 캐시 원본은 관리자 승인 없이 저장하되 지식 DB 반영은 검토가 통과된 자료에만 수행한다.
- 백엔드가 `record_owner_answer`를 처리한 결과는 `{question_id, answered_round, confirmation_state}`이며 `confirmation_state`는 `confirmed | needs_second | needs_third`다. 이 객체를 AI가 같은 호출에서 직접 받는 별도 endpoint는 만들지 않는다. 백엔드는 결과를 저장하고 다음 Judge의 `KnowledgeBundle.exact_feedback.exact.owner_question_states[]`에 대상 재료 또는 플래그 정보와 함께 넣는다. ③은 이를 `ExactFeedbackResponse.owner_question_states[]`로 보존하고, Supervisor는 ⑧ `DecisionRequest.owner_question_states[]`까지 전달한다. ⑧은 `needs_second`와 `needs_third`에만 후속 카드를 생성한다.
- 스캔 중 생긴 작업은 원래 `item` context를 사용한다. 사장님 답변처럼 스캔 흐름 밖에서 들어오는 작업은 `call_scope: out_of_band`, `item_id: null`로 별도 요청한다.

⑦이 command에 추가하는 `provenance`는 다음 closed schema다.

```json
{
  "origin_node": "web_search",
  "source_type": "web_search",
  "evidence_class": "soft",
  "evidence_refs": ["web:https://example.com/recipe/mala-tang"]
}
```

- `origin_node`: `ontology | web_search | decision | external_owner_answer | db_update`
- `source_type`: `web_search | ocr_variant_tag | decision_policy | owner_feedback | system`
- `evidence_class`: `hard | soft | null`. 아직 답변이 없는 질문이나 집계 명령은 `null`이다.
- `evidence_refs`: 명령을 만들게 한 기존 근거 ID 목록. 백엔드가 저장 과정에서 새로 발급할 근거 ID는 미리 만들지 않는다.

초기 `verification_status`는 백엔드가 action별 저장 정책으로 정하고, `reliability_weight`는 백엔드의 `source_reliability`에서 관리하므로 command provenance에 중복하지 않는다. operation별 provenance 고정값은 다음과 같다.

| `action` | `origin_node` | `source_type` | `evidence_class` |
|---|---|---|---|
| `create_review_item`(웹) | `web_search` | `web_search` | `soft` |
| `create_review_item`(변형) | `ontology` | `ocr_variant_tag` | `soft` |
| `create_owner_verification_request` | `decision` | `decision_policy` | `null` |
| `record_owner_answer` | `external_owner_answer` | `owner_feedback` | `hard` |
| `update_source_reliability` | `db_update` | `system` | `null` |
| `save_web_search_cache` | `web_search` | `web_search` | `soft` |

#### 응답 — `DbUpdateResponse`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "item",
    "item_id": "scan-123:0"
  },
  "commands": [
    {
      "command_id": "command-001",
      "operation_id": "operation-001",
      "item_id": "scan-123:0",
      "card_id": null,
      "action": "create_review_item",
      "idempotency_key": "sha256:<action·내용 키 해시>",
      "payload": {
        "review_origin": "web_search",
        "menu": {
          "menu_id": null,
          "normalized_menu_name": "마라탕"
        },
        "web_sources": [
          {
            "source_url": "https://example.com/recipe/mala-tang",
            "title": "마라탕 레시피",
            "fetched_at": "2026-10-08T03:00:00Z",
            "ingredients": [
              {
                "ingredient_id": null,
                "canonical_name": "소고기"
              }
            ],
            "evidence": {
              "evidence_id": "web:https://example.com/recipe/mala-tang",
              "evidence_class": "soft",
              "source_type": "web_search",
              "source_ref": "https://example.com/recipe/mala-tang",
              "verification_status": "pending_review",
              "reliability_weight": null,
              "observed_at": "2026-10-08T03:00:00Z"
            }
          }
        ],
        "review_notes": [
          {
            "rule_code": "general_web_source",
            "message": "일반 웹 출처이므로 관리자 확인이 필요해요."
          }
        ],
        "variant_suggestion": null,
        "provenance": {
          "origin_node": "web_search",
          "source_type": "web_search",
          "evidence_class": "soft",
          "evidence_refs": ["web:https://example.com/recipe/mala-tang"]
        }
      }
    }
  ],
  "warnings": [],
  "errors": []
}
```

각 command의 `action`은 입력 `operation_type`과 같고 `operation_id`를 보존한다. 스캔 중 명령의 `item_id`는 원래 item context 값을 보존하고, `card_id`는 `create_owner_verification_request`일 때 해당 카드 ID, 그 외에는 `null`이다. `idempotency_key`는 **내용 기준**으로 만든다: `"sha256:" + sha256_hex(action + "|" + 내용 키의 정규 JSON)`. `trace_id`·`operation_id`·시각은 넣지 않는다. 넣으면 같은 스캔의 Judge를 재시도하거나 백엔드가 응답을 다시 받을 때 키가 바뀌어 같은 명령이 두 번 저장된다. 질문 키에는 `card_id`, `round`, `previous_question_id`를 포함하므로 같은 대상의 1·2·3차 질문은 서로 다른 명령이지만 동일 차수 재시도는 같은 명령이다. 정규 JSON은 key 정렬·공백 없음이고, 배열 값은 정렬한다.

| action | 내용 키 |
|---|---|
| `create_review_item` | `store_id`, `review_origin`, 메뉴(`normalized_menu_name` 또는 `variant_suggestion`의 `base_menu_id`·`remain_token`), 정렬한 `source_url` 목록 |
| `create_owner_verification_request` | `store_id`, `scan_session_id`, `item_id`, `card_id`, `menu_id`, `round`, `previous_question_id`, 재료 카드는 `ingredient_id` / 플래그 카드는 `ambiguity_flag`·정렬한 `constraint_tags` |
| `record_owner_answer` | `question_id` |
| `update_source_reliability` | `source_type`, `trigger_evidence_ref` |
| `save_web_search_cache` | `normalized_menu_name`, `next_rank` |
| `save_menu_ingredient_cache` (④) | `store_id`, `menu_id`, `menu_revision` |

command `payload`는 해당 입력 payload의 모든 필드를 보존하고 위 `provenance` 객체 하나를 추가한 형태다.

soft evidence는 관리자 검토 명령까지만 만들고, hard evidence는 백엔드가 검증할 기록 명령을 만든다. ⑦은 저장 완료를 가장하지 않으며 실제 쓰기는 백엔드가 권한·FK·멱등성을 검증한 뒤 수행한다. 응답 `commands`는 Supervisor의 `persistence_commands`에 그대로 누적한다.

### 4-7. ⑧ Decision Policy / XAI Agent

#### 요청 — `DecisionRequest`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "item",
    "item_id": "scan-123:0"
  },
  "menu": {
    "menu_id": 1001,
    "menu_category_id": 11,
    "normalized_menu_name": "김치찌개"
  },
  "confirmed_results": [
    {
      "ingredient": {
        "ingredient_id": 101,
        "canonical_name": "돼지고기"
      },
      "constraint_tags": ["is_pork"],
      "role": "identity",
      "status": "present",
      "override_eligible": true,
      "expired": false,
      "evidence_refs": ["evidence-001"]
    }
  ],
  "probability_results": [
    {
      "ingredient": {
        "ingredient_id": 110,
        "canonical_name": "액젓"
      },
      "constraint_tags": ["is_fish"],
      "role": "hidden",
      "posterior_mean": 0.62,
      "confidence": 0.55,
      "prior_source": "store",
      "anomaly_locked": false,
      "evidence_refs": ["recipe:menu-001:ingredient-010"]
    }
  ],
  "proposed_variant_ingredients": [
    {
      "ingredient": {
        "ingredient_id": 120,
        "canonical_name": "소고기"
      },
      "constraint_tags": ["is_beef"],
      "role": "identity",
      "source": "variant_suggested",
      "evidence_refs": ["runtime:scan-123:0:ocr_variant_tag:0"]
    }
  ],
  "risk_tag_observations": [
    {
      "tag": "is_spicy",
      "confidence": 0.78,
      "evidence_refs": ["ocr:scan-123:0:is_spicy"]
    }
  ],
  "menu_ambiguity_flags": ["has_unclear_jeotgal"],
  "flag_confirmations": [],
  "owner_question_states": [
    {
      "question_id": "question-002",
      "ingredient": {
        "ingredient_id": 110,
        "canonical_name": "액젓"
      },
      "ambiguity_flag": null,
      "constraint_tags": [],
      "answered_round": 2,
      "confirmation_state": "needs_third"
    }
  ],
  "no_information": false,
  "information_status": "complete",
  "user_profile": {
    "religion_type": "halal",
    "is_vegetarian": false,
    "vegetarian_type": null,
    "no_alcohol": false,
    "allergies": ["is_fish"],
    "no_spicy": false
  },
  "locale": "en",
  "warnings": [],
  "errors": []
}
```

#### 응답 — `DecisionResponse`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "item",
    "item_id": "scan-123:0"
  },
  "menu": {
    "menu_id": 1001,
    "menu_category_id": 11,
    "normalized_menu_name": "김치찌개"
  },
  "risk_level": "danger",
  "evidence_basis": "confirmed",
  "information_status": "complete",
  "risk_ingredients": [
    {
      "ingredient": {
        "ingredient_id": 101,
        "canonical_name": "돼지고기",
        "localized_name": "pork"
      },
      "constraint_tags": ["is_pork"],
      "matched_tags": ["is_pork"],
      "role": "identity",
      "status": "present",
      "posterior_mean": null,
      "confidence": null,
      "prior_source": null,
      "anomaly_locked": false,
      "evidence_refs": ["evidence-001"]
    }
  ],
  "matched_tags": ["is_pork"],
  "evidence_refs": ["evidence-001"],
  "message": "This menu contains pork.",
  "owner_cards": [],
  "high_risk_review": {
    "required": false,
    "reason_code": null
  },
  "warnings": [],
  "errors": []
}
```

`risk_level`은 `danger | caution | safe`, 출력 `evidence_basis`는 `confirmed | estimated | unknown`, `information_status`는 `complete | partial | none`이다.

`message`는 요청 `locale`로 작성한 단일 string이다. `risk_ingredients[].ingredient.canonical_name`은 한국어 표준명을 유지하고 `localized_name`에는 요청 `locale`의 이름을 넣는다. `locale: ko`이면 두 이름은 같은 값이다.

⑧은 사용자 제한 태그와 겹치고 유효한 사장님 확인값이 없는 재료를 질문 후보로 다루며 확률이나 confidence 숫자로 후보를 제거하지 않는다. `risk_ingredients[].confidence`와 `prior_source`는 ⑤ 결과에서 온 추정 재료라면 그대로 보존한다. ③의 확정값처럼 prior 계산을 사용하지 않은 재료는 두 필드를 모두 `null`로 보낸다. `role`은 ④가 정한 값을 입력 세 목록과 최종 `risk_ingredients[]`까지 그대로 보존하며 위험도 계산에는 사용하지 않는다.

`constraint_tags`는 해당 재료가 가진 전체 제한 태그, `matched_tags`는 그중 현재 `user_profile`과 실제로 충돌한 태그다. 최상위 `matched_tags`는 모든 위험 재료와 `risk_tag_observations`에서 충돌한 태그를 중복 제거한 목록이다. ⑧은 이름 문자열로 위험을 추측하지 않고 이 태그들로 판정한다.

`evidence_refs`는 판정에 사용한 `confirmed_results`, `probability_results`, `proposed_variant_ingredients`, `risk_tag_observations`의 근거 ID를 중복 제거한 목록이다. 위험 재료가 없는 SAFE 판정도 부재 확정 근거가 있으면 이 목록에 보존한다.

`owner_cards`는 질문이 필요 없으면 `[]`이고, 필요하면 다음 closed schema의 카드를 재료별 또는 메뉴 애매함 플래그별로 하나씩 담는다. 한 메뉴에서 여러 장을 반환할 수 있다.

```json
{
  "item_id": "scan-123:0",
  "card_id": "card-001",
  "menu_id": 1001,
  "round": 3,
  "previous_question_id": "question-002",
  "ingredient": {
    "ingredient_id": 110,
    "canonical_name": "액젓"
  },
  "ambiguity_flag": null,
  "constraint_tags": [],
  "question": {
    "ko": "이 메뉴에 액젓이 들어가나요?",
    "en": "Does this menu contain fish sauce?"
  },
  "reason_codes": ["low_confidence"],
  "evidence_refs": ["evidence-010"]
}
```

`owner_cards[].item_id`는 현재 메뉴의 `item_id`이고, `card_id`는 Supervisor가 카드마다 한 번 발급해 같은 카드의 `create_owner_verification_request`에 복사한다. `question`은 한국어 `ko`와 요청 `locale` 두 문구만 담으며, `locale: ko`이면 `ko` 하나만 담는다. `owner_cards[].round`는 `1 | 2 | 3`만 허용한다. 첫 질문은 `round: 1`, `previous_question_id: null`이다. ⑧은 `owner_question_states[].confirmation_state`가 `needs_second`이면 `round: 2`, `needs_third`이면 `round: 3`인 카드를 만들고, 직전 `question_id`를 `previous_question_id`로 복사한다. `confirmed`이면 같은 대상에 추가 카드를 만들지 않는다. 질문 차수는 각 카드의 이 필드에서만 관리하며 메뉴 결과에 `owner_question_round`를 중복해서 만들지 않는다. `ingredient`는 재료 카드면 물어볼 재료, 메뉴 애매함 플래그 카드면 `null`이다. 플래그 카드는 `ambiguity_flag`에 플래그 이름, `constraint_tags`에 물어볼 태그(그 플래그에 걸릴 수 있는 태그 중 사용자 금지 태그와 겹친 것)를 넣는다. 예) "이 메뉴의 젓갈에 생선이 들어가나요?" 재료 카드는 `ambiguity_flag: null`, `constraint_tags: []`다.

정보 없는 메뉴(`no_information: true`)는 재료가 없으므로, 사용자에게 금지 태그가 있으면 메뉴 전체를 묻는 카드를 한 장만 만든다: `ingredient: null`, `ambiguity_flag: null`, `constraint_tags`에 사용자 금지 태그 전부, `reason_codes: ["no_information"]`. 메뉴가 DB에 없으면 `menu_id`도 null이다. 이 카드는 손님이 가게에서 사장님께 보여 주는 **화면 표시용**이다. 답이 판정을 바꾸지 않고(무엇이 들어가는지 알 수 없음) 저장할 메뉴 행도 없으므로 ⑦ 저장 요청을 만들지 않는다.

`reason_codes` 허용값은 `low_confidence`(확률 근거 부족: 실질 레시피 근거 5개 미만), `anomaly`(이상 답변), `no_information`(정보 없음), `unknown_tag`(태그 모름), `expired`(3개월 만료), `high_risk_review`(재검증 발견), `ambiguous_menu`(메뉴 재료 목록 밖에 사용자 제한에 걸릴 재료가 있을 수 있음), `unconfirmed`(사장님 확인값 없음. 다른 이유가 하나도 없을 때만)이며 한 카드에 여러 값을 넣을 수 있다. 모든 카드에는 이유가 하나 이상 있다. 카드 정렬 우선순위는 `anomaly → expired → unknown_tag → ambiguous_menu → high_risk_review → low_confidence → no_information → unconfirmed`이다.

`high_risk_review.required`는 해당 메뉴가 고가 모델 재검증 후보인지 나타낸다. `reason_code`는 `anomaly | unknown_tag | no_information | partial_information | insufficient_web_sources | null`이며 `required: false`이면 반드시 `null`이다. `insufficient_web_sources`는 ⑥ warning `insufficient_sources`(유효 출처 부족), `web_search_deadline_exceeded`(시간 초과로 일부만 수집), `web_llm_extraction_failed`(LLM 추출 실패) 셋을 모두 포함한다. 이 값은 Judge Graph가 메뉴판 전체 후보 중 최대 3개를 고르기 위한 신호이며 ⑧의 현재 `risk_level`을 바꾸지 않는다.

`probability_results`의 `anomaly_locked`는 ③이 override 거부한 재료가 ④를 거쳐 ⑤까지 온 값을 그대로 옮긴다. Supervisor나 ⑧이 이 값을 제거하지 않는다.

③의 3-State와 `expired`는 `confirmed_results[]`로 그대로 전달하며 boolean status로 축약하지 않는다. ④·⑤의 `warnings`도 요청 `warnings`에 누적한다. 다음 중 하나라도 있으면 SAFE를 허용하지 않는다.

- `forces_caution: true` warning이 있음
- `no_information: true`
- `information_status != complete`
- `anomaly_locked: true`
- `probability_results`가 비어 있지 않음
- `proposed_variant_ingredients`가 비어 있지 않음
- `risk_tag_observations`가 사용자 금지 태그와 겹침
- `menu_ambiguity_flags`에 사용자와 관련 있고 사장님 답(`flag_confirmations`)으로 풀리지 않은 플래그가 있음

확률값이나 변형 제안만 있는 경우는 Hard Evidence가 아니므로 확률이 낮더라도 CAUTION이다.

`risk_tag_observations`는 사용자 프로필의 대응 제한(`no_spicy` 등)과 겹칠 때 CAUTION 근거로만 사용한다. hard evidence가 아니므로 이것만으로 DANGER를 확정하지 않으며, ⑧은 필요하면 `owner_cards`를 만든다.

`menu_ambiguity_flags`는 ④ 응답(④ 확장을 건너뛰는 exact complete 경로에서는 Bundle의 `ontology.menu.ambiguity_flags`)을 그대로 넣는다. **경로와 관계없이 빠뜨리지 않는다.** 모든 재료가 사장님 확인값이어도 목록 밖 젓갈·육수 때문에 SAFE가 잘못 나갈 수 있기 때문이다. `flag_confirmations`는 ③ 응답을 그대로 넣는다. 플래그가 사용자와 관련 있는지(플래그별로 걸릴 수 있는 태그와 사용자 금지 태그가 겹치는지), 사장님 답으로 풀리는지는 ⑧이 정한다 (⑧ §1-1, §2-1 5-2). 사장님이 "들어 있다"고 답했고 물어본 태그가 모두 사용자 금지 태그면 hard evidence로 DANGER가 될 수 있다. 플래그만으로는 DANGER를 확정하지 않는다.

### 4-8. ⑨ Curation Tool

#### 요청 — `CurationRequest`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "scan",
    "item_id": null
  },
  "decision_results": [
    {
      "item_id": "scan-123:1",
      "source_index": 1,
      "menu_id": 1002,
      "menu_category_id": 12,
      "normalized_menu_name": "비빔밥",
      "risk_level": "safe",
      "evidence_basis": "confirmed",
      "information_status": "complete",
      "risk_ingredients": [],
      "matched_tags": [],
      "evidence_refs": ["evidence-010"]
    },
    {
      "item_id": "scan-123:0",
      "source_index": 0,
      "menu_id": 1001,
      "menu_category_id": 11,
      "normalized_menu_name": "김치찌개",
      "risk_level": "caution",
      "evidence_basis": "estimated",
      "information_status": "complete",
      "risk_ingredients": [],
      "matched_tags": [],
      "evidence_refs": ["evidence-011"]
    }
  ],
  "curation_contents": [
    {
      "menu_id": 1002,
      "content_id": "culture-101",
      "locale": "ko",
      "title": "비빔밥의 구성과 먹는 방법",
      "content": "검수된 문화 설명 본문",
      "summary": "비빔밥의 문화적 배경",
      "source_id": "curation-source-11",
      "constraint_tags": [],
      "review_status": "approved",
      "updated_at": "2026-10-12T03:00:00Z"
    }
  ],
  "user_profile": {
    "religion_type": "halal",
    "is_vegetarian": false,
    "vegetarian_type": null,
    "no_alcohol": false,
    "allergies": [],
    "no_spicy": false
  },
  "locale": "ko",
  "limit": 5,
  "warnings": [],
  "errors": []
}
```

`decision_results`에는 SAFE 후보만 거르지 않고 ⑧ 처리가 끝난 전체 메뉴를 전달한다. `decision_results[].evidence_refs`는 해당 ⑧ 결과의 최상위 `evidence_refs`를 그대로 전달하고, `source_index`는 메뉴판에서의 원래 순서다. ⑨가 전체 메뉴 순서를 만든 뒤, 그중 `risk_level: safe`이면서 `information_status: complete`인 항목만 추천 후보로 사용한다.

`curation_contents`는 Supervisor가 `KnowledgeBundle.curation_index.contents[]`에서 꺼내 그대로 전달하며 §1-3과 같은 closed schema다. ⑨는 별도 DB나 인덱스를 직접 조회하지 않는다. status가 `not_found | unavailable`이면 `[]`로 전달하고, `unavailable` warning은 보존한다. 콘텐츠가 없어도 전체 `menu_order`는 만들며 추천은 문화 콘텐츠 없이 정해진 fallback 점수만 사용할 수 있다.

#### 응답 — `CurationResponse`

```json
{
  "context": {
    "schema_version": "2.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-123",
    "store_id": 123456,
    "call_scope": "scan",
    "item_id": null
  },
  "menu_order": ["scan-123:1", "scan-123:0"],
  "recommendations": [
    {
      "rank": 1,
      "item_id": "scan-123:1",
      "menu_id": 1002,
      "menu_name": "비빔밥",
      "recommendation_score": 1.0,
      "reason_codes": ["safe_confirmed", "culture_match"],
      "culture_contents": [
        {
          "content_id": "culture-101",
          "title": "비빔밥의 구성과 먹는 방법",
          "summary": "검증된 인덱스에서 가져온 요약",
          "source_id": "curation-source-11"
        }
      ],
      "sponsored": false
    }
  ],
  "warnings": [],
  "errors": []
}
```

`menu_order`는 전체 `decision_results`의 `item_id`를 위험도와 근거 기준으로 정렬한 배열이며 입력의 모든 item_id를 정확히 한 번씩 포함한다. ⑨는 표시 순서와 SAFE 추천·문화 콘텐츠만 반환하며 기존 판정·근거·위험 재료를 수정할 수 없다. SAFE 후보가 없어도 ⑨를 호출하고 `menu_order`와 빈 `recommendations`를 반환한다.

전체 메뉴는 `safe` → `caution + estimated` → `caution + unknown` → `danger` 순서로 배치하고, 같은 구간에서는 `source_index` 순서를 유지한다. 확률값은 전체 메뉴 정렬 기준으로 사용하지 않는다.

`recommendation_score`는 검증된 문화 콘텐츠가 있으면 `1.0`, 없으면 `0.5`이며 안전도·확률·스폰서 여부를 포함하지 않는다. `reason_codes`는 현재 `safe_confirmed | culture_match`만 사용하고 `profile_match`는 사용자 선호 데이터가 생길 때까지 만들지 않는다. `culture_contents`의 네 필드(`content_id`, `title`, `summary`, `source_id`)는 모두 필수 문자열이다. 같은 `recommendation_score`는 `menu_id` 오름차순으로 정렬한다.

---

## 5. 라우팅 규칙

| 상황 | route | 호출 경로 |
|---|---|---|
| 모든 관련 재료가 exact로 확인됨 | `exact_only` | ③ → ④ tag lookup → ⑧ |
| DB 메뉴 + 미확인 재료 있음 | `db_bayes` | ③ → ④ → ⑤ → ④ tag lookup → ⑧ |
| DB 등록/런타임 변형 메뉴 | `db_variant_bayes` | ③ → ④ → `exact_inherited` 보류 node → ⑤ → ④ tag lookup → ⑧ |
| DB에 없고 웹서치 성공 | `web_bayes` | ④ → ⑥ 수집 → ④ 매핑 → ⑥ 집계 → ⑤ → ④ tag lookup → ⑧ |
| DB에도 없고 웹서치 실패 | `no_information` | ④ → ⑥ → ⑧ |

`no_information` 경로에서는 SAFE 판정을 허용하지 않는다. ⑧이 CAUTION 이상을 유지하도록 `no_information: true`를 반드시 전달한다.

---

## 6. 예외 처리

### 6-1. 공통 오류 모델

```json
{
  "code": "web_search_timeout",
  "node": "web_search",
  "item_id": "scan-123:0",
  "message": "웹 검색 시간이 초과되었습니다.",
  "retryable": true,
  "fallback": "no_information"
}
```

- 읽기 전용이고 멱등인 호출만 제한적으로 재시도한다.
- 입력 검증 실패와 ⑦ 저장 명령은 Supervisor가 자동 재시도하지 않는다.
- `trace_id`, `item_id`, node, route, latency를 구조화 로그로 남긴다.
- 사용자 프로필과 원문 메뉴는 로그에 그대로 남기지 않고 마스킹·요약 정책을 적용한다.

| 상황 | 처리 |
|---|---|
| `store_id` 누락·null·문자열·0 이하 | `store_id_required`, ①~⑨ 호출 금지 |
| ② Menu Normalization Agent 실패 | 원본 메뉴명을 보존하고 ④ longest-match/unknown 경로로 넘김 |
| ③ 조회 에러 | 해당 메뉴는 ④⑤⑧ heavy path로 보내되 로그 남김 |
| ④ cycle 감지 | `forces_caution: true` warning과 확장 가능한 재료를 ⑤·⑧에 전달 |
| ④ 치명적 조회 실패 | `information_status: partial`, ⑥ 또는 ⑧ 보수 경로 |
| ⑤ 계산 실패 | 해당 재료를 `unknown`, `information_status: partial`로 ⑧에 전달 |
| ⑥ deadline 초과 | 수집 결과나 캐시가 있으면 모은 결과를 반환하고 `web_search_deadline_exceeded`; 둘 다 없을 때만 `found: false`, `no_information: true` |
| ⑦ 명령 생성 실패 | 판정은 반환하되 저장 명령 누락을 error에 기록, 자동 재시도 금지 |
| ⑧ 판정 실패 | 해당 메뉴 `status: partial_failed`, `risk_level: caution`, `evidence_basis: unknown`, `information_status: partial`로 채우고 "확인하지 못했어요" fallback 문구 사용 |
| 고위험 재검증 timeout/error | 기존 판정·질문·문구를 그대로 반환하고 다음 메뉴 처리 계속 |
| ⑨ timeout/error | 추천은 빈 배열, `menu_order`는 `source_index` 순 fallback, 기존 판정 결과는 정상 반환 |
| 일부 메뉴만 실패 | 실패 메뉴는 CAUTION 이상, 나머지 메뉴는 정상 결과 반환 |

---

## 7. 테스트 케이스

| # | 입력 | 기대 동작 | 검증 포인트 |
|---|---|---|---|
| 1 | 양의 정수 `store_id` + 이미지 | Prepare ①→②-A, Judge ②-B→메뉴별 라우팅 | 모든 하위 호출과 응답에 같은 `store_id` 포함 |
| 2 | `store_id` 누락/문자열/0 이하 | 즉시 검증 실패 | 모든 하위 호출 없음 |
| 3 | Exact complete 메뉴 | 확장·⑤는 스킵하고 ④ tag lookup 후 ⑧ | confirmed 기반 판정과 태그 유지 |
| 4 | Exact 일부 확인 | 확인 재료 제외, 미확인 재료만 ④⑤ | 부분 확인 유지 |
| 5 | `flagged_anomaly` 포함 | override 금지, ⑤와 ⑧까지 anomaly 신호 유지 | SAFE로 내려가지 않음 |
| 5-a | `expired: true`, `flagged_anomaly: false` | heavy path 진행 + `anomaly_locked: false` + ⑧에 expired 전달 | 만료를 이상 답변으로 잘못 설명하지 않음 |
| 6 | 차돌된장찌개 런타임 변형 | ④ variant_suggested → ⑤ scale 0.5 → ⑧ caution 가능 | DB 자동 INSERT 없음 |
| 7 | unknown 메뉴 + 웹서치 실패 | ⑧에 `no_information: true` | CAUTION 이상 |
| 7-a | 웹서치가 deadline 전에 일부 출처만 수집 | 모은 결과로 계속 처리 + `web_search_deadline_exceeded` | 부분 근거를 버리지 않음 |
| 8 | 웹서치 성공 | ⑥ 수집 → ④ 매핑 → ⑥ 집계 후 ⑤에 사용, ⑦에는 관리자 검토 항목 생성 | 표준화 전에 재료 개수를 세지 않음 |
| 9 | 메뉴 3개 중 1개 timeout | 2개 정상 + 1개 보수적 부분 응답 | 전체 요청 실패 없음, 순서 유지 |
| 10 | ⑨ Curation 실패 | 판정 결과 정상 반환 + 추천 빈 배열 | 판정 결과 변경 없음 |
| 11 | OCR `needs_retake` | ②~⑨ 미호출 + 재촬영 응답 | 불량 OCR 텍스트로 SAFE 판정하지 않음 |
| 12 | exact `present` 돼지고기 + halal 프로필 | `tag_lookup`(④)이 `constraint_tags: [is_pork]`를 채운 뒤 DANGER | 모든 경로에서 확인값 태그 손실 없음 |
| 12-a | 일부 확인 메뉴 + 돼지고기 `present` | ④⑤ 이후 공통 `tag_lookup`이 확인값의 `is_pork`를 채움 | heavy path에서도 확인값 태그 손실 없음 |
| 12-b | ⑥ 웹 재료가 표준 재료로 매핑 안 됨 | ④ `unmapped_constraint_tags` warning, CAUTION 이상 | 태그 없이 SAFE로 새지 않음 |
| 13 | ④ 재료가 ⑤를 통과 | `constraint_tags`·`anomaly_locked`·`evidence_refs` 동일 | ⑧ 입력까지 메타데이터 손실 없음 |
| 14 | 확률 재료 또는 변형 제안만 존재 | 확률값이 낮아도 CAUTION | Hard Evidence 없이 SAFE 금지 |
| 15 | 한 메뉴에 확인할 재료 2개 | `owner_cards` 2개 + ⑦ 명령 2개 | 질문 누락 없음 |
| 16 | SAFE 후보가 없는 메뉴판 | ⑨ 호출, `recommendations: []`, 전체 `menu_order` 반환 | 추천과 전체 정렬 분리 |
| 17 | ⑧ 판정 실패 | `partial_failed`, CAUTION, unknown, partial | ⑨ 정렬 가능한 보수 fallback |
| 18 | 웹서치 대상 메뉴 7개 | 동시에 최대 5개 실행, 나머지는 완료 순서대로 시작 | 검색·LLM 동시성 상한 유지 |
| 19 | 숨은 재료 액젓 | ④의 `role: hidden`이 ⑤·⑧·최종 결과까지 유지 | 안내용 역할 정보 손실 없음 |
| 20 | 재검증 후보 메뉴 5개 | 우선순위와 `source_index`로 최대 3개만 재검증 | 판정값은 유지하고 카드·문구만 보강 |
| 21 | runtime 변형에 `base_menu_id` 존재 | `exact_inherited` 보류 node 통과, ③ 재호출 없음, `inherited_confirmations: []` | 사장님 답변을 확률 계산에 사용하지 않음 |
| 22 | `prepared_items_hash` 불일치 | HTTP 409로 Judge 거부 | ②~⑨ 호출 없음 |
| 23 | 필수 Bundle slice 또는 candidate 대응 누락 | `bundle_miss`, HTTP 422 | 누락 자료를 추측해 판정하지 않음 |
| 24 | safety slice `unavailable` | 가능한 경로는 계속 실행하되 `partial_failed`, SAFE 금지 | 장애를 데이터 없음으로 오인하지 않음 |
| 25 | 여러 corpus 관측치와 신뢰도 weight | ⑤가 corpus별 가중 후 posterior와 90% 신용구간 반환 | 관측치가 Bundle→④→⑤에서 손실되지 않음 |
| 26 | 생선 알레르기 + 김치찌개 모든 재료 사장님 확인(exact complete) + `ontology.menu.ambiguity_flags: [has_unclear_jeotgal]` | ④ 확장을 건너뛰어도 ⑧에 `menu_ambiguity_flags` 전달, CAUTION + 플래그 질문 카드 + ⑦ 명령(`ingredient: null`) | 플래그가 빠져 SAFE로 새지 않음 |
| 27 | 26번 + `flag_confirmations`에 `is_fish` `absent` 답 | ⑧ SAFE 가능 | 플래그 답이 Bundle→③→⑧에서 손실되지 않음 |
| 28 | 할랄 사용자 + unknown 메뉴 + 웹서치 실패 | ⑧ CAUTION + 메뉴 전체 카드 1장(`ingredient: null`, `ambiguity_flag: null`), ⑦ 명령 없음, `owner_request` node 건너뜀 | 화면 표시용 카드로 저장 요청을 만들지 않음 |
| 29 | 같은 대상의 1차·2차 답변 불일치 후 Bundle에 `confirmation_state: needs_third` | Bundle → ③ → ⑧로 상태를 보존하고 `round: 3`, `previous_question_id`가 있는 카드·저장 명령 생성 | 3차 질문 누락 없음, 동일 Judge 재시도 시 명령 중복 없음 |

---

## 8. 남은 협의

설계와 JSON 계약의 확정 내용은 각 본문에만 기록한다. 아래에는 구현 전에 실제 담당자와 값을 맞춰야 하는 항목만 남긴다.

- LangGraph·LangChain과 provider 패키지의 정확한 호환 버전
- Prepare/Judge timeout, Prepare 결과 저장 위치·보존 기간, Judge API 재시도·멱등성
- `persistence_commands` 적용 시점과 저장 실패 명령의 보관·재처리 방식
- 메뉴판 단위 ③④⑤⑧ batch 최적화 여부와 동시성 상한
- 메뉴별 부분 실패를 사용자 화면에 표시할 문구
- 메뉴 애매함 플래그 사장님 답(`flag_confirmations`)의 저장 테이블 (백엔드·DB 스키마)
- 큐레이션 탭(메뉴판을 찍지 않고 들어오는 화면)에서 ⑨를 부르는 진입 경로 — 이 문서의 Prepare/Judge 밖 별도 API인지 (⑨ §8)

---

## 9. 구현 추적

이 문서는 실행 계약과 설계의 기준만 유지한다. 일정·담당자·완료 체크는 중복과 불일치를 막기 위해 GitHub Issues와 Projects Iteration에서 관리한다.
