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
