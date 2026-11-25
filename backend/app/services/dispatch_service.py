
"""
Pre-send validation service.
"""

import hashlib
import re
from typing import Optional, TypedDict

from backend.app.core.db import get_conn


class ValidationResult(TypedDict):
    allowed: bool
    reason: Optional[str]


def validate_before_send(
    draft_id: str,
    recipient_email: str,
    idempotency_key: str
) -> ValidationResult:
    """
    Runs sequential checks before an outreach message can be sent.
    Returns whether sending is allowed and the rejection reason.
    """

    # 1. Validate and normalize recipient email
    email = recipient_email.strip().lower()

    email_pattern = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"

    if not re.fullmatch(email_pattern, email):
        return {
            "allowed": False,
            "reason": "Invalid recipient email format."
        }

    domain = email.split("@", 1)[1]

    with get_conn() as conn:
        with conn.cursor() as cur:

            # 2. Verify the exact draft recipient is deliverable
            cur.execute(
                """
                SELECT cm.verified_status
                FROM campaign_drafts d
                JOIN contact_methods cm
                  ON cm.id = d.recipient_contact_method_id
                WHERE d.id = %s
                  AND cm.kind = 'email'
                  AND LOWER(cm.value) = %s
                """,
                (draft_id, email)
            )

            result = cur.fetchone()

            if not result or result[0] != "deliverable":
                return {
                    "allowed": False,
                    "reason": "Recipient email is not verified as deliverable."
                }

            # 3. Check email and domain suppression
            cur.execute(
                """
                SELECT 1
                FROM suppression_entries
                WHERE (kind = 'email' AND LOWER(value) = %s)
                   OR (kind = 'domain' AND LOWER(value) = %s)
                LIMIT 1
                """,
                (email, domain)
            )

            if cur.fetchone():
                return {
                    "allowed": False,
                    "reason": "Recipient email or domain is suppressed."
                }

            # 4. Check approval and retrieve current draft content
            cur.execute(
                """
                SELECT
                    d.campaign_id,
                    d.edited_subject,
                    d.edited_body,
                    d.ai_output,
                    a.id,
                    a.content_hash,
                    a.recipient
                FROM campaign_drafts d
                LEFT JOIN approvals a
                  ON a.draft_id = d.id
                 AND a.revoked_at IS NULL
                WHERE d.id = %s
                ORDER BY a.approved_at DESC
                LIMIT 1
                """,
                (draft_id,)
            )

            draft = cur.fetchone()

            if not draft or not draft[4]:
                return {
                    "allowed": False,
                    "reason": "No valid approval exists for this draft."
                }

            (
                campaign_id,
                subject,
                body,
                ai_output,
                approval_id,
                approved_hash,
                approved_recipient
            ) = draft

            # Use edited content when available; otherwise use generated content
            ai_output = ai_output or {}
            subject = subject or ai_output.get("subject", "")
            body = body or ai_output.get("body", "")

            # Approval must match the exact recipient
            if (approved_recipient or "").strip().lower() != email:
                return {
                    "allowed": False,
                    "reason": "Approved recipient does not match the current recipient."
                }

            # Hash must match the approved recipient, subject and body
            current_hash = hashlib.sha256(
                f"{email}:{subject}:{body}".encode("utf-8")
            ).hexdigest()

            if current_hash != approved_hash:
                return {
                    "allowed": False,
                    "reason": "Draft content does not match the approved content."
                }

            # 5. Check whether the idempotency key was already used
            cur.execute(
                """
                SELECT 1
                FROM outreach_messages
                WHERE idempotency_key = %s
                LIMIT 1
                """,
                (idempotency_key,)
            )

            if cur.fetchone():
                return {
                    "allowed": False,
                    "reason": "This message has already been processed."
                }

            # 6. Check campaign status
            cur.execute(
                """
                SELECT status, daily_limit
                FROM campaigns
                WHERE id = %s
                """,
                (campaign_id,)
            )

            campaign = cur.fetchone()

            if not campaign or campaign[0] != "active":
                return {
                    "allowed": False,
                    "reason": "Campaign is not active."
                }

            # 7. Check campaign's daily send limit
            cur.execute(
                """
                SELECT COUNT(*)
                FROM outreach_messages om
                JOIN campaign_drafts cd
                  ON cd.id = om.draft_id
                WHERE cd.campaign_id = %s
                  AND om.status = 'sent'
                  AND om.sent_at >= CURRENT_DATE
                """,
                (campaign_id,)
            )

            sent_count = cur.fetchone()[0]

            if sent_count >= campaign[1]:
                return {
                    "allowed": False,
                    "reason": "Campaign daily send limit has been reached."
                }

    return {
        "allowed": True,
        "reason": None
    }
