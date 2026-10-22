"""The four public Prepare/Judge models and their nested KnowledgeBundle."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from .common import (
    AmbiguityFlag,
    Confirmation,
    ConstraintTag,
    EvidenceRef,
    FlagConfirmation,
    IngredientRef,
    IngredientStatus,
    Locale,
    LocalizedIngredientRef,
    NodeError,
    NodeWarning,
    NonEmptyString,
    NonNegativeInt,
    OwnerQuestionState,
    PositiveInt,
    Probability,
    SchemaVersion,
    SliceStatus,
    StrictModel,
    UserProfile,
    _ensure_unique,
)

PreparedHash = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class ImageRef(StrictModel):
    source: Literal["camera", "upload"]
    storage_key: NonEmptyString
    version_id: NonEmptyString | None
    expected_etag: NonEmptyString | None


class PriceOption(StrictModel):
    label: NonEmptyString
    price_text: NonEmptyString
    inferred: StrictBool


class RiskTagObservation(StrictModel):
    tag: Literal["is_spicy"]
    confidence: Probability
    evidence: EvidenceRef


class PreparedItem(StrictModel):
    item_id: NonEmptyString
    source_index: NonNegativeInt
    raw_menu_name: NonEmptyString
    normalized_menu_name: NonEmptyString
    display_name: NonEmptyString
    description: str | None
    price_text: str | None
    price_options: list[PriceOption]
    locale_hint: Locale | None
    options: list[str]
    residual_tokens: list[str]
    risk_tag_observations: list[RiskTagObservation]


class ScanQuality(StrictModel):
    status: Literal["usable", "low_confidence", "needs_retake"]
    reasons: list[str]
    retake_suggestions: list[str]


class PrepareSignals(StrictModel):
    scan_quality_status: Literal["usable", "low_confidence", "needs_retake"]
    warnings: list[NodeWarning]

    @field_validator("warnings")
    @classmethod
    def caution_warnings_only(cls, value: list[NodeWarning]) -> list[NodeWarning]:
        if any(not warning.forces_caution for warning in value):
            raise ValueError("prepare_signals may contain only forces_caution warnings")
        return value


class PrepareRequest(StrictModel):
    schema_version: SchemaVersion
    trace_id: UUID
    scan_session_id: NonEmptyString
    image: ImageRef
    locale: Locale


class PrepareResponse(StrictModel):
    schema_version: SchemaVersion
    trace_id: UUID
    prepare_id: UUID
    scan_session_id: NonEmptyString
    prepared_items_hash: PreparedHash
    scan_quality: ScanQuality
    prepare_signals: PrepareSignals
    prepared_items: list[PreparedItem]
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_prepare_result(self) -> "PrepareResponse":
        if self.scan_quality.status != self.prepare_signals.scan_quality_status:
            raise ValueError("scan quality status must be preserved in prepare_signals")
        if self.scan_quality.status == "needs_retake" and self.prepared_items:
            raise ValueError("needs_retake responses must not contain prepared_items")
        item_ids = [item.item_id for item in self.prepared_items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("prepared item_id values must be unique")
        indexes = [item.source_index for item in self.prepared_items]
        if len(indexes) != len(set(indexes)):
            raise ValueError("prepared source_index values must be unique")
        for item in self.prepared_items:
            if item.item_id != f"{self.scan_session_id}:{item.source_index}":
                raise ValueError("item_id must be {scan_session_id}:{source_index}")
        return self


class GraphIngredient(StrictModel):
    ingredient_id: PositiveInt
    canonical_name: NonEmptyString
    name_normalized: NonEmptyString
    localized_name: str | None
    taxonomy_category: Literal[
        "fermented",
        "broth",
        "sauce",
        "processed",
        "seasoning",
        "oil",
        "garnish",
        "base",
        "unknown",
    ]


class IngredientAlias(StrictModel):
    ingredient_id: PositiveInt
    alias_normalized: NonEmptyString


class IngredientTag(StrictModel):
    ingredient_id: PositiveInt
    tag_code: ConstraintTag


class IngredientComposition(StrictModel):
    parent_ingredient_id: PositiveInt
    child_ingredient_id: PositiveInt
    ambiguity_flag: AmbiguityFlag | None


class IngredientCompositionTag(StrictModel):
    parent_ingredient_id: PositiveInt
    child_ingredient_id: PositiveInt
    tag_code: ConstraintTag


class IngredientGraph(StrictModel):
    status: SliceStatus
    ingredients: list[GraphIngredient]
    aliases: list[IngredientAlias]
    tags: list[IngredientTag]
    compositions: list[IngredientComposition]
    composition_tags: list[IngredientCompositionTag]

    @model_validator(mode="after")
    def empty_unless_ok(self) -> "IngredientGraph":
        if self.status != "ok" and any(
            (self.ingredients, self.aliases, self.tags, self.compositions, self.composition_tags)
        ):
            raise ValueError("non-ok ingredient_graph slices must be empty")
        return self


class Candidate(StrictModel):
    menu_id: PositiveInt
    menu_category_id: PositiveInt | None
    canonical_name: NonEmptyString
    score: Probability
    match_type: Literal["canonical", "spacing", "synonym", "ocr_correction", "fuzzy"]


class CandidateLookup(StrictModel):
    status: SliceStatus
    candidates: Annotated[list[Candidate], Field(max_length=5)]

    @model_validator(mode="after")
    def validate_candidates(self) -> "CandidateLookup":
        if self.status == "ok" and not self.candidates:
            raise ValueError("ok candidate_lookup requires at least one candidate")
        if self.status != "ok" and self.candidates:
            raise ValueError("non-ok candidate_lookup must be empty")
        expected = sorted(self.candidates, key=lambda item: (-item.score, item.menu_id))
        if self.candidates != expected:
            raise ValueError("candidates must be sorted by score desc, menu_id asc")
        return self


class ExactFeedbackData(StrictModel):
    menu_id: PositiveInt
    confirmations: list[Confirmation]
    flag_confirmations: list[FlagConfirmation]
    owner_question_states: list[OwnerQuestionState]


class ExactFeedbackSlice(StrictModel):
    status: SliceStatus
    exact: ExactFeedbackData | None

    @model_validator(mode="after")
    def validate_exact(self) -> "ExactFeedbackSlice":
        if (self.status == "ok") != (self.exact is not None):
            raise ValueError("exact is required exactly when status is ok")
        return self


class OntologyMenu(StrictModel):
    menu_id: PositiveInt
    base_menu_id: PositiveInt | None
    remain_token: str | None
    menu_category_id: PositiveInt | None
    source: Literal["curated", "web_search_generated", "variant_generated"]
    ambiguity_flags: list[AmbiguityFlag]
    knowledge_revision: PositiveInt

    @model_validator(mode="after")
    def validate_variant_columns(self) -> "OntologyMenu":
        if (self.base_menu_id is None) != (self.remain_token is None):
            raise ValueError("base_menu_id and remain_token must be set together")
        return self


class RecipeIngredient(StrictModel):
    ingredient_id: PositiveInt


class MenuRecipeCorpus(StrictModel):
    corpus: NonEmptyString
    n_total: PositiveInt


class MenuIngredientPrior(StrictModel):
    corpus: NonEmptyString
    ingredient_id: PositiveInt
    k_count: PositiveInt


class MenuIngredientCache(StrictModel):
    menu_revision: PositiveInt
    ingredients_snapshot: list[dict[str, Any]]


class OntologySlice(StrictModel):
    status: SliceStatus
    menu: OntologyMenu | None
    recipe_ingredients: list[RecipeIngredient]
    menu_recipe_corpora: list[MenuRecipeCorpus]
    menu_ingredient_priors: list[MenuIngredientPrior]
    cache: MenuIngredientCache | None

    @model_validator(mode="after")
    def validate_slice_shape(self) -> "OntologySlice":
        if self.status == "ok" and self.menu is None:
            raise ValueError("ok ontology requires menu")
        if self.status != "ok" and any(
            (
                self.menu is not None,
                bool(self.recipe_ingredients),
                bool(self.menu_recipe_corpora),
                bool(self.menu_ingredient_priors),
                self.cache is not None,
            )
        ):
            raise ValueError("non-ok ontology must use the documented empty shape")
        return self


class StorePrior(StrictModel):
    ingredient_id: PositiveInt
    alpha: Annotated[float, Field(gt=0.0)]
    beta: Annotated[float, Field(gt=0.0)]
    evidence_refs: list[NonEmptyString]


class PriorSlice(StrictModel):
    status: SliceStatus
    store: list[StorePrior]
    evidence: list[EvidenceRef]

    @model_validator(mode="after")
    def validate_prior_shape(self) -> "PriorSlice":
        if self.status != "ok" and (self.store or self.evidence):
            raise ValueError("non-ok priors must be empty")
        evidence_ids = {evidence.evidence_id for evidence in self.evidence}
        if any(ref not in evidence_ids for prior in self.store for ref in prior.evidence_refs):
            raise ValueError("store prior evidence_refs must resolve inside priors.evidence")
        return self


class WebSource(StrictModel):
    source_url: NonEmptyString
    title: NonEmptyString
    fetched_at: datetime
    evidence: EvidenceRef
    ingredients: list[IngredientRef]


class WebSearchCache(StrictModel):
    status: SliceStatus
    cached_sources: list[WebSource]
    next_rank: PositiveInt

    @model_validator(mode="after")
    def validate_cache_shape(self) -> "WebSearchCache":
        if self.status == "ok" and not self.cached_sources:
            raise ValueError("ok web_search_cache requires cached_sources")
        if self.status == "not_found" and (self.cached_sources or self.next_rank != 1):
            raise ValueError("not_found web_search_cache must be empty with next_rank 1")
        return self


class CandidateKnowledge(StrictModel):
    menu_id: PositiveInt
    exact_feedback: ExactFeedbackSlice
    ontology: OntologySlice
    priors: PriorSlice


class ItemKnowledge(StrictModel):
    item_id: NonEmptyString
    candidate_lookup: CandidateLookup
    candidate_knowledge: list[CandidateKnowledge]
    fallback_ontology: OntologySlice
    web_search_cache: WebSearchCache

    @model_validator(mode="after")
    def validate_candidate_mapping(self) -> "ItemKnowledge":
        candidate_ids = [candidate.menu_id for candidate in self.candidate_lookup.candidates]
        knowledge_ids = [knowledge.menu_id for knowledge in self.candidate_knowledge]
        if (
            len(knowledge_ids) != len(set(knowledge_ids))
            or set(candidate_ids) != set(knowledge_ids)
        ):
            raise ValueError("each candidate requires exactly one matching knowledge slice")
        return self


class SourceWeight(StrictModel):
    source_key: NonEmptyString
    weight: Probability


class SourceReliability(StrictModel):
    status: SliceStatus
    weights: list[SourceWeight]

    @model_validator(mode="after")
    def validate_weights(self) -> "SourceReliability":
        keys = [weight.source_key for weight in self.weights]
        if len(keys) != len(set(keys)):
            raise ValueError("source reliability keys must be unique")
        if self.status == "not_found" and self.weights:
            raise ValueError("not_found source_reliability must be empty")
        return self


class KnowledgeBundle(StrictModel):
    bundle_schema_version: SchemaVersion
    ingredient_graph: IngredientGraph
    items: list[ItemKnowledge]
    source_reliability: SourceReliability


class JudgeRequest(StrictModel):
    schema_version: SchemaVersion
    trace_id: UUID
    prepare_id: UUID
    scan_session_id: NonEmptyString
    store_id: PositiveInt | None
    prepared_items_hash: PreparedHash
    knowledge_revision: NonEmptyString
    prepare_signals: PrepareSignals
    prepared_items: list[PreparedItem]
    knowledge_bundle: KnowledgeBundle
    user_profile: UserProfile
    locale: Locale

    @model_validator(mode="after")
    def validate_bundle_scope(self) -> "JudgeRequest":
        prepared_ids = [item.item_id for item in self.prepared_items]
        bundle_ids = [item.item_id for item in self.knowledge_bundle.items]
        if len(prepared_ids) != len(set(prepared_ids)):
            raise ValueError("prepared item_id values must be unique")
        if len(bundle_ids) != len(set(bundle_ids)) or set(prepared_ids) != set(bundle_ids):
            raise ValueError("knowledge items must match prepared_items one-to-one")
        for item in self.prepared_items:
            if item.item_id != f"{self.scan_session_id}:{item.source_index}":
                raise ValueError("item_id must be {scan_session_id}:{source_index}")
        if self.store_id is None:
            for item in self.knowledge_bundle.items:
                for knowledge in item.candidate_knowledge:
                    if knowledge.exact_feedback.status != "not_found":
                        raise ValueError("storeless request cannot include exact feedback")
                    if knowledge.priors.status != "not_found":
                        raise ValueError("storeless request cannot include store priors")
                    if knowledge.ontology.cache is not None:
                        raise ValueError("storeless request cannot include store cache")
                if item.fallback_ontology.cache is not None:
                    raise ValueError("storeless request cannot include fallback store cache")
        return self


class MessageNote(StrictModel):
    code: Literal["hard_to_remove", "ambiguous_menu"]
    text: NonEmptyString


class DecisionMessage(StrictModel):
    verdict: NonEmptyString
    reason: str | None
    notes: list[MessageNote]


class RiskIngredient(StrictModel):
    ingredient: LocalizedIngredientRef
    constraint_tags: list[ConstraintTag]
    matched_tags: list[ConstraintTag]
    role: Literal["identity", "component", "hidden"] | None
    status: IngredientStatus
    posterior_mean: Probability | None
    confidence: Probability | None
    prior_source: Literal["store", "cluster", "global", "uninformative"] | None
    evidence_refs: list[NonEmptyString]


class OwnerCard(StrictModel):
    item_id: NonEmptyString
    card_id: NonEmptyString
    menu_id: PositiveInt | None
    round: Literal[1, 2, 3]
    previous_question_id: NonEmptyString | None
    ingredient: IngredientRef | None
    ambiguity_flag: AmbiguityFlag | None
    constraint_tags: list[ConstraintTag]
    question: dict[Locale, NonEmptyString]
    reason_codes: list[
        Literal[
            "insufficient_recipe_evidence",
            "anomaly",
            "no_information",
            "expired",
            "high_risk_review",
            "ambiguous_menu",
            "unconfirmed",
        ]
    ]
    evidence_refs: list[NonEmptyString]

    @model_validator(mode="after")
    def validate_card(self) -> "OwnerCard":
        if self.round == 1 and self.previous_question_id is not None:
            raise ValueError("round 1 must not have previous_question_id")
        if self.round > 1 and self.previous_question_id is None:
            raise ValueError("round 2/3 requires previous_question_id")
        if not self.reason_codes:
            raise ValueError("owner card requires at least one reason")
        if "ko" not in self.question:
            raise ValueError("owner question must include Korean")
        return self


class ItemResult(StrictModel):
    item_id: NonEmptyString
    source_index: NonNegativeInt
    menu_id: PositiveInt | None
    menu_category_id: PositiveInt | None
    normalization_status: Literal["normalized", "ambiguous", "unmatched", "invalid"]
    match_type: Literal["canonical", "spacing", "synonym", "ocr_correction", "fuzzy"] | None
    selection_method: Literal["deterministic", "llm", "none"]
    status: Literal["completed", "partial_failed", "failed"]
    route: Literal["exact_only", "db_bayes", "db_variant_bayes", "web_bayes", "no_information"]
    risk_level: Literal["danger", "caution", "safe"]
    evidence_basis: Literal["confirmed", "estimated", "unknown"]
    information_status: Literal["complete", "partial", "none"]
    risk_ingredients: list[RiskIngredient]
    matched_tags: list[ConstraintTag]
    evidence_refs: list[NonEmptyString]
    message: DecisionMessage
    owner_cards: list[OwnerCard]
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_item_cards(self) -> "ItemResult":
        if any(card.item_id != self.item_id for card in self.owner_cards):
            raise ValueError("owner cards must belong to their item")
        return self


class PersistenceCommand(StrictModel):
    command_id: NonEmptyString
    operation_id: NonEmptyString | None
    item_id: NonEmptyString | None
    card_id: NonEmptyString | None
    action: Literal[
        "create_review_item",
        "create_owner_verification_request",
        "record_owner_answer",
        "update_source_reliability",
        "save_web_search_cache",
        "save_menu_ingredient_cache",
    ]
    idempotency_key: PreparedHash
    payload: dict[str, Any]


class JudgeResponse(StrictModel):
    schema_version: SchemaVersion
    trace_id: UUID
    prepare_id: UUID
    scan_session_id: NonEmptyString
    store_id: PositiveInt | None
    prepared_items_hash: PreparedHash
    bundle_hash: PreparedHash
    knowledge_revision: NonEmptyString
    items: list[ItemResult]
    evidence: list[EvidenceRef]
    persistence_commands: list[PersistenceCommand]
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_result_integrity(self) -> "JudgeResponse":
        indexes = [item.source_index for item in self.items]
        if indexes != sorted(indexes) or len(indexes) != len(set(indexes)):
            raise ValueError("items must have unique source_index values in order")
        item_ids = [item.item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("item_id values must be unique")
        evidence_ids = [evidence.evidence_id for evidence in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_id values must be unique")
        evidence_set = set(evidence_ids)
        refs = [
            ref
            for item in self.items
            for ref in (
                item.evidence_refs
                + [ref for ingredient in item.risk_ingredients for ref in ingredient.evidence_refs]
            )
        ]
        if any(ref not in evidence_set for ref in refs):
            raise ValueError("all result evidence_refs must resolve")
        card_ids = [card.card_id for item in self.items for card in item.owner_cards]
        if len(card_ids) != len(set(card_ids)):
            raise ValueError("card_id values must be unique")
        if self.store_id is None:
            if self.persistence_commands or any(item.owner_cards for item in self.items):
                raise ValueError("storeless result cannot contain cards or persistence commands")
            if any(item.risk_level == "safe" for item in self.items):
                raise ValueError("storeless result cannot be safe")
            for item in self.items:
                if not any(warning.code == "store_context_missing" for warning in item.warnings):
                    raise ValueError("storeless items require store_context_missing warning")
        return self
