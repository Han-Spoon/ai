from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SearchResult:
    query: str
    title: str
    url: str
    domain: str
    snippet: str = ""
    content: str = ""
    fetched_at: str = field(default_factory=utc_now_iso)

    def text(self) -> str:
        return " ".join(part for part in (self.title, self.snippet, self.content) if part)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SourceEvidence:
    query: str
    title: str
    url: str
    domain: str
    source_type: str
    source_weight: float
    fetched_at: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class IngredientEvidence:
    name: str
    tags: set[str] = field(default_factory=set)
    sources: list[SourceEvidence] = field(default_factory=list)

    @property
    def support_count(self) -> int:
        return len({source.url for source in self.sources})

    def evidence_score(self) -> float:
        domains = set()
        score = 0.0
        for source in self.sources:
            domain_factor = 0.75 if source.domain in domains else 1.0
            domains.add(source.domain)
            score += source.source_weight * domain_factor
        return round(min(score / 3.0, 1.0), 4)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "tags": sorted(self.tags),
            "support_count": self.support_count,
            "evidence_score": self.evidence_score(),
            "sources": [source.to_dict() for source in self.sources],
        }

