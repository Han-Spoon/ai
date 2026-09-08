from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


class WebSearchCache:
    def __init__(self, db_path: Path, ttl_days: int):
        self.db_path = db_path
        self.ttl_days = ttl_days
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def get(self, normalized_menu_name: str) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT payload, searched_at
                FROM web_search_cache
                WHERE normalized_menu_name = ?
                """,
                (normalized_menu_name,),
            ).fetchone()
        if not row:
            return None

        payload_raw, searched_at_raw = row
        searched_at = datetime.fromisoformat(searched_at_raw)
        if searched_at.tzinfo is None:
            searched_at = searched_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - searched_at > timedelta(days=self.ttl_days):
            return None
        payload = json.loads(payload_raw)
        payload["search_status"] = "cache_hit"
        return payload

    def save(self, normalized_menu_name: str, payload: dict) -> None:
        searched_at = payload.get("searched_at") or datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO web_search_cache (
                    normalized_menu_name,
                    original_menu_name,
                    search_queries,
                    extracted_ingredients,
                    web_tags,
                    sources,
                    evidence_score,
                    searched_at,
                    search_provider,
                    status,
                    payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(normalized_menu_name) DO UPDATE SET
                    original_menu_name = excluded.original_menu_name,
                    search_queries = excluded.search_queries,
                    extracted_ingredients = excluded.extracted_ingredients,
                    web_tags = excluded.web_tags,
                    sources = excluded.sources,
                    evidence_score = excluded.evidence_score,
                    searched_at = excluded.searched_at,
                    search_provider = excluded.search_provider,
                    status = excluded.status,
                    payload = excluded.payload
                """,
                (
                    normalized_menu_name,
                    payload.get("menu_name_ko"),
                    json.dumps(payload.get("queries", []), ensure_ascii=False),
                    json.dumps(payload.get("ingredients", []), ensure_ascii=False),
                    json.dumps(payload.get("web_tags", []), ensure_ascii=False),
                    json.dumps(payload.get("sources", []), ensure_ascii=False),
                    float(payload.get("evidence_score", 0.0)),
                    searched_at,
                    payload.get("search_provider"),
                    payload.get("search_status"),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS web_search_cache (
                    normalized_menu_name TEXT PRIMARY KEY,
                    original_menu_name TEXT,
                    search_queries TEXT NOT NULL,
                    extracted_ingredients TEXT NOT NULL,
                    web_tags TEXT NOT NULL,
                    sources TEXT NOT NULL,
                    evidence_score REAL NOT NULL,
                    searched_at TEXT NOT NULL,
                    search_provider TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )

