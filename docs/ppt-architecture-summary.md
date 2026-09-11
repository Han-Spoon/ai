# 번역이 아니라 신뢰: AI 아키텍처 핵심 요약

> PPT 제작용 압축 요약본. 상세 스펙은 `catoin-multi-agent-architecture.md`, `catoin-db-schema.md`, `agent-*.md` 참고.

---

## 1. 핵심 메시지 (1순위)

- 이 앱의 본질은 **번역기가 아니라 신뢰 판정 엔진**
- 질문은 "뭐라고 써있나"가 아니라 **"내가 먹어도 안전한가"**
- 단순 번역은 안전을 보장 못 함 → 재료 추론 + 확률 판정 + 근거 설명까지 필요

---

## 2. 왜 어려운가 (문제 정의)

- 메뉴판엔 숨은 재료·변형 메뉴가 텍스트로 안 드러남
- 사장님께 매번 물어볼 수 없고, 웹 크롤링 정보는 **그 가게의 실제 레시피가 아님**
- → **정보가 불완전한 상태에서도 안전하게 판단**해야 하는 게 핵심 난제

---

## 3. 신뢰를 설계하는 4가지 장치

1. **가게별 베이지안 확률 모델(Store-scoped Bayesian Inference)**
   확정 안 된 재료만 **이 가게에 특화된 확률로 점진 추정** (웹서치·변형 태깅 증거 누적, Beta-Binomial 업데이트) — 다른 가게 데이터와 절대 안 섞임
2. **증거 계층화(Evidence Hierarchy)**
   사장님이 직접 확인해주면 확률 계산을 **건너뛰고 즉시 확정값(SAFE/DANGER)**으로 처리 — 확정값(Hard Evidence) > 추정값(Soft Evidence), 서로 다른 신뢰도는 절대 안 섞음
3. **FN-Minimization 원칙 (North Star Metric)**
   정보 부족·애매함 → 무조건 안전 쪽(CAUTION 이상) — **거짓 안심(False Negative) 최소화**가 최우선
4. **Human-in-the-Loop 데이터 거버넌스**
   AI가 혼자 추측한 데이터(웹서치·변형 태깅)는 **관리자 컨펌 전엔 DB에 반영되지 않음** — AI 환각 차단

---

## 4. 기술 아키텍처 (고도화 포인트)

- **멀티 에이전트 시스템(Multi-Agent System)** — Supervisor 오케스트레이터가 지휘하는 8개 전문 에이전트 협업 구조
- **재료 온톨로지(Ingredient Ontology)** — Taxonomy(is-a) + Composition(part-of, N단계 재귀 확장) + Allergen Tagging 3축 구조, 순환참조 탐지(Cycle Detection)
- **베이지안 추론 엔진(Bayesian Inference Engine)** — Beta-Binomial posterior update, 이벤트 소싱(Event Sourcing) 기반 증거 로그
- **변형 메뉴 자동 인식(Variant Detection)** — Longest-Match + Remain-Token Tagging으로 메뉴명 변형(예: "차돌된장찌개") 자동 추론
- **설명가능 AI(XAI)** — 블랙박스 판정이 아닌 근거 기반 설명, 확정형/추정형 신뢰도(Confidence) 구분 제공
- **6개 언어 다국어 대응** — 한/영/일/중국어 간체·번체/스페인어

---

## 5. 한 줄 요약

> **"번역이 아니라, 근거 있는 안심을 설계한다."**
