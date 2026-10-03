# 문서 인덱스

Catoin AI 설계 문서 모음입니다. 아래 순서대로 읽으면 전체 구조가 잡힙니다.

## 읽는 순서

| 순서 | 문서 | 내용 |
|---|---|---|
| 1 | [ppt-baseline.md](ppt-baseline.md) | **제출 PPT 기준 문서 (AI 파트 7~10쪽).** 이미 제출된 내용이라 수정 대상이 아니며, 다른 문서·코드와 충돌하면 이 문서가 우선합니다 |
| 2 | [catoin-multi-agent-architecture.md](catoin-multi-agent-architecture.md) | 전체 서비스 흐름도, 케이스별 시나리오, 오케스트레이션 구조, Agent·Tool별 역할 |
| 3 | [catoin-db-schema.md](catoin-db-schema.md) | DB 스키마 v3 — 전역 참조 테이블, 가게 스코프 테이블, 캐시 테이블, 백엔드 전달용 제약사항 |
| 4 | Agent·Tool 스펙 | 아래 표 참고 |

## Agent · Tool 스펙

명칭은 [`ppt-baseline.md`](ppt-baseline.md) 7쪽 표기를 따릅니다. 기준 문서에는 Supervisor가 번호 없이 적혀 있지만, 10/2 회의 결정("0: supervisor / 1(ocr tool) ~ 9(curation tool)")에 따라 **⓪를 부여해 ⓪~⑨로 통일**합니다. 파일명 앞자리도 번호와 맞춥니다.

| 문서 | Agent / Tool | 역할 |
|---|---|---|
| [agent-0-supervisor.md](agent-0-supervisor.md) | ⓪ Supervisor Agent | `store_id` 검증 + 전체 오케스트레이션 |
| — | ① OCR Tool | 메뉴판 이미지에서 메뉴명·가격·설명 텍스트를 구조화하여 추출 |
| [agent-2-normalization.md](agent-2-normalization.md) | ② Menu Normalization Agent | OCR 오탈자·표기 변형·다국어 메뉴명을 표준명으로 해석 |
| [agent-3-exact.md](agent-3-exact.md) | ③ Exact Feedback Tool | 재료 단위 확정 근거(Hard Evidence) 조회 |
| [agent-4-DBontology.md](agent-4-DBontology.md) | ④ DB / Ontology Tool | 온톨로지 조회 + 재귀 확장 + 변형 태깅 |
| [agent-5-Statistics.md](agent-5-Statistics.md) | ⑤ Bayesian Tool | 가게별 α/β 기반 재료 포함 확률 계산 |
| [agent-6-websearch.md](agent-6-websearch.md) | ⑥ Web Search Agent | DB 미등록 메뉴의 외부 재료 근거 수집 (soft evidence) |
| [agent-7-dbupdate.md](agent-7-dbupdate.md) | ⑦ DB Update Tool | 저장 명령 생성 · 관리자 컨펌 게이트 |
| [agent-8-xai.md](agent-8-xai.md) | ⑧ Decision Policy / XAI Agent | 최종 판정(Danger/Caution/Safe)과 근거 설명 생성 |
| — | ⑨ Curation Tool | 판정 완료 후 안전 후보 랭킹 · 메뉴/한식 문화 콘텐츠 추천 |

> **스펙 문서가 아직 없는 노드**
> - ① OCR Tool — 구현은 [`../ai_ocr/README.md`](../ai_ocr/README.md)에 있고, 별도 스펙 문서는 없습니다.
> - ⑨ Curation Tool — 기준 문서에는 있으나 스펙 문서가 없습니다. (문서 #81 / 구현 #78)

> **Agent vs Tool**: Agent는 판단하고(LLM 추론이 필요한 판단 노드), Tool은 수행합니다(정해진 절차를 수행하는 실행 노드). 기준은 [`ppt-baseline.md`](ppt-baseline.md) 7쪽 "Agent와 Tool의 Boundary".

## 문서를 읽기 전에

- 각 Agent·Tool 문서 끝에 **"미확정 항목 (팀 확인 대기)"** 절이 있습니다. 구현 전 반드시 확인하세요.
- **"확정된 결정 (변경 금지)"** 절의 항목은 임의로 바꾸지 않습니다. 변경이 필요하면 이슈로 올립니다.
- 작업 규칙(커밋 형식, 이슈·PR 규칙, 용어집)은 [`../AGENTS.md`](../AGENTS.md)에 있습니다. Claude Code·Codex 공용입니다.
