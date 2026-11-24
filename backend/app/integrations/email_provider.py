"""
Email Provider Integration Client (BE-47).

Wraps the Resend email API: adds standard List-Unsubscribe headers to every
outbound message, defaults to sandbox mode outside production so sends never
reach a real inbox by accident, and uses a per-send Idempotency-Key so a
network timeout can be retried without ever risking a duplicate send.

A genuine connection timeout means no response was ever received, so there
is no message id available to query Resend's status endpoint with — this is
a hard constraint of the provider's API (status can only be looked up by
id), not a gap in this client. `check_status()` is exposed as a standalone
method for callers that do have a known message id (e.g. BE-48's webhook
handling, or a later manual check) and need to confirm its current status.
"""

import logging
import os
import uuid
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_API_URL = "https://api.resend.com/emails"
_STATUS_CHECK_TIMEOUT = 10.0
_REQUEST_TIMEOUT = 10.0
_MAX_SEND_ATTEMPTS = 2

SANDBOX_RECIPIENT = "delivered@resend.dev"


class EmailProviderError(Exception):
    """Base exception for email provider integration failures."""


class EmailProviderAPIError(EmailProviderError):
    """Raised when Resend returns an unhandled non-2xx response."""


class EmailProviderClient:
    """Thin client around the Resend email API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        from_address: Optional[str] = None,
        sandbox_mode: Optional[bool] = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("EMAIL_PROVIDER_API_KEY", "")
        self.from_address = from_address or os.environ.get(
            "EMAIL_FROM_ADDRESS", "outreach@company.example.com"
        )

        environment = os.environ.get("ENVIRONMENT", "development").lower()
        is_non_production = environment != "production"

        if is_non_production:
            self.sandbox_mode = True
        elif sandbox_mode is not None:
            self.sandbox_mode = sandbox_mode
        else:
            self.sandbox_mode = False

    def check_status(self, message_id: str) -> Optional[str]:
        """
        Queries Resend for a previously sent email's current status
        (e.g. "sent", "delivered"). Returns None if the lookup itself fails,
        so a failed status check never blocks a caller from proceeding.
        """
        try:
            response = httpx.get(
                f"{_API_URL}/{message_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=_STATUS_CHECK_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            logger.warning("Status check failed for message_id=%s: %s", message_id, exc)
            return None

        if response.status_code != 200:
            logger.warning(
                "Status check for message_id=%s returned HTTP %s", message_id, response.status_code
            )
            return None

        return response.json().get("last_event")

    def send(
        self,
        to: str,
        subject: str,
        html: str,
        unsubscribe_url: str,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:

        if not self.api_key:
            raise ValueError("EMAIL_PROVIDER_API_KEY is not configured.")

        recipient = to
        if self.sandbox_mode:
            logger.info(
                "Sandbox mode active: rerouting send (originally to %s) to %s",
                to,
                SANDBOX_RECIPIENT,
            )
            recipient = SANDBOX_RECIPIENT

        key = idempotency_key or str(uuid.uuid4())

        payload = {
            "from": self.from_address,
            "to": [recipient],
            "subject": subject,
            "html": html,
            "headers": {
                "List-Unsubscribe": f"<{unsubscribe_url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": key,
        }

        last_exc: Optional[Exception] = None
        for attempt in range(1, _MAX_SEND_ATTEMPTS + 1):
            try:
                response = httpx.post(
                    _API_URL, json=payload, headers=headers, timeout=_REQUEST_TIMEOUT
                )
            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning(
                    "Timeout sending email (idempotency_key=%s, attempt %d/%d). "
                    "Retrying with the same Idempotency-Key so Resend cannot create a duplicate send.",
                    key,
                    attempt,
                    _MAX_SEND_ATTEMPTS,
                )
                continue

            if response.status_code == 200:
                data = response.json()
                logger.info(
                    "Email sent (idempotency_key=%s, provider_message_id=%s, sandbox=%s)",
                    key,
                    data.get("id"),
                    self.sandbox_mode,
                )
                return {
                    "provider_message_id": data.get("id"),
                    "idempotency_key": key,
                    "sandbox": self.sandbox_mode,
                }

            logger.error(
                "Resend API error: HTTP %s - %s (idempotency_key=%s)",
                response.status_code,
                response.text,
                key,
            )
            raise EmailProviderAPIError(
                f"Resend API failed with HTTP {response.status_code}: {response.text}"
            )

        raise EmailProviderError(
            f"Email send timed out after {_MAX_SEND_ATTEMPTS} attempts "
            f"(idempotency_key={key})"
        ) from last_exc
