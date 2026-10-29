import asyncio

import pytest

from ai_db_update import DbUpdateTool, build_commands, idempotency_key
from ai_supervisor.contracts.common import NodeContext, NodeWarning
from ai_supervisor.contracts.db_update import DbUpdateRequest

ITEM_ID = "scan-123:0"


def evidence(evidence_id, source_type="web_search"):
    return {
        "evidence_id": evidence_id,
        "evidence_class": "soft",
        "source_type": source_type,
        "source_ref": evidence_id,
        "verification_status": "pending_review",
        "reliability_weight": None,
        "observed_at": "2026-10-23T00:00:00Z",
    }


def web_source(url):
    return {
        "source_url": url,
        "title": "마라탕 레시피",
        "fetched_at": "2026-10-23T00:00:00Z",
        "evidence": evidence(f"web:{url}"),
        "ingredients": [{"ingredient_id": None, "canonical_name": "소고기"}],
    }


URL_A = "https://www.10000recipe.com/recipe/1"
URL_B = "https://www.10000recipe.com/recipe/2"

WEB_REVIEW = {
    "review_origin": "web_search",
    "menu": {"menu_id": None, "normalized_menu_name": "마라탕"},
    "web_sources": [web_source(URL_B), web_source(URL_A)],
    "review_notes": [],
    "variant_suggestion": None,
}
VARIANT_REVIEW = {
    "review_origin": "variant_suggestion",
    "menu": {"menu_id": None, "normalized_menu_name": "차돌된장찌개"},
    "web_sources": [],
    "review_notes": [],
    "variant_suggestion": {
        "base_menu_id": 1000,
        "remain_token": "차돌",
        "suggested_ingredients": [
            {
                "ingredient": {"ingredient_id": 6, "canonical_name": "소고기"},
                "constraint_tags": ["is_beef"],
                "role": "identity",
            }
        ],
        "evidence": evidence("runtime:scan-123:0:ocr_variant_tag:0", "ocr_variant_tag"),
    },
}
OWNER_REQUEST = {
    "item_id": ITEM_ID,
    "card_id": "card:scan-123:0:ingredient-4",
    "menu_id": 1001,
    "round": 1,
    "previous_question_id": None,
    "ingredient": {"ingredient_id": 4, "canonical_name": "돼지고기"},
    "ambiguity_flag": None,
    "constraint_tags": [],
    "recipe_probability": 0.95,
    "question": {"ko": "이 메뉴에 돼지고기가 들어가나요?", "en": "Does this contain pork?"},
    "reason_codes": ["unconfirmed"],
    "evidence_refs": ["recipe:pork"],
}
FLAG_REQUEST = {
    **OWNER_REQUEST,
    "card_id": "card:scan-123:0:flag-has_unclear_jeotgal",
    "ingredient": None,
    "ambiguity_flag": "has_unclear_jeotgal",
    "constraint_tags": ["is_shellfish", "is_fish"],
    "recipe_probability": None,
}
OWNER_ANSWER = {
    "menu_id": 1001,
    "ingredient": {"ingredient_id": 4, "canonical_name": "돼지고기"},
    "ambiguity_flag": None,
    "constraint_tags": [],
    "question_id": "0199a2f0-0000-7000-8000-00000000aaaa",
    "present": False,
    "answered_at": "2026-10-26T01:00:00Z",
}
WEB_CACHE = {
    "normalized_menu_name": "마라탕",
    "original_menu_name": "마라탕",
    "search_status": "searched",
    "sources": [web_source(URL_A)],
    "searched_at": "2026-10-26T01:00:00Z",
}
RELIABILITY = {
    "source_type": "corpus:10000recipe",
    "correct_count_delta": 1,
    "comparison_count_delta": 1,
    "trigger_evidence_ref": "owner:pork",
}


def request(*operations, scope="item", trace="0199a2f0-0000-7000-8000-000000000001", warnings=()):
    return DbUpdateRequest(
        context=NodeContext(
            schema_version="3.0",
            trace_id=trace,
            scan_session_id="scan-123",
            store_id=123456,
            call_scope=scope,
            item_id=ITEM_ID if scope == "item" else None,
        ),
        operations=[
            {"operation_id": f"operation:{i}", "operation_type": kind, "payload": payload}
            for i, (kind, payload) in enumerate(operations)
        ],
        warnings=list(warnings),
        errors=[],
    )


def command(kind, payload, **kw):
    return build_commands(request((kind, payload), **kw)).commands[0]


@pytest.mark.parametrize(
    "kind, payload, provenance",
    [
        ("create_review_item", WEB_REVIEW, ("web_search", "web_search", "soft")),
        ("create_review_item", VARIANT_REVIEW, ("ontology", "ocr_variant_tag", "soft")),
        ("create_owner_verification_request", OWNER_REQUEST, ("decision", "decision_policy", None)),
        ("record_owner_answer", OWNER_ANSWER, ("external_owner_answer", "owner_feedback", "hard")),
        ("update_source_reliability", RELIABILITY, ("db_update", "system", None)),
        ("save_web_search_cache", WEB_CACHE, ("web_search", "web_search", "soft")),
    ],
)
def test_action_keeps_payload_and_adds_fixed_provenance(kind, payload, provenance):  # ⓪ §4-6 table
    cmd = command(kind, payload)
    assert cmd.action == kind and cmd.operation_id == "operation:0"
    p = cmd.payload.provenance
    assert (p.origin_node, p.source_type, p.evidence_class) == provenance
    dumped = cmd.payload.model_dump(mode="json")
    dumped.pop("provenance")
    assert dumped == request((kind, payload)).operations[0].payload.model_dump(mode="json")


def test_provenance_points_at_existing_evidence():  # ⑦ §3, test 7
    assert command("create_review_item", WEB_REVIEW).payload.provenance.evidence_refs == [
        f"web:{URL_B}",
        f"web:{URL_A}",
    ]
    assert command("create_review_item", VARIANT_REVIEW).payload.provenance.evidence_refs == [
        "runtime:scan-123:0:ocr_variant_tag:0"
    ]
    assert command("create_owner_verification_request", OWNER_REQUEST).payload.provenance.evidence_refs == [
        "recipe:pork"
    ]


def test_card_id_only_on_owner_request():
    assert command("create_owner_verification_request", OWNER_REQUEST).card_id == OWNER_REQUEST["card_id"]
    assert command("create_review_item", WEB_REVIEW).card_id is None


def test_same_scan_retry_keeps_key():  # ⑦ test 8, 8-b
    a = command("create_owner_verification_request", OWNER_REQUEST)
    b = command(
        "create_owner_verification_request",
        OWNER_REQUEST,
        trace="0199a2f0-0000-7000-8000-000000000002",
    )
    assert a.idempotency_key == b.idempotency_key


def test_review_key_ignores_source_order():
    reordered = {**WEB_REVIEW, "web_sources": list(reversed(WEB_REVIEW["web_sources"]))}
    assert command("create_review_item", WEB_REVIEW).idempotency_key == command(
        "create_review_item", reordered
    ).idempotency_key


def test_flag_key_ignores_tag_order():
    flipped = {**FLAG_REQUEST, "constraint_tags": ["is_fish", "is_shellfish"]}
    assert command("create_owner_verification_request", FLAG_REQUEST).idempotency_key == command(
        "create_owner_verification_request", flipped
    ).idempotency_key


def test_flag_and_ingredient_questions_differ():  # ⑦ test 13
    assert command("create_owner_verification_request", FLAG_REQUEST).idempotency_key != command(
        "create_owner_verification_request", OWNER_REQUEST
    ).idempotency_key


def test_two_answers_have_two_keys():  # ⑦ test 8-c
    other = {**OWNER_ANSWER, "question_id": "0199a2f0-0000-7000-8000-00000000bbbb"}
    assert command("record_owner_answer", OWNER_ANSWER).idempotency_key != command(
        "record_owner_answer", other
    ).idempotency_key


def test_key_format_matches_contract():
    expected = idempotency_key("record_owner_answer", {"question_id": OWNER_ANSWER["question_id"]})
    assert command("record_owner_answer", OWNER_ANSWER).idempotency_key == expected
    assert expected.startswith("sha256:") and len(expected) == 71


def test_out_of_band_answer_has_no_item():
    cmd = command("record_owner_answer", OWNER_ANSWER, scope="out_of_band")
    assert cmd.item_id is None


def test_one_command_per_operation_in_order():
    res = build_commands(
        request(("create_review_item", WEB_REVIEW), ("save_web_search_cache", WEB_CACHE))
    )
    assert [c.action for c in res.commands] == ["create_review_item", "save_web_search_cache"]


def test_tool_keeps_context_and_warnings():  # ⑦ test 9
    upstream = NodeWarning(
        code="insufficient_sources", node="web_search", item_id=ITEM_ID,
        message="few", forces_caution=True,
    )
    req = request(("record_owner_answer", OWNER_ANSWER), warnings=[upstream])
    res = asyncio.run(DbUpdateTool().run(req))
    assert res.context == req.context
    assert [w.code for w in res.warnings] == ["insufficient_sources"]


def test_web_cache_key_changes_with_new_sources():  # 10/27: no search rank to tell saves apart
    later = {**WEB_CACHE, "sources": [web_source(URL_B)]}
    assert command("save_web_search_cache", WEB_CACHE).idempotency_key != command(
        "save_web_search_cache", later
    ).idempotency_key
    retry = command("save_web_search_cache", WEB_CACHE, trace="0199a2f0-0000-7000-8000-000000000002")
    assert retry.idempotency_key == command("save_web_search_cache", WEB_CACHE).idempotency_key
