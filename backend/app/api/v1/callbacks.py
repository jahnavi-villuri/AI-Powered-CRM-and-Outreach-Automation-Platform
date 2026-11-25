"""
BE-48: Provider Callback Webhook.

POST /api/v1/callbacks receives delivery / engagement events from the email
provider (delivered, opened, clicked, replied, bounce, unsubscribe).

What this endpoint does, in order:
  1. Verifies the HMAC-SHA256 signature (X-Provider-Signature)  -> 401 if bad.
  2. Verifies the timestamp (X-Provider-Timestamp) is within 5 minutes -> 401.
  3. Validates the JSON body                                      -> 422 if bad.
  4. Stores the event in message_events ONCE per provider_event_id
     (a duplicate is ignored and returns status "duplicate").
  5. On a hard bounce or an unsubscribe, adds the recipient to
     suppression_entries and cancels any still-queued messages to them.

Signature format (the provider and this code must agree on it):
    signature = hex( HMAC_SHA256( PROVIDER_WEBHOOK_SECRET,
                                  "<timestamp>." + <raw request body> ) )
The timestamp is sent in the X-Provider-Timestamp header (unix seconds or
ISO-8601). Including it in the signed text stops an attacker replaying an old,
validly signed request later.

All SQL is parameterised raw SQL (no ORM), as required by CONTRIBUTING.md.
"""

import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from backend.app.core.config import get_settings
from backend.app.core.db import transaction

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Webhooks & Callbacks"])

TIMESTAMP_TOLERANCE = timedelta(minutes=5)

# Bounce hints a provider may put in payload["bounce_type"].
SOFT_BOUNCE_TYPES = {"soft", "transient", "temporary", "undetermined"}
HARD_BOUNCE_TYPES = {"hard", "permanent"}


# --------------------------------------------------------------------------
# Signature + timestamp verification
# --------------------------------------------------------------------------
def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
    )


def parse_timestamp(value: str) -> Optional[datetime]:
    """Turn a header value (unix seconds/ms or ISO-8601) into an aware UTC datetime."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        number = None

    if number is not None:
        if number > 1e11:  # looks like milliseconds
            number = number / 1000.0
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def compute_signature(secret: str, timestamp: str, raw_body: bytes) -> str:
    """HMAC-SHA256 hex digest of '<timestamp>.<raw body>'."""
    signed_payload = timestamp.encode("utf-8") + b"." + raw_body
    return hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()


def verify_signature_and_timestamp(
    raw_body: bytes,
    signature: Optional[str],
    timestamp: Optional[str],
    secret: str,
    now: Optional[datetime] = None,
) -> None:
    """Raise 401 unless the signature is valid AND the timestamp is fresh."""
    if not signature:
        raise _unauthorized("Missing X-Provider-Signature header")
    if not timestamp:
        raise _unauthorized("Missing X-Provider-Timestamp header")

    # Check the signature FIRST so an attacker learns nothing about timing rules.
    provided = signature.strip()
    if provided.lower().startswith("sha256="):
        provided = provided[len("sha256="):]
    expected = compute_signature(secret, timestamp.strip(), raw_body)
    # compare_digest = constant-time comparison (defeats timing attacks).
    if not hmac.compare_digest(provided.lower().encode("utf-8"), expected.encode("utf-8")):
        raise _unauthorized("Invalid signature")

    sent_at = parse_timestamp(timestamp)
    if sent_at is None:
        raise _unauthorized("Invalid X-Provider-Timestamp header")
    current = now or datetime.now(timezone.utc)
    if abs(current - sent_at) > TIMESTAMP_TOLERANCE:
        raise _unauthorized("Timestamp outside the allowed 5 minute window")


async def verified_callback_body(
    request: Request,
    x_provider_signature: Optional[str] = Header(None, alias="X-Provider-Signature"),
    x_provider_timestamp: Optional[str] = Header(None, alias="X-Provider-Timestamp"),
) -> bytes:
    """
    Dependency: reads the RAW body (the signature is computed over the exact
    bytes, so we must not re-serialise parsed JSON) and verifies it.
    """
    raw_body = await request.body()
    verify_signature_and_timestamp(
        raw_body,
        x_provider_signature,
        x_provider_timestamp,
        get_settings().PROVIDER_WEBHOOK_SECRET,
    )
    return raw_body


# --------------------------------------------------------------------------
# Request model (matches contracts/events.md section 2)
# --------------------------------------------------------------------------
class CallbackEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: str = Field(min_length=1, max_length=50)
    provider_event_id: str = Field(min_length=1, max_length=255)
    event_type: Literal["delivered", "opened", "clicked", "replied", "bounce", "unsubscribe"]
    occurred_at: Optional[datetime] = None
    provider_message_id: str = Field(min_length=1, max_length=255)
    recipient: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider", "provider_event_id", "provider_message_id")
    @classmethod
    def _strip_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @field_validator("payload", mode="before")
    @classmethod
    def _null_payload_is_empty(cls, v: Any) -> Any:
        return {} if v is None else v


# --------------------------------------------------------------------------
# Business rules
# --------------------------------------------------------------------------
def is_hard_bounce(payload: Dict[str, Any]) -> bool:
    """
    A bounce is HARD (permanent) unless the provider says it is soft.
    Soft = payload.bounce_type says so, or the SMTP code starts with 4 (temporary).
    """
    bounce_type = str(payload.get("bounce_type") or "").strip().lower()
    if bounce_type in SOFT_BOUNCE_TYPES:
        return False
    if bounce_type in HARD_BOUNCE_TYPES:
        return True
    bounce_code = str(payload.get("bounce_code") or "").strip()
    if bounce_code.startswith("4"):
        return False
    return True


def should_suppress(event: CallbackEvent) -> bool:
    if event.event_type == "unsubscribe":
        return True
    return event.event_type == "bounce" and is_hard_bounce(event.payload)


def _suppression_reason(event: CallbackEvent) -> str:
    if event.event_type == "unsubscribe":
        return "Recipient unsubscribed (provider callback)"
    detail = event.payload.get("bounce_reason") or event.payload.get("bounce_code")
    reason = f"Hard bounce: {detail}" if detail else "Hard bounce (provider callback)"
    return reason[:500]


# --------------------------------------------------------------------------
# Endpoint
# --------------------------------------------------------------------------
@router.post("/callbacks")
def provider_callbacks(raw_body: bytes = Depends(verified_callback_body)):
    """
    Handles provider callbacks (delivered, opened, clicked, replied, bounce,
    unsubscribe). Signature and timestamp are already verified by the dependency.
    """
    try:
        event = CallbackEvent.model_validate_json(raw_body)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()],
        ) from exc

    occurred_at = event.occurred_at or datetime.now(timezone.utc)
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)

    # Keep the provider and recipient next to the raw payload so the stored
    # event is self-describing.
    stored_payload = dict(event.payload)
    stored_payload.setdefault("provider", event.provider)
    if event.recipient:
        stored_payload.setdefault("recipient", event.recipient)

    # Everything below happens in ONE transaction: the event, the suppression
    # entry and the cancellations are saved together or not at all.
    with transaction() as conn, conn.cursor(row_factory=dict_row) as cur:
        # 1. Which message is this event about?
        cur.execute(
            """SELECT id, recipient
               FROM outreach_messages
               WHERE provider = %s AND provider_message_id = %s
               ORDER BY sent_at DESC
               LIMIT 1""",
            (event.provider, event.provider_message_id),
        )
        message = cur.fetchone()
        if message is None:
            # 404 (not 200) so the provider retries - the callback can arrive
            # before our dispatcher has saved provider_message_id.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Unknown provider_message_id for this provider",
            )

        # 2. Store the event once per provider_event_id. The unique index
        #    idx_events_provider_event makes ON CONFLICT DO NOTHING safe even
        #    when two identical callbacks arrive at the same moment.
        cur.execute(
            """INSERT INTO message_events
                   (message_id, provider_event_id, type, payload, occurred_at)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (provider_event_id) DO NOTHING
               RETURNING id""",
            (
                message["id"],
                event.provider_event_id,
                event.event_type,
                Jsonb(stored_payload),
                occurred_at,
            ),
        )
        inserted = cur.fetchone()
        if inserted is None:
            logger.info("Duplicate provider callback ignored: %s", event.provider_event_id)
            return {
                "status": "duplicate",
                "event_id": event.provider_event_id,
                "suppressed": False,
                "cancelled_messages": 0,
            }

        # 3. Hard bounce / unsubscribe -> suppress the recipient.
        suppressed = False
        cancelled = 0
        if should_suppress(event):
            # Use the address WE sent to (trusted), not the one in the body.
            email = (message["recipient"] or event.recipient or "").strip().lower()
            if email:
                source = "unsubscribe" if event.event_type == "unsubscribe" else "bounce"
                cur.execute(
                    """INSERT INTO suppression_entries (value, kind, reason, source)
                       VALUES (%s, 'email', %s, %s)
                       ON CONFLICT (LOWER(value), kind) DO NOTHING""",
                    (email, _suppression_reason(event), source),
                )
                suppressed = True
                # Contract: cancel pending follow-ups for this recipient.
                cur.execute(
                    """UPDATE outreach_messages
                       SET status = 'cancelled'
                       WHERE LOWER(recipient) = %s AND status = 'queued'""",
                    (email,),
                )
                cancelled = cur.rowcount

    logger.info(
        "Provider callback stored: event=%s type=%s suppressed=%s cancelled=%s",
        event.provider_event_id, event.event_type, suppressed, cancelled,
    )
    return {
        "status": "processed",
        "event_id": event.provider_event_id,
        "suppressed": suppressed,
        "cancelled_messages": cancelled,
    }
