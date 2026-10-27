# Menu Normalization Agent

`docs/2-agent-normalization.md`의 ② Menu Normalization Agent 구현입니다.

문서에서 확정된 v1 범위인 ②-A 결정론적 정제와 ②-B 후보 선택 구현은 완료됐습니다. 구체 LLM 연결은 미확정 이슈 #164이며, 연결 전에는 보수적인 결정론적 fallback을 사용합니다.

- ②-A `NameCleaningAgent`: OCR 원문에서 장식·가격·인분·크기 옵션을 분리하고, 위험 가능 토큰을 보존한 검색명을 만듭니다.
- ②-B `CandidateSelectionAgent`: 백엔드가 전달한 최대 5개 후보 안에서 문서의 v1 점수 정책으로 후보를 선택합니다.
- 모든 입출력은 `extra="forbid"` Pydantic 모델이며 `context`, `warnings`, `errors`를 보존합니다.
- DB 조회와 물리 DB 쓰기는 하지 않습니다.

실제 다국어/후보 해소 LLM의 모델과 timeout은 문서상 미확정이므로 `NameResolver`, `CandidateResolver` 인터페이스로 분리했습니다. Resolver를 주입하지 않아도 사전·점수 기반 결정론적 경로는 동작하며, 모호한 후보는 안전하게 `ambiguous`로 남습니다.

```python
from ai_normalization import NameCleaningAgent

response = await NameCleaningAgent().run(request)
```

테스트:

```bash
pytest -q ai_normalization/tests
```
