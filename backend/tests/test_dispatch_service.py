
import hashlib
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.dispatch_service import validate_before_send


EMAIL = "test@example.com"
DRAFT_ID = "draft-123"
IDEMPOTENCY_KEY = "message-123"


def create_draft():
    subject = "Hello"
    body = "This is a test message."

    content_hash = hashlib.sha256(
        f"{EMAIL}:{subject}:{body}".encode("utf-8")
    ).hexdigest()

    return (
        "campaign-123",
        subject,
        body,
        {},
        "approval-123",
        content_hash,
        EMAIL,
    )


def run_validation(responses, recipient=EMAIL):
    cursor = MagicMock()
    cursor.fetchone.side_effect = responses

    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor

    db_context = MagicMock()
    db_context.__enter__.return_value = connection

    with patch(
        "backend.app.services.dispatch_service.get_conn",
        return_value=db_context
    ):
        result = validate_before_send(
            DRAFT_ID,
            recipient,
            IDEMPOTENCY_KEY
        )

    return result, cursor


def test_invalid_email():
    result, cursor = run_validation([], "invalid-email")

    assert result["allowed"] is False
    assert result["reason"] == "Invalid recipient email format."
    cursor.execute.assert_not_called()


def test_email_not_deliverable():
    result, cursor = run_validation([None])

    assert result["allowed"] is False
    assert result["reason"] == (
        "Recipient email is not verified as deliverable."
    )
    assert cursor.execute.call_count == 1


def test_suppressed_email():
    result, cursor = run_validation([
        ("deliverable",),
        (1,)
    ])

    assert result["allowed"] is False
    assert result["reason"] == (
        "Recipient email or domain is suppressed."
    )
    assert cursor.execute.call_count == 2


def test_missing_approval():
    result, cursor = run_validation([
        ("deliverable",),
        None,
        None
    ])

    assert result["allowed"] is False
    assert result["reason"] == "No valid approval exists for this draft."
    assert cursor.execute.call_count == 3


def test_recipient_mismatch():
    draft = list(create_draft())
    draft[6] = "other@example.com"

    result, cursor = run_validation([
        ("deliverable",),
        None,
        tuple(draft)
    ])

    assert result["allowed"] is False
    assert result["reason"] == (
        "Approved recipient does not match the current recipient."
    )
    assert cursor.execute.call_count == 3


def test_content_hash_mismatch():
    draft = list(create_draft())
    draft[5] = "incorrect-hash"

    result, cursor = run_validation([
        ("deliverable",),
        None,
        tuple(draft)
    ])

    assert result["allowed"] is False
    assert result["reason"] == (
        "Draft content does not match the approved content."
    )
    assert cursor.execute.call_count == 3


def test_duplicate_idempotency_key():
    result, cursor = run_validation([
        ("deliverable",),
        None,
        create_draft(),
        (1,)
    ])

    assert result["allowed"] is False
    assert result["reason"] == (
        "This message has already been processed."
    )
    assert cursor.execute.call_count == 4


def test_inactive_campaign():
    result, cursor = run_validation([
        ("deliverable",),
        None,
        create_draft(),
        None,
        ("paused", 50)
    ])

    assert result["allowed"] is False
    assert result["reason"] == "Campaign is not active."
    assert cursor.execute.call_count == 5


def test_daily_send_limit_reached():
    result, cursor = run_validation([
        ("deliverable",),
        None,
        create_draft(),
        None,
        ("active", 10),
        (10,)
    ])

    assert result["allowed"] is False
    assert result["reason"] == (
        "Campaign daily send limit has been reached."
    )
    assert cursor.execute.call_count == 6


def test_validation_success():
    result, cursor = run_validation([
        ("deliverable",),
        None,
        create_draft(),
        None,
        ("active", 50),
        (5,)
    ])

    assert result == {
        "allowed": True,
        "reason": None
    }
    assert cursor.execute.call_count == 6


def test_email_is_normalized():
    result, cursor = run_validation([
        ("deliverable",),
        None,
        create_draft(),
        None,
        ("active", 50),
        (5,)
    ], " TEST@EXAMPLE.COM ")

    assert result["allowed"] is True
