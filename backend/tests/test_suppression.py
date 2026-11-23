"""Tests for BE-49 suppression service and API behaviour."""

from datetime import datetime, timezone
from uuid import UUID

import pytest
from unittest.mock import MagicMock, patch

from backend.app.services import suppression_service as service


def test_normalizes_email():
    assert service.normalize_suppression_value("  Person@Example.COM ", "email") == "person@example.com"


def test_normalizes_domain_with_at_prefix():
    assert service.normalize_suppression_value("@Example.COM", "domain") == "example.com"


@pytest.mark.parametrize("value,kind", [
    ("not-an-email", "email"),
    ("https://example.com", "domain"),
    ("example.com/path", "domain"),
    ("person@example.com", "unknown"),
])
def test_rejects_invalid_suppression_values(value, kind):
    with pytest.raises(ValueError):
        service.normalize_suppression_value(value, kind)


def test_is_suppressed_checks_exact_and_domain():
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = {"suppressed": True}
    conn.cursor.return_value.__enter__.return_value = cursor

    with patch.object(service, "get_conn") as get_conn:
        get_conn.return_value.__enter__.return_value = conn
        assert service.is_suppressed("Person@Example.com") is True

    args = cursor.execute.call_args.args
    assert args[1] == ("person@example.com", "example.com")


def test_is_suppressed_returns_false_for_invalid_email_without_database():
    with patch.object(service, "get_conn") as get_conn:
        assert service.is_suppressed("not-an-email") is False
        get_conn.assert_not_called()


def test_remove_requires_justification():
    with pytest.raises(ValueError, match="justification"):
        service.remove_suppression(
            entry_id=UUID("00000000-0000-0000-0000-000000000001"),
            justification=" ",
            actor="admin-id",
        )


def test_remove_writes_audit_in_same_transaction():
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "id": UUID("00000000-0000-0000-0000-000000000001"),
        "value": "person@example.com",
        "kind": "email",
        "reason": "unsubscribe",
        "source": "unsubscribe",
    }
    conn.cursor.return_value.__enter__.return_value = cursor

    with patch.object(service, "transaction") as tx, patch.object(service, "log_action") as audit:
        tx.return_value.__enter__.return_value = conn
        result = service.remove_suppression(
            entry_id=UUID("00000000-0000-0000-0000-000000000001"),
            justification="User confirmed re-subscription",
            actor="admin-id",
        )

    assert result["removed"] is True
    audit.assert_called_once()
    assert audit.call_args.kwargs["action"] == "suppression_removed"
    assert audit.call_args.kwargs["details_dict"]["justification"] == "User confirmed re-subscription"
"""Tests for BE-49 suppression service and API behaviour."""

from datetime import datetime, timezone
from uuid import UUID

import pytest
from unittest.mock import MagicMock, patch

from backend.app.services import suppression_service as service


def test_normalizes_email():
    assert service.normalize_suppression_value("  Person@Example.COM ", "email") == "person@example.com"


def test_normalizes_domain_with_at_prefix():
    assert service.normalize_suppression_value("@Example.COM", "domain") == "example.com"


@pytest.mark.parametrize("value,kind", [
    ("not-an-email", "email"),
    ("https://example.com", "domain"),
    ("example.com/path", "domain"),
    ("person@example.com", "unknown"),
])
def test_rejects_invalid_suppression_values(value, kind):
    with pytest.raises(ValueError):
        service.normalize_suppression_value(value, kind)


def test_is_suppressed_checks_exact_and_domain():
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = {"suppressed": True}
    conn.cursor.return_value.__enter__.return_value = cursor

    with patch.object(service, "get_conn") as get_conn:
        get_conn.return_value.__enter__.return_value = conn
        assert service.is_suppressed("Person@Example.com") is True

    args = cursor.execute.call_args.args
    assert args[1] == ("person@example.com", "example.com")


def test_is_suppressed_returns_false_for_invalid_email_without_database():
    with patch.object(service, "get_conn") as get_conn:
        assert service.is_suppressed("not-an-email") is False
        get_conn.assert_not_called()


def test_remove_requires_justification():
    with pytest.raises(ValueError, match="justification"):
        service.remove_suppression(
            entry_id=UUID("00000000-0000-0000-0000-000000000001"),
            justification=" ",
            actor="admin-id",
        )


def test_remove_writes_audit_in_same_transaction():
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "id": UUID("00000000-0000-0000-0000-000000000001"),
        "value": "person@example.com",
        "kind": "email",
        "reason": "unsubscribe",
        "source": "unsubscribe",
    }
    conn.cursor.return_value.__enter__.return_value = cursor

    with patch.object(service, "transaction") as tx, patch.object(service, "log_action") as audit:
        tx.return_value.__enter__.return_value = conn
        result = service.remove_suppression(
            entry_id=UUID("00000000-0000-0000-0000-000000000001"),
            justification="User confirmed re-subscription",
            actor="admin-id",
        )

    assert result["removed"] is True
    audit.assert_called_once()
    assert audit.call_args.kwargs["action"] == "suppression_removed"
    assert audit.call_args.kwargs["details_dict"]["justification"] == "User confirmed re-subscription"
