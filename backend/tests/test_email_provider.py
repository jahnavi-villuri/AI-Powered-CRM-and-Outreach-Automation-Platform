from unittest.mock import MagicMock, patch

import httpx
import pytest

from backend.app.integrations.email_provider import (
    SANDBOX_RECIPIENT,
    EmailProviderClient,
    EmailProviderAPIError,
    EmailProviderError,
)


def _make_client(sandbox_mode=None, environment="development"):
    with patch.dict(
        "os.environ",
        {"ENVIRONMENT": environment, "EMAIL_PROVIDER_API_KEY": "test-key"},
        clear=False,
    ):
        return EmailProviderClient(sandbox_mode=sandbox_mode)


def _ok_response(provider_message_id="msg_123"):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"id": provider_message_id}
    return response


def test_sandbox_mode_defaults_true_outside_production():
    client = _make_client(environment="development")
    assert client.sandbox_mode is True


def test_sandbox_mode_defaults_false_in_production():
    client = _make_client(environment="production")
    assert client.sandbox_mode is False


def test_non_production_send_routes_to_sandbox_recipient():
    client = _make_client()
    with patch("backend.app.integrations.email_provider.httpx.post", return_value=_ok_response()) as mock_post:
        client.send(
            to="real-user@example.com",
            subject="Subject",
            html="<p>Hi</p>",
            unsubscribe_url="https://example.com/unsubscribe",
        )
    sent_to = mock_post.call_args.kwargs["json"]["to"]
    assert sent_to == [SANDBOX_RECIPIENT]


def test_sandbox_override_cannot_disable_sandbox_outside_production():
    """A caller-supplied sandbox_mode=False must never defeat the strict
    non-production sandbox routing guarantee."""
    client = _make_client(sandbox_mode=False, environment="development")
    assert client.sandbox_mode is True


def test_sandbox_override_can_force_sandbox_on_in_production():
    """The override may still force sandbox ON in production (e.g. for a
    deliberate manual test send), since that direction is safe."""
    client = _make_client(sandbox_mode=True, environment="production")
    assert client.sandbox_mode is True


def test_production_send_uses_real_recipient():
    client = _make_client(environment="production")
    with patch("backend.app.integrations.email_provider.httpx.post", return_value=_ok_response()) as mock_post:
        client.send(
            to="real-user@example.com",
            subject="Subject",
            html="<p>Hi</p>",
            unsubscribe_url="https://example.com/unsubscribe",
        )
    sent_to = mock_post.call_args.kwargs["json"]["to"]
    assert sent_to == ["real-user@example.com"]


def test_list_unsubscribe_headers_are_set():
    client = _make_client()
    with patch("backend.app.integrations.email_provider.httpx.post", return_value=_ok_response()) as mock_post:
        client.send(
            to="real-user@example.com",
            subject="Subject",
            html="<p>Hi</p>",
            unsubscribe_url="https://example.com/unsubscribe?id=abc",
        )
    headers = mock_post.call_args.kwargs["json"]["headers"]
    assert headers["List-Unsubscribe"] == "<https://example.com/unsubscribe?id=abc>"
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_timeout_retries_with_the_same_idempotency_key_not_a_new_one():
    client = _make_client()
    seen_keys = []

    def always_times_out(url, json, headers, timeout):
        seen_keys.append(headers["Idempotency-Key"])
        raise httpx.TimeoutException("simulated timeout")

    with patch("backend.app.integrations.email_provider.httpx.post", side_effect=always_times_out):
        with pytest.raises(EmailProviderError):
            client.send(
                to="real-user@example.com",
                subject="Subject",
                html="<p>Hi</p>",
                unsubscribe_url="https://example.com/unsubscribe",
                idempotency_key="fixed-key",
            )

    assert len(seen_keys) >= 2  # an actual retry happened, not just one attempt
    assert len(set(seen_keys)) == 1  # every attempt reused the identical key


def test_timeout_never_succeeds_with_a_different_key_than_requested():
    """A caller-supplied idempotency_key must never be swapped for a generated one mid-retry."""
    client = _make_client()

    def always_times_out(url, json, headers, timeout):
        raise httpx.TimeoutException("simulated timeout")

    with patch("backend.app.integrations.email_provider.httpx.post", side_effect=always_times_out):
        with pytest.raises(EmailProviderError) as exc_info:
            client.send(
                to="real-user@example.com",
                subject="Subject",
                html="<p>Hi</p>",
                unsubscribe_url="https://example.com/unsubscribe",
                idempotency_key="caller-supplied-key",
            )
    assert "caller-supplied-key" in str(exc_info.value)


def test_provider_error_response_raises_without_retrying():
    client = _make_client()
    error_response = MagicMock()
    error_response.status_code = 422
    error_response.text = "Invalid recipient"

    with patch("backend.app.integrations.email_provider.httpx.post", return_value=error_response) as mock_post:
        with pytest.raises(EmailProviderAPIError):
            client.send(
                to="real-user@example.com",
                subject="Subject",
                html="<p>Hi</p>",
                unsubscribe_url="https://example.com/unsubscribe",
            )
    assert mock_post.call_count == 1  # a real provider error is not retried, only a timeout is


def test_successful_send_returns_provider_message_id():
    client = _make_client()
    with patch(
        "backend.app.integrations.email_provider.httpx.post",
        return_value=_ok_response(provider_message_id="msg_abc"),
    ):
        result = client.send(
            to="real-user@example.com",
            subject="Subject",
            html="<p>Hi</p>",
            unsubscribe_url="https://example.com/unsubscribe",
        )
    assert result["provider_message_id"] == "msg_abc"
    assert result["sandbox"] is True


def test_missing_api_key_raises_before_any_network_call():
    with patch.dict("os.environ", {"EMAIL_PROVIDER_API_KEY": ""}, clear=False):
        client = EmailProviderClient(api_key="")
    with patch("backend.app.integrations.email_provider.httpx.post") as mock_post:
        with pytest.raises(ValueError):
            client.send(
                to="real-user@example.com",
                subject="Subject",
                html="<p>Hi</p>",
                unsubscribe_url="https://example.com/unsubscribe",
            )
    mock_post.assert_not_called()


def _status_response(status_code=200, last_event="delivered"):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {"last_event": last_event}
    return response


def test_check_status_returns_last_event_on_success():
    client = _make_client()
    with patch(
        "backend.app.integrations.email_provider.httpx.get",
        return_value=_status_response(last_event="delivered"),
    ):
        assert client.check_status("msg_123") == "delivered"


def test_check_status_returns_none_on_non_200():
    client = _make_client()
    with patch(
        "backend.app.integrations.email_provider.httpx.get",
        return_value=_status_response(status_code=404),
    ):
        assert client.check_status("msg_missing") is None


def test_check_status_returns_none_on_network_error():
    client = _make_client()
    with patch(
        "backend.app.integrations.email_provider.httpx.get",
        side_effect=httpx.ConnectError("boom"),
    ):
        assert client.check_status("msg_123") is None
