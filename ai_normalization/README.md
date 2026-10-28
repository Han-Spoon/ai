# Menu Normalization Agent

`docs/2-agent-normalization.md`의 ② Menu Normalization Agent 구현입니다.

문서에서 확정된 v1 범위인 ②-A 결정론적 정제, ②-B 후보 선택, OpenAI 구조화 LLM 연결까지 구현됐습니다. API 오류나 timeout이면 보수적인 결정론적 fallback을 사용합니다.

- ②-A `NameCleaningAgent`: OCR 원문에서 장식·가격·인분·크기 옵션을 분리하고, 위험 가능 토큰을 보존한 검색명을 만듭니다.
- ②-B `CandidateSelectionAgent`: 백엔드가 전달한 최대 5개 후보 안에서 문서의 v1 점수 정책으로 후보를 선택합니다.
- 모든 입출력은 `extra="forbid"` Pydantic 모델이며 `context`, `warnings`, `errors`를 보존합니다.
- DB 조회와 물리 DB 쓰기는 하지 않습니다.

실제 다국어/후보 해소는 `OpenAINormalizationResolver`가 담당합니다. 기본 모델은 `gpt-5.4-mini`이며 `OPENAI_API_KEY`만 설정하면 Supervisor가 Resolver를 자동 연결합니다. 키가 없으면 사전·점수 기반 결정론적 경로만 동작하며, 모호한 후보는 안전하게 `ambiguous`로 남습니다.

```bash
export OPENAI_API_KEY="..."
```

선택 설정은 `NORMALIZATION_OPENAI_MODEL`, `NORMALIZATION_OPENAI_TIMEOUT_SECONDS`, `NORMALIZATION_OPENAI_MAX_RETRIES`, `NORMALIZATION_OPENAI_MAX_OUTPUT_TOKENS`입니다. 모두 기본값이 있어 일반 실행에는 API 키만 필요합니다.

Supervisor 밖에서 직접 사용할 때도 Resolver를 lifespan에서 한 번 만들고 두 Agent가 공유합니다.

```python
from ai_normalization import (
    CandidateSelectionAgent,
    NameCleaningAgent,
    OpenAINormalizationResolver,
)

resolver = OpenAINormalizationResolver.from_env()
name_cleaning = NameCleaningAgent(resolver)
candidate_selection = CandidateSelectionAgent(resolver)
```

테스트:

```bash
pytest -q ai_normalization/tests
```
