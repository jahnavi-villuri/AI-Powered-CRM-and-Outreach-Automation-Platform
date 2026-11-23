"""
Database-backed suppression management service.

Entries store email addresses exactly as supplied (normalized to lowercase) and
domains without a leading "@", matching database/migrations/006 schema.
"""

import re
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from psycopg.rows import dict_row

from backend.app.core.db import get_conn, transaction
from backend.app.services.audit_service import log_action


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


def normalize_suppression_value(value: str, kind: str) -> str:
    """Validate and normalize an email or domain suppression value."""
    if not isinstance(value, str):
        raise ValueError("Suppression value must be a string.")
    normalized = value.strip().lower()
    if kind == "email":
        if not _EMAIL_RE.fullmatch(normalized):
            raise ValueError("A valid email address is required for kind 'email'.")
        return normalized
    if kind == "domain":
        normalized = normalized.removeprefix("@")
        if not _DOMAIN_RE.fullmatch(normalized):
            raise ValueError("A valid domain is required for kind 'domain'.")
        return normalized
    raise ValueError("kind must be either 'email' or 'domain'.")


def _serialize(row: Any) -> dict:
    """Convert a database row mapping into an API-friendly dictionary."""
    return {
        "id": str(row["id"]),
        "value": row["value"],
        "kind": row["kind"],
        "reason": row["reason"],
        "source": row["source"],
        "created_by": str(row["created_by"]) if row.get("created_by") else None,
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


def list_suppressions(*, limit: int = 100, offset: int = 0) -> dict:
    """Return a paginated list of suppression entries."""
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, value, kind, reason, source, created_by, created_at
                FROM suppression_entries
                ORDER BY created_at DESC, id
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            rows = cur.fetchall()
            cur.execute("SELECT COUNT(*) AS total FROM suppression_entries")
            total = cur.fetchone()["total"]
    return {"items": [_serialize(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def create_suppression(
    *, value: str, kind: str, reason: str, source: str = "manual", created_by: str | None = None
) -> dict:
    """Create a normalized suppression entry."""
    if not reason or not reason.strip():
        raise ValueError("reason is required.")
    normalized = normalize_suppression_value(value, kind)
    if source not in {"manual", "bounce", "unsubscribe", "admin"}:
        raise ValueError("source must be manual, bounce, unsubscribe, or admin.")

    try:
        with transaction() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    INSERT INTO suppression_entries (value, kind, reason, source, created_by)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id, value, kind, reason, source, created_by, created_at
                    """,
                    (normalized, kind, reason.strip(), source, created_by),
                )
                row = cur.fetchone()
    except Exception as exc:
        # The unique index on (LOWER(value), kind) is the concurrency-safe
        # duplicate guard. Translate PostgreSQL unique violations to HTTP 409.
        from psycopg.errors import UniqueViolation
        if isinstance(exc, UniqueViolation):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Suppression entry already exists.") from exc
        raise
    return _serialize(row)


def is_suppressed(email: str) -> bool:
    """Check exact-address and domain suppression using a single indexed query."""
    if not isinstance(email, str) or not _EMAIL_RE.fullmatch(email.strip().lower()):
        return False
    normalized = email.strip().lower()
    domain = normalized.rsplit("@", 1)[1]
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM suppression_entries
                    WHERE (kind = 'email' AND LOWER(value) = %s)
                       OR (kind = 'domain' AND LOWER(value) = %s)
                ) AS suppressed
                """,
                (normalized, domain),
            )
            row = cur.fetchone()
    return bool(row["suppressed"])


def remove_suppression(*, entry_id: UUID, justification: str, actor: str) -> dict:
    """Remove one entry and append an audit record in the same transaction."""
    if not justification or not justification.strip():
        raise ValueError("A justification is required to remove a suppression entry.")
    with transaction() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                DELETE FROM suppression_entries
                WHERE id = %s
                RETURNING id, value, kind, reason, source
                """,
                (entry_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suppression entry not found.")
        log_action(
            conn=conn,
            actor=actor,
            action="suppression_removed",
            entity_type="suppression_entry",
            entity_id=str(row["id"]),
            details_dict={
                "value": row["value"],
                "kind": row["kind"],
                "source": row["source"],
                "justification": justification.strip(),
            },
        )
    return {"id": str(row["id"]), "value": row["value"], "kind": row["kind"], "removed": True}
