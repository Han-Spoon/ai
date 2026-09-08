from __future__ import annotations

from config import COMMUNITY_DOMAINS, OFFICIAL_DOMAINS, RECIPE_DOMAINS, SOURCE_WEIGHTS


def classify_source(domain: str, title: str = "") -> tuple[str, float]:
    domain = domain.lower().removeprefix("www.")
    title = title.lower()

    if any(domain.endswith(d) for d in OFFICIAL_DOMAINS):
        source_type = "official"
    elif any(domain.endswith(d) for d in RECIPE_DOMAINS) or "레시피" in title or "만드는" in title:
        source_type = "recipe"
    elif "blog" in domain or "tistory.com" in domain:
        source_type = "blog"
    elif any(domain.endswith(d) for d in COMMUNITY_DOMAINS):
        source_type = "community"
    else:
        source_type = "unknown"

    return source_type, SOURCE_WEIGHTS[source_type]

