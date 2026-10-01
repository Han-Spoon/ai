# 문서 인덱스

Catoin AI 설계 문서 모음입니다. 아래 순서대로 읽으면 전체 구조가 잡힙니다.

## 읽는 순서

| 순서 | 문서 | 내용 |
|---|---|---|
| 1 | [ppt-baseline.md](ppt-baseline.md) | **제출 PPT 기준 문서 (AI 파트 7~10쪽).** 이미 제출된 내용이라 수정 대상이 아니며, 다른 문서·코드와 충돌하면 이 문서가 우선합니다 |
| 2 | [catoin-multi-agent-architecture.md](catoin-multi-agent-architecture.md) | 전체 서비스 흐름도, 케이스별 시나리오, 오케스트레이션 구조, 에이전트별 역할 |
| 3 | [catoin-db-schema.md](catoin-db-schema.md) | DB 스키마 v3 — 전역 참조 테이블, 가게 스코프 테이블, 캐시 테이블, 백엔드 전달용 제약사항 |
| 4 | 에이전트 스펙 ①~⑧ | 아래 표 참고 |

## 에이전트 스펙

| 문서 | 에이전트 / 툴 | 역할 |
|---|---|---|
| [agent-1-supervisor.md](agent-1-supervisor.md) | Supervisor Agent | `store_id` 검증 + 전체 오케스트레이션 |
| [agent-2-normalization.md](agent-2-normalization.md) | ② Menu Normalization Agent | OCR 오탈자·표기 변형·다국어 메뉴명을 표준명으로 해석 |
| [agent-3-exact.md](agent-3-exact.md) | ③ Exact Feedback Tool | 재료 단위 확정 근거(Hard Evidence) 조회 |
| [agent-4-DBontology.md](agent-4-DBontology.md) | ④ DB / Ontology Tool | 온톨로지 조회 + 재귀 확장 + 변형 태깅 |
| [agent-5-Statistics.md](agent-5-Statistics.md) | ⑤ Bayesian Tool | 가게별 α/β 기반 재료 포함 확률 계산 |
| [agent-6-7-websearch-dbupdate.md](agent-6-7-websearch-dbupdate.md) | ⑥ Web Search Agent / ⑦ DB Update Tool | 외부 근거 수집 · 관리자 컨펌 게이트 |
| [agent-8-xai.md](agent-8-xai.md) | ⑧ Decision Policy / XAI Agent | 최종 판정(Danger/Caution/Safe)과 근거 설명 생성 |

> ⑨ Curation Tool은 기준 문서에는 있으나 스펙 문서가 아직 없습니다. (이슈 #78)

## 문서를 읽기 전에

- 각 에이전트 문서 끝에 **"미확정 항목 (팀 확인 대기)"** 절이 있습니다. 구현 전 반드시 확인하세요.
- **"확정된 결정 (변경 금지)"** 절의 항목은 임의로 바꾸지 않습니다. 변경이 필요하면 이슈로 올립니다.
- 작업 규칙(커밋 형식, 이슈·PR 규칙, 용어집)은 [`../CLAUDE.md`](../CLAUDE.md)에 있습니다.
