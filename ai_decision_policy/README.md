# ⑧ Decision Policy / XAI Agent — 판정 규칙

사용자 제한과 재료 근거를 대조해 DANGER / CAUTION / SAFE를 정하고, 결과 문장과 사장님께 물을 질문 카드를 만듭니다. 규칙은 [`docs/8-agent-xai.md`](../docs/8-agent-xai.md) §2·§3이 기준입니다.

- 판정은 코드(규칙)로만 정합니다. 같은 입력이면 항상 같은 판정입니다.
- LLM은 판정 뒤의 **이유 문장**(`message.reason`)과 **고위험 재검증**에만 씁니다. `OPENAI_API_KEY`가 없으면 둘 다 꺼지고 규칙만으로 돕니다.
- DANGER·SAFE는 사장님 확인값으로만 나옵니다. 확률은 아무리 높거나 낮아도 CAUTION입니다.

## 파일

```text
ai_decision_policy/
  profile.py   # 사용자 프로필 → 금지 태그 (§2-1 1단계)
  flags.py     # 메뉴 애매함 플래그와 사장님 답 (§1-1 표, §2-1 5-2)
  policy.py    # 판정 (§2-1 2~5단계, §2-2 SAFE 금지 조건), 고위험 재검증 표시 (§2-3)
  cards.py     # 사장님 질문 카드 owner_cards (§1-3, §2-1 7단계)
  notes.py     # 결과 안내 message.notes 종류 고르기 (§4 notes, §4-1)
  messages.py  # 결과 문장 message (판정 한 줄·안내 문구, 7개 언어 고정 템플릿, §4)
  questions.py # 질문 문구 (7개 언어 고정 템플릿, ko 외에는 원어민 확인 전)
  tool.py      # ⓪이 부르는 어댑터 DecisionTool.run(DecisionRequest) -> DecisionResponse
  tests/test_decision_policy.py  # 문서 §7 중 판정·재검증 표시 부분
  tests/test_owner_cards.py      # 문서 §7 중 질문 카드 부분
  tests/test_notes.py            # 문서 §7 중 안내 종류 부분 (13~15, 18)
  tests/test_messages.py         # 결과 문장, 어댑터 (§4, §7 1~3·11~14·16·18·21·31·33)
  tests/test_supervisor_integration.py  # 실제 ④⑤⑧을 ⓪ 메뉴 그래프에 꽂아 실행
```

⓪에는 이렇게 꽂습니다.

```python
from ai_decision_policy import DecisionTool

ItemGraphDependencies(..., decision=DecisionTool(), ...)
```

## 판정 순서

| 순서 | 조건 | 결과 |
|---|---|---|
| 1 | 사장님 확인값으로 금지 재료가 "있음", 또는 플래그 답이 "있음"이고 물은 태그가 모두 금지 태그 | `danger` / `confirmed` |
| 2 | 정보 없음(`no_information`), 또는 판정할 재료가 0개 | `caution` / `unknown` |
| 3 | 확률·변형 추정 재료가 있음 | `caution` / `estimated` |
| 4 | 재료는 모두 사장님 확인값인데 정보가 빠짐 (`information_status`, 경고, 가게 없는 스캔, 풀리지 않은 플래그) | `caution` / `unknown` |
| 5 | 재료는 모두 사장님 확인값인데 매운맛 등 메뉴 속성이 겹침 | `caution` / `estimated` |
| 6 | 그 밖 | `safe` / `confirmed` |

같은 재료에 사장님 확인값(`present`/`absent`)이 있으면 확률보다 확인값을 씁니다 (§6). 예전 칸 `override_eligible`은 보지 않습니다. ⓪ 계약에서 선택 칸이 되어(#368) 빠지거나 `false`로 와도 같습니다 (§1-1).

입력은 ⓪ §4-7 `DecisionRequest`(`ai_supervisor/contracts/decision.py`)이고, 위험 재료와 재검증 표시는 같은 파일의 `DecisionRiskIngredient`·`HighRiskReview`로 냅니다.

## 고위험 재검증 표시 (§2-3)

가게 없는 스캔이면 항상 `required: false`입니다. 아래 신호가 여럿이면 위에 있는 것을 `reason_code`로 씁니다.

| `reason_code` | 신호 | 이 사용자와 관련 있을 때만 |
|---|---|---|
| `unknown_tag` | ④ 경고 `unmapped_constraint_tags`, `constraint_tags_lookup_failed` | 항상 (겹치는지 알 수 없음) |
| `no_information` | `no_information`, 또는 재료 0개 | 금지 태그가 하나라도 있을 때 |
| `partial_information` | `information_status`가 `complete`가 아님 | 금지 태그가 하나라도 있을 때 |
| `insufficient_web_sources` | ⑥ 경고 `insufficient_sources`, `web_search_deadline_exceeded`, `web_llm_extraction_failed` | 금지 태그가 하나라도 있을 때 |

## 사장님 질문 카드 (§1-3, §2-1 7단계)

```python
outcome = decide(request)
cards = build_owner_cards(request, outcome)
```

가게 없는 스캔이거나 금지 태그가 없으면 `[]`입니다. 그 밖에는 아래처럼 만듭니다.

| 카드 | 언제 | 이유 코드 |
|---|---|---|
| 재료 카드 | 금지 태그와 겹치는데 사장님 답(`present`/`absent`)이 없는 재료마다 한 장. 확률로 거르지 않음 | `insufficient_recipe_evidence`(⑤ `confidence < 0.59`), 없으면 `unconfirmed` |
| 플래그 카드 | 사장님 답으로 풀리지 않은 메뉴 애매함 플래그마다 한 장. 물을 태그는 플래그 태그 중 금지 태그 | `ambiguous_menu` |
| 메뉴 카드 | 정보 없는 메뉴(또는 재료 0개)에 한 장. 화면 표시용이라 저장하지 않음 | `no_information` |

- 태그를 모르는 재료는 겹치는지 알 수 없어 카드를 만들지 않습니다
- 사장님에게는 대상마다 한 번만 묻습니다 (10/26 회의). 이미 답한 재료는 이상 답변 표시가 있어도 다시 묻지 않고, 만료·2·3차 질문도 없어 카드에 차수 칸(`round`, `previous_question_id`)이 없습니다 (§9)
- `card_id`는 `card:{item_id}:{대상}`으로, 같은 요청이면 항상 같은 값입니다
- 순서: `ambiguous_menu` → `high_risk_review` → `insufficient_recipe_evidence` → `no_information` → `unconfirmed`. 같은 이유끼리는 확률이 높은 재료, 확률이 없으면 `ingredient_id` 순, 플래그는 이름 순
- 문구: 한국어 `ko`와 사용자 언어(한국어 사용자면 `ko` 하나). LLM 없이 고정 템플릿으로 만듭니다. 재료 이름은 `localized_names`에서 가져오고, 없으면 한국어 이름을 씁니다. `ko` 외 6개 언어는 원어민 확인 전 초안입니다 (#135)
- `high_risk_review` 카드는 고위험 재검증이 더합니다 (§2-3)

## 결과 안내 종류 (§4 `notes`, §4-1)

```python
notes = select_notes(outcome)  # [NoteChoice(code, ambiguity_flag, ingredient, role), ...]
```

어떤 안내를 붙일지 고릅니다. 문장은 아래 "결과 문장"에서 붙입니다. 안내는 판정을 바꾸지 않습니다.

| `code` | 언제 | 한 장 단위 |
|---|---|---|
| `ambiguous_menu` | 이 사용자와 관련 있고 사장님 답으로 풀리지 않은 메뉴 애매함 플래그 | 플래그마다 |
| `hard_to_remove` | 위험 재료(`risk_ingredients`)의 `role`이 `identity`(주재료)나 `hidden`(양념 속 재료) | 역할마다 한 문장 (같은 역할 재료를 묶음, 11/17) |

- 순서: `ambiguous_menu` 먼저, 그다음 `hard_to_remove`를 `risk_ingredients` 순서대로
- `role`이 `component`이거나 없으면 안내를 붙이지 않습니다 (모르는 역할을 지어내지 않음)
- 사용자 금지 태그와 겹치지 않는 재료는 위험 재료가 아니므로 주재료여도 안내가 없습니다

## 결과 문장 (§4, §4-1)

```python
message = build_message(request, outcome)  # DecisionMessage(verdict, reason, notes)
```

사용자 언어(`locale`) 하나로, LLM 없이 고정 템플릿으로 만듭니다. 형식은 프론트 결과 화면(피그마 S10)을 따릅니다 (11/06): 누가 무엇을 확인했는지, 무엇이 아직 확인이 필요한지, 사용자의 어떤 제한에 맞지 않는지. 확률은 넣지 않습니다(화면이 재료마다 보여 줌). 재료 이름은 `localized_names` → 번역 사전 → 한국어 순서입니다.

| 판정 | `verdict` (ko 예) |
|---|---|
| `danger` / `confirmed` | 할랄 기준으로는 드시기 어려워요. 사장님이 돼지고기가 들어 있다고 확인했어요. (먹을 수 있는지를 먼저, 그 태그를 금지하는 제한 이름, 사장님이 "있음"이라고 한 재료, 태그 하나짜리 플래그 답이면 태그 이름). 태그 여러 개를 함께 물은 플래그 답은 "사장님이 육수에 돼지고기나 새우가 들어간다고 답했어요."처럼 "또는"으로 쓴다 |
| `caution` / `estimated` | 비건 기준으로는 드시기 전에 직원 확인이 필요해요. 사장님이 달걀 지단은 넣지 않는다고 확인했어요. 생선이 들어갈 확률이 46% 이상이에요. 어묵이 들어 있을 수 있어요. (확률은 가장 높은 재료 값을 그 성분 이름으로, 사장님 문장은 사장님이 "없음"이라고 한 금지 재료가 있을 때만, 확률 높은 순, 매운맛 같은 메뉴 속성은 태그 이름) |
| `caution` / `estimated`, 겹치는 재료 없음 | 드시기 전에 직원 확인이 필요해요. 피해야 할 재료는 찾지 못했지만, 추정 결과예요. |
| `caution` / `unknown` | 드시기 전에 직원 확인이 필요해요. 재료 정보를 찾지 못했어요. (정보 없음) / 드시기 전에 직원 확인이 필요해요. 아직 확인되지 않은 정보가 있어요. (그 밖) |
| `safe` / `confirmed` | 할랄 기준으로 드실 수 있어요. 사장님이 확인한 재료 중 피해야 할 재료가 없어요. (제한이 없으면 첫 문장은 "드실 수 있어요.") |

- 같은 재료가 태그 묶음별로 두 번 와도(④ #327) 한 번만 씁니다
- 재료 이름은 최대 7개까지 쓰고 나머지는 "외 N개"로 줄입니다 (11/08). 화면이 재료마다 확률을 따로 보여 줍니다
- 안내: `ambiguous_menu`는 "젓갈에 생선이 들어갈 수 있어 확인이 필요해요.", `hard_to_remove`는 "참치는 이 메뉴의 주재료라 빼기 어려울 수 있어요." / "새우는 양념이나 소스 같은 다른 재료 안에 들어 있어 빼기 어려울 수 있어요."
- `reason`(이유 설명)은 아래 LLM이 씁니다. LLM이 없거나 실패·검수 탈락이면 `null`이고, 판정 한 줄과 안내는 그대로 나갑니다 (§4 "LLM 실패"). 단, 결과가 덜 확실한 이유(한계 문장)가 있으면 한계 문장과 카드 안내는 코드 템플릿이라 그대로 나갑니다 (11/23)

## LLM (§0-2, §2-3, §4)

```python
tool = DecisionTool.from_env()              # 키가 있으면 이유 문장 LLM을 붙임
review = HighRiskReviewAgent.from_env()     # ⓪ high_risk_review 자리. 키가 없으면 그대로 돌려줌
```

| 파일 | 하는 일 |
|---|---|
| `llm.py` | OpenAI 클라이언트 하나(Responses API + Pydantic 출력 형식). ②와 같은 방식. 환경 변수 `XAI_OPENAI_*` (`.env.example`) |
| `reason.py` | 이유 문장. 코드가 먼저 경우를 나눔(`reason_route`, 11/23): 이름만으로 왜 걸렸는지 모르는 위험 재료(액젓 → 생선. 원재료 `base`와 누구나 아는 조합 `WELL_KNOWN_SOURCES`(햄 → 돼지고기 등)는 제외)가 있으면 LLM이 확률 높은 묶음 2개까지 짧은 구로 설명하고 코드가 "• 재료: 구" 한 줄씩 만들며(카드 안내, 가장 중요한 한계 한 줄이 뒤에 붙음), 결과가 덜 확실한 이유(웹 레시피 부족, 가게 없는 스캔, 정보 일부 실패 등)는 LLM에 넘기지 않고 `messages.py` 고정 문장으로 붙임. 한계만 있으면 LLM을 부르지 않음. 프롬프트는 그 메뉴에 필요한 규칙·예시만 조립(`reason_instructions`). 자동 검수(#174): 판정 한 줄에 없는 퍼센트·0%·100%, 걸린 재료(`risk_ingredients`)가 아닌 재료 이름(그 이름 안에 든 재료 이름은 허용, 번역 사전 324개 기준), 설명할 묶음을 빠뜨린 문장, CAUTION·DANGER에 "안전해요" 류, 사용자 언어가 아닌데 남은 한글이면 버림. 번역 이름이 없는 재료는 LLM이 번역하고 `name_translations`로 알려 주며, 알려 준 번역은 새 재료로 보지 않음. 판정 한 줄을 그대로 베낀 부분은 잘라냄 |
| `review.py` | 고위험 재검증 (질문 다듬기는 하지 않음, 11/02). 사용자 금지 태그와 겹치는 표준 재료만 LLM에 보여 주고, LLM이 "드물게 들어감"(`few_recipes`)이라고 답한 재료는 버리고, 남은 재료마다 질문 카드(`high_risk_review`)를 더함. 이유 문장은 같은 검수를 거치고, 버린 재료 이름이 나오면 탈락(11/12). 추정인데 "이미 들어 있다"처럼 단정하거나 판정 한 줄을 쉼표만 바꿔 되풀이해도 걸러짐(11/15). 탈락하면 원래 문장을 둠. 실패하면 원래 카드·문장 그대로 |

## 아직 없는 것

- **⓪ 조립**: `app.py` `_build_supervisor_service()`에 `decision=DecisionTool.from_env()`, `high_risk_review=HighRiskReviewAgent.from_env()`를 넣는 일 (⑧ §9). 지금은 이유 문장 없는 `DecisionTool()`과 항상 실패하는 임시 재검증 어댑터가 들어 있습니다
- 질문 문구 원어민 확인 (#135)

## 테스트

```bash
pytest -q ai_decision_policy
```
