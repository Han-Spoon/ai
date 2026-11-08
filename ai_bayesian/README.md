# ai_bayesian — ⑤ Bayesian Tool

스펙: [`docs/5-tool-Statistics.md`](../docs/5-tool-Statistics.md). 입출력 형식은 [`ai_supervisor/contracts/bayesian.py`](../ai_supervisor/contracts/bayesian.py)를 그대로 쓴다.

확인되지 않은 재료마다 "이 메뉴에 들어갈 확률"을 Beta-Binomial로 계산한다. 판정하지 않고, DB를 조회·저장하지 않고, LLM을 쓰지 않는다.

| 진입점 | 하는 일 |
|---|---|
| `BayesianTool` / `estimate_probabilities` (`bayesian` node) | 재료별 `posterior_mean`, `alpha_post`, `beta_post`, `prior_source`, `confidence` |

계산 순서 (재료 하나):

1. 사이트별 레시피 관측값 `k`, `n`에 출처 신뢰도를 곱해 `α = 1 + Σ w·k`, `β = 1 + Σ w·(n − k)` (§3-3). 웹 재료는 출처 키 `web_search`, 나머지는 `corpus:{사이트}`. 목록에 없는 출처는 0.5
2. 큐레이션 레시피에 있으면 α에만 `w(curated)`를 더한다
3. 가게 증거가 있으면 `α − 1`, `β − 1`을 그대로 더한다 (`prior_source: store`). 가게 없는 스캔이면 쓰지 않는다
4. 최종 α·β를 같은 비율로 줄인다: 변형 제안 0.5, 하위 재료 `max(0.5^depth, 0.125)` (직접 관측이면 1.0). 평균은 그대로, 분포만 넓어진다
5. `confidence = 1 − 1/√(ν+1)`, `ν = α + β − 2`

가게 증거(3번)는 `_store_priors` 한 곳에서만 읽는다. 팀에서 가게 보정을 빼기로 하면 그 함수만 비우면 된다.

## 성분 재현율 평가 (`python -m ai_bayesian.evaluate`)

시드 데이터로 "메뉴에 실제로 든 알레르기 성분을 얼마나 놓치지 않는가"를 잰다.

- 정답: 공식 레시피(`ai_ruleengine/data/menus.csv`) 재료와 그 숨은 재료의 태그
- 모델: ④→⑤를 **크롤링 레시피 통계만으로** 돌린다. 공식 레시피는 가려서 모델이 정답을 보지 못하게 한다
- 점수: (메뉴, 성분)마다 그 성분이 붙은 재료 중 가장 높은 확률
- 출력: 기준 확률별 재현율·정밀도·F2·놓친 수, AUC, 한 번도 안 잡힌 (메뉴, 성분) 목록
- ⑧은 태그가 붙은 재료가 있으면 확률과 상관없이 CAUTION·질문으로 보내므로, 가장 낮은 기준(1%)의 재현율이 실제 서비스 재현율에 가깝다
- 한계: 정답이 실제 가게 레시피가 아니라 공식 레시피 기준이고, 정답과 모델이 같은 태그 사전을 쓴다
- `tests/test_evaluate.py`가 재현율·F2·AUC 하한을 지켜, 바꾼 코드가 지표를 떨어뜨리면 테스트가 실패한다
